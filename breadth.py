#!/usr/bin/env python3
"""大盤寬度：每天全市場「站上月線」與「月線上升」的股票比例，給主站頂端的大盤狀態列用。

2026-10-26 加（網站設計紀錄 第 4 節①）。只用 data/stock/<代號>.txt 的官方收盤價。

  站上月線＝收盤 > 20 日均線
  月線上升＝今天的 20 日均線 > 昨天的 20 日均線（跟 indicators.py 的 slope20 > 0 同義）
  母體＝四碼、非 0 開頭的代號（排除 ETF 等），且當天有收盤、往前至少 21 個交易日

狀態標籤的門檻是 Claude 暫定、未驗證，放在主站，不放這裡。
用法：python breadth.py [data 目錄]   → data/breadth.json（最近 120 個交易日）
"""
import json, os, re, sys

KEEP = 120
CODE = re.compile(r"^[1-9]\d{3}$")


def main(base="data"):
    sdir = os.path.join(base, "stock")
    agg = {}                                   # 日期 → [站上月線, 月線上升, 檔數]
    for fn in os.listdir(sdir):
        code = fn[:-4]
        if not fn.endswith(".txt") or not CODE.match(code):
            continue
        ds, cs = [], []
        with open(os.path.join(sdir, fn), encoding="utf-8") as fh:
            for line in fh:
                if not line.strip() or line[0] == "#":
                    continue
                p = line.rstrip("\n").split(",")
                try:
                    c = float(p[4])
                except (IndexError, ValueError):
                    continue
                if c <= 0:
                    continue
                ds.append(p[0]); cs.append(c)
        if len(cs) < 21:
            continue
        start = max(20, len(cs) - KEEP - 5)
        ma_prev = sum(cs[start - 20:start]) / 20
        for i in range(start, len(cs)):
            ma = ma_prev + (cs[i] - cs[i - 20]) / 20
            a = agg.setdefault(ds[i], [0, 0, 0])
            a[2] += 1
            if cs[i] > ma:
                a[0] += 1
            if ma > ma_prev:
                a[1] += 1
            ma_prev = ma
    days = sorted(agg)[-KEEP:]
    # 檔數太少的日子（例如某檔資料多一天）不是全市場，丟掉
    if days:
        top = max(agg[d][2] for d in days)
        days = [d for d in days if agg[d][2] >= top * 0.8]
    out = {"note": "站上月線＝收盤>20日均線；月線上升＝20日均線比昨天高；母體＝四碼一般股",
           "dates": days,
           "above": [round(agg[d][0] / agg[d][2] * 100, 1) for d in days],
           "up": [round(agg[d][1] / agg[d][2] * 100, 1) for d in days],
           "n": [agg[d][2] for d in days]}
    with open(os.path.join(base, "breadth.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    if days:
        print("breadth：%d 天，最後 %s 站上月線 %.1f%% 月線上升 %.1f%%（%d 檔）"
              % (len(days), days[-1], out["above"][-1], out["up"][-1], out["n"][-1]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
