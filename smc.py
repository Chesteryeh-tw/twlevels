#!/usr/bin/env python3
"""SMC／ICT 概念的日線機械化版本＋分價量表（2026-10-27，使用者要研究加看板）。
回測（bt_smc.py）與籌碼看板（--site）共用這一份；只用「當天收盤以前」看得到的資料。

定義（網路通行寫法：LuxAlgo、smartmoneyconcepts 套件、XS／StatOasis 的機械化版本；看結果前寫死）：
  轉折高／低：前後各 5 天最高／最低（跟 patterns.py 一樣），第 j 天的轉折要到 j+5 天收盤才確認。
  市場結構：
    往上破＝收盤第一次站上「最近一個已確認、還沒被破的轉折高」。原本趨勢是空（上一次破的是低點）→ CHoCH↑（轉折），
            否則 → BOS↑（續勢）。往下鏡像。
  Order Block（OB）：發生往上破時，從「破之前那段的最低點」到破的前一天，最後一根收黑 K 的高低區間＝多方 OB；
            往下鏡像（最後一根收紅 K）。之後 30 天內價格第一次回到區間（最低 ≤ 區間上緣）而且收盤沒跌破區間下緣＝「OB 回測守住」。
  Fair Value Gap（FVG）：第 i 天最低 > 第 i−2 天最高（多方缺口；寬度 ≥ 0.3 × ATR14）；空方鏡像。
            之後 20 天內第一次回到缺口（最低 ≤ 缺口上緣）而且收盤沒跌破缺口下緣＝「FVG 回補守住」。
  流動性掃蕩：最低跌破「最近一個已確認、還沒被收盤跌破的轉折低」但收盤站回上面＝掃賣方流動性（看多）；鏡像看空。
  分價量表（Volume Profile）：前 60 天（不含今天）每天的量平均分到當天高低之間的 24 格；最多量那格＝POC；
            從 POC 往兩邊加到 70% 量＝價值區（VAH／VAL）。「回測 POC 守住」＝昨收在 POC 上、今天最低碰到 POC、收盤仍在上；鏡像看空。
  顯示用（不回測）：Premium／Discount（最近轉折高低區間的上半／下半）、等高／等低（兩個轉折高或低差距 ≤ 0.1 × ATR14）。

事件 key：choch_u／choch_d／bos_u／bos_d／ob_u／ob_d／fvg_u／fvg_d／sweep_u／sweep_d／poc_u／poc_d（_u 看多、_d 看空）
用法：python smc.py --site [天數] → data/smc/<代號>.json
"""
import json
import os
import sys

W = 5
NAMES = {"choch_u": "CHoCH↑ 結構轉多", "choch_d": "CHoCH↓ 結構轉空", "bos_u": "BOS↑ 續漲破高", "bos_d": "BOS↓ 續跌破低",
         "ob_u": "多方 OB 回測守住", "ob_d": "空方 OB 回測守住", "fvg_u": "多方 FVG 回補守住", "fvg_d": "空方 FVG 回補守住",
         "sweep_u": "掃低點流動性後收回", "sweep_d": "掃高點流動性後收回", "poc_u": "回測 POC 守住", "poc_d": "反彈 POC 不過"}
SIDE = {k: (1 if k.endswith("_u") else -1) for k in NAMES}


def _atr(h, l, c, i, n=14):
    if i < n:
        return None
    s = 0.0
    for k in range(i - n + 1, i + 1):
        s += max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1]))
    return s / n


def volume_profile(h, l, v, a, b, bins=24):
    """[a, b) 這段的分價量表 → (POC, VAL, VAH)；資料不夠回 None。"""
    if b - a < 20:
        return None
    lo = min(l[a:b])
    hi = max(h[a:b])
    if not hi > lo:
        return None
    step = (hi - lo) / bins
    vol = [0.0] * bins
    for k in range(a, b):
        if not v[k]:
            continue
        x0 = int((l[k] - lo) / step)
        x1 = int((h[k] - lo) / step)
        x0 = min(max(x0, 0), bins - 1)
        x1 = min(max(x1, 0), bins - 1)
        w = v[k] / (x1 - x0 + 1)
        for x in range(x0, x1 + 1):
            vol[x] += w
    tot = sum(vol)
    if tot <= 0:
        return None
    p = max(range(bins), key=lambda x: vol[x])
    lo_i = hi_i = p
    acc = vol[p]
    while acc < 0.7 * tot and (lo_i > 0 or hi_i < bins - 1):
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= dn:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]
    return (lo + (p + 0.5) * step, lo + lo_i * step, lo + (hi_i + 1) * step)


def detect(o, h, l, c, v, start=0, with_vp=True, state=False):
    n = len(c)
    h = [x if x is not None else c[k] for k, x in enumerate(h)]
    l = [x if x is not None else c[k] for k, x in enumerate(l)]
    o = [x if x is not None else c[k] for k, x in enumerate(o)]
    v = [x if x is not None else 0.0 for x in v]
    ph, pl = [], []
    for j in range(W, n - W):
        hw, lw = h[j - W:j + W + 1], l[j - W:j + W + 1]
        if h[j] == max(hw) and hw.index(h[j]) == W:
            ph.append(j)
        if l[j] == min(lw) and lw.index(l[j]) == W:
            pl.append(j)
    a = b = 0
    sh = sl = None            # 最近已確認、還沒被收盤突破的轉折高／低（索引）
    trend = 0
    obs, fvgs = [], []        # 進行中的區間：dict(side, top, bot, born, kind)
    out, lines = [], []
    swept = set()
    for i in range(2, n):
        while a < len(ph) and ph[a] + W <= i - 1:
            sh = ph[a]
            a += 1
        while b < len(pl) and pl[b] + W <= i - 1:
            sl = pl[b]
            b += 1
        evs = []
        atr = _atr(h, l, c, i)
        # ── 區間回測（先判斷舊區間，今天新生的區間明天起才算）
        for z in obs + fvgs:
            if z.get("done"):
                continue
            if i - z["born"] > (30 if z["kind"] == "ob" else 20):
                z["done"] = True
                continue
            if z["side"] > 0 and l[i] <= z["top"]:
                z["done"] = True
                if c[i] >= z["bot"]:
                    evs.append((z["kind"] + "_u", z["top"], z["bot"], z["born"]))
            elif z["side"] < 0 and h[i] >= z["bot"]:
                z["done"] = True
                if c[i] <= z["top"]:
                    evs.append((z["kind"] + "_d", z["top"], z["bot"], z["born"]))
        # ── 流動性掃蕩（影線刺穿、收盤收回）
        # 同一個轉折點只算第一次被掃
        if sl is not None and sl not in swept and l[i] < l[sl] and c[i] > l[sl]:
            evs.append(("sweep_u", l[sl], l[sl], sl))
            swept.add(sl)
        if sh is not None and sh not in swept and h[i] > h[sh] and c[i] < h[sh]:
            evs.append(("sweep_d", h[sh], h[sh], sh))
            swept.add(sh)
        # ── 結構突破
        if sh is not None and c[i] > h[sh]:
            kind = "choch_u" if trend == -1 else "bos_u"
            m = min(range(sh, i + 1), key=lambda k: l[k])
            ks = [k for k in range(m, i) if c[k] < o[k]]
            k = ks[-1] if ks else m
            obs.append(dict(side=1, top=h[k], bot=l[k], born=i, kind="ob", src=k))
            evs.append((kind, h[sh], h[sh], sh))
            lines.append((sh, i, h[sh], kind))
            trend, sh = 1, None
        if sl is not None and c[i] < l[sl]:
            kind = "choch_d" if trend == 1 else "bos_d"
            m = max(range(sl, i + 1), key=lambda k: h[k])
            ks = [k for k in range(m, i) if c[k] > o[k]]
            k = ks[-1] if ks else m
            obs.append(dict(side=-1, top=h[k], bot=l[k], born=i, kind="ob", src=k))
            evs.append((kind, l[sl], l[sl], sl))
            lines.append((sl, i, l[sl], kind))
            trend, sl = -1, None
        # ── 新的 FVG
        if atr:
            if l[i] > h[i - 2] and l[i] - h[i - 2] >= 0.3 * atr:
                fvgs.append(dict(side=1, top=l[i], bot=h[i - 2], born=i, kind="fvg", src=i - 1))
            if h[i] < l[i - 2] and l[i - 2] - h[i] >= 0.3 * atr:
                fvgs.append(dict(side=-1, top=l[i - 2], bot=h[i], born=i, kind="fvg", src=i - 1))
        # ── 分價量表 POC
        if with_vp and i >= 61:
            vp = volume_profile(h, l, v, i - 60, i)
            if vp:
                poc = vp[0]
                if c[i - 1] > poc and l[i] <= poc < c[i]:
                    evs.append(("poc_u", poc, poc, i - 60))
                if c[i - 1] < poc and h[i] >= poc > c[i]:
                    evs.append(("poc_d", poc, poc, i - 60))
        if i >= start:
            for key, top, bot, ref in evs:
                out.append(dict(key=key, i=i, top=round(top, 2), bot=round(bot, 2), ref=ref))
        obs = [z for z in obs if not z.get("done")][-20:]
        fvgs = [z for z in fvgs if not z.get("done")][-20:]
    if not state:
        return out
    # 看板用：最後一天還有效的區間、結構線、POC／價值區、Premium／Discount、等高等低
    i = n - 1
    vp = volume_profile(h, l, v, max(0, n - 60), n) if n >= 21 else None
    rng = None
    if ph and pl:
        hi_j, lo_j = ph[-1], pl[-1]
        if ph[-1] + W <= i and pl[-1] + W <= i:
            rng = (h[hi_j], l[lo_j])
    atr = _atr(h, l, c, i) or 0
    eq = []
    for arr, side in ((ph, 1), (pl, -1)):
        conf = [j for j in arr if j + W <= i][-6:]
        for x in range(len(conf) - 1):
            for y in range(x + 1, len(conf)):
                pa, pb = (h[conf[x]], h[conf[y]]) if side > 0 else (l[conf[x]], l[conf[y]])
                if atr and abs(pa - pb) <= 0.1 * atr:
                    eq.append(dict(side=side, a=conf[x], b=conf[y], level=round((pa + pb) / 2, 2)))
    return out, dict(zones=[dict(kind=z["kind"], side=z["side"], top=round(z["top"], 2), bot=round(z["bot"], 2),
                                 born=z["born"], src=z["src"]) for z in obs + fvgs if not z.get("done")],
                     lines=lines[-12:], vp=vp, rng=rng, eq=eq[-4:], trend=trend)


def _site(days=250):
    here = os.path.dirname(os.path.abspath(__file__))
    sd = os.path.join(here, "data", "stock")
    od = os.path.join(here, "data", "smc")
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
        if len(rows) < 70:
            continue
        d = [r[0] for r in rows]
        evs, st = detect([r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows],
                         [r[5] for r in rows], start=max(0, len(rows) - days), state=True)
        out = dict(
            ev=[dict(k=e["key"], d=d[e["i"]], top=e["top"], bot=e["bot"], ref=d[e["ref"]]) for e in evs],
            zones=[dict(k=z["kind"], s=z["side"], top=z["top"], bot=z["bot"], d=d[z["src"]], born=d[z["born"]])
                   for z in st["zones"]],
            lines=[dict(a=d[x], b=d[y], p=round(p, 2), k=k) for x, y, p, k in st["lines"]],
            vp=None if not st["vp"] else dict(poc=round(st["vp"][0], 2), val=round(st["vp"][1], 2), vah=round(st["vp"][2], 2)),
            rng=None if not st["rng"] else dict(hi=round(st["rng"][0], 2), lo=round(st["rng"][1], 2)),
            eq=[dict(s=x["side"], a=d[x["a"]], b=d[x["b"]], p=x["level"]) for x in st["eq"]],
            trend=st["trend"])
        path = os.path.join(od, fn[:-4] + ".json")
        txt = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old != txt:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(txt)
        n_ev += len(out["ev"])
        n_st += 1
    print("SMC：%d 檔、%d 個事件（最近 %d 天）" % (n_st, n_ev, days))


if __name__ == "__main__":
    if "--site" in sys.argv:
        _site(next((int(a) for a in sys.argv[1:] if a.isdigit()), 120))
