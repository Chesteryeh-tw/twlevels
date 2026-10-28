#!/usr/bin/env python3
"""回測工具（2026-10-27）：單一條件與兩兩組合，做多、做空都算，給 bt.html 顯示。

跟 backtest.py 用同一套進出場與基準（直接呼叫 backtest.run 收集），不另寫一套：
  訊號日收盤篩 → 隔天開盤進 → 持有 1／5／10／20 天收盤出；超額＝減同日成交金額 ≥ 1 億全市場平均；
  t 用每個進場日的平均（Newey-West）；淨＝照方向原始報酬 − 隔夜成本 0.386%。
母體：當天成交金額 ≥ 1 億（跟網站各情境一樣）。
做空＝同一批股票、報酬取負號（放空賺的是股票跌）。

這是「探索工具」：組合很多（約 500 組 × 4 持有期 × 2 方向），純靠運氣就會有幾百格 |t| ≥ 2。
所以輸出同時給：①期間切兩段（2021-02～2025-08／2025-09 以後）②全部試過幾格、運氣預期幾格。

用法：python bt_explore.py [--adjust] [天數]
輸出：data/bt_explore.json（統計）、data/bt_explore_curve_1.json、data/bt_explore_curve_10.json（週累計曲線）
"""
import json, math, os, sys
from collections import defaultdict

import backtest as B

COST = B.COST_OVERNIGHT
HS = B.HORIZONS                      # [1, 5, 10, 20]
SPLIT = "20250901"                   # 之前＝樣本外（多數門檻用近一年挑的）；之後＝近一年
MIN_TRADES, MIN_DAYS = 100, 30


def _g(x, k):
    return x.get(k) if x else None


# (key, 標籤, 判斷)；b＝價量、d＝指標、k＝籌碼（成交金額 ≥ 1 億才有）
COND = [
    ("ma_up", "月線上升", lambda b, d, k: (d.get("slope20") or 0) > 0),
    ("ma_dn", "月線下彎", lambda b, d, k: (d.get("slope20") or 0) < 0),
    ("above", "站上月線", lambda b, d, k: (d.get("bbPos") or 0) > 0),
    ("below", "跌破月線", lambda b, d, k: d.get("bbPos") is not None and d["bbPos"] < 0),
    ("upband", "貼上軌（位階 ≥ 8）", lambda b, d, k: (d.get("bbPos") or 0) >= 8),
    ("loband", "貼下軌（位階 ≤ −8）", lambda b, d, k: d.get("bbPos") is not None and d["bbPos"] <= -8),
    ("up_open", "上軌外開（上通斜率 ≥ 3%）", lambda b, d, k: (d.get("slopeUp") or 0) >= 3),
    ("lo_open", "下軌外開（下通斜率 ≤ −3%）", lambda b, d, k: d.get("slopeLo") is not None and d["slopeLo"] <= -3),
    ("squeeze", "擠壓（帶寬 < 10%）", lambda b, d, k: d.get("bbW") is not None and d["bbW"] < 10),
    ("bopen", "開布林（帶寬變寬、前 10 日曾 ≤ 10%）",
     lambda b, d, k: (d.get("bbWChg") or 0) >= 0.5 and d.get("bbWMin10") is not None and d["bbWMin10"] <= 10),
    ("bias_hi", "乖離年線 > 20%", lambda b, d, k: (d.get("biasY") or 0) > 20),
    ("bias_lo", "乖離年線 < −20%", lambda b, d, k: d.get("biasY") is not None and d["biasY"] < -20),
    ("vol15", "帶量（均量比 ≥ 1.5）", lambda b, d, k: (d.get("vr20") or 0) >= 1.5),
    ("vol3", "爆量（均量比 ≥ 3）", lambda b, d, k: (d.get("vr20") or 0) >= 3),
    ("vol_low", "量縮（均量比 ≤ 0.7）", lambda b, d, k: d.get("vr20") is not None and d["vr20"] <= 0.7),
    ("chg_up3", "當天漲 ≥ 3%", lambda b, d, k: (b.get("chgPct") or 0) >= 3),
    ("chg_dn3", "當天跌 ≥ 3%", lambda b, d, k: b.get("chgPct") is not None and b["chgPct"] <= -3),
    ("close_hi", "收在高點（日內位置 ≥ 0.8）", lambda b, d, k: b.get("pos", 0) >= 0.8),
    ("close_lo", "收在低點（日內位置 ≤ 0.2）", lambda b, d, k: b.get("pos", 1) <= 0.2),
    ("run_up3", "連漲 ≥ 3 天", lambda b, d, k: (d.get("runUp") or 0) >= 3),
    ("run_dn3", "連跌 ≥ 3 天", lambda b, d, k: (d.get("runDn") or 0) >= 3),
    ("fgn_buy", "外資買超", lambda b, d, k: (_g(k, "fgn") or 0) > 0),
    ("fgn_sell", "外資賣超", lambda b, d, k: (_g(k, "fgn") or 0) < 0),
    ("fgn_3", "外資連買 ≥ 3 天", lambda b, d, k: (_g(k, "fgnD") or 0) >= 3),
    ("inv_buy", "投信買超", lambda b, d, k: (_g(k, "inv") or 0) > 0),
    ("inv_sell", "投信賣超", lambda b, d, k: (_g(k, "inv") or 0) < 0),
    ("inv_3", "投信連買 ≥ 3 天", lambda b, d, k: (_g(k, "invD") or 0) >= 3),
    ("mgn_up", "融資增加", lambda b, d, k: (_g(k, "mgnChg") or 0) > 0),
    ("mgn_dn", "融資減少", lambda b, d, k: (_g(k, "mgnChg") or 0) < 0),
    ("hedge_buy", "自營避險買超", lambda b, d, k: (_g(k, "dlrH") or 0) > 0),
    ("rev_up", "營收年增 > 0", lambda b, d, k: (d.get("_ryoy") or 0) > 0),
    ("rev_new", "營收創 12 個月新高", lambda b, d, k: bool(d.get("_rnew"))),
]


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


def main():
    args = sys.argv[1:]
    if "--adjust" in args:
        B.ADJUST = True
    limit = next((int(a) for a in args if a.isdigit()), None)

    # 只要收集，不要原本 32 個情境（省時間）
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    rows = []                      # (日期序號, bits, 報酬×4, 基準×4)；用 tuple 省記憶體
    days = []

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1:
                continue
            k = krows.get(code)
            bits = 0
            for j, (_, _, f) in enumerate(COND):
                try:
                    if f(base, d, k):
                        bits |= 1 << j
                except (TypeError, KeyError):
                    pass
            rows.append((di, bits) + tuple(fwd.get(h) for h in HS) + tuple(bm.get(h) for h in HS))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    nC = len(COND)
    combos = [(a,) for a in range(nC)] + [(a, b) for a in range(nC) for b in range(a + 1, nC)]
    split_i = next((i for i, d in enumerate(days) if d >= SPLIT), len(days))
    # 週索引（曲線用）：每 5 個交易日一點
    out, curves = {}, {h: {} for h in HS}
    raws, cnts = {h: {} for h in HS}, {h: {} for h in HS}   # 10-28：累計淨損益用（原始報酬累計、進場日數累計）
    tested = 0
    for cb in combos:
        mask = 0
        for a in cb:
            mask |= 1 << a
        sel = [r for r in rows if r[1] & mask == mask]
        key = "|".join(COND[a][0] for a in cb)
        if len(sel) < MIN_TRADES:
            continue
        res = {}
        for hi, h in enumerate(HS):
            recs = [(r[0], r[2 + hi], r[6 + hi]) for r in sel if r[2 + hi] is not None and r[6 + hi] is not None]
            if len(recs) < MIN_TRADES:
                continue
            by_day = defaultdict(list)
            for di, ret, bmv in recs:
                by_day[di].append((ret, bmv))
            dkeys = sorted(by_day)
            if len(dkeys) < MIN_DAYS:
                continue
            hres = {}
            for side, s in (("L", 1), ("S", -1)):
                tested += 1
                dret = [s * r for _, r, _ in recs]
                wins = [x for x in dret if x > 0]
                loss = [x for x in dret if x < 0]
                aw = sum(wins) / len(wins) if wins else None
                al = sum(loss) / len(loss) if loss else None
                daily = [(di, sum(s * (r - bmv) for r, bmv in by_day[di]) / len(by_day[di])) for di in dkeys]
                ex = [v for _, v in daily]
                cum = peak = mdd = 0.0
                for v in ex:
                    cum += v
                    peak = max(peak, cum)
                    mdd = min(mdd, cum - peak)
                pre = [v for di, v in daily if di < split_i]
                post = [v for di, v in daily if di >= split_i]
                years = defaultdict(list)
                for di, v in daily:
                    years[days[di][:4]].append(v)
                hres[side] = dict(
                    n=len(recs), nd=len(dkeys),
                    ex=round(sum(ex) / len(ex), 3), t=_r(nw_t(ex, h)),
                    net=round(sum(dret) / len(dret) - COST, 3),
                    win=round(len(wins) / len(dret) * 100, 1),
                    aw=_r(aw), al=_r(al), pay=_r(aw / -al if (aw is not None and al) else None),
                    mdd=round(mdd, 1),
                    pre=[len(pre), _r(sum(pre) / len(pre) if pre else None), _r(nw_t(pre, h))],
                    post=[len(post), _r(sum(post) / len(post) if post else None), _r(nw_t(post, h))],
                    yr={y: round(sum(v) / len(v), 2) for y, v in sorted(years.items())})
                if h in curves and side == "L":
                    # 週累計（做空的曲線＝做多取負號，網頁自己翻）
                    c, cr, cn, pts, prs, pcs = 0.0, 0.0, 0, [], [], []
                    m = {di: v for di, v in daily}
                    mr = {di: sum(r for r, _ in by_day[di]) / len(by_day[di]) for di in dkeys}
                    for i in range(len(days)):
                        c += m.get(i, 0.0)
                        if i in mr:
                            cr += mr[i]
                            cn += 1
                        if i % 5 == 4 or i == len(days) - 1:
                            pts.append(round(c, 1))
                            prs.append(round(cr, 1))
                            pcs.append(cn)
                    curves[h][key] = pts
                    raws[h][key] = prs
                    cnts[h][key] = pcs
            res[str(h)] = hres
        if res:
            out[key] = res
    lucky = round(tested * 0.0455)
    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, cost=COST,
                adjusted=B.ADJUST, min_trades=MIN_TRADES,
                conditions=[[k, lab] for k, lab, _ in COND],
                n_combos=len(out), tested=tested, lucky=lucky,
                note="探索用：試過 %d 格（組合×持有期×方向），純靠運氣約 %d 格 |t| ≥ 2" % (tested, lucky))
    here = os.path.dirname(os.path.abspath(__file__))
    sfx = "_adj" if B.ADJUST else ""
    with open(os.path.join(here, "data", "bt_explore%s.json" % sfx), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, combos=out), fh, ensure_ascii=False, separators=(",", ":"))
    for h, cv in curves.items():
        with open(os.path.join(here, "data", "bt_explore_curve_%d%s.json" % (h, sfx)), "w", encoding="utf-8") as fh:
            json.dump(dict(start=days[0], end=days[-1], step=5, cost=COST, curves=cv, raw=raws[h], cnt=cnts[h]), fh,
                      separators=(",", ":"))
    print("bt_explore：%d 組有結果、試過 %d 格、運氣預期 %d 格" % (len(out), tested, lucky))
    print("::notice title=回測工具::%d 天，%d 組有結果，試過 %d 格，純運氣預期約 %d 格 |t| ≥ 2" % (len(days), len(out), tested, lucky))


def _r(x, n=3):
    return None if x is None else round(x, n)


if __name__ == "__main__":
    main()
