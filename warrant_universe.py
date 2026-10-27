#!/usr/bin/env python3
"""權證每日母體清單（給 GPT 排權證分點抓取用，2026-10-27）。不連網，只讀 Actions 快取裡已抓的證交所資料。

輸入：data/warrant_raw/derived/<日期>.csv（warrant_fetch.py 從 T86 權證類別＋MI_INDEX 權證行情整理；上市權證）
輸出（私人，只放 Actions 下載區，不進 repo）：
  warrant_universe_daily.csv.gz  每個交易日 × 每檔「當天有成交」的權證：日期、權證代號、名稱、認購／認售、標的、
                                 標的對應方式、成交股數、成交金額、收盤價（含之後到期的權證）
  warrant_universe_summary.csv   每檔權證一列：第一個／最後一個有成交日、有成交天數、總成交金額
  warrant_universe_readme.md     欄位說明、範圍、限制（沒有履約價、行使比例、到期日；只有上市）
用法：python warrant_universe.py 起日 迄日（YYYYMMDD）
"""
import csv
import glob
import gzip
import os
import sys

D = os.path.join("data", "warrant_raw", "derived")


def main():
    a, b = sys.argv[1], sys.argv[2]
    files = sorted(f for f in glob.glob(os.path.join(D, "*.csv")) if a <= os.path.basename(f)[:8] <= b)
    summ = {}
    n = 0
    with gzip.open("warrant_universe_daily.csv.gz", "wt", encoding="utf-8", newline="") as gz:
        w = csv.writer(gz)
        w.writerow(["date", "warrant", "name", "cp", "underlying", "map_method", "vol_sh", "amt_twd", "close"])
        for f in files:
            for r in csv.DictReader(open(f, encoding="utf-8")):
                if not r["vol_sh"] or float(r["vol_sh"] or 0) <= 0:
                    continue
                w.writerow([r["date"], r["warrant"], r["name"], r["cp"], r["underlying"], r["map_method"],
                            r["vol_sh"], r["amt_twd"], r["close"]])
                n += 1
                s = summ.setdefault(r["warrant"], [r["name"], r["cp"], r["underlying"], r["date"], r["date"], 0, 0.0])
                s[4] = r["date"]
                s[5] += 1
                s[6] += float(r["amt_twd"] or 0)
    with open("warrant_universe_summary.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["warrant", "name", "cp", "underlying", "first_trade_date", "last_trade_date", "trade_days", "amt_twd"])
        for k, s in sorted(summ.items()):
            w.writerow([k] + s[:6] + [round(s[6])])
    days = [os.path.basename(f)[:8] for f in files]
    with open("warrant_universe_readme.md", "w", encoding="utf-8") as fh:
        fh.write("# 權證每日母體（上市）\n\n- 範圍：%s～%s，%d 個交易日（只含證交所有資料的開市日）\n"
                 "- 每日有成交的權證日：%d 筆；權證檔數：%d（含期間內已到期的）\n"
                 "- 來源：證交所 T86（權證類別）＋MI_INDEX（權證行情），由 warrant_fetch.py 整理；標的對應四層（map_method）\n"
                 "- 沒有：履約價、行使比例、到期日、發行商（要另找官方權證基本資料）；上櫃權證不在內\n"
                 "- 用途：排權證分點抓取的查詢鍵（日期＋市場＋權證代號）；成交 0 的權證日不列\n"
                 % (days[0] if days else "", days[-1] if days else "", len(days), n, len(summ)))
    print("::notice title=權證母體清單::%s～%s，%d 天、%d 個權證日、%d 檔" % (
        days[0] if days else "", days[-1] if days else "", len(days), n, len(summ)))


if __name__ == "__main__":
    main()
