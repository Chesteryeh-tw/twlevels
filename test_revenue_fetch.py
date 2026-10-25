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

# ---- GPT 2026-10-24 複核 P1～P4 ----
# P1 目標範圍在第一個請求前就存好：已有 202101 快取，要求到 202103，第一份重抓被擋 → panel_meta／plan 的終點是 202103
fresh(((2021, 1),))
run_main()
meta_before = json.load(open(os.path.join(rf.OUT, "panel_meta.json"), encoding="utf-8"))
panel_before = open(PANEL(), "rb").read()
rf.months_to_fetch = lambda today=None: [(2021, 1), (2021, 2), (2021, 3)]
rf.download = lambda *a: (_ for _ in ()).throw(rf.Blocked("synthetic block"))
code = run_main()
plan = json.load(open(rf.PLAN, encoding="utf-8"))
check("P1 被擋時 plan 已記 planned_end=202103、exit 2", code == 2 and plan["planned_end"] == "202103" and len(plan["planned_keys"]) == 6, (code, plan))
check("P1b 沒發布新 panel（舊 panel 與 meta 不變）", open(PANEL(), "rb").read() == panel_before and
      json.load(open(os.path.join(rf.OUT, "panel_meta.json"), encoding="utf-8")) == meta_before)
check("P1c --panel-only 沿用最近一輪計畫的終點，也不發布", run_main(["--panel-only"]) == 4 and open(PANEL(), "rb").read() == panel_before)
check("P1d plans.jsonl 逐輪追加", sum(1 for _ in open(rf.PLANS, encoding="utf-8")) == 2)

# P2 503 配驗證頁：第一次就停（Blocked），不重試
fresh()
calls = []
def html503(*a):
    calls.append(1)
    raise urllib.error.HTTPError(rf.URL, 503, "x", {}, io.BytesIO(b"<html>captcha</html>"))
rf.download = html503
try:
    rf.fetch_one("sii", 2021, 1)
    check("P2 503＋驗證頁 → Blocked", False, "沒有丟例外")
except rf.Blocked:
    check("P2 503＋驗證頁 → 只呼叫 1 次就 Blocked", len(calls) == 1, len(calls))
# P2b 503 非網頁仍照舊重試 4 次
fresh()
calls = []
def plain503(*a):
    calls.append(1)
    raise urllib.error.HTTPError(rf.URL, 503, "x", {}, io.BytesIO(b"service unavailable"))
rf.download = plain503
try:
    rf.fetch_one("sii", 2021, 1)
except rf.Bad:
    pass
check("P2b 503（非網頁）仍重試 4 次", len(calls) == 4, len(calls))

# P3 --test 時請求紀錄寫不進去 → 非 0
fresh()
rf.ATTEMPTS = os.path.join(rf.RAW, "as_directory")
os.makedirs(rf.ATTEMPTS, exist_ok=True)
rf.AUDIT_FAILED.clear()
code = run_main(["--test"])
check("P3 --test 請求紀錄寫入失敗 → exit 5（不顯示通過）", code == 5 and rf.AUDIT_FAILED, code)
rf.ATTEMPTS = os.path.join(rf.RAW, "attempts.jsonl")
rf.AUDIT_FAILED.clear()

# P4 panel_meta 寫入失敗 → panel.csv 與 meta 都維持上一份（同一 generation 才發布）
fresh()
run_main()
panel_before = open(PANEL(), "rb").read()
meta_path = os.path.join(rf.OUT, "panel_meta.json")
meta_before_bytes = open(meta_path, "rb").read()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
def bad_meta(path, data):
    if str(path).endswith("panel_meta.json"):
        raise OSError("synthetic meta failure")
    return REAL_SAVE(path, data)
rf.save_atomic = bad_meta
code = run_main()
rf.save_atomic = REAL_SAVE
check("P4 meta 寫入失敗 → panel.csv 不變、meta 不變、exit 4", open(PANEL(), "rb").read() == panel_before and open(meta_path, "rb").read() == meta_before_bytes and code == 4, code)
# P4b 正常發布：meta 的 panel_sha256 等於 panel.csv，generation 資料夾兩個檔都在
run_main()
meta = json.load(open(meta_path, encoding="utf-8"))
gdir = os.path.join(rf.OUT, "gen", meta["generation"])
check("P4b meta.panel_sha256＝panel.csv 雜湊；generation 資料夾有兩個檔",
      meta["panel_sha256"] == hashlib.sha256(open(PANEL(), "rb").read()).hexdigest() and
      sorted(os.listdir(gdir)) == ["panel.csv", "panel_meta.json"], meta)
# P4c 第二個檔（meta）發布失敗而 panel 已換 → 還原：模擬只在「發布」那次寫 meta 時失敗（generation 內的 meta 寫成功）
fresh()
run_main()
panel_before = open(PANEL(), "rb").read()
meta_before_bytes = open(meta_path, "rb").read()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
state = {"n": 0}
def bad_second(path, data):
    if str(path).endswith("panel_meta.json"):
        state["n"] += 1
        if state["n"] == 2:          # 第 1 次是 generation 內的 meta，第 2 次才是正式發布
            raise OSError("synthetic publish failure")
    return REAL_SAVE(path, data)
rf.save_atomic = bad_second
code = run_main()
rf.save_atomic = REAL_SAVE
check("P4c 發布 meta 失敗 → panel.csv 還原成上一份", open(PANEL(), "rb").read() == panel_before and open(meta_path, "rb").read() == meta_before_bytes and code == 4, code)

# ---- GPT 2026-10-24 第二輪（SUS v2.1 複核）：P1 計畫檔損壞、P4 單一指標發布 ----
import revenue_panel as rp
# P1e plan.json 損壞但 plans.jsonl 存在 → --panel-only 不得縮短範圍後成功
fresh(((2021, 1),))
run_main()
rf.months_to_fetch = lambda today=None: [(2021, 1), (2021, 2), (2021, 3)]
rf.download = lambda *a: (_ for _ in ()).throw(rf.Blocked("synthetic block"))
run_main()                                               # 被擋，plan 記 202103
panel_before = open(PANEL(), "rb").read()
open(rf.PLAN, "w").write("{broken")
code = run_main(["--panel-only"])
check("P1e plan.json 損壞 → --panel-only exit 4，不發布", code == 4 and open(PANEL(), "rb").read() == panel_before, code)
# P1f plan.json 被刪、plans.jsonl 還在 → 同樣拒絕
os.remove(rf.PLAN)
code = run_main(["--panel-only"])
check("P1f plan.json 不見但有 plans.jsonl → 拒絕", code == 4 and open(PANEL(), "rb").read() == panel_before, code)
# P1g 欄位矛盾（planned_end 跟 planned_keys 對不上）→ PlanBroken
json.dump(dict(planned_start="202101", planned_end="202103", planned_keys=["sii_202101", "otc_202101"], planned_at="x"), open(rf.PLAN, "w"))
try:
    rf.load_plan(); check("P1g 內容矛盾 → PlanBroken", False, "沒丟例外")
except rf.PlanBroken:
    check("P1g 內容矛盾 → PlanBroken", True)
# P1h 舊快取（沒有 plan.json、沒有 plans.jsonl）→ 相容，None，可發布
fresh(((2021, 1),))
run_main()
os.remove(rf.PLAN); os.remove(rf.PLANS)
check("P1h 舊快取沒有計畫檔 → load_plan 回 None、--panel-only 仍可發布", rf.load_plan() is None and run_main(["--panel-only"]) == 0)

# P4d 指標 current.json 是權威發布點；正常發布後讀取器讀 generation 並核雜湊
fresh()
run_main()
ptr = json.load(open(os.path.join(rf.OUT, "current.json"), encoding="utf-8"))
pp, mp, how = rp.resolve(rf.OUT)
check("P4d 正常發布後 resolve 指到 generation，雜湊一致", ptr["generation"] in pp and
      ptr["panel_sha256"] == hashlib.sha256(open(pp, "rb").read()).hexdigest(), (pp, how))
# P4e 兩次正式寫入之間被打斷（BaseException，不是 OSError）：panel 已換、meta 仍舊 → 指標仍指上一代，讀取器讀到完整上一代
old_panel = open(pp, "rb").read()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
class Crash(BaseException):
    pass
def crash_meta(path, data):
    if str(path) == os.path.join(rf.OUT, "panel_meta.json"):
        raise Crash()
    return REAL_SAVE(path, data)
rf.save_atomic = crash_meta
try:
    run_main()
except Crash:
    pass
rf.save_atomic = REAL_SAVE
pp2, mp2, _ = rp.resolve(rf.OUT)
root_mismatch = open(PANEL(), "rb").read() != open(pp2, "rb").read()
check("P4e 被打斷：指標仍指上一代、讀取器讀到完整上一代；根目錄相容檔已不一致", open(pp2, "rb").read() == old_panel and root_mismatch)
# P4f 下一次啟動 repair_compat 把根目錄相容檔補回指標指到的 generation
rf.repair_compat()
check("P4f 啟動時 repair_compat 補回相容檔", open(PANEL(), "rb").read() == open(pp2, "rb").read() and
      open(mp, "rb").read() == open(mp2, "rb").read())
# P4g generation 的 panel 被改過 → 讀取器拒用（不退回根目錄）
open(pp2, "ab").write(b"x")
try:
    rp.resolve(rf.OUT); check("P4g generation 被改 → resolve 拒用", False, "沒丟例外")
except SystemExit:
    check("P4g generation 被改 → resolve 拒用", True)

# ---- 早年試抓（SUS v2.3 第 7 節）----
fresh()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, ym="106/12")
code = run_main(["--early-probe", "201712"])
files = sorted(os.listdir(os.path.join(rf.RAW, "early")))
check("E1 早年試抓：存 2 份原始檔、不建 manifest 與 panel、exit 0", code == 0 and len(files) == 2 and
      not os.path.exists(rf.MANIFEST) and not os.path.exists(PANEL()), (code, files))
fresh()
rf.download = lambda mkt, y, m: b"<html>captcha</html>"
code = run_main(["--early-probe", "201712"])
check("E2 早年試抓拿到網頁 → exit 2，不存檔", code == 2 and not os.path.exists(os.path.join(rf.RAW, "early")) or
      (code == 2 and not os.listdir(os.path.join(rf.RAW, "early"))), code)
fresh()
def h403(*a):
    raise urllib.error.HTTPError(rf.URL, 403, "x", {}, io.BytesIO(b"forbidden"))
rf.download = h403
check("E3 早年試抓 403 → exit 2（停）", run_main(["--early-probe", "201712"]) == 2)

# ---- 共同營收快照（SUS v2.4 第 2、9 節）----
fresh(((2021, 1), (2021, 2)))
run_main()
sd = os.path.join(rf.SNAP, "202102")
meta = json.load(open(os.path.join(sd, "meta.json"), encoding="utf-8"))
check("K1 全數驗過 → 存目標月快照，雜湊＝當下 panel", meta["revenue_month"] == "202102" and
      meta["panel_sha256"] == hashlib.sha256(open(PANEL(), "rb").read()).hexdigest() and meta["cutoff_at"], meta)
first = open(os.path.join(sd, "panel.csv"), "rb").read()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, rev="2,000")
run_main()
check("K1b repo 持久 meta 與快照 meta 相同", json.load(open(os.path.join(rf.SNAP_META, "202102.json"), encoding="utf-8")) == meta)
check("K2 之後的更正不回填快照（已存在就不動）", open(os.path.join(sd, "panel.csv"), "rb").read() == first and
      json.load(open(os.path.join(sd, "meta.json"), encoding="utf-8")) == meta)
fresh(((2021, 1), (2021, 2)))
rf.download = lambda mkt, y, m: (_ for _ in ()).throw(rf.Blocked("x")) if (y, m) == (2021, 2) else make_csv(mkt, y, m)
run_main()
check("K3 目標月沒抓齊（被擋）→ 不存快照", not os.path.exists(os.path.join(rf.SNAP, "202102")))

# ---- 早年清單匯出 ----
fresh()
rf.download = lambda mkt, y, m: make_csv(mkt, y, m, ym="106/12")
run_main(["--early-probe", "201712"])
code = run_main(["--export-early"])
rows = list(csv.reader(open(os.path.join("early_check", "early_codes.csv"), encoding="utf-8-sig")))
meta = json.load(open(os.path.join("early_check", "early_files.json"), encoding="utf-8"))
check("E4 匯出清單：每列有市場、代號、資料年月；meta 有 bytes 與 sha256", code == 0 and len(rows) - 1 == len(meta[0:1]) * 0 + sum(m["rows"] for m in meta) and
      all(m["sha256"] and m["bytes"] for m in meta) and rows[1][0] in ("sii", "otc"), (code, len(rows)))

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
