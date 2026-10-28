#!/usr/bin/env python3
"""回踩支撐反彈（做多）／反彈到壓力被壓回（做空）（2026-10-28，使用者）。

使用者 10-28：上漲常沿布林上軌走，漲一段一定回踩，回踩 10MA、20MA，甚至下軌，這些位置跟關鍵支撐有關，
碰到就反彈；要找的是「即將上漲／下跌」（短天期），要把量價一起看。

定義（看結果前寫死，門檻用整數或通行寫法，沒用資料挑）：
  母體：成交金額 ≥ 1 億。訊號日收盤判斷，隔天開盤進，持有 1／5／10／20 天（回測引擎既有的），主要格 5 天。
  趨勢（做多）：
    多頭＝月線上升（20MA 比昨天高）且收盤在 60MA 之上。
    沿上軌＝多頭，而且前 10 個交易日（不含今天）裡至少 4 天布林位階 ≥ 8（貼上軌）。
  回踩的位置（做多）：
    10MA／20MA／布林下軌：今天最低 ≤ 線 × 1.01、收盤 ≥ 線（碰到但守住），且昨天收盤 > 線 × 1.01（從上面下來，第一次碰）。
    前波高點：最近 60 天內、已確認的轉折高點 P（前後各 5 天最高），近 20 天內有收盤站上 P × 1.01（突破過），
              今天最低 ≤ P × 1.01、收盤 ≥ P（回測守住）。
  量價：量縮＝均量比（今天量 ÷ 前 20 日均量）≤ 0.8；不限＝不看量。
  K 棒：收紅＝收盤 > 開盤；不限。
  做空是鏡像：空頭＝月線下彎且收盤在 60MA 之下；沿下軌＝前 10 天至少 4 天位階 ≤ −8；
    反彈到 10MA／20MA／上軌／前波低點（跌破過的轉折低點）：今天最高 ≥ 線 × 0.99、收盤 ≤ 線、昨天收盤 < 線 × 0.99；
    量縮同上；K 棒＝收黑。
  格數：做多 2 趨勢 × 4 位置 × 2 量 × 2 K ＝ 32；做空 32；共 64 格。

跟 backtest.py 同一套進出場與基準：超額＝照方向減同日母體平均；t＝每個進場日平均、Newey-West；
淨＝照方向原始報酬 − 隔夜成本 0.386%（每筆 net、每日 net_d）。

事先登記（2026-10-28，看結果前寫死、先推上 GitHub）：
  主要格＝持有 5 天，判定期間＝2021-02-24～2025-08-31。
  通過＝照方向超額 > 0、t ≥ 2、淨（每筆與每日）> 0、進場日 ≥ 30。64 格純靠運氣約 3 格 |t| ≥ 2。
  過去相關結果（測試結果、CLAUDE.md 支撐壓力列）：非多頭碰壓力沒過 5 天比大盤差（濾網）、長時間支撐守住方向相反。
  1／10 天、2025-09 以後只當參考。

用法：python bt_pullback.py [天數] → data/bt_pullback.json
"""
import json
import os
import sys
from collections import defaultdict

import backtest as B
from bt_patterns import stats

HS = B.HORIZONS
SPLIT = "20250901"
MAIN_H = 5
W = 5
LEVELS_L = ["ma10", "ma20", "lo", "piv"]
LEVELS_S = ["ma10", "ma20", "up", "piv"]
LAB = {"ma10": "10MA", "ma20": "20MA", "lo": "布林下軌", "up": "布林上軌", "piv": "前波高／低點"}


def ma(c, i, n):
    w = c[i - n + 1:i + 1]
    if i - n + 1 < 0 or None in w:
        return None
    return sum(w) / n


def pivots(h, l):
    ph, pl = [], []
    for j in range(W, len(h) - W):
        hw, lw = h[j - W:j + W + 1], l[j - W:j + W + 1]
        if None not in hw and h[j] == max(hw) and hw.index(h[j]) == W:
            ph.append(j)
        if None not in lw and l[j] == min(lw) and lw.index(l[j]) == W:
            pl.append(j)
    return ph, pl


def main():
    limit = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    days = []
    recs = defaultdict(list)          # key -> [(di, ret, bm)] by h
    pv_cache = {}

    def level(st, i, name, side):
        c, h, l = st["c"], st["h"], st["l"]
        if name == "ma10":
            return ma(c, i, 10), ma(c, i - 1, 10)
        if name == "ma20":
            return ma(c, i, 20), ma(c, i - 1, 20)
        if name in ("lo", "up"):
            arr = st["bbLo"] if name == "lo" else st["bbUp"]
            return arr[i], arr[i - 1]
        # 前波高（做多）／前波低（做空）：已確認（j + W ≤ i − 1）、60 天內、近 20 天曾收盤越過
        ph, pl = pv_cache[id(st)]
        js = ph if side > 0 else pl
        best = None
        for j in reversed(js):
            if j + W > i - 1:
                continue
            if j < i - 60:
                break
            P = h[j] if side > 0 else l[j]
            crossed = any(c[k] is not None and ((c[k] > P * 1.01) if side > 0 else (c[k] < P * 0.99))
                          for k in range(max(j + 1, i - 20), i))
            if crossed:
                best = P
                break
        return best, best

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1 or i < 61:
                continue
            c, h, l, o, pos = st["c"], st["h"], st["l"], st["o"], st["pos"]
            if None in (c[i], h[i], l[i], o[i], c[i - 1]):
                continue
            s20, m60 = d.get("slope20"), d.get("ma60")
            if s20 is None or m60 is None:
                continue
            vr = d.get("vr20")
            if id(st) not in pv_cache:
                pv_cache[id(st)] = pivots(h, l)
            for side in (1, -1):
                trend = (s20 > 0 and c[i] > m60) if side > 0 else (s20 < 0 and c[i] < m60)
                if not trend:
                    continue
                prev_pos = [p for p in pos[i - 10:i] if p is not None]
                ride = len(prev_pos) >= 8 and sum(1 for p in prev_pos if (p >= 8 if side > 0 else p <= -8)) >= 4
                for lv in (LEVELS_L if side > 0 else LEVELS_S):
                    x, xp = level(st, i, lv, side)
                    if x is None or xp is None or x <= 0:
                        continue
                    if side > 0:
                        touch = l[i] <= x * 1.01 and c[i] >= x and c[i - 1] > xp * 1.01
                    else:
                        touch = h[i] >= x * 0.99 and c[i] <= x and c[i - 1] < xp * 0.99
                    if lv == "piv":
                        touch = (l[i] <= x * 1.01 and c[i] >= x) if side > 0 else (h[i] >= x * 0.99 and c[i] <= x)
                    if not touch:
                        continue
                    shrink = vr is not None and vr <= 0.8
                    candle = (c[i] > o[i]) if side > 0 else (c[i] < o[i])
                    for tr in (("trend", True), ("ride", ride)):
                        if not tr[1]:
                            continue
                        for vv in (("any", True), ("shrink", shrink)):
                            if not vv[1]:
                                continue
                            for kk in (("any", True), ("candle", candle)):
                                if not kk[1]:
                                    continue
                                key = "%s|%s|%s|%s|%s" % ("L" if side > 0 else "S", tr[0], lv, vv[0], kk[0])
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
        side = 1 if key[0] == "L" else -1
        hs = {}
        for hh in HS:
            rr = recs.get((key, hh), [])
            hs[str(hh)] = dict(all=stats(rr, side, hh), pre=stats([x for x in rr if x[0] < split_i], side, hh),
                               post=stats([x for x in rr if x[0] >= split_i], side, hh))
        m = hs[str(MAIN_H)]["pre"]
        ok = bool(m and m["nd"] >= 30 and m["ex"] is not None and m["ex"] > 0 and m["t"] is not None and m["t"] >= 2
                  and m["net"] > 0 and m["net_d"] > 0)
        out[key] = dict(h=hs, pass_=ok)
        if ok:
            passed.append(key)
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, main_h=MAIN_H, cost=B.COST_OVERNIGHT,
                n_cells=64, tested=len(keys), lucky=round(64 * 0.0455, 1), passed=passed, labels=LAB)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "bt_pullback.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, cells=out), fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=回踩反彈::%d 天，%d 格有資料，登記判定通過：%s" % (len(days), len(keys), "、".join(passed) or "無"))
    lines = []
    for key in keys:
        m = out[key]["h"][str(MAIN_H)]["pre"]
        q = out[key]["h"][str(MAIN_H)]["post"]
        m1 = out[key]["h"]["1"]["pre"]
        if m:
            lines.append("%s n%d 勝%.0f%% 超額%+.2f t%s 淨%+.2f／%+.2f｜1天%s｜近一年%s" % (
                key, m["n"], m["win"], m["ex"], m["t"], m["net"], m["net_d"],
                ("%+.2f" % m1["ex"]) if m1 else "—", ("%+.2f" % q["ex"]) if q else "—"))
    for k in range(0, len(lines), 6):
        print("::notice title=回踩 5 天樣本外 %d::%s" % (k // 6 + 1, " ／ ".join(lines[k:k + 6])))


if __name__ == "__main__":
    main()
