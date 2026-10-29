#!/usr/bin/env python3
"""葛蘭碧八大法則（含假突破、假跌破）（2026-10-29，使用者提供 OANDA、玩股網、CMoney 文章）。

定義（看結果前寫死；文章都沒給數字門檻，這裡用整數、通行寫法）：
  均線 N＝20（月線，主要）與 60（季線）。均線方向＝今天均線 vs 5 天前均線：> 上升、< 下降。
  「轉平或轉升」＝5 天前的方向是下降（5 天前均線 < 10 天前均線），今天均線 ≥ 5 天前；「轉平或轉降」鏡像。
  做多（買點）：
    G1 突破：均線轉平或轉升，昨天收盤 < 均線、今天收盤 > 均線。
    G2 假跌破：均線上升，前 3 天內有收盤 < 均線，今天收盤站回均線之上（昨天收盤 < 均線）。
    G3 回測支撐：均線上升，今天最低 ≤ 均線 × 1.02、收盤 > 均線，前 3 天收盤都 > 均線（沒跌破）。
    G4 負乖離過大反彈：均線下降，收盤 < 均線 × 0.85，今天收紅（收 > 昨收）。
  做空（賣點）：
    G5 跌破：均線轉平或轉降，昨天收盤 > 均線、今天收盤 < 均線。
    G6 假突破：均線下降，前 3 天內有收盤 > 均線，今天收盤跌回均線之下（昨天收盤 > 均線）。
    G7 反彈遇壓：均線下降，今天最高 ≥ 均線 × 0.98、收盤 < 均線，前 3 天收盤都 < 均線。
    G8 正乖離過大回落：均線上升，收盤 > 均線 × 1.15，今天收黑（收 < 昨收）。
  量：每條另做一個量版本——G1、G2、G4、G5、G6、G8 加「帶量」（均量比 ≥ 1.5），G3、G7 加「量縮」（≤ 0.8）。
  格數：8 法則 × 2（不限量／量版本）× 2 均線 ＝ 32 格。

進出場與統計跟 backtest.py 一樣：母體成交金額 ≥ 1 億；隔天開盤進；超額＝照方向減同日母體平均；
t＝每個進場日平均、Newey-West；淨＝照方向原始報酬 − 成本（抱 1 天＝當天開盤進收盤出，用當沖成本 0.235%；其餘 0.386%）。

事先登記（2026-10-29，看結果前寫死、先推上 GitHub）：
  主要格＝持有 5 天，判定期間＝2021-02-24～2025-08-31。
  通過＝照方向超額 > 0、t ≥ 2、淨（每筆與每日）> 0、進場日 ≥ 30。32 格純靠運氣約 1.5 格 |t| ≥ 2。
  已知相關結果：G3 類（回踩守住）在 bt_pullback 64 格做多全賠；G7 類（空頭反彈遇壓）只當濾網。1／10 天只當參考。

用法：python bt_granville.py [天數] → data/bt_granville.json
"""
import json
import os
import sys
from collections import defaultdict

import backtest as B
from bt_patterns import nw_t, _r

HS = B.HORIZONS
SPLIT = "20250901"
MAIN_H = 5
RULES = {"G1": ("突破", 1), "G2": ("假跌破", 1), "G3": ("回測支撐", 1), "G4": ("負乖離反彈", 1),
         "G5": ("跌破", -1), "G6": ("假突破", -1), "G7": ("反彈遇壓", -1), "G8": ("正乖離回落", -1)}
VOLV = {"G1": "up", "G2": "up", "G4": "up", "G5": "up", "G6": "up", "G8": "up", "G3": "dn", "G7": "dn"}


def cost(h):
    return B.COST_DAY if h == 1 else B.COST_OVERNIGHT


def stats(recs, side, h):
    if len(recs) < 20:
        return None
    by = defaultdict(list)
    for di, r, b in recs:
        by[di].append((side * r, side * (r - b)))
    dk = sorted(by)
    ex = [sum(x for _, x in by[d]) / len(by[d]) for d in dk]
    rawd = [sum(x for x, _ in by[d]) / len(by[d]) for d in dk]
    dret = [side * r for _, r, _ in recs]
    wins = [x for x in dret if x > cost(h)]
    return dict(n=len(recs), nd=len(dk), ex=_r(sum(ex) / len(ex)), t=_r(nw_t(ex, h), 2),
                net=_r(sum(dret) / len(dret) - cost(h)), net_d=_r(sum(rawd) / len(rawd) - cost(h)),
                win=round(len(wins) / len(dret) * 100, 1))


def sma(c, i, n):
    if i - n + 1 < 0:
        return None
    w = c[i - n + 1:i + 1]
    return None if None in w else sum(w) / n


def main():
    limit = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    days = []
    recs = defaultdict(list)
    mcache = {}

    def mas(st, n):
        k = (id(st), n)
        if k not in mcache:
            c = st["c"]
            mcache[k] = [sma(c, i, n) for i in range(len(c))]
        return mcache[k]

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1 or i < 75:
                continue
            c, h, l = st["c"], st["h"], st["l"]
            if None in (c[i], c[i - 1], h[i], l[i]):
                continue
            vr = d.get("vr20")
            for n in (20, 60):
                m = mas(st, n)
                if None in (m[i], m[i - 1], m[i - 5], m[i - 10]) or None in c[i - 3:i]:
                    continue
                up, dn = m[i] > m[i - 5], m[i] < m[i - 5]
                was_dn, was_up = m[i - 5] < m[i - 10], m[i - 5] > m[i - 10]
                hit = set()
                if was_dn and m[i] >= m[i - 5] and c[i - 1] < m[i - 1] and c[i] > m[i]:
                    hit.add("G1")
                if up and c[i - 1] < m[i - 1] and c[i] > m[i] and any(c[j] < m[j] for j in range(i - 3, i)):
                    hit.add("G2")
                if up and l[i] <= m[i] * 1.02 and c[i] > m[i] and all(c[j] > m[j] for j in range(i - 3, i)):
                    hit.add("G3")
                if dn and c[i] < m[i] * 0.85 and c[i] > c[i - 1]:
                    hit.add("G4")
                if was_up and m[i] <= m[i - 5] and c[i - 1] > m[i - 1] and c[i] < m[i]:
                    hit.add("G5")
                if dn and c[i - 1] > m[i - 1] and c[i] < m[i] and any(c[j] > m[j] for j in range(i - 3, i)):
                    hit.add("G6")
                if dn and h[i] >= m[i] * 0.98 and c[i] < m[i] and all(c[j] < m[j] for j in range(i - 3, i)):
                    hit.add("G7")
                if up and c[i] > m[i] * 1.15 and c[i] < c[i - 1]:
                    hit.add("G8")
                for g in hit:
                    vv = VOLV[g]
                    volok = vr is not None and (vr >= 1.5 if vv == "up" else vr <= 0.8)
                    for vk in ("any", vv) if volok else ("any",):
                        key = "%s|MA%d|%s" % (g, n, vk)
                        for hh in HS:
                            r, b = fwd.get(hh), bm.get(hh)
                            if r is not None and b is not None:
                                recs[(key, hh)].append((di, r, b))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    split_i = next((k for k, d in enumerate(days) if d >= SPLIT), len(days))
    out, passed = {}, []
    keys = sorted({k for k, _ in recs})
    for key in keys:
        side = RULES[key.split("|")[0]][1]
        hs = {}
        for hh in HS:
            rr = recs.get((key, hh), [])
            hs[str(hh)] = dict(all=stats(rr, side, hh), pre=stats([x for x in rr if x[0] < split_i], side, hh),
                               post=stats([x for x in rr if x[0] >= split_i], side, hh))
        m = hs[str(MAIN_H)]["pre"]
        ok = bool(m and m["nd"] >= 30 and m["ex"] is not None and m["ex"] > 0 and m["t"] is not None and m["t"] >= 2
                  and m["net"] > 0 and m["net_d"] > 0)
        out[key] = dict(h=hs, pass_=ok, label=RULES[key.split("|")[0]][0], side=side)
        if ok:
            passed.append(key)
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, main_h=MAIN_H,
                cost={"1": B.COST_DAY, "other": B.COST_OVERNIGHT}, n_cells=32, lucky=round(32 * 0.0455, 1), passed=passed)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "bt_granville.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, cells=out), fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=葛蘭碧::%d 天，%d 格，登記判定通過：%s" % (len(days), len(keys), "、".join(passed) or "無"))


if __name__ == "__main__":
    main()
