#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把每一個篩選情境拿回過去每一天重跑一次，看當天被選出來的股票接下來漲跌如何。

做法
  1. data/stock/<代號>.txt 有每檔逐日 OHLCV（268 個交易日）。
     對第 i 天，把序列切到 closes[:i+1] 丟進 indicators.compute() ——
     跟正式網站用的是同一支函式，不是另外寫一份，才不會測到不一樣的東西。
  2. data/chip/<日期>.txt 有每日三大法人與融資融券，同樣切到當天為止，
     用 chips.py 的 streak() 與 margin_cost() 算連買天數、融資維持率。
  3. 套上 index.html 裡每個情境的條件，拿到當天的入選名單。
  4. 往後看 1/5/10/20 個交易日的報酬，跟「同一天所有通過流動性門檻的股票」
     的平均比較 —— 大盤本來就在漲的時候，選股會跟著漲，不減掉就是自欺欺人。

沒辦法測的
  * 集保大戶／散戶／戶數：data/tdcc 只有一期，沒有歷史 → c1、c3 跳過。
  * 券商分點主力：本來就拿不到。
  * 乖離年線要 MA240，前 239 天算不出來 → p4、p6 可用的天數很少，會標出來。

注意事項（結果要這樣讀）
  * 漲跌幅用收盤價相減算，沒有還原除權息 —— 除息日會被當成下跌。
  * 週轉率用現在的股本回推，中間有增減資的話會偏掉。
  * 樣本期間只有 2025-08 ~ 2026-09，一年多、一種盤而已。
    這是「這套條件在這段期間長什麼樣」，不是「這套條件會賺錢」。
"""

import os
import sys
import json
import random
from collections import defaultdict

import twse as T
import indicators as I
import chips as C

HORIZONS = [1, 5, 10, 20]
# 「隔天跳空」當成一個特殊的持有期：訊號日收盤 → 隔天開盤。
# 盤後選股吃不到這一段，但要知道它有多大 —— 有些條件的價值其實整個在跳空上。
GAP = "g"
KEYS = [GAP] + HORIZONS


def nw_lag(k):
    """Newey-West 要修正幾期重疊。跳空不重疊，所以是 1。"""
    return 1 if k == GAP else k


WARMUP = 30          # 算得出 bbWMin10 的最低天數
MARGIN_MIN_DAYS = 120  # 融資維持率至少要這麼多天的籌碼史才夠可信


# ---------------------------------------------------------------- 讀資料

def load_stocks():
    """code -> dict(dates, o, h, l, c, lot, wan)，皆由舊到新。"""
    out = {}
    for fn in sorted(os.listdir(T.STOCK_DIR)):
        if not fn.endswith(".txt"):
            continue
        code = fn[:-4]
        dates, o, h, l, c, lot, wan = [], [], [], [], [], [], []
        for d, line in sorted(T.read_stock(code).items()):
            p = line.split(",")
            if len(p) < 7:
                continue
            try:
                cc = float(p[4])
            except ValueError:
                continue
            if cc <= 0:
                continue
            dates.append(d)
            o.append(T.pn(p[1]))
            h.append(T.pn(p[2]))
            l.append(T.pn(p[3]))
            c.append(cc)
            lot.append(T.pn(p[5]) or 0.0)
            wan.append(T.pn(p[6]))
        if len(c) >= WARMUP + max(HORIZONS):
            out[code] = dict(dates=dates, o=o, h=h, l=l, c=c, lot=lot, wan=wan)
    return out


def load_market_meta():
    """code -> (mkt, name, kshares)。股本只有現在這一版，沒有歷史。"""
    info = {}
    path = os.path.join(T.DATA, "market.txt")
    with open(path, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            p = line.rstrip("\n").split(",")
            if len(p) < 10:
                continue
            info[p[0]] = (p[2], p[1], T.pn(p[9]))
    return info


def load_chip_days():
    days = C.chip_days()
    return days, {d: C.read_day(d) for d in days}


# ---------------------------------------------------------------- 籌碼衍生

def chip_row(code, di, days, cache, price_hist, mkt, cost_cache):
    """把某一天的籌碼算成跟 chips.txt 同樣的欄位。di 是 days 裡的索引。"""
    cur = cache[days[di]].get(code)
    if cur is None:
        return None
    recent = [cache[days[j]].get(code, {}).get("inv")
              for j in range(di, max(-1, di - C.LOOKBACK), -1)]
    recent_f = [cache[days[j]].get(code, {}).get("fgn")
                for j in range(di, max(-1, di - C.LOOKBACK), -1)]
    mgn = cur.get("mgn")
    mgn_prev = cur.get("mgnPrev")
    shrt = cur.get("shrt")
    row = {
        "inv": cur.get("inv"),
        "invD": C.streak(recent),
        "fgn": cur.get("fgn"),
        "fgnD": C.streak(recent_f),
        "mgn": mgn,
        "mgnChg": (mgn - mgn_prev) if (mgn is not None and mgn_prev is not None) else None,
        "sr": (shrt / mgn * 100) if (shrt is not None and mgn and mgn > 0) else None,
        "mgnRate": None,
    }
    # 融資維持率：要往回看一段才估得出平均成本，歷史不夠長就不給
    if mgn and mgn > 0 and di + 1 >= MARGIN_MIN_DAYS:
        lo = max(0, di + 1 - C.MARGIN_LOOKBACK)
        window = days[lo:di + 1]
        mcost, _conf = margin_cost_hist(code, window, cache, price_hist)
        px = price_hist.get(code, {}).get(days[di])
        if mcost and mcost > 0 and px:
            r = C.MARGIN_RATE.get(mkt, C.MARGIN_DEFAULT)
            row["mgnRate"] = px / (mcost * r) * 100
    return row


def margin_cost_hist(code, window, cache, price_hist):
    """chips.py 的 margin_cost，但價格從記憶體裡的歷史拿，不重讀檔案。"""
    px = price_hist.get(code)
    if not px:
        return None, None
    cost, bal, added = None, 0.0, 0.0
    for d in window:
        r = cache.get(d, {}).get(code)
        if r is None:
            continue
        m = r.get("mgn")
        p = px.get(d)
        if m is None or m < 0 or p is None:
            continue
        if bal <= 0:
            if m > 0:
                cost, added = p, m
            bal = m
            continue
        delta = m - bal
        if delta > 0:
            cost = (cost * bal + p * delta) / m
            added += delta
        bal = m
    if not cost or bal <= 0:
        return None, None
    return cost, min(1.0, added / bal)


# ---------------------------------------------------------------- 情境

def nn(x):
    return x is not None


# 每個情境的條件，欄位名稱跟 index.html 的 PRESETS 一模一樣。
# val=成交金額億、lot=張、turn=週轉率%、amp=振幅%、chg1/2=漲跌幅、
# pos1/2=收盤位置、slope1/2=月線斜率、bpos1/2=位階、bw1/2=帶寬、
# bias1/2=乖離年線、vr=均量比、runup/rundn=連漲連跌、
# supt=上通斜率上限、supt2=上通斜率下限、slot=下通斜率上限、
# wchg=帶寬變化下限、wmin=前10日最小帶寬上限、
# invd=投信連買、inv=投信買超、invs=投信賣超(填正數)、fgn/fgnd=外資、
# sr=券資比、mgnc1/2=融資增減、mr1/2=融資維持率
PRESETS = {
    "p1": ("月線上升強勢股", "多", dict(val=1, lot=1000, slope1=1, bpos1=0, vr=1)),
    "p2": ("回檔量縮待買", "多", dict(val=1, slope1=0, chg2=0, bpos1=-6, bpos2=4)),
    "p3": ("連跌反彈候選", "多", dict(val=1, rundn=3, vr=1.5)),
    "p4": ("高檔出貨紅K", "空", dict(val=1, lot=1000, bias1=40, chg1=3, slope2=0, supt=3)),
    "p5": ("月線下彎量增跌", "空", dict(val=1, slope2=-1, bpos2=0, chg2=0, supt=3)),
    "p6": ("價背離空（半套）", "空", dict(lot=300, chg1=3, slope2=0, bias1=20, supt=3)),
    "p7": ("隔日沖出貨警示", "空", dict(val=1, vr=3, chg1=5)),
    "b1": ("往上帶量開布林", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10)),
    "b2": ("往下帶量開布林", "空", dict(val=1, wchg=0.5, slot=-1, bpos2=0, chg2=-1, vr=1.5, wmin=10, supt=3)),
    "b3": ("反彈放空點", "空", dict(val=1, slope2=0, bpos1=0, supt=3)),
    "c2": ("投信認養股", "多", dict(val=1, invd=3, slope1=0, bpos1=0)),
    "c4": ("投信倒貨融資接", "空", dict(val=1, invs=100, mgnc1=0, slope2=0.5)),
    "c5": ("軋空候選", "多", dict(val=1, sr=20, slope1=0, chg1=1, vr=1.5)),
    "c6": ("融資斷頭壓力", "空", dict(val=1, mr2=130, slope2=0)),
    "c7": ("下跌但融資大減", "多", dict(val=1, mr1=160, chg2=0, mgnc2=-100)),
    "hot": ("高週轉高振幅", "—", dict(val=1, turn=10, amp=5)),
    # 對照組：同樣的流動性門檻，但隨機挑 30 檔。
    # 這一列的超額報酬必須接近 0、t 值必須接近 0，否則就是量測方法本身有偏差，
    # 上面每一列都不能信。這是整份回測的體溫計。
    "rnd10": ("隨機對照 10 檔", "多", dict(val=1, _random=10, _seed=1)),
    "rnd30": ("隨機對照 30 檔", "多", dict(val=1, _random=30, _seed=2)),
    "rnd60": ("隨機對照 60 檔", "多", dict(val=1, _random=60, _seed=3)),
}
CONTROL = {"rnd10", "rnd30", "rnd60"}

NEED_CHIP = {"c2", "c4", "c5", "c6", "c7"}
NEED_MA240 = {"p4", "p6"}

# 拆解測試：不是測「情境」，是把講義裡單獨一條規則拉出來，跟它的相反面比。
# 情境是好幾條疊在一起，測出來好或不好都不知道是哪一條的功勞；
# 這裡一次只動一個條件，其他都一樣，差異才歸得出去。
# (組名, 這組在驗哪一條, [(標籤, 方向, 條件), ...])
RULES = [
    ("做空找位階高", "講義：「做多找翹上軌；做空找位階高」——"
                  "空單要等月線下彎的股票反彈到中軌之上才進，不是追殺已經在低點的。", [
        ("月線下彎＋位階 > 0（反彈才空）", "空", dict(val=1, slope2=0, bpos1=0)),
        ("月線下彎＋位階 < 0（追殺低點）", "空", dict(val=1, slope2=0, bpos2=0)),
        ("月線下彎，不管位階", "空", dict(val=1, slope2=0)),
    ]),
    ("做空避開上軌翹的", "講義做空實務：「避開上通斜率超過 3%」、"
                    "做空結論：「避開強勢股，強勢股嘎空太恐怖」。", [
        ("上軌斜率 > 3% 拿來空", "空", dict(val=1, supt2=3)),
        ("上軌斜率 0～3% 拿來空", "空", dict(val=1, supt2=0, supt=3)),
        ("上軌斜率 < 0 拿來空", "空", dict(val=1, supt=0)),
    ]),
    ("下軌斜率的做空綠燈", "參數字典：下通斜率 < −3% 是綠燈。這一組是在檢查這個門檻。", [
        ("下軌斜率 < −3% 拿來空", "空", dict(val=1, slot=-3)),
        ("下軌斜率 −3～0% 拿來空", "空", dict(val=1, slot=0)),
    ]),
    ("月線方向", "講義：月線斜率 > 1 超級強勢、< −1 超級弱勢；"
              "做多結論看月線升、做空結論看月線下彎。", [
        ("月線斜率 > 1% 做多", "多", dict(val=1, slope1=1)),
        ("月線斜率 0～1% 做多", "多", dict(val=1, slope1=0, slope2=1)),
        ("月線下彎還去做多", "多", dict(val=1, slope2=0)),
    ]),
    ("上軌翹做多", "講義多方四大條件之一：「上軌翹」。同樣是月線升、位階翻正，"
                "差別只在上軌有沒有翹。", [
        ("月線升＋位階 > 0＋上軌翹", "多", dict(val=1, slope1=0, bpos1=0, supt2=0)),
        ("月線升＋位階 > 0＋上軌沒翹", "多", dict(val=1, slope1=0, bpos1=0, supt=0)),
    ]),
]


def passes(s, base, d, k):
    """base = 當天的價量衍生；d = 指標；k = 籌碼。回 True 表示入選。"""
    g = s.get   # 底線開頭的 key（例如 _random）不是篩選條件，下面都不會去讀
    if g("val") is not None and (base["valE"] is None or base["valE"] < g("val")):
        return False
    if g("lot") is not None and base["lot"] < g("lot"):
        return False
    if g("turn") is not None and (base["turn"] is None or base["turn"] < g("turn")):
        return False
    if g("amp") is not None and base["ampPct"] < g("amp"):
        return False
    if g("chg1") is not None and (base["chgPct"] is None or base["chgPct"] < g("chg1")):
        return False
    if g("chg2") is not None and (base["chgPct"] is None or base["chgPct"] > g("chg2")):
        return False
    if g("pos1") is not None and base["pos"] < g("pos1"):
        return False
    if g("pos2") is not None and base["pos"] > g("pos2"):
        return False

    pairs = [("slope1", "slope20", "min"), ("slope2", "slope20", "max"),
             ("bpos1", "bbPos", "min"), ("bpos2", "bbPos", "max"),
             ("bw1", "bbW", "min"), ("bw2", "bbW", "max"),
             ("bias1", "biasY", "min"), ("bias2", "biasY", "max"),
             ("vr", "vr20", "min"),
             ("supt", "slopeUp", "max"), ("supt2", "slopeUp", "min"),
             ("slot", "slopeLo", "max"),
             ("wchg", "bbWChg", "min"), ("wmin", "bbWMin10", "max")]
    for key, field, how in pairs:
        v = g(key)
        if v is None:
            continue
        x = d.get(field)
        if x is None:
            return False
        if how == "min" and x < v:
            return False
        if how == "max" and x > v:
            return False
    if g("runup") is not None and d["runUp"] < g("runup"):
        return False
    if g("rundn") is not None and d["runDn"] < g("rundn"):
        return False

    if any(g(x) is not None for x in ("invd", "inv", "invs", "fgn", "fgnd",
                                      "sr", "mgnc1", "mgnc2", "mr1", "mr2")):
        if k is None:
            return False
        if g("invd") is not None and k["invD"] < g("invd"):
            return False
        if g("inv") is not None and (k["inv"] is None or k["inv"] < g("inv")):
            return False
        if g("invs") is not None and (k["inv"] is None or k["inv"] > -g("invs")):
            return False
        if g("fgn") is not None and (k["fgn"] is None or k["fgn"] < g("fgn")):
            return False
        if g("fgnd") is not None and k["fgnD"] < g("fgnd"):
            return False
        if g("sr") is not None and (k["sr"] is None or k["sr"] < g("sr")):
            return False
        if g("mgnc1") is not None and (k["mgnChg"] is None or k["mgnChg"] < g("mgnc1")):
            return False
        if g("mgnc2") is not None and (k["mgnChg"] is None or k["mgnChg"] > g("mgnc2")):
            return False
        if g("mr1") is not None and (k["mgnRate"] is None or k["mgnRate"] < g("mr1")):
            return False
        if g("mr2") is not None and (k["mgnRate"] is None or k["mgnRate"] > g("mr2")):
            return False
    return True


# ---------------------------------------------------------------- 主流程

def base_metrics(st, i, kshares):
    h, l, c = st["h"][i], st["l"][i], st["c"][i]
    if h is None or l is None:
        return None
    a = h - l
    if a <= 0:            # 跟網站一樣：沒有振幅就不算（漲跌停鎖死）
        return None
    prev = st["c"][i - 1] if i >= 1 else None
    lot = st["lot"][i] or 0.0
    wan = st["wan"][i]
    return {
        "ampPct": a / c * 100 if c > 0 else 0,
        "pos": (c - l) / a,
        "chgPct": ((c / prev - 1) * 100) if (prev and prev > 0) else None,
        "lot": lot,
        "valE": (wan / 10000) if wan is not None else None,
        "turn": (lot / kshares * 100) if (kshares and kshares > 0) else None,
    }


def run(limit_days=None, verbose=True):
    stocks = load_stocks()
    info = load_market_meta()
    chip_ds, chip_cache = load_chip_days()
    chip_idx = {d: i for i, d in enumerate(chip_ds)}

    # 給 margin_cost_hist 用的價格表
    price_hist = {code: dict(zip(st["dates"], st["c"])) for code, st in stocks.items()}

    # 全市場的交易日（用最常見的那條序列）
    counter = defaultdict(int)
    for st in stocks.values():
        for d in st["dates"]:
            counter[d] += 1
    all_days = sorted(d for d, n in counter.items() if n > len(stocks) * 0.5)
    if verbose:
        print("股票 %d 檔、交易日 %d 天（%s ~ %s）、籌碼日 %d 天"
              % (len(stocks), len(all_days), all_days[0], all_days[-1], len(chip_ds)))

    max_h = max(HORIZONS)
    eval_days = all_days[WARMUP:len(all_days) - max_h]
    if limit_days:
        eval_days = eval_days[-limit_days:]

    # 收集：picks[preset][horizon] = [報酬...]，base[horizon] = [報酬...]
    picks = {p: {h: [] for h in KEYS} for p in PRESETS}
    counts = {p: [] for p in PRESETS}
    usable = {p: 0 for p in PRESETS}
    bench = {h: [] for h in KEYS}
    bench_days = {h: [] for h in KEYS}   # 每日平均，用來配對比較
    day_bench = {}

    pos_in = {code: {d: j for j, d in enumerate(st["dates"])}
              for code, st in stocks.items()}

    for dn_i, day in enumerate(eval_days):
        # 這一天所有股票的指標
        rows = []
        for code, st in stocks.items():
            i = pos_in[code].get(day)
            if i is None or i < WARMUP:
                continue
            if i + max_h >= len(st["c"]):
                continue
            mkt, name, ksh = info.get(code, ("1", code, None))
            base = base_metrics(st, i, ksh)
            if base is None:
                continue
            d = I.compute(st["c"][:i + 1], st["lot"][:i + 1])
            # 進場價用「隔天開盤」，不是訊號當天的收盤 ——
            # 這是盤後選股，名單出來的時候今天已經收了，最快也只能明天開盤才買得到。
            # 用當天收盤當進場價會憑空多賺一段開盤跳空，那是騙自己。
            entry = st["o"][i + 1] if (i + 1) < len(st["o"]) else None
            fwd = {}
            for h in HORIZONS:
                c1 = st["c"][i + h]
                fwd[h] = (c1 / entry - 1) * 100 if (entry and entry > 0) else None
            # 跳空：訊號日收盤 → 隔天開盤。這一段吃不到，但要看得到。
            c0 = st["c"][i]
            fwd[GAP] = (entry / c0 - 1) * 100 if (entry and c0 and c0 > 0) else None
            rows.append((code, mkt, base, d, fwd, i))

        # 基準：通過流動性門檻（成交金額 ≥ 1 億）的全市場平均
        liq = [r for r in rows if r[2]["valE"] is not None and r[2]["valE"] >= 1]
        bm, bmed = {}, {}
        for h in KEYS:
            vals = sorted(r[4][h] for r in liq if r[4][h] is not None)
            if vals:
                bm[h] = sum(vals) / len(vals)
                m = len(vals)
                bmed[h] = vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2
                bench[h].append(bm[h])
            else:
                bm[h] = bmed[h] = None
        day_bench[day] = bm

        # 籌碼（只在需要時算，很貴）
        di = chip_idx.get(day)
        krows = {}
        if di is not None:
            for code, mkt, base, d, fwd, i in rows:
                if base["valE"] is None or base["valE"] < 1:
                    continue
                k = chip_row(code, di, chip_ds, chip_cache, price_hist, mkt, None)
                if k:
                    krows[code] = k

        has240 = any(r[3].get("biasY") is not None for r in rows)
        for pname, (label, side, spec) in PRESETS.items():
            if pname in NEED_CHIP and di is None:
                continue
            if pname in NEED_MA240 and not has240:
                continue           # 這天全市場都還算不出乖離年線，不能算「那天沒選到」
            sel = [r for r in rows
                   if not (pname in NEED_MA240 and r[3].get("biasY") is None)
                   and passes(spec, r[2], r[3], krows.get(r[0]))]
            if spec.get("_random"):
                rng = random.Random("%s|%d" % (day, spec.get("_seed", 0)))
                sel = rng.sample(sel, min(spec["_random"], len(sel)))
            n = len(sel)
            for code, mkt, base, d, fwd, i in sel:
                for h in KEYS:
                    if fwd[h] is not None:
                        picks[pname][h].append((fwd[h], bm[h], bmed[h], day))
            counts[pname].append(n)
            usable[pname] += 1

        if verbose and (dn_i + 1) % 25 == 0:
            print("  %d/%d 天 …" % (dn_i + 1, len(eval_days)))

    return dict(picks=picks, counts=counts, usable=usable, bench=bench,
                eval_days=eval_days, n_stocks=len(stocks))


def summarize(res):
    out = []
    for pname, (label, side, spec) in PRESETS.items():
        cnt = res["counts"][pname]
        row = {"key": pname, "name": label, "side": side,
               "days": res["usable"][pname],
               "avg_picks": (sum(cnt) / len(cnt)) if cnt else 0,
               "total_picks": sum(cnt), "h": {}}
        for h in KEYS:
            recs = res["picks"][pname][h]
            if not recs:
                row["h"][h] = None
                continue
            rets = [r for r, b, bmd, d in recs]
            exc = [r - b for r, b, bmd, d in recs if b is not None]
            # 贏過大盤的比率要跟「中位數」比，不是跟平均比 ——
            # 平均被少數飆股拉高，隨便挑一檔本來就有一半機率輸給平均，
            # 跟中位數比才是「隨機挑的基準線 = 50%」。
            beatm = [1 if r > bmd else 0 for r, b, bmd, d in recs if bmd is not None]
            n = len(rets)
            mean_r = sum(rets) / n
            mean_e = (sum(exc) / len(exc)) if exc else None
            win = sum(1 for r in rets if r > 0) / n * 100
            beat = (sum(beatm) / len(beatm) * 100) if beatm else None
            srt = sorted(rets)
            med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2

            # 同一天選出來的股票會一起漲一起跌，直接把每一筆當獨立樣本會高估顯著性。
            # 所以先把每天的超額報酬平均成一個數字，再看這些「每日平均」穩不穩。
            by_day = defaultdict(list)
            for r, b, bmd, dd in recs:
                if b is not None:
                    by_day[dd].append(r - b)
            daily = [sum(v) / len(v) for _, v in sorted(by_day.items())]
            t, sd = None, None
            if len(daily) >= 20:      # 樣本天數太少的話 t 值只是巧合，不給
                m = sum(daily) / len(daily)
                nd = len(daily)
                dev = [x - m for x in daily]
                # Newey-West：持有 h 天的報酬會重疊 h−1 天，相鄰樣本自己就相關，
                # 不修正的話 t 值會被灌水好幾倍。
                hl = nw_lag(h)
                g0 = sum(e * e for e in dev) / nd
                var = g0
                for lag in range(1, min(hl, nd - 1)):
                    g = sum(dev[j] * dev[j - lag] for j in range(lag, nd)) / nd
                    var += 2 * (1 - lag / hl) * g
                if var > 0:
                    sd = var ** 0.5
                    t = m / (sd / nd ** 0.5)
            pos_days = (sum(1 for x in daily if x > 0) / len(daily) * 100) if daily else None
            # 逐月平均，用來看它是一路都這樣，還是靠某一兩個月撐起來的
            by_month = defaultdict(list)
            for r, b, bmd, dd in recs:
                if b is not None:
                    by_month[dd[:6]].append(r - b)
            monthly = {m: sum(v) / len(v) for m, v in sorted(by_month.items())}

            row["h"][h] = dict(n=n, mean=mean_r, median=med, win=win,
                               excess=mean_e, beat=beat,
                               t=t, sd_daily=sd, n_days=len(daily),
                               pos_days=pos_days, monthly=monthly)
        out.append(row)
    return out


def fmt_table(rows, res):
    lines = []
    bench_avg = {h: (sum(res["bench"][h]) / len(res["bench"][h]) if res["bench"][h] else None)
                 for h in HORIZONS}
    lines.append("基準（每天成交金額 ≥ 1 億的全市場平均報酬）：" +
                 "　".join("%d 天 %+.2f%%" % (h, bench_avg[h]) for h in HORIZONS
                           if bench_avg[h] is not None))
    lines.append("")
    lines.append("進場價＝訊號隔天的開盤（盤後選股最快只能這樣買），出場＝第 N 天收盤。")
    lines.append("每格＝超額報酬／贏過當天中位數的比率（隨機挑是 50%）／t 值（已修正重疊）。")
    lines.append("t 值絕對值 < 2 就跟雜訊分不出來，別當真。空方的數字都是「做空」的角度。")
    lines.append("")
    head = "%-18s %-3s %5s %7s" % ("情境", "方向", "天數", "平均檔數")
    for h in HORIZONS:
        head += " │ %-24s" % ("%d 天" % h)
    lines.append(head)
    lines.append("─" * len(head))
    for r in rows:
        line = "%-18s %-3s %5d %8.1f" % (r["name"], r["side"], r["days"], r["avg_picks"])
        for h in HORIZONS:
            x = r["h"][h]
            if not x:
                line += " │ %-24s" % "—"
            else:
                sign = -1 if r["side"] == "空" else 1
                tv = x["t"]
                line += " │ %+6.2f%% %5.1f%% t%s" % (
                    sign * x["excess"],
                    x["beat"] if sign > 0 else 100 - x["beat"],
                    ("%+5.1f" % (sign * tv)) if tv is not None else "  —  ")
        lines.append(line)
    return "\n".join(lines)


def run_rules():
    """跑拆解測試。把 RULES 攤平成一組 PRESETS 重用同一套流程。"""
    global PRESETS, NEED_CHIP, NEED_MA240
    saved = (PRESETS, NEED_CHIP, NEED_MA240)
    flat, groups = {}, []
    for gi, (gname, why, items) in enumerate(RULES):
        keys = []
        for ii, (label, side, spec) in enumerate(items):
            key = "g%d_%d" % (gi, ii)
            flat[key] = (label, side, spec)
            keys.append(key)
        groups.append({"name": gname, "why": why, "keys": keys})
    flat["rndR"] = ("隨機對照 30 檔", "多", dict(val=1, _random=30, _seed=99))
    groups.append({"name": "對照組", "why": "隨機挑 30 檔。這一列越接近 0，"
                                        "上面各組的差異才越可信。", "keys": ["rndR"]})
    PRESETS, NEED_CHIP, NEED_MA240 = flat, set(), set()
    try:
        res = run(verbose=True)
        rows = summarize(res)
    finally:
        PRESETS, NEED_CHIP, NEED_MA240 = saved
    return rows, groups, res


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    limit = None
    for a in args:
        if a.isdigit():
            limit = int(a)
    here = os.path.dirname(os.path.abspath(__file__))

    res = run(limit_days=limit)
    rows = summarize(res)
    print()
    print(fmt_table(rows, res))
    out = {"rows": rows,
           "bench": {str(h): res["bench"][h] for h in KEYS},
           "days": len(res["eval_days"]),
           "from": res["eval_days"][0], "to": res["eval_days"][-1]}

    if "--rules" in args:
        print("\n\n=== 拆解測試：一次只動一個條件 ===")
        rrows, groups, rres = run_rules()
        print()
        print(fmt_table(rrows, rres))
        out["rule_rows"] = rrows
        out["rule_groups"] = groups

    with open(os.path.join(here, "backtest.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
