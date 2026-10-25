#!/usr/bin/env python3
"""corporate_actions.py 離線測試：用官方單日樣本（testdata/corp/，證交所 GPT 10-24 實抓、櫃買 Claude 10-25 實抓）。"""
import gzip, hashlib, json, os, sys, tempfile, urllib.error, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corporate_actions as CA
HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []
def check(n, ok, info=""):
    print(("通過  " if ok else "失敗  ") + n + ("" if ok else "  →  %s" % (info,)))
    if not ok: FAILS.append(n)

check("C1 民國日期三種寫法", CA.roc_date("115年09月02日") == "20260902" and CA.roc_date("114/10/20") == "20251020"
      and CA.roc_date("1141111") == "20251111" and CA.roc_date("abc") is None and CA.roc_date("115/02/30") is None)
S = {"TWT49U": "20260902", "TWTAUU": "20251020", "TWTB8U": "20260119", "TWTCAU": "20251126",
     "exDailyQ": "20260814", "revivt": "20251111", "pvChgRslt": "20260309"}
for src, d in S.items():
    body = open(os.path.join(HERE, "testdata", "corp", src + ".json"), "rb").read()
    f, rows = CA.parse(src, body, d, d)
    spec = next(s for s in CA.SOURCES if s[0] == src)
    check("C2 %s 樣本：欄位有代號與日期欄、列日期都是 %s" % (src, d), spec[4] in f and spec[5] in f and rows and
          all(CA.roc_date(dict(zip(f, r))[spec[5]]) == d for r in rows), (f[:3], len(rows)))
body = open(os.path.join(HERE, "testdata", "corp", "TWTAUU.json"), "rb").read()
try:
    CA.parse("TWTAUU", body, "20251001", "20251031"); check("C3 回應區間對不上 → Bad", False)
except CA.Bad:
    check("C3 回應區間對不上 → Bad", True)
try:
    CA.parse("TWTAUU", b"<html>404</html>", "1", "1"); check("C4 回網頁 → Stop", False)
except CA.Stop:
    check("C4 回網頁 → Stop", True)
check("C5 證交所查無資料 → 空表，不是錯", CA.parse("TWTAUU", json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode(), "1", "1") == ([], []))
# C6 build：把樣本包成某月份的回應（改日期區間），檢查類型與純除息
tmp = tempfile.mkdtemp(); os.chdir(tmp)
man = {}
def put(src, ym, body):
    p = os.path.join(CA.RAW, src, ym + ".json.gz"); os.makedirs(os.path.dirname(p), exist_ok=True)
    gzip.open(p, "wb").write(body); man["%s_%s" % (src, ym)] = dict(ok=True, sha256=hashlib.sha256(body).hexdigest())
j = json.load(open(os.path.join(HERE, "testdata", "corp", "TWT49U.json"), encoding="utf-8"))
j["strDate"], j["endDate"] = "20260901", "20260930"; put("TWT49U", "202609", json.dumps(j, ensure_ascii=False).encode())
j = json.load(open(os.path.join(HERE, "testdata", "corp", "pvChgRslt.json"), encoding="utf-8"))
j["date"] = "20260301~20260331"; put("pvChgRslt", "202603", json.dumps(j, ensure_ascii=False).encode())
CA.save_man(man)
rows, bad = CA.build()
w = [r for r in rows if r["code"] == "6669"]
check("C6a 緯穎 6669 2026-09-02 → 除權（非純除息）", w and w[0]["type"] == "ex_right" and w[0]["cash_only"] == 0 and w[0]["effective_date"] == "20260902", w)
check("C6b 有純除息列 cash_only=1", any(r["type"] == "ex_dividend" and r["cash_only"] == 1 for r in rows))
z = [r for r in rows if r["code"] == "8932"]
check("C6c 櫃買 8932 面額變更、前收 199.50、參考價 99.75", z and z[0]["type"] == "par_change" and z[0]["prev_close"] == "199.50" and z[0]["ref_price"] == "99.75", z)
check("C6d 沒有問題列", not bad, bad)
# C7 雜湊對不上 → 不收
p = os.path.join(CA.RAW, "pvChgRslt", "202603.json.gz"); gzip.open(p, "wb").write(b"{}")
rows2, bad2 = CA.build()
check("C7 原始檔被改 → 不收、列入問題", not any(r["code"] == "8932" for r in rows2) and bad2)
# C8 轉址／403 → Stop
class R:
    def open(self, *a, **k): raise urllib.error.HTTPError("u", 307, "x", {}, io.BytesIO(b""))
CA.OPENER = R()
try:
    CA.get("http://x"); check("C8 轉址 → Stop", False)
except CA.Stop:
    check("C8 轉址 → Stop", True)
# C9 截短列 → Bad；totalCount 不符 → Bad
j = json.load(open(os.path.join(HERE, "testdata", "corp", "revivt.json"), encoding="utf-8"))
j["tables"][0]["data"][0] = j["tables"][0]["data"][0][:5]
try:
    CA.parse("revivt", json.dumps(j, ensure_ascii=False).encode(), "20251111", "20251111"); check("C9 截短列 → Bad", False)
except CA.Bad:
    check("C9 截短列 → Bad", True)
j = json.load(open(os.path.join(HERE, "testdata", "corp", "revivt.json"), encoding="utf-8"))
j["tables"][0]["totalCount"] = 5
try:
    CA.parse("revivt", json.dumps(j, ensure_ascii=False).encode(), "20251111", "20251111"); check("C10 totalCount 不符 → Bad", False)
except CA.Bad:
    check("C10 totalCount 不符 → Bad", True)
# C11 未知 權/息 → bad、不覆寫正式表
tmp2 = tempfile.mkdtemp(); os.chdir(tmp2); man = {}
j = json.load(open(os.path.join(HERE, "testdata", "corp", "TWT49U.json"), encoding="utf-8"))
j["strDate"], j["endDate"] = "20260901", "20260930"
fi = j["fields"].index("權/息"); j["data"][0][fi] = "怪"
put("TWT49U", "202609", json.dumps(j, ensure_ascii=False).encode()); CA.save_man(man)
open(CA.OUT, "w").write("old\n")
_r, b3 = CA.build()
check("C11 未知權息值 → 列為問題、不覆寫正式表", b3 and open(CA.OUT).read() == "old\n", b3[:2])
# C12 涵蓋：七個來源都驗過的月份才算
tmp3 = tempfile.mkdtemp(); os.chdir(tmp3); man = {}
empty_twse = json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode()
for src in ("TWT49U", "TWTAUU", "TWTB8U", "TWTCAU"):
    put(src, "202101", empty_twse)
for src in ("exDailyQ", "revivt", "pvChgRslt"):
    put(src, "202101", json.dumps({"stat": "ok", "date": "20210101~20210131", "tables": [{"fields": ["a"], "data": [], "totalCount": 0}]}).encode())
for src in ("TWT49U", "TWTAUU", "TWTB8U", "TWTCAU", "exDailyQ", "revivt"):
    put(src, "202102", empty_twse if src.startswith("TW") else json.dumps({"stat": "ok", "date": "20210201~20210228", "tables": [{"fields": ["a"], "data": [], "totalCount": 0}]}).encode())
CA.save_man(man)
CA.build()
cov = json.load(open(CA.COVERAGE, encoding="utf-8"))
check("C12 七個來源都齊的月份才算涵蓋（202102 缺 pvChgRslt）", "202101" in cov["months"] and "202102" not in cov["months"] and
      "pvChgRslt_202102" in cov["missing"], cov["months"][:3])
# C13 帳本 ok 但原始檔不見 → 算缺，不算涵蓋
os.remove(os.path.join(CA.RAW, "TWTAUU", "202101.json.gz"))
CA.build()
cov = json.load(open(CA.COVERAGE, encoding="utf-8"))
check("C13 帳本說 ok 但原始檔不見 → 缺、不涵蓋", "202101" not in cov["months"] and "TWTAUU_202101" in cov["missing"])

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
