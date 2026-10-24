#!/usr/bin/env python3
"""公司行動事件表（證交所四表＋櫃買三表，官方 JSON）→ data/corporate_actions.csv。

來源與欄位：GPT 2026-10-24 實查（證交所 TWT49U、TWTAUU〔路徑拼法 reducation〕、TWTB8U、TWTCAU）；
Claude 2026-10-25 實查（櫃買 exDailyQ、revivt、pvChgRslt，參數 startDate/endDate＝YYYY/MM/DD）。
- 一次查一個月；回傳的日期區間要對上請求；欄位一律照欄名取，不用固定位置。
- 抓取間隔 4～7 秒隨機。401／403／407／429／451、3xx 轉址、回網頁 → 整批停（不換位址、不偽裝）；5xx 等 10／30／60 秒重試。
- 原始回應 gzip 存 data/corp_raw/<來源>/<YYYYMM>.json.gz（雜湊進 manifest，不進 repo），已抓過且驗過的月份不重抓（最近 2 個月除外）。
- 總表沒有公告時間 → announce_at 留空（SUS 登記：歷史段標 announce_unknown，事後資料品質排除）。
- 純除息（權/息＝息、除息）cash_only＝1；換股率等細節只保留原文，不從參考價反推。

用法：python corporate_actions.py            抓＋建表＋比對價格斷點
      python corporate_actions.py --build    不抓，只用已存的原始檔建表與比對
"""
import csv, datetime as dt, glob, gzip, hashlib, json, os, random, re, sys, time
import urllib.error, urllib.request

RAW = os.path.join("data", "corp_raw")
MAN = os.path.join(RAW, "manifest.json")
OUT = os.path.join("data", "corporate_actions.csv")
MATCH = "corporate_actions_match.md"
START = (2021, 1)
BUDGET_MIN = float(os.environ.get("CORP_BUDGET_MIN", "80"))
STOP_HTTP = {401, 403, 407, 429, 451}
UA = "twlevels-research (official corporate-action tables; monthly windows, 4-7s apart)"

# (來源鍵, 市場, 網址, 日期格式, 代號欄, 日期欄, 類型)
SOURCES = [
    ("TWT49U", "twse", "https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={a}&endDate={b}&response=json", "ymd", "股票代號", "資料日期", "ex"),
    ("TWTAUU", "twse", "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU?startDate={a}&endDate={b}&response=json", "ymd", "股票代號", "恢復買賣日期", "capital_reduction"),
    ("TWTB8U", "twse", "https://www.twse.com.tw/rwd/zh/change/TWTB8U?startDate={a}&endDate={b}&response=json", "ymd", "股票代號", "恢復買賣日期", "par_change"),
    ("TWTCAU", "twse", "https://www.twse.com.tw/rwd/zh/split/TWTCAU?startDate={a}&endDate={b}&response=json", "ymd", "ETF代號", "恢復買賣日期", "etf_split"),
    ("exDailyQ", "tpex", "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={a}&endDate={b}", "slash", "代號", "除權息日期", "ex"),
    ("revivt", "tpex", "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate={a}&endDate={b}", "slash", "股票代號", "恢復買賣日期", "capital_reduction"),
    ("pvChgRslt", "tpex", "https://www.tpex.org.tw/www/zh-tw/bulletin/pvChgRslt?startDate={a}&endDate={b}", "slash", "證券代號", "恢復買賣日期", "par_change"),
]
NO_DATA = ("很抱歉，沒有符合條件的資料", "查詢無資料")


class Stop(Exception):
    pass


class Bad(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                      # 轉址不跟：當成對方拒絕


OPENER = urllib.request.build_opener(NoRedirect)


def months(today=None):
    today = today or dt.date.today()
    y, m = START
    out = []
    while (y, m) <= (today.year, today.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def window(y, m, fmt):
    a = dt.date(y, m, 1)
    b = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
    f = "%Y%m%d" if fmt == "ymd" else "%Y/%m/%d"
    return a.strftime(f), b.strftime(f), a.strftime("%Y%m%d"), b.strftime("%Y%m%d")


def roc_date(s):
    """'115年09月02日'、'114/10/20'、'1141111' → '20260902' 等；認不得回 None。"""
    s = str(s).strip()
    m = re.match(r"^(\d{2,3})\D(\d{1,2})\D(\d{1,2})\D?$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif re.match(r"^\d{7}$", s):
        y, mo, d = int(s[:3]), int(s[3:5]), int(s[5:])
    else:
        return None
    try:
        return dt.date(y + 1911, mo, d).strftime("%Y%m%d")
    except ValueError:
        return None


def load_man():
    if not os.path.exists(MAN):
        return {}
    return json.load(open(MAN, encoding="utf-8"))


def save_man(man):
    os.makedirs(RAW, exist_ok=True)
    tmp = MAN + ".tmp"
    json.dump(man, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MAN)


def get(url):
    for k, wait in enumerate((10, 30, 60, None)):
        try:
            with OPENER.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400 or e.code in STOP_HTTP:
                raise Stop("HTTP %d：%s" % (e.code, url))
            if e.code < 500 or wait is None:
                raise Bad("HTTP %d" % e.code)
        except (urllib.error.URLError, OSError) as e:
            if wait is None:
                raise Bad(str(e))
        time.sleep(wait)


def parse(src, body, a8, b8):
    """回傳 (fields, rows)；驗日期區間；沒資料的官方回應回 ([], [])。"""
    head = body[:300].lower()
    if b"<html" in head or b"<!doctype" in head:
        raise Stop("回的是網頁（%s）" % src)
    j = json.loads(body.decode("utf-8"))
    if "tables" in j:                                         # 櫃買
        if str(j.get("stat", "")).lower() != "ok":
            raise Bad("stat=%s" % j.get("stat"))
        if j.get("date") != "%s~%s" % (a8, b8):
            raise Bad("回應區間 %s ≠ %s~%s" % (j.get("date"), a8, b8))
        t = j["tables"][0]
        return t["fields"], t["data"]
    stat = str(j.get("stat", ""))
    if any(x in stat for x in NO_DATA):
        return [], []
    if stat != "OK":
        raise Bad("stat=%s" % stat)
    got = (j.get("strDate") or j.get("startDate") or (j.get("params") or {}).get("startDate"),
           j.get("endDate") or (j.get("params") or {}).get("endDate"))
    if got != (a8, b8):
        raise Bad("回應區間 %s ≠ %s~%s" % (got, a8, b8))
    return j.get("fields", []), j.get("data", [])


def fetch(today=None):
    man = load_man()
    t0 = time.time()
    ms = months(today)
    recent = set(ms[-2:])
    for (y, m) in ms:
        for src, mkt, url, fmt, *_ in SOURCES:
            key = "%s_%d%02d" % (src, y, m)
            if man.get(key, {}).get("ok") and (y, m) not in recent:
                continue
            if (time.time() - t0) / 60 > BUDGET_MIN:
                print("::notice::公司行動：時間到，下次接著抓（停在 %s）" % key)
                save_man(man)
                return 0
            a, b, a8, b8 = window(y, m, fmt)
            time.sleep(random.uniform(4, 7))
            try:
                body = get(url.format(a=a, b=b))
                fields, rows = parse(src, body, a8, b8)
            except Stop as e:
                save_man(man)
                print("::error::公司行動：被拒或轉址，整批停止（%s）" % e)
                return 2
            except (Bad, ValueError) as e:
                man[key] = dict(ok=False, error=str(e), at=dt.datetime.utcnow().isoformat() + "Z")
                print("  %s 失敗：%s" % (key, e))
                continue
            sha = hashlib.sha256(body).hexdigest()
            p = os.path.join(RAW, src, "%d%02d.json.gz" % (y, m))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with gzip.open(p, "wb") as f:
                f.write(body)
            man[key] = dict(ok=True, sha256=sha, rows=len(rows), at=dt.datetime.utcnow().isoformat() + "Z")
            save_man(man)
            print("  %s：%d 列" % (key, len(rows)))
    save_man(man)
    return 0


def build():
    man = load_man()
    out, bad = [], []
    for src, mkt, url, fmt, code_f, date_f, kind in SOURCES:
        for p in sorted(glob.glob(os.path.join(RAW, src, "*.json.gz"))):
            ym = os.path.basename(p)[:6]
            info = man.get("%s_%s" % (src, ym), {})
            body = gzip.open(p).read()
            if not info.get("ok") or hashlib.sha256(body).hexdigest() != info.get("sha256"):
                bad.append("%s_%s 雜湊或帳本對不上" % (src, ym))
                continue
            y, m = int(ym[:4]), int(ym[4:])
            _a, _b, a8, b8 = window(y, m, fmt)
            fields, rows = parse(src, body, a8, b8)
            if rows and (code_f not in fields or date_f not in fields):
                bad.append("%s_%s 欄位缺 %s／%s" % (src, ym, code_f, date_f))
                continue
            for r in rows:
                d = dict(zip(fields, [str(x).strip() for x in r]))
                eff = roc_date(d[date_f])
                if not eff or not (a8 <= eff <= b8):
                    bad.append("%s_%s 日期 %s 不在區間" % (src, ym, d[date_f]))
                    continue
                typ, cash = kind, 0
                if kind == "ex":
                    v = d.get("權/息", "")
                    typ = {"息": "ex_dividend", "除息": "ex_dividend", "權": "ex_right", "除權": "ex_right"}.get(v, "ex_right_dividend")
                    cash = 1 if typ == "ex_dividend" else 0
                elif kind == "etf_split":
                    typ = "etf_reverse_split" if "反" in d.get("分割(反分割)", "") else "etf_split"
                prev = next((d[k] for k in fields if "前收盤" in k or "最後交易日之收盤" in k or "停止買賣前收盤" in k), "")
                ref = next((d[k] for k in fields if "參考價" in k and "減除股利" not in k and "除權參考價" != k), "")
                out.append(dict(code=d[code_f], effective_date=eff, type=typ, announce_at="", cash_only=cash, market=mkt,
                                source=src, prev_close=prev, ref_price=ref, reason=d.get("減資原因", ""),
                                raw_sha256=info["sha256"], source_row=json.dumps(d, ensure_ascii=False)))
    out.sort(key=lambda r: (r["effective_date"], r["code"], r["source"]))
    cols = ["code", "effective_date", "type", "announce_at", "cash_only", "market", "source", "prev_close", "ref_price",
            "reason", "raw_sha256", "source_row"]
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    os.replace(tmp, OUT)
    print("::notice::公司行動表：%d 筆（%s）；問題 %d 個" % (len(out), ", ".join(
        "%s %d" % (s[0], sum(1 for r in out if r["source"] == s[0])) for s in SOURCES), len(bad)))
    for x in bad[:20]:
        print("  " + x)
    return out, bad


def gaps(root=os.path.join("data", "stock"), th=11.0):
    ev = []
    for p in sorted(glob.glob(os.path.join(root, "*.txt"))):
        code, prev = os.path.basename(p)[:-4], None
        for line in open(p, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            r = line.strip().split(",")
            try:
                d, o, c = r[0], float(r[1]), float(r[4])
            except (ValueError, IndexError):
                continue
            if prev and o > 0 and abs(o / prev * 100 - 100) > th:
                ev.append((code, d, prev, o, (o / prev - 1) * 100))
            prev = c
    return ev


def match(rows, ev):
    idx = {}
    for r in rows:
        idx.setdefault((r["code"], r["effective_date"]), []).append(r)
    hit, miss = [], []
    for e in ev:
        rs = idx.get((e[0], e[1]))
        (hit if rs else miss).append((e, rs))
    kinds = {}
    for _e, rs in hit:
        k = "/".join(sorted({r["type"] for r in rs}))
        kinds[k] = kinds.get(k, 0) + 1
    L = ["# 價格斷點 × 官方公司行動表", "",
         "價格斷點＝repo data/stock（未還原）開盤比前一天收盤差超過 11%%，共 %d 筆；對上官方事件 %d 筆，對不上 %d 筆。" % (len(ev), len(hit), len(miss)),
         "", "對上的類型：" + "、".join("%s %d" % kv for kv in sorted(kinds.items())), "",
         "## 對不上的（不猜原因，待查）", "| 代號 | 日期 | 前收 | 開盤 | 跳動 % |", "|---|---|---|---|---|"]
    L += ["| %s | %s | %.2f | %.2f | %+.1f |" % e for e, _ in miss]
    open(MATCH, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("::notice::價格斷點 %d 筆：對上官方事件 %d、對不上 %d" % (len(ev), len(hit), len(miss)))
    return hit, miss


if __name__ == "__main__":
    code = 0
    if "--build" not in sys.argv:
        code = fetch()
    rows, _bad = build()
    match(rows, gaps())
    sys.exit(code)
