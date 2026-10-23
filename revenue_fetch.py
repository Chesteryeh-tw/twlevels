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
  python revenue_fetch.py --test         只抓 2021-01 上市、上櫃各一份，驗證後印出來，不寫檔
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
    """回傳 (原始 bytes, 資料列, 出表日期, 這次請求開始時間, 收完時間)；時間都是 UTC。"""
    last = None
    for k in range(len(RETRY_WAIT) + 1):
        started = utc_now()
        try:
            data = download(mkt, y, m)
            got = utc_now()
            body, gen = validate(data, mkt, y, m)
            return data, body, gen, started, got
        except Blocked:
            raise
        except urllib.error.HTTPError as e:
            if e.code in STOP_HTTP:
                raise Blocked("HTTP %d（對方拒絕或限流），整批停止" % e.code)
            last = e                # 5xx 等暫時錯誤才走下面的重試
            if k < len(RETRY_WAIT):
                print("    %s %d-%02d 第 %d 次失敗：HTTP %d，%d 秒後重試" % (mkt, y, m, k + 1, e.code, RETRY_WAIT[k]))
                time.sleep(RETRY_WAIT[k])
        except Exception as e:      # 連線錯誤、截斷、格式不對才重試
            last = e
            if k < len(RETRY_WAIT):
                print("    %s %d-%02d 第 %d 次失敗：%s，%d 秒後重試" % (mkt, y, m, k + 1, e, RETRY_WAIT[k]))
                time.sleep(RETRY_WAIT[k])
    raise Bad(str(last))


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


def save_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def check_inputs(man):
    """發布 panel 前重驗每一份標 ok 的原始檔：檔案在、大小與 SHA-256 跟 manifest 一樣、內容再驗一次。
    回傳 (合格清單 [(key, 資料列)], 問題清單)。有任何問題，呼叫的人就不能發布新 panel。"""
    good, problems = [], []
    for key in sorted(man):
        info = man[key]
        if not info.get("ok"):
            continue
        try:
            mkt, ym = key.split("_")
            y, m = int(ym[:4]), int(ym[4:])
        except ValueError:
            problems.append("%s：manifest 的鍵看不懂" % key)
            continue
        p = os.path.join(RAW, mkt, ym + ".csv")
        if not os.path.exists(p):
            problems.append("%s：manifest 說合格，但原始檔不見了" % key)
            continue
        data = open(p, "rb").read()
        if len(data) != info.get("bytes") or hashlib.sha256(data).hexdigest() != info.get("sha256"):
            problems.append("%s：原始檔大小或 SHA-256 跟 manifest 對不上" % key)
            continue
        try:
            body, _ = validate(data, mkt, y, m)
        except (Bad, Blocked) as e:
            problems.append("%s：原始檔重驗不合格：%s" % (key, e))
            continue
        good.append((key, body))
    return good, problems


def build_panel(man):
    """把所有驗過的原始檔合成一張表。每列帶來源鍵（原始檔 SHA-256、抓取時間、pit_verified），
    研究程式才追得回是哪一版。任何一份輸入有問題就不發布、保留上一份合格的 panel。"""
    good, problems = check_inputs(man)
    if problems:
        print("原始檔有 %d 個問題，不發布新的 panel.csv（保留上一份）：" % len(problems))
        for x in problems:
            print("  " + x)
        return False
    out = []
    for key, body in good:
        mkt, ym = key.split("_")
        info = man[key]
        for r in body:
            out.append([mkt, r[2].strip(), ym, num(r[5]), num(r[6]), num(r[7]), num(r[9]), num(r[8]),
                        r[0].strip(), info["sha256"], info.get("captured_at", ""), "false"])
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, "panel.csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["market", "code", "ym", "rev", "rev_prev", "rev_ly", "yoy_src", "mom_src", "generated",
                    "raw_sha256", "captured_at", "pit_verified"])
        w.writerows([["" if x is None else x for x in r] for r in out])
    os.replace(tmp, os.path.join(OUT, "panel.csv"))
    print("panel.csv：%d 列（實際驗過 %d 個市場月份）" % (len(out), len(good)))
    return True


def main():
    args = sys.argv[1:]
    if "--test" in args:
        for mkt in ("sii", "otc"):
            data, body, gen = fetch_one(mkt, 2021, 1)
            print("%s 2021-01：%d 列、%d bytes、出表日期 %s、SHA-256 %s"
                  % (mkt, len(body), len(data), gen, hashlib.sha256(data).hexdigest()[:16]))
            for r in body[:3]:
                print("   ", r[2], r[3], "當月", r[5], "去年當月", r[7], "年增%", r[9][:6])
            time.sleep(PAUSE)
        print("測試通過（沒有寫任何檔）")
        return
    try:
        man = load_manifest()
    except ManifestBroken as e:
        print("停止：%s。檔案保留不動，請人工檢查，不會自動整批重抓。" % e)
        sys.exit(3)
    if "--panel-only" in args:
        if not build_panel(man):
            sys.exit(4)
        return
    months = months_to_fetch()
    recent = set(months[-3:])           # 最近 3 個月可能還有更正，每次都重抓
    failed, blocked = [], None
    for (y, m) in months:
        for mkt in ("sii", "otc"):
            key = "%s_%d%02d" % (mkt, y, m)
            if man.get(key, {}).get("ok") and (y, m) not in recent:
                continue
            try:
                data, body, gen, started, got = fetch_one(mkt, y, m)
            except Blocked as e:
                blocked = "%s：%s" % (key, e)
                man.setdefault(key, {})["last_blocked"] = dict(at=utc_now(), error=str(e))
                break
            except Bad as e:
                failed.append("%s：%s" % (key, e))
                rec = man.setdefault(key, {})
                # 失敗不能抹掉之前合格的版本：只記錄這次失敗，ok 狀態照舊
                rec["last_error"] = dict(at=utc_now(), error=str(e))
                rec.setdefault("ok", False)
                time.sleep(PAUSE)
                continue
            sha = hashlib.sha256(data).hexdigest()
            p = os.path.join(RAW, mkt, "%d%02d.csv" % (y, m))
            old = man.get(key, {})
            hist = list(old.get("history", []))
            if old.get("ok") and old.get("sha256") != sha and os.path.exists(p):
                # 數字變了（更正）：舊版另存、記進 history（只增不改），不覆蓋成「以前就是這個數字」
                kept = p[:-4] + "." + old.get("captured_at", "old").replace(":", "").replace("-", "") + ".csv"
                os.replace(p, kept)
                hist.append(dict(sha256=old.get("sha256"), bytes=old.get("bytes"), rows=old.get("rows"),
                                 generated=old.get("generated"), captured_at=old.get("captured_at"),
                                 file=os.path.basename(kept), superseded_at=got))
                print("  %s 內容有變動，舊版另存" % key)
            save_atomic(p, data)
            # 同一份內容第一次被我們看到的時間（只證明我們何時看到，不是官方首次公布時間）
            if old.get("ok") and old.get("sha256") == sha:
                first = old.get("first_captured_at") or old.get("captured_at")
            else:
                first = got
            man[key] = dict(ok=True, sha256=sha, rows=len(body), bytes=len(data), generated=gen,
                            request_started_at=started, captured_at=got, first_captured_at=first,
                            pit_verified=False, history=hist)
            print("  %s：%d 列" % (key, len(body)))
            time.sleep(PAUSE)
        if blocked:
            break
    os.makedirs(RAW, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    json.dump(man, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MANIFEST)
    published = build_panel(man)
    ok = sum(1 for v in man.values() if v.get("ok"))
    print("完成：%d／%d 份合格" % (ok, len(months) * 2))
    if failed:
        print("失敗 %d 份：\n  " % len(failed) + "\n  ".join(failed))
    if blocked:
        print("被擋，已停止：" + blocked)
        sys.exit(2)
    if not published:
        sys.exit(4)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
