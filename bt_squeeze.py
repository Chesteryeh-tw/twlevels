#!/usr/bin/env python3
"""開布林「先擠壓」門檻掃描：固定放寬 vs 動態（自己過去 125 天的百分位）（2026-10-28）。

使用者 10-28：東台 4526 10-19～10-22 開口前帶寬 14～17%，沒有低於 10%，現行定義抓不到；
要比較放寬範圍或動態調整，看勝率、損益。

共同進場（往上帶量開布林·首日，跟 b1f 一樣，只拿掉 bwy「昨天帶寬 ≤ 15%」——它跟擠壓門檻綁在一起，
放寬時會擋掉；首日條件已經保證是剛打開那天）：
  成交金額 ≥ 1 億、帶寬比昨天寬 ≥ 0.5 點、上通斜率 ≥ 1%、收在中軌以上、當天漲 ≥ 1%、均量比 ≥ 1.5、
  首日（昨天還沒在開）。
只換「先擠壓」這一個條件：
  固定：前 10 天（不含今天）最窄帶寬 ≤ 8／10（現行）／12／15／20％；不要求擠壓（none）。
  動態：前 10 天帶寬在自己過去 125 天的最低百分位 ≤ 10／20／30（跟喇叭口 h2 同一個算法）。
參考列：b1f 原版（含 bwy 15）。

跟 backtest.py 同一套進出場與基準：隔天開盤進、抱 1／5／10／20 天收盤出；超額＝減同日成交金額 ≥ 1 億全市場平均；
t＝每個進場日平均、Newey-West；淨＝原始報酬 − 隔夜成本 0.386%（每筆平均 net、每日平均 net_d 並列）。

事先登記（2026-10-28，看結果前寫死、先推上 GitHub）：
  主要格＝做多、持有 10 天，判定期間＝2021-02-24～2025-08-31。
  通過＝超額 > 0、t ≥ 2、淨（每筆與每日都要）> 0。9 個門檻純靠運氣約 0.4 格 |t| ≥ 2。
  門檻要「高原」：通過的門檻，相鄰的門檻（固定表裡前後一格、動態表裡前後一格）超額也要 > 0，
  否則只算單點、不採用。勝率、賺賠比、1／5／20 天、2025-09 以後只當參考。
  過去結果（研究_開布林）：往上開布林隔天追是反指標，b1f 首日 10 天測不出來；預期大多仍測不出來。

用法：python bt_squeeze.py [天數] → data/bt_squeeze.json
"""
import json
import os
import sys
from collections import defaultdict

import backtest as B
from bt_patterns import stats

HS = B.HORIZONS
SPLIT = "20250901"
MAIN_H = 10

CORE = dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, first=1)
VARIANTS = [
    ("none", "不要求擠壓", dict(CORE)),
    ("w8", "固定 ≤ 8%", dict(CORE, wmin=8)),
    ("w10", "固定 ≤ 10%（現行）", dict(CORE, wmin=10)),
    ("w12", "固定 ≤ 12%", dict(CORE, wmin=12)),
    ("w15", "固定 ≤ 15%", dict(CORE, wmin=15)),
    ("w20", "固定 ≤ 20%", dict(CORE, wmin=20)),
    ("p10", "動態：自己最窄 10%", dict(CORE, sqp=10)),
    ("p20", "動態：自己最窄 20%", dict(CORE, sqp=20)),
    ("p30", "動態：自己最窄 30%", dict(CORE, sqp=30)),
    ("b1f", "參考：b1f 原版（含昨天帶寬 ≤ 15%）", dict(CORE, wmin=10, bwy=15)),
]
NEIGH = {"w8": ["w10"], "w10": ["w8", "w12"], "w12": ["w10", "w15"], "w15": ["w12", "w20"], "w20": ["w15"],
         "p10": ["p20"], "p20": ["p10", "p30"], "p30": ["p20"]}


def main():
    args = sys.argv[1:]
    limit = next((int(a) for a in args if a.isdigit()), None)
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    days = []
    recs = defaultdict(list)            # (變體, h) -> [(di, ret, bm)]

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1:
                continue
            for key, _, spec in VARIANTS:
                try:
                    ok = B.passes(spec, base, d, krows.get(code))
                except (TypeError, KeyError):
                    ok = False
                if not ok:
                    continue
                for h in HS:
                    r, b = fwd.get(h), bm.get(h)
                    if r is not None and b is not None:
                        recs[(key, h)].append((di, r, b))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    split_i = next((k for k, d in enumerate(days) if d >= SPLIT), len(days))
    out = {}
    for key, lab, _ in VARIANTS:
        hs = {}
        for h in HS:
            rr = recs.get((key, h), [])
            hs[str(h)] = dict(all=stats(rr, 1, h),
                              pre=stats([x for x in rr if x[0] < split_i], 1, h),
                              post=stats([x for x in rr if x[0] >= split_i], 1, h))
        out[key] = dict(label=lab, h=hs)

    def m_of(k):
        return out[k]["h"][str(MAIN_H)]["pre"]

    def ok3(m):
        return bool(m and m["ex"] is not None and m["ex"] > 0 and m["t"] is not None and m["t"] >= 2
                    and m["net"] > 0 and m["net_d"] > 0)

    passed = []
    for key, _, _ in VARIANTS:
        m = m_of(key)
        out[key]["pass3"] = ok3(m)
        nb = NEIGH.get(key, [])
        plateau = all(m_of(n) and m_of(n)["ex"] is not None and m_of(n)["ex"] > 0 for n in nb) if nb else False
        out[key]["plateau"] = plateau
        out[key]["pass"] = out[key]["pass3"] and plateau and key != "b1f"
        if out[key]["pass"]:
            passed.append(key)
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, main_h=MAIN_H, cost=B.COST_OVERNIGHT,
                lucky=round(9 * 0.0455, 1), passed=passed)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "bt_squeeze.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, variants=out), fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=擠壓門檻::%d 天，登記判定通過：%s" % (len(days), "、".join(passed) or "無"))
    lines = []
    for key, lab, _ in VARIANTS:
        m = m_of(key)
        q = out[key]["h"][str(MAIN_H)]["post"]
        if m:
            lines.append("%s：n%d 勝%.0f%% 超額%+.2f t%s 淨%+.2f／%+.2f｜近一年%s" % (
                key, m["n"], m["win"], m["ex"], m["t"], m["net"], m["net_d"], ("%+.2f" % q["ex"]) if q else "—"))
        else:
            lines.append("%s：樣本不足" % key)
    for k in range(0, len(lines), 5):
        print("::notice title=擠壓 10 天樣本外 %d::%s" % (k // 5 + 1, " ／ ".join(lines[k:k + 5])))
    for h in HS:
        if h == MAIN_H:
            continue
        ls = []
        for key, _, _ in VARIANTS:
            m = out[key]["h"][str(h)]["pre"]
            if m:
                ls.append("%s 勝%.0f%% 超額%+.2f t%s 淨%+.2f" % (key, m["win"], m["ex"], m["t"], m["net"]))
        print("::notice title=擠壓 %d 天樣本外::%s" % (h, " ／ ".join(ls)))


if __name__ == "__main__":
    main()
