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
print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
