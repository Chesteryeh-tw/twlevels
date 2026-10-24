#!/usr/bin/env python3
"""official_cases.py 的離線驗收（不連網）。用法：python test_official_cases.py"""
import io, os, sys, tempfile, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import official_cases as oc

oc.time.sleep = lambda s: None
FAILS = []


def check(name, cond, detail=""):
    print(("通過  " if cond else "失敗  ") + name + ("" if cond else "：" + str(detail)))
    if not cond:
        FAILS.append(name)


def fake_urlopen(responses, calls):
    def f(req, timeout=60):
        calls.append(req.full_url)
        code, body = responses[min(len(calls) - 1, len(responses) - 1)]
        if code >= 400:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
        class R:
            status = code
            def read(self):
                return body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()
    return f


# O1 5xx 但內容是驗證頁 → 第一次就停，不重試
calls = []
oc.urllib.request.urlopen = fake_urlopen([(503, b"<html>captcha</html>")], calls)
try:
    oc.get("https://example.invalid/a")
    check("O1 503＋網頁 → 立刻停", False, "沒有停")
except oc.Stop:
    check("O1 503＋網頁 → 立刻停（只呼叫 1 次）", len(calls) == 1, len(calls))

# 5xx 非網頁 → 重試，最後成功
calls = []
oc.urllib.request.urlopen = fake_urlopen([(520, b"error code: 520"), (200, b'{"data":[]}')], calls)
data, status, _, _ = oc.get("https://example.invalid/b")
check("O1b 520（非網頁）→ 重試後拿到 200", status == 200 and len(calls) == 2, (status, len(calls)))

# 403 → 停
calls = []
oc.urllib.request.urlopen = fake_urlopen([(403, b"forbidden")], calls)
try:
    oc.get("https://example.invalid/c")
    check("403 → 停", False)
except oc.Stop:
    check("403 → 停（只呼叫 1 次）", len(calls) == 1)

# O2 必要窗口缺一天 → unresolved、不輸出年線
d = tempfile.mkdtemp()
os.chdir(d)
oc.OUT = "."
cal = ["2024%02d%02d" % (m, dd) for m in range(1, 13) for dd in range(1, 29)][:300]
sig = cal[260]
oc.CASES = [("T-1", "u_reg2", "9999", sig, cal[261], "x")]
daily = [["twse", "9999", day, 10.0, 10.5, 9.5, 10.0 if i != 260 else 12.0, 1000.0, 1e8, "h"] for i, day in enumerate(cal)]
snaps = [["twse_9999_202401.json", "u", "t", "t", "200", 1, "h", 1]]
oc.write(snaps, [r for r in daily if r[2] != cal[100]], set(cal))
import csv
r = next(csv.DictReader(open("feature_check.csv", encoding="utf-8")))
check("O2 必要窗口缺 1 天 → coverage incomplete、eligibility unresolved、yl_pct 空白",
      r["coverage_status"] == "incomplete" and r["eligibility"] == "unresolved" and r["yl_pct"] == "" and r["missing_dates"] == "1", r)
oc.write(snaps, daily, set(cal))
r = next(csv.DictReader(open("feature_check.csv", encoding="utf-8")))
check("O2b 窗口完整 → complete，給出年線與判定", r["coverage_status"] == "complete" and r["yl_pct"] != "" and r["eligibility"] in ("pass", "fail"), r)

# P5 日曆只到訊號日 → 次一市場日沒驗證，不能宣告完整
daily5 = [r for r in daily if r[2] <= sig]
oc.write(snaps, daily5, set(d for d in cal if d <= sig))
r = next(csv.DictReader(open("feature_check.csv", encoding="utf-8")))
check("P5 日曆沒有次一市場日 → incomplete、unresolved、原因 next_market_day_unverified",
      r["coverage_status"] == "incomplete" and r["eligibility"] == "unresolved" and "next_market_day_unverified" in r["coverage_reason"], r)
# P5b 日曆有次日、但該股次日沒有開盤價 → incomplete
daily5b = [r for r in daily if r[2] != cal[261]]
oc.write(snaps, daily5b, set(cal))
r = next(csv.DictReader(open("feature_check.csv", encoding="utf-8")))
check("P5b 次日無有效開盤 → incomplete（next_open_missing）", r["coverage_status"] == "incomplete" and "next_open_missing" in r["coverage_reason"], r)
# P5c 日曆少一整個月 → incomplete（calendar_month_missing）
cal_hole = set(d for d in cal if not d.startswith("202406"))
oc.write(snaps, daily, cal_hole)
r = next(csv.DictReader(open("feature_check.csv", encoding="utf-8")))
check("P5c 日曆缺整個月 → incomplete（calendar_month_missing）", r["coverage_status"] == "incomplete" and "calendar_month_missing" in r["coverage_reason"], r)

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
