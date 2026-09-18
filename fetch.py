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
