#!/usr/bin/env python3
"""revenue_fetch.py 的離線驗收（不連網）：用假 CSV、假 HTTP 錯誤在暫存資料夾跑。
對應 GPT 2026-10-22／23 的探針：前 11 項（v1）＋ A～G 七項（v3），改成驗收「正確行為」。
用法：python test_revenue_fetch.py"""
import csv, hashlib, io, json, os, sys, tempfile, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import revenue_fetch as rf

rf.time.sleep = lambda s: None
REAL_DOWNLOAD = rf.download
REAL_SAVE = rf.save_atomic
FAILS = []


def check(name, cond, detail=""):
    print(("通過  " if cond else "失敗  ") + name + ("" if cond else "：" + str(detail)))
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


def fresh(months=((2021, 1),)):
    d = tempfile.mkdtemp(prefix="rftest_")
    os.chdir(d)
    rf.months_to_fetch = lambda today=None: list(months)
    rf.START = months[0]
    rf.download = lambda mkt, y, m: make_csv(mkt, y, m)
    rf.save_atomic = REAL_SAVE
    rf.utc_now = lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d


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


def man():
    return json.load(open(rf.MANIFEST, encoding="utf-8"))


def cur_path(key):
    mkt, ym = key.split("_")
    return rf.raw_path(mkt, ym, man()[key])


PANEL = lambda: os.path.join(rf.OUT, "panel.csv")

# 1 合成有效 CSV
fresh()
code = run_main()
m = man()
check("1 有效 CSV 正常完成、兩市場合格", code == 0 and m["sii_202101"]["ok"] and m["otc_202101"]["ok"], code)
rec = m["sii_202101"]
check("1b 每份有自己的請求／收完時間（秒）", all(k in rec for k in ("request_started_at", "captured_at", "first_captured_at")) and len(rec["captured_at"]) == 20, rec)
check("1c 原始檔用內容雜湊命名", rec["file"] == "202101.%s.csv" % rec["sha256"][:16], rec.get("file"))
row = next(r for r in csv.DictReader(open(PANEL(), encoding="utf-8")) if r["market"] == "sii")
check("1d panel 帶 raw_sha256＝原始檔雜湊、pit_verified=false", row["raw_sha256"] == rec["sha256"] and row["pit_verified"] == "false")
check("1e attempts.jsonl 逐次追加", sum(1 for _ in open(rf.ATTEMPTS, encoding="utf-8")) == 2)

# 2～4 無效值
for name, kw in (("2 NaN 營收", dict(rev="NaN")), ("3 Infinity 營收", dict(rev="Infinity")), ("4 空白代號", dict(code_blank=True)),
                 ("12 截斷檔", dict(cut=True)), ("13 資料年月不對", dict(ym="110/2"))):
    try:
        rf.validate(make_csv("sii", 2021, 1, **kw), "sii", 2021, 1)
        check(name + " 被拒絕", False, "竟然通過")
    except rf.Bad:
        check(name + " 被拒絕", True)

# 5、6 HTTP 403／429：只呼叫一次就整批停；G 404：不重試；5b 503：重試 4 次
for name, c, want_calls, want_exit in (("5 HTTP 403", 403, 1, 2), ("6 HTTP 429", 429, 1, 2)):
    fresh(((2021, 1), (2021, 2)))
    calls = []
    def dl(mkt, y, m, c=c):
        calls.append(1)
        raise urllib.error.HTTPError(rf.URL, c, "x", {}, None)
    rf.download = dl
    code = run_main()
    check(name + " 呼叫 1 次就整批停（exit 2）", len(calls) == want_calls and code == want_exit, "呼叫 %d 次、exit %s" % (len(calls), code))
for name, c, n in (("G HTTP 404 不重試", 404, 1), ("5b HTTP 503 重試共 4 次", 503, 4)):
    fresh()
    calls = []
    def dl(mkt, y, m, c=c):
        calls.append(1)
        raise urllib.error.HTTPError(rf.URL, c, "x", {}, None)
    rf.download = dl
    try:
        rf.fetch_one("sii", 2021, 1)
    except rf.Bad:
        pass
    check(name, len(calls) == n, len(calls))

# 7 HTML 封鎖頁
fresh()
rf.download = lambda mkt, y, m: b"<html><body>captcha</body></html>"
check("7 HTML 封鎖頁整批停（exit 2）", run_main() == 2)

# A --test 命令入口
fresh()
check("A --test 命令入口可以跑完", run_main(["--test"]) == 0)

# 8、10 原始檔被改或不見 → 不發布、舊 panel 不變
fresh()
run_main()
before = open(PANEL(), "rb").read()
p = cur_path("sii_202101")
open(p, "wb").write(make_csv("sii", 2021, 1, n=999))
check("8 原始檔被改 → 拒絕發布（exit 4）、舊 panel 不變", run_main(["--panel-only"]) == 4 and open(PANEL(), "rb").read() == before)
os.remove(p)
check("10 原始檔不見 → 拒絕發布（exit 4）", run_main(["--panel-only"]) == 4 and open(PANEL(), "rb").read() == before)

# B manifest 不見 → 不發布空 panel
fresh()
run_main()
before = open(PANEL(), "rb").read()
os.remove(rf.MANIFEST)
check("B manifest 不見 → --panel-only exit 4、panel 不變", run_main(["--panel-only"]) == 4 and open(PANEL(), "rb").read() == before)

# 11 manifest 損毀 → 停，不下載
fresh()
os.makedirs(rf.RAW, exist_ok=True)
open(rf.MANIFEST, "w").write("{壞掉")
calls = []
rf.download = lambda mkt, y, m: calls.append(1) or make_csv(mkt, y, m)
code = run_main()
check("11 manifest 損毀 → 停（exit 3）、沒有下載、檔案保留", code == 3 and not calls and open(rf.MANIFEST).read() == "{壞掉", code)

# 缺月 → 不發布
fresh(((2021, 1), (2021, 2), (2021, 3)))
def dl_gap(mkt, y, m):
    if (y, m) == (2021, 2) and mkt == "otc":
        raise urllib.error.HTTPError(rf.URL, 404, "x", {}, None)
    return make_csv(mkt, y, m)
rf.download = dl_gap
code = run_main()
check("缺中間月份 → 不發布 panel（exit 4）", code == 4 and not os.path.exists(PANEL()), code)

# F manifest 列數對不上
fresh()
run_main()
mm = man()
mm["sii_202101"]["rows"] = 1
good, problems = rf.check_inputs(mm)
check("F manifest 列數 1、實際 800 → 列為問題", any("列數" in x for x in problems), problems)

# 14 更正：舊版保留、history 可驗；同內容重抓 first_captured_at 不變
fresh()
run_main()
m1 = man()["sii_202101"]
run_main()
m2 = man()["sii_202101"]
check("14a 同內容重抓：first_captured_at 不變、沒有新版本", m2["first_captured_at"] == m1["first_captured_at"] and not m2.get("history"))
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
run_main()
m3 = man()["sii_202101"]
h = m3["history"][-1]
check("14b 更正：舊檔原封不動、history 雜湊可驗", h["sha256"] == m1["sha256"] and rf.verify_file(rf.raw_path("sii", "202101", h), h["sha256"]) is None and h["status"] == "ok")

# C 同一秒連續兩次更正：檔名不撞、各自雜湊正確
fresh()
rf.utc_now = lambda: "2026-10-23T04:00:00Z"
run_main()
for v in ("2,000", "3,000"):
    rf.download = lambda mkt, y, m, v=v: make_csv(mkt, y, m, rev=v)
    run_main()
hs = man()["sii_202101"]["history"]
check("C 同秒兩次更正：2 筆 history、檔案不同、雜湊都對", len(hs) == 2 and hs[0]["file"] != hs[1]["file"]
      and all(rf.verify_file(rf.raw_path("sii", "202101", x), x["sha256"]) is None for x in hs), hs)

# D 新原始檔寫入後、manifest 寫入前中斷：帳本仍一致，下次可接續
fresh()
run_main()
m_before_d = man()["sii_202101"]["sha256"]
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
def boom(path, data):
    REAL_SAVE(path, data)
    if path.endswith(".csv"):
        raise KeyboardInterrupt("模擬中斷")
rf.save_atomic = boom
try:
    run_main()
    interrupted = False
except KeyboardInterrupt:
    interrupted = True
rf.save_atomic = REAL_SAVE
good, problems = rf.check_inputs(man())
check("D 中斷後 manifest 仍指向舊的合格版本、沒有問題", interrupted and not problems and man()["sii_202101"]["sha256"] == m_before_d, problems)
code = run_main()
check("D2 再跑一次可以接續完成", code == 0 and rf.check_inputs(man())[1] == [], code)

# E 舊原始檔被改壞後收到新版：舊版記成衝突證據，不當成有效版本
fresh()
run_main()
open(cur_path("sii_202101"), "wb").write(b"tampered")
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
code = run_main()
h = man()["sii_202101"]["history"][-1]
check("E 舊檔壞掉 → history 記成 conflict、附實際雜湊", h["status"] == "conflict" and "實際 SHA-256" in h.get("conflict", ""), h)

# H 舊版快取（10-23 以前：存在 年月.csv、manifest 沒有 file 欄）仍可驗、更正時舊檔保留
fresh()
os.makedirs(os.path.join(rf.RAW, "sii"), exist_ok=True)
os.makedirs(os.path.join(rf.RAW, "otc"), exist_ok=True)
legacy = {}
for mkt in ("sii", "otc"):
    data = make_csv(mkt, 2021, 1)
    open(os.path.join(rf.RAW, mkt, "202101.csv"), "wb").write(data)
    legacy["%s_202101" % mkt] = dict(ok=True, sha256=hashlib.sha256(data).hexdigest(), rows=rf.MIN_ROWS[mkt],
                                     bytes=len(data), generated="115/10/22", captured_at="2026-10-21T09:41Z", pit_verified=False)
json.dump(legacy, open(rf.MANIFEST, "w", encoding="utf-8"))
check("H 舊版快取 --panel-only 可以發布", run_main(["--panel-only"]) == 0)
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
run_main()
h = man()["sii_202101"]["history"][-1]
check("H2 舊版快取遇更正：舊檔 202101.csv 保留並進 history", h["file"] == "202101.csv" and h["status"] == "ok"
      and os.path.exists(os.path.join(rf.RAW, "sii", "202101.csv")))

# N1 最後月份兩市都缺 → 不發布（舊 panel 不變）、--panel-only 也不發布
fresh(((2021, 1),))
run_main()
before = open(PANEL(), "rb").read()
rf.months_to_fetch = lambda today=None: [(2021, 1), (2021, 2)]
def dl_tail(mkt, y, m):
    if (y, m) == (2021, 2):
        raise urllib.error.HTTPError(rf.URL, 404, "x", {}, None)
    return make_csv(mkt, y, m)
rf.download = dl_tail
code = run_main()
check("N1 最後月份兩市都缺 → 主流程不發布（exit 4）、panel 不變", code == 4 and open(PANEL(), "rb").read() == before, code)
check("N1b --panel-only 也不發布（exit 4）", run_main(["--panel-only"]) == 4 and open(PANEL(), "rb").read() == before)

# N1c 正常發布時寫 panel_meta.json（要求範圍、實際範圍）
fresh(((2021, 1), (2021, 2)))
run_main()
meta = json.load(open(os.path.join(rf.OUT, "panel_meta.json"), encoding="utf-8"))
check("N1c panel_meta 記 requested_end＝observed_end＝202102", meta["requested_end"] == "202102" and meta["observed_end"] == "202102", meta)

# N2 舊格式同內容重抓：first_captured_at 沿用舊 captured_at 並標記
fresh()
os.makedirs(os.path.join(rf.RAW, "sii"), exist_ok=True)
os.makedirs(os.path.join(rf.RAW, "otc"), exist_ok=True)
legacy = {}
for mkt in ("sii", "otc"):
    data = make_csv(mkt, 2021, 1)
    open(os.path.join(rf.RAW, mkt, "202101.csv"), "wb").write(data)
    legacy["%s_202101" % mkt] = dict(ok=True, sha256=hashlib.sha256(data).hexdigest(), rows=rf.MIN_ROWS[mkt],
                                     bytes=len(data), generated="115/10/22", captured_at="2026-10-21T09:41Z", pit_verified=False)
json.dump(legacy, open(rf.MANIFEST, "w", encoding="utf-8"))
run_main()
r = man()["sii_202101"]
check("N2 舊格式同內容重抓：first_captured_at 保留舊時間並標記來源", r["first_captured_at"] == "2026-10-21T09:41Z" and r.get("first_captured_from") == "migrated_from_captured_at", r)

# N3 請求紀錄寫不進去 → 不發布、exit 5
fresh()
run_main()
before = open(PANEL(), "rb").read()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
old_attempts = rf.ATTEMPTS
rf.ATTEMPTS = os.path.join(rf.RAW, "no_such_dir", "x", "attempts.jsonl")
os.makedirs(os.path.dirname(rf.ATTEMPTS), exist_ok=True)
os.chmod(os.path.dirname(rf.ATTEMPTS), 0o500)
writable = os.access(os.path.dirname(rf.ATTEMPTS), os.W_OK)
if writable:   # 以 root 跑時權限擋不住：改成指到一個目錄名稱，讓 open() 失敗
    os.chmod(os.path.dirname(rf.ATTEMPTS), 0o700)
    os.makedirs(rf.ATTEMPTS, exist_ok=True)
rf.AUDIT_FAILED.clear()
code = run_main()
check("N3 請求紀錄寫入失敗 → exit 5、panel 不變", code == 5 and open(PANEL(), "rb").read() == before, code)
rf.ATTEMPTS = old_attempts
rf.AUDIT_FAILED.clear()

# N5 目前原始檔不見，收到相同內容 → 補回並可發布
fresh()
run_main()
os.remove(cur_path("sii_202101"))
code = run_main()
r = man()["sii_202101"]
check("N5 原始檔不見、同內容重抓 → 補回、記 repairs、可以發布", code == 0 and rf.verify_file(cur_path("sii_202101"), r["sha256"]) is None and r.get("repairs"), (code, r.get("repairs")))
# N5b 目前原始檔壞了，收到相同內容 → 壞檔另存衝突證據再補回
fresh()
run_main()
p = cur_path("sii_202101")
open(p, "wb").write(b"broken")
code = run_main()
r = man()["sii_202101"]
moved = r["repairs"][-1].get("moved_to", "")
check("N5b 原始檔壞了 → 壞檔另存 .conflict、補回、可以發布", code == 0 and moved and os.path.exists(os.path.join(rf.RAW, "sii", moved)), (code, r.get("repairs")))

# 15 之後抓失敗：已合格版本保留
fresh()
run_main()
before = man()["sii_202101"]["sha256"]
def bad(mkt, y, m):
    raise ConnectionError("網路斷了")
rf.download = bad
run_main()
check("15 之後抓失敗：ok 與雜湊保留、attempts 有記錄", man()["sii_202101"]["ok"] and man()["sii_202101"]["sha256"] == before
      and any('"retryable"' in l for l in open(rf.ATTEMPTS, encoding="utf-8")))

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
