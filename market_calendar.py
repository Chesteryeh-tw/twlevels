#!/usr/bin/env python3
"""官方市場日曆 → data/market_calendar.json（SUS 前瞻決策與研究共用；GPT 06 B：用官方開休市日，不用「過半代號有價格」推算）。

- 過去：證交所 FMTQIK（每月市場成交資訊，一列＝一個交易日），2020-01 起，已抓且不是當月的月份不重抓。
- 未來：證交所 OpenAPI 休市日表 holidaySchedule。說明含「開始交易」「最後交易」的是交易日（不算休市）；其餘列為休市。
- 抓取間隔 4～7 秒隨機；401／403／407／429／451、轉址、回網頁 → 停（不換位址）。原始回應存 data/cal_raw/（Actions 快取 cal-raw-）。

- 臨時休市（GPT 08 P4）：年度休市表不含颱風等臨時停市。已核實的官方公告記在 data/market_closures.json
  （日期、原因、公告日、官方網址、原檔 SHA-256），併入休市日；臨時恢復開市則從休市日拿掉。
- 輸出 schedule_years（休市表涵蓋的年度）與 built_at；SUS 前瞻決策只在日曆 36 小時內更新過、而且涵蓋下一個市場日年度時才用。

用法：python market_calendar.py            全部（缺的月份才抓）
      python market_calendar.py --refresh  只重抓當月與休市日表
"""
import datetime as dt, gzip, hashlib, json, os, random, sys, time, urllib.error, urllib.request

RAW = os.path.join("data", "cal_raw")
OUT = os.path.join("data", "market_calendar.json")
CLOSURES = os.path.join("data", "market_closures.json")
VERIFIED = os.path.join(RAW, "verified.json")
# 各必要來源最後一次「成功抓到並解析」的時間（GPT 09 4.2）
SEED = os.path.join("data", "holiday_seed.json")      # v5：快取裡已被覆蓋、但之前成功核驗過的年度（附來源）


def hol_index_path():                                  # v5：休市表分檔的抓取時間、涵蓋年度、雜湊（跟著 RAW）
    return os.path.join(RAW, "holiday_index.json")


def _utcnow():
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_verified(key, extra=None):
    v = json.load(open(VERIFIED, encoding="utf-8")) if os.path.exists(VERIFIED) else {}
    v[key] = dict(at=_utcnow(), **(extra or {}))
    tmp = VERIFIED + ".tmp"
    json.dump(v, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, VERIFIED)
START = (2020, 1)
UA = "twlevels-research (official market calendar; monthly, 4-7s apart)"
STOP = {401, 403, 407, 429, 451}


class Stop(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def get(url):
    for wait in (10, 30, 60, None):
        try:
            with OPENER.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
                b = r.read()
            if b[:300].lower().find(b"<html") >= 0 or b[:300].lower().find(b"<!doctype") >= 0:
                raise Stop("回的是網頁：%s" % url)
            return b
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400 or e.code in STOP:
                raise Stop("HTTP %d：%s" % (e.code, url))
            if e.code < 500 or wait is None:
                raise
        except (urllib.error.URLError, OSError):
            if wait is None:
                raise
        time.sleep(wait)


def roc(s):
    s = str(s).strip().replace("年", "/").replace("月", "/").replace("日", "")
    if "/" in s:
        y, m, d = [int(x) for x in s.split("/")]
    else:
        y, m, d = int(s[:-4]), int(s[-4:-2]), int(s[-2:])
    return "%d%02d%02d" % (y + 1911, m, d)


def months(today):
    y, m, out = START[0], START[1], []
    while (y, m) <= (today.year, today.month):
        out.append("%d%02d" % (y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch(refresh_only=False, today=None):
    today = today or dt.date.today()
    os.makedirs(RAW, exist_ok=True)
    cur = today.strftime("%Y%m")
    for ym in months(today):
        p = os.path.join(RAW, "FMTQIK_%s.json.gz" % ym)
        if os.path.exists(p) and ym != cur:
            continue
        if refresh_only and ym != cur and os.path.exists(p):
            continue
        time.sleep(random.uniform(4, 7))
        b = get("https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=%s01" % ym)
        j = json.loads(b.decode("utf-8-sig"))
        if j.get("stat") != "OK" and ym != cur:
            raise Stop("FMTQIK %s stat=%s" % (ym, j.get("stat")))
        with gzip.open(p, "wb") as f:
            f.write(b)
        if ym == cur:
            mark_verified("FMTQIK_current", dict(month=ym))
    time.sleep(random.uniform(4, 7))
    b = get("https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule")
    rows = json.loads(b.decode("utf-8-sig"))
    yrs = sorted({roc(r.get("Date", ""))[:4] for r in rows})
    if not yrs:
        raise Stop("休市表沒有任何日期")
    # v5（2026-11-19）：每年 11 月證交所改公布下一年度休市表，舊做法整份覆蓋 → 當年度消失、決策停擺。
    # 改成依涵蓋年度分檔保存、不覆蓋其他年度；同年度再抓到就換成新的那份。
    name = "holidaySchedule_%s.json.gz" % "-".join(yrs)
    with gzip.open(os.path.join(RAW, name), "wb") as f:
        f.write(b)
    idx = json.load(open(hol_index_path(), encoding="utf-8")) if os.path.exists(hol_index_path()) else {}
    idx[name] = dict(at=_utcnow(), years=yrs, sha256=hashlib.sha256(b).hexdigest())
    tmp = hol_index_path() + ".tmp"
    json.dump(idx, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, hol_index_path())
    mark_verified("holidaySchedule", dict(years=yrs, file=name))


def build():
    trading, src = set(), {}
    for p in sorted(os.listdir(RAW)):
        if not p.startswith("FMTQIK_"):
            continue
        b = gzip.open(os.path.join(RAW, p)).read()
        src[p] = hashlib.sha256(b).hexdigest()
        j = json.loads(b.decode("utf-8-sig"))
        ym = p[7:13]
        for r in j.get("data") or []:
            d = roc(r[0])
            if d[:6] != ym:
                raise SystemExit("FMTQIK %s 有不屬於該月的日期 %s" % (ym, d))
            trading.add(d)
    hol, trade_marked = set(), set()
    # 每個年度用「最新抓到、涵蓋該年度」的那一份：分檔（依 holiday_index.json 的抓取時間）優先，
    # 其次舊版單一檔 holidaySchedule.json.gz，最後才用 data/holiday_seed.json（之前成功核驗的官方表，見檔內來源）。
    cands = []                                       # (排序鍵, 名稱, {年: [(日期, 是否交易日)]}, sha)
    idx = json.load(open(hol_index_path(), encoding="utf-8")) if os.path.exists(hol_index_path()) else {}

    def parse_rows(rows):
        by = {}
        for r in rows:
            d = roc(r.get("Date", ""))
            desc = (r.get("Name", "") or "") + (r.get("Description", "") or "")
            by.setdefault(d[:4], []).append((d, "開始交易" in desc or "最後交易" in desc))
        return by

    for p in sorted(os.listdir(RAW)):
        if p.startswith("holidaySchedule") and p.endswith(".json.gz"):
            b = gzip.open(os.path.join(RAW, p)).read()
            key = (2, idx[p]["at"]) if p in idx else (1, "")
            cands.append((key, p, parse_rows(json.loads(b.decode("utf-8-sig"))), hashlib.sha256(b).hexdigest()))
    if os.path.exists(SEED):
        sj = json.load(open(SEED, encoding="utf-8"))
        by = {}
        for y, v in sj["years"].items():
            by[y] = [(d, False) for d in v["holidays"]] + [(d, True) for d in v.get("trading_marked", [])]
        cands.append(((0, ""), "holiday_seed.json", by, hashlib.sha256(open(SEED, "rb").read()).hexdigest()))
    year_src = {}
    for key, name, by, sha in sorted(cands, key=lambda c: c[0], reverse=True):
        for y, rows in by.items():
            if y in year_src:
                continue
            year_src[y] = dict(file=name, sha256=sha)
            for d, is_trade in rows:
                (trade_marked if is_trade else hol).add(d)
            src["holidaySchedule:" + name] = sha
    years = sorted(year_src)
    closures, reopen, cl_src = set(), set(), []
    if os.path.exists(CLOSURES):
        b = open(CLOSURES, "rb").read()
        src["market_closures"] = hashlib.sha256(b).hexdigest()
        for r in json.loads(b.decode("utf-8")):
            if not (r.get("date") and r.get("source_url") and r.get("announced_at")):
                raise SystemExit("market_closures.json 有一筆缺日期、公告日或官方網址：%r" % r)
            (reopen if r.get("kind") == "reopen" else closures).add(r["date"])
            cl_src.append(r)
    hol = (hol | closures) - reopen
    conflict = sorted(d for d in hol if d in trading)
    out = dict(trading_days=sorted(trading), holidays=sorted(hol), closures=sorted(closures - reopen),
               closure_sources=cl_src, schedule_years=years, schedule_year_sources=year_src, schedule_trading_marked=sorted(trade_marked),
               conflicts_with_fmtqik=conflict, sources=src, built_at=_utcnow(),
               sources_verified_at={k: v.get("at") for k, v in (json.load(open(VERIFIED, encoding="utf-8"))
                                                                  if os.path.exists(VERIFIED) else {}).items()},
               note="built_at 只是重建時間；新鮮度看 sources_verified_at（必要來源最後一次成功抓到的時間）")
    tmp = OUT + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    os.replace(tmp, OUT)
    print("::notice::官方市場日曆：交易日 %d 天（%s～%s）、休市表 %d 天、與成交資訊衝突 %d 天" % (
        len(trading), min(trading) if trading else "-", max(trading) if trading else "-", len(hol), len(conflict)))
    return out


if __name__ == "__main__":
    try:
        fetch(refresh_only="--refresh" in sys.argv)
    except Stop as e:
        print("::error::市場日曆：%s；用已存的檔建表" % e)
        build()
        sys.exit(2)
    build()
