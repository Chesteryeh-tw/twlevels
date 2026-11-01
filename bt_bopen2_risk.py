#!/usr/bin/env python3
"""開布林深度開口 7 格（隔天當沖空）的軋空風險補測（2026-11-01，使用者：帶量開布林隔天空會不會被軋）。

只描述風險，不是新的通過判定（7 格的判定以 bt_bopen2.py 與舊資料驗證為準）。
事件定義照 bt_bopen2.py；隔天＝訊號日的下一個市場交易日（T+1），開盤空、收盤補。
  報酬（照放空方向）＝(開 − 收) ÷ 開；當沖成本 0.235%。
  盤中最高比開盤高 ≥ 3%、≥ 5% 的比例；收在漲停（收 ≥ 前收 × 1.095）的比例；開盤就漲停（開 ≥ 前收 × 1.095，空不到）的比例。
  停損版：最高 ≥ 開 × 1.03 就當作在 +3% 停損（虧 3%）；不知道盤中先後，所以碰到就算停損，結果偏保守。
  最差 5% 的單筆、最大單筆虧損。
用法：python bt_bopen2_risk.py <輸出.json>
"""
import json
import sys

import backtest as B
from bt_bopen2 import bucket, DEPTH, SIZE, VOL

CELLS = ["D|deep|m|hi|S", "D|deep|s|hi|S", "U|deep|l|hi|S", "U|mid|s|mid|S",
         "U|shallow|m|mid|S", "U|shallow|s|lo|S", "U|shallow|s|mid|S"]
WANT = {k[:-2] for k in CELLS}
COST = B.COST_DAY


def main(out_path):
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    recs = {k: [] for k in WANT}
    days = []

    def collect(day, rws, bm, krows):
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
            if ev not in WANT or i + 1 >= len(st["c"]):
                continue
            pc, o, h, c = st["c"][i], st["o"][i + 1], st["h"][i + 1], st["c"][i + 1]
            if not (pc and o and h and c) or o <= 0:
                continue
            recs[ev].append(dict(day=day, ret=(o - c) / o * 100, up=(h / o - 1) * 100,
                                 lu_close=c >= pc * 1.095, lu_open=o >= pc * 1.095))

    try:
        B.run(verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    out = dict(days=len(days), start=days[0] if days else None, end=days[-1] if days else None,
               cost=COST, cells={})
    for k in CELLS:
        rr = recs[k[:-2]]
        if not rr:
            out["cells"][k] = None
            continue
        n = len(rr)
        rets = sorted(x["ret"] for x in rr)
        stop = [(-3.0 if x["up"] >= 3 else x["ret"]) for x in rr]
        out["cells"][k] = dict(
            n=n, days=len({x["day"] for x in rr}),
            net=round(sum(rets) / n - COST, 3),
            net_stop3=round(sum(stop) / n - COST, 3),
            win=round(sum(1 for x in rets if x > COST) / n * 100, 1),
            up3=round(sum(1 for x in rr if x["up"] >= 3) / n * 100, 1),
            up5=round(sum(1 for x in rr if x["up"] >= 5) / n * 100, 1),
            lu_close=round(sum(1 for x in rr if x["lu_close"]) / n * 100, 1),
            lu_open=round(sum(1 for x in rr if x["lu_open"]) / n * 100, 1),
            p5=round(rets[int(n * 0.05)], 2), worst=round(rets[0], 2))
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = []
    for k, v in out["cells"].items():
        if v:
            L.append("%s n=%d 淨%+.2f 停損3%%淨%+.2f 高+3%%:%.0f%% 高+5%%:%.0f%% 收漲停:%.1f%% 開漲停:%.1f%% 最差5%%:%.1f 最差:%.1f"
                     % (k, v["n"], v["net"], v["net_stop3"], v["up3"], v["up5"], v["lu_close"], v["lu_open"], v["p5"], v["worst"]))
    print("\n".join(L))
    print("::notice title=軋空風險 %s～%s::%s" % (out["start"], out["end"], "｜".join(L)))


if __name__ == "__main__":
    main(sys.argv[1])
