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
    blank_days = []       # 兩邊都沒資料的日期 → 放假，之後記成非交易日
    partial = 0           # 只有一邊有資料 → 對方在維護，不能當成放假
    for back in range(12):
        day = start - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        print("  嘗試 %s ..." % day.strftime("%Y-%m-%d"))
        tw, tp = T.fetch_day_split(day)
        if tw and tp:
            print("    上市 %d 檔、上櫃 %d 檔" % (len(tw), len(tp)))
            picked = (day, tw + tp)
            break
        if tw or tp:
            # 有一邊活著就代表這天有開盤，只是另一邊拿不到（例如櫃買維護）。
            # 這種日子絕對不能記成非交易日，否則行事曆會被寫壞。
            print("    只有%s有資料（上市 %d／上櫃 %d）—— 這天有開盤，"
                  "但資料不完整，不採用也不記成放假"
                  % ("上市" if tw else "上櫃", len(tw), len(tp)))
            partial += 1
            continue
        print("    非交易日或尚未出檔")
        blank_days.append(day)

    if not picked:
        if partial:
            print("錯誤：有交易日但資料不完整（很可能是證交所或櫃買正在維護）。"
                  "這次不動任何檔案，等下一次排程再試。")
        else:
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

    # 把抓不到資料的日子記成非交易日（放假、颱風假），下次就不用再試。
    # 只記「今天以前」的：今天可能只是證交所還沒出檔，不能當成放假。
    today = now.date()
    learned = 0
    for d in blank_days:
        if d.date() >= today:
            continue
        k = d.strftime("%Y%m%d")
        if k not in days:
            days[k] = 0
            learned += 1
    if learned:
        print("  新學到 %d 個非交易日（放假或颱風假）" % learned)
    T.write_days(days)

    # tradingDays 只算真正有開盤的日子；記成 0 的是非交易日，不能算進去
    n_trading = sum(1 for v in days.values() if isinstance(v, list))
    n_holiday = sum(1 for v in days.values() if v == 0)
    meta = T.write_meta(day, rows, now,
                        extra={"tradingDays": n_trading, "nonTradingDays": n_holiday})
    h = meta["history"]
    print("完成：資料日 %s，%d 檔；歷史涵蓋 %s ~ %s（%d 天）；"
          "已知交易日 %d、非交易日 %d"
          % (meta["date"], meta["count"], h["first"], h["last"], h["days"],
             n_trading, n_holiday))


if __name__ == "__main__":
    main()
