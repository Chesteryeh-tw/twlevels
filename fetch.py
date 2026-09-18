#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日更新：抓最新一個交易日的全市場盤後資料。

產出
  data/market.txt        最新一日全市場（查詢／篩選用）
  data/stock/<代號>.txt  每檔的歷史（走勢圖用，逐日累加）
  data/meta.json         狀態
"""

import sys
from datetime import datetime, timedelta

import twse as T


def main():
    now = datetime.now(T.TPE)
    print("執行時間（台北）：%s" % now.strftime("%Y-%m-%d %H:%M"))

    start = T.target_day(now)
    picked = None
    for back in range(12):
        day = start - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        print("  嘗試 %s ..." % day.strftime("%Y-%m-%d"))
        rows = T.fetch_day(day)
        if rows:
            n1 = sum(1 for r in rows if r["mkt"] == "1")
            print("    上市 %d 檔、上櫃 %d 檔" % (n1, len(rows) - n1))
            picked = (day, rows)
            break
        print("    非交易日或尚未出檔")

    if not picked:
        print("錯誤：連續 12 天都抓不到資料。")
        sys.exit(1)

    day, rows = picked
    shares = T.load_shares()

    size = T.write_market(day, rows, shares, now)
    print("  已寫入 %s（%d KB）" % (T.MARKET, size // 1024))

    # 逐檔累加歷史
    added = skipped = 0
    dstr = day.strftime("%Y%m%d")
    for r in rows:
        series = T.read_stock(r["code"])
        line = T.stock_line(day, r)
        if series.get(dstr) == line:
            skipped += 1
            continue
        series[dstr] = line
        T.write_stock(r["code"], series)
        added += 1
    print("  歷史檔：更新 %d 檔、未變動 %d 檔" % (added, skipped))

    days = T.read_days()
    days[dstr] = T.day_counts(rows)
    T.write_days(days)

    meta = T.write_meta(day, rows, now, extra={"tradingDays": len(days)})
    h = meta["history"]
    print("完成：資料日 %s，%d 檔；歷史涵蓋 %s ~ %s（%d 天）"
          % (meta["date"], meta["count"], h["first"], h["last"], h["days"]))


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股三線點位 — 全市場盤後資料抓取
由 GitHub Actions 每天自動執行，不需要任何人的電腦。

輸出： data/market.txt
格式： TWLV1|資料日|抓取時間|檔數
       代號,名稱,市場(1上市/2上櫃),高,低,收,漲跌,成交張數,成交金額(萬元),流通股數(千股)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
OUT = os.path.join("data", "market.txt")
SHARES = os.path.join("data", "shares.txt")


# ---------------------------------------------------------------- 網路

def get_json(url, tries=3):
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
            print("    抓取失敗 (%d/%d) %s → %s" % (i + 1, tries, url, e))
            time.sleep(3 * (i + 1))
    return None


# ---------------------------------------------------------------- 解析小工具

def clean(s):
    if s is None:
        return ""
    s = str(s)
    out, skip = [], False
    for ch in s:                      # 去掉 HTML 標籤
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
    """先找完全相同的欄名，再找包含關係。"""
    for n in names:
        if n in fields:
            return fields.index(n)
    for n in names:
        for i, f in enumerate(fields):
            if n in f:
                return i
    return -1


def fmt(x):
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
        iH, iL, iX = idx(f, "最高價"), idx(f, "最低價"), idx(f, "收盤價")
        iS, iV = idx(f, "成交股數"), idx(f, "成交金額")
        iU, iD = idx(f, "漲跌(+/-)", "漲跌"), idx(f, "漲跌價差")
        for r in tb.get("data", []):
            code = clean(r[iC])
            if len(code) != 4:                      # 只留 4 碼（剔除權證等）
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
                "h": h, "l": l, "c": c, "chg": chg,
                "lot": None if sh is None else round(sh / 1000),
                "wan": None if vv is None else round(vv / 10000),
            })
    return rows


# ---------------------------------------------------------------- 流通股數（週轉率用）

def load_shares():
    if os.path.exists(SHARES):
        age = (time.time() - os.path.getmtime(SHARES)) / 86400
        if age < 20:
            m = {}
            with open(SHARES, encoding="utf-8") as fh:
                for line in fh:
                    p = line.strip().split(",")
                    if len(p) == 2 and p[0]:
                        m[p[0]] = p[1]
            print("  流通股數：沿用快取 %d 檔" % len(m))
            return m

    m = {}
    for url in ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        arr = get_json(url, tries=2)
        if not isinstance(arr, list):
            continue
        for it in arr:
            code = str(it.get("公司代號", "")).strip()
            v = it.get("已發行普通股數或TDR原股發行股數") or it.get("已發行普通股數")
            n = pn(v)
            if code and n and n > 0:
                m[code] = str(round(n / 1000))          # 千股
    if m:
        os.makedirs("data", exist_ok=True)
        with open(SHARES, "w", encoding="utf-8") as fh:
            fh.write("\n".join("%s,%s" % (k, v) for k, v in sorted(m.items())))
        print("  流通股數：重新下載 %d 檔" % len(m))
    else:
        print("  流通股數：取得失敗，週轉率這次會留白")
    return m


# ---------------------------------------------------------------- 主程式

def target_day(now):
    """平日 15 點後算今天，否則往前推到上一個平日。"""
    d = now
    if d.weekday() < 5 and d.hour >= 15:
        return d
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main():
    now = datetime.now(TPE)
    print("執行時間（台北）：%s" % now.strftime("%Y-%m-%d %H:%M"))

    start = target_day(now)
    picked = None
    for back in range(12):
        day = start - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        print("  嘗試 %s ..." % day.strftime("%Y-%m-%d"))
        tw = fetch_twse(day)
        if not tw:
            print("    非交易日或尚未出檔")
            continue
        tp = fetch_tpex(day)
        print("    上市 %d 檔、上櫃 %d 檔" % (len(tw), len(tp)))
        picked = (day, tw + tp)
        break

    if not picked:
        print("錯誤：連續 12 天都抓不到資料。")
        sys.exit(1)

    day, rows = picked
    shares = load_shares()

    lines = ["TWLV1|%s|%s|%d" % (day.strftime("%Y%m%d"),
                                 now.strftime("%Y%m%d%H%M"), len(rows))]
    for r in rows:
        lines.append(",".join([
            r["code"], r["name"].replace(",", ""), r["mkt"],
            fmt(r["h"]), fmt(r["l"]), fmt(r["c"]), fmt(r["chg"]),
            "" if r["lot"] is None else str(r["lot"]),
            "" if r["wan"] is None else str(r["wan"]),
            shares.get(r["code"], ""),
        ]))

    text = "\n".join(lines) + "\n"
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)

    print("完成：資料日 %s，共 %d 檔，%d KB → %s"
          % (day.strftime("%Y-%m-%d"), len(rows), len(text.encode()) // 1024, OUT))


if __name__ == "__main__":
    main()
