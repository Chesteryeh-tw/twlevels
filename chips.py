#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
籌碼面資料：三大法人買賣超、融資融券。上市上櫃都抓，全部是免費公開資料。

產出
  data/chip/<YYYYMMDD>.txt   每個交易日一個檔，一檔股票一行（原始數字）
  data/chips.txt             最新一日的衍生指標（前端讀這個）

用法
  python chips.py              抓最新一天（配合每日更新）
  python chips.py 400 0        回補：400 天前 ~ 今天
  python chips.py --derive     只重算 chips.txt，不連網

資料來源
  上市三大法人  https://www.twse.com.tw/fund/T86
  上市融資融券  https://www.twse.com.tw/exchangeReport/MI_MARGN
  上櫃三大法人  https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade
  上櫃融資融券  https://www.tpex.org.tw/www/zh-tw/margin/balance
  集保大戶散戶  tdcc.py 另外處理（每週一次）

單位一律換算成「張」（1 張 = 1000 股），跟畫面上其他欄位一致。
"""

import os
import sys
import time
from datetime import datetime, timedelta

import twse as T

CHIP_DIR = os.path.join(T.DATA, "chip")
OUT = os.path.join(T.DATA, "chips.txt")
TDCC_DIR = os.path.join(T.DATA, "tdcc")

# 每日檔的欄位（原始值，單位：張）
RAW_COLS = "code,fgn,inv,dlrSelf,dlrHedge,mgn,mgnPrev,shrt,shrtPrev"

# chips.txt 的欄位（衍生值）
COLS = ("code,fgn,fgnD,inv,invD,dlrH,mgn,mgnChg,shrt,sr,"
        "big,bigChg,small,smallChg,holders,holdersChg,mgnRate,mgnCost,mgnConf")

# 融資成數：上市自備款 4 成（借 6 成）、上櫃自備款 5 成（借 5 成）。
# 講義只寫「一般情況融資成數 60%、自備款 40%」，處置股會被調降，
# 這裡用一般值估算，處置期間會失真。
MARGIN_RATE = {"1": 0.6, "2": 0.5}
MARGIN_DEFAULT = 0.6

LOOKBACK = 40         # 算連買連賣要回看幾個交易日
MARGIN_LOOKBACK = 300 # 估融資平均成本要回看幾個交易日（越長越準）
PAUSE = 1.2


# ---------------------------------------------------------------- 讀寫

def day_path(dstr):
    return os.path.join(CHIP_DIR, dstr + ".txt")


# 一個正常交易日，全市場大約有 1900~2000 檔有融資餘額。
# 低於這個數就代表證交所那份還沒出，寫出來的是半套檔。
MIN_MARGIN_ROWS = 500


def have_day(dstr, min_ratio=0.9):
    """這天的籌碼抓齊了沒。

    不能只看檔案在不在。櫃買維護那天會寫出一個「只有上市」的半套檔，
    下次再跑時若只看檔名存在就跳過，上櫃那半永遠補不回來 ——
    這正是 2026-09-18 發生的事。所以這裡比對 market.txt 的股票清單，
    涵蓋率不夠就當作沒抓過，重抓一次覆蓋掉。
    """
    rows = read_day(dstr)
    if not rows:
        return False
    keep = market_codes()
    if not keep:
        return True                 # 沒有 market.txt 可比對，就不多管
    hit = sum(1 for c in rows if c in keep)
    if hit < len(keep) * min_ratio:
        return False
    # 光看「每檔都有一列」不夠。三大法人 16:00 就出了，融資融券要更晚；
    # 太早跑的話會寫出一個「有法人、沒融資」的半套檔，而舊版只看列數就跳過，
    # 那天的融資資料永遠補不回來 —— 2026-09-21 就是這樣掛的。
    # 所以這裡再檢查融資欄位實際有值的檔數。
    with_mgn = sum(1 for r in rows.values() if r.get("mgn") is not None)
    return with_mgn >= MIN_MARGIN_ROWS


def write_day(dstr, rows):
    """rows: {code: dict}"""
    os.makedirs(CHIP_DIR, exist_ok=True)
    keys = RAW_COLS.split(",")[1:]
    lines = [RAW_COLS]
    for code in sorted(rows):
        r = rows[code]
        lines.append(code + "," + ",".join(T.fmt(r.get(k)) for k in keys))
    with open(day_path(dstr), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def read_day(dstr):
    p = day_path(dstr)
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
        keys = head[1:]
        for line in fh:
            p2 = line.rstrip("\n").split(",")
            if len(p2) != len(head) or not p2[0]:
                continue
            d = {}
            for k, v in zip(keys, p2[1:]):
                d[k] = T.pn(v)
            out[p2[0]] = d
    return out


def chip_days():
    """已經抓好的交易日，由舊到新。"""
    if not os.path.isdir(CHIP_DIR):
        return []
    return sorted(fn[:-4] for fn in os.listdir(CHIP_DIR) if fn.endswith(".txt"))


# ---------------------------------------------------------------- 上市

def fetch_twse_insti(day):
    """T86 三大法人。回傳 {code: {fgn, inv, dlrSelf, dlrHedge}}，單位張。"""
    url = ("https://www.twse.com.tw/fund/T86?response=json&date=%s&selectType=ALL"
           % day.strftime("%Y%m%d"))
    j = T.get_json(url)
    if not j or j.get("stat") != "OK":
        return {}
    fields = j.get("fields") or []
    data = j.get("data") or []
    if not data:
        return {}

    i_fgn = T.idx(fields, "外陸資買賣超股數(不含外資自營商)", "外陸資買賣超股數")
    i_inv = T.idx(fields, "投信買賣超股數")
    i_self = T.idx(fields, "自營商買賣超股數(自行買賣)")
    i_hedge = T.idx(fields, "自營商買賣超股數(避險)")
    if min(i_fgn, i_inv, i_self, i_hedge) < 0:
        print("      T86 欄位對不上，跳過：%s" % fields[:4])
        return {}

    out = {}
    for row in data:
        if len(row) <= max(i_fgn, i_inv, i_self, i_hedge):
            continue
        code = T.clean(row[0])
        if not code:
            continue
        out[code] = {
            "fgn": lot(T.pn(row[i_fgn])),
            "inv": lot(T.pn(row[i_inv])),
            "dlrSelf": lot(T.pn(row[i_self])),
            "dlrHedge": lot(T.pn(row[i_hedge])),
        }
    return out


def fetch_twse_margin(day):
    """MI_MARGN 融資融券（股票表，單位本來就是張）。"""
    url = ("https://www.twse.com.tw/exchangeReport/MI_MARGN"
           "?response=json&date=%s&selectType=STOCK" % day.strftime("%Y%m%d"))
    j = T.get_json(url)
    if not j or j.get("stat") != "OK":
        return {}

    # 新版把表放在 tables 裡，舊版直接給 data，兩種都接
    tables = j.get("tables")
    if tables:
        tb = None
        for t in tables:
            if t.get("data") and t.get("fields") and len(t["fields"]) >= 14:
                tb = t
                break
        if not tb:
            return {}
        fields, data = tb["fields"], tb["data"]
    else:
        fields, data = j.get("fields") or [], j.get("data") or []
    if not data:
        return {}

    # 融資、融券各有一組「買進/賣出/償還/前日餘額/今日餘額/限額」，欄名會重複，
    # 所以用位置：代號 名稱 | 資×6 | 券×6 | 資券互抵 註記
    if len(fields) < 16:
        print("      MI_MARGN 欄位數不對（%d），跳過" % len(fields))
        return {}
    i_mgn_prev, i_mgn, i_shrt_prev, i_shrt = 5, 6, 11, 12

    out = {}
    for row in data:
        if len(row) <= i_shrt:
            continue
        code = T.clean(row[0])
        if not code:
            continue
        out[code] = {
            "mgn": T.pn(row[i_mgn]),
            "mgnPrev": T.pn(row[i_mgn_prev]),
            "shrt": T.pn(row[i_shrt]),
            "shrtPrev": T.pn(row[i_shrt_prev]),
        }
    return out


# ---------------------------------------------------------------- 上櫃

def _tpex_rows(j, min_cols):
    """櫃買的 json 會把資料包在 tables 裡，挑出欄位數對得上的那張表。"""
    if not j:
        return None, None
    for t in (j.get("tables") or []):
        data = t.get("data") or []
        if data and len(data[0]) >= min_cols:
            return t.get("fields") or [], data
    return None, None


def fetch_tpex_insti(day):
    """上櫃三大法人買賣明細。欄名重複，靠位置＋加總驗算確認。"""
    url = ("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
           "?type=Daily&sect=EW&date=%s&id=&response=json"
           % day.strftime("%Y/%m/%d"))
    fields, data = _tpex_rows(T.get_json(url), 24)
    if not data:
        return {}

    # 代號 名稱 |外資不含自營 3| 外資自營 3| 外資合計 3| 投信 3|
    #           自營自行 3| 自營避險 3| 自營合計 3| 三大法人合計
    I_FGN, I_INV, I_SELF, I_HEDGE, I_TOTAL = 10, 13, 16, 19, 23

    out, bad = {}, 0
    for row in data:
        if len(row) <= I_TOTAL:
            continue
        code = T.clean(row[0])
        if not code:
            continue
        fgn = T.pn(row[I_FGN])
        inv = T.pn(row[I_INV])
        sf = T.pn(row[I_SELF])
        hd = T.pn(row[I_HEDGE])
        tot = T.pn(row[I_TOTAL])
        if None in (fgn, inv, sf, hd, tot):
            continue
        # 驗算：外資 + 投信 + 自營（自行＋避險）要等於三大法人合計
        if abs((fgn + inv + sf + hd) - tot) > 1:
            bad += 1
            continue
        out[code] = {"fgn": lot(fgn), "inv": lot(inv),
                     "dlrSelf": lot(sf), "dlrHedge": lot(hd)}
    if bad:
        print("      上櫃法人：%d 筆加總對不上，已跳過（欄位可能改版）" % bad)
    return out


def fetch_tpex_margin(day):
    """上櫃融資融券餘額，單位張。"""
    url = ("https://www.tpex.org.tw/www/zh-tw/margin/balance"
           "?type=Daily&date=%s&response=json" % day.strftime("%Y/%m/%d"))
    fields, data = _tpex_rows(T.get_json(url), 18)
    if not data:
        return {}

    # 代號 名稱 前資餘額 資買 資賣 現償 資餘額 資屬證金 資使用率 資限額
    #           前券餘額 券賣 券買 券償 券餘額 ...
    I_MGN_PREV, I_MGN, I_SHRT_PREV, I_SHRT = 2, 6, 10, 14

    out = {}
    for row in data:
        if len(row) <= I_SHRT:
            continue
        code = T.clean(row[0])
        if not code:
            continue
        out[code] = {
            "mgn": T.pn(row[I_MGN]),
            "mgnPrev": T.pn(row[I_MGN_PREV]),
            "shrt": T.pn(row[I_SHRT]),
            "shrtPrev": T.pn(row[I_SHRT_PREV]),
        }
    return out


# ---------------------------------------------------------------- 組裝

def lot(shares):
    """股 → 張。"""
    return None if shares is None else shares / 1000.0


def fetch_chip_day(day, pause=PAUSE):
    """抓一天的四個來源，合併成 {code: {...}}。抓不到就回空的。"""
    merged = {}

    def merge(src):
        for code, d in src.items():
            merged.setdefault(code, {}).update(d)

    merge(fetch_twse_insti(day))
    time.sleep(pause)
    merge(fetch_tpex_insti(day))
    time.sleep(pause)
    merge(fetch_twse_margin(day))
    time.sleep(pause)
    merge(fetch_tpex_margin(day))

    # 只留下真的有數字的（法人或融資至少一邊有）
    return {c: d for c, d in merged.items() if any(v is not None for v in d.values())}


# ---------------------------------------------------------------- 集保

def read_tdcc_latest_two():
    """回傳最新與上一期的集保資料 (new, old)，各是 {code: {big, small, holders}}。"""
    if not os.path.isdir(TDCC_DIR):
        return {}, {}
    fns = sorted(fn for fn in os.listdir(TDCC_DIR) if fn.endswith(".txt"))
    if not fns:
        return {}, {}

    def load(fn):
        out = {}
        with open(os.path.join(TDCC_DIR, fn), encoding="utf-8") as fh:
            fh.readline()
            for line in fh:
                p = line.rstrip("\n").split(",")
                if len(p) >= 4 and p[0]:
                    out[p[0]] = {"big": T.pn(p[1]), "small": T.pn(p[2]),
                                 "holders": T.pn(p[3])}
        return out

    new = load(fns[-1])
    old = load(fns[-2]) if len(fns) >= 2 else {}
    return new, old


# ---------------------------------------------------------------- 衍生

def streak(series):
    """series 由新到舊。回傳連買天數（正）或連賣天數（負）；當天是 0 就回 0。"""
    if not series or series[0] is None or series[0] == 0:
        return 0
    sign = 1 if series[0] > 0 else -1
    n = 0
    for v in series:
        if v is None or v == 0 or (v > 0) != (sign > 0):
            break
        n += 1
    return n * sign


_MKT_CACHE = {}


def market_info():
    """market.txt → {code: (mkt, close)}。算融資維持率要用現價和市場別。"""
    info = {}
    if not os.path.exists(T.MARKET):
        return info
    with open(T.MARKET, encoding="utf-8") as fh:
        head = fh.readline().strip().split("|")[-1].split(",")
        try:
            i_mkt, i_close = head.index("mkt"), head.index("close")
        except ValueError:
            return info
        for line in fh:
            p = line.rstrip("\n").split(",")
            if len(p) <= max(i_mkt, i_close) or not p[0]:
                continue
            c = T.pn(p[i_close])
            if c and c > 0:
                info[p[0]] = (p[i_mkt], c)
    return info


def margin_cost(code, days, chipcache, pxcache):
    """估算目前融資餘額的平均買進成本，用移動平均成本法。

    每天融資餘額比前一天多出來的部分，當成「那天用當天收盤價融資買進」；
    餘額減少時當成按比例平倉，平均成本不變。這是籌碼網站的標準估法 ——
    證交所只公佈餘額張數，不公佈金額，成本只能這樣反推。

    回傳 (平均成本, 可信度)。可信度 = 視窗內累積新增 ÷ 目前餘額：
    接近 1 代表現在的部位大多是在這段期間建立的，估出來的成本才可信；
    很小代表大部分部位在 267 天之前就進場了，成本是猜的。
    """
    px = pxcache.get(code)
    if px is None:
        px = {}
        for d, line in T.read_stock(code).items():
            p = line.split(",")
            if len(p) >= 5:
                v = T.pn(p[4])
                if v and v > 0:
                    px[d] = v
        pxcache[code] = px
    if not px:
        return None, None

    cost, bal, added = None, 0.0, 0.0
    for d in days:
        row = chipcache.get(d, {}).get(code)
        if row is None:
            continue
        m = row.get("mgn")
        p = px.get(d)
        if m is None or m < 0 or p is None:
            continue
        if bal <= 0:
            if m > 0:
                cost, added = p, m       # 視窗內第一次看到餘額，只能假設就在這天建立
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


def market_codes():
    """market.txt 裡的股票代號。T86 連權證、ETN 都給，不篩會多出一萬多筆。"""
    if "v" in _MKT_CACHE:
        return _MKT_CACHE["v"]
    _MKT_CACHE["v"] = None
    if not os.path.exists(T.MARKET):
        return None
    codes = set()
    with open(T.MARKET, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            c = line.split(",")[0].strip()
            if c:
                codes.add(c)
    _MKT_CACHE["v"] = codes or None
    return _MKT_CACHE["v"]


def derive():
    """讀最近幾天的每日檔＋集保，算出 chips.txt。"""
    days = chip_days()
    if not days:
        print("還沒有任何籌碼資料，先跑一次 python chips.py。")
        return 0

    recent = days[-LOOKBACK:][::-1]          # 由新到舊，算連買連賣用
    cache = {d: read_day(d) for d in recent}
    today = cache[recent[0]]

    # 估融資成本要看更長的一段（連買天數只要 40 天，成本要盡量涵蓋整批部位）
    mdays = days[-MARGIN_LOOKBACK:]          # 由舊到新
    mcache = dict(cache)
    for d in mdays:
        if d not in mcache:
            mcache[d] = read_day(d)
    info = market_info()
    pxcache = {}

    keep = market_codes()
    if keep:
        skipped = len(today) - sum(1 for c in today if c in keep)
        today = {c: v for c, v in today.items() if c in keep}
        print("  只留 market.txt 裡的 %d 檔（濾掉權證等 %d 筆）"
              % (len(today), skipped))

    tdcc_new, tdcc_old = read_tdcc_latest_two()

    lines = []
    for code in sorted(today):
        cur = today[code]
        fgn_s = [cache[d].get(code, {}).get("fgn") for d in recent]
        inv_s = [cache[d].get(code, {}).get("inv") for d in recent]

        mgn = cur.get("mgn")
        mgn_prev = cur.get("mgnPrev")
        shrt = cur.get("shrt")
        mgn_chg = (mgn - mgn_prev) if (mgn is not None and mgn_prev is not None) else None
        # 券資比 = 融券餘額 / 融資餘額
        sr = (shrt / mgn * 100) if (shrt is not None and mgn and mgn > 0) else None

        t_new = tdcc_new.get(code, {})
        t_old = tdcc_old.get(code, {})
        # 教材：大戶增、散戶減 → 股價向上；大戶減、散戶增 → 股價向下
        #       集保戶數減 → 股價漲；集保戶數增 → 股價跌
        # 所以三個都要算「跟上一期比」的變化，不是只看當期水位。
        def wk(key):
            a, b = t_new.get(key), t_old.get(key)
            return (a - b) if (a is not None and b is not None) else None
        big = t_new.get("big")
        big_chg = wk("big")
        small_chg = wk("small")
        holders_chg = wk("holders")

        # 融資維持率 = 現價 ÷ (平均成本 × 融資成數) × 100
        rate = mcost = conf = None
        if mgn and mgn > 0 and code in info:
            mkt, px_now = info[code]
            mcost, conf = margin_cost(code, mdays, mcache, pxcache)
            if mcost and mcost > 0:
                r = MARGIN_RATE.get(mkt, MARGIN_DEFAULT)
                rate = px_now / (mcost * r) * 100

        lines.append(",".join([
            code,
            T.fmt(cur.get("fgn")), str(streak(fgn_s)),
            T.fmt(cur.get("inv")), str(streak(inv_s)),
            T.fmt(cur.get("dlrHedge")),
            T.fmt(mgn), T.fmt(mgn_chg),
            T.fmt(shrt), ("" if sr is None else "%.1f" % sr),
            ("" if big is None else "%.2f" % big),
            ("" if big_chg is None else "%.2f" % big_chg),
            ("" if t_new.get("small") is None else "%.2f" % t_new["small"]),
            ("" if small_chg is None else "%.2f" % small_chg),
            T.fmt(t_new.get("holders")),
            T.fmt(holders_chg),
            ("" if rate is None else "%.1f" % rate),
            ("" if mcost is None else "%.2f" % mcost),
            ("" if conf is None else "%.2f" % conf),
        ]))

    os.makedirs(T.DATA, exist_ok=True)
    now = datetime.now(T.TPE)
    head = "TWLVC1|%s|%s|%d|%s" % (recent[0], now.strftime("%Y%m%d%H%M"),
                                   len(lines), COLS)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(head + "\n" + "\n".join(lines) + "\n")
    print("已寫入 %s：%d 檔、%d KB（資料日 %s，回看 %d 天）"
          % (OUT, len(lines), os.path.getsize(OUT) // 1024, recent[0], len(recent)))
    return len(lines)


# ---------------------------------------------------------------- 主程式

def run_range(days_from, days_to):
    now = datetime.now(T.TPE)
    base = now.date()
    known = T.read_days()          # 拿行情的日曆當基準，非交易日不用白跑
    todo = []
    for back in range(days_to, days_from + 1):
        d = base - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y%m%d")
        if known.get(ds) == 0:              # 已知的放假日
            continue
        if ds not in known:                 # 行情都還沒抓過，籌碼也不用抓
            continue
        if have_day(ds):
            continue
        todo.append(d)
    todo.sort()
    print("待抓 %d 個交易日的籌碼" % len(todo))

    ok = miss = 0
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        day = datetime(d.year, d.month, d.day, tzinfo=T.TPE)
        ds = day.strftime("%Y%m%d")
        try:
            rows = fetch_chip_day(day)
        except Exception as e:
            print("  %s 例外：%s" % (ds, e))
            miss += 1
            continue
        if rows:
            write_day(ds, rows)
            ok += 1
        else:
            miss += 1
        if i % 10 == 0 or i == len(todo):
            el = time.time() - t0
            print("  進度 %d/%d　成功 %d、沒資料 %d　已用 %.0f 分、預估剩 %.0f 分"
                  % (i, len(todo), ok, miss, el / 60,
                     (len(todo) - i) * (el / i) / 60))
    return ok


def run_latest():
    """跟著 market.txt 的資料日走，確保籌碼和行情是同一天。"""
    import json
    if not os.path.exists(T.META):
        print("找不到 meta.json，先跑 fetch.py。")
        return 0
    meta = json.load(open(T.META, encoding="utf-8"))
    ds = str(meta.get("date") or "")
    if len(ds) != 8:
        print("meta.json 沒有資料日。")
        return 0
    # 先回頭看最近 5 個已經抓過的日子有沒有半套的（通常是融資融券還沒出就跑了），
    # 有的話補抓。不補的話那幾天的融資維持率、券資比會永遠是空的。
    fixed = 0
    for old in chip_days()[-5:]:
        if old == ds or have_day(old):
            continue
        d0 = datetime(int(old[:4]), int(old[4:6]), int(old[6:]), tzinfo=T.TPE)
        print("補抓 %s（之前只抓到一半）..." % old)
        r0 = fetch_chip_day(d0)
        if r0 and sum(1 for r in r0.values() if r.get("mgn") is not None) >= MIN_MARGIN_ROWS:
            write_day(old, r0)
            print("  補回 %d 檔" % len(r0))
            fixed += 1
        else:
            print("  還是沒有融資資料，下次再試。")

    if have_day(ds):
        print("籌碼 %s 已經抓齊，不重抓。" % ds)
        return fixed
    day = datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:]), tzinfo=T.TPE)
    print("抓 %s 的籌碼 ..." % ds)
    rows = fetch_chip_day(day)
    if not rows:
        print("  四個來源都沒給資料（可能還沒出檔）。")
        return fixed
    n_mgn = sum(1 for r in rows.values() if r.get("mgn") is not None)
    write_day(ds, rows)
    print("  %d 檔（其中 %d 檔有融資資料）" % (len(rows), n_mgn))
    if n_mgn < MIN_MARGIN_ROWS:
        print("  ⚠ 融資融券還沒出檔，這天先算半套，明天會自動補。")
    return len(rows)


def main():
    args = [a for a in sys.argv[1:] if a != "--derive"]
    if "--derive" in sys.argv[1:]:
        derive()
        return
    if len(args) >= 2:
        run_range(int(args[0]), int(args[1]))
    else:
        run_latest()
    derive()


if __name__ == "__main__":
    main()
