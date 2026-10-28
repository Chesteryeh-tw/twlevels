#!/usr/bin/env python3
"""財報（官方最新一季綜合損益表，政府資料開放授權，可公開）→ data/fin.json（2026-10-28，使用者同意建議 1）。

來源（6 種產業格式各一支 API；上市證交所 OpenAPI、上櫃櫃買 OpenAPI）：
  https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{ci,basi,bd,fh,ins,mim}
  https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_{ci,basi,bd,fh,ins,mim}
  ci＝一般業、basi＝銀行、bd＝證券、fh＝金控、ins＝保險、mim＝異業。
只有「最新一季」：數字是今年累計（第 2 季＝1～6 月）。每天跑一次，換季時把新的一季加進每檔的清單，
所以單季數字（本季累計 − 上季累計）、去年同期比較要累積一段時間才會有。

data/fin.json：{"updated": 日期, "q": {代號: [[年, 季, 營收千元, 毛利千元, 營業利益千元, 淨利(母公司)千元, EPS(累計)], ...]}}
"""
import json
import os
import sys
import time

import twse as T

OUT = os.path.join("data", "fin.json")
KINDS = ["ci", "basi", "bd", "fh", "ins", "mim"]
URLS = (["https://openapi.twse.com.tw/v1/opendata/t187ap06_L_%s" % k for k in KINDS] +
        ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_%s" % k for k in KINDS])


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def first(r, *keys):
    for k in keys:
        if k in r and num(r[k]) is not None:
            return num(r[k])
    return None


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("q", {})
        except Exception:
            old = {}
    got, fails = {}, []
    for u in URLS:
        j = T.get_json(u)
        time.sleep(1.0)
        if not isinstance(j, list):
            fails.append(u.rsplit("/", 1)[-1])
            continue
        for r in j:
            code = str(r.get("SecuritiesCompanyCode") or r.get("公司代號") or "").strip()
            y, q = num(r.get("Year") or r.get("年度")), num(r.get("Season") or r.get("季別"))
            if not code or y is None or q is None:
                continue
            rev = first(r, "營業收入", "收益", "淨收益", "收入", "利息淨收益")
            gp = first(r, "營業毛利（毛損）淨額", "營業毛利（毛損）")
            op = first(r, "營業利益（損失）", "營業利益")
            ni = first(r, "淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主", "本期淨利（淨損）", "本期稅後淨利（淨損）")
            eps = first(r, "基本每股盈餘（元）", "基本每股盈餘")
            got[code] = [int(y) + 1911, int(q), rev, gp, op, ni, eps]
    if len(got) < 1000:
        print("::error title=財報::只拿到 %d 檔（失敗：%s），不覆寫" % (len(got), "、".join(fails)))
        sys.exit(0)
    q = dict(old)
    added = 0
    for code, row in got.items():
        lst = [x for x in q.get(code, []) if not (x[0] == row[0] and x[1] == row[1])]
        if len(lst) == len(q.get(code, [])):
            added += 1
        lst.append(row)
        lst.sort(key=lambda x: (x[0], x[1]))
        q[code] = lst[-12:]
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"updated": time.strftime("%Y-%m-%d"), "q": q}, fh, ensure_ascii=False, separators=(",", ":"))
    yq = sorted({(r[0], r[1]) for r in got.values()})
    print("::notice title=財報::%d 檔、季別 %s；新增（或換季）%d 檔%s" % (
        len(got), "、".join("%dQ%d" % x for x in yq[-3:]), added, ("；失敗：" + "、".join(fails)) if fails else ""))


if __name__ == "__main__":
    main()
