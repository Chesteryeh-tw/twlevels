#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股盤後資料共用模組（fetch.py 與 backfill.py 都用這支）

資料來源：臺灣證券交易所、證券櫃檯買賣中心 公開資料
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

DATA = "data"
STOCK_DIR = os.path.join(DATA, "stock")     # 每檔一個歷史檔
MARKET = os.path.join(DATA, "market.txt")   # 最新一日全市場
SHARES = os.path.join(DATA, "shares.txt")   # 流通股數快取
META = os.path.join(DATA, "meta.json")
DAYS = os.path.join(DATA, "days.json")      # 每個交易日抓到幾檔 {日期: [上市, 上櫃]}


def read_days():
    if os.path.exists(DAYS):
        try:
            with open(DAYS, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def write_days(days):
    os.makedirs(DATA, exist_ok=True)
    with open(DAYS, "w", encoding="utf-8") as fh:
        json.dump(days, fh, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"))


def day_counts(rows):
    n1 = sum(1 for r in rows if r["mkt"] == "1")
    return [n1, len(rows) - n1]


# ---------------------------------------------------------------- 網路

def get_json(url, tries=3, pause=2.0):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-TW,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8-sig"))
        except Exception as e:
            if i == tries - 1:
                print("      取得失敗 %s → %s" % (url[:70], e))
            time.sleep(pause * (i + 1))
    return None


# ---------------------------------------------------------------- 解析

def clean(s):
    if s is None:
        return ""
    out, skip = [], False
    for ch in str(s):
        if ch == "<":
            skip = True
        elif ch == ">":
            skip = False
        elif not skip:
            out.append(ch)
    return "".join(out).replace("&nbsp;", "").strip()


def pn(s):
    t = clean(s).replace(",", "").replace("+", "").replace(" ", "")
    if t in ("", "--", "-", "X", "x", "null", "None"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def idx(fields, *names):
    for n in names:
        if n in fields:
            return fields.index(n)
    for n in names:
        for i, f in enumerate(fields):
            if n in f:
                return i
    return -1


def fmt(x):
    """數字轉最短字串；None → 空字串。"""
    if x is None:
        return ""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return ("%.4f" % x).rstrip("0").rstrip(".")


# ---------------------------------------------------------------- 上市

def fetch_twse(day):
    url = ("https://www.twse.com.tw/exchangeReport/MI_INDEX"
           "?response=json&date=%s&type=ALLBUT0999" % day.strftime("%Y%m%d"))
    j = get_json(url)
    rows = []
    if not j or j.get("stat") != "OK":
        return rows
    for tb in j.get("tables", []):
        f = [clean(x) for x in tb.get("fields", [])]
        if "證券代號" not in f or "收盤價" not in f:
            continue
        iC, iN = idx(f, "證券代號"), idx(f, "證券名稱")
        iO = idx(f, "開盤價")
        iH, iL, iX = idx(f, "最高價"), idx(f, "最低價"), idx(f, "收盤價")
        iS, iV = idx(f, "成交股數"), idx(f, "成交金額")
        iU, iD = idx(f, "漲跌(+/-)", "漲跌"), idx(f, "漲跌價差")
        for r in tb.get("data", []):
            code = clean(r[iC])
            if len(code) != 4:
                continue
            h, l, c = pn(r[iH]), pn(r[iL]), pn(r[iX])
            if None in (h, l, c) or c <= 0:
                continue
            chg = pn(r[iD]) if iD >= 0 else None
            if chg is not None and iU >= 0 and "-" in clean(r[iU]):
                chg = -chg
            sh = pn(r[iS]) if iS >= 0 else None
            vv = pn(r[iV]) if iV >= 0 else None
            rows.append({
                "code": code, "name": clean(r[iN]), "mkt": "1",
                "o": pn(r[iO]) if iO >= 0 else None,
                "h": h, "l": l, "c": c, "chg": chg,
                "lot": None if sh is None else round(sh / 1000),
                "wan": None if vv is None else round(vv / 10000),
            })
    return rows


# ---------------------------------------------------------------- 上櫃

def fetch_tpex(day):
    url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
           "?date=%s&type=EW&id=&response=json" % day.strftime("%Y/%m/%d"))
    j = get_json(url)
    rows = []
    if not j:
        return rows
    for tb in j.get("tables", []):
        f = [clean(x).replace("<br>", "") for x in tb.get("fields", [])]
        if "代號" not in f or "收盤" not in f:
            continue
        iC, iN = idx(f, "代號"), idx(f, "名稱")
        iO = idx(f, "開盤")
        iH, iL, iX = idx(f, "最高"), idx(f, "最低"), idx(f, "收盤")
        iG = idx(f, "漲跌")
        iS, iV = idx(f, "成交股數"), idx(f, "成交金額(元)", "成交金額")
        for r in tb.get("data", []):
            code = clean(r[iC])
            if len(code) != 4:
                continue
            h, l, c = pn(r[iH]), pn(r[iL]), pn(r[iX])
            if None in (h, l, c) or c <= 0:
                continue
            chg = None
            if iG >= 0:
                raw = clean(r[iG])
                v = pn(raw)
                if v is not None:
                    chg = -abs(v) if raw.startswith("-") else abs(v)
            sh = pn(r[iS]) if iS >= 0 else None
            vv = pn(r[iV]) if iV >= 0 else None
            rows.append({
                "code": code, "name": clean(r[iN]), "mkt": "2",
                "o": pn(r[iO]) if iO >= 0 else None,
                "h": h, "l": l, "c": c, "chg": chg,
                "lot": None if sh is None else round(sh / 1000),
                "wan": None if vv is None else round(vv / 10000),
            })
    return rows


def fetch_day_split(day, pause=1.0):
    """回傳 (上市 rows, 上櫃 rows)。兩邊都可能是空的。"""
    tw = fetch_twse(day)
    if not tw:
        return [], []
    time.sleep(pause)
    tp = fetch_tpex(day)
    time.sleep(pause)
    return tw, tp


def fetch_day(day, pause=1.0):
    """回傳當日全市場（上市＋上櫃）。

    只有一邊抓得到時一律回空 list —— 這是刻意的。
    櫃買有維護時段，之前那樣直接回「只有上市」的結果，
    會把 market.txt 覆蓋成少了 868 檔上櫃的半套資料，
    畫面上查上櫃股票全部變成查無此股，比少更新一天糟糕得多。
    """
    tw, tp = fetch_day_split(day, pause=pause)
    if not tw:
        return []
    if not tp:
        print("      上市有 %d 檔但上櫃是空的 —— 當成沒抓到，不覆蓋既有資料" % len(tw))
        return []
    return tw + tp


# ---------------------------------------------------------------- 流通股數

def load_shares(max_age_days=20, min_codes=1500):
    """流通股數（千股）。快取一陣子，但缺太多檔就重抓。

    以前這裡只看檔案的 mtime，在 GitHub Actions 上永遠失效 ——
    每次 checkout 都會把檔案時間設成當下，所以快取「永遠是新的」，
    上櫃那批股本從來沒被抓進來過，上櫃週轉率也就一直是空的。
    現在改成同時看「筆數夠不夠」，缺了就重抓。
    """
    if os.path.exists(SHARES):
        cached = {}
        with open(SHARES, encoding="utf-8") as fh:
            for line in fh:
                p = line.strip().split(",")
                if len(p) == 2 and p[0]:
                    cached[p[0]] = p[1]
        age = (time.time() - os.path.getmtime(SHARES)) / 86400
        if age < max_age_days and len(cached) >= min_codes:
            print("  流通股數：沿用快取 %d 檔" % len(cached))
            return cached
        print("  流通股數：快取只有 %d 檔（要 %d 檔），重抓"
              % (len(cached), min_codes))

    m = {}
    srcs = [
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",   # 上櫃，892 檔
        "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_R",   # 興櫃，備用
    ]
    for url in srcs:
        arr = get_json(url, tries=2)
        if not isinstance(arr, list) or not arr:
            continue
        got = 0
        for it in arr:
            if not isinstance(it, dict):
                continue
            code = str(it.get("公司代號") or it.get("SecuritiesCompanyCode") or "").strip()
            v = (it.get("已發行普通股數或TDR原股發行股數")
                 or it.get("已發行普通股數")
                 or it.get("IssueShares"))
            n = pn(v)
            if code and n and n > 0 and code not in m:
                m[code] = str(round(n / 1000))      # 千股
                got += 1
        print("  流通股數：%s → %d 檔" % (url.split("/")[-1], got))

    if m:
        os.makedirs(DATA, exist_ok=True)
        with open(SHARES, "w", encoding="utf-8") as fh:
            fh.write("\n".join("%s,%s" % (k, v) for k, v in sorted(m.items())))
        print("  流通股數：合計 %d 檔" % len(m))
    else:
        print("  流通股數：全部來源都失敗，週轉率會留白")
    return m


# ---------------------------------------------------------------- 寫檔

MARKET_HEADER_COLS = "code,name,mkt,high,low,close,chg,lot,wan,kshares,open"


def write_market(day, rows, shares, now):
    """最新一日全市場快照。欄位只往後加，舊版解析器不會壞。"""
    lines = ["TWLV1|%s|%s|%d|%s" % (day.strftime("%Y%m%d"),
                                    now.strftime("%Y%m%d%H%M"),
                                    len(rows), MARKET_HEADER_COLS)]
    for r in rows:
        lines.append(",".join([
            r["code"], r["name"].replace(",", ""), r["mkt"],
            fmt(r["h"]), fmt(r["l"]), fmt(r["c"]), fmt(r["chg"]),
            fmt(r["lot"]), fmt(r["wan"]),
            shares.get(r["code"], ""),
            fmt(r["o"]),
        ]))
    os.makedirs(DATA, exist_ok=True)
    text = "\n".join(lines) + "\n"
    with open(MARKET, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode())


STOCK_COLS = "date,open,high,low,close,lot,wan"


def read_stock(code):
    """回傳 {日期字串: 該行文字}，不含表頭。"""
    path = os.path.join(STOCK_DIR, code + ".txt")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = line.split(",", 1)[0]
            if len(d) == 8 and d.isdigit():
                out[d] = line
    return out


def write_stock(code, series):
    """series: {日期: 行文字}，依日期排序後寫入。"""
    os.makedirs(STOCK_DIR, exist_ok=True)
    path = os.path.join(STOCK_DIR, code + ".txt")
    body = "\n".join(series[d] for d in sorted(series))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# " + STOCK_COLS + "\n" + body + "\n")


def stock_line(day, r):
    return ",".join([day.strftime("%Y%m%d"), fmt(r["o"]), fmt(r["h"]),
                     fmt(r["l"]), fmt(r["c"]), fmt(r["lot"]), fmt(r["wan"])])


def write_meta(day, rows, now, extra=None):
    codes = sorted(set(r["code"] for r in rows))
    first = last = None
    n_days = 0
    if codes:
        # 用成交量最大的那檔當代表，量測歷史涵蓋範圍
        rep = max(rows, key=lambda r: (r["lot"] or 0))["code"]
        s = read_stock(rep)
        if s:
            ks = sorted(s)
            first, last, n_days = ks[0], ks[-1], len(ks)
    meta = {
        "date": day.strftime("%Y%m%d"),
        "fetched": now.strftime("%Y%m%d%H%M"),
        "count": len(rows),
        "listed": sum(1 for r in rows if r["mkt"] == "1"),
        "otc": sum(1 for r in rows if r["mkt"] == "2"),
        "history": {"first": first, "last": last, "days": n_days},
        "marketCols": MARKET_HEADER_COLS,
        "stockCols": STOCK_COLS,
    }
    if extra:
        meta.update(extra)
    os.makedirs(DATA, exist_ok=True)
    with open(META, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return meta


# ---------------------------------------------------------------- 日期

def target_day(now):
    d = now
    if d.weekday() < 5 and d.hour >= 15:
        return d
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
