#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
籌碼面資料：三大法人買賣超、融資融券。上市上櫃都抓，全部是免費公開資料。

產出
  data/chip/<YYYYMMDD>.txt   每個交易日一個檔，一檔股票一行（原始數字）
  data/chips.txt             最新一日的衍生指標（前端讀這個）

用法
  python chips.py              抓最新一天（配合每日更新）
  python chips.py 400 0        回補：400 天前 ~ 今天
  python chips.py --derive     只重算 chips.txt，不連網

資料來源
  上市三大法人  https://www.twse.com.tw/fund/T86
  上市融資融券  https://www.twse.com.tw/exchangeReport/MI_MARGN
  上櫃三大法人  https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade
  上櫃融資融券  https://www.tpex.org.tw/www/zh-tw/margin/balance
  集保大戶散戶  tdcc.py 另外處理（每週一次）

單位一律換算成「張」（1 張 = 1000 股），跟畫面上其他欄位一致。
"""

import os
import sys
import time
from datetime import datetime, timedelta

import twse as T

CHIP_DIR = os.path.join(T.DATA, "chip")
OUT = os.path.join(T.DATA, "chips.txt")
TDCC_DIR = os.path.join(T.DATA, "tdcc")

# 每日檔的欄位（原始值，單位：張）
RAW_COLS = "code,fgn,inv,dlrSelf,dlrHedge,mgn,mgnPrev,shrt,shrtPrev"

# chips.txt 的欄位（衍生值）
COLS = ("code,fgn,fgnD,inv,invD,dlrH,mgn,mgnChg,shrt,sr,"
        "big,bigChg,small,holders")

LOOKBACK = 40        # 算連買連賣要回看幾個交易日
PAUSE = 1.2


# ---------------------------------------------------------------- 讀寫

def day_path(dstr):
    return os.path.join(CHIP_DIR, dstr + ".txt")


def have_day(dstr):
    return os.path.exists(day_path(dstr))


def write_day(dstr, rows):
    """rows: {code: dict}"""
    os.makedirs(CHIP_DIR, exist_ok=True)
    keys = RAW_COLS.split(",")[1:]
    lines = [RAW_COLS]
    for code in sorted(rows):
        r = rows[code]
        lines.append(code + "," + ",".join(T.fmt(r.get(k)) for k in keys))
    with open(day_path(dstr), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def read_day(dstr):
    p = day_path(dstr)
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
        keys = head[1:]
        for line in fh:
            p2 = line.rstrip("\n").split(",")
            if len(p2) != len(head) or not p2[0]:
                continue
            d = {}
            for k, v in zip(keys, p2[1:]):
                d[k] = T.pn(v)
            out[p2[0]] = d
    return out


def chip_days():
    """已經抓好的交易日，由舊到新。"""
    if not os.path.isdir(CHIP_DIR):
        return []
    return sorted(fn[:-4] for fn in os.listdir(CHIP_DIR) if fn.endswith(".txt"))


# ---------------------------------------------------------------- 上市

def fetch_twse_insti(day):
    """T86 三大法人。回傳 {code: {fgn, inv, dlrSelf, dlrHedge}}，單位張。"""
    url = ("https://www.twse.com.tw/fund/T86?response=json&date=%s&selectType=ALL"
           % day.strftime("%Y%m%d"))
    j = T.get_json(url)
    if not j or j.get("stat") != "OK":
        return {}
    fields = j.get("fields") or []
    data = j.get("data") or []
    if not data:
        return {}

    i_fgn = T.idx(fields, "外陸資買賣超股數(不含外資自營商)", "外陸資買賣超股數")
    i_inv = T.idx(fields, "投信買賣超股數")
    i_self = T.idx(fields, "自營商買賣超股數(自行買賣)")
    i_hedge = T.idx(fields, "自營商買賣超股數(避險)")
    if min(i_fgn, i_inv, i_self, i_hedge) < 0:
        print("      T86 欄位對不上，跳過：%s" % fields[:4])
        return {}

    out = {}
    for row in data:
        if len(row) <= max(i_fgn, i_inv, i_self, i_hedge):
            continue
        code = T.clean(row[0])
        if not code:
            continue
        out[code] = {
            "fgn": lot(T.pn(row[i_fgn])),
            "inv": lot(T.pn(row[i_inv])),
            "dlrSelf": lot(T.pn(row[i_self])),
            "dlrHedge": lot(T.pn(row[i_hedge])),
        }
    return out


def fetch_twse_margin(day):
    """MI_MARGN 融資融券（股票表，單位本來就是張）。"""
    url = ("https://www.twse.com.tw/exchangeReport/MI_MARGN"
           "?response=json&date=%s&selectType=STOCK" % day.strftime("%Y%m%d"))
    j = T.get_json(url)
    if not j or j.get("stat") != "OK":
        return {}

    # 新版把表放在 tables 裡，舊版直接給 data，兩種都接
    tables = j.get("tables")
    if tables:
        tb = None
        for t in tables:
            if t.get("data") and t.get("fields") and len(t["fields"]) >= 14:
                tb = t
                break
        if not tb:
            return {}
        fields, data = tb["fields"], tb["data"]
    else:
        fields, data = j.get("fields") or [], j.get("data") or []
    if not data:
        return {}

    # 融資、融券各有一組「買進/賣出/償還/前日餘額/今日餘額/限額」，欄名會重複，
    # 所以用位置：代號 名稱 | 資×6 | 券×6 | 資券互抵 註記
    if len(fields) < 16:
        print("      MI_MARGN 欄位數不對（%d），跳過" % len(fields))
        return {}
    i_mgn_prev, i_mgn, i_shrt_prev, i_shrt = 5, 6, 11, 12

    out = {}
    for row in data:
        if len(row) <= i_shrt:
            continue
        code = T.clean(row[0])
        if not code:
            continue
        out[code] = {
            "mgn": T.pn(row[i_mgn]),
            "mgnPrev": T.pn(row[i_mgn_prev]),
            "shrt": T.pn(row[i_shrt]),
            "shrtPrev": T.pn(row[i_shrt_prev]),
        }
    return out


# ---------------------------------------------------------------- 上櫃

def _tpex_rows(j, min_cols):
    """櫃買的 json 會把資料包在 tables 裡，挑出欄位數對得上的那張表。"""
    if not j:
        return None, None
    for t in (j.get("tables") or []):
        data = t.get("data") or []
        if data and len(data[0]) >= min_cols:
            return t.get("fields") or [], data
    return None, None


def fetch_tpex_insti(day):
    """上櫃三大法人買賣明細。欄名重複，靠位置＋加總驗算確認。"""
    url = ("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
           "?type=Daily&sect=EW&date=%s&id=&response=json"
           % day.strftime("%Y/%m/%d"))
    fields, data = _tpex_rows(T.get_json(url), 24)
    if not data:
        return {}

    # 代號 名稱 |外資不含自營 3| 外資自營 3| 外資合計 3| 投信 3|
    #           自營自行 3| 自營避險 3| 自營合計 3| 三大法人合計
    I_FGN, I_INV, I_SELF, I_HEDGE, I_TOTAL = 10, 13, 16, 19, 23

    out, bad = {}, 0
    for row in data:
        if len(row) <= I_TOTAL:
            continue
        code = T.clean(row[0])
        if not code:
            continue
        fgn = T.pn(row[I_FGN])
        inv = T.pn(row[I_INV])
        sf = T.pn(row[I_SELF])
        hd = T.pn(row[I_HEDGE])
        tot = T.pn(row[I_TOTAL])
        if None in (fgn, inv, sf, hd, tot):
            continue
        # 驗算：外資 + 投信 + 自營（自行＋避險）要等於三大法人合計
        if abs((fgn + inv + sf + hd) - tot) > 1:
            bad += 1
            continue
        out[code] = {"fgn": lot(fgn), "inv": lot(inv),
                     "dlrSelf": lot(sf), "dlrHedge": lot(hd)}
    if bad:
        print("      上櫃法人：%d 筆加總對不上，已跳過（欄位可能改版）" % bad)
    return out


def fetch_tpex_margin(day):
    """上櫃融資融券餘額，單位張。"""
    url = ("https://www.tpex.org.tw/www/zh-tw/margin/balance"
           "?type=Daily&date=%s&response=json" % day.strftime("%Y/%m/%d"))
    fields, data = _tpex_rows(T.get_json(url), 18)
    if not data:
        return {}

    # 代號 名稱 前資餘額 資買 資賣 現償 資餘額 資屬證金 資使用率 資限額
    #           前券餘額 券賣 券買 券償 券餘額 ...
    I_MGN_PREV, I_MGN, I_SHRT_PREV, I_SHRT = 2, 6, 10, 14

    out = {}
    for row in data:
        if len(row) <= I_SHRT:
            continue
        code = T.clean(row[0])
        if not code:
            continue
        out[code] = {
            "mgn": T.pn(row[I_MGN]),
            "mgnPrev": T.pn(row[I_MGN_PREV]),
            "shrt": T.pn(row[I_SHRT]),
            "shrtPrev": T.pn(row[I_SHRT_PREV]),
        }
    return out


# ---------------------------------------------------------------- 組裝

def lot(shares):
    """股 → 張。"""
    return None if shares is None else shares / 1000.0


def fetch_chip_day(day, pause=PAUSE):
    """抓一天的四個來源，合併成 {code: {...}}。抓不到就回空的。"""
    merged = {}

    def merge(src):
        for code, d in src.items():
            merged.setdefault(code, {}).update(d)

    merge(fetch_twse_insti(day))
    time.sleep(pause)
    merge(fetch_tpex_insti(day))
    time.sleep(pause)
    merge(fetch_twse_margin(day))
    time.sleep(pause)
    merge(fetch_tpex_margin(day))

    # 只留下真的有數字的（法人或融資至少一邊有）
    return {c: d for c, d in merged.items() if any(v is not None for v in d.values())}


# ---------------------------------------------------------------- 集保

def read_tdcc_latest_two():
    """回傳最新與上一期的集保資料 (new, old)，各是 {code: {big, small, holders}}。"""
    if not os.path.isdir(TDCC_DIR):
        return {}, {}
    fns = sorted(fn for fn in os.listdir(TDCC_DIR) if fn.endswith(".txt"))
    if not fns:
        return {}, {}

    def load(fn):
        out = {}
        with open(os.path.join(TDCC_DIR, fn), encoding="utf-8") as fh:
            fh.readline()
            for line in fh:
                p = line.rstrip("\n").split(",")
                if len(p) >= 4 and p[0]:
                    out[p[0]] = {"big": T.pn(p[1]), "small": T.pn(p[2]),
                                 "holders": T.pn(p[3])}
        return out

    new = load(fns[-1])
    old = load(fns[-2]) if len(fns) >= 2 else {}
    return new, old


# ---------------------------------------------------------------- 衍生

def streak(series):
    """series 由新到舊。回傳連買天數（正）或連賣天數（負）；當天是 0 就回 0。"""
    if not series or series[0] is None or series[0] == 0:
        return 0
    sign = 1 if series[0] > 0 else -1
    n = 0
    for v in series:
        if v is None or v == 0 or (v > 0) != (sign > 0):
            break
        n += 1
    return n * sign


def derive():
    """讀最近幾天的每日檔＋集保，算出 chips.txt。"""
    days = chip_days()
    if not days:
        print("還沒有任何籌碼資料，先跑一次 python chips.py。")
        return 0

    recent = days[-LOOKBACK:][::-1]          # 由新到舊
    cache = {d: read_day(d) for d in recent}
    today = cache[recent[0]]

    tdcc_new, tdcc_old = read_tdcc_latest_two()

    lines = []
    for code in sorted(today):
        cur = today[code]
        fgn_s = [cache[d].get(code, {}).get("fgn") for d in recent]
        inv_s = [cache[d].get(code, {}).get("inv") for d in recent]

        mgn = cur.get("mgn")
        mgn_prev = cur.get("mgnPrev")
        shrt = cur.get("shrt")
        mgn_chg = (mgn - mgn_prev) if (mgn is not None and mgn_prev is not None) else None
        # 券資比 = 融券餘額 / 融資餘額
        sr = (shrt / mgn * 100) if (shrt is not None and mgn and mgn > 0) else None

        t_new = tdcc_new.get(code, {})
        t_old = tdcc_old.get(code, {})
        big = t_new.get("big")
        big_old = t_old.get("big")
        big_chg = (big - big_old) if (big is not None and big_old is not None) else None

        lines.append(",".join([
            code,
            T.fmt(cur.get("fgn")), str(streak(fgn_s)),
            T.fmt(cur.get("inv")), str(streak(inv_s)),
            T.fmt(cur.get("dlrHedge")),
            T.fmt(mgn), T.fmt(mgn_chg),
            T.fmt(shrt), ("" if sr is None else "%.1f" % sr),
            ("" if big is None else "%.2f" % big),
            ("" if big_chg is None else "%.2f" % big_chg),
            ("" if t_new.get("small") is None else "%.2f" % t_new["small"]),
            T.fmt(t_new.get("holders")),
        ]))

    os.makedirs(T.DATA, exist_ok=True)
    now = datetime.now(T.TPE)
    head = "TWLVC1|%s|%s|%d|%s" % (recent[0], now.strftime("%Y%m%d%H%M"),
                                   len(lines), COLS)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(head + "\n" + "\n".join(lines) + "\n")
    print("已寫入 %s：%d 檔、%d KB（資料日 %s，回看 %d 天）"
          % (OUT, len(lines), os.path.getsize(OUT) // 1024, recent[0], len(recent)))
    return len(lines)


# ---------------------------------------------------------------- 主程式

def run_range(days_from, days_to):
    now = datetime.now(T.TPE)
    base = now.date()
    known = T.read_days()          # 拿行情的日曆當基準，非交易日不用白跑
    todo = []
    for back in range(days_to, days_from + 1):
        d = base - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y%m%d")
        if known.get(ds) == 0:              # 已知的放假日
            continue
        if ds not in known:                 # 行情都還沒抓過，籌碼也不用抓
            continue
        if have_day(ds):
            continue
        todo.append(d)
    todo.sort()
    print("待抓 %d 個交易日的籌碼" % len(todo))

    ok = miss = 0
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        day = datetime(d.year, d.month, d.day, tzinfo=T.TPE)
        ds = day.strftime("%Y%m%d")
        try:
            rows = fetch_chip_day(day)
        except Exception as e:
            print("  %s 例外：%s" % (ds, e))
            miss += 1
            continue
        if rows:
            write_day(ds, rows)
            ok += 1
        else:
            miss += 1
        if i % 10 == 0 or i == len(todo):
            el = time.time() - t0
            print("  進度 %d/%d　成功 %d、沒資料 %d　已用 %.0f 分、預估剩 %.0f 分"
                  % (i, len(todo), ok, miss, el / 60,
                     (len(todo) - i) * (el / i) / 60))
    return ok


def run_latest():
    """跟著 market.txt 的資料日走，確保籌碼和行情是同一天。"""
    import json
    if not os.path.exists(T.META):
        print("找不到 meta.json，先跑 fetch.py。")
        return 0
    meta = json.load(open(T.META, encoding="utf-8"))
    ds = str(meta.get("date") or "")
    if len(ds) != 8:
        print("meta.json 沒有資料日。")
        return 0
    if have_day(ds):
        print("籌碼 %s 已經有了，不重抓。" % ds)
        return 0
    day = datetime(int(ds[:4]), int(ds[4:6]), int(ds[6:]), tzinfo=T.TPE)
    print("抓 %s 的籌碼 ..." % ds)
    rows = fetch_chip_day(day)
    if not rows:
        print("  四個來源都沒給資料（可能還沒出檔）。")
        return 0
    write_day(ds, rows)
    print("  %d 檔" % len(rows))
    return len(rows)


def main():
    args = [a for a in sys.argv[1:] if a != "--derive"]
    if "--derive" in sys.argv[1:]:
        derive()
        return
    if len(args) >= 2:
        run_range(int(args[0]), int(args[1]))
    else:
        run_latest()
    derive()


if __name__ == "__main__":
    main()
