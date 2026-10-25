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
import csv, datetime as dt, glob, gzip, hashlib, io, json, os, random, re, sys, time
import urllib.error, urllib.request
from collections import defaultdict

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
        if len(j["tables"]) != 1:
            raise Bad("tables 有 %d 個" % len(j["tables"]))
        t = j["tables"][0]
        if t.get("totalCount") is not None and int(t["totalCount"]) != len(t["data"]):
            raise Bad("totalCount %s ≠ 列數 %d" % (t["totalCount"], len(t["data"])))
        return check_shape(t["fields"], t["data"])
    stat = str(j.get("stat", ""))
    if any(x in stat for x in NO_DATA):
        return [], []
    if stat != "OK":
        raise Bad("stat=%s" % stat)
    got = (j.get("strDate") or j.get("startDate") or (j.get("params") or {}).get("startDate"),
           j.get("endDate") or (j.get("params") or {}).get("endDate"))
    if got != (a8, b8):
        raise Bad("回應區間 %s ≠ %s~%s" % (got, a8, b8))
    return check_shape(j.get("fields", []), j.get("data", []))


def check_shape(fields, rows):
    """GPT 06 D：欄名不可重複、每列長度要等於欄數（不讓 zip 默默截斷）。"""
    if len(set(fields)) != len(fields):
        raise Bad("欄名重複")
    short = [i for i, r in enumerate(rows) if len(r) != len(fields)]
    if short:
        raise Bad("%d 列長度跟欄數 %d 不同（第一個在第 %d 列）" % (len(short), len(fields), short[0]))
    return fields, rows


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
                return 3
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


FIRST_SEEN = os.path.join("data", "corporate_actions_first_seen.json")
COVERAGE = os.path.join("data", "corporate_actions_coverage.json")
EX_TYPES = {"息": "ex_dividend", "除息": "ex_dividend", "權": "ex_right", "除權": "ex_right", "權息": "ex_right_dividend",
            "除權息": "ex_right_dividend"}


def build(today=None):
    """先列出全部「來源 × 月份」預期鍵，逐一驗：帳本 ok、原始檔存在、雜湊相符、欄位與列日期正確。
    - 七個來源都驗過的月份才算「涵蓋」（寫進 coverage）；缺抓、驗不過的月份列出來，SUS 前瞻遇到就當未解。
    - 有任何解析錯誤（bad）→ 不覆寫正式表，保留上一份合格版本，回非 0。
    - first_seen_at：每個事件「這一版內容」第一次出現在我們抓到的官方表的時間（抓取時間，UTC），存在 repo 的登錄檔，只增不改。
      GPT 08 P2：繫結列內容雜湊；同一事件（來源|代號|生效日）內容被官方修訂 → 另記新版本的 first_seen，不沿用舊時間。
      舊格式（只有時間、沒有雜湊）第一次重建時繫結當下內容（無法回溯舊內容，已知限制）。
    - 發布（GPT 08 P5a）：先換事件表、再換 first_seen、最後換 coverage；coverage 記前兩者的雜湊，是唯一的發布點。
      中途中斷時讀取端（sus_study.load_actions）會發現雜湊不符，整份當不可用，不會用到新舊混合的版本。
    - coverage.verified（P5b）：每個「來源_月份」的抓取時間；前瞻用來證明事件表已核對到出場日之後。"""
    man = load_man()
    out, bad, missing, ok_months = [], [], [], defaultdict(set)
    seen = json.load(open(FIRST_SEEN, encoding="utf-8")) if os.path.exists(FIRST_SEEN) else {}
    seen = {k: ([dict(row_sha256=None, first_seen_at=v)] if isinstance(v, str) else v) for k, v in seen.items()}
    verified = {}
    exp = ["%d%02d" % ym for ym in months(today)]
    for src, mkt, url, fmt, code_f, date_f, kind in SOURCES:
        for ym in exp:
            key = "%s_%s" % (src, ym)
            info = man.get(key, {})
            p = os.path.join(RAW, src, ym + ".json.gz")
            if not info.get("ok") or not os.path.exists(p):
                missing.append(key)
                continue
            body = gzip.open(p).read()
            if hashlib.sha256(body).hexdigest() != info.get("sha256"):
                bad.append("%s 雜湊跟帳本對不上" % key)
                continue
            y, m = int(ym[:4]), int(ym[4:])
            _a, _b, a8, b8 = window(y, m, fmt)
            try:
                fields, rows = parse(src, body, a8, b8)
            except (Bad, Stop, ValueError) as e:
                bad.append("%s 解析失敗：%s" % (key, e))
                continue
            if rows and (code_f not in fields or date_f not in fields):
                bad.append("%s 欄位缺 %s／%s" % (key, code_f, date_f))
                continue
            row_bad = False
            for r in rows:
                d = dict(zip(fields, [str(x).strip() for x in r]))
                eff = roc_date(d[date_f])
                if not eff or not (a8 <= eff <= b8):
                    bad.append("%s 日期 %s 不在區間" % (key, d[date_f]))
                    row_bad = True
                    continue
                typ, cash = kind, 0
                if kind == "ex":
                    v = d.get("權/息", "")
                    if v not in EX_TYPES:
                        bad.append("%s 權/息 值認不得：%r（%s）" % (key, v, d[code_f]))
                        row_bad = True
                        continue
                    typ = EX_TYPES[v]
                    cash = 1 if typ == "ex_dividend" else 0
                elif kind == "etf_split":
                    typ = "etf_reverse_split" if "反" in d.get("分割(反分割)", "") else "etf_split"
                prev = next((d[k] for k in fields if "前收盤" in k or "最後交易日之收盤" in k or "停止買賣前收盤" in k), "")
                ref = next((d[k] for k in fields if "參考價" in k and "減除股利" not in k and "除權參考價" != k), "")
                ev = "%s|%s|%s" % (src, d[code_f], eff)
                rs = hashlib.sha256(json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                vers = seen.setdefault(ev, [])
                hitv = next((x for x in vers if x.get("row_sha256") == rs), None)
                if hitv is None and len(vers) == 1 and vers[0].get("row_sha256") is None:
                    hitv = vers[0]
                    hitv["row_sha256"] = rs              # 舊格式：繫結當下內容
                if hitv is None:
                    hitv = dict(row_sha256=rs, first_seen_at=info.get("at", ""))
                    vers.append(hitv)
                out.append(dict(event_id=ev, code=d[code_f], effective_date=eff, type=typ, announce_at="",
                                first_seen_at=hitv["first_seen_at"], cash_only=cash, market=mkt, source=src, prev_close=prev,
                                ref_price=ref, reason=d.get("減資原因", ""), raw_sha256=info["sha256"],
                                source_row=json.dumps(d, ensure_ascii=False)))
            if not row_bad:
                ok_months[ym].add(src)
                verified[key] = info.get("at", "")
    covered = sorted(ym for ym in exp if len(ok_months[ym]) == len(SOURCES))
    cov = dict(months=covered, missing=missing, bad=bad[:200], sources=[s_[0] for s_ in SOURCES],
               verified=dict(sorted(verified.items())),
               note="first_seen_at 是抓到官方結果表的時間；結果表多在生效日當天或之後才有，前瞻用不到事前已知（需另抓預告表）。")
    print("::notice::公司行動表：%d 筆；涵蓋 %d／%d 個月；缺 %d 個來源月；問題 %d 個" % (
        len(out), len(covered), len(exp), len(missing), len(bad)))
    for x in bad[:20]:
        print("  " + x)
    if bad:
        print("::error::公司行動表有 %d 個問題，不覆寫正式表（保留上一份合格版本）" % len(bad))
        json.dump(cov, open(COVERAGE + ".failed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return out, bad
    out.sort(key=lambda r: (r["effective_date"], r["code"], r["source"]))
    cols = ["event_id", "code", "effective_date", "type", "announce_at", "first_seen_at", "cash_only", "market", "source",
            "prev_close", "ref_price", "reason", "raw_sha256", "source_row"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\r\n")
    w.writeheader()
    w.writerows(out)
    csv_b = buf.getvalue().encode("utf-8")
    seen_b = json.dumps(seen, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8")
    cov.update(csv_sha256=hashlib.sha256(csv_b).hexdigest(), first_seen_sha256=hashlib.sha256(seen_b).hexdigest(),
               generation=dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"))
    cov_b = json.dumps(cov, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8")
    for pth, b in ((OUT, csv_b), (FIRST_SEEN, seen_b), (COVERAGE, cov_b)):    # coverage 最後：發布點
        t2 = pth + ".tmp"
        with open(t2, "wb") as f:
            f.write(b)
        os.replace(t2, pth)
    return out, bad


def gaps(root=os.path.join("data", "stock"), th=11.0):
    ev = []
    for p in sorted(glob.glob(os.path.join(root, "*.txt"))):
        code, prev, pdate = os.path.basename(p)[:-4], None, None
        for line in open(p, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            r = line.strip().split(",")
            try:
                d, o, c = r[0], float(r[1]), float(r[4])
            except (ValueError, IndexError):
                continue
            if prev and o > 0 and abs(o / prev * 100 - 100) > th:
                ev.append((code, d, prev, o, (o / prev - 1) * 100, pdate))
            prev, pdate = c, d
    return ev


def match(rows, ev):
    """斷點 (code, 日期 d, 前收, 開盤, 跳動%, 前一筆有效股價日 p)：官方生效日 e 落在 p < e ≤ d 就算對上
    （停牌或當天無成交時，第一筆價格會晚於官方恢復買賣日；GPT 10-25 建議）。"""
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    hit, miss = [], []
    for e in ev:
        rs = [r for r in by_code.get(e[0], []) if (e[5] or "") < r["effective_date"] <= e[1]]
        (hit if rs else miss).append((e, rs))
    kinds = {}
    for _e, rs in hit:
        k = "/".join(sorted({r["type"] for r in rs}))
        kinds[k] = kinds.get(k, 0) + 1
    exact = sum(1 for e, rs in hit if any(r["effective_date"] == e[1] for r in rs))
    L = ["# 價格斷點 × 官方公司行動表", "",
         "價格斷點＝repo data/stock（未還原）開盤比前一天收盤差超過 11%%，共 %d 筆；對上官方事件 %d 筆（生效日＝斷點日 %d 筆，"
         "生效日落在前一筆有效股價日之後、斷點日之前 %d 筆），對不上 %d 筆。" % (len(ev), len(hit), exact, len(hit) - exact, len(miss)),
         "", "對上的類型：" + "、".join("%s %d" % kv for kv in sorted(kinds.items())), "",
         "## 對不上的（不猜原因，待查）", "| 代號 | 日期 | 前一筆股價日 | 前收 | 開盤 | 跳動 % |", "|---|---|---|---|---|---|"]
    L += ["| %s | %s | %s | %.2f | %.2f | %+.1f |" % (e[0], e[1], e[5], e[2], e[3], e[4]) for e, _ in miss]
    open(MATCH, "w", encoding="utf-8").write("\n".join(L) + "\n")
    with open("corporate_actions_gaps.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "date", "prev_price_date", "prev_close", "open", "gap_pct", "matched", "event_types", "effective_dates", "sources"])
        for e, rs in hit + miss:
            w.writerow([e[0], e[1], e[5], e[2], e[3], "%.1f" % e[4], int(bool(rs)), "/".join(sorted({r["type"] for r in rs})),
                        "/".join(sorted({r["effective_date"] for r in rs})), "/".join(sorted({r["source"] for r in rs}))])
    print("::notice::價格斷點 %d 筆：對上官方事件 %d（其中生效日早於斷點日 %d）、對不上 %d" % (len(ev), len(hit), len(hit) - exact, len(miss)))
    return hit, miss


if __name__ == "__main__":
    code = 0
    if "--build" not in sys.argv:
        code = fetch()
    rows, bad = build()
    if not bad:
        match(rows, gaps())
    sys.exit(code or (4 if bad else 0))
