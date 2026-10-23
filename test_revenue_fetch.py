#!/usr/bin/env python3
"""revenue_fetch.py 的離線驗收（不連網）：用假 CSV、假 HTTP 錯誤在暫存資料夾跑。
對應 GPT 2026-10-22／23 的 11 項探針與 P0／P1 修正。用法：python test_revenue_fetch.py"""
import csv, hashlib, io, json, os, sys, tempfile, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import revenue_fetch as rf

rf.time.sleep = lambda s: None
FAILS = []


def check(name, cond, detail=""):
    print(("通過  " if cond else "失敗  ") + name + ("" if cond else "：" + detail))
    if not cond:
        FAILS.append(name)


def make_csv(mkt, y, m, n=None, rev="1,000", code_blank=False, ym=None, cut=False):
    n = n or rf.MIN_ROWS[mkt]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(rf.HEADER)
    for i in range(n):
        code = "" if (code_blank and i == 5) else str(1000 + i)
        w.writerow(["115/10/22", ym or "%d/%d" % (y - 1911, m), code, "公司%d" % i, "電子",
                    rev if i == 3 else "1,000", "900", "800", "11.1", "25.0", "9000", "8000", "12.5", ""])
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return data[: len(data) // 2] if cut else data


def http_error(code):
    def f(mkt, y, m):
        raise urllib.error.HTTPError(rf.URL, code, "x", {}, None)
    return f


def fresh():
    d = tempfile.mkdtemp(prefix="rftest_")
    os.chdir(d)
    return d


def months_stub(ms):
    rf.months_to_fetch = lambda today=None: ms


def run_main(args=()):
    old = sys.argv
    sys.argv = ["revenue_fetch.py"] + list(args)
    try:
        rf.main()
        return 0
    except SystemExit as e:
        return e.code
    finally:
        sys.argv = old


# 1 合成有效 CSV
fresh()
months_stub([(2021, 1)])
rf.download = lambda mkt, y, m: make_csv(mkt, y, m)
code = run_main()
man = json.load(open(rf.MANIFEST, encoding="utf-8"))
check("1 有效 CSV 正常完成、兩市場合格", code == 0 and man["sii_202101"]["ok"] and man["otc_202101"]["ok"], str(code))
rec = man["sii_202101"]
check("1b 每份有自己的請求／收完時間（秒）與 first_captured_at", all(k in rec for k in ("request_started_at", "captured_at", "first_captured_at")) and rec["captured_at"].endswith("Z") and len(rec["captured_at"]) == 20, str(rec))
hdr = open(os.path.join(rf.OUT, "panel.csv"), encoding="utf-8").readline().strip().split(",")
check("1c panel 每列帶 raw_sha256、captured_at、pit_verified", hdr[-3:] == ["raw_sha256", "captured_at", "pit_verified"], str(hdr))
row = next(r for r in csv.DictReader(open(os.path.join(rf.OUT, "panel.csv"), encoding="utf-8")) if r["market"] == "sii")
check("1d panel 的 raw_sha256 等於原始檔雜湊、pit_verified=false", row["raw_sha256"] == rec["sha256"] and row["pit_verified"] == "false")

# 2 NaN、3 Infinity、4 空代號
for name, kw in (("2 NaN 營收", dict(rev="NaN")), ("3 Infinity 營收", dict(rev="Infinity")), ("4 空白代號", dict(code_blank=True))):
    try:
        rf.validate(make_csv("sii", 2021, 1, **kw), "sii", 2021, 1)
        check(name + " 被拒絕", False, "竟然通過")
    except rf.Bad:
        check(name + " 被拒絕", True)

# 5 HTTP 403、6 HTTP 429：立刻停，不重試
for name, c in (("5 HTTP 403", 403), ("6 HTTP 429", 429)):
    fresh()
    calls = []
    def dl(mkt, y, m, c=c):
        calls.append(1)
        raise urllib.error.HTTPError(rf.URL, c, "x", {}, None)
    rf.download = dl
    months_stub([(2021, 1), (2021, 2)])
    code = run_main()
    check(name + " 只呼叫 1 次就整批停（exit 2）", len(calls) == 1 and code == 2, "呼叫 %d 次、exit %s" % (len(calls), code))

# 5b HTTP 503 仍走重試（暫時錯誤）
calls = []
def dl503(mkt, y, m):
    calls.append(1)
    raise urllib.error.HTTPError(rf.URL, 503, "x", {}, None)
rf.download = dl503
try:
    rf.fetch_one("sii", 2021, 1)
except rf.Bad:
    pass
check("5b HTTP 503 會重試（共 4 次）", len(calls) == 4, str(len(calls)))

# 7 HTML 封鎖頁
fresh()
rf.download = lambda mkt, y, m: b"<html><body>captcha</body></html>"
months_stub([(2021, 1)])
check("7 HTML 封鎖頁整批停（exit 2）", run_main() == 2)

# 8 原始檔 hash 不符 → 不發布新 panel，保留舊的
fresh()
months_stub([(2021, 1)])
rf.download = lambda mkt, y, m: make_csv(mkt, y, m)
run_main()
panel = os.path.join(rf.OUT, "panel.csv")
before = open(panel, "rb").read()
p = os.path.join(rf.RAW, "sii", "202101.csv")
open(p, "wb").write(make_csv("sii", 2021, 1, n=999))
code = run_main(["--panel-only"])
check("8 原始檔被改、hash 不符 → 拒絕發布（exit 4）且舊 panel 不變", code == 4 and open(panel, "rb").read() == before, "exit %s" % code)

# 9 panel 來源欄（已在 1c、1d）；10 manifest OK 但原始檔不見
os.remove(p)
code = run_main(["--panel-only"])
check("10 manifest 說合格但原始檔不見 → 拒絕發布（exit 4）", code == 4 and open(panel, "rb").read() == before, "exit %s" % code)

# 11 manifest 損毀 → 停，不整批重抓
fresh()
os.makedirs(rf.RAW, exist_ok=True)
open(rf.MANIFEST, "w").write("{壞掉")
calls = []
rf.download = lambda mkt, y, m: calls.append(1) or make_csv(mkt, y, m)
months_stub([(2021, 1)])
code = run_main()
check("11 manifest 損毀 → 停（exit 3）、沒有下載、檔案保留", code == 3 and not calls and open(rf.MANIFEST).read() == "{壞掉", "exit %s、下載 %d 次" % (code, len(calls)))

# 12 截斷、13 錯年月
for name, kw in (("12 截斷檔", dict(cut=True)), ("13 資料年月不對", dict(ym="110/2"))):
    try:
        rf.validate(make_csv("sii", 2021, 1, **kw), "sii", 2021, 1)
        check(name + " 被拒絕", False, "竟然通過")
    except rf.Bad:
        check(name + " 被拒絕", True)

# 14 更正保存：內容變了，舊版另存並記進 history；同內容重抓保留 first_captured_at
fresh()
months_stub([(2021, 1)])
rf.download = lambda mkt, y, m: make_csv(mkt, y, m)
run_main()
m1 = json.load(open(rf.MANIFEST, encoding="utf-8"))["sii_202101"]
run_main()
m2 = json.load(open(rf.MANIFEST, encoding="utf-8"))["sii_202101"]
check("14a 同內容重抓：first_captured_at 不變", m2["first_captured_at"] == m1["first_captured_at"])
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
run_main()
m3 = json.load(open(rf.MANIFEST, encoding="utf-8"))["sii_202101"]
kept = os.path.join(rf.RAW, "sii", m3["history"][-1]["file"]) if m3.get("history") else ""
check("14b 更正：舊版另存、history 記下舊雜湊", m3["sha256"] != m1["sha256"] and m3["history"][-1]["sha256"] == m1["sha256"] and os.path.exists(kept))

# 15 中斷／失敗不抹掉已合格版本
def bad(mkt, y, m):
    raise ConnectionError("網路斷了")
rf.download = bad
run_main()
m4 = json.load(open(rf.MANIFEST, encoding="utf-8"))["sii_202101"]
check("15 之後抓失敗：ok 與雜湊保留，只多 last_error", m4["ok"] and m4["sha256"] == m3["sha256"] and "last_error" in m4)

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
