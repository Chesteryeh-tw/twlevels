#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外資及陸資持股比率（上市）。

來源：證交所 MI_QFIIS（外資及陸資投資持股統計），每日盤後公布
產出：data/qfii/<日期>.txt
      code,ratio,held
      ratio = 全體外資及陸資持股比率（%）
      held  = 全體外資及陸資持有股數 ÷ 1000（張，取整）

這是外資的「庫存」（官方持股），不是每日買賣超的累加。
上櫃的官方來源還沒確認（交給 GPT 查），確認前只有上市。

用法：
  python qfii.py        抓 data/meta.json 記錄的最新交易日（已存在就略過）
  python qfii.py 20     往回補最近 20 個交易日（依 data/days.json）
"""
import os, sys, time, json

import twse as T

DIR = os.path.join(T.DATA, "qfii")


def parse(j):
    """MI_QFIIS 的 JSON → [(代號, 比率, 張)]。"""
    out = []
    if not j or j.get("stat") != "OK":
        return out
    f = [T.clean(x) for x in j.get("fields", [])]
    iC = T.idx(f, "證券代號")
    iR = T.idx(f, "全體外資及陸資持股比率")
    iH = T.idx(f, "全體外資及陸資持有股數")
    if min(iC, iR, iH) < 0:
        return out
    for r in j.get("data", []):
        code = T.clean(r[iC])
        if len(code) != 4:
            continue
        ratio, held = T.pn(r[iR]), T.pn(r[iH])
        if ratio is None:
            continue
        out.append((code, ratio, None if held is None else round(held / 1000)))
    return out


def fetch_day(d):
    url = ("https://www.twse.com.tw/fund/MI_QFIIS"
           "?response=json&date=%s&selectType=ALLBUT0999" % d)
    return parse(T.get_json(url))


def save(d, rows):
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, d + ".txt"), "w", encoding="utf-8") as fh:
        fh.write("code,ratio,held\n")
        for c, r, h in rows:
            fh.write("%s,%s,%s\n" % (c, T.fmt(r), "" if h is None else h))


def trading_days():
    try:
        with open(T.DAYS, encoding="utf-8") as fh:
            days = json.load(fh)
        return sorted(k for k, v in days.items() if isinstance(v, list))
    except Exception:
        return []


def main(n=1):
    if n == 1:
        try:
            with open(T.META, encoding="utf-8") as fh:
                todo = [json.load(fh)["date"]]
        except Exception:
            print("讀不到 meta.json，先跑 fetch.py"); return
    else:
        todo = trading_days()[-n:]
    done = 0
    for d in todo:
        if os.path.exists(os.path.join(DIR, d + ".txt")):
            continue
        rows = fetch_day(d)
        if len(rows) < 500:          # 太少代表還沒公布或抓失敗，不寫半套檔
            print("  %s：只有 %d 檔，略過（可能還沒公布）" % (d, len(rows)))
        else:
            save(d, rows); done += 1
            print("  %s：%d 檔" % (d, len(rows)))
        time.sleep(1.5)
    print("qfii：新增 %d 天" % done)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
