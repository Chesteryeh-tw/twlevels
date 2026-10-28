#!/usr/bin/env python3
"""K 線型態·照「進出場框架」回測：停損＋目標價出場（2026-10-28）。型態定義在 patterns.py（看板共用）。

使用者 10-28 同意：別人把型態當進出場框架（跌破頸線停損、到目標價出場），不是固定抱 N 天；照這種用法重測。
bt_patterns.py（固定抱 1／5／10／20 天）15 種都沒通過（測試結果 5-2）。

進出場（盤後工具，判斷都用收盤、隔天開盤成交，跟 backtest.exit_scan 同一個習慣）：
  訊號日收盤出現型態 → 隔天開盤進（成交金額 ≥ 1 億才算）。
  主要版 A「頸線停損」：收盤跌破頸線（多）／站上頸線（空）→ 隔天開盤出；
                        收盤到目標價（多：≥ 目標；空：≤ 目標）→ 隔天開盤出；
                        都沒碰到，持有滿 40 個交易日 → 隔天開盤出。
  參考版 B「型態停損」：停損改用型態最低（多）／最高（空）點，其他同 A。
  停損、目標、頸線都是 patterns.py 在訊號日就算好的（看板畫的那幾條線）。價格照 backtest 的公司行動設定。

對照組（條件出場要有自己的對照組，回測方法說明）：
  同一個訊號日，從當天成交金額 ≥ 1 億的股票隨機抽 30 檔（固定亂數種子），用「同樣百分比距離」的停損與目標、
  同樣最長 40 天，算它們的平均淨報酬。超額＝這筆的淨 − 同日對照組平均淨。
  （這樣比的是「型態選股＋這套出場」有沒有比「隨便選股＋同一套出場」好；出場規則本身的效果被扣掉。）

統計：淨＝照方向原始報酬 − 隔夜成本 0.386%（每筆平均 net、每日平均 net_d 並列）；
      t＝每個進場日平均超額的序列、Newey-West（lag 20）；勝率、賺賠比、平均持有天數、停損／目標／到期比例。

事先登記（2026-10-28，看結果前寫死、先推上 GitHub）：
  主要格＝版本 A，判定期間＝訊號日在 2021-02-24～2025-08-31。
  通過＝超額 > 0、t ≥ 2、淨（每筆與每日）都 > 0。15 格純靠運氣約 0.7 格 |t| ≥ 2。
  版本 B、2025-09 以後只當參考。通過的型態才在看板 K 棒標「買」「賣」（使用者 10-28）。

用法：python bt_patexit.py [天數] → data/bt_patexit.json
"""
import json
import math
import os
import random
import sys
from collections import defaultdict

import backtest as B
import patterns as P

COST = B.COST_OVERNIGHT
SPLIT = "20250901"
MAXD = 40
NCTRL = 30
LAG = 20


def nw_t(x, h):
    n = len(x)
    if n < 20:
        return None
    m = sum(x) / n
    dev = [v - m for v in x]
    var = sum(e * e for e in dev) / n
    for L in range(1, min(h, n - 1)):
        g = sum(dev[j] * dev[j - L] for j in range(L, n)) / n
        var += 2 * (1 - L / h) * g
    return m / (math.sqrt(var) / math.sqrt(n)) if var > 0 else None


def _r(x, n=3):
    return None if x is None else round(x, n)


def walk(st, i, side, stop, tgt):
    """訊號日 i、方向 side（1 多／−1 空）、停損價、目標價（都是價格）。
    回 (照方向報酬%, 持有天數, 原因) 或 None。"""
    c, o = st["c"], st["o"]
    a = i + 1
    if a >= len(o) or not o[a] or o[a] <= 0:
        return None
    entry = o[a]
    for n in range(1, MAXD + 1):
        j = i + n
        if j + 1 >= len(c):
            return None
        pc = c[j]
        if pc is None or pc <= 0:
            return None
        pa = pc * B.af(st, a, j)            # 跟進場價同一基準
        reason = None
        if side > 0:
            if pa <= stop:
                reason = "停損"
            elif pa >= tgt:
                reason = "目標"
        else:
            if pa >= stop:
                reason = "停損"
            elif pa <= tgt:
                reason = "目標"
        if reason is None and n >= MAXD:
            reason = "到期"
        if reason:
            nxt = o[j + 1]
            if not nxt or nxt <= 0:
                return None
            ret = (nxt * B.af(st, a, j + 1) / entry - 1) * 100
            return side * ret, n, reason
    return None


def stats(recs):
    """recs: [(di, 淨, 超額, 天數, 原因)]"""
    if len(recs) < 20:
        return None
    by = defaultdict(list)
    for di, nt, ex, n, rs in recs:
        by[di].append((nt, ex))
    dk = sorted(by)
    exd = [sum(e for _, e in by[d]) / len(by[d]) for d in dk]
    ntd = [sum(x for x, _ in by[d]) / len(by[d]) for d in dk]
    nets = [r[1] for r in recs]
    wins = [x for x in nets if x > 0]
    loss = [x for x in nets if x < 0]
    aw = sum(wins) / len(wins) if wins else None
    al = sum(loss) / len(loss) if loss else None
    rs = defaultdict(int)
    for r in recs:
        rs[r[4]] += 1
    return dict(n=len(recs), nd=len(dk), ex=_r(sum(exd) / len(exd)), t=_r(nw_t(exd, LAG), 2),
                net=_r(sum(nets) / len(nets)), net_d=_r(sum(ntd) / len(ntd)),
                win=round(len(wins) / len(nets) * 100, 1), pay=_r(aw / -al if (aw is not None and al) else None, 2),
                aw=_r(aw, 2), al=_r(al, 2), hold=round(sum(r[3] for r in recs) / len(recs), 1),
                why={k: round(v / len(recs) * 100, 1) for k, v in rs.items()})


def main():
    args = sys.argv[1:]
    limit = next((int(a) for a in args if a.isdigit()), None)
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    cache = {}
    days = []
    recs = defaultdict(list)            # (型態, 版本) -> [(di, 淨, 超額, 天數, 原因)]
    rnd = random.Random(20261028)

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        uni = [r for r in rws if r[2]["valE"] is not None and r[2]["valE"] >= 1]
        if not uni:
            return
        for code, mkt, base, d, fwd, i, st in uni:
            ev = cache.get(code)
            if ev is None:
                ev = {}
                for e in P.detect(st["o"], st["h"], st["l"], st["c"], st["lot"]):
                    ev.setdefault(e["i"], []).append(e)
                cache[code] = ev
            for e in ev.get(i, ()):
                side = e["side"]
                a = i + 1
                if a >= len(st["o"]) or not st["o"][a] or st["o"][a] <= 0:
                    continue
                entry = st["o"][a]
                ctrl = rnd.sample(uni, min(NCTRL, len(uni)))
                for ver, stop in (("A", e["neck"]), ("B", e["stop"])):
                    tgt = e["target"]
                    # 進場價已經在停損外或目標外：照規則第一天收盤就會出場，照算，不跳過
                    w = walk(st, i, side, stop, tgt)
                    if w is None:
                        continue
                    ret, n, why = w
                    sp, tp = stop / entry, tgt / entry      # 百分比距離（給對照組）
                    cs = []
                    for cr in ctrl:
                        cst, ci = cr[6], cr[5]
                        ca = ci + 1
                        if ca >= len(cst["o"]) or not cst["o"][ca] or cst["o"][ca] <= 0:
                            continue
                        ce = cst["o"][ca]
                        cw = walk(cst, ci, side, ce * sp, ce * tp)
                        if cw is not None:
                            cs.append(cw[0] - COST)
                    if len(cs) < 10:
                        continue
                    nt = ret - COST
                    recs[(e["type"], ver)].append((di, nt, nt - sum(cs) / len(cs), n, why))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    split_i = next((k for k, d in enumerate(days) if d >= SPLIT), len(days))
    out, passed = {}, []
    for t in P.LONG + P.SHORT:
        row = {"side": P.SIDE[t], "key": P.KEY[t]}
        for ver in ("A", "B"):
            rr = recs.get((t, ver), [])
            row[ver] = dict(all=stats(rr), pre=stats([x for x in rr if x[0] < split_i]),
                            post=stats([x for x in rr if x[0] >= split_i]))
        m = row["A"]["pre"]
        ok = bool(m and m["ex"] is not None and m["ex"] > 0 and m["t"] is not None and m["t"] >= 2
                  and m["net"] > 0 and m["net_d"] > 0)
        row["pass"] = ok
        if ok:
            passed.append(t)
        out[t] = row
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, maxd=MAXD, ctrl=NCTRL, cost=COST,
                lucky=round(len(out) * 0.0455, 1), passed=passed)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "bt_patexit.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, types=out), fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=型態停損目標::%d 天，登記判定通過：%s" % (len(days), "、".join(passed) or "無"))
    for ver in ("A", "B"):
        lines = []
        for t, row in out.items():
            m = row[ver]["pre"]
            if m:
                lines.append("%s：n%d 勝%.0f%% 淨%+.2f／%+.2f 超額%+.2f t%s 抱%.0f天 停損%s%%" % (
                    t, m["n"], m["win"], m["net"], m["net_d"], m["ex"], m["t"], m["hold"], m["why"].get("停損", 0)))
        for k in range(0, len(lines), 5):
            print("::notice title=型態出場 %s 樣本外 %d::%s" % (ver, k // 5 + 1, " ／ ".join(lines[k:k + 5])))


if __name__ == "__main__":
    main()
