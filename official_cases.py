#!/usr/bin/env python3
"""GPT 固定抽核 6 案例（2026-10-23）的官方日成交重建資料。只在 GitHub Actions 手動跑，結果只放下載區、不 commit。

來源（官方、公開）：
  上市：https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMM01&stockNo=代號
        成交股數（股）、成交金額（元）、開高低收（元）
  上櫃：https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=代號&date=YYYY/MM/01&response=json
        成交張數（千股）、成交金額（千元）、開高低收（元）
  市場開市日：https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=YYYYMM01
每個案例抓訊號月往前 14 個月到往後 3 個月；每個月上市、上櫃兩邊都查（事件當時在哪個市場要用官方回應判斷，不用今天的名單）。
規則：遇到 HTTP 401／403／407／429／451 或回應是網頁 → 整批停，不重試、不換來源或身分。每次請求間隔 3 秒。
原始回應逐份存檔＋SHA-256＋請求開始／收完時間（UTC）。價格不還原（跟回測用的原始價一致）；除權息、停牌只記錄、不處理。

輸出（official_cases/）：
  raw/*.json             原始回應 bytes
  source_snapshot.csv    每份請求：網址、參數、時間、狀態、bytes、SHA-256、資料列數
  daily_ohlcv.csv        正規化日線（market, code, trade_date, open, high, low, close, volume_shares, value_yuan, source_sha256）
  calendar.csv           證交所開市日
  case_manifest.csv      6 案例
  feature_check.csv      用官方資料照原程式公式重算 ②（距年線）與 ⑤（三線壓力）的值，給 GPT 跟原輸出比
"""
import csv, datetime as dt, hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request

OUT = "official_cases"
PAUSE = 3
STOP_HTTP = {401, 403, 407, 429, 451}
UA = "twlevels-official-case-audit (GitHub Actions; manual, 6 stocks)"
# GPT 2026-10-23 v2 第 7 節：依 SHA-256 選樣，不看結果
CASES = [
    ("②-1", "u_reg2", "3680", "20220420", "20220421", "000260d04e6c"),
    ("②-2", "u_reg2", "3227", "20221219", "20221220", "0007ac3dce7d"),
    ("②-3", "u_reg2", "3006", "20240424", "20240425", "001b2f562877"),
    ("⑤-1", "f_rfail_dn", "3013", "20231102", "20231103", "000225d0dc18"),
    ("⑤-2", "f_rfail_dn", "3260", "20220516", "20220517", "0002ef203be4"),
    ("⑤-3", "f_rfail_dn", "2412", "20230926", "20230927", "000366e6d383"),
]


class Stop(Exception):
    pass


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def months_around(sig, before=14, after=3):
    y, m = int(sig[:4]), int(sig[4:6])
    out = []
    for k in range(-before, after + 1):
        mm = m + k
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append((yy, mm))
    return out


def get(url):
    """5xx（例如 2026-10-23 遇到的 520）是暫時錯誤：等 10／30／60 秒重試，最多 4 次；其餘照舊。"""
    for wait in (10, 30, 60, None):
        started = utc_now()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            if e.code in STOP_HTTP:
                raise Stop("HTTP %d：%s" % (e.code, url))
            data, status = e.read() or b"", e.code
        if status < 500 or wait is None:
            break
        print("    HTTP %d，%d 秒後重試：%s" % (status, wait, url))
        time.sleep(wait)
    got = utc_now()
    head = data[:500].lower()
    if b"<html" in head or b"<!doctype" in head or b"captcha" in head:
        raise Stop("回應是網頁（可能被擋）：%s" % url)
    return data, status, started, got


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def roc_date(s):
    y, m, d = s.strip().split("/")
    return "%d%02d%02d" % (int(y) + 1911, int(m), int(d))


def main():
    os.makedirs(os.path.join(OUT, "raw"), exist_ok=True)
    snaps, daily, cal = [], [], set()
    jobs = []
    for case in CASES:
        for (y, m) in months_around(case[3]):
            jobs.append(("twse", case[2], y, m))
            jobs.append(("tpex", case[2], y, m))
    cal_months = sorted({(y, m) for _, _, y, m in jobs})
    for (y, m) in cal_months:
        jobs.append(("cal", "", y, m))
    seen = set()
    try:
        for mkt, code, y, m in jobs:
            if (mkt, code, y, m) in seen:
                continue
            seen.add((mkt, code, y, m))
            if mkt == "twse":
                url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY?" + urllib.parse.urlencode(
                    dict(response="json", date="%d%02d01" % (y, m), stockNo=code))
            elif mkt == "tpex":
                url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?" + urllib.parse.urlencode(
                    dict(code=code, date="%d/%02d/01" % (y, m), response="json"))
            else:
                url = "https://www.twse.com.tw/exchangeReport/FMTQIK?" + urllib.parse.urlencode(
                    dict(response="json", date="%d%02d01" % (y, m)))
            data, status, started, got = get(url)
            sha = hashlib.sha256(data).hexdigest()
            name = "%s_%s_%d%02d.json" % (mkt, code or "market", y, m)
            open(os.path.join(OUT, "raw", name), "wb").write(data)
            rows = []
            try:
                js = json.loads(data.decode("utf-8"))
                if mkt == "tpex":
                    rows = (js.get("tables") or [{}])[0].get("data") or []
                else:
                    rows = js.get("data") or []
            except (ValueError, UnicodeDecodeError):
                pass
            snaps.append([name, url, started, got, status, len(data), sha, len(rows)])
            for r in rows:
                try:
                    d = roc_date(r[0])
                except (ValueError, IndexError):
                    continue
                if mkt == "cal":
                    cal.add(d)
                elif mkt == "twse":     # 日期, 成交股數, 成交金額, 開, 高, 低, 收, 漲跌, 筆數
                    daily.append(["twse", code, d, num(r[3]), num(r[4]), num(r[5]), num(r[6]),
                                  num(r[1]), num(r[2]), sha])
                else:                   # 日期, 成交張數(千股), 成交金額(千元), 開, 高, 低, 收, 漲跌, 筆數
                    v, a = num(r[1]), num(r[2])
                    daily.append(["tpex", code, d, num(r[3]), num(r[4]), num(r[5]), num(r[6]),
                                  None if v is None else v * 1000, None if a is None else a * 1000, sha])
            print("%-30s HTTP %s  %6d bytes  %3d 列" % (name, status, len(data), len(rows)))
            time.sleep(PAUSE)
    except Stop as e:
        print("被擋，整批停止：%s" % e)
        write(snaps, daily, cal)
        sys.exit(2)
    write(snaps, daily, cal)


def write(snaps, daily, cal):
    def w(name, header, rows):
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as f:
            c = csv.writer(f)
            c.writerow(header)
            c.writerows([["" if x is None else x for x in r] for r in rows])
    w("source_snapshot.csv", ["file", "url", "request_started_at_utc", "captured_at_utc", "http_status",
                              "bytes", "sha256", "data_rows"], snaps)
    daily.sort(key=lambda r: (r[1], r[2], r[0]))
    w("daily_ohlcv.csv", ["market", "code", "trade_date", "open", "high", "low", "close",
                          "volume_shares", "value_yuan", "source_sha256"], daily)
    w("calendar.csv", ["trade_date"], [[d] for d in sorted(cal)])
    w("case_manifest.csv", ["case_id", "flag", "code", "signal_date", "exported_entry_date", "selection_hash12"],
      CASES)
    # 照原程式公式重算（只用官方原始價；索引以「該股有成交的日線」為準，跟原程式一樣）
    by = {}
    for r in daily:
        by.setdefault(r[1], []).append(r)
    out = []
    for cid, flag, code, sig, entry, _ in CASES:
        rows = sorted((r for r in by.get(code, []) if r[6] is not None and (r[7] or 0) > 0), key=lambda r: r[2])
        mk = sorted({r[0] for r in rows})
        idx = next((i for i, r in enumerate(rows) if r[2] == sig), None)
        rec = dict(case_id=cid, code=code, signal_date=sig, markets_with_data="/".join(mk),
                   signal_found=idx is not None)
        if idx is not None:
            C = [r[6] for r in rows]
            rec["next_trade_row_date"] = rows[idx + 1][2] if idx + 1 < len(rows) else ""
            rec["next_open"] = rows[idx + 1][3] if idx + 1 < len(rows) else ""
            rec["next_market_day"] = next((d for d in sorted(cal) if d > sig), "")
            if flag == "u_reg2":
                lo = min(C[idx - 20:idx]) if idx >= 20 else None
                rec["low20"] = lo
                rec["cross_today"] = lo is not None and C[idx] >= lo * 1.10
                rec["below_yesterday"] = lo is not None and C[idx - 1] < lo * 1.10
                rec["rows_before"] = idx
                rec["yl_pct"] = round((C[idx - 1] / (sum(C[idx - 240:idx]) / 240) - 1) * 100, 6) if idx >= 240 else ""
                rec["window_first_date"] = rows[idx - 240][2] if idx >= 240 else ""
            else:
                ma = lambda j: sum(C[j - 19:j + 1]) / 20
                up = C[idx] > ma(idx) and ma(idx) > ma(idx - 1) if idx >= 20 else None
                H, L = rows[idx - 1][4], rows[idx - 1][5]
                A = (H - L) if None not in (H, L) else None
                R = round(C[idx - 1] + 0.75 * A, 6) if A else None
                rec.update(bull=up, A=A, R=R, high_today=rows[idx][4], close_today=C[idx],
                           touched=R is not None and rows[idx][4] >= R, not_above=R is not None and C[idx] <= R)
        out.append(rec)
    keys = ["case_id", "code", "signal_date", "markets_with_data", "signal_found", "next_trade_row_date",
            "next_market_day", "next_open", "low20", "cross_today", "below_yesterday", "rows_before", "yl_pct",
            "window_first_date", "bull", "A", "R", "high_today", "close_today", "touched", "not_above"]
    # 缺漏檢查：每檔在有資料的區間內，跟證交所開市日比，缺幾天（缺月會讓 240 列年線算錯）
    for rec in out:
        ds = sorted({r[2] for r in by.get(rec["code"], [])})
        miss = [d for d in sorted(cal) if ds and ds[0] <= d <= ds[-1] and d not in set(ds)]
        rec["missing_market_days"] = len(miss)
        rec["missing_months"] = " ".join(sorted({d[:6] for d in miss}))
    keys += ["missing_market_days", "missing_months"]
    w("feature_check.csv", keys, [[r.get(k, "") for k in keys] for r in out])
    for rec in out:       # 寫成 Actions 註記，不用下載檔也看得到
        print("::notice title=%s::%s" % (rec["case_id"], " ".join("%s=%s" % (k, rec.get(k, "")) for k in keys if rec.get(k, "") != "")))
    print("完成：%d 份回應、%d 筆日線、%d 個開市日" % (len(snaps), len(daily), len(cal)))


if __name__ == "__main__":
    main()
