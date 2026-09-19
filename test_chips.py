#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
籌碼解析器的測試。夾具是從證交所／櫃買／集保實際回來的資料剪下來的，
欄位順序、空白、逗號、民國年都照原樣保留。

跑法：python test_chips.py
"""

import os
import shutil
import sys
import tempfile

import twse as T
import chips
import tdcc

FAIL = []


def eq(name, got, want):
    if got != want:
        FAIL.append("%s：得到 %r，應該是 %r" % (name, got, want))
        print("  ✗ %s  得到 %r，應該是 %r" % (name, got, want))
    else:
        print("  ✓ %s = %r" % (name, got))


def close(name, got, want, tol=1e-6):
    if got is None or abs(got - want) > tol:
        FAIL.append("%s：得到 %r，應該約 %r" % (name, got, want))
        print("  ✗ %s  得到 %r，應該約 %r" % (name, got, want))
    else:
        print("  ✓ %s ≈ %r" % (name, got))


# ---------------------------------------------------------------- 夾具

T86 = {
    "stat": "OK", "date": "20260918",
    "fields": ["證券代號", "證券名稱",
               "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
               "外陸資買賣超股數(不含外資自營商)",
               "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
               "投信買進股數", "投信賣出股數", "投信買賣超股數",
               "自營商買賣超股數",
               "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)",
               "自營商買賣超股數(自行買賣)",
               "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)",
               "三大法人買賣超股數"],
    "data": [
        ["2330", "台積電          ", "32,975,037", "26,935,685", "6,039,352",
         "0", "0", "0", "1,615,969", "1,137,568", "478,401", "1,361,432",
         "1,064,550", "86,005", "978,545", "422,996", "40,109", "382,887",
         "7,879,185"],
        ["2317", "鴻海            ", "25,934,354", "32,631,984", "-6,697,630",
         "0", "0", "0", "751,389", "1,295,558", "-544,169", "1,212,845",
         "1,073,400", "197,000", "876,400", "490,294", "153,849", "336,445",
         "-6,028,954"],
    ],
}

MARGN = {
    "stat": "OK", "date": "20260918",
    "tables": [
        {"title": "融資融券彙總", "fields": [], "data": []},
        {"title": "115年09月18日 融資融券彙總 (股票)",
         "fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額",
                    "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還",
                    "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
         "data": [
             ["2330", "台積電", "472", "834", "23", "29,148", "28,763",
              "6,483,092", "1", "5", "0", "11", "15", "6,483,092", "1", " "],
         ]},
    ],
}

TPEX_INSTI = {
    "tables": [
        {"title": "三大法人買賣明細資訊",
         "fields": ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7
                   + ["三大法人買賣超股數合計"],
         "data": [
             ["6488", "環球晶",
              "7,333,698", "6,931,615", "402,083",      # 外資不含自營
              "0", "0", "0",                            # 外資自營
              "7,333,698", "6,931,615", "402,083",      # 外資合計
              "51,600", "160,083", "-108,483",          # 投信
              "152,294", "59,600", "92,694",            # 自營自行
              "304,678", "228,640", "76,038",           # 自營避險
              "456,972", "288,240", "168,732",          # 自營合計
              "462,332"],
             # 故意放一筆加總對不上的，應該被擋掉
             ["9999", "亂數股",
              "1,000", "0", "1,000", "0", "0", "0", "1,000", "0", "1,000",
              "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
              "999,999"],
         ]},
        {"title": "空表", "fields": [], "data": []},
    ],
}

TPEX_MARGIN = {
    "tables": [
        {"title": "上櫃股票融資融券餘額",
         "fields": ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償",
                    "資餘額", "資屬證金", "資使用率(%)", "資限額",
                    "前券餘額(張)", "券賣", "券買", "券償", "券餘額",
                    "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"],
         "data": [
             ["6488", "環球晶", "12,621", "883", "469", "5", "13,030", "213",
              "10.9", "119,528", "325", "277", "131", "0", "471", "0",
              "0.39", "119,528", "18", ""],
         ]},
    ],
}

TDCC_CSV = """資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
20260918,2330  ,1,2496562,291447275,1.12
20260918,2330  ,2,451879,864178840,3.33
20260918,2330  ,3,52850,378241832,1.45
20260918,2330  ,4,17488,214518692,0.82
20260918,2330  ,5,8103,142463307,0.54
20260918,2330  ,6,7842,191883324,0.73
20260918,2330  ,7,3660,127097544,0.49
20260918,2330  ,8,2045,92232393,0.35
20260918,2330  ,9,4092,285523487,1.10
20260918,2330  ,10,2058,288145154,1.11
20260918,2330  ,11,1310,367509037,1.41
20260918,2330  ,12,577,280496503,1.08
20260918,2330  ,13,351,244673997,0.94
20260918,2330  ,14,220,197248393,0.76
20260918,2330  ,15,1481,21966711289,84.70
20260918,2330  ,16,1,1000,0.00
20260918,2330  ,17,3050518,25932370067,100.00
20260918,000218,1,0,0,0.00
20260918,000218,17,0,0,0.00
"""


# ---------------------------------------------------------------- 測試

def test_twse_insti():
    print("\n[上市三大法人 T86]")
    got = chips.fetch_twse_insti.__wrapped__(T86) if hasattr(
        chips.fetch_twse_insti, "__wrapped__") else None
    # 直接把 get_json 換掉比較乾淨
    orig, T.get_json = T.get_json, lambda *a, **k: T86
    try:
        from datetime import datetime
        out = chips.fetch_twse_insti(datetime(2026, 9, 18))
    finally:
        T.get_json = orig
    eq("檔數", len(out), 2)
    close("2330 外資（張）", out["2330"]["fgn"], 6039.352)
    close("2330 投信（張）", out["2330"]["inv"], 478.401)
    close("2330 自營避險（張）", out["2330"]["dlrHedge"], 382.887)
    close("2317 外資是賣超", out["2317"]["fgn"], -6697.630)


def test_twse_margin():
    print("\n[上市融資融券 MI_MARGN]")
    orig, T.get_json = T.get_json, lambda *a, **k: MARGN
    try:
        from datetime import datetime
        out = chips.fetch_twse_margin(datetime(2026, 9, 18))
    finally:
        T.get_json = orig
    eq("2330 融資餘額（張）", out["2330"]["mgn"], 28763.0)
    eq("2330 融資前日", out["2330"]["mgnPrev"], 29148.0)
    eq("2330 融券餘額", out["2330"]["shrt"], 15.0)


def test_tpex_insti():
    print("\n[上櫃三大法人]")
    orig, T.get_json = T.get_json, lambda *a, **k: TPEX_INSTI
    try:
        from datetime import datetime
        out = chips.fetch_tpex_insti(datetime(2026, 9, 18))
    finally:
        T.get_json = orig
    eq("加總對不上的被擋掉", "9999" in out, False)
    close("6488 外資（張）", out["6488"]["fgn"], 402.083)
    close("6488 投信是賣超", out["6488"]["inv"], -108.483)
    close("6488 自營避險", out["6488"]["dlrHedge"], 76.038)


def test_tpex_margin():
    print("\n[上櫃融資融券]")
    orig, T.get_json = T.get_json, lambda *a, **k: TPEX_MARGIN
    try:
        from datetime import datetime
        out = chips.fetch_tpex_margin(datetime(2026, 9, 18))
    finally:
        T.get_json = orig
    eq("6488 融資餘額", out["6488"]["mgn"], 13030.0)
    eq("6488 融資前日", out["6488"]["mgnPrev"], 12621.0)
    eq("6488 融券餘額", out["6488"]["shrt"], 471.0)


def test_tdcc():
    print("\n[集保股權分散]")
    date, rows = tdcc.parse(TDCC_CSV)
    eq("資料日期", date, "20260918")
    close("2330 大戶（千張以上）%", rows["2330"]["big"], 84.70)
    close("2330 散戶（百張以下）%", rows["2330"]["small"],
          1.12 + 3.33 + 1.45 + 0.82 + 0.54 + 0.73 + 0.49 + 0.35 + 1.10)
    eq("2330 集保戶數", rows["2330"]["holders"], 3050518.0)
    eq("全空的那檔不列入", "000218" in rows, False)


def test_streak():
    print("\n[連買連賣天數]")
    eq("連買 3 天", chips.streak([5, 2, 1, -3, 4]), 3)
    eq("連賣 2 天", chips.streak([-1, -9, 4, 5]), -2)
    eq("今天平盤就是 0", chips.streak([0, 5, 5]), 0)
    eq("中間有 None 就斷", chips.streak([3, 2, None, 4]), 2)
    eq("空的", chips.streak([]), 0)


def test_roundtrip_and_derive():
    print("\n[每日檔讀寫 + chips.txt 衍生]")
    tmp = tempfile.mkdtemp()
    old_data, old_chip, old_tdcc, old_out = (
        T.DATA, chips.CHIP_DIR, chips.TDCC_DIR, chips.OUT)
    T.DATA = tmp
    chips.CHIP_DIR = os.path.join(tmp, "chip")
    chips.TDCC_DIR = os.path.join(tmp, "tdcc")
    chips.OUT = os.path.join(tmp, "chips.txt")
    try:
        # 三天：投信連買 2 天（今天 +30、昨天 +10、前天 -5）
        chips.write_day("20260916", {"1101": {
            "fgn": -1.5, "inv": -5, "dlrSelf": 0, "dlrHedge": 1,
            "mgn": 900, "mgnPrev": 880, "shrt": 40, "shrtPrev": 38}})
        chips.write_day("20260917", {"1101": {
            "fgn": 2.25, "inv": 10, "dlrSelf": 0, "dlrHedge": 1,
            "mgn": 950, "mgnPrev": 900, "shrt": 45, "shrtPrev": 40}})
        chips.write_day("20260918", {"1101": {
            "fgn": 3.5, "inv": 30, "dlrSelf": 0, "dlrHedge": 2,
            "mgn": 1000, "mgnPrev": 950, "shrt": 50, "shrtPrev": 45}})

        back = chips.read_day("20260918")
        close("小數有存回來", back["1101"]["fgn"], 3.5)

        os.makedirs(chips.TDCC_DIR)
        for d, big in (("20260911", "40.00"), ("20260918", "42.50")):
            with open(os.path.join(chips.TDCC_DIR, d + ".txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("code,big,small,holders\n1101,%s,30.00,120000\n" % big)

        eq("chips.txt 檔數", chips.derive(), 1)
        head, line = open(chips.OUT, encoding="utf-8").read().strip().split("\n")
        cols = head.split("|")[-1].split(",")
        v = dict(zip(cols, line.split(",")))
        eq("資料日寫在表頭", head.split("|")[1], "20260918")
        eq("投信連買天數", v["invD"], "2")
        # 外資 +3.5 / +2.25 / −1.5 → 只有兩天是買超
        eq("外資連買天數", v["fgnD"], "2")
        eq("融資增減", v["mgn"] + "/" + v["mgnChg"], "1000/50")
        eq("券資比 50/1000", v["sr"], "5.0")
        eq("大戶比率", v["big"], "42.50")
        eq("大戶週變化", v["bigChg"], "2.50")
        eq("散戶比率", v["small"], "30.00")
        eq("集保戶數", v["holders"], "120000")
    finally:
        T.DATA, chips.CHIP_DIR, chips.TDCC_DIR, chips.OUT = (
            old_data, old_chip, old_tdcc, old_out)
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_sources():
    print("\n[來源掛掉時不能炸]")
    orig, T.get_json = T.get_json, lambda *a, **k: None
    try:
        from datetime import datetime
        d = datetime(2026, 9, 18)
        eq("T86 回 None", chips.fetch_twse_insti(d), {})
        eq("融資回 None", chips.fetch_twse_margin(d), {})
        eq("上櫃法人回 None", chips.fetch_tpex_insti(d), {})
        eq("上櫃融資回 None", chips.fetch_tpex_margin(d), {})
    finally:
        T.get_json = orig
    eq("集保空字串", tdcc.parse("")[1], {})
    eq("集保亂碼", tdcc.parse("亂七八糟\n沒有逗號")[1], {})


def main():
    for fn in (test_twse_insti, test_twse_margin, test_tpex_insti,
               test_tpex_margin, test_tdcc, test_streak,
               test_roundtrip_and_derive, test_empty_sources):
        fn()
    print("\n" + "=" * 50)
    if FAIL:
        print("失敗 %d 項：" % len(FAIL))
        for f in FAIL:
            print("  - " + f)
        sys.exit(1)
    print("全部通過。")


if __name__ == "__main__":
    main()
