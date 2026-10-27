#!/usr/bin/env python3
"""K 線型態辨識（日線，多空都有）。2026-10-27 重寫（10-21 的 study/patterns.py 沒進備份），定義沿用 10-21 寫死的版本，
另補旗形（空）、V 型反轉、杯柄。只用「訊號日當天收盤以前」看得到的資料，回測（bt_patterns.py）與籌碼看板（--site）共用這一份。

共同：
  轉折高（低）點＝最高（最低）價是前後各 5 天裡最高（最低）的；第 j 天的轉折要到 j+5 天收盤才確認，
  所以第 i 天只用 j + 5 ≤ i − 1 的轉折。
  訊號日＝收盤「第一次」突破（跌破）頸線的那天；隔天開盤才進場。同一檔同一型態 20 天內只算第一次。
  帶量＝訊號日量 ≥ 前 20 日均量 1.5 倍（另外標，不是條件）。
  目標價＝頸線 ± 型態高度；停損＝型態最低（最高）點。都只是畫圖參考。

多方：
  W 底：兩個轉折低 L1、L2 相隔 10～60 天、差距 ±3%；中間最高的轉折高 P 比兩低點高 ≥ 8%（頸線＝P）；L2 之後 30 天內收盤站上 P。
  頭肩底：連續三個轉折低（左肩、頭、右肩），頭比兩肩都低 ≥ 3%、兩肩差距 ±5%、左肩到右肩 ≤ 120 天；
          頸線＝兩肩之間轉折高的最高者；右肩後 30 天內收盤站上頸線。
  上升三角形：最近 60 天的轉折高 ≥ 2 個、彼此差距 ≤ 4%（水平壓力＝最高者）；轉折低 ≥ 2 個、每個比前一個高 ≥ 2%；收盤 > 壓力 × 1.01。
  對稱三角形·往上：最近 60 天轉折高 ≥ 2 個、每個比前一個低 ≥ 2%；轉折低 ≥ 2 個、每個比前一個高 ≥ 2%；收盤 > 最後一個轉折高。
  箱型·往上：最近 60 天轉折高 ≥ 2 個彼此差距 ≤ 4%、轉折低 ≥ 2 個彼此差距 ≤ 4%，箱高（頂 ÷ 底 − 1）≥ 6%；收盤 > 箱頂 × 1.01。
  旗形（多）：旗桿＝10 天收盤漲 ≥ 15%；之後 3～10 天整理（最高 ÷ 最低 − 1 ≤ 8%、最低沒跌破旗桿漲幅的一半）；收盤 > 整理區最高。
  杯柄：杯左緣轉折高 A、右緣轉折高 C 相隔 30～150 天、差距 ±5%，中間沒有更高的；杯底比 A 低 12～35%；
        C 之後 5～25 天是柄（最低不低於 C 的 88%，也不低於杯的一半）；收盤 > C。
  V 型反轉（底）：最近 5 天內出現低點 m，m 之前 10 天的最高到 m 跌 ≥ 15%，m 起量 ≥ 前 20 日均量 2 倍；
                  收盤收復跌幅一半以上（頸線＝一半的位置）。
空方：
  M 頭、頭肩頂、下降三角形、對稱三角形·往下、箱型·往下、旗形（空）、V 型反轉（頂）＝上面的鏡像。
  （杯柄不做倒杯：教學網站都只講多方杯柄。）

用法：
  import patterns; patterns.detect(o, h, l, c, v) → [事件 dict]
  python patterns.py --site [天數]   → data/patterns/<代號>.json（籌碼看板用，最近 N 天的型態，預設 250）
"""
import json
import os
import sys

W = 5            # 轉折點左右各幾天
DEDUP = 20       # 同型態幾天內只算第一次

LONG = ["W 底", "頭肩底", "上升三角形", "對稱三角形·往上", "箱型·往上", "旗形（多）", "杯柄", "V 型反轉（底）"]
SHORT = ["M 頭", "頭肩頂", "下降三角形", "對稱三角形·往下", "箱型·往下", "旗形（空）", "V 型反轉（頂）"]
SIDE = {**{k: 1 for k in LONG}, **{k: -1 for k in SHORT}}
KEY = {"W 底": "w", "頭肩底": "hsb", "上升三角形": "asc", "對稱三角形·往上": "symu", "箱型·往上": "boxu",
       "旗形（多）": "flagu", "杯柄": "cup", "V 型反轉（底）": "vb",
       "M 頭": "m", "頭肩頂": "hst", "下降三角形": "desc", "對稱三角形·往下": "symd", "箱型·往下": "boxd",
       "旗形（空）": "flagd", "V 型反轉（頂）": "vt"}


def _pivots(h, l):
    n = len(h)
    ph, pl = [], []
    for j in range(W, n - W):
        hw = h[j - W:j + W + 1]
        lw = l[j - W:j + W + 1]
        if h[j] is not None and None not in hw and h[j] == max(hw) and hw.index(h[j]) == W:
            ph.append(j)
        if l[j] is not None and None not in lw and l[j] == min(lw) and lw.index(l[j]) == W:
            pl.append(j)
    return ph, pl


def _first_cross_up(c, i, level):
    return c[i] > level and c[i - 1] <= level


def _first_cross_dn(c, i, level):
    return c[i] < level and c[i - 1] >= level


def _ev(name, i, neck, pts, tgt, stop):
    return dict(type=name, key=KEY[name], side=SIDE[name], i=i, neck=round(neck, 2),
                pts=[(int(j), round(float(p), 2)) for j, p in pts], target=round(tgt, 2), stop=round(stop, 2))


def detect(o, h, l, c, v, start=0):
    """回傳事件清單（依訊號日排序）。只看 i 以前的資料。start 之前的訊號不輸出（仍用來去重）。"""
    n = len(c)
    # 缺值（例如鎖漲停沒有振幅的檔案格式）用收盤補，量缺當 0
    h = [x if x is not None else c[k] for k, x in enumerate(h)]
    l = [x if x is not None else c[k] for k, x in enumerate(l)]
    v = [x if x is not None else 0.0 for x in v]
    ph_all, pl_all = _pivots(h, l)
    out, last = [], {}
    a = b = 0                                   # 已確認的轉折數（ph_all[:a]、pl_all[:b]）
    for i in range(1, n):
        while a < len(ph_all) and ph_all[a] + W <= i - 1:
            a += 1
        while b < len(pl_all) and pl_all[b] + W <= i - 1:
            b += 1
        if c[i] is None or c[i - 1] is None:
            continue
        PH, PL = ph_all[:a], pl_all[:b]
        evs = []
        # ── W 底／M 頭
        if PL:
            j2 = PL[-1]
            if i - j2 <= 30:
                for j1 in reversed(PL[:-1]):
                    if j2 - j1 > 60:
                        break
                    if j2 - j1 < 10 or abs(l[j2] / l[j1] - 1) > 0.03:
                        continue
                    mid = [j for j in PH if j1 < j < j2]
                    if not mid:
                        continue
                    jp = max(mid, key=lambda j: h[j])
                    P = h[jp]
                    if P < max(l[j1], l[j2]) * 1.08:
                        continue
                    if all(x <= P for x in c[j2:i]) and _first_cross_up(c, i, P):
                        lo = min(l[j1], l[j2])
                        evs.append(_ev("W 底", i, P, [(j1, l[j1]), (jp, P), (j2, l[j2]), (i, c[i])], P + (P - lo), lo))
                    break
        if PH:
            j2 = PH[-1]
            if i - j2 <= 30:
                for j1 in reversed(PH[:-1]):
                    if j2 - j1 > 60:
                        break
                    if j2 - j1 < 10 or abs(h[j2] / h[j1] - 1) > 0.03:
                        continue
                    mid = [j for j in PL if j1 < j < j2]
                    if not mid:
                        continue
                    jp = min(mid, key=lambda j: l[j])
                    P = l[jp]
                    if P > min(h[j1], h[j2]) * 0.92:
                        continue
                    if all(x >= P for x in c[j2:i]) and _first_cross_dn(c, i, P):
                        hi = max(h[j1], h[j2])
                        evs.append(_ev("M 頭", i, P, [(j1, h[j1]), (jp, P), (j2, h[j2]), (i, c[i])], P - (hi - P), hi))
                    break
        # ── 頭肩底／頭肩頂
        if len(PL) >= 3:
            js, jh, jr = PL[-3], PL[-2], PL[-1]
            if (i - jr <= 30 and jr - js <= 120 and l[jh] <= l[js] * 0.97 and l[jh] <= l[jr] * 0.97
                    and abs(l[jr] / l[js] - 1) <= 0.05):
                mid = [j for j in PH if js < j < jr]
                if mid:
                    jn = max(mid, key=lambda j: h[j])
                    N = h[jn]
                    if all(x <= N for x in c[jr:i]) and _first_cross_up(c, i, N):
                        evs.append(_ev("頭肩底", i, N, [(js, l[js]), (jh, l[jh]), (jr, l[jr]), (jn, N), (i, c[i])],
                                       N + (N - l[jh]), l[jh]))
        if len(PH) >= 3:
            js, jh, jr = PH[-3], PH[-2], PH[-1]
            if (i - jr <= 30 and jr - js <= 120 and h[jh] >= h[js] * 1.03 and h[jh] >= h[jr] * 1.03
                    and abs(h[jr] / h[js] - 1) <= 0.05):
                mid = [j for j in PL if js < j < jr]
                if mid:
                    jn = min(mid, key=lambda j: l[j])
                    N = l[jn]
                    if all(x >= N for x in c[jr:i]) and _first_cross_dn(c, i, N):
                        evs.append(_ev("頭肩頂", i, N, [(js, h[js]), (jh, h[jh]), (jr, h[jr]), (jn, N), (i, c[i])],
                                       N - (h[jh] - N), h[jh]))
        # ── 三角形、箱型（最近 60 天的轉折）
        hs = [j for j in PH if j >= i - 60]
        ls = [j for j in PL if j >= i - 60]
        if len(hs) >= 2 and len(ls) >= 2:
            hv = [h[j] for j in hs]
            lv = [l[j] for j in ls]
            flat_h = max(hv) / min(hv) - 1 <= 0.04
            flat_l = max(lv) / min(lv) - 1 <= 0.04
            up_l = all(lv[k] >= lv[k - 1] * 1.02 for k in range(1, len(lv)))
            dn_h = all(hv[k] <= hv[k - 1] * 0.98 for k in range(1, len(hv)))
            pts = sorted([(j, h[j]) for j in hs] + [(j, l[j]) for j in ls])
            if flat_h and up_l:
                R = max(hv)
                if _first_cross_up(c, i, R * 1.01):
                    evs.append(_ev("上升三角形", i, R, pts + [(i, c[i])], R + (R - lv[0]), lv[-1]))
            if flat_l and dn_h:
                S = min(lv)
                if _first_cross_dn(c, i, S * 0.99):
                    evs.append(_ev("下降三角形", i, S, pts + [(i, c[i])], S - (hv[0] - S), hv[-1]))
            if dn_h and up_l:
                if _first_cross_up(c, i, hv[-1]):
                    evs.append(_ev("對稱三角形·往上", i, hv[-1], pts + [(i, c[i])], hv[-1] + (hv[0] - lv[0]), lv[-1]))
                if _first_cross_dn(c, i, lv[-1]):
                    evs.append(_ev("對稱三角形·往下", i, lv[-1], pts + [(i, c[i])], lv[-1] - (hv[0] - lv[0]), hv[-1]))
            if flat_h and flat_l:
                T, B = max(hv), min(lv)
                if T / B - 1 >= 0.06:
                    if _first_cross_up(c, i, T * 1.01):
                        evs.append(_ev("箱型·往上", i, T, pts + [(i, c[i])], T + (T - B), B))
                    if _first_cross_dn(c, i, B * 0.99):
                        evs.append(_ev("箱型·往下", i, B, pts + [(i, c[i])], B - (T - B), T))
        # ── 旗形（旗桿 10 天、整理 3～10 天）
        for f in range(3, 11):
            e = i - f - 1
            s = e - 10
            if s < 0:
                break
            fh = h[i - f:i]
            fl = l[i - f:i]
            if None in fh or None in fl:
                continue
            top, bot = max(fh), min(fl)
            if top / bot - 1 > 0.08:
                continue
            g = c[e] / c[s] - 1
            if g >= 0.15 and bot >= c[s] + 0.5 * (c[e] - c[s]) and c[i] > top and c[i - 1] <= top:
                evs.append(_ev("旗形（多）", i, top, [(s, c[s]), (e, c[e]), (i - f, top), (i - 1, bot), (i, c[i])],
                               top + (c[e] - c[s]), bot))
                break
            if g <= -0.15 and top <= c[s] + 0.5 * (c[e] - c[s]) and c[i] < bot and c[i - 1] >= bot:
                evs.append(_ev("旗形（空）", i, bot, [(s, c[s]), (e, c[e]), (i - f, bot), (i - 1, top), (i, c[i])],
                               bot - (c[s] - c[e]), top))
                break
        # ── 杯柄
        if len(PH) >= 2:
            jc = PH[-1]
            if 5 <= i - jc <= 25:
                for ja in reversed(PH[:-1]):
                    if jc - ja > 150:
                        break
                    if jc - ja < 30 or abs(h[jc] / h[ja] - 1) > 0.05:
                        continue
                    rim = max(h[ja], h[jc])
                    if max(h[ja + 1:jc]) > rim:
                        continue
                    jb = min(range(ja, jc + 1), key=lambda j: l[j])
                    depth = 1 - l[jb] / h[ja]
                    if not (0.12 <= depth <= 0.35):
                        continue
                    hl = min(l[jc + 1:i])
                    if hl < h[jc] * 0.88 or hl < l[jb] + 0.5 * (h[ja] - l[jb]):
                        continue
                    if all(x <= h[jc] for x in c[jc:i]) and _first_cross_up(c, i, h[jc]):
                        evs.append(_ev("杯柄", i, h[jc], [(ja, h[ja]), (jb, l[jb]), (jc, h[jc]), (i, c[i])],
                                       h[jc] + (h[ja] - l[jb]), hl))
                    break
        # ── V 型反轉
        if i >= 36:
            m = min(range(i - 5, i), key=lambda j: l[j])
            pk = max(range(m - 10, m), key=lambda j: h[j])
            avg = sum(v[m - 20:m]) / 20 if None not in v[m - 20:m] else 0
            if avg > 0 and h[pk] > 0 and l[m] / h[pk] - 1 <= -0.15 and max(v[m:i + 1]) >= 2 * avg:
                N = l[m] + 0.5 * (h[pk] - l[m])
                if _first_cross_up(c, i, N):
                    evs.append(_ev("V 型反轉（底）", i, N, [(pk, h[pk]), (m, l[m]), (i, c[i])], h[pk], l[m]))
            m = max(range(i - 5, i), key=lambda j: h[j])
            pk = min(range(m - 10, m), key=lambda j: l[j])
            avg = sum(v[m - 20:m]) / 20 if None not in v[m - 20:m] else 0
            if avg > 0 and l[pk] > 0 and h[m] / l[pk] - 1 >= 0.15 and max(v[m:i + 1]) >= 2 * avg:
                N = h[m] - 0.5 * (h[m] - l[pk])
                if _first_cross_dn(c, i, N):
                    evs.append(_ev("V 型反轉（頂）", i, N, [(pk, l[pk]), (m, h[m]), (i, c[i])], l[pk], h[m]))
        for e in evs:
            k = e["type"]
            if k in last and i - last[k] <= DEDUP:
                continue
            last[k] = i
            if i >= start:
                base = sum(v[i - 20:i]) / 20 if i >= 20 and None not in v[i - 20:i] else 0
                e["vol"] = bool(base > 0 and v[i] >= 1.5 * base)
                out.append(e)
    return out


def _site(days=250):
    """籌碼看板用：每檔最近 N 天的型態 → data/patterns/<代號>.json（只用 repo 裡的價量）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    sd = os.path.join(here, "data", "stock")
    od = os.path.join(here, "data", "patterns")
    os.makedirs(od, exist_ok=True)
    n_ev = n_st = 0
    for fn in sorted(os.listdir(sd)):
        if not fn.endswith(".txt") or fn.startswith("_"):
            continue
        rows = []
        for line in open(os.path.join(sd, fn), encoding="utf-8"):
            if not line[:1].isdigit():
                continue
            p = line.strip().split(",")
            try:
                rows.append((p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5] or 0)))
            except (ValueError, IndexError):
                continue
        rows = [r for r in rows if r[4] > 0 and r[2] > 0 and r[3] > 0]
        if len(rows) < 60:
            continue
        d = [r[0] for r in rows]
        evs = detect([r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
                     [r[4] for r in rows], [r[5] for r in rows], start=max(0, len(rows) - days))
        out = [dict(t=e["type"], k=e["key"], s=e["side"], d=d[e["i"]], neck=e["neck"], tgt=e["target"],
                    stop=e["stop"], vol=e["vol"], pts=[[d[j], p] for j, p in e["pts"]]) for e in evs]
        path = os.path.join(od, fn[:-4] + ".json")
        txt = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old != txt:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(txt)
        n_ev += len(out)
        n_st += 1
    print("型態：%d 檔、%d 個事件（最近 %d 天）" % (n_st, n_ev, days))


if __name__ == "__main__":
    if "--site" in sys.argv:
        nd = next((int(a) for a in sys.argv[1:] if a.isdigit()), 250)
        _site(nd)
