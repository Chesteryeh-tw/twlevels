#!/usr/bin/env python3
"""今天的「強勢＋營收創 12 個月新高」名單 → data/watch.json（2026-10-29，事先登記前瞻 A，標探索、未驗證）。

強勢＝上通斜率 ≥ 3%（上軌外開；含貼上軌強勢走）或 當天漲 ≥ 9.5%（一價到底的不算）；成交金額 ≥ 1 億。
營收旗標讀 data/rev_flags.json（每月 11 日更新）。update.yml 每天跑。
"""
import json
import os

import twse as T


def last_two(code):
    p = os.path.join("data", "stock", code + ".txt")
    if not os.path.exists(p):
        return None
    rows = [l.strip().split(",") for l in open(p, encoding="utf-8") if l[:1].isdigit()]
    return rows[-2:] if len(rows) >= 2 else None


def main():
    if not os.path.exists("data/rev_flags.json"):
        print("::warning title=名單::沒有 data/rev_flags.json，跳過")
        return
    rf = json.load(open("data/rev_flags.json", encoding="utf-8"))
    nh = set(rf.get("new_high", []))
    lines = open(os.path.join("data", "daily.txt"), encoding="utf-8").read().splitlines()
    head = lines[0].split("|")
    day = head[1]
    cols = head[-1].split(",")
    iu = cols.index("slopeUp")
    names = {}
    if os.path.exists(T.MARKET):
        for l in open(T.MARKET, encoding="utf-8").read().splitlines()[1:]:
            p = l.split(",")
            if len(p) > 1:
                names[p[0]] = p[1]
    out = []
    for l in lines[1:]:
        p = l.split(",")
        code = p[0]
        if code not in nh:
            continue
        r = last_two(code)
        if not r or r[-1][0] != day:
            continue
        (d0, o0, h0, l0, c0, v0, w0), (d1, o1, h1, l1, c1, v1, w1) = [x[:7] for x in r]
        c0, c1, h1, l1 = float(c0), float(c1), float(h1), float(l1)
        val = float(w1 or 0) / 1e4          # 萬元 → 億
        if val < 1 or c0 <= 0:
            continue
        chg = (c1 / c0 - 1) * 100
        su = float(p[iu]) if p[iu] not in ("", "None") else None
        why = []
        if su is not None and su >= 3:
            why.append("上軌外開")
        if chg >= 9.5 and h1 > l1:
            why.append("漲 ≥ 9.5%")
        if why:
            out.append({"code": code, "name": names.get(code, ""), "close": c1, "chg": round(chg, 2),
                        "val": round(val, 1), "why": why})
    out.sort(key=lambda x: -x["chg"])
    with open(os.path.join("data", "watch.json"), "w", encoding="utf-8") as fh:
        json.dump({"date": day, "rev_usable_from": rf.get("usable_from"), "list": out}, fh,
                  ensure_ascii=False, separators=(",", ":"))
    print("::notice title=強勢＋營收創新高::%s 共 %d 檔" % (day, len(out)))


if __name__ == "__main__":
    main()
