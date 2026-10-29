#!/usr/bin/env python3
"""開布林：擠壓多深 × 開口多大 × 量（2026-10-29，使用者：擠壓多少、開口多大、兩者比例，怎樣才算開布林，
開完隔天做多或做空能不能贏，量價對不對得上）。

事件（看結果前寫死）：
  突破日＝收盤在布林上軌之外（位階 ≥ 10）且昨天還沒有（昨天位階 < 10）→「往上開」；
          收盤在下軌之外（位階 ≤ −10）且昨天還沒有 →「往下開」。今天帶寬要比昨天寬（帶寬變化 > 0）。
  擠壓深度（前 10 天、不含今天的最窄帶寬，在自己過去 125 天的百分位，0＝最窄；跟喇叭口 h2 同一個算法）：
          深 ≤ 10、中 10～30、淺 > 30（＝沒什麼擠壓）。
  開口大小＝今天帶寬 ÷ 前 10 天最窄帶寬：小 < 1.3、中 1.3～1.6、大 ≥ 1.6。
  量（均量比＝今天量 ÷ 前 20 日均量）：低 < 1.5、中 1.5～3、高 ≥ 3。
  格數：方向 2 × 深度 3 × 開口 3 × 量 3 ＝ 54 種事件；每種都算做多與做空 → 108 格。

進出場：母體成交金額 ≥ 1 億；隔天開盤進。抱 1 天＝隔天開盤進、收盤出（當沖成本 0.235%）；5／10 天用隔夜成本 0.386%。
超額＝照方向減同日母體平均；t＝每個進場日平均、Newey-West。

事先登記（2026-10-29，看結果前寫死、先推上 GitHub）：
  主要格＝持有 1 天（使用者：開完隔天還能不能贏），判定期間＝2021-02-24～2025-08-31。
  通過＝照方向超額 > 0、t ≥ 2、淨（每筆與每日）> 0、進場日 ≥ 30。108 格純靠運氣約 5 格 |t| ≥ 2。
  另要「高原」：通過的格，同方向、同交易方向、開口相鄰一格（小↔中↔大）或深度相鄰一格至少一個超額也 > 0。
  5／10 天、2025-09 以後只當參考。已知：往上帶量開布林隔天追是反指標（1 天 t −3.0），往上開做空 1 天可能會「通過」。

用法：python bt_bopen2.py [天數] → data/bt_bopen2.json
"""
import json
import os
import sys
from collections import defaultdict

import backtest as B
from bt_granville import stats

HS = B.HORIZONS
SPLIT = "20250901"
MAIN_H = 1
DEPTH = (("deep", 10), ("mid", 30), ("shallow", 101))
SIZE = (("s", 1.3), ("m", 1.6), ("l", 1e9))
VOL = (("lo", 1.5), ("mid", 3.0), ("hi", 1e9))
LAB = {"deep": "擠壓深", "mid": "擠壓中", "shallow": "擠壓淺", "s": "開口小", "m": "開口中", "l": "開口大",
       "lo": "量低", "hi": "量高", "U": "往上開", "D": "往下開", "L": "做多", "S": "做空"}


def bucket(x, table):
    for name, hi in table:
        if x < hi or (table is DEPTH and x <= hi):
            return name
    return table[-1][0]


def main():
    limit = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    days = []
    recs = defaultdict(list)

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1 or i < 1:
                continue
            pos, ppos = d.get("bbPos"), st["pos"][i - 1]
            bw, bmin, chg, sq, vr = d.get("bbW"), d.get("bbWMin10"), d.get("bbWChg"), d.get("_sqp10"), d.get("vr20")
            if None in (pos, ppos, bw, bmin, chg, sq, vr) or bmin <= 0 or chg <= 0:
                continue
            if pos >= 10 and ppos < 10:
                dr = "U"
            elif pos <= -10 and ppos > -10:
                dr = "D"
            else:
                continue
            ev = "%s|%s|%s|%s" % (dr, bucket(sq, DEPTH), bucket(bw / bmin, SIZE), bucket(vr, VOL))
            for hh in HS:
                r, b = fwd.get(hh), bm.get(hh)
                if r is not None and b is not None:
                    recs[(ev, hh)].append((di, r, b))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    split_i = next((k for k, d in enumerate(days) if d >= SPLIT), len(days))
    out = {}
    evs = sorted({k for k, _ in recs})
    for ev in evs:
        for side, sg in (("L", 1), ("S", -1)):
            hs = {}
            for hh in HS:
                rr = recs.get((ev, hh), [])
                hs[str(hh)] = dict(all=stats(rr, sg, hh), pre=stats([x for x in rr if x[0] < split_i], sg, hh),
                                   post=stats([x for x in rr if x[0] >= split_i], sg, hh))
            out[ev + "|" + side] = dict(h=hs)

    def m_of(k):
        x = out.get(k)
        return x and x["h"][str(MAIN_H)]["pre"]

    passed = []
    for k, x in out.items():
        m = m_of(k)
        ok3 = bool(m and m["nd"] >= 30 and m["ex"] is not None and m["ex"] > 0 and m["t"] is not None and m["t"] >= 2
                   and m["net"] > 0 and m["net_d"] > 0)
        dr, dp, sz, vl, sd = k.split("|")
        nb = []
        si = [s for s, _ in SIZE].index(sz)
        di_ = [s for s, _ in DEPTH].index(dp)
        for j in (si - 1, si + 1):
            if 0 <= j < 3:
                nb.append("|".join((dr, dp, SIZE[j][0], vl, sd)))
        for j in (di_ - 1, di_ + 1):
            if 0 <= j < 3:
                nb.append("|".join((dr, DEPTH[j][0], sz, vl, sd)))
        plateau = any(m_of(n) and m_of(n)["ex"] is not None and m_of(n)["ex"] > 0 for n in nb)
        x["pass_"] = ok3 and plateau
        x["ok3"] = ok3
        if x["pass_"]:
            passed.append(k)
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, main_h=MAIN_H, labels=LAB,
                cost={"1": B.COST_DAY, "other": B.COST_OVERNIGHT}, n_cells=108, lucky=round(108 * 0.0455, 1), passed=passed)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "bt_bopen2.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, cells=out), fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=開布林深度開口::%d 天，%d 種事件，登記判定通過：%s" % (len(days), len(evs), "、".join(passed) or "無"))


if __name__ == "__main__":
    main()
