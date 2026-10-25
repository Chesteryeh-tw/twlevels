#!/usr/bin/env python3
"""月營收：從公開資訊觀測站下載「每月營業收入彙總表」（上市 sii、上櫃 otc），驗證後存起來。

來源（GPT 2026-10-19 查證）：
  POST https://mopsov.twse.com.tw/server-java/FileDownLoad
       step=9, functionName=show_file2, filePath=/t21/{sii|otc}/, fileName=t21sc03_{民國年}_{月}.csv
  CSV 是 UTF-8 含 BOM、14 欄、金額單位千元；同一份 CSV 已含國內和國外（KY、DR）公司。

注意：
  - 只能拿到「現在查得到的版本」。之後更正過的數字會覆蓋原值，也沒有各公司的申報時間，
    所以拿來回測時一律標 pit_verified=false（不是當時真的看得到的版本）。
  - 回應 200 也可能被截斷（GPT 實測遇過），所以每份都要驗：欄數、資料年月、代號唯一、筆數。
  - 遇到 HTML、驗證頁、封鎖就整批停下來，不繞過、不換身分。
  - 原始檔只放 Actions 快取，不 commit（歷史檔的授權範圍還沒確認）。

用法：
  python revenue_fetch.py --test         只抓 2021-01 上市、上櫃各一份，驗證後印出來；不寫原始檔與 panel，只寫請求紀錄
  python revenue_fetch.py                抓 2020-01 到最新一個月；已驗過的跳過，最近 3 個月重抓
  python revenue_fetch.py --panel-only   不抓，只用已存的原始檔重建 data/revenue/panel.csv
"""
import csv, datetime as dt, hashlib, io, json, math, os, sys, time, urllib.error, urllib.parse, urllib.request

URL = "https://mopsov.twse.com.tw/server-java/FileDownLoad"
RAW = os.path.join("data", "revenue_raw")
OUT = os.path.join("data", "revenue")
MANIFEST = os.path.join(RAW, "manifest.json")
START = (2020, 1)
PAUSE = 3          # 每次請求之間停幾秒（官方沒公布上限，保守一點）
RETRY_WAIT = (10, 30, 60)
MIN_ROWS = {"sii": 800, "otc": 600}
HEADER = ["出表日期", "資料年月", "公司代號", "公司名稱", "產業別",
          "營業收入-當月營收", "營業收入-上月營收", "營業收入-去年當月營收",
          "營業收入-上月比較增減(%)", "營業收入-去年同月增減(%)",
          "累計營業收入-當月累計營收", "累計營業收入-去年累計營收",
          "累計營業收入-前期比較增減(%)", "備註"]


class Blocked(Exception):
    """拿到的是網頁、驗證頁或封鎖訊息 —— 整批停下來。"""


class Bad(Exception):
    """內容不完整或對不上 —— 可以重試。"""


class ManifestBroken(Exception):
    """manifest.json 存在但讀不出來 —— 停下來保留證據，不能當成沒抓過而整批重抓。"""


# 這些 HTTP 狀態代表對方拒絕或限流：照「網站拒絕就停」的規則，整批停，不重試、不換來源或身分。
STOP_HTTP = {401, 403, 407, 429, 451}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def months_to_fetch(today=None):
    today = today or dt.date.today()
    # M 月營收最晚 M+1 月 10 日申報；11 日以後才把上個月當成「有了」
    y, m = today.year, today.month
    back = 1 if today.day >= 11 else 2
    for _ in range(back):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    out, (cy, cm) = [], START
    while (cy, cm) <= (y, m):
        out.append((cy, cm))
        cy, cm = (cy + 1, 1) if cm == 12 else (cy, cm + 1)
    return out


def download(mkt, y, m):
    body = urllib.parse.urlencode({
        "step": "9", "functionName": "show_file2",
        "filePath": "/t21/%s/" % mkt, "fileName": "t21sc03_%d_%d.csv" % (y - 1911, m),
    }).encode()
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "twlevels-monthly-revenue (GitHub Actions; once a month)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        clen = r.headers.get("Content-Length")
    if clen and clen.isdigit() and int(clen) != len(data):
        raise Bad("收到 %d bytes，標頭說 %s" % (len(data), clen))
    return data


def validate(data, mkt, y, m):
    """回傳 (列, 出表日期)。不合格就丟例外。"""
    head = data[:2000].lower()
    if b"<html" in head or b"<!doctype" in head or b"captcha" in head:
        raise Blocked("拿到的是網頁，不是 CSV（可能是錯誤頁或驗證頁）")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Bad("不是 UTF-8")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(x.strip() for x in r)]
    if not rows or [h.strip() for h in rows[0]] != HEADER:
        raise Bad("表頭對不上：%s" % (rows[0] if rows else "空檔"))
    body = rows[1:]
    short = [i for i, r in enumerate(body) if len(r) != 14]
    if short:
        raise Bad("有 %d 列欄數不是 14（第一個在第 %d 列，可能被截斷）" % (len(short), short[0] + 2))
    want = "%d/%d" % (y - 1911, m)
    wrong = [r for r in body if r[1].strip() != want]
    if wrong:
        raise Bad("資料年月不是 %s：%s" % (want, wrong[0][1]))
    codes = [r[2].strip() for r in body]
    if any(not c for c in codes):
        raise Bad("有空白的公司代號")
    if len(set(codes)) != len(codes):
        raise Bad("公司代號有重複")
    if len(body) < MIN_ROWS[mkt]:
        raise Bad("只有 %d 列，少於 %d" % (len(body), MIN_ROWS[mkt]))
    for r in body:     # 當月營收一定要是有限數字（空的不補 0；NaN、Infinity 也不行）
        try:
            v = float(r[5].replace(",", ""))
        except ValueError:
            raise Bad("當月營收不是數字：%s %s" % (r[2], r[5]))
        if not math.isfinite(v):
            raise Bad("當月營收不是有限數字：%s %s" % (r[2], r[5]))
    return body, body[0][0].strip()


def fetch_one(mkt, y, m):
    """回傳 (原始 bytes, 資料列, 出表日期, 這次請求開始時間, 收完時間)；時間都是 UTC。
    重試只限：5xx、連線／逾時、內容截斷或格式不對（Bad）。401／403／407／429／451、網頁 → Blocked 整批停；
    其他 4xx（例如 404 找不到這份檔）→ 直接判失敗、不重試、不換來源。"""
    last = None
    for k in range(len(RETRY_WAIT) + 1):
        started = utc_now()
        try:
            data = download(mkt, y, m)
            got = utc_now()
            body, gen = validate(data, mkt, y, m)
            log_attempt(mkt, y, m, started, "ok", sha=hashlib.sha256(data).hexdigest())
            return data, body, gen, started, got
        except Blocked as e:
            log_attempt(mkt, y, m, started, "blocked", error=str(e))
            raise
        except urllib.error.HTTPError as e:
            try:
                ebody = (e.read() or b"")[:2000].lower()
            except Exception:
                ebody = b""
            if b"<html" in ebody or b"<!doctype" in ebody or b"captcha" in ebody:     # 錯誤狀態碼配驗證頁：第一次就停（GPT P2）
                msg = "HTTP %d 且內容是網頁（可能是驗證頁），整批停止" % e.code
                log_attempt(mkt, y, m, started, "blocked", error=msg)
                raise Blocked(msg)
            log_attempt(mkt, y, m, started, "http_%d" % e.code)
            if e.code in STOP_HTTP:
                raise Blocked("HTTP %d（對方拒絕或限流），整批停止" % e.code)
            if e.code < 500:
                raise Bad("HTTP %d（不重試）" % e.code)
            last = "HTTP %d" % e.code
        except (urllib.error.URLError, OSError, Bad) as e:     # 連線、逾時、截斷、格式不對
            log_attempt(mkt, y, m, started, "retryable", error=str(e))
            last = e
        if k < len(RETRY_WAIT):
            print("    %s %d-%02d 第 %d 次失敗：%s，%d 秒後重試" % (mkt, y, m, k + 1, last, RETRY_WAIT[k]))
            time.sleep(RETRY_WAIT[k])
    raise Bad(str(last))


ATTEMPTS = os.path.join(RAW, "attempts.jsonl")


AUDIT_FAILED = []


def log_attempt(mkt, y, m, started, result, **kw):
    """每一次請求都追加一行（只增不改），跟 manifest 的「目前版本」分開。
    寫不進去不會中斷抓取，但會記在 AUDIT_FAILED，最後不發布 panel、回非 0。"""
    try:
        os.makedirs(RAW, exist_ok=True)
        rec = dict(key="%s_%d%02d" % (mkt, y, m), request_started_at=started, logged_at=utc_now(), result=result)
        rec.update(kw)
        with open(ATTEMPTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        AUDIT_FAILED.append("%s_%d%02d：%s" % (mkt, y, m, e))


def load_manifest():
    """第一次（檔案不存在）回空的；檔案存在但壞掉就丟 ManifestBroken，不能當成沒抓過。"""
    if not os.path.exists(MANIFEST):
        return {}
    try:
        man = json.load(open(MANIFEST, encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestBroken("manifest.json 讀不出來：%s" % e)
    if not isinstance(man, dict):
        raise ManifestBroken("manifest.json 格式不對（不是物件）")
    return man


def save_manifest(man):
    os.makedirs(RAW, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MANIFEST)


def save_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


REAL_SAVE_ATOMIC = save_atomic       # 還原舊檔時用，不受測試對 save_atomic 的替換影響


def raw_path(mkt, ym, info):
    """舊版（2026-10-23 以前）存在 {年月}.csv；新版用內容雜湊命名、寫了就不再改。"""
    return os.path.join(RAW, mkt, info.get("file") or (ym + ".csv"))


def store_immutable(mkt, ym, data, sha):
    """原始檔用「年月.雜湊前 16 碼.csv」存，永不覆寫；同名已存在時先比對 bytes，不同就停。"""
    name = "%s.%s.csv" % (ym, sha[:16])
    p = os.path.join(RAW, mkt, name)
    if os.path.exists(p):
        if hashlib.sha256(open(p, "rb").read()).hexdigest() != sha:
            raise ManifestBroken("%s 已存在但內容不同，停止（保留現場）" % p)
        return name
    save_atomic(p, data)
    return name


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def verify_file(p, sha, nbytes=None):
    if not os.path.exists(p):
        return "不見了"
    data = open(p, "rb").read()
    if hashlib.sha256(data).hexdigest() != sha or (nbytes is not None and len(data) != nbytes):
        return "大小或 SHA-256 對不上"
    return None


def check_inputs(man):
    """發布 panel 前重驗：每份目前版本（存在、bytes、SHA-256、列數、內容再驗）＋每個歷史版本的檔案雜湊。
    回傳 (合格清單 [(key, 資料列)], 問題清單)。有任何問題，呼叫的人就不能發布新 panel。"""
    good, problems = [], []
    for key in sorted(man):
        info = man[key]
        if not isinstance(info, dict):
            problems.append("%s：manifest 紀錄格式不對" % key)
            continue
        try:
            mkt, ym = key.split("_")
            y, m = int(ym[:4]), int(ym[4:])
            assert mkt in MIN_ROWS and len(ym) == 6 and 1 <= m <= 12
        except (ValueError, AssertionError):
            problems.append("%s：manifest 的鍵看不懂" % key)
            continue
        for h in info.get("history", []):
            if h.get("status") == "conflict":
                continue          # 已記錄的衝突證據，本來就不是有效版本
            err = verify_file(raw_path(mkt, ym, h), h.get("sha256"))
            if err:
                problems.append("%s：歷史版本 %s %s" % (key, h.get("file"), err))
        if not info.get("ok"):
            continue
        p = raw_path(mkt, ym, info)
        err = verify_file(p, info.get("sha256"), info.get("bytes"))
        if err:
            problems.append("%s：目前版本原始檔%s" % (key, err))
            continue
        try:
            body, _ = validate(open(p, "rb").read(), mkt, y, m)
        except (Bad, Blocked) as e:
            problems.append("%s：原始檔重驗不合格：%s" % (key, e))
            continue
        if info.get("rows") != len(body):
            problems.append("%s：manifest 列數 %s 跟實際 %d 不同" % (key, info.get("rows"), len(body)))
            continue
        good.append((key, body))
    return good, problems


PLAN = os.path.join(RAW, "plan.json")
PLANS = os.path.join(RAW, "plans.jsonl")


def write_plan(months):
    """第一個請求之前先把「這一輪要求到哪個月」存起來（GPT P1）。plan.json 是最新一份，plans.jsonl 逐輪追加。
    之後的覆蓋檢查用這個終點，不從「已經嘗試過的鍵」倒推。"""
    rec = dict(planned_start="%d%02d" % months[0], planned_end="%d%02d" % months[-1],
               planned_keys=["%s_%d%02d" % (mkt, y, m) for (y, m) in months for mkt in ("sii", "otc")],
               planned_at=utc_now())
    try:
        save_atomic(PLAN, json.dumps(rec, ensure_ascii=False).encode("utf-8"))
        with open(PLANS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        AUDIT_FAILED.append("plan：%s" % e)
    return rec


class PlanBroken(Exception):
    pass


def load_plan():
    """舊快取從沒建過計畫 → None（相容）；曾經有過計畫（plan.json 或 plans.jsonl 存在）卻讀不了、欄位不全、
    內容自相矛盾 → PlanBroken，呼叫端不得縮短範圍後繼續發布（GPT P1）。"""
    if not os.path.exists(PLAN):
        if os.path.exists(PLANS) and os.path.getsize(PLANS) > 0:
            raise PlanBroken("plan.json 不見了，但 plans.jsonl 顯示曾經有計畫")
        return None
    try:
        rec = json.load(open(PLAN, encoding="utf-8"))
        s, e, keys = rec["planned_start"], rec["planned_end"], rec["planned_keys"]
        ok = (isinstance(s, str) and isinstance(e, str) and len(s) == 6 and len(e) == 6 and s.isdigit() and e.isdigit()
              and s <= e and isinstance(keys, list) and keys and all(isinstance(k, str) and "_" in k for k in keys)
              and min(k.split("_")[1] for k in keys) == s and max(k.split("_")[1] for k in keys) == e)
    except (OSError, ValueError, KeyError, TypeError) as ex:
        raise PlanBroken("plan.json 讀不了或欄位不全：%s" % ex)
    if not ok:
        raise PlanBroken("plan.json 內容自相矛盾（起訖與 planned_keys 對不上）")
    return rec


def requested_end(man):
    """要求的最後月份＝最近一輪計畫的終點（第一個請求前就存好）；沒有計畫檔（舊快取）才退回帳本裡最大的月份。
    兩者取較晚的，帳本裡出現過更晚的月份也算要求過。"""
    yms = [k.split("_")[1] for k in man if "_" in k]
    plan = load_plan()
    if plan and plan.get("planned_end"):
        yms.append(plan["planned_end"])
    return max(yms) if yms else None


def coverage_gaps(good, end):
    """從 START 到「要求的最後月份」end，上市、上櫃每個月都要有；缺的列出來（包含最後月份兩市都缺）。"""
    have = {k for k, _ in good}
    if not have:
        return ["一份合格的都沒有"]
    last = end or max(k.split("_")[1] for k in have)
    gaps, (y, m) = [], START
    while "%d%02d" % (y, m) <= last:
        for mkt in ("sii", "otc"):
            if "%s_%d%02d" % (mkt, y, m) not in have:
                gaps.append("%s_%d%02d" % (mkt, y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return gaps


def repair_compat():
    """啟動時：指標指到的 generation 完整，但根目錄相容檔跟它不一致（上次發布被打斷）→ 用該 generation 補回。
    指標或 generation 壞了就不動，讀取器會拒用。"""
    ptr_path = os.path.join(OUT, "current.json")
    if not os.path.exists(ptr_path):
        return
    try:
        import revenue_panel
        pp, mp, _ = revenue_panel.resolve(OUT)
    except SystemExit as e:
        print("repair_compat：%s；不動根目錄檔" % e)
        return
    for src, dst in ((pp, os.path.join(OUT, "panel.csv")), (mp, os.path.join(OUT, "panel_meta.json"))):
        data = open(src, "rb").read()
        if not os.path.exists(dst) or open(dst, "rb").read() != data:
            REAL_SAVE_ATOMIC(dst, data)
            print("repair_compat：%s 已用 %s 補回" % (os.path.basename(dst), os.path.basename(os.path.dirname(src))))


def build_panel(man):
    """把所有驗過的原始檔合成一張表。每列帶來源鍵（原始檔 SHA-256、抓取時間、pit_verified），
    研究程式才追得回是哪一版。任何問題（含 manifest 不見、零份合格、月份有缺）就不發布、保留上一份。"""
    panel = os.path.join(OUT, "panel.csv")
    if not man:
        print("沒有來源帳本（manifest 不存在或是空的），不發布 panel%s" % ("，保留上一份" if os.path.exists(panel) else ""))
        return False
    good, problems = check_inputs(man)
    try:
        end = requested_end(man)
        plan = load_plan() or {}
    except PlanBroken as e:
        print("計畫檔有問題（%s），不發布新的 panel（保留上一份）；請人工檢查 plan.json／plans.jsonl" % e)
        return False
    problems += ["缺 %s" % g for g in coverage_gaps(good, end)]
    if AUDIT_FAILED:
        problems += ["請求紀錄寫不進去（證據不完整）：%s" % x for x in AUDIT_FAILED]
    if problems:
        print("有 %d 個問題，不發布新的 panel.csv（保留上一份）：" % len(problems))
        for x in problems[:50]:
            print("  " + x)
        return False
    out = []
    for key, body in good:
        mkt, ym = key.split("_")
        info = man[key]
        for r in body:
            out.append([mkt, r[2].strip(), ym, num(r[5]), num(r[6]), num(r[7]), num(r[9]), num(r[8]),
                        r[0].strip(), info["sha256"], info.get("captured_at", ""), "false"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["market", "code", "ym", "rev", "rev_prev", "rev_ly", "yoy_src", "mom_src", "generated",
                "raw_sha256", "captured_at", "pit_verified"])
    w.writerows([["" if x is None else x for x in r] for r in out])
    panel_bytes = buf.getvalue().encode("utf-8")
    psha = hashlib.sha256(panel_bytes).hexdigest()
    observed = max(k.split("_")[1] for k, _ in good)
    gen_id = "%s-%s" % (utc_now().replace(":", "").replace("-", ""), psha[:12])
    meta = dict(generation=gen_id, panel_sha256=psha, requested_start="%d%02d" % START, requested_end=end,
                planned_end=plan.get("planned_end"), observed_end=observed, months_verified=len(good), rows=len(out),
                missing_keys=[], published_at=utc_now(), pit_verified=False)
    meta_bytes = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
    gdir = os.path.join(OUT, "gen", gen_id)
    meta_path = os.path.join(OUT, "panel_meta.json")
    old = {p: (open(p, "rb").read() if os.path.exists(p) else None) for p in (panel, meta_path)}
    try:
        # 1) 先在不可變的 generation 資料夾寫兩個檔，重讀驗雜湊；任何一步失敗都不碰正在用的檔
        save_atomic(os.path.join(gdir, "panel.csv"), panel_bytes)
        save_atomic(os.path.join(gdir, "panel_meta.json"), meta_bytes)
        if hashlib.sha256(open(os.path.join(gdir, "panel.csv"), "rb").read()).hexdigest() != psha or \
                json.load(open(os.path.join(gdir, "panel_meta.json"), encoding="utf-8")).get("panel_sha256") != psha:
            raise OSError("generation 重讀對不上")
        # 2) 發布：先 panel、後 meta；第二個失敗就把第一個還原
        # 相容匯出（根目錄 panel.csv、panel_meta.json）先寫，指標 current.json 最後換：指標是唯一的發布點，
        # 中途被打斷時指標仍指向完整的上一代，讀取器（revenue_panel.resolve）不會用到新舊混合的資料。
        ptr_path = os.path.join(OUT, "current.json")
        old[ptr_path] = open(ptr_path, "rb").read() if os.path.exists(ptr_path) else None
        ptr_bytes = json.dumps(dict(generation=gen_id, panel_sha256=psha, published_at=meta["published_at"]),
                               ensure_ascii=False).encode("utf-8")
        try:
            save_atomic(panel, panel_bytes)
            save_atomic(meta_path, meta_bytes)
            save_atomic(ptr_path, ptr_bytes)
        except OSError:
            for p, data in old.items():
                if data is None:
                    if os.path.exists(p):
                        os.remove(p)
                else:
                    REAL_SAVE_ATOMIC(p, data)
            raise
    except OSError as e:
        print("panel 發布失敗（%s），保留上一份 panel 與 panel_meta" % e)
        return False
    gens = sorted(os.listdir(os.path.join(OUT, "gen")))
    for g in gens[:-3]:          # 只留最近 3 個 generation
        for fn in os.listdir(os.path.join(OUT, "gen", g)):
            os.remove(os.path.join(OUT, "gen", g, fn))
        os.rmdir(os.path.join(OUT, "gen", g))
    print("panel.csv：%d 列（%d 個市場月份，%s～%s 全數驗過；generation %s）" % (len(out), len(good), meta["requested_start"], end, gen_id))
    return True


def record_version(man, key, mkt, ym, data, body, gen, started, got):
    """新內容：存成不可變檔 → 舊版本驗過後進 history（驗不過就記成衝突證據）→ 立刻寫 manifest。"""
    sha = hashlib.sha256(data).hexdigest()
    old = man.get(key, {})
    hist = list(old.get("history", []))
    if old.get("ok") and old.get("sha256") == sha:
        # 同內容重抓：保留「我們最早看到這份內容」的時間（舊格式沒有 first_captured_at 就沿用舊 captured_at 並標記）
        if not old.get("first_captured_at"):
            old["first_captured_at"] = old.get("captured_at")
            old["first_captured_from"] = "migrated_from_captured_at"
        err = verify_file(raw_path(mkt, ym, old), sha, old.get("bytes"))
        if err:      # 目前版本的原始檔不見或壞了：壞檔先另存成衝突證據，再用剛驗過的同內容 bytes 補回
            p = raw_path(mkt, ym, old)
            rep = dict(at=got, previous_file=old.get("file") or (ym + ".csv"), issue=err)
            if os.path.exists(p):
                bad = p + ".conflict-" + got.replace(":", "").replace("-", "")
                os.replace(p, bad)
                rep["moved_to"] = os.path.basename(bad)
            old["file"] = store_immutable(mkt, ym, data, sha)
            old.setdefault("repairs", []).append(rep)
            print("  %s 原始檔%s，已用相同內容補回" % (key, err))
        old.update(captured_at=got, request_started_at=started)
        man[key] = old
        save_manifest(man)
        return False
    name = store_immutable(mkt, ym, data, sha)
    if old.get("ok"):
        err = verify_file(raw_path(mkt, ym, old), old.get("sha256"), old.get("bytes"))
        entry = dict(file=old.get("file") or (ym + ".csv"), sha256=old.get("sha256"), bytes=old.get("bytes"),
                     rows=old.get("rows"), generated=old.get("generated"),
                     first_captured_at=old.get("first_captured_at") or old.get("captured_at"),
                     captured_at=old.get("captured_at"), request_started_at=old.get("request_started_at"),
                     superseded_at=got, status="ok" if not err else "conflict")
        if err:
            p = raw_path(mkt, ym, old)
            entry["conflict"] = "舊版原始檔%s；實際 SHA-256 %s" % (
                err, hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else "（無檔）")
        hist.append(entry)
        print("  %s 內容有變動，舊版保留%s" % (key, "" if not err else "（但舊檔驗不過，記成衝突）"))
    man[key] = dict(ok=True, file=name, sha256=sha, rows=len(body), bytes=len(data), generated=gen,
                    request_started_at=started, captured_at=got, first_captured_at=got,
                    pit_verified=False, history=hist)
    save_manifest(man)
    return True


SNAP = os.path.join(RAW, "snapshots")
SNAP_META = os.path.join("data", "revenue_snapshots_meta")


def export_early():
    """給 GPT 複核早年母體：把 data/revenue_raw/early/ 裡的原始檔逐份列出 bytes、sha256、資料年月、出表日期、列數，
    並匯出代號清單 early_check/early_codes.csv（只有代號、名稱、產業別、日期，沒有營收金額；market、requested_ym、row_ym、code、name、industry、generated_date、source_file）。
    只讀已抓的檔，不連網。"""
    d = os.path.join(RAW, "early")
    files = sorted(os.listdir(d)) if os.path.isdir(d) else []
    meta, out = [], []
    for fn in files:
        mkt, ym, _sha = fn[:-4].split("_")
        data = open(os.path.join(d, fn), "rb").read()
        rows = [r for r in csv.reader(io.StringIO(data.decode("utf-8-sig"))) if any(x.strip() for x in r)]
        body = rows[1:]
        meta.append(dict(file=fn, market=mkt, requested_ym=ym, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                         rows=len(body), row_ym=sorted({r[1].strip() for r in body}), generated=sorted({r[0].strip() for r in body})))
        for r in body:
            out.append([mkt, ym, r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip(), r[0].strip(), fn])
    os.makedirs("early_check", exist_ok=True)
    with open(os.path.join("early_check", "early_codes.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["market", "requested_ym", "row_ym", "code", "name", "industry", "generated_date", "source_file"])
        w.writerows(out)
    json.dump(meta, open(os.path.join("early_check", "early_files.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("::notice::early 匯出：%d 份檔、%d 列；%s" % (len(meta), len(out), "；".join(
        "%s %s %d 列 %d bytes sha %s" % (m["market"], m["requested_ym"], m["rows"], m["bytes"], m["sha256"][:12]) for m in meta)))


SNAP_CLOCK = None       # 測試用：回傳 aware datetime 的函式；None＝現在


def snapshot_window(ym):
    """GPT 08 P3：正式共同快照只在 M+1 月 11 日 19:30 ～ 12 日 09:00（台北）之間建立；時窗外抓到的不能當這個月的快照。"""
    tpe = dt.timezone(dt.timedelta(hours=8))
    y, m = int(ym[:4]), int(ym[4:])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return dt.datetime(y, m, 11, 19, 30, tzinfo=tpe), dt.datetime(y, m, 12, 9, 0, tzinfo=tpe)


def save_snapshot(ym):
    """SUS 事先登記 v2.4 第 2、9 節「共同營收快照」：營收月 ym 在正式時窗內第一次成功發布 panel 時，把當下整張 panel
    原樣存成不可變快照。已存在就不動（之後的更正不回填）。11 日版與 16 日版都只用這一份。
    meta：formal、captured_at（開始存）、cutoff_at（重讀驗完雜湊的時間）、panel 雜湊、generation、來源原始檔雜湊清單；
    snapshot_pit＝true 只代表「這份快照確實在這個時間取得」，history_pit_verified＝false（歷史月仍是現在查得到的版本）。"""
    now = (SNAP_CLOCK or (lambda: dt.datetime.now(dt.timezone.utc)))()
    lo, hi = snapshot_window(ym)
    d = os.path.join(SNAP, ym)
    if os.path.exists(os.path.join(d, "meta.json")):
        return False
    if not (lo <= now < hi):
        print("::notice::快照：現在不在營收月 %s 的正式時窗（%s～%s），不建正式快照" % (ym, lo.isoformat(), hi.isoformat()))
        return False
    try:
        import revenue_panel
        pp, mp, _ = revenue_panel.resolve(OUT)
    except SystemExit as e:
        print("快照：panel 不一致（%s），不存" % e)
        return False
    if not pp:
        return False
    data = open(pp, "rb").read()
    sha = hashlib.sha256(data).hexdigest()
    srcs = sorted({(r["market"], r["ym"], r["raw_sha256"]) for r in csv.DictReader(io.StringIO(data.decode("utf-8")))})
    gen = json.load(open(mp, encoding="utf-8")).get("generation") if mp and os.path.exists(mp) else None
    save_atomic(os.path.join(d, "panel.csv"), data)
    if hashlib.sha256(open(os.path.join(d, "panel.csv"), "rb").read()).hexdigest() != sha:
        print("快照：重讀雜湊對不上，不寫 meta")
        return False
    done = (SNAP_CLOCK or (lambda: dt.datetime.now(dt.timezone.utc)))()
    if not (lo <= done < hi):
        print("::notice::快照：驗完時已超過正式時窗，不寫 meta")
        return False
    fmt = lambda t: t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = dict(revenue_month=ym, formal=True, captured_at=fmt(now), cutoff_at=fmt(done), panel_sha256=sha,
                panel_generation=gen, source=os.path.relpath(pp, OUT),
                sources_sha256=hashlib.sha256(json.dumps(srcs).encode()).hexdigest(), sources_count=len(srcs),
                snapshot_pit=True, history_pit_verified=False)
    mb = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
    save_atomic(os.path.join(d, "meta.json"), mb)
    # 快取不是永久證據。meta（沒有營收數字）另存一份進 repo；已存在就不動。sus_study 要求兩份整份相同。
    pm = os.path.join(SNAP_META, "%s.json" % ym)
    if not os.path.exists(pm):
        save_atomic(pm, mb)
    print("快照：營收月 %s 存好（cutoff_at %s，sha %s）" % (ym, meta["cutoff_at"], sha[:12]))
    return True


def early_probe(ym):
    """SUS 事先登記 v2.3 第 7 節：補抓早年營收前，先抓指定月份上市、上櫃各 1 份，只看格式、列數、重複代號，
    不碰 manifest、不建 panel、不看績效。原始檔存 data/revenue_raw/early/（不進 repo）。被擋就停。"""
    y, m = int(ym[:4]), int(ym[4:6])
    os.makedirs(os.path.join(RAW, "early"), exist_ok=True)
    rows_out = []
    for mkt in ("sii", "otc"):
        started = utc_now()
        try:
            data = download(mkt, y, m)
        except urllib.error.HTTPError as e:
            ebody = (e.read() or b"")[:2000].lower()
            blocked = e.code in STOP_HTTP or b"<html" in ebody or b"<!doctype" in ebody or b"captcha" in ebody
            log_attempt(mkt, y, m, started, "blocked" if blocked else "http_%d" % e.code)
            print("::notice::early %s %s：HTTP %d%s" % (mkt, ym, e.code, "（被擋，停止）" if blocked else ""))
            if blocked:
                sys.exit(2)
            rows_out.append((mkt, "HTTP %d" % e.code))
            continue
        except (urllib.error.URLError, OSError) as e:
            log_attempt(mkt, y, m, started, "retryable", error=str(e))
            print("::notice::early %s %s：連線失敗 %s" % (mkt, ym, e))
            rows_out.append((mkt, "連線失敗"))
            continue
        sha = hashlib.sha256(data).hexdigest()
        log_attempt(mkt, y, m, started, "early_probe", sha=sha)
        head = data[:2000].lower()
        if b"<html" in head or b"<!doctype" in head or b"captcha" in head:
            print("::notice::early %s %s：拿到的是網頁，停止" % (mkt, ym))
            sys.exit(2)
        with open(os.path.join(RAW, "early", "%s_%s_%s.csv" % (mkt, ym, sha[:12])), "wb") as f:
            f.write(data)
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            rows_out.append((mkt, "不是 UTF-8，%d bytes，sha %s" % (len(data), sha[:12])))
            continue
        rows = [r for r in csv.reader(io.StringIO(text)) if any(x.strip() for x in r)]
        body = rows[1:] if rows else []
        codes = [r[2].strip() for r in body if len(r) > 2]
        ok_head = bool(rows) and [h.strip() for h in rows[0]] == HEADER
        try:
            validate(data, mkt, y, m)
            strict = "嚴格驗證通過"
        except (Bad, Blocked) as e:
            strict = "嚴格驗證不過：%s" % e
        msg = "%s %s：%d bytes、sha %s、表頭%s、%d 列、代號重複 %d、欄數非 14 的 %d 列；%s" % (
            mkt, ym, len(data), sha[:12], "對得上" if ok_head else "對不上 %s" % (rows[0] if rows else "空檔"),
            len(body), len(codes) - len(set(codes)), sum(1 for r in body if len(r) != 14), strict)
        print(msg)
        print("::notice::early " + msg)
        rows_out.append((mkt, msg))
        time.sleep(PAUSE)
    return rows_out


def main():
    args = sys.argv[1:]
    if "--export-early" in args:
        export_early()
        return
    if "--early-probe" in args:
        early_probe(args[args.index("--early-probe") + 1])
        return
    if "--test" in args:
        for mkt in ("sii", "otc"):
            data, body, gen, started, got = fetch_one(mkt, 2021, 1)
            print("%s 2021-01：%d 列、%d bytes、出表日期 %s、SHA-256 %s、收完 %s"
                  % (mkt, len(body), len(data), gen, hashlib.sha256(data).hexdigest()[:16], got))
            for r in body[:3]:
                print("   ", r[2], r[3], "當月", r[5], "去年當月", r[7], "年增%", r[9][:6])
            time.sleep(PAUSE)
        if AUDIT_FAILED:
            print("請求紀錄寫入失敗 %d 次，證據不完整，測試不算通過" % len(AUDIT_FAILED))
            sys.exit(5)
        print("測試通過（沒有寫行情原始檔或 panel；請求紀錄會寫進 attempts.jsonl）")
        return
    try:
        man = load_manifest()
    except ManifestBroken as e:
        print("停止：%s。檔案保留不動，請人工檢查，不會自動整批重抓。" % e)
        sys.exit(3)
    repair_compat()
    if "--panel-only" in args:
        if not build_panel(man):
            sys.exit(4)
        return
    months = months_to_fetch()
    write_plan(months)                  # 第一個請求之前先存這一輪的目標範圍（GPT P1）
    recent = set(months[-3:])           # 最近 3 個月可能還有更正，每次都重抓
    failed, blocked = [], None
    try:
        for (y, m) in months:
            for mkt in ("sii", "otc"):
                key = "%s_%d%02d" % (mkt, y, m)
                if man.get(key, {}).get("ok") and (y, m) not in recent:
                    continue
                try:
                    data, body, gen, started, got = fetch_one(mkt, y, m)
                except Blocked as e:
                    blocked = "%s：%s" % (key, e)
                    man.setdefault(key, {}).setdefault("ok", False)   # 留鍵：這個月也在要求範圍內
                    break
                except Bad as e:
                    failed.append("%s：%s" % (key, e))
                    rec = man.setdefault(key, {})
                    rec.setdefault("ok", False)      # 失敗不能抹掉之前合格的版本
                    save_manifest(man)
                    time.sleep(PAUSE)
                    continue
                record_version(man, key, mkt, "%d%02d" % (y, m), data, body, gen, started, got)
                print("  %s：%d 列" % (key, len(body)))
                time.sleep(PAUSE)
            if blocked:
                break
    except ManifestBroken as e:
        save_manifest(man)
        print("停止：%s" % e)
        sys.exit(3)
    save_manifest(man)
    published = build_panel(man)
    if published and not failed and not blocked and not AUDIT_FAILED:
        save_snapshot("%d%02d" % months[-1])     # 這一輪的目標月份全數驗過才存快照
    ok = sum(1 for v in man.values() if v.get("ok"))
    print("完成：%d／%d 份合格" % (ok, len(months) * 2))
    if failed:
        print("失敗 %d 份：\n  " % len(failed) + "\n  ".join(failed))
    if blocked:
        print("被擋，已停止：" + blocked)
        sys.exit(2)
    if AUDIT_FAILED:
        print("請求紀錄寫入失敗 %d 次，證據不完整" % len(AUDIT_FAILED))
        sys.exit(5)
    if not published:
        sys.exit(4)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
