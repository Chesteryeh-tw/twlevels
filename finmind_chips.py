#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 FinMind 免費帳號補「三大法人買賣超、融資融券」的歷史（2021 起），
讓籌碼類回測從約 246 天拉長到跟價量一樣的 1,300 多天。

為什麼要另外一支（不是改 chips.py）
  chips.py 走證交所／櫃買官方，一天一天抓全市場，每天自動更新、結果會進公開 repo。
  FinMind 的條款不准轉散布原始資料，所以這支抓的東西**只放 Actions 快取**，
  只在 finmind.yml 那趟回測裡臨時併進 data/chip/，跑完不 commit。
  公開網站上的籌碼看板仍然只用官方資料。

流程（三步）
  1. 抓：一檔一檔抓完整歷史，原樣存到 data/chip_fm/<資料集>/<代號>.csv
         （不換算單位，換算在第 3 步做，抓錯單位不用重抓）
  2. 對帳：抓之前先拿幾檔跟官方 data/chip/ 重疊的日子比對，
         自動判斷 FinMind 的單位（股或張），對不上就停，不會白跑幾小時
  3. 併入（--merge）：官方 data/chip/ 沒有的交易日，用 FinMind 補一份同格式的日檔。
         官方有的日子一律不動，官方優先。

用法
  python finmind_chips.py --test                 只抓 4 檔做對帳，不寫檔（約 1 分鐘）
  python finmind_chips.py [起始日] [--max-minutes=320] [--only=insti|margin]
                                                 正式補，可中斷重跑，已抓好的不重抓
  python finmind_chips.py --merge                把快取併進 data/chip/（只在 finmind.yml 的 job 裡用）

流量（免費層）
  每小時 600 次，每 7 秒一次（約 514 次／小時，留餘裕）。
  兩個資料集 × 約 1,960 檔 ≈ 3,900 次 ≈ 7.6 小時，
  Actions 一趟上限約 6 小時，所以要跑兩趟；第二趟會接著抓。

已知限制
  - 只抓「今天還在市場上」的股票（market.txt），2021～2025 下市的不在裡面（跟價量歷史一樣的倖存者偏差）。
  - FinMind 說三大法人數字公布後可能小幅修正；歷史資料不受影響。

Token
  從環境變數 FINMIND_TOKEN 讀（GitHub Actions 走 Secrets）。
  **絕對不要把 token 寫進程式或 commit 任何含 token 的檔案。**
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

import twse as T

API = "https://api.finmindtrade.com/api/v4/data"
PAUSE = 7.0
RATE_WAIT = 90
MAX_FAILS = 10
DEFAULT_START = "2021-01-01"
END_OVERRIDE = next((x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--end=")), None)   # 10-29 驗證舊資料用
DEFAULT_MAX_MIN = 320          # finmind_chips.yml 的 timeout 是 350 分，留 30 分存快取

FM_DIR = os.path.join(T.DATA, "chip_fm")
CHIP_DIR = os.path.join(T.DATA, "chip")
COVER_FILE = os.path.join(FM_DIR, "_covered.json")
UNITS_FILE = os.path.join(FM_DIR, "_units.json")
RAW_COLS = "code,fgn,inv,dlrSelf,dlrHedge,mgn,mgnPrev,shrt,shrtPrev"   # 跟 chips.py 一樣

DATASETS = {
    # 我們的名字: (FinMind 資料集, 原樣存的欄位)
    "insti":  ("TaiwanStockInstitutionalInvestorsBuySell", ("date", "name", "buy", "sell")),
    "margin": ("TaiwanStockMarginPurchaseShortSale",
               ("date", "MarginPurchaseTodayBalance", "MarginPurchaseYesterdayBalance",
                "ShortSaleTodayBalance", "ShortSaleYesterdayBalance")),
}

# FinMind 三大法人的 name → 我們的欄位
#   fgn = 外陸資（不含外資自營商）；dlrSelf、dlrHedge = 自營商自行買賣、避險
#   Foreign_Dealer_Self（外資自營商）官方 fgn 也不含，所以不用
INSTI_NAME = {"Foreign_Investor": "fgn", "Investment_Trust": "inv",
              "Dealer_self": "dlrSelf", "Dealer_Hedging": "dlrHedge"}

# 對帳用：上市兩檔、上櫃兩檔
TEST_CODES = ("2330", "2317", "6488", "8069")


# ---------------------------------------------------------------- 共用

def read_token():
    tok = os.environ.get("FINMIND_TOKEN", "").strip()
    if tok:
        return tok
    p = os.path.join(os.path.expanduser("~"), "finmind_token.txt")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            tok = fh.read().strip()
        if tok:
            return tok
    print("找不到 token。請在 GitHub 的 Secrets 設定 FINMIND_TOKEN。")
    sys.exit(1)


def fetch(dataset, code, start, end, token):
    """回傳 list[dict]；限流回 'RATE'；失敗回 None。"""
    q = urllib.parse.urlencode({"dataset": dataset, "data_id": code,
                                "start_date": start, "end_date": end, "token": token})
    req = urllib.request.Request(API + "?" + q, headers={"User-Agent": "twlevels/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
    except Exception as e:
        print("    %s 連線失敗：%s" % (code, e))
        return None
    st = body.get("status")
    if st != 200:
        msg = str(body.get("msg") or "")
        if st in (402, 429) or "limit" in msg.lower() or "request" in msg.lower():
            return "RATE"
        print("    %s API 回 %s：%s" % (code, st, msg))
        return None
    return body.get("data") or []


def fetch_retry(dataset, code, start, end, token):
    rows = fetch(dataset, code, start, end, token)
    time.sleep(PAUSE)
    for _ in range(3):
        if rows != "RATE":
            break
        print("  碰到流量上限，等 %d 秒再試 %s" % (RATE_WAIT, code))
        time.sleep(RATE_WAIT)
        rows = fetch(dataset, code, start, end, token)
        time.sleep(PAUSE)
    return rows


def num(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def market_codes():
    codes = []
    with open(os.path.join(T.DATA, "market.txt"), encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            p = line.rstrip("\n").split(",")
            if len(p) >= 3 and p[0]:
                codes.append(p[0])
    return sorted(codes)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_json(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def raw_path(ds, code):
    return os.path.join(FM_DIR, ds, code + ".csv")


INSTI_COLS = ["date"] + ["%s_%s" % (n, x) for n in INSTI_NAME for x in ("buy", "sell")]


def save_raw(ds, code, rows):
    """存原始買賣（不換算單位）。三大法人轉成一天一列，只留用得到的四類法人，檔案小一半。"""
    os.makedirs(os.path.join(FM_DIR, ds), exist_ok=True)
    tmp = raw_path(ds, code) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        if ds == "insti":
            byday = {}
            for r in rows:
                n = r.get("name")
                if n in INSTI_NAME:
                    d = byday.setdefault(str(r.get("date", "")), {})
                    d[n + "_buy"], d[n + "_sell"] = r.get("buy", ""), r.get("sell", "")
            fh.write(",".join(INSTI_COLS) + "\n")
            for day in sorted(byday):
                fh.write(",".join([day] + [str(byday[day].get(c, "")) for c in INSTI_COLS[1:]]) + "\n")
        else:
            cols = DATASETS[ds][1]
            fh.write(",".join(cols) + "\n")
            for r in rows:
                fh.write(",".join(str(r.get(c, "")).replace(",", "") for c in cols) + "\n")
    os.replace(tmp, raw_path(ds, code))


def read_raw(ds, code):
    """讀回成 FinMind 原本的樣子（三大法人還原成一類法人一列）。"""
    p = raw_path(ds, code)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split(",")
        for line in fh:
            v = line.rstrip("\n").split(",")
            if len(v) != len(head):
                continue
            r = dict(zip(head, v))
            if ds == "insti":
                for n in INSTI_NAME:
                    if r.get(n + "_buy", "") != "" or r.get(n + "_sell", "") != "":
                        out.append({"date": r["date"], "name": n,
                                    "buy": r.get(n + "_buy"), "sell": r.get(n + "_sell")})
            else:
                out.append(r)
    return out


# ---------------------------------------------------------------- 換算成 chips.py 的日格式

def to_days(ds, rows, unit):
    """FinMind 原始列 → {YYYYMMDD: {欄位: 張}}。unit 是乘數（股→張 = 0.001）。"""
    out = {}
    if ds == "insti":
        for r in rows:
            k = INSTI_NAME.get(r.get("name"))
            b, s = num(r.get("buy")), num(r.get("sell"))
            if not k or b is None or s is None:
                continue
            d = str(r.get("date", "")).replace("-", "")[:8]
            out.setdefault(d, {})[k] = (b - s) * unit
    else:
        m = {"mgn": "MarginPurchaseTodayBalance", "mgnPrev": "MarginPurchaseYesterdayBalance",
             "shrt": "ShortSaleTodayBalance", "shrtPrev": "ShortSaleYesterdayBalance"}
        for r in rows:
            d = str(r.get("date", "")).replace("-", "")[:8]
            vals = {k: num(r.get(f)) for k, f in m.items()}
            if all(v is None for v in vals.values()):
                continue
            out[d] = {k: (None if v is None else v * unit) for k, v in vals.items()}
    return out


# ---------------------------------------------------------------- 對帳（決定單位）

_OFF = {}


def read_official(dstr):
    """官方日檔，讀過就記住（對帳時同一天會被查很多次）。"""
    if dstr not in _OFF:
        _OFF[dstr] = _read_official(dstr)
    return _OFF[dstr]


def _read_official(dstr):
    p = os.path.join(CHIP_DIR, dstr + ".txt")
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
        for line in fh:
            v = line.rstrip("\n").split(",")
            if len(v) == len(head) and v[0]:
                out[v[0]] = {k: T.pn(x) for k, x in zip(head[1:], v[1:])}
    return out


def official_days():
    if not os.path.isdir(CHIP_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(CHIP_DIR) if f.endswith(".txt"))


def close(a, b):
    return abs(a - b) <= max(1.0, abs(b) * 0.01)


def compare(ds, code, rows, unit, days):
    """回傳 (比得到的格數, 對上的格數)。只比官方有值、FinMind 也有值的格子。"""
    fm = to_days(ds, rows, unit)
    keys = ("fgn", "inv", "dlrSelf", "dlrHedge") if ds == "insti" else ("mgn", "shrt")
    n = ok = 0
    for d in days:
        off = read_official(d).get(code)
        f = fm.get(d)
        if not off or not f:
            continue
        for k in keys:
            if off.get(k) is None or f.get(k) is None:
                continue
            # 兩邊都是 0 的格子不算，不然單位錯了也會「對上」
            if abs(off[k]) < 1e-9 and abs(f[k]) < 1e-9:
                continue
            n += 1
            ok += close(f[k], off[k])
    return n, ok


def calibrate(token, codes=TEST_CODES, verbose=True):
    """抓幾檔跟官方重疊的日子比對，決定每個資料集的單位。對不上回 None。"""
    days = official_days()[-60:]
    if len(days) < 10:
        print("官方 data/chip/ 不到 10 天，沒辦法對帳。")
        return None
    start = "%s-%s-%s" % (days[0][:4], days[0][4:6], days[0][6:])
    end = "%s-%s-%s" % (days[-1][:4], days[-1][4:6], days[-1][6:])
    units = {}
    for ds, (dname, _) in DATASETS.items():
        tot = {1.0: [0, 0], 0.001: [0, 0]}
        for code in codes:
            rows = fetch_retry(dname, code, start, end, token)
            if rows in (None, "RATE") or not rows:
                print("  %s %s：抓不到資料" % (ds, code))
                continue
            if verbose:
                print("  %s %s：收到 %d 筆，欄位 %s" % (ds, code, len(rows), sorted(rows[0].keys())))
            for u in tot:
                n, ok = compare(ds, code, rows, u, days)
                tot[u][0] += n; tot[u][1] += ok
        best = max(tot, key=lambda u: (tot[u][1] / tot[u][0]) if tot[u][0] else 0)
        n, ok = tot[best]
        rate = ok / n if n else 0
        print("  %s：跟官方比 %d 格，單位 ×%g 時對上 %d 格（%.1f%%）；另一種單位 %d/%d"
              % (ds, n, best, ok, rate * 100, tot[0.001 if best == 1.0 else 1.0][1], n))
        if n < 50 or rate < 0.95:
            print("  ★ %s 對不上官方（要 ≥ 50 格、≥ 95%%），不要繼續 ★" % ds)
            return None
        units[ds] = best
    return units


def selftest():
    print("=== 測試模式：抓 %d 檔、跟官方 data/chip/ 對帳，不寫檔 ===" % len(TEST_CODES))
    units = calibrate(read_token())
    print("=== %s ===" % ("測試通過，可以跑 full" if units else "測試沒過，先不要跑 full"))
    return bool(units)


# ---------------------------------------------------------------- 正式抓

def run(start, max_min, only):
    t0 = time.time()
    token = read_token()
    end = END_OVERRIDE or datetime.now().strftime("%Y-%m-%d")
    print("先對帳（決定單位）...")
    units = calibrate(token, codes=TEST_CODES[:2] + TEST_CODES[2:3], verbose=False)
    if not units:
        sys.exit(1)
    save_json(UNITS_FILE, {"units": units, "checked_at": datetime.now().isoformat(timespec="seconds"),
                           "note": "乘數：FinMind 原始值 × units = 張"})
    cov = load_json(COVER_FILE)
    codes = market_codes()
    todo = []
    for ds in DATASETS:
        if only and ds != only:
            continue
        c = cov.setdefault(ds, {})
        for code in codes:
            if c.get(code, "9999") <= start and os.path.exists(raw_path(ds, code)):
                continue
            todo.append((ds, code))
    print("全市場 %d 檔；要抓 %d 個（資料集 × 股票），每 %.0f 秒一次，預估 %.1f 小時；"
          "這趟最多跑 %d 分鐘，沒抓完就下次接著抓。"
          % (len(codes), len(todo), PAUSE, len(todo) * PAUSE / 3600, max_min))
    if not todo:
        print("全部都已經補齊。")
        return
    done = failed = streak = 0
    for i, (ds, code) in enumerate(todo, 1):
        if (time.time() - t0) / 60 > max_min:
            print("時間到（%d 分鐘），先停。已抓 %d、剩 %d，再跑一次 full 會接著抓。"
                  % (max_min, done, len(todo) - i + 1))
            break
        rows = fetch_retry(DATASETS[ds][0], code, start, end, token)
        if rows == "RATE":
            print("  等了三輪還是被擋，先停下來；一小時後重跑會接著抓。")
            break
        if rows is None:
            failed += 1; streak += 1
            if streak >= MAX_FAILS:
                print("連續失敗 %d 次，先停。可能是 token 錯了或被擋。" % MAX_FAILS)
                break
            continue
        streak = 0
        save_raw(ds, code, rows)              # 空的也存：代表這檔真的沒資料（例如 ETF 沒融資），不用重抓
        cov[ds][code] = start
        done += 1
        if done % 25 == 0:
            save_json(COVER_FILE, cov)
            el = (time.time() - t0) / 60
            print("  進度 %d/%d（失敗 %d）已用 %.0f 分" % (i, len(todo), failed, el))
    save_json(COVER_FILE, cov)
    left = sum(1 for ds in DATASETS for c in codes
               if not (cov.get(ds, {}).get(c, "9999") <= start and os.path.exists(raw_path(ds, c))))
    print("這趟抓了 %d 個、失敗 %d 個；全部還差 %d 個。%s"
          % (done, failed, left, "全部補齊。" if not left else "再跑一次 full 會接著抓。"))


# ---------------------------------------------------------------- 併入 data/chip/

def merge():
    """官方沒有的交易日，用 FinMind 寫一份跟 chips.py 同格式的日檔。官方有的日子不動。"""
    _OFF.clear()
    u = load_json(UNITS_FILE).get("units")
    if not u:
        print("沒有 FinMind 籌碼快取（或沒對過帳），不併入。")
        return 0
    have = set(official_days())
    days = {}
    for ds in DATASETS:
        d = os.path.join(FM_DIR, ds)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".csv"):
                continue
            code = fn[:-4]
            for day, vals in to_days(ds, read_raw(ds, code), u[ds]).items():
                if day in have:
                    continue
                days.setdefault(day, {}).setdefault(code, {}).update(vals)
    keys = RAW_COLS.split(",")[1:]
    os.makedirs(CHIP_DIR, exist_ok=True)
    for day, rows in days.items():
        with open(os.path.join(CHIP_DIR, day + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(RAW_COLS + "\n")
            for code in sorted(rows):
                fh.write(code + "," + ",".join(T.fmt(rows[code].get(k)) for k in keys) + "\n")
    if days:
        print("FinMind 籌碼併入 %d 個交易日（%s ~ %s）；官方原有 %d 天不動。"
              "這些檔只在這趟 job 裡，不 commit。"
              % (len(days), min(days), max(days), len(have)))
    else:
        print("沒有要併入的日子。")
    return len(days)


def main():
    a = sys.argv[1:]
    if "--test" in a:
        sys.exit(0 if selftest() else 1)
    if "--merge" in a:
        merge()
        return
    max_min, only = DEFAULT_MAX_MIN, None
    for x in a:
        if x.startswith("--max-minutes="):
            max_min = float(x.split("=", 1)[1])
        if x.startswith("--only="):
            only = x.split("=", 1)[1]
    pos = [x for x in a if not x.startswith("--")]
    run(pos[0] if pos else DEFAULT_START, max_min, only)


if __name__ == "__main__":
    main()
