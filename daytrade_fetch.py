#!/usr/bin/env python3
"""現股當沖（2026-10-28，使用者同意抓）：證交所 TWTB4U、櫃買 intraday stat，每天全市場每檔的當沖成交股數與金額。

輸出（Actions 快取，不進 repo；檔大、而且是逐日原始值）：
  data/daytrade/<YYYYMMDD>.txt：code,dt_lots,buy_wan,sell_wan（張、萬元；上市＋上櫃）
已有的日期跳過；抓不到（休市、還沒公布）不寫檔，下次再抓。
當沖率＝當沖成交股數 ÷ 當日成交股數（回測端用 data/stock 的成交張數算）。

用法：
  python daytrade_fetch.py [--start 20210101] [--limit N] [--pause 3.5]
  日期清單＝data/stock/2330.txt（或 0050）出現的交易日（跟回測同一份）。
"""
import json
import os
import sys
import time
import urllib.parse

import twse as T

OUT = os.path.join("data", "daytrade")


def _tables(j):
    """兩個網站、新舊格式都可能：{"tables":[{fields,data}]}、或頂層 fields/data、或 fields1/data1…"""
    if not isinstance(j, dict):
        return []
    out = []
    for t in j.get("tables") or []:
        if isinstance(t, dict) and t.get("fields") and t.get("data") is not None:
            out.append((t["fields"], t["data"]))
    if j.get("fields") and j.get("data") is not None:
        out.append((j["fields"], j["data"]))
    for k in list(j.keys()):
        if k.startswith("fields") and k != "fields":
            dk = "data" + k[len("fields"):]
            if dk in j:
                out.append((j[k], j[dk]))
    return out


def _parse(j):
    rows = {}
    for f, data in _tables(j):
        f = [str(x).strip() for x in f]
        try:
            ic = next(i for i, x in enumerate(f) if "代號" in x)
            iv = next(i for i, x in enumerate(f) if "成交股數" in x)
            ib = next(i for i, x in enumerate(f) if "買進成交金額" in x)
            isl = next(i for i, x in enumerate(f) if "賣出成交金額" in x)
        except StopIteration:
            continue
        for r in data:
            try:
                code = str(r[ic]).strip()
                v = T.pn(r[iv])
                b = T.pn(r[ib])
                s = T.pn(r[isl])
            except (IndexError, TypeError):
                continue
            if not code or v is None:
                continue
            rows[code] = (v / 1000.0, (b or 0) / 1e4, (s or 0) / 1e4)
    return rows


def fetch(day, pause):
    y, m, d = day[:4], day[4:6], day[6:]
    u1 = "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date=%s&selectType=All&response=json" % day
    u2 = ("https://www.tpex.org.tw/www/zh-tw/intraday/stat?type=Daily&date=%s&response=json"
          % urllib.parse.quote("%s/%s/%s" % (y, m, d), safe=""))
    a = T.get_json(u1)
    time.sleep(pause)
    b = T.get_json(u2)
    time.sleep(1.0)
    return _parse(a), _parse(b)


def main():
    args = sys.argv[1:]

    def opt(name, default):
        return args[args.index(name) + 1] if name in args else default
    start = opt("--start", "20210101")
    limit = int(opt("--limit", "0"))
    pause = float(opt("--pause", "3.5"))
    os.makedirs(OUT, exist_ok=True)
    src = next((p for p in ("data/stock/2330.txt", "data/stock/0050.txt") if os.path.exists(p)), None)
    days = sorted({l.split(",")[0] for l in open(src, encoding="utf-8") if l[:1].isdigit()})
    days = [d for d in days if d >= start and not os.path.exists(os.path.join(OUT, d + ".txt"))]
    if limit:
        days = days[:limit]
    print("要抓 %d 天（%s ～ %s）" % (len(days), days[0] if days else "-", days[-1] if days else "-"))
    ok = bad = 0
    miss = []
    t0 = time.time()
    for n, d in enumerate(days, 1):
        try:
            a, b = fetch(d, pause)
        except Exception as e:
            print("::warning title=當沖 %s::%s" % (d, str(e)[:300]))
            a, b = {}, {}
        if not a or not b:          # 有一邊沒抓到就不寫檔（下次再抓）；連續 10 天都缺多半是被擋，照規則停
            miss.append("%s(%s%s)" % (d, "" if a else "上市", "" if b else "上櫃"))
            bad += 1
            if bad >= 10:
                print("::error title=當沖::連續 10 天抓不完整，停止（可能被網站拒絕；不換位址、不偽裝）")
                break
            continue
        bad = 0
        with open(os.path.join(OUT, d + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("code,dt_lots,buy_wan,sell_wan\n")
            for code, (v, bw, sw) in sorted({**a, **b}.items()):
                fh.write("%s,%.3f,%.1f,%.1f\n" % (code, v, bw, sw))
        ok += 1
        if n % 50 == 0:
            print("  %d/%d 天，%.0f 秒" % (n, len(days), time.time() - t0))
    have = len([f for f in os.listdir(OUT) if f.endswith(".txt")])
    print("::notice title=當沖抓取::這次寫入 %d 天、快取共 %d 天；缺一邊 %d 天%s" % (
        ok, have, len(miss), ("：" + "、".join(miss[:15])) if miss else ""))


if __name__ == "__main__":
    main()
