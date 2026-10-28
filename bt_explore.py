#!/usr/bin/env python3
"""回測工具（2026-10-27；10-28 擴充）：單一條件與兩兩組合，做多、做空都算，給 bt.html 顯示。

跟 backtest.py 用同一套進出場與基準（直接呼叫 backtest.run 收集），不另寫一套：
  訊號日收盤篩 → 隔天開盤進 → 持有 1／5／10／20 天收盤出；超額＝減同日成交金額 ≥ 1 億全市場平均；
  t 用每個進場日的平均（Newey-West）；淨＝照方向原始報酬 − 隔夜成本 0.386%。
母體：當天成交金額 ≥ 1 億（跟網站各情境一樣）。
做空＝同一批股票、報酬取負號（放空賺的是股票跌）。

10-28（使用者：條件要分做多、做空；條件太少，小哥的情境都要能選）：
  條件分組：小哥情境·做多／小哥情境·做空（backtest.py 的情境，定義一模一樣）、偏多、偏空、中性（狀態）。
  「偏多／偏空」只是照講義的直覺分類，方便找，不代表測過。
  新增：均線多空頭排列、站上／跌破年線、漲停／跌停、長上影、創 20 日新高／新低、跳空漲／跌、外資／投信連賣、
        三大法人同買／同賣、券資比高、融資維持率低、營收衰退、自營避險賣超。
  沒有的（沒有 2021 起的歷史）：主力（分點前 15，只有約 35 天且不能公開）、大戶／散戶（集保歷史要付費）、
        當沖率（官方有，還沒抓）。
  計算改用 numpy；結果分持有天數存檔，網頁只讀選到的那一份。

這是「探索工具」：組合很多（約 2,400 組 × 4 持有期 × 2 方向），純靠運氣就會有上千格 |t| ≥ 2。
所以輸出同時給：①期間切兩段（2021-02～2025-08／2025-09 以後）②全部試過幾格、運氣預期幾格。

用法：python bt_explore.py [--adjust] [天數]
輸出：data/bt_explore.json（meta）、data/bt_explore_h{1,5,10,20}.json（統計）、
      data/bt_explore_curve_{1,5,10,20}.json（每 20 個交易日一點的累計曲線：超額、原始、進場日數）
"""
import json
import os
import sys

import numpy as np

import backtest as B

COST = B.COST_OVERNIGHT
HS = B.HORIZONS                      # [1, 5, 10, 20]
SPLIT = "20250901"                   # 之前＝樣本外（多數門檻用近一年挑的）；之後＝近一年
MIN_TRADES, MIN_DAYS = 100, 30
STEP = 20                            # 曲線每幾個交易日一點


def _g(x, k):
    return x.get(k) if x else None


def _n(x):
    return x if x is not None else 0


# (key, 標籤, 分組, 判斷)；b＝價量（含 10-28 補的 hi20/lo20/gapU/gapD）、d＝指標、k＝籌碼（成交金額 ≥ 1 億才有）
# 分組：L＝偏多、S＝偏空、N＝中性／狀態；小哥情境另外從 backtest.PRESETS 加（XL／XS）
COND = [
    ("ma_up", "月線上升", "L", lambda b, d, k: _n(d.get("slope20")) > 0),
    ("above", "站上月線", "L", lambda b, d, k: _n(d.get("bbPos")) > 0),
    ("ma_bull", "均線多頭排列（5＞20＞60）", "L",
     lambda b, d, k: None not in (d.get("ma5"), d.get("ma20"), d.get("ma60")) and d["ma5"] > d["ma20"] > d["ma60"]),
    ("above_y", "站上年線", "L", lambda b, d, k: d.get("biasY") is not None and d["biasY"] > 0),
    ("upband", "貼上軌（位階 ≥ 8）", "L", lambda b, d, k: _n(d.get("bbPos")) >= 8),
    ("up_open", "上軌外開（上通斜率 ≥ 3%）", "L", lambda b, d, k: _n(d.get("slopeUp")) >= 3),
    ("chg_up3", "當天漲 ≥ 3%", "L", lambda b, d, k: _n(b.get("chgPct")) >= 3),
    ("limit_up", "漲 ≥ 9.5%（一價到底的漲停不算）", "L", lambda b, d, k: _n(b.get("chgPct")) >= 9.5),
    ("close_hi", "收在高點（日內位置 ≥ 0.8）", "L", lambda b, d, k: b.get("pos", 0) >= 0.8),
    ("hi20", "收盤創 20 日新高", "L", lambda b, d, k: bool(b.get("hi20"))),
    ("gap_up", "跳空漲（開盤 ＞ 昨天最高）", "L", lambda b, d, k: bool(b.get("gapU"))),
    ("run_up3", "連漲 ≥ 3 天", "L", lambda b, d, k: _n(d.get("runUp")) >= 3),
    ("fgn_buy", "外資買超", "L", lambda b, d, k: _n(_g(k, "fgn")) > 0),
    ("fgn_3", "外資連買 ≥ 3 天", "L", lambda b, d, k: _n(_g(k, "fgnD")) >= 3),
    ("inv_buy", "投信買超", "L", lambda b, d, k: _n(_g(k, "inv")) > 0),
    ("inv_3", "投信連買 ≥ 3 天", "L", lambda b, d, k: _n(_g(k, "invD")) >= 3),
    ("three_buy", "三大法人同買", "L",
     lambda b, d, k: k is not None and _n(k.get("fgn")) > 0 and _n(k.get("inv")) > 0
     and (_n(k.get("dlrS")) + _n(k.get("dlrH"))) > 0),
    ("hedge_buy", "自營避險買超", "L", lambda b, d, k: _n(_g(k, "dlrH")) > 0),
    ("mgn_dn", "融資減少", "L", lambda b, d, k: _n(_g(k, "mgnChg")) < 0),
    ("rev_up", "營收年增 > 0", "L", lambda b, d, k: _n(d.get("_ryoy")) > 0),
    ("rev_new", "營收創 12 個月新高", "L", lambda b, d, k: bool(d.get("_rnew"))),

    ("ma_dn", "月線下彎", "S", lambda b, d, k: _n(d.get("slope20")) < 0),
    ("below", "跌破月線", "S", lambda b, d, k: d.get("bbPos") is not None and d["bbPos"] < 0),
    ("ma_bear", "均線空頭排列（5＜20＜60）", "S",
     lambda b, d, k: None not in (d.get("ma5"), d.get("ma20"), d.get("ma60")) and d["ma5"] < d["ma20"] < d["ma60"]),
    ("below_y", "跌破年線", "S", lambda b, d, k: d.get("biasY") is not None and d["biasY"] < 0),
    ("loband", "貼下軌（位階 ≤ −8）", "S", lambda b, d, k: d.get("bbPos") is not None and d["bbPos"] <= -8),
    ("lo_open", "下軌外開（下通斜率 ≤ −3%）", "S", lambda b, d, k: d.get("slopeLo") is not None and d["slopeLo"] <= -3),
    ("chg_dn3", "當天跌 ≥ 3%", "S", lambda b, d, k: b.get("chgPct") is not None and b["chgPct"] <= -3),
    ("limit_dn", "跌 ≥ 9.5%（一價到底的跌停不算）", "S", lambda b, d, k: b.get("chgPct") is not None and b["chgPct"] <= -9.5),
    ("close_lo", "收在低點（日內位置 ≤ 0.2）", "S", lambda b, d, k: b.get("pos", 1) <= 0.2),
    ("ush_long", "長上影（上影 ≥ 振幅一半）", "S", lambda b, d, k: _n(b.get("ush")) >= 0.5),
    ("lo20", "收盤創 20 日新低", "S", lambda b, d, k: bool(b.get("lo20"))),
    ("gap_dn", "跳空跌（開盤 ＜ 昨天最低）", "S", lambda b, d, k: bool(b.get("gapD"))),
    ("run_dn3", "連跌 ≥ 3 天", "S", lambda b, d, k: _n(d.get("runDn")) >= 3),
    ("fgn_sell", "外資賣超", "S", lambda b, d, k: _n(_g(k, "fgn")) < 0),
    ("fgn_s3", "外資連賣 ≥ 3 天", "S", lambda b, d, k: _n(_g(k, "fgnD")) <= -3),
    ("inv_sell", "投信賣超", "S", lambda b, d, k: _n(_g(k, "inv")) < 0),
    ("inv_s3", "投信連賣 ≥ 3 天", "S", lambda b, d, k: _n(_g(k, "invD")) <= -3),
    ("three_sell", "三大法人同賣", "S",
     lambda b, d, k: k is not None and _n(k.get("fgn")) < 0 and _n(k.get("inv")) < 0
     and (_n(k.get("dlrS")) + _n(k.get("dlrH"))) < 0),
    ("hedge_sell", "自營避險賣超", "S", lambda b, d, k: _n(_g(k, "dlrH")) < 0),
    ("mgn_up", "融資增加", "S", lambda b, d, k: _n(_g(k, "mgnChg")) > 0),
    ("mrate_lo", "融資維持率 < 140%", "S", lambda b, d, k: _g(k, "mgnRate") is not None and k["mgnRate"] < 140),
    ("rev_dn", "營收年增 ≤ 0", "S", lambda b, d, k: d.get("_ryoy") is not None and d["_ryoy"] <= 0),

    ("squeeze", "擠壓（帶寬 < 10%）", "N", lambda b, d, k: d.get("bbW") is not None and d["bbW"] < 10),
    ("bopen", "開布林（帶寬變寬、前 10 日曾 ≤ 10%）", "N",
     lambda b, d, k: _n(d.get("bbWChg")) >= 0.5 and d.get("bbWMin10") is not None and d["bbWMin10"] <= 10),
    ("bias_hi", "乖離年線 > 20%", "N", lambda b, d, k: _n(d.get("biasY")) > 20),
    ("bias_lo", "乖離年線 < −20%", "N", lambda b, d, k: d.get("biasY") is not None and d["biasY"] < -20),
    ("vol15", "帶量（均量比 ≥ 1.5）", "N", lambda b, d, k: _n(d.get("vr20")) >= 1.5),
    ("vol3", "爆量（均量比 ≥ 3）", "N", lambda b, d, k: _n(d.get("vr20")) >= 3),
    ("vol_low", "量縮（均量比 ≤ 0.7）", "N", lambda b, d, k: d.get("vr20") is not None and d["vr20"] <= 0.7),
    ("sr_hi", "券資比 ≥ 20%", "N", lambda b, d, k: _g(k, "sr") is not None and k["sr"] >= 20),
    # 10-28 當沖率＝現股當沖成交張數 ÷ 當日成交張數（data/daytrade，沒抓到的日子不算成立）
    ("dt_lo", "當沖率 ≤ 10%", "N", lambda b, d, k: b.get("dtr") is not None and b["dtr"] <= 10),
    ("dt_30", "當沖率 ≥ 30%", "N", lambda b, d, k: b.get("dtr") is not None and b["dtr"] >= 30),
    ("dt_50", "當沖率 ≥ 50%", "N", lambda b, d, k: b.get("dtr") is not None and b["dtr"] >= 50),
]


def load_daytrade(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daytrade")):
    """{日期: {代號: 當沖張數}}；沒有資料夾就回空 dict。"""
    out = {}
    if not os.path.isdir(path):
        return out
    for f in os.listdir(path):
        if not f.endswith(".txt"):
            continue
        m = {}
        with open(os.path.join(path, f), encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                p = line.split(",")
                if len(p) >= 2:
                    try:
                        m[p[0]] = float(p[1])
                    except ValueError:
                        pass
        out[f[:8]] = m
    return out

# 小哥情境（網站「情境」頁同一份定義）；hot 方向是「—」，放中性
XIAOGE = ["p1", "p2", "p3", "b1", "b4", "c2", "c5", "c7",
          "p4", "p5", "p6", "p7", "b2", "b3", "s1", "c4", "c6", "hot"]


def nw_t(x, h):
    n = len(x)
    if n < 20:
        return None
    m = float(x.mean())
    dev = x - m
    var = float(dev @ dev) / n
    for L in range(1, min(h, n - 1)):
        var += 2 * (1 - L / h) * float(dev[L:] @ dev[:-L]) / n
    return m / (var ** 0.5 / n ** 0.5) if var > 0 else None


def _r(x, n=3):
    return None if x is None else round(float(x), n)


def evaluator():
    """回傳 (conds, ev)：conds＝[(key, 標籤, 分組)]；ev(base, d, k, st, i) → bytearray（每個條件 0／1）。
    bt_explore 與 study_anatomy 共用，定義只寫這一份。要在 B.PRESETS 清空之前呼叫。"""
    presets = dict(B.PRESETS)
    xg = []
    for p in XIAOGE:
        if p in presets:
            lab, side, spec = presets[p]
            grp = "XL" if side == "多" else ("XS" if side == "空" else "N")
            xg.append(("x_" + p, "小哥·" + lab, grp, spec))
    conds = [(k, lab, g) for k, lab, g, _ in COND] + [(k, lab, g) for k, lab, g, _ in xg]
    nC = len(conds)
    off = len(COND)
    DT = load_daytrade()
    print("當沖資料 %d 天" % len(DT))

    def ev(base, d, k, st, i, code=None, day=None):
        c, h, l, o = st["c"], st["h"], st["l"], st["o"]
        b = dict(base)
        if DT and code and day in DT and base.get("lot"):
            v = DT[day].get(code)
            if v is not None:
                b["dtr"] = v / base["lot"] * 100
        if i >= 20 and c[i] is not None:
            prev = [x for x in c[i - 20:i] if x is not None]
            if len(prev) >= 15:
                b["hi20"] = c[i] > max(prev)
                b["lo20"] = c[i] < min(prev)
        if i >= 1 and o[i] is not None and h[i - 1] is not None and l[i - 1] is not None:
            b["gapU"] = o[i] > h[i - 1]
            b["gapD"] = o[i] < l[i - 1]
        bits = bytearray(nC)
        for j, (_, _, _, f) in enumerate(COND):
            try:
                if f(b, d, k):
                    bits[j] = 1
            except (TypeError, KeyError):
                pass
        for j, (_, _, _, spec) in enumerate(xg):
            try:
                if B.passes(spec, base, d, k):
                    bits[off + j] = 1
            except (TypeError, KeyError):
                pass
        return bits

    return conds, ev


def main():
    args = sys.argv[1:]
    if "--adjust" in args:
        B.ADJUST = True
    limit = next((int(a) for a in args if a.isdigit()), None)

    conds, ev = evaluator()
    nC = len(conds)

    # 只要收集，不要原本的情境（省時間）
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    R_day, R_bits, R_ret, R_bm = [], [], [], []
    days = []

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        for code, mkt, base, d, fwd, i, st in rws:
            if base["valE"] is None or base["valE"] < 1:
                continue
            bits = ev(base, d, krows.get(code), st, i, code, day)
            R_day.append(di)
            R_bits.append(bytes(bits))
            R_ret.append(tuple(np.nan if fwd.get(hh) is None else fwd[hh] for hh in HS))
            R_bm.append(tuple(np.nan if bm.get(hh) is None else bm[hh] for hh in HS))

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    nD = len(days)
    DAY = np.array(R_day, dtype=np.int32)
    BITS = np.frombuffer(b"".join(R_bits), dtype=np.uint8).reshape(-1, nC).astype(bool)
    RET = np.array(R_ret, dtype=np.float64)
    BM = np.array(R_bm, dtype=np.float64)
    del R_day, R_bits, R_ret, R_bm
    split_i = next((i for i, d in enumerate(days) if d >= SPLIT), nD)
    years = np.array([int(d[:4]) for d in days])
    yset = sorted(set(years.tolist()))
    pts_idx = [i for i in range(nD) if i % STEP == STEP - 1 or i == nD - 1]

    combos = [(a,) for a in range(nC)] + [(a, b) for a in range(nC) for b in range(a + 1, nC)]
    out = {h: {} for h in HS}
    curves = {h: {"ex": {}, "raw": {}, "cnt": {}} for h in HS}
    tested = 0
    n_combo = 0
    for cb in combos:
        sel = BITS[:, cb[0]].copy()
        for a in cb[1:]:
            sel &= BITS[:, a]
        if sel.sum() < MIN_TRADES:
            continue
        key = "|".join(conds[a][0] for a in cb)
        any_h = False
        for hi, h in enumerate(HS):
            r = RET[sel, hi]
            bmv = BM[sel, hi]
            dd = DAY[sel]
            ok = ~(np.isnan(r) | np.isnan(bmv))
            r, bmv, dd = r[ok], bmv[ok], dd[ok]
            if len(r) < MIN_TRADES:
                continue
            cnt = np.bincount(dd, minlength=nD)
            has = cnt > 0
            if has.sum() < MIN_DAYS:
                continue
            sr = np.bincount(dd, weights=r, minlength=nD)
            se = np.bincount(dd, weights=r - bmv, minlength=nD)
            dmean_ex = np.where(has, se / np.maximum(cnt, 1), 0.0)
            dmean_raw = np.where(has, sr / np.maximum(cnt, 1), 0.0)
            idx = np.nonzero(has)[0]
            hres = {}
            for side, s in (("L", 1), ("S", -1)):
                tested += 1
                dret = s * r
                wins = dret[dret > 0]
                loss = dret[dret < 0]
                aw = wins.mean() if len(wins) else None
                al = loss.mean() if len(loss) else None
                ex = s * dmean_ex[idx]
                cum = np.cumsum(ex)
                mdd = float((cum - np.maximum.accumulate(np.maximum(cum, 0))).min()) if len(cum) else 0.0
                pre = ex[idx < split_i]
                post = ex[idx >= split_i]
                yr = {}
                for y in yset:
                    m = years[idx] == y
                    if m.any():
                        yr[str(y)] = round(float(ex[m].mean()), 2)
                hres[side] = dict(
                    n=int(len(r)), nd=int(len(idx)),
                    ex=_r(ex.mean()), t=_r(nw_t(ex, h)),
                    net=_r(dret.mean() - COST),
                    net_d=_r(s * dmean_raw[idx].mean() - COST),
                    win=round(float(len(wins) / len(dret) * 100), 1),
                    aw=_r(aw), al=_r(al), pay=_r(aw / -al if (aw is not None and al) else None),
                    mdd=round(mdd, 1),
                    pre=[int(len(pre)), _r(pre.mean() if len(pre) else None), _r(nw_t(pre, h))],
                    post=[int(len(post)), _r(post.mean() if len(post) else None), _r(nw_t(post, h))],
                    yr=yr)
            out[h][key] = hres
            any_h = True
            # 曲線（做多方向；做空網頁自己換算）：累計超額、累計原始報酬、累計進場日數
            ce = np.cumsum(dmean_ex)
            cr = np.cumsum(dmean_raw)
            cc = np.cumsum(has.astype(np.int32))
            curves[h]["ex"][key] = [round(float(ce[i]), 1) for i in pts_idx]
            curves[h]["raw"][key] = [round(float(cr[i]), 1) for i in pts_idx]
            curves[h]["cnt"][key] = [int(cc[i]) for i in pts_idx]
        if any_h:
            n_combo += 1

    lucky = round(tested * 0.0455)
    meta = dict(days=nD, start=days[0], end=days[-1], split=SPLIT, cost=COST,
                adjusted=B.ADJUST, min_trades=MIN_TRADES, version=2, horizons=HS, step=STEP,
                conditions=[list(c) for c in conds],
                groups={"XL": "小哥情境·做多", "XS": "小哥情境·做空", "L": "偏多", "S": "偏空", "N": "中性／狀態"},
                n_combos=n_combo, tested=tested, lucky=lucky,
                note="探索用：試過 %d 格（組合×持有期×方向），純靠運氣約 %d 格 |t| ≥ 2" % (tested, lucky))
    here = os.path.dirname(os.path.abspath(__file__))
    sfx = "_adj" if B.ADJUST else ""
    with open(os.path.join(here, "data", "bt_explore%s.json" % sfx), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta), fh, ensure_ascii=False, separators=(",", ":"))
    for h in HS:
        with open(os.path.join(here, "data", "bt_explore_h%d%s.json" % (h, sfx)), "w", encoding="utf-8") as fh:
            json.dump(dict(h=h, combos=out[h]), fh, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(here, "data", "bt_explore_curve_%d%s.json" % (h, sfx)), "w", encoding="utf-8") as fh:
            json.dump(dict(start=days[0], end=days[-1], step=STEP, cost=COST, dates=[days[i] for i in pts_idx],
                           curves=curves[h]["ex"], raw=curves[h]["raw"], cnt=curves[h]["cnt"]), fh,
                      separators=(",", ":"))
    print("bt_explore：%d 個條件、%d 組有結果、試過 %d 格、運氣預期 %d 格" % (nC, n_combo, tested, lucky))
    print("::notice title=回測工具::%d 天，%d 個條件，%d 組有結果，試過 %d 格，純運氣預期約 %d 格 |t| ≥ 2" % (
        nD, nC, n_combo, tested, lucky))


if __name__ == "__main__":
    main()
