#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加權指數（TAIEX）每日開高低收。

來源：證交所「發行量加權股價指數歷史資料」MI_5MINS_HIST（一次回傳一整個月）
產出：data/index_taiex.txt
      # date,open,high,low,close
      20261008,49783.06,49783.06,49189.77,49313.44

用途：主站頂端顯示大盤收盤、漲跌、振幅；當沖手冊「加權與櫃買怎麼配」的環境判斷。
櫃買指數的官方歷史端點還沒確認（交給 GPT 查），確認前主站用「上櫃等權平均漲跌」代替。

用法：
  python marketidx.py          抓本月（月初幾天會連上個月一起抓）
  python marketidx.py 14       往回補 14 個月（第一次建立歷史用）
"""
import os, sys, time
from datetime import datetime, timedelta

import twse as T

OUT = os.path.join(T.DATA, "index_taiex.txt")


def roc_to_ymd(s):
    """'115/10/08' → '20261008'。格式不對回 None。"""
    try:
        y, m, d = s.strip().split("/")
        return "%04d%02d%02d" % (int(y) + 1911, int(m), int(d))
    except Exception:
        return None


def parse_month(j):
    """MI_5MINS_HIST 的 JSON → {日期: (開, 高, 低, 收)}。"""
    out = {}
    if not j or j.get("stat") != "OK":
        return out
    f = [T.clean(x) for x in j.get("fields", [])]
    iD, iO, iH, iL, iC = (T.idx(f, "日期"), T.idx(f, "開盤指數"), T.idx(f, "最高指數"),
                          T.idx(f, "最低指數"), T.idx(f, "收盤指數"))
    if min(iD, iO, iH, iL, iC) < 0:
        return out
    for r in j.get("data", []):
        d = roc_to_ymd(T.clean(r[iD]))
        vals = [T.pn(r[i]) for i in (iO, iH, iL, iC)]
        if d and None not in vals and vals[3] > 0:
            out[d] = tuple(vals)
    return out


def read():
    m = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                p = line.strip().split(",")
                if len(p) == 5:
                    m[p[0]] = line.strip()
    return m


def write(m):
    os.makedirs(T.DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("# date,open,high,low,close\n")
        for d in sorted(m):
            fh.write(m[d] + "\n")


def main(months=1):
    now = datetime.now(T.TPE)
    m = read()
    before = len(m)
    if months == 1 and len(m) < 200:       # 第一次跑：自動往回補 14 個月，跟個股歷史一樣長
        months = 14
        print("  歷史只有 %d 天，這次往回補 14 個月" % len(m))
    first = now.replace(day=1)
    targets = []
    for k in range(months):
        y, mo = first.year, first.month - k
        while mo <= 0:
            mo += 12; y -= 1
        targets.append(datetime(y, mo, 1))
    if months == 1 and now.day <= 5:       # 月初：上個月最後幾天可能還沒寫進來
        prev = first - timedelta(days=1)
        targets.append(prev.replace(day=1))
    for t in targets:
        url = ("https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
               "?response=json&date=%s" % t.strftime("%Y%m01"))
        got = parse_month(T.get_json(url))
        for d, (o, h, l, c) in got.items():
            m[d] = ",".join([d, T.fmt(o), T.fmt(h), T.fmt(l), T.fmt(c)])
        print("  加權指數 %s：%d 天" % (t.strftime("%Y-%m"), len(got)))
        time.sleep(1.5)
    write(m)
    print("index_taiex：共 %d 天（新增 %d）" % (len(m), len(m) - before))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
