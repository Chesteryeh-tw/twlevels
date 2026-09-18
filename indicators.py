#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把需要「歷史」才算得出來的指標，在伺服器端一次算好。

讀  data/stock/<代號>.txt （每檔的逐日 OHLCV）
寫  data/daily.txt        （每檔一行，最新一個交易日的衍生指標）

為什麼要這樣做：均線、布林、連漲天數這些都要回看幾十到兩百多天。
若讓瀏覽器自己算，等於要下載一千多個檔案，手機一定卡死。
在這裡算好，前端只要多讀一個幾十 KB 的檔案。

欄位定義（全部照權證小哥教材）
  ma5/ma20/ma60/ma240  移動平均（收盤）
  slope20  月線斜率 = MA20(今)/MA20(昨) − 1，百分比
           > 1 超級強勢；< −1 超級弱勢
  bbUp/bbLo  布林上下軌 = MA20 ± 2 倍標準差（母體）
  bbPos      位階：上軌 = 10、中軌 = 0、下軌 = −10（線性，不夾限）
  bbW        布林帶寬 = 上軌/下軌 − 1，百分比
             < 5 不適合當沖；< 10 有效壓縮；> 20 適合當沖
  slopeUp    上通斜率 = 上軌(今)/上軌(昨) − 1，百分比　> 3 紅燈
  slopeLo    下通斜率 = 下軌(今)/下軌(昨) − 1，百分比　< −3 綠燈
  biasY      乖離年線 = 收盤/MA240 − 1，百分比　> 30 高檔出貨股
  runUp/runDn  連漲／連跌天數（以收盤價比較）
  vr20       均量比 = 今日量 / 前 20 日均量（不含今日）　> 3 且有隔日沖 → 黑K機率高
  vrY        昨量比 = 今日量 / 昨日量
  ndays      這檔累積了幾天歷史（前端用來把資料不足的欄位標灰）
"""

import os
from datetime import datetime

import twse as T

OUT = os.path.join(T.DATA, "daily.txt")

COLS = ("code,ma5,ma20,ma60,ma240,slope20,bbUp,bbLo,bbPos,bbW,"
        "slopeUp,slopeLo,biasY,runUp,runDn,vr20,vrY,ndays")


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def stdev_p(xs):
    """母體標準差。布林通道慣例用母體，不是樣本。"""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def ma(xs, n):
    """xs 由舊到新；取最後 n 筆的平均。不足就回 None。"""
    return mean(xs[-n:]) if len(xs) >= n else None


def boll(closes):
    """回傳 (中軌, 上軌, 下軌)；不足 20 天回 (None, None, None)。"""
    if len(closes) < 20:
        return None, None, None
    win = closes[-20:]
    mid = mean(win)
    sd = stdev_p(win)
    if sd is None:
        return None, None, None
    return mid, mid + 2 * sd, mid - 2 * sd


def pct_change(now, prev):
    if now is None or prev is None or prev == 0:
        return None
    return (now / prev - 1) * 100


def streaks(closes):
    """回傳 (連漲天數, 連跌天數)。兩者不會同時 > 0。"""
    up = dn = 0
    i = len(closes) - 1
    while i >= 1 and closes[i] > closes[i - 1]:
        up += 1
        i -= 1
    if up:
        return up, 0
    i = len(closes) - 1
    while i >= 1 and closes[i] < closes[i - 1]:
        dn += 1
        i -= 1
    return 0, dn


def f(x, nd=2):
    if x is None:
        return ""
    try:
        if x != x or x in (float("inf"), float("-inf")):
            return ""
    except Exception:
        return ""
    return ("%." + str(nd) + "f") % x


def parse_series(series):
    """series: {日期: 行文字} → (日期list, 收盤list, 量list)，皆由舊到新。"""
    dates, closes, vols = [], [], []
    for d in sorted(series.keys()):
        p = series[d].split(",")
        if len(p) < 6:
            continue
        try:
            c = float(p[4])
            v = float(p[5]) if p[5] != "" else 0.0
        except ValueError:
            continue
        if c <= 0:
            continue
        dates.append(d)
        closes.append(c)
        vols.append(v)
    return dates, closes, vols


def compute(closes, vols):
    """回傳一個 dict；資料不足的欄位是 None。"""
    o = {"ndays": len(closes)}
    c = closes[-1]

    o["ma5"] = ma(closes, 5)
    o["ma20"] = ma(closes, 20)
    o["ma60"] = ma(closes, 60)
    o["ma240"] = ma(closes, 240)

    # 月線斜率：要昨天的 MA20，所以至少 21 天
    o["slope20"] = pct_change(o["ma20"], ma(closes[:-1], 20)) if len(closes) >= 21 else None

    mid, up, lo = boll(closes)
    o["bbUp"], o["bbLo"] = up, lo
    if up is not None and lo is not None:
        o["bbW"] = (up / lo - 1) * 100 if lo > 0 else None
        o["bbPos"] = (c - mid) / (up - mid) * 10 if up > mid else 0.0
    else:
        o["bbW"] = o["bbPos"] = None

    if len(closes) >= 21:
        _, up0, lo0 = boll(closes[:-1])
        o["slopeUp"] = pct_change(up, up0)
        o["slopeLo"] = pct_change(lo, lo0)
    else:
        o["slopeUp"] = o["slopeLo"] = None

    o["biasY"] = pct_change(c, o["ma240"]) if o["ma240"] else None

    o["runUp"], o["runDn"] = streaks(closes)

    # 均量比：今日量 ÷ 前 20 日均量（刻意不含今日，讓「3 倍量」就是 3）
    if len(vols) >= 21:
        base = mean(vols[-21:-1])
        o["vr20"] = (vols[-1] / base) if base and base > 0 else None
    else:
        o["vr20"] = None
    o["vrY"] = (vols[-1] / vols[-2]) if len(vols) >= 2 and vols[-2] > 0 else None

    return o


def line_for(code, o):
    return ",".join([
        code,
        f(o["ma5"]), f(o["ma20"]), f(o["ma60"]), f(o["ma240"]),
        f(o["slope20"]),
        f(o["bbUp"]), f(o["bbLo"]), f(o["bbPos"], 1), f(o["bbW"], 1),
        f(o["slopeUp"]), f(o["slopeLo"]), f(o["biasY"], 1),
        str(o["runUp"]), str(o["runDn"]),
        f(o["vr20"]), f(o["vrY"]),
        str(o["ndays"]),
    ])


def main():
    now = datetime.now(T.TPE)
    if not os.path.isdir(T.STOCK_DIR):
        print("找不到 %s，先跑過 fetch.py 或 backfill.py。" % T.STOCK_DIR)
        return

    files = sorted(fn for fn in os.listdir(T.STOCK_DIR) if fn.endswith(".txt"))
    print("要處理 %d 檔" % len(files))

    out, newest, skipped = [], "", 0
    for fn in files:
        code = fn[:-4]
        dates, closes, vols = parse_series(T.read_stock(code))
        if not closes:
            skipped += 1
            continue
        if dates[-1] > newest:
            newest = dates[-1]
        out.append(line_for(code, compute(closes, vols)))

    os.makedirs(T.DATA, exist_ok=True)
    head = "TWLVI1|%s|%s|%d|%s" % (newest, now.strftime("%Y%m%d%H%M"), len(out), COLS)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(head + "\n" + "\n".join(out) + "\n")

    size = os.path.getsize(OUT)
    print("已寫入 %s：%d 檔、%d KB（最新資料日 %s，略過 %d 檔）"
          % (OUT, len(out), size // 1024, newest, skipped))


if __name__ == "__main__":
    main()
