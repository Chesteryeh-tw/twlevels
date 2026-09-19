#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集保股權分散表：大戶持股比率、散戶持股比率、集保戶數。每週更新一次。

產出
  data/tdcc/<YYYYMMDD>.txt   一期一個檔：code,big,small,holders

定義（照權證小哥 12 大籌碼分析）
  大戶 = 持股 1,000 張以上          → 分級 15（1,000,001 股以上）
  散戶 = 持股 100 張以下            → 分級 1~9（1 ~ 100,000 股）
  集保戶數 = 分級 17（合計）的人數

來源 https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
     原始欄位：資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
     這支 API 只給「最新一期」，所以要每週跑、自己累積歷史。
"""

import os
import urllib.request

import twse as T

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
TDCC_DIR = os.path.join(T.DATA, "tdcc")

BIG_TIERS = {"15"}                                   # 千張以上
SMALL_TIERS = {str(i) for i in range(1, 10)}         # 百張以下
TOTAL_TIER = "17"

HEAD = "code,big,small,holders"


def fetch_text(url=URL, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": T.UA,
                "Accept": "text/csv, text/plain, */*",
            })
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8-sig", "replace")
        except Exception as e:
            if i == tries - 1:
                print("  集保取得失敗：%s" % e)
    return None


def parse(text):
    """回傳 (資料日期, {code: {big, small, holders}})。"""
    if not text:
        return None, {}
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return None, {}

    date = None
    agg = {}
    for line in lines[1:]:
        p = line.strip().split(",")
        if len(p) < 6:
            continue
        d, code, tier = p[0].strip(), p[1].strip(), p[2].strip()
        people, ratio = T.pn(p[3]), T.pn(p[5])
        if not code:
            continue
        if date is None and len(d) == 8:
            date = d
        a = agg.setdefault(code, {"big": None, "small": 0.0, "holders": None})
        if tier in BIG_TIERS and ratio is not None:
            a["big"] = (a["big"] or 0.0) + ratio
        elif tier in SMALL_TIERS and ratio is not None:
            a["small"] += ratio
        elif tier == TOTAL_TIER and people is not None:
            a["holders"] = people

    # 已下市或當期沒有庫存的代號，三個欄位都會是 0/None，留著只是雜訊
    out = {}
    for code, a in agg.items():
        if not a["big"] and not a["small"] and not a["holders"]:
            continue
        out[code] = {"big": a["big"], "small": a["small"],
                     "holders": a["holders"]}
    return date, out


def write(date, rows):
    os.makedirs(TDCC_DIR, exist_ok=True)
    path = os.path.join(TDCC_DIR, date + ".txt")
    lines = [HEAD]
    for code in sorted(rows):
        r = rows[code]
        lines.append("%s,%s,%s,%s" % (
            code,
            "" if r["big"] is None else "%.2f" % r["big"],
            "" if r["small"] is None else "%.2f" % r["small"],
            T.fmt(r["holders"]),
        ))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def main():
    print("抓集保股權分散表 ...")
    date, rows = parse(fetch_text())
    if not date or not rows:
        print("沒有拿到資料，這次跳過（下次排程會再試）。")
        return
    path = os.path.join(TDCC_DIR, date + ".txt")
    if os.path.exists(path):
        print("  %s 這一期已經有了，不重寫。" % date)
        return
    write(date, rows)
    n_big = sum(1 for r in rows.values() if r["big"] is not None)
    print("  已寫入 %s：%d 檔（其中 %d 檔有千張大戶）" % (path, len(rows), n_big))

    kept = sorted(fn for fn in os.listdir(TDCC_DIR) if fn.endswith(".txt"))
    print("  已累積 %d 期（%s ~ %s）" % (len(kept), kept[0][:-4], kept[-1][:-4]))


if __name__ == "__main__":
    main()
