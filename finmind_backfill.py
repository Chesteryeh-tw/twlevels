#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 FinMind 把每檔股票的歷史日線一次補足，寫進 data/stock/<代號>.txt

為什麼不用證交所爬：
  證交所那條路是「一天一天抓全市場快照」，一天 1.1 秒，五年要跑好幾小時，
  而且中間任何一天失敗就少一天。FinMind 是「一檔一檔抓它的完整歷史」，
  一個請求就拿到那檔從頭到尾的資料，1900 個請求就收工。

流量限制（免費層）
  註冊並驗證信箱後 600 requests / 小時 = 每 6 秒一次。
  程式照這個節奏跑，不要調快 —— 被擋掉要等一小時，反而更慢。
  1900 檔 × 6.2 秒 ≈ 3.3 小時。

可以中斷重跑
  每檔抓完就立刻寫檔，並且把「這檔已經從哪一天補過」記進 data/stock/_covered.json。
  下次再跑，記錄裡已經涵蓋到 START_DATE 的就跳過，中途斷掉再跑一次也不會重抓。

  （舊版是拿「本地最舊日期 <= 起始日」當判準，但起始日通常給 1/1 這種休市日，
   最舊日期不可能等於那天，條件永遠不成立 —— 等於每次都把 1900 檔重抓一遍。
   2026-09-21 的兩次 Actions 各白跑約 4 小時就是這個原因。）

Token
  從環境變數 FINMIND_TOKEN 讀。GitHub Actions 走 Secrets，
  本機跑的話可以放檔案（見 read_token）。
  **絕對不要把 token 寫進這支程式，也不要 commit 任何含 token 的檔案。**
"""

import os
import sys
import time
from datetime import datetime

import urllib.request
import urllib.parse
import json
from datetime import timedelta

import twse as T

API = "https://api.finmindtrade.com/api/v4/data"

# 每次請求之間隔多久。
# 上限 600/hr 換算是 6.0 秒，但貼著上限跑很危險 —— 對方的計數方式可能是
# 滑動視窗，或把別的請求也算進來，一旦被擋就會連續失敗、觸發下面的停止保護。
# 7.0 秒 = 514/hr，留 14% 餘裕，代價只是多半小時。
PAUSE = 7.0
# 被限流的時候等多久再重試。不計入失敗次數，因為這不是錯誤，只是要等。
RATE_WAIT = 90
# 預設補到幾年前。五年約 110 MB、1250 個交易日。
DEFAULT_START = "2021-01-01"
# 連續失敗幾次就停下來，不要悶著頭跑三小時跑出一堆空檔
MAX_FAILS = 10
REPORT_EVERY = 25

# 「這檔已經從哪一天補過」的紀錄。放在 data/stock/ 底下，
# 這樣它會跟歷史檔一起被 Actions 的快取存下來、下次一起還原。
# 副檔名是 .json，indicators.py 和 backtest.py 掃目錄時只收 .txt，不會被誤讀。
COVER_FILE = os.path.join(T.STOCK_DIR, "_covered.json")
# 還沒有紀錄時（第一次跑修正版）的寬限天數。
# 起始日給 2021-01-01，實際第一個交易日是 1/4；再加上農曆年前後的休市，
# 抓到的最舊日期跟起始日差個幾天到幾週是正常的，不該當成「沒抓過」。
# 90 天夠寬，又不至於把 2021 年中才上市的新股誤判成已涵蓋。
COVER_SLACK_DAYS = 90


def read_token():
    """優先吃環境變數；本機跑的話也可以放在家目錄的 finmind_token.txt。"""
    tok = os.environ.get("FINMIND_TOKEN", "").strip()
    if tok:
        return tok
    for p in (os.path.expanduser("~/finmind_token.txt"),
              os.path.join(os.path.expanduser("~"), "finmind_token.txt")):
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                tok = fh.read().strip()
            if tok:
                return tok
    print("找不到 token。請設定環境變數 FINMIND_TOKEN，"
          "或把 token 放在家目錄的 finmind_token.txt。")
    sys.exit(1)


def fetch(code, start, end, token):
    """抓一檔的日線。回傳 list of dict，失敗回 None。"""
    q = urllib.parse.urlencode({
        "dataset": "TaiwanStockPrice",
        "data_id": code,
        "start_date": start,
        "end_date": end,
        "token": token,
    })
    req = urllib.request.Request(API + "?" + q,
                                 headers={"User-Agent": "twlevels/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
    except Exception as e:
        print("    %s 連線失敗：%s" % (code, e))
        return None
    st = body.get("status")
    if st != 200:
        msg = str(body.get("msg") or "")
        # 402 是 FinMind 的「超過流量」。其他帶 limit/request 字樣的也當作限流。
        if st in (402, 429) or "limit" in msg.lower() or "request" in msg.lower():
            return "RATE"
        print("    %s API 回 %s：%s" % (code, st, msg))
        return None
    return body.get("data") or []


# FinMind 的欄位名稱，左邊是我們要的、右邊是可能的叫法。
# 寫成這樣是因為沙箱連不到它的 API，沒辦法實測，所以多備幾個名字，
# 而且 --test 模式會把真正收到的欄位印出來給人看。
FIELD = {
    "date":  ("date",),
    "open":  ("open", "Open"),
    "high":  ("max", "high", "High", "Max"),
    "low":   ("min", "low", "Low", "Min"),
    "close": ("close", "Close"),
    "vol":   ("Trading_Volume", "trading_volume", "Trading_Volumn", "volume"),
    "money": ("Trading_money", "trading_money", "Trading_Money", "amount"),
}


def pick(row, key):
    for k in FIELD[key]:
        if k in row:
            return row[k]
    return None


def to_line(row):
    """FinMind 的一筆 → 本站格式：日期,開,高,低,收,張,萬

    FinMind 的量是「股」、金額是「元」；本站存的是「張」和「萬元」，
    跟證交所抓下來的那一份保持一致，不然指標會算錯。
    """
    try:
        raw_d = pick(row, "date")
        if not raw_d:
            return None, None
        d = str(raw_d).replace("-", "")[:8]
        o = pick(row, "open"); h = pick(row, "high")
        l = pick(row, "low");  c = pick(row, "close")
        if c in (None, "") or float(c) <= 0:
            return None, None
        lot = round(float(pick(row, "vol") or 0) / 1000)
        wan = round(float(pick(row, "money") or 0) / 10000)
    except (KeyError, TypeError, ValueError):
        return None, None

    def f(x):
        if x is None or x == "":
            return ""
        x = float(x)
        return ("%.4f" % x).rstrip("0").rstrip(".")

    return d, "%s,%s,%s,%s,%s,%d,%d" % (d, f(o), f(h), f(l), f(c), lot, wan)


def load_cover():
    """讀「已經補到哪一天」的紀錄。壞掉或不存在都當成空的，重抓而已，不會錯。"""
    try:
        with open(COVER_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_cover(cov):
    """先寫暫存再換名，中途被砍不會留下半個壞掉的 json。"""
    try:
        os.makedirs(T.STOCK_DIR, exist_ok=True)
        tmp = COVER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cov, fh)
        os.replace(tmp, COVER_FILE)
    except Exception as e:
        print("  （紀錄檔寫不進去：%s，不影響資料本身）" % e)


def already_covered(code, start, cov):
    """本地已經有的資料是不是已經涵蓋到 start。有的話就不用再抓。

    兩道判斷：
      1. 紀錄檔說這檔之前就是從 start（或更早）抓的 —— 明確跳過。
      2. 還沒有紀錄（第一次跑修正版，或紀錄掉了）—— 看最舊日期，
         落在 start 之後 COVER_SLACK_DAYS 天以內就算數。
         這一條是為了讓快取裡已經抓好的 1337 天直接生效，不用再抓一輪。
    """
    ser = T.read_stock(code)
    if not ser:
        return False, {}
    rec = cov.get(code)
    if rec and rec <= start:
        return True, ser
    s = start.replace("-", "")
    oldest = min(ser.keys())
    if oldest <= s:
        return True, ser
    limit = (datetime.strptime(s, "%Y%m%d")
             + timedelta(days=COVER_SLACK_DAYS)).strftime("%Y%m%d")
    return oldest <= limit, ser


def selftest(token):
    """抓 3 檔的最近幾天，印出原始欄位、轉換結果，並跟本地資料對帳。

    連不到 API、欄位改名、單位算錯，這裡都會當場現形 ——
    比跑完三小時才發現資料是壞的好太多。什麼都不寫檔。
    """
    print("=== 測試模式：只抓 3 檔、不寫任何檔案 ===\n")
    ok = True
    for code in ("2330", "2317", "0050"):
        rows = fetch(code, "2026-09-10", "2026-09-30", token)
        if not rows:
            print("%s：抓不到資料\n" % code)
            ok = False
            continue
        print("%s：收到 %d 筆" % (code, len(rows)))
        print("  API 實際給的欄位：%s" % sorted(rows[-1].keys()))
        d, line = to_line(rows[-1])
        print("  轉出：%s" % line)
        local = T.read_stock(code)
        if d and d in local:
            print("  本地：%s" % local[d])
            a = line.split(",")
            b = local[d].split(",")
            same = all(abs(float(a[i]) - float(b[i])) <= max(1.0, abs(float(b[i])) * 0.01)
                       for i in range(1, 7) if a[i] and b[i])
            print("  對帳：%s" % ("一致 ✓" if same else "★ 不一致，不要繼續跑 ★"))
            if not same:
                ok = False
        else:
            print("  本地沒有這一天，無法對帳")
        print()
        time.sleep(PAUSE)
    print("=== %s ===" % ("測試通過，可以正式跑了" if ok else "測試沒過，先不要跑正式的"))
    return ok


def main():
    args = [a for a in sys.argv[1:]]
    if "--test" in args:
        sys.exit(0 if selftest(read_token()) else 1)
    args = [a for a in args if not a.startswith("--")]
    start = args[0] if args else DEFAULT_START
    end = datetime.now().strftime("%Y-%m-%d")
    token = read_token()
    limit = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])

    info = {}
    path = os.path.join(T.DATA, "market.txt")
    with open(path, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            p = line.rstrip("\n").split(",")
            if len(p) >= 3 and p[0]:
                info[p[0]] = p[1]
    codes = sorted(info)
    if limit:
        codes = codes[:limit]

    print("全市場 %d 檔，要補到 %s ~ %s" % (len(codes), start, end))

    os.makedirs(T.STOCK_DIR, exist_ok=True)
    cov = load_cover()
    done = skipped = failed = added = 0
    fails_in_a_row = 0

    # 先把要抓的挑出來再開始，這樣一開頭就知道實際要跑多久，
    # 而不是等跑完才發現「其實全部都跳過了」或「其實全部都重抓了」。
    todo = []
    for code in codes:
        covered, _ = already_covered(code, start, cov)
        if covered:
            skipped += 1
        else:
            todo.append(code)
    print("已經補過、跳過 %d 檔；實際要抓 %d 檔，"
          "每 %.1f 秒一個請求（約 %.0f 次/小時，上限 600），預估 %.1f 小時。"
          "中途可以中斷，重跑會接著做。"
          % (skipped, len(todo), PAUSE, 3600 / max(PAUSE, 0.001),
             len(todo) * PAUSE / 3600))
    if not todo:
        print("全部都已經補齊，直接收工。")
        save_cover(cov)
        return

    for n, code in enumerate(todo, 1):
        ser = T.read_stock(code)

        rows = fetch(code, start, end, token)
        time.sleep(PAUSE)

        # 被限流就等一下再試同一檔，最多等三輪。這不算失敗 ——
        # 算成失敗的話連續幾次就會觸發停止保護，整晚白跑。
        for _ in range(3):
            if rows != "RATE":
                break
            print("  碰到流量上限，等 %d 秒再試 %s" % (RATE_WAIT, code))
            time.sleep(RATE_WAIT)
            rows = fetch(code, start, end, token)
            time.sleep(PAUSE)
        if rows == "RATE":
            print("  等了三輪還是被擋，先停下來。等一小時之後重跑，已抓好的不會重抓。")
            break

        if rows is None:
            failed += 1
            fails_in_a_row += 1
            if fails_in_a_row >= MAX_FAILS:
                print("連續失敗 %d 次，先停下來。"
                      "可能是 token 錯了或流量被擋，檢查後重跑。" % MAX_FAILS)
                break
            continue
        fails_in_a_row = 0

        new = 0
        for row in rows:
            d, line = to_line(row)
            if d and d not in ser:      # 本地已經有的日期不覆蓋
                ser[d] = line
                new += 1
        if new:
            T.write_stock(code, ser)
            added += new
        done += 1
        # 抓成功就記一筆。就算這檔沒有新資料（本來就抓齊了）也要記 ——
        # 重點是「已經向 FinMind 問過這個區間」，下次不必再問。
        if cov.get(code, "9999-99-99") > start:
            cov[code] = start
        if done % REPORT_EVERY == 0:
            save_cover(cov)

        if n % REPORT_EVERY == 0:
            left = (len(todo) - n) * PAUSE / 60
            print("  %d/%d　已補 %d 檔、新增 %d 天、跳過 %d、失敗 %d"
                  "　剩約 %.0f 分鐘"
                  % (n, len(todo), done, added, skipped, failed, left))

    # 不管是跑完、被限流中斷、還是連續失敗停下，都要把進度存起來。
    save_cover(cov)
    print("結束：補了 %d 檔、新增 %d 個交易日、跳過 %d 檔、失敗 %d 檔"
          % (done, added, skipped, failed))
    if failed:
        print("失敗的重跑一次就會補上，已經抓好的不會重抓。")


if __name__ == "__main__":
    main()
