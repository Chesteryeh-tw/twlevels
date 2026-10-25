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

# ---- GPT 08 P2／P5 ----
import sus_study as SS
tmp4 = tempfile.mkdtemp(); os.chdir(tmp4); man = {}
j = json.load(open(os.path.join(HERE, "testdata", "corp", "TWT49U.json"), encoding="utf-8"))
j["strDate"], j["endDate"] = "20260901", "20260930"; put("TWT49U", "202609", json.dumps(j, ensure_ascii=False).encode())
man["TWT49U_202609"]["at"] = "2026-10-01T00:00:00Z"; CA.save_man(man)
CA.build()
a = SS.load_actions(CA.OUT, CA.COVERAGE)
cov = json.load(open(CA.COVERAGE, encoding="utf-8"))
check("P5a coverage 記事件表與 first_seen 雜湊，讀取端驗過可用", a["ok"] and cov["csv_sha256"] and cov["first_seen_sha256"], a.get("why"))
seen0 = json.load(open(CA.FIRST_SEEN, encoding="utf-8"))
ev = next(k for k in seen0 if "|6669|" in k)
check("P2a first_seen 繫結列內容雜湊", seen0[ev][0]["row_sha256"] and seen0[ev][0]["first_seen_at"] == "2026-10-01T00:00:00Z")
# 官方修訂同一事件內容 → 新版本另記時間，不沿用舊時間
fi = j["fields"].index("權/息") if "權/息" in j["fields"] else None
row = next(r for r in j["data"] if "6669" in r)
row[-1] = str(row[-1]) + "（修訂）"
put("TWT49U", "202609", json.dumps(j, ensure_ascii=False).encode()); man["TWT49U_202609"]["at"] = "2026-10-20T00:00:00Z"; CA.save_man(man)
rows5, _b = CA.build()
seen1 = json.load(open(CA.FIRST_SEEN, encoding="utf-8"))
w = [r for r in rows5 if r["code"] == "6669"][0]
check("P2b 內容修訂 → 新版本 first_seen＝新抓取時間，舊版本保留", len(seen1[ev]) == 2 and w["first_seen_at"] == "2026-10-20T00:00:00Z", seen1[ev])
# 舊格式登錄（只有時間）→ 繫結當下內容、保留時間
json.dump({ev: "2026-09-05T00:00:00Z"}, open(CA.FIRST_SEEN, "w", encoding="utf-8"))
rows6, _b = CA.build()
check("P2c 舊格式登錄 → 繫結內容、沿用原時間", [r for r in rows6 if r["code"] == "6669"][0]["first_seen_at"] == "2026-09-05T00:00:00Z")
# P5a 故障注入：每一個發布步驟中斷 → 讀取端不是讀到上一整套，就是判定不可用（不會新舊混用還當可用）
good_csv = open(CA.OUT, "rb").read()
real_replace = os.replace
for k in range(3):
    put("TWT49U", "202609", json.dumps(dict(j, data=j["data"][:4 + k]), ensure_ascii=False).encode()); CA.save_man(man)
    calls = [0]
    def boom(a_, b_, k=k):
        if b_ in (CA.OUT, CA.FIRST_SEEN, CA.COVERAGE):
            if calls[0] == k:
                raise OSError("注入中斷")
            calls[0] += 1
        return real_replace(a_, b_)
    os.replace = boom
    try:
        CA.build()
    except OSError:
        pass
    os.replace = real_replace
    a = SS.load_actions(CA.OUT, CA.COVERAGE)
    consistent = a["ok"] and json.load(open(CA.COVERAGE, encoding="utf-8"))["csv_sha256"] == hashlib.sha256(open(CA.OUT, "rb").read()).hexdigest()
    check("P5a-%d 發布第 %d 步中斷 → 舊整套可用或判不可用" % (k + 1, k + 1), (k == 0 and consistent) or (k > 0 and not a["ok"]), a.get("why"))
    CA.build()      # 復原成完整一套
check("P5b coverage.verified 有每個涵蓋來源月的抓取時間", json.load(open(CA.COVERAGE, encoding="utf-8")).get("verified"))

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
