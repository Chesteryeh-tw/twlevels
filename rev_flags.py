#!/usr/bin/env python3
"""月營收旗標（2026-10-29）：最近一期可用月營收「創 12 個月新高且年增 > 0」的股票代號 → data/rev_flags.json。

只放代號與年月（衍生旗標），不放營收金額；原始營收照舊只在 Actions 快取。
定義跟 backtest.load_revenue 同一套（前 11 個月要齊、當月 > 前 11 個月最大、年增 > 0）。
revenue.yml 每月 11 日跑完重建營收表後執行；看板「強勢＋營收創新高」名單用（事先登記前瞻 A）。
"""
import json
import os

import backtest as B


def main():
    rev = B.load_revenue()
    best = {}
    for code, lst in rev.items():
        if lst:
            cut, yoy, nh = lst[-1]
            best[code] = (cut, yoy, nh)
    if not best:
        print("::warning title=營收旗標::沒有營收表，不寫")
        return
    latest = max(c for c, _, _ in best.values())
    codes = sorted(k for k, (c, y, nh) in best.items() if c == latest and nh and y is not None and y > 0)
    ym_cut = latest            # 可用日（M+1 月 11 日）
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "rev_flags.json"), "w", encoding="utf-8") as fh:
        json.dump({"usable_from": ym_cut, "new_high": codes}, fh, ensure_ascii=False, separators=(",", ":"))
    print("::notice title=營收旗標::可用日 %s，營收創 12 個月新高 %d 檔" % (ym_cut, len(codes)))


if __name__ == "__main__":
    main()
