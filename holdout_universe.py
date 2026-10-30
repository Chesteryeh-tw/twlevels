#!/usr/bin/env python3
"""2016～2020 驗證資料的存活偏誤：找出當時有交易、現在已經不在市場上的普通股（2026-10-30）。

做法：2016-01～2020-12 每個月第一個交易日抓一次全市場收盤行情（證交所 MI_INDEX、櫃買 EW；官方公開資料），
四碼、第一碼不是 0（排除 ETF）的代號取聯集；減掉現在 data/market.txt 還在的代號 → 下市櫃（或改代號）候選。
輸出 data/holdout_delisted.csv：code,name,market,first_seen,last_seen（月快照的最早、最晚出現日；不是最後交易日，
最後交易日以補抓的日線最後一天為準）。只放代號與名稱，可進 repo。GPT 的證交所 66 筆清單另外對照。

用法：python holdout_universe.py   （Actions；證交所容器連不到）
"""
import datetime as dt
import os
import time

import twse as T


def first_trading(y, m):
    for d in range(1, 12):
        day = dt.date(y, m, d)
        if day.weekday() >= 5:
            continue
        a = T.fetch_twse(day)
        time.sleep(3.5)
        if a:
            b = T.fetch_tpex(day)
            time.sleep(1.5)
            return day, a, b or []
    return None, [], []


def main():
    cur = set()
    for l in open(T.MARKET, encoding="utf-8").read().splitlines()[1:]:
        p = l.split(",")
        if p and p[0]:
            cur.add(p[0])
    seen = {}
    nd = 0
    for y in range(2016, 2021):
        for m in range(1, 13):
            day, a, b = first_trading(y, m)
            if not day:
                print("::warning title=快照::%d-%02d 抓不到" % (y, m))
                continue
            nd += 1
            ds = day.strftime("%Y%m%d")
            for mk, rows in (("TWSE", a), ("TPEX", b)):
                for r in rows:
                    c = r["code"]
                    if len(c) != 4 or not c.isdigit() or c[0] == "0":
                        continue
                    s = seen.setdefault(c, {"name": r["name"], "market": mk, "first": ds, "last": ds})
                    s["last"] = ds
                    s["name"], s["market"] = r["name"], mk
    gone = sorted(c for c in seen if c not in cur)
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "holdout_delisted.csv"), "w", encoding="utf-8") as fh:
        fh.write("code,name,market,first_seen,last_seen\n")
        for c in gone:
            s = seen[c]
            fh.write("%s,%s,%s,%s,%s\n" % (c, s["name"].replace(",", " "), s["market"], s["first"], s["last"]))
    print("::notice title=舊資料母體::%d 個月快照；出現過 %d 檔，現在不在市場上 %d 檔（上市 %d、上櫃 %d）" % (
        nd, len(seen), len(gone), sum(1 for c in gone if seen[c]["market"] == "TWSE"),
        sum(1 for c in gone if seen[c]["market"] == "TPEX")))


if __name__ == "__main__":
    main()
