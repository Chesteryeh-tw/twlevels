#!/usr/bin/env python3
"""用官方日行情核對 SUS 歷史段的流動性母體（GPT 09 第 2 節；SUS 登記 v2.5 第 10 節）。不算 SUS、不看績效。

做法：按 SUS 兩版（11 日、16 日）每個營收月的「進場前一交易日」S（sus_study.historical_entry，官方市場日曆），
抓證交所 MI_INDEX（ALLBUT0999）與櫃買 dailyQuotes 當天全部行情，取 4 碼代號、成交金額 ≥ 1 億的名單（＝官方母體），
跟本地長期價量（data/stock，Actions 快取 finmind-hist）同一天成交金額 ≥ 1 億的名單比對。

輸出（只有代碼、日期、家數，可公開；官方原始回應只放 Actions 快取 data/uni_raw/）：
  early_check/universe_official.md、universe_official_dates.csv（每個 S 日的家數）、universe_official_missing.csv（官方有、本地沒有的代碼×日期）
抓取規矩：每次 4～7 秒隨機；401／403／407／429／451、轉址、回網頁 → 整批停（不換位址）；時間預算到了先停，下次接著抓。
"""
import csv, datetime as dt, gzip, json, os, random, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sus_study as S

RAW = os.path.join("data", "uni_raw")
OUTD = "early_check"
UA = "twlevels-research (official daily quotes for universe check; 4-7s apart)"
STOP = {401, 403, 407, 429, 451}
LIQ = 100_000_000          # 1 億元
BUDGET_MIN = float(os.environ.get("UNI_BUDGET_MIN", "50"))
FIRST_YM, LAST_YM = "202012", "202607"


class Stop(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def get(url):
    for wait in (10, 30, None):
        try:
            with OPENER.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=40) as r:
                b = r.read()
            if b"<html" in b[:300].lower() or b"<!doctype" in b[:300].lower():
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


def num(x):
    t = str(x).replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def tables(j):
    """新格式 tables[]；舊格式 fieldsN／dataN 也收。"""
    out = list(j.get("tables") or [])
    for k in list(j):
        if k.startswith("fields") and ("data" + k[6:]) in j:
            out.append(dict(fields=j[k], data=j["data" + k[6:]]))
    return out


def parse(body, market, ymd):
    """回傳 {代號: 成交金額（元）}；只收 4 碼代號（與本地價量管線相同）。日期對不上 → ValueError。"""
    j = json.loads(body.decode("utf-8-sig"))
    if market == "twse":
        if j.get("stat") != "OK":
            raise ValueError("TWSE stat=%s" % j.get("stat"))
        if str(j.get("date", ymd)) != ymd:
            raise ValueError("TWSE 回應日期 %s ≠ %s" % (j.get("date"), ymd))
    else:
        if str(j.get("stat", "")).lower() != "ok":
            raise ValueError("TPEx stat=%s" % j.get("stat"))
        if j.get("date") and str(j["date"]).replace("/", "") not in (ymd, str(int(ymd[:4]) - 1911) + ymd[4:]):
            raise ValueError("TPEx 回應日期 %s ≠ %s" % (j.get("date"), ymd))
    out = {}
    for tb in tables(j):
        f = [str(x).replace("<br>", "").strip() for x in tb.get("fields") or []]
        ic = next((i for i, x in enumerate(f) if x in ("證券代號", "代號")), None)
        iv = next((i for i, x in enumerate(f) if x.startswith("成交金額")), None)
        if ic is None or iv is None:
            continue
        for r in tb.get("data") or []:
            if len(r) != len(f):
                raise ValueError("%s %s 列長度與欄數不同" % (market, ymd))
            c = str(r[ic]).strip()
            v = num(r[iv])
            if len(c) == 4 and v is not None:
                out[c] = max(out.get(c, 0), v)
    if not out:
        raise ValueError("%s %s 沒有可用的行情表" % (market, ymd))
    return out


URL = dict(twse="https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={d}&type=ALLBUT0999",
           tpex="https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={s}&response=json")


def s_dates(days):
    out = {}
    ym = FIRST_YM
    while ym <= LAST_YM:
        for v in (11, 16):
            he = S.historical_entry(ym, v, days)
            if he:
                out.setdefault(he[1], []).append("%s_v%d" % (ym, v))
        ym = S.ym_add(ym, 1)
    return out


def fetch(dates):
    os.makedirs(RAW, exist_ok=True)
    t0 = time.time()
    for d in sorted(dates):
        for m in ("twse", "tpex"):
            p = os.path.join(RAW, "%s_%s.json.gz" % (m, d))
            if os.path.exists(p):
                continue
            if (time.time() - t0) / 60 > BUDGET_MIN:
                print("::notice::官方母體核對：時間到，下次接著抓（停在 %s %s）" % (m, d))
                return 3
            time.sleep(random.uniform(4, 7))
            url = URL[m].format(d=d, s="%s/%s/%s" % (d[:4], d[4:6], d[6:]))
            b = get(url)
            parse(b, m, d)                    # 驗過才存
            with gzip.open(p, "wb") as f:
                f.write(b)
    return 0


def build(dates, px, missing100):
    rows, miss, cover = [], [], 0
    hit100 = {}
    for d in sorted(dates):
        pt, pp = os.path.join(RAW, "twse_%s.json.gz" % d), os.path.join(RAW, "tpex_%s.json.gz" % d)
        if not (os.path.exists(pt) and os.path.exists(pp)):
            continue
        cover += 1
        off = {}
        for m, p in (("twse", pt), ("tpex", pp)):
            for c, v in parse(gzip.open(p).read(), m, d).items():
                off[c] = (m, v)
        o_liq = {c for c, (m, v) in off.items() if v >= LIQ}
        l_liq = {c for c, r in px.items() if d in r and S.fin(r[d][3]) and r[d][3] >= S.MIN_WAN}
        only_o = sorted(o_liq - l_liq)
        no_file = [c for c in only_o if c not in px]
        rows.append(dict(date=d, months="/".join(dates[d]), official_liquid=len(o_liq), local_liquid=len(l_liq),
                         official_only=len(only_o), official_only_no_local_file=len(no_file), local_only=len(l_liq - o_liq),
                         share_missing_pct=round(100 * len(only_o) / len(o_liq), 2) if o_liq else ""))
        for c in only_o:
            miss.append(dict(date=d, code=c, market=off[c][0], turnover=int(off[c][1]),
                             local_file="yes" if c in px else "no", in_missing100="yes" if c in missing100 else "no"))
        for c in o_liq & set(missing100):
            hit100.setdefault(c, []).append(d)
    return rows, miss, hit100, cover


def report(rows, miss, hit100, cover, n_dates, missing100):
    tot_o = sum(r["official_liquid"] for r in rows)
    tot_m = sum(r["official_only"] for r in rows)
    codes_m = sorted({m["code"] for m in miss})
    L = ["# 官方日行情核對：SUS 歷史段流動性母體（不算 SUS、不看績效）", "",
         "按 SUS 兩版每個營收月（%s～%s）的進場前一交易日，取官方 4 碼代號、成交金額 ≥ 1 億的名單，跟本地長期價量比。" % (FIRST_YM, LAST_YM),
         "已核 %d／%d 個日期（兩市場都抓到的才算）。" % (cover, n_dates), "",
         "| 指標 | 數值 | 意思 |", "|---|---:|---|",
         "| 官方母體（代號×日期） | %d | 各 S 日官方成交 ≥ 1 億的代號數加總 |" % tot_o,
         "| 本地價量沒有的（代號×日期） | %d（%.2f%%） | 官方母體裡、本地那天沒有或未達 1 億 |" % (tot_m, 100 * tot_m / tot_o if tot_o else 0),
         "| 涉及代號數 | %d | 其中本地完全沒有價量檔：%d |" % (len(codes_m), len({m["code"] for m in miss if m["local_file"] == "no"})),
         "| 本地有、官方沒有（代號×日期） | %d | 應為 0；不是 0 代表本地資料或口徑要查 |" % sum(r["local_only"] for r in rows), ""]
    L += ["## 07 的 100 個缺碼：在哪些 S 日進得了官方母體", "",
          "進得了官方母體的有 %d 個代碼（其餘 %d 個在核對日期內官方成交都未達 1 億或已不在市場）。" % (len(hit100), len(missing100) - len(hit100)), "",
          "| 代碼 | 達標的 S 日數 | 最早 | 最晚 |", "|---|---:|---|---|"]
    for c, ds in sorted(hit100.items(), key=lambda x: -len(x[1])):
        L.append("| %s | %d | %s | %s |" % (c, len(ds), min(ds), max(ds)))
    L += ["", "## 每個 S 日", "", "| S 日 | 營收月_版本 | 官方母體 | 本地母體 | 本地缺 | 缺的比例 |", "|---|---|---:|---:|---:|---:|"]
    for r in rows:
        L.append("| %s | %s | %d | %d | %d | %s%% |" % (r["date"], r["months"], r["official_liquid"], r["local_liquid"], r["official_only"], r["share_missing_pct"]))
    L += ["", "限制：只比成交金額門檻（進場前一交易日），不含進場日可成交與 37 月營收；4 碼代號與本地管線同口徑（含 4 碼 ETF）；"
          "官方成交金額與本地萬元欄四捨五入差可能讓門檻邊緣的少數代號不一致。缺的代號是「本地資料不足」，不是官方下市證明。"]
    return "\n".join(L) + "\n"


def main():
    days, _hol = S.load_official_calendar()
    if not days:
        print("::error::沒有官方市場日曆")
        sys.exit(1)
    dates = s_dates(days)
    missing100 = [r["code"] for r in csv.DictReader(open(os.path.join(OUTD, "missing_in_early_revenue_20171229.csv"), encoding="utf-8-sig"))]
    code = 0
    if "--no-fetch" not in sys.argv:
        try:
            code = fetch(dates)
        except Stop as e:
            print("::error::官方母體核對：被拒或轉址，整批停止（%s）；已抓的照常建表" % e)
            code = 2
    px = S.load_prices()
    rows, miss, hit100, cover = build(dates, px, missing100)
    os.makedirs(OUTD, exist_ok=True)
    for name, data in (("universe_official_dates.csv", rows), ("universe_official_missing.csv", miss)):
        with open(os.path.join(OUTD, name), "w", encoding="utf-8-sig", newline="") as f:
            if data:
                w = csv.DictWriter(f, fieldnames=list(data[0]))
                w.writeheader()
                w.writerows(data)
    md = report(rows, miss, hit100, cover, len(dates), missing100)
    open(os.path.join(OUTD, "universe_official.md"), "w", encoding="utf-8").write(md)
    tot_o = sum(r["official_liquid"] for r in rows)
    tot_m = sum(r["official_only"] for r in rows)
    print("::notice::官方母體核對：%d／%d 日；官方母體 %d、本地缺 %d（%.2f%%）；100 缺碼進得了官方母體 %d 個" % (
        cover, len(dates), tot_o, tot_m, 100 * tot_m / tot_o if tot_o else 0, len(hit100)))
    sys.exit(code)


if __name__ == "__main__":
    main()
