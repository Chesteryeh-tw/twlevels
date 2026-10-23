#!/usr/bin/env python3
"""上市權證的官方日資料（2026-10-24 起）。只在 GitHub Actions 跑；原始檔只放 Actions 快取，不進 repo。

來源（證交所，公開、免費）：
  三大法人 T86  https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date=YYYYMMDD&selectType=0999 （認購）
                                                                            selectType=0999P（認售）
                （舊網址 /fund/T86 的 0999 回「沒有符合條件的資料」，10-24 探測；rwd 網址才有）
  每日收盤行情  https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=YYYYMMDD&type=0999 （認購）
                                                                                    type=0999P（認售）
                最後一張表有「標的代號」，用它對標的（官方）；名稱比對只當備援與檢查
  開市日       https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=YYYYMM01
用途：研究_權證分點 H5「認購買超排行」。非自營商淨股數＝−自營商買賣超股數（GPT 10-15 對 HiStock 67／80 張數全符）。

規則（同 revenue_fetch.py）：
  HTTP 401／403／407／429／451 或回應是網頁 → 整批停，不重試、不換來源或身分。
  5xx、連線錯誤 → 等 10／30／60 秒重試。其他 4xx 直接記失敗。
  每次請求間隔 PAUSE 秒。原始回應 gzip 存檔＋SHA-256＋請求時間；每份成功立刻原子寫帳本；attempts.jsonl 只增不改。
  時間預算用完就正常結束，下次從帳本接著抓。

用法：
  python warrant_fetch.py --probe [YYYYMMDD]   抓一天四份＋OpenAPI 權證端點，只印欄位與筆數（Actions 註記），不寫檔
  python warrant_fetch.py                      從 START 抓到昨天（台灣時間）
  python warrant_fetch.py --derive-only        只重建 derived/
"""
import csv, datetime as dt, gzip, hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request

START = "20241001"
RAW = os.path.join("data", "warrant_raw")
PAUSE = 4
BUDGET_MIN = float(os.environ.get("WARRANT_BUDGET_MIN", "290"))
STOP_HTTP = {401, 403, 407, 429, 451}
RETRY_WAIT = (10, 30, 60)
UA = "twlevels-warrant-research (GitHub Actions; daily official warrant tables)"
TW = dt.timezone(dt.timedelta(hours=8))

KINDS = {
    "t86c": "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date=%s&selectType=0999",
    "t86p": "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date=%s&selectType=0999P",
    "mic": "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=%s&type=0999",
    "mip": "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=%s&type=0999P",
}


class Stop(Exception):
    pass


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def notice(msg):
    print("::notice::" + msg.replace("\n", " | ")[:3800])


def log_attempt(rec):
    os.makedirs(RAW, exist_ok=True)
    with open(os.path.join(RAW, "attempts.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def get(url, log=True):
    """回傳 (bytes, status, started, got)。被擋 → Stop。"""
    for k in range(len(RETRY_WAIT) + 1):
        started = utc_now()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        err = None
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            data, status = (e.read() or b""), e.code
        except (urllib.error.URLError, OSError) as e:
            data, status, err = b"", 0, str(e)
        head = data[:500].lower()
        html = b"<html" in head or b"<!doctype" in head or b"captcha" in head
        if log:
            log_attempt(dict(url=url, started=started, got=utc_now(), status=status,
                             bytes=len(data), html=html, err=err))
        if status in STOP_HTTP:
            raise Stop("HTTP %d：%s" % (status, url))
        if html:
            snip = data[:300].decode("utf-8", "replace").replace("\n", " ")
            raise Stop("回應是網頁（可能被擋，HTTP %s）：%s；開頭：%s" % (status, url, snip))
        if (status >= 500 or status == 0) and k < len(RETRY_WAIT):
            print("    HTTP %s，%d 秒後重試" % (status or err, RETRY_WAIT[k]))
            time.sleep(RETRY_WAIT[k])
            continue
        return data, status, started, utc_now()


def load_manifest():
    p = os.path.join(RAW, "manifest.json")
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(man):
    p = os.path.join(RAW, "manifest.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, p)


def parse_rows(kind, js):
    """回傳 (fields, rows)。T86 是 fields/data；MI_INDEX 是 tables 裡有「證券代號」「成交股數」的那張。"""
    if kind.startswith("t86"):
        return js.get("fields") or [], js.get("data") or []
    for tb in js.get("tables") or []:
        f = [str(x).strip() for x in tb.get("fields") or []]
        if "證券代號" in f and "成交股數" in f and tb.get("data"):
            return f, tb["data"]
    if js.get("fields") and js.get("data"):     # 舊格式 fields9/data9 之類
        return js["fields"], js["data"]
    for k in sorted(js):
        if k.startswith("fields") and js.get("data" + k[6:]):
            f = [str(x).strip() for x in js[k]]
            if "證券代號" in f:
                return f, js["data" + k[6:]]
    return [], []


# ---------------------------------------------------------------- 開市日

def months(start, end):
    y, m = int(start[:4]), int(start[4:6])
    while (y, m) <= (int(end[:4]), int(end[4:6])):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def trading_days(today):
    cal_dir = os.path.join(RAW, "cal")
    os.makedirs(cal_dir, exist_ok=True)
    days = []
    cur = today[:6]
    for y, m in months(START, today):
        ym = "%d%02d" % (y, m)
        p = os.path.join(cal_dir, ym + ".json")
        if ym != cur and os.path.exists(p):
            data = open(p, "rb").read()
        else:
            data, status, _, _ = get("https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=%s01" % ym)
            time.sleep(PAUSE)
            if status != 200:
                raise Stop("開市日 %s HTTP %s" % (ym, status))
            if ym != cur:
                open(p, "wb").write(data)
        js = json.loads(data.decode("utf-8-sig"))
        for r in js.get("data") or []:
            yy, mm, dd = str(r[0]).strip().split("/")
            d = "%d%02d%02d" % (int(yy) + 1911, int(mm), int(dd))
            if START <= d < today:
                days.append(d)
    return sorted(set(days))


# ---------------------------------------------------------------- 抓

def fetch_one(man, kind, day):
    url = KINDS[kind] % day
    data, status, started, got = get(url)
    key = "%s/%s" % (kind, day)
    rec = dict(url=url, started=started, captured_at=got, http=status, bytes=len(data))
    if status != 200:
        rec["status"] = "http_error"
        man[key] = rec
        return False
    try:
        js = json.loads(data.decode("utf-8-sig"))
    except ValueError:
        rec["status"] = "bad_json"
        man[key] = rec
        return False
    fields, rows = parse_rows(kind, js)
    rec["stat"] = js.get("stat")
    rec["rows"] = len(rows)
    if js.get("stat") != "OK" or not rows:
        rec["status"] = "empty"
        man[key] = rec
        return False
    sha = hashlib.sha256(data).hexdigest()
    rel = os.path.join("raw", kind, "%s.%s.json.gz" % (day, sha[:16]))
    p = os.path.join(RAW, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        old = gzip.decompress(open(p, "rb").read())
        if hashlib.sha256(old).hexdigest() != sha:
            raise Stop("同名原始檔內容不同：%s" % p)
    else:
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(gzip.compress(data))
        os.replace(tmp, p)
    rec.update(status="ok", file=rel, sha256=sha, fields=len(fields))
    man[key] = rec
    return True


def run_fetch():
    t0 = time.time()
    today = dt.datetime.now(TW).strftime("%Y%m%d")
    man = load_manifest()
    days = trading_days(today)
    todo = [(k, d) for d in days for k in KINDS
            if man.get("%s/%s" % (k, d), {}).get("status") != "ok"]      # 開市日卻是空的 → 下次再試
    print("開市日 %d 天（%s～%s），要抓 %d 份" % (len(days), days[0], days[-1], len(todo)))
    done = fail = 0
    for i, (k, d) in enumerate(todo):
        if (time.time() - t0) / 60 > BUDGET_MIN:
            notice("權證：時間預算用完，已抓 %d 份、剩 %d 份，下次接著抓" % (done, len(todo) - i))
            break
        try:
            ok = fetch_one(man, k, d)
        except Stop as e:
            save_manifest(man)
            notice("權證：被擋或異常，整批停：%s（已抓 %d 份）" % (e, done))
            return 2
        save_manifest(man)
        done += ok
        fail += not ok
        if (i + 1) % 100 == 0:
            print("  %d／%d" % (i + 1, len(todo)))
        time.sleep(PAUSE)
    st = {}
    for v in man.values():
        st[v.get("status")] = st.get(v.get("status"), 0) + 1
    notice("權證抓取：本次成功 %d、失敗或空 %d；帳本 %s；開市日 %d 天" % (done, fail, st, len(days)))
    return 0


# ---------------------------------------------------------------- 整理成每日權證表

def num(s):
    s = str(s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def col(fields, *names):
    f = [str(x).strip() for x in fields]
    for n in names:
        if n in f:
            return f.index(n)
    return -1


def read_raw(man, kind, day):
    v = man.get("%s/%s" % (kind, day))
    if not v or v.get("status") != "ok":
        return None
    p = os.path.join(RAW, v["file"])
    data = gzip.decompress(open(p, "rb").read())
    if hashlib.sha256(data).hexdigest() != v["sha256"]:
        raise Stop("原始檔驗不過：%s" % p)
    return parse_rows(kind, json.loads(data.decode("utf-8-sig")))


def norm(s):
    return "".join(ch for ch in str(s) if ch not in " -*　").upper()


def stock_names():
    """data/market.txt 的上市股票與 ETF 名稱 → 代號（今天的名稱；改過名的對不上，會記在未對應）。"""
    names = {}
    p = os.path.join("data", "market.txt")
    with open(p, encoding="utf-8") as f:
        # 第一行是「TWLV1|日期|時間|筆數|code,name,mkt,…」：欄名在最後一個 | 之後
        hdr = next(f).strip().split("|")[-1].split(",")
        ic, iN, iM = hdr.index("code"), hdr.index("name"), hdr.index("mkt")
        for line in f:
            r = line.rstrip("\n").split(",")
            if len(r) <= max(ic, iN, iM) or r[iM] != "1":
                continue
            n = norm(r[iN])
            if len(n) >= 2:
                names.setdefault(n, r[ic])
                if n.endswith("KY") and len(n) >= 4:
                    names.setdefault(n[:-2], r[ic])
    return names


def underlying(wname, names, cache):
    w = norm(wname)
    if w in cache:
        return cache[w]
    best = ""
    for L in range(min(len(w), 12), 1, -1):
        if w[:L] in names:
            best = names[w[:L]]
            break
    cache[w] = best
    return best


def derive():
    man = load_manifest()
    names = stock_names()
    cache = {}
    out_dir = os.path.join(RAW, "derived")
    os.makedirs(out_dir, exist_ok=True)
    days = sorted({k.split("/")[1] for k in man})
    n_days = 0
    amt_all = amt_mapped = 0.0
    unmapped = {}
    chk = [0, 0]           # 官方標的代號與名稱比對都有的筆數、一致的筆數
    for d in days:
        rows = []
        complete = True
        for cp, tk, mk in (("C", "t86c", "mic"), ("P", "t86p", "mip")):
            t86, mi = read_raw(man, tk, d), read_raw(man, mk, d)
            if t86 is None or mi is None:
                complete = False
                continue
            f, data = t86
            iC, iN = col(f, "證券代號"), col(f, "證券名稱")
            iD = col(f, "自營商買賣超股數")
            if iD < 0:
                iS, iH = col(f, "自營商買賣超股數(自行買賣)"), col(f, "自營商買賣超股數(避險)")
            px = {}
            mf, mdata = mi
            jC, jV, jA, jX = col(mf, "證券代號"), col(mf, "成交股數"), col(mf, "成交金額"), col(mf, "收盤價")
            jU = col(mf, "標的代號")
            for r in mdata:
                px[str(r[jC]).strip()] = (num(r[jV]), num(r[jA]), num(r[jX]) if jX >= 0 else None,
                                          str(r[jU]).strip() if jU >= 0 else "")
            for r in data:
                code, name = str(r[iC]).strip(), str(r[iN]).strip()
                dn = num(r[iD]) if iD >= 0 else ((num(r[iS]) or 0) + (num(r[iH]) or 0))
                if dn is None:
                    continue
                vol, amt, close, uo = px.get(code, (None, None, None, ""))
                vwap = amt / vol if vol and amt else None
                nd = -dn
                est = nd * vwap if vwap else None
                un = underlying(name, names, cache)
                u = uo or un
                if uo and un:
                    chk[0] += 1
                    chk[1] += uo == un
                if amt:
                    amt_all += amt
                    if u:
                        amt_mapped += amt
                    else:
                        key = norm(name)[:4]
                        unmapped[key] = unmapped.get(key, 0) + amt
                rows.append([d, code, name, cp, u, int(nd), vol or "", amt or "", close or "",
                             "" if vwap is None else round(vwap, 4), "" if est is None else round(est)])
        if not rows:
            continue
        p = os.path.join(out_dir, d + ".csv")
        with open(p + ".tmp", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "warrant", "name", "cp", "underlying", "nondealer_net_sh", "vol_sh",
                        "amt_twd", "close", "vwap", "est_net_amt_twd"])
            w.writerows(rows)
        os.replace(p + ".tmp", p)
        if complete:
            n_days += 1
    top = sorted(unmapped.items(), key=lambda x: -x[1])[:15]
    notice("權證整理：完整日 %d 天；成交金額對到標的 %.1f%%；官方標的代號 vs 名稱比對 %d／%d 一致；未對應前幾名（名稱前 4 字：金額億）%s" % (
        n_days, 100 * amt_mapped / amt_all if amt_all else 0, chk[1], chk[0],
        ", ".join("%s:%.1f" % (k, v / 1e8) for k, v in top)))
    return n_days


# ---------------------------------------------------------------- 探測

PROBE_EXTRA = {
    "t86_rwd_0999": "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date=%s&selectType=0999",
    "t86_ALL": "https://www.twse.com.tw/fund/T86?response=json&date=%s&selectType=ALL",
    "mi_rwd_0999": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date=%s&type=0999",
}


def probe2(day):
    """第二輪探測：列出每張表的標題、欄位、筆數；T86 ALL 裡 6 碼權證的筆數。"""
    for k, u in list(KINDS.items())[2:] + list(PROBE_EXTRA.items()):
        data, status, _, _ = get(u % day, log=False)
        time.sleep(PAUSE)
        try:
            js = json.loads(data.decode("utf-8-sig"))
        except ValueError:
            notice("探測2 %s %s：HTTP %s 不是 JSON" % (k, day, status))
            continue
        if js.get("tables") is not None:
            desc = ["%s：%s 筆 %d" % (t.get("title"), t.get("fields"), len(t.get("data") or [])) for t in js["tables"]]
            notice("探測2 %s %s stat=%s 表 %d 張｜%s" % (k, day, js.get("stat"), len(js["tables"]), " ‖ ".join(desc)))
        else:
            rows = js.get("data") or []
            w = [r for r in rows if len(str(r[0]).strip()) == 6]
            notice("探測2 %s %s stat=%s 筆數 %d、6 碼 %d；鍵 %s；6 碼前兩筆 %s" % (
                k, day, js.get("stat"), len(rows), len(w), sorted(js)[:12], w[:2]))
    data, status, _, _ = get("https://openapi.twse.com.tw/v1/opendata/t187ap36_L", log=False)
    js = json.loads(data.decode("utf-8-sig"))
    ds = sorted(r.get("申請發行日期", "") for r in js)
    codes = {r.get("權證代號") for r in js}
    notice("t187ap36_L：%d 筆、權證代號 %d 個、申請發行日期 %s～%s；各年筆數 %s" % (
        len(js), len(codes), ds[0], ds[-1],
        sorted({d[:3]: sum(1 for x in ds if x[:3] == d[:3]) for d in ds}.items())))


def probe(day):
    for k in KINDS:
        data, status, _, _ = get(KINDS[k] % day, log=False)
        try:
            js = json.loads(data.decode("utf-8-sig"))
        except ValueError:
            notice("探測 %s %s：HTTP %s，不是 JSON（%d bytes）" % (k, day, status, len(data)))
            continue
        f, rows = parse_rows(k, js)
        notice("探測 %s %s：HTTP %s stat=%s 筆數 %d；欄位 %s；前兩筆 %s" % (
            k, day, status, js.get("stat"), len(rows), f, rows[:2]))
        time.sleep(PAUSE)
    try:
        data, status, _, _ = get("https://openapi.twse.com.tw/v1/swagger.json", log=False)
        sw = json.loads(data.decode("utf-8-sig"))
        hits = []
        for path, v in sw.get("paths", {}).items():
            s = json.dumps(v, ensure_ascii=False)
            if "權證" in s:
                summ = [x.get("summary") for x in v.values() if isinstance(x, dict)]
                hits.append((path, summ))
        notice("OpenAPI 權證端點：%s" % hits)
        for path, _ in hits[:8]:
            time.sleep(PAUSE)
            data, status, _, _ = get("https://openapi.twse.com.tw/v1" + path, log=False)
            try:
                js = json.loads(data.decode("utf-8-sig"))
                first = js[0] if isinstance(js, list) and js else js
                notice("OpenAPI %s：HTTP %s 筆數 %s；第一筆 %s" % (
                    path, status, len(js) if isinstance(js, list) else "-", first))
            except ValueError:
                notice("OpenAPI %s：HTTP %s 不是 JSON" % (path, status))
    except Stop as e:
        notice("OpenAPI 探測停止：%s" % e)


def main():
    a = sys.argv[1:]
    try:
        if a and a[0] == "--probe2":
            for d in a[1:] or ["20261022"]:
                probe2(d)
            return 0
        if a and a[0] == "--probe":
            probe(a[1] if len(a) > 1 else (dt.datetime.now(TW) - dt.timedelta(days=1)).strftime("%Y%m%d"))
            return 0
        if a and a[0] == "--derive-only":       # 不連網，只用已抓的資料
            return 0 if derive() > 0 else 4
        rc = run_fetch()
        derive()
        return rc
    except Stop as e:
        notice("權證：停止：%s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
