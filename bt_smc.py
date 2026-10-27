#!/usr/bin/env python3
"""SMC／ICT 概念＋分價量表 長樣本回測（2026-10-27）。定義在 smc.py（籌碼看板共用同一份）。

跟 backtest.py 同一套進出場與基準（直接呼叫 backtest.run 收集）：
  訊號日收盤出現型態 → 隔天開盤進 → 持有 1／5／10／20 天收盤出；母體＝當天成交金額 ≥ 1 億；
  超額＝照方向（多方型態做多、空方型態做空）減同日成交金額 ≥ 1 億全市場平均；
  t＝每個進場日平均的序列、Newey-West（lag＝持有天數）；淨＝照方向原始報酬 − 隔夜成本 0.386%。
  「淨」同時給每筆平均（net）與每日平均（net_d），兩種加權並列（使用者 10-27）。

事先登記（2026-10-27，看結果前寫死、先推上 GitHub；定義照網路通行寫法，沒用任何資料挑門檻）：
  12 種事件（看多 6：CHoCH↑、BOS↑、多方 OB 回測守住、多方 FVG 回補守住、掃低點收回、回測 POC 守住；看空 6 為鏡像），
  方向照 smc.py 的 SIDE（_u 做多、_d 做空），主要格＝持有 10 天，判定期間＝2021-02-24～2025-08-31（整段都沒看過）。
  通過＝照方向超額 > 0、t ≥ 2、淨 > 0 三個都成立。1／5／20 天、2025-09 以後只當參考。12 格純靠運氣約 0.5 格 |t| ≥ 2。

用法：python bt_smc.py [天數] → data/bt_smc.json
"""
import json
import math
import os
import sys
from collections import defaultdict

import backtest as B
import smc as P

COST = B.COST_OVERNIGHT
HS = B.HORIZONS
SPLIT = "20250901"
MAIN_H = 10


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


def stats(recs, side, h):
    """recs: [(日期序號, 原始報酬, 基準)]；side 1／−1。"""
    if len(recs) < 20:
        return None
    by = defaultdict(list)
    for di, r, b in recs:
        by[di].append((side * r, side * (r - b)))
    dk = sorted(by)
    ex = [sum(x for _, x in by[d]) / len(by[d]) for d in dk]
    rawd = [sum(x for x, _ in by[d]) / len(by[d]) for d in dk]
    dret = [side * r for _, r, _ in recs]
    wins = [x for x in dret if x > 0]
    loss = [x for x in dret if x < 0]
    aw = sum(wins) / len(wins) if wins else None
    al = sum(loss) / len(loss) if loss else None
    return dict(n=len(recs), nd=len(dk), ex=_r(sum(ex) / len(ex)), t=_r(nw_t(ex, h), 2),
                net=_r(sum(dret) / len(dret) - COST), net_d=_r(sum(rawd) / len(rawd) - COST),
                win=round(len(wins) / len(dret) * 100, 1), pay=_r(aw / -al if (aw is not None and al) else None, 2))


def main():
    args = sys.argv[1:]
    limit = next((int(a) for a in args if a.isdigit()), None)
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    cache = {}
    days = []
    recs = defaultdict(list)            # (型態, 帶量?, h) -> [(di, ret, bm)]

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1:
                continue
            ev = cache.get(code)
            if ev is None:
                ev = {}
                for e in P.detect(st["o"], st["h"], st["l"], st["c"], st["lot"]):
                    ev.setdefault(e["i"], []).append((e["key"], False))
                cache[code] = ev
            for t, vol in ev.get(i, ()):
                for h in HS:
                    r, b = fwd.get(h), bm.get(h)
                    if r is None or b is None:
                        continue
                    recs[(t, False, h)].append((di, r, b))
                    if vol:
                        recs[(t, True, h)].append((di, r, b))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    split_i = next((k for k, d in enumerate(days) if d >= SPLIT), len(days))
    out = {}
    passed = []
    for t in P.NAMES:
        side = P.SIDE[t]
        row = {"side": side, "key": t, "name": P.NAMES[t]}
        for vol in (False,):
            hs = {}
            for h in HS:
                rr = recs.get((t, vol, h), [])
                hs[str(h)] = dict(all=stats(rr, side, h),
                                  pre=stats([x for x in rr if x[0] < split_i], side, h),
                                  post=stats([x for x in rr if x[0] >= split_i], side, h))
            row["base"] = hs
        m = row["base"][str(MAIN_H)]["pre"]
        ok = bool(m and m["ex"] is not None and m["ex"] > 0 and m["t"] is not None and m["t"] >= 2 and m["net"] > 0)
        row["pass"] = ok
        if ok:
            passed.append(t)
        out[t] = row
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, main_h=MAIN_H, cost=COST,
                n_types=len(out), lucky=round(len(out) * 0.0455, 1), passed=passed)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "bt_smc.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, types=out), fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=SMC 回測::%d 天，12 種事件，登記判定通過：%s" % (len(days), "、".join(passed) or "無"))
    # 主要格分批寫註記（容器讀不到日誌）
    lines = []
    for t, row in out.items():
        m = row["base"][str(MAIN_H)]["pre"]
        q = row["base"][str(MAIN_H)]["post"]
        if m:
            lines.append("%s %s：n%d 超額%+.2f t%s 淨%+.2f｜近一年%s" % (
                t, "多" if row["side"] > 0 else "空", m["n"], m["ex"], m["t"], m["net"],
                ("%+.2f" % q["ex"]) if q else "—"))
    for k in range(0, len(lines), 5):
        print("::notice title=SMC 10 天樣本外 %d::%s" % (k // 5 + 1, " ／ ".join(lines[k:k + 5])))


if __name__ == "__main__":
    main()
