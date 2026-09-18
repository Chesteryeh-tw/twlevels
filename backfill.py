#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
歷史回補：把過去一段時間的每日行情補進 data/stock/<代號>.txt

用法（由 GitHub Actions 傳參數）
    python backfill.py <起始天數前> <結束天數前>
    例：python backfill.py 400 0     → 補最近 400 個日曆天
        python backfill.py 800 400   → 再往前補一段

特性
  * 已經有的日期會自動跳過，重跑不會重複抓
  * 中途某一天失敗不會中斷，繼續跑下一天
  * 全部讀進記憶體、最後一次寫出，避免上萬次小寫入
"""

import os
import sys
import time
from datetime import datetime, timedelta

import twse as T

PAUSE = 1.1          # 每次請求之間的間隔（秒），對證交所客氣一點
REPORT_EVERY = 10
FLUSH_EVERY = 40     # 每抓幾天就存檔一次，中途中斷也不會全白費


def flush(cache, days):
    T.write_days(days)
    os.makedirs(T.STOCK_DIR, exist_ok=True)
    for code, series in cache.items():
        if series:
            T.write_stock(code, series)


def main():
    days_from = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    days_to = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if days_from <= days_to:
        print("參數錯誤：起始天數要大於結束天數。")
        sys.exit(1)

    now = datetime.now(T.TPE)
    print("回補範圍：%d 天前 ~ %d 天前（台北時間 %s）"
          % (days_from, days_to, now.strftime("%Y-%m-%d %H:%M")))

    # 先把現有歷史全部讀進記憶體
    cache = {}
    if os.path.isdir(T.STOCK_DIR):
        for fn in os.listdir(T.STOCK_DIR):
            if fn.endswith(".txt"):
                cache[fn[:-4]] = T.read_stock(fn[:-4])
    have = sum(len(v) for v in cache.values())
    print("現有歷史：%d 檔、共 %d 筆" % (len(cache), have))

    # 已完整抓過的日期：上市、上櫃都要有資料才算數
    days = T.read_days()
    known_days = set(d for d, c in days.items()
                     if isinstance(c, list) and len(c) == 2 and c[0] > 0 and c[1] > 0)
    nontrading = set(d for d, c in days.items() if c == 0)
    print("已完整涵蓋 %d 個交易日、已知非交易日 %d 天"
          % (len(known_days), len(nontrading)))
    known_days |= nontrading

    base = now.date()
    todo = []
    for back in range(days_to, days_from + 1):
        d = base - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        if d.strftime("%Y%m%d") in known_days:
            continue
        todo.append(d)
    todo.sort()
    print("待抓 %d 個日期\n" % len(todo))

    if not todo:
        print("沒有需要回補的日期。")
        return

    ok = miss = fail = 0
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        day = datetime(d.year, d.month, d.day, tzinfo=T.TPE)
        try:
            rows = T.fetch_day(day, pause=PAUSE)
        except Exception as e:
            print("  %s 發生例外：%s" % (d, e))
            fail += 1
            continue

        dstr = day.strftime("%Y%m%d")
        if not rows:
            miss += 1
            days[dstr] = 0                      # 記成非交易日，下次不再試
        else:
            for r in rows:
                cache.setdefault(r["code"], {})[dstr] = T.stock_line(day, r)
            days[dstr] = T.day_counts(rows)
            ok += 1

        if i % FLUSH_EVERY == 0:
            flush(cache, days)
            print("  （已存檔，中途失敗也不會白跑）")

        if i % REPORT_EVERY == 0 or i == len(todo):
            el = time.time() - t0
            rate = el / i
            print("  進度 %d/%d　交易日 %d、非交易日 %d、失敗 %d　"
                  "已用 %.0f 分、預估剩 %.0f 分"
                  % (i, len(todo), ok, miss, fail,
                     el / 60, (len(todo) - i) * rate / 60))

    print("\n寫出歷史檔 ...")
    T.write_days(days)
    os.makedirs(T.STOCK_DIR, exist_ok=True)
    written = 0
    for code, series in cache.items():
        if series:
            T.write_stock(code, series)
            written += 1
    total = sum(len(v) for v in cache.values())
    print("完成：%d 檔、共 %d 筆（本次新增 %d 筆）"
          % (written, total, total - have))

    # 更新 meta 裡的歷史涵蓋範圍（不動 market.txt）
    if os.path.exists(T.META):
        import json
        meta = json.load(open(T.META, encoding="utf-8"))
        alld = set()
        for v in cache.values():
            alld.update(v.keys())
        if alld:
            ks = sorted(alld)
            meta["history"] = {"first": ks[0], "last": ks[-1], "days": len(ks)}
            json.dump(meta, open(T.META, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            print("meta.json 已更新：歷史 %s ~ %s（%d 天）"
                  % (ks[0], ks[-1], len(ks)))


if __name__ == "__main__":
    main()
