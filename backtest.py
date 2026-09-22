#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把每一個篩選情境拿回過去每一天重跑一次，看當天被選出來的股票接下來漲跌如何。

做法
  1. data/stock/<代號>.txt 有每檔逐日 OHLCV（268 個交易日）。
     對第 i 天，把序列切到 closes[:i+1] 丟進 indicators.compute() ——
     跟正式網站用的是同一支函式，不是另外寫一份，才不會測到不一樣的東西。
  2. data/chip/<日期>.txt 有每日三大法人與融資融券，同樣切到當天為止，
     用 chips.py 的 streak() 與 margin_cost() 算連買天數、融資維持率。
  3. 套上 index.html 裡每個情境的條件，拿到當天的入選名單。
  4. 往後看 1/5/10/20 個交易日的報酬，跟「同一天所有通過流動性門檻的股票」
     的平均比較 —— 大盤本來就在漲的時候，選股會跟著漲，不減掉就是自欺欺人。
  5. `--exits` 另外跑一份「條件出場」：不抱固定天數，照講義的方式賣
     （離開上軌的黑K、跌破月線、第五日出清）。詳見下面「條件出場」那一段。

沒辦法測的
  * 集保大戶／散戶／戶數：data/tdcc 只有一期，沒有歷史 → c1、c3 跳過。
  * 券商分點主力：本來就拿不到。
  * 乖離年線要 MA240，前 239 天算不出來 → p4、p6 可用的天數很少，會標出來。

注意事項（結果要這樣讀）
  * 漲跌幅用收盤價相減算，沒有還原除權息 —— 除息日會被當成下跌。
  * 週轉率用現在的股本回推，中間有增減資的話會偏掉。
  * 樣本期間只有 2025-08 ~ 2026-09，一年多、一種盤而已。
    這是「這套條件在這段期間長什麼樣」，不是「這套條件會賺錢」。
"""

import os
import sys
import json
import random
from collections import defaultdict

import twse as T
import indicators as I
import chips as C

HORIZONS = [1, 5, 10, 20]
# 「隔天跳空」當成一個特殊的持有期：訊號日收盤 → 隔天開盤。
# 盤後選股吃不到這一段，但要知道它有多大 —— 有些條件的價值其實整個在跳空上。
GAP = "g"
KEYS = [GAP] + HORIZONS


def nw_lag(k):
    """Newey-West 要修正幾期重疊。跳空不重疊，所以是 1。"""
    return 1 if k == GAP else k


WARMUP = 30          # 算得出 bbWMin10 的最低天數
MARGIN_MIN_DAYS = 120  # 融資維持率至少要這麼多天的籌碼史才夠可信


# ---------------------------------------------------------------- 讀資料

STATES = ["貼上軌", "上半部", "下半部", "貼下軌"]


def boll_series(c):
    """每日的 (月線, 上軌, 下軌, 位階)，前 19 天是 None。

    跟 indicators.boll() 同一套定義（母體標準差），只是改成滾動算。
    狀態轉移和條件出場都要看「未來第 N 天」的布林，一天一天重算會太慢。
    """
    n = len(c)
    ma = [None] * n
    up = [None] * n
    lo = [None] * n
    pos = [None] * n
    s = s2 = 0.0
    for i in range(n):
        s += c[i]
        s2 += c[i] * c[i]
        if i >= 20:
            s -= c[i - 20]
            s2 -= c[i - 20] * c[i - 20]
        if i >= 19:
            m = s / 20
            var = max(0.0, s2 / 20 - m * m)
            sd = var ** 0.5
            ma[i] = m
            up[i] = m + 2 * sd
            lo[i] = m - 2 * sd
            pos[i] = ((c[i] - m) / (up[i] - m) * 10) if up[i] > m else 0.0
    return ma, up, lo, pos


def state_of(p):
    if p is None:
        return None
    if p >= 8:
        return "貼上軌"
    if p > 0:
        return "上半部"
    if p > -8:
        return "下半部"
    return "貼下軌"


def load_stocks():
    """code -> dict(dates, o, h, l, c, lot, wan)，皆由舊到新。"""
    out = {}
    for fn in sorted(os.listdir(T.STOCK_DIR)):
        if not fn.endswith(".txt"):
            continue
        code = fn[:-4]
        dates, o, h, l, c, lot, wan = [], [], [], [], [], [], []
        for d, line in sorted(T.read_stock(code).items()):
            p = line.split(",")
            if len(p) < 7:
                continue
            try:
                cc = float(p[4])
            except ValueError:
                continue
            if cc <= 0:
                continue
            dates.append(d)
            o.append(T.pn(p[1]))
            h.append(T.pn(p[2]))
            l.append(T.pn(p[3]))
            c.append(cc)
            lot.append(T.pn(p[5]) or 0.0)
            wan.append(T.pn(p[6]))
        if len(c) >= WARMUP + max(HORIZONS):
            ma20, bbup, bblo, pos = boll_series(c)
            out[code] = dict(dates=dates, o=o, h=h, l=l, c=c, lot=lot, wan=wan,
                             pos=pos, ma20=ma20, bbUp=bbup, bbLo=bblo)
    return out


def load_market_meta():
    """code -> (mkt, name, kshares)。股本只有現在這一版，沒有歷史。"""
    info = {}
    path = os.path.join(T.DATA, "market.txt")
    with open(path, encoding="utf-8") as fh:
        fh.readline()
        for line in fh:
            p = line.rstrip("\n").split(",")
            if len(p) < 10:
                continue
            info[p[0]] = (p[2], p[1], T.pn(p[9]))
    return info


def load_chip_days():
    days = C.chip_days()
    return days, {d: C.read_day(d) for d in days}


# ---------------------------------------------------------------- 籌碼衍生

def chip_row(code, di, days, cache, price_hist, mkt, cost_cache):
    """把某一天的籌碼算成跟 chips.txt 同樣的欄位。di 是 days 裡的索引。"""
    cur = cache[days[di]].get(code)
    if cur is None:
        return None
    recent = [cache[days[j]].get(code, {}).get("inv")
              for j in range(di, max(-1, di - C.LOOKBACK), -1)]
    recent_f = [cache[days[j]].get(code, {}).get("fgn")
                for j in range(di, max(-1, di - C.LOOKBACK), -1)]
    mgn = cur.get("mgn")
    mgn_prev = cur.get("mgnPrev")
    shrt = cur.get("shrt")
    row = {
        "inv": cur.get("inv"),
        "invD": C.streak(recent),
        "fgn": cur.get("fgn"),
        "fgnD": C.streak(recent_f),
        "mgn": mgn,
        "mgnChg": (mgn - mgn_prev) if (mgn is not None and mgn_prev is not None) else None,
        "sr": (shrt / mgn * 100) if (shrt is not None and mgn and mgn > 0) else None,
        "dlrH": cur.get("dlrHedge"),
        "mgnRate": None,
    }
    # 融資維持率：要往回看一段才估得出平均成本，歷史不夠長就不給
    if mgn and mgn > 0 and di + 1 >= MARGIN_MIN_DAYS:
        lo = max(0, di + 1 - C.MARGIN_LOOKBACK)
        window = days[lo:di + 1]
        mcost, _conf = margin_cost_hist(code, window, cache, price_hist)
        px = price_hist.get(code, {}).get(days[di])
        if mcost and mcost > 0 and px:
            r = C.MARGIN_RATE.get(mkt, C.MARGIN_DEFAULT)
            row["mgnRate"] = px / (mcost * r) * 100
    return row


def margin_cost_hist(code, window, cache, price_hist):
    """chips.py 的 margin_cost，但價格從記憶體裡的歷史拿，不重讀檔案。"""
    px = price_hist.get(code)
    if not px:
        return None, None
    cost, bal, added = None, 0.0, 0.0
    for d in window:
        r = cache.get(d, {}).get(code)
        if r is None:
            continue
        m = r.get("mgn")
        p = px.get(d)
        if m is None or m < 0 or p is None:
            continue
        if bal <= 0:
            if m > 0:
                cost, added = p, m
            bal = m
            continue
        delta = m - bal
        if delta > 0:
            cost = (cost * bal + p * delta) / m
            added += delta
        bal = m
    if not cost or bal <= 0:
        return None, None
    return cost, min(1.0, added / bal)


# ---------------------------------------------------------------- 情境

def nn(x):
    return x is not None


# 每個情境的條件，欄位名稱跟 index.html 的 PRESETS 一模一樣。
# val=成交金額億、lot=張、turn=週轉率%、amp=振幅%、chg1/2=漲跌幅、
# pos1/2=收盤位置、slope1/2=月線斜率、bpos1/2=位階、bw1/2=帶寬、
# bias1/2=乖離年線、vr=均量比、runup/rundn=連漲連跌、
# supt=上通斜率上限、supt2=上通斜率下限、slot=下通斜率上限、
# wchg=帶寬變化下限、wmin=前10日最小帶寬上限、
# invd=投信連買、inv=投信買超、invs=投信賣超(填正數)、fgn/fgnd=外資、
# sr=券資比、mgnc1/2=融資增減、mr1/2=融資維持率
PRESETS = {
    "p1": ("月線上升強勢股", "多", dict(val=1, lot=1000, slope1=1, bpos1=0, vr=1)),
    "p2": ("回檔量縮待買", "多", dict(val=1, slope1=0, chg2=0, bpos1=-6, bpos2=4)),
    "p3": ("連跌反彈候選", "多", dict(val=1, rundn=3, vr=1.5)),
    "p4": ("高檔出貨紅K", "空", dict(val=1, lot=1000, bias1=40, chg1=3, slope2=0, supt=3)),
    "p5": ("月線下彎量增跌", "空", dict(val=1, slope2=-1, bpos2=0, chg2=0, supt=3)),
    "p6": ("價背離空（半套）", "空", dict(lot=300, chg1=3, slope2=0, bias1=20, supt=3)),
    "p7": ("隔日沖出貨警示", "空", dict(val=1, vr=3, chg1=5)),
    "b1": ("往上帶量開布林", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                   wmin=10, bwy=15)),
    "b2": ("往下帶量開布林", "空", dict(val=1, wchg=0.5, slot=-1, bpos2=0, chg2=-1, vr=1.5,
                                   wmin=10, supt=3, bwy=15)),
    "b3": ("反彈放空點", "空", dict(val=1, slope2=0, bpos1=0, supt=3)),
    "c2": ("投信認養股", "多", dict(val=1, invd=3, slope1=0, bpos1=0)),
    "c4": ("投信倒貨融資接", "空", dict(val=1, invs=100, mgnc1=0, slope2=0.5)),
    "c5": ("軋空候選", "多", dict(val=1, sr=20, slope1=0, chg1=1, vr=1.5)),
    "c6": ("融資斷頭壓力", "空", dict(val=1, mr2=130, slope2=0)),
    "c7": ("下跌但融資大減", "多", dict(val=1, mr1=160, chg2=0, mgnc2=-100)),
    "hot": ("高週轉高振幅", "—", dict(val=1, turn=10, amp=5)),
    # b1old：修正前的版本，留著當對照組。
    # 兩者唯一差別是有沒有 bwy（昨日還是縮的），差異就是「抓對時機」值多少。
    "b1old": ("往上開布林（舊版·會追高）", "多",
              dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10)),
    # ── 這一批是照講義補的，之前漏掉或做反了 ──────────────────
    # s1：教材「上通斜率 > 3，超級強勢股，若要空只能短空」。
    #     我原本把 supt:3 當成全面避開，等於把這個條件整個擋掉。
    #     拆解測試顯示：空它 1 天原始 +0.62%（t=+2.4），空一個月 -5.26%。
    #     教材是對的，所以這裡把它單獨拉出來測，而且刻意不設持有期限制，
    #     讓報告自己標出「只能短空」。
    "s1": ("強勢股隔日空", "空", dict(val=1, lot=1000, supt2=3)),
    # b4：教材「上通斜率在高點、沿著布林上軌 ＝ 強勢上漲發動中」。
    #     跟 b1（擠壓→剛打開）是兩件事：b1 抓發動那一天，b4 抓發動中。
    #     狀態轉移統計：這批股票 5 天內掉到中軌以下只有 4.4%，
    #     上軌沒在漲的那組是 22.4%。差 5 倍。
    "b4": ("貼上軌強勢走", "多", dict(val=1, bpos1=8, supt2=3)),
    # c8：教材分兩句，我原本只做了第二句。
    #     「第一天大買，隔天一早容易有追價買盤」← 短線／跳空
    #     「連買時適合偏多操作」← 波段（這句是既有的 c2）
    #     投信不能當沖，買了就是波段，所以第一天的追價是乾淨的。
    "c8": ("投信首日大買", "多", dict(val=1, invd=1, invd2=1, inv=300)),
    # c9：教材「自營避險買超通常跟權證有關。若權證買超的是隔日沖分點，
    #     隔天早盤（十點前）容易有權證賣壓 → 容易小殺」。
    #     這是現有資料裡最接近「分點主力」的東西，之前完全沒用過。
    #     門檻 3% 大約落在全市場 95 百分位。
    "c9": ("自營避險大買", "空", dict(val=1, lot=1000, dlr=3)),
    # 對照組：同樣的流動性門檻，但隨機挑 30 檔。
    # 這一列的超額報酬必須接近 0、t 值必須接近 0，否則就是量測方法本身有偏差，
    # 上面每一列都不能信。這是整份回測的體溫計。
    "rnd10": ("隨機對照 10 檔", "多", dict(val=1, _random=10, _seed=1)),
    "rnd30": ("隨機對照 30 檔", "多", dict(val=1, _random=30, _seed=2)),
    "rnd60": ("隨機對照 60 檔", "多", dict(val=1, _random=60, _seed=3)),
}
CONTROL = {"rnd10", "rnd30", "rnd60"}

NEED_CHIP = {"c2", "c4", "c5", "c6", "c7", "c8", "c9"}
NEED_MA240 = {"p4", "p6"}

# 拆解測試：不是測「情境」，是把講義裡單獨一條規則拉出來，跟它的相反面比。
# 情境是好幾條疊在一起，測出來好或不好都不知道是哪一條的功勞；
# 這裡一次只動一個條件，其他都一樣，差異才歸得出去。
# (組名, 這組在驗哪一條, [(標籤, 方向, 條件), ...])
RULES = [
    ("開布林要抓「正在開」不是「開完了」",
     "「前 10 日最小帶寬」是滾動窗口，突破之後那個壓縮期的舊值還留在窗口裡 10 天，"
     "所以條件在突破後好幾天都還成立 —— 選到的是已經開完的。"
     "「昨日帶寬」＝今日帶寬 − 今日變化，要求昨天還是縮的，才是真的剛發動。"
     "這一組在掃門檻：看是一整片高原（真效果）還是孤立尖峰（雜訊）。", [
        ("不限昨日帶寬（舊版）", "多",
         dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10)),
        ("昨日帶寬 ≤ 25%", "多",
         dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=25)),
        ("昨日帶寬 ≤ 20%", "多",
         dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=20)),
        ("昨日帶寬 ≤ 15%", "多",
         dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=15)),
        ("昨日帶寬 ≤ 12%", "多",
         dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=12)),
    ]),
    ("上軌斜率門檻會不會漏掉發動當天",
     "布林上軌是 20 日均線加標準差，第一天爆量大漲對它的影響有限，要隔一兩天才反應。"
     "用「上軌斜率 ≥ 1%」當發動訊號天生慢半拍 —— 2468 在 2026-09-11 爆 16 倍量、"
     "漲 10%、位階從 −7.6 跳到 +9.5，就是因為上軌斜率只有 0.28% 而漏掉。"
     "這一組固定昨日帶寬 ≤ 15%，只動上軌斜率門檻。", [
        ("上軌斜率 ≥ 1%（現行）", "多",
         dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=15)),
        ("上軌斜率 ≥ 0%", "多",
         dict(val=1, wchg=0.5, supt2=0, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=15)),
        ("不限上軌斜率", "多",
         dict(val=1, wchg=0.5, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=15)),
    ]),
    ("做空找位階高", "講義：「做多找翹上軌；做空找位階高」——"
                  "空單要等月線下彎的股票反彈到中軌之上才進，不是追殺已經在低點的。", [
        ("月線下彎＋位階 > 0（反彈才空）", "空", dict(val=1, slope2=0, bpos1=0)),
        ("月線下彎＋位階 < 0（追殺低點）", "空", dict(val=1, slope2=0, bpos2=0)),
        ("月線下彎，不管位階", "空", dict(val=1, slope2=0)),
    ]),
    ("做空避開上軌翹的", "講義做空實務：「避開上通斜率超過 3%」、"
                    "做空結論：「避開強勢股，強勢股嘎空太恐怖」。", [
        ("上軌斜率 > 3% 拿來空", "空", dict(val=1, supt2=3)),
        ("上軌斜率 0～3% 拿來空", "空", dict(val=1, supt2=0, supt=3)),
        ("上軌斜率 < 0 拿來空", "空", dict(val=1, supt=0)),
    ]),
    ("下軌斜率的做空綠燈", "參數字典：下通斜率 < −3% 是綠燈。這一組是在檢查這個門檻。", [
        ("下軌斜率 < −3% 拿來空", "空", dict(val=1, slot=-3)),
        ("下軌斜率 −3～0% 拿來空", "空", dict(val=1, slot=0)),
    ]),
    ("月線方向", "講義：月線斜率 > 1 超級強勢、< −1 超級弱勢；"
              "做多結論看月線升、做空結論看月線下彎。", [
        ("月線斜率 > 1% 做多", "多", dict(val=1, slope1=1)),
        ("月線斜率 0～1% 做多", "多", dict(val=1, slope1=0, slope2=1)),
        ("月線下彎還去做多", "多", dict(val=1, slope2=0)),
    ]),
    ("上軌翹做多", "講義多方四大條件之一：「上軌翹」。同樣是月線升、位階翻正，"
                "差別只在上軌有沒有翹。", [
        ("月線升＋位階 > 0＋上軌翹", "多", dict(val=1, slope1=0, bpos1=0, supt2=0)),
        ("月線升＋位階 > 0＋上軌沒翹", "多", dict(val=1, slope1=0, bpos1=0, supt=0)),
    ]),
]


# ---------------------------------------------------------------- 條件出場
#
# 固定抱 1/5/10/20 天測的不是講義在講的東西。講義的出場全是條件式的：
#   「離開布林上軌的黑K 是短線賣點」「跌破月線時就出場」「第五日出清」
# 這一段就是讓回測可以照那樣出場。
#
# 時間軸（跟進場同一套誠實標準）：
#   訊號日 i 收盤看到 → 第 i+1 天開盤買進
#   之後每天收盤檢查出場條件 → 觸發當天收盤 (retC) 與觸發隔天開盤 (retO) 都記
#   retO 是實際做得到的（盤後才看得到黑K），retC 是上限參考。
#
# 出場條件（EXIT 規格）
#   maxd      最多抱幾個交易日，到了就強制出清（講義「第五日出清」用這個）
#   leaveup   多方：離開上軌的黑K。值是位階門檻（8 ＝ 貼上軌）
#             要先「貼過上軌」才算數 —— 沒貼過就談不上離開，
#             否則像開布林那種進場當天位階才 +2 的，第一天就會被判出場。
#   leavelo   空方鏡像：離開下軌的紅K
#   belowma   多方：收盤跌破月線就走     abovema  空方：收盤站上月線就回補
#   belowpos  多方：位階跌破這個值就走   （空方自動鏡像成「位階站上 −值」）
#   stop/take 停損／停利，用收盤價判（日線看不到盤中，所以不能假裝停在停損價）
#
# 判定順序：停損停利 → 離開軌道 → 月線 → 位階 → 到期。
# 只看收盤，因為這是盤後工具，盤中資料我們沒有。

MAXD_CAP = 20


def exit_scan(st, i, side, ex):
    """從訊號日 i 出發，走到出場為止。回 dict 或 None（資料不夠）。

    n = 持有交易日數（進場日算第 1 天），出場判定日是 i+n。
    """
    c, o = st["c"], st["o"]
    ma, pos = st["ma20"], st["pos"]
    a = i + 1
    entry = o[a] if a < len(o) else None
    if not entry or entry <= 0:
        return None
    maxd = min(ex.get("maxd", MAXD_CAP), MAXD_CAP)
    long_ = (side != "空")
    band = ex.get("leaveup") if long_ else ex.get("leavelo")

    # 「離開上軌」要先貼過上軌。訊號日本身就貼著的話，一進場就算貼過。
    armed = False
    if band is not None and pos[i] is not None:
        armed = (pos[i] >= band) if long_ else (pos[i] <= -band)

    for n in range(1, maxd + 1):
        j = i + n
        if j + 1 >= len(c):
            return None
        pc, po, px = c[j], o[j], pos[j]
        if pc is None or pc <= 0:
            return None
        reason = None

        ret_now = (pc / entry - 1) * 100
        pnl = ret_now if long_ else -ret_now
        if ex.get("stop") is not None and pnl <= ex["stop"]:
            reason = "停損"
        elif ex.get("take") is not None and pnl >= ex["take"]:
            reason = "停利"

        if reason is None and band is not None and px is not None:
            if armed:
                if long_ and px < band and po is not None and pc < po:
                    reason = "離開上軌黑K"
                elif (not long_) and px > -band and po is not None and pc > po:
                    reason = "離開下軌紅K"
            if reason is None:
                if (px >= band) if long_ else (px <= -band):
                    armed = True

        if reason is None and ma[j] is not None:
            if long_ and ex.get("belowma") and pc < ma[j]:
                reason = "跌破月線"
            elif (not long_) and ex.get("abovema") and pc > ma[j]:
                reason = "站上月線"

        if reason is None and ex.get("belowpos") is not None and px is not None:
            lim = ex["belowpos"]
            if long_ and px < lim:
                reason = "位階跌破"
            elif (not long_) and px > -lim:
                reason = "位階站上"

        if reason is None and n >= maxd:
            reason = "到期"

        if reason:
            nxt = o[j + 1]
            if not nxt or nxt <= 0:
                return None
            # 同一筆單「不設條件、抱滿 maxd 天」會是多少 —— 用來回答
            # 「這個出場規則到底有沒有加分」。跟大盤比會被持有天數的選擇效應污染：
            # 抱 2 天就跑的那些，本來就是走壞的那些，拿去跟「隨便一檔抱 2 天」比，
            # 本來就會輸。要判斷出場規則好不好，只能跟同一筆單抱滿比。
            jf = i + maxd
            fc = c[jf] if jf < len(c) else None
            fo = o[jf + 1] if (jf + 1) < len(o) else None
            return dict(n=n, reason=reason,
                        retC=(pc / entry - 1) * 100,
                        retO=(nxt / entry - 1) * 100,
                        fullC=((fc / entry - 1) * 100) if (fc and fc > 0) else None,
                        fullO=((fo / entry - 1) * 100) if (fo and fo > 0) else None)
    return None


# (標籤, 方向, 進場條件, 出場條件)
# 進場條件的欄位跟 PRESETS 一模一樣，差別只在出場。
EXIT_PRESETS = {
    # 講義 p179 的完整戰法：壓縮 → 帶量開布林進場 → 離開上軌黑K賣。
    # 固定天數測出來是反指標，但那是「抱滿 N 天」；這裡才是講義真正在說的做法。
    "x1": ("開布林→離開上軌黑K賣", "多",
           dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5, wmin=10, bwy=15),
           dict(leaveup=8, maxd=20)),
    # 目前唯一長樣本驗證會賺錢的條件，換成條件出場看能不能更好。
    "x2": ("貼上軌強勢走→離開上軌黑K賣", "多",
           dict(val=1, bpos1=8, supt2=3),
           dict(leaveup=8, maxd=20)),
    "x2b": ("貼上軌強勢走→跌破月線賣", "多",
            dict(val=1, bpos1=8, supt2=3),
            dict(belowma=True, maxd=20)),
    "x2s": ("貼上軌強勢走→離開上軌黑K賣＋停損7%", "多",
            dict(val=1, bpos1=8, supt2=3),
            dict(leaveup=8, stop=-7, maxd=20)),
    # 講義 p201-204 高檔出貨股放空術：乖離年線 > 30%、第一日反彈 > 3%，
    # 第二日進場，第五日出清。這是講義裡唯一有明確持有期的策略。
    "x3": ("高檔出貨股放空術（5日出清）", "空",
           dict(val=1, lot=1000, bias1=30, chg1=3),
           dict(maxd=5)),
    "x3m": ("高檔出貨股放空術（跌破月線前先回補）", "空",
            dict(val=1, lot=1000, bias1=30, chg1=3),
            dict(abovema=True, maxd=5)),
    # 講義空方：「跌破月線時就出場」的反面 —— 空單站上月線就回補。
    "x4": ("反彈放空點→站上月線回補", "空",
           dict(val=1, slope2=0, bpos1=0, supt=3),
           dict(abovema=True, maxd=20)),
    # 籌碼類（只有 247 天，不能跟上面比）
    "x5": ("投信認養股→跌破月線賣", "多",
           dict(val=1, invd=3, slope1=0, bpos1=0),
           dict(belowma=True, maxd=20)),
    # ── 對照與校驗 ────────────────────────────────────────────
    # 對照組：隨機 30 檔，但套上「一模一樣的出場規則」。
    #
    # 為什麼每一種出場規則都要有自己的對照組：出場條件本身就是一個壞日子
    # （「離開上軌的黑K」就是當天收黑）。一筆單在第 n 天因為黑K出場，
    # 拿去跟「同一天進場、同樣抱 n 天的全市場平均」比，本來就會輸 ——
    # 大盤那個平均沒有「最後一天必須收黑」這個條件。
    # 這個偏誤有多大，就是對照組那一列的數字。
    # **所以每一列要看的不是它自己的超額，是它跟同一種出場規則的對照組差多少。**
    "xr": ("　對照：隨機30→離開上軌黑K賣", "多",
           dict(val=1, _random=30, _seed=7),
           dict(leaveup=8, maxd=20)),
    "xr2": ("　對照：隨機30→跌破月線賣", "多",
            dict(val=1, _random=30, _seed=8),
            dict(belowma=True, maxd=20)),
    "xr3": ("　對照：隨機30空→站上月線回補", "空",
            dict(val=1, _random=30, _seed=9),
            dict(abovema=True, maxd=20)),
    "xr4": ("　對照：隨機30空→固定抱5天", "空",
            dict(val=1, _random=30, _seed=10),
            dict(maxd=5)),
    "xr5": ("　對照：隨機30空→站上月線回補（5天上限）", "空",
            dict(val=1, _random=30, _seed=11),
            dict(abovema=True, maxd=5)),
    # xchk5：出場規則只有「到期」，maxd=5。這一列的原始報酬必須跟主表
    #        「貼上軌強勢走」的 5 天幾乎一樣 —— 用來證明新路徑沒算錯。
    "xchk5": ("校驗：貼上軌強勢走 固定抱5天", "多",
              dict(val=1, bpos1=8, supt2=3),
              dict(maxd=5)),
}
EXIT_CONTROL = {"xr", "xr2", "xr3", "xr4", "xr5"}
EXIT_NEED_CHIP = {"x5"}
EXIT_NEED_MA240 = {"x3", "x3m"}


def passes(s, base, d, k):
    """base = 當天的價量衍生；d = 指標；k = 籌碼。回 True 表示入選。"""
    g = s.get   # 底線開頭的 key（例如 _random）不是篩選條件，下面都不會去讀
    if g("val") is not None and (base["valE"] is None or base["valE"] < g("val")):
        return False
    if g("lot") is not None and base["lot"] < g("lot"):
        return False
    if g("turn") is not None and (base["turn"] is None or base["turn"] < g("turn")):
        return False
    if g("amp") is not None and base["ampPct"] < g("amp"):
        return False
    if g("chg1") is not None and (base["chgPct"] is None or base["chgPct"] < g("chg1")):
        return False
    if g("chg2") is not None and (base["chgPct"] is None or base["chgPct"] > g("chg2")):
        return False
    if g("pos1") is not None and base["pos"] < g("pos1"):
        return False
    if g("pos2") is not None and base["pos"] > g("pos2"):
        return False

    pairs = [("slope1", "slope20", "min"), ("slope2", "slope20", "max"),
             ("bpos1", "bbPos", "min"), ("bpos2", "bbPos", "max"),
             ("bw1", "bbW", "min"), ("bw2", "bbW", "max"),
             ("bias1", "biasY", "min"), ("bias2", "biasY", "max"),
             ("vr", "vr20", "min"),
             ("supt", "slopeUp", "max"), ("supt2", "slopeUp", "min"),
             ("slot", "slopeLo", "max"),
             ("wchg", "bbWChg", "min"), ("wmin", "bbWMin10", "max")]
    for key, field, how in pairs:
        v = g(key)
        if v is None:
            continue
        x = d.get(field)
        if x is None:
            return False
        if how == "min" and x < v:
            return False
        if how == "max" and x > v:
            return False
    # bwy：昨日帶寬上限 = 今日帶寬 − 今日帶寬變化。
    # 「前 10 日最小帶寬」是滾動窗口，布林開完之後那個舊的壓縮值還留在窗口裡，
    # 所以突破後整整 10 天條件都還成立 —— 等於在選「已經開完的」而不是「正在開的」。
    # 加這條就是要求「昨天還是縮的、今天才打開」，把追高的那幾天擋掉。
    if g("bwy") is not None:
        if d.get("bbW") is None or d.get("bbWChg") is None:
            return False
        if d["bbW"] - d["bbWChg"] > g("bwy"):
            return False
    if g("runup") is not None and d["runUp"] < g("runup"):
        return False
    if g("rundn") is not None and d["runDn"] < g("rundn"):
        return False

    if any(g(x) is not None for x in ("invd", "invd2", "inv", "invs", "fgn", "fgnd",
                                      "sr", "mgnc1", "mgnc2", "mr1", "mr2", "dlr")):
        if k is None:
            return False
        if g("invd") is not None and k["invD"] < g("invd"):
            return False
        # invd2：連買天數上限。invd=1 且 invd2=1 就是「投信今天才開始買」——
        # 講義說追價效應在第一天最明顯，跟「連買好幾天」是兩件事。
        if g("invd2") is not None and k["invD"] > g("invd2"):
            return False
        # dlr：自營避險買超佔當日成交量的百分比。
        # 講義「自避」＝自營避險買超/成交量，跟權證有關。
        if g("dlr") is not None:
            dh = k.get("dlrH")
            if dh is None or not base["lot"] or base["lot"] <= 0:
                return False
            if dh / base["lot"] * 100 < g("dlr"):
                return False
        if g("inv") is not None and (k["inv"] is None or k["inv"] < g("inv")):
            return False
        if g("invs") is not None and (k["inv"] is None or k["inv"] > -g("invs")):
            return False
        if g("fgn") is not None and (k["fgn"] is None or k["fgn"] < g("fgn")):
            return False
        if g("fgnd") is not None and k["fgnD"] < g("fgnd"):
            return False
        if g("sr") is not None and (k["sr"] is None or k["sr"] < g("sr")):
            return False
        if g("mgnc1") is not None and (k["mgnChg"] is None or k["mgnChg"] < g("mgnc1")):
            return False
        if g("mgnc2") is not None and (k["mgnChg"] is None or k["mgnChg"] > g("mgnc2")):
            return False
        if g("mr1") is not None and (k["mgnRate"] is None or k["mgnRate"] < g("mr1")):
            return False
        if g("mr2") is not None and (k["mgnRate"] is None or k["mgnRate"] > g("mr2")):
            return False
    return True


# ---------------------------------------------------------------- 主流程

def base_metrics(st, i, kshares):
    h, l, c = st["h"][i], st["l"][i], st["c"][i]
    if h is None or l is None:
        return None
    a = h - l
    if a <= 0:            # 跟網站一樣：沒有振幅就不算（漲跌停鎖死）
        return None
    prev = st["c"][i - 1] if i >= 1 else None
    lot = st["lot"][i] or 0.0
    wan = st["wan"][i]
    return {
        "ampPct": a / c * 100 if c > 0 else 0,
        "pos": (c - l) / a,
        "chgPct": ((c / prev - 1) * 100) if (prev and prev > 0) else None,
        "lot": lot,
        "valE": (wan / 10000) if wan is not None else None,
        "turn": (lot / kshares * 100) if (kshares and kshares > 0) else None,
    }


def run(limit_days=None, verbose=True, exits=None):
    stocks = load_stocks()
    info = load_market_meta()
    chip_ds, chip_cache = load_chip_days()
    chip_idx = {d: i for i, d in enumerate(chip_ds)}

    # 給 margin_cost_hist 用的價格表
    price_hist = {code: dict(zip(st["dates"], st["c"])) for code, st in stocks.items()}

    # 全市場的交易日（用最常見的那條序列）
    counter = defaultdict(int)
    for st in stocks.values():
        for d in st["dates"]:
            counter[d] += 1
    all_days = sorted(d for d, n in counter.items() if n > len(stocks) * 0.5)
    if verbose:
        print("股票 %d 檔、交易日 %d 天（%s ~ %s）、籌碼日 %d 天"
              % (len(stocks), len(all_days), all_days[0], all_days[-1], len(chip_ds)))

    max_h = max(HORIZONS)
    maxd = 0
    if exits:
        maxd = max(min(ex.get("maxd", MAXD_CAP), MAXD_CAP)
                   for (_l, _s, _e, ex) in exits.values())
        # 條件出場最遠要看到「出場判定日的隔天開盤」，所以比 maxd 多一天
        max_h = max(max_h, maxd + 1)
    eval_days = all_days[WARMUP:len(all_days) - max_h]
    if limit_days:
        eval_days = eval_days[-limit_days:]

    # 收集：picks[preset][horizon] = [報酬...]，base[horizon] = [報酬...]
    picks = {p: {h: [] for h in KEYS} for p in PRESETS}
    counts = {p: [] for p in PRESETS}
    usable = {p: 0 for p in PRESETS}
    # 狀態轉移：選出來的股票，N 天後跑到布林的哪一區
    states = {p: {h: defaultdict(int) for h in HORIZONS} for p in PRESETS}
    bench = {h: [] for h in KEYS}
    bench_days = {h: [] for h in KEYS}   # 每日平均，用來配對比較
    day_bench = {}

    # 條件出場的收集：每一筆是一次進出
    xtrades = {p: [] for p in (exits or {})}
    xcounts = {p: [] for p in (exits or {})}
    xusable = {p: 0 for p in (exits or {})}

    pos_in = {code: {d: j for j, d in enumerate(st["dates"])}
              for code, st in stocks.items()}

    for dn_i, day in enumerate(eval_days):
        # 這一天所有股票的指標
        rows = []
        for code, st in stocks.items():
            i = pos_in[code].get(day)
            if i is None or i < WARMUP:
                continue
            if i + max_h >= len(st["c"]):
                continue
            mkt, name, ksh = info.get(code, ("1", code, None))
            base = base_metrics(st, i, ksh)
            if base is None:
                continue
            d = I.compute(st["c"][:i + 1], st["lot"][:i + 1])
            # 進場價用「隔天開盤」，不是訊號當天的收盤 ——
            # 這是盤後選股，名單出來的時候今天已經收了，最快也只能明天開盤才買得到。
            # 用當天收盤當進場價會憑空多賺一段開盤跳空，那是騙自己。
            entry = st["o"][i + 1] if (i + 1) < len(st["o"]) else None
            fwd = {}
            for h in HORIZONS:
                c1 = st["c"][i + h]
                fwd[h] = (c1 / entry - 1) * 100 if (entry and entry > 0) else None
            # 跳空：訊號日收盤 → 隔天開盤。這一段吃不到，但要看得到。
            c0 = st["c"][i]
            fwd[GAP] = (entry / c0 - 1) * 100 if (entry and c0 and c0 > 0) else None
            rows.append((code, mkt, base, d, fwd, i, st))

        # 基準：通過流動性門檻（成交金額 ≥ 1 億）的全市場平均
        liq = [r for r in rows if r[2]["valE"] is not None and r[2]["valE"] >= 1]
        bm, bmed = {}, {}
        for h in KEYS:
            vals = sorted(r[4][h] for r in liq if r[4][h] is not None)
            if vals:
                bm[h] = sum(vals) / len(vals)
                m = len(vals)
                bmed[h] = vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2
                bench[h].append(bm[h])
            else:
                bm[h] = bmed[h] = None
        day_bench[day] = bm

        # 條件出場的基準：持有天數每一筆都不一樣，不能跟固定 N 天的基準比。
        # 所以對 n = 1..maxd 各算一組「同一天、同樣從隔天開盤進、抱 n 天」的
        # 全市場平均與中位數 —— 一筆單抱幾天，就跟那個 n 的基準比。
        bmN = {"C": {}, "O": {}}
        bmedN = {"C": {}, "O": {}}
        if exits:
            ents = []
            for r in liq:
                st_, i_ = r[6], r[5]
                e_ = st_["o"][i_ + 1]
                if e_ and e_ > 0:
                    ents.append((st_, i_, e_))
            for n in range(1, maxd + 1):
                vc, vo = [], []
                for st_, i_, e_ in ents:
                    j_ = i_ + n
                    cj = st_["c"][j_]
                    if cj and cj > 0:
                        vc.append((cj / e_ - 1) * 100)
                    oj = st_["o"][j_ + 1]
                    if oj and oj > 0:
                        vo.append((oj / e_ - 1) * 100)
                for tag, vals in (("C", vc), ("O", vo)):
                    if not vals:
                        bmN[tag][n] = bmedN[tag][n] = None
                        continue
                    vals.sort()
                    bmN[tag][n] = sum(vals) / len(vals)
                    m_ = len(vals)
                    bmedN[tag][n] = (vals[m_ // 2] if m_ % 2
                                     else (vals[m_ // 2 - 1] + vals[m_ // 2]) / 2)

        # 籌碼（只在需要時算，很貴）
        di = chip_idx.get(day)
        krows = {}
        if di is not None:
            for code, mkt, base, d, fwd, i, st in rows:
                if base["valE"] is None or base["valE"] < 1:
                    continue
                k = chip_row(code, di, chip_ds, chip_cache, price_hist, mkt, None)
                if k:
                    krows[code] = k

        has240 = any(r[3].get("biasY") is not None for r in rows)
        for pname, (label, side, spec) in PRESETS.items():
            if pname in NEED_CHIP and di is None:
                continue
            if pname in NEED_MA240 and not has240:
                continue           # 這天全市場都還算不出乖離年線，不能算「那天沒選到」
            sel = [r for r in rows
                   if not (pname in NEED_MA240 and r[3].get("biasY") is None)
                   and passes(spec, r[2], r[3], krows.get(r[0]))]
            if spec.get("_random"):
                rng = random.Random("%s|%d" % (day, spec.get("_seed", 0)))
                sel = rng.sample(sel, min(spec["_random"], len(sel)))
            n = len(sel)
            for code, mkt, base, d, fwd, i, st in sel:
                for h in KEYS:
                    if fwd[h] is not None:
                        picks[pname][h].append((fwd[h], bm[h], bmed[h], day))
                for h in HORIZONS:
                    j = i + h
                    if j < len(st["pos"]):
                        s1 = state_of(st["pos"][j])
                        if s1:
                            states[pname][h][s1] += 1
            counts[pname].append(n)
            usable[pname] += 1

        for pname, (label, side, spec, ex) in (exits or {}).items():
            if pname in EXIT_NEED_CHIP and di is None:
                continue
            if pname in EXIT_NEED_MA240 and not has240:
                continue
            sel = [r for r in rows
                   if not (pname in EXIT_NEED_MA240 and r[3].get("biasY") is None)
                   and passes(spec, r[2], r[3], krows.get(r[0]))]
            if spec.get("_random"):
                rng = random.Random("%s|%d" % (day, spec.get("_seed", 0)))
                sel = rng.sample(sel, min(spec["_random"], len(sel)))
            xcounts[pname].append(len(sel))
            xusable[pname] += 1
            for code, mkt, base, d, fwd, i, st in sel:
                t_ = exit_scan(st, i, side, ex)
                if t_ is None:
                    continue
                n_ = t_["n"]
                xtrades[pname].append((t_["retC"], bmN["C"].get(n_), bmedN["C"].get(n_),
                                       t_["retO"], bmN["O"].get(n_), bmedN["O"].get(n_),
                                       n_, t_["reason"], day,
                                       t_["fullC"], t_["fullO"]))

        if verbose and (dn_i + 1) % 25 == 0:
            print("  %d/%d 天 …" % (dn_i + 1, len(eval_days)))

    return dict(picks=picks, counts=counts, usable=usable, bench=bench,
                states=states, eval_days=eval_days, n_stocks=len(stocks),
                xtrades=xtrades, xcounts=xcounts, xusable=xusable)


def summarize(res):
    out = []
    for pname, (label, side, spec) in PRESETS.items():
        cnt = res["counts"][pname]
        row = {"key": pname, "name": label, "side": side,
               "days": res["usable"][pname],
               "avg_picks": (sum(cnt) / len(cnt)) if cnt else 0,
               "total_picks": sum(cnt), "h": {}}
        for h in KEYS:
            recs = res["picks"][pname][h]
            if not recs:
                row["h"][h] = None
                continue
            rets = [r for r, b, bmd, d in recs]
            exc = [r - b for r, b, bmd, d in recs if b is not None]
            # 贏過大盤的比率要跟「中位數」比，不是跟平均比 ——
            # 平均被少數飆股拉高，隨便挑一檔本來就有一半機率輸給平均，
            # 跟中位數比才是「隨機挑的基準線 = 50%」。
            beatm = [1 if r > bmd else 0 for r, b, bmd, d in recs if bmd is not None]
            n = len(rets)
            mean_r = sum(rets) / n
            mean_e = (sum(exc) / len(exc)) if exc else None
            win = sum(1 for r in rets if r > 0) / n * 100
            beat = (sum(beatm) / len(beatm) * 100) if beatm else None
            srt = sorted(rets)
            med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2

            # 同一天選出來的股票會一起漲一起跌，直接把每一筆當獨立樣本會高估顯著性。
            # 所以先把每天的超額報酬平均成一個數字，再看這些「每日平均」穩不穩。
            by_day = defaultdict(list)
            for r, b, bmd, dd in recs:
                if b is not None:
                    by_day[dd].append(r - b)
            daily = [sum(v) / len(v) for _, v in sorted(by_day.items())]
            t, sd = None, None
            if len(daily) >= 20:      # 樣本天數太少的話 t 值只是巧合，不給
                m = sum(daily) / len(daily)
                nd = len(daily)
                dev = [x - m for x in daily]
                # Newey-West：持有 h 天的報酬會重疊 h−1 天，相鄰樣本自己就相關，
                # 不修正的話 t 值會被灌水好幾倍。
                hl = nw_lag(h)
                g0 = sum(e * e for e in dev) / nd
                var = g0
                for lag in range(1, min(hl, nd - 1)):
                    g = sum(dev[j] * dev[j - lag] for j in range(lag, nd)) / nd
                    var += 2 * (1 - lag / hl) * g
                if var > 0:
                    sd = var ** 0.5
                    t = m / (sd / nd ** 0.5)
            pos_days = (sum(1 for x in daily if x > 0) / len(daily) * 100) if daily else None
            # 逐月平均，用來看它是一路都這樣，還是靠某一兩個月撐起來的
            by_month = defaultdict(list)
            for r, b, bmd, dd in recs:
                if b is not None:
                    by_month[dd[:6]].append(r - b)
            monthly = {m: sum(v) / len(v) for m, v in sorted(by_month.items())}

            row["h"][h] = dict(n=n, mean=mean_r, median=med, win=win,
                               excess=mean_e, beat=beat,
                               t=t, sd_daily=sd, n_days=len(daily),
                               pos_days=pos_days, monthly=monthly,
                               states=dict(res["states"][pname].get(h, {}))
                               if h in HORIZONS else None)
        out.append(row)
    return out


COST_DAY = 0.235     # 當沖來回成本
COST_OVERNIGHT = 0.386   # 隔夜來回成本


def nw_t(daily, hl):
    """每日平均序列的 t 值，Newey-West 修正 hl 期重疊。天數不足 20 不給。"""
    nd = len(daily)
    if nd < 20:
        return None
    m = sum(daily) / nd
    dev = [x - m for x in daily]
    var = sum(e * e for e in dev) / nd
    for lag in range(1, min(hl, nd - 1)):
        g = sum(dev[j] * dev[j - lag] for j in range(lag, nd)) / nd
        var += 2 * (1 - lag / hl) * g
    if var <= 0:
        return None
    return m / ((var ** 0.5) / nd ** 0.5)


def summarize_exits(res, exits):
    """條件出場的統計。跟固定持有期同一套規矩：
    超額比同一天同樣抱 n 天的全市場平均、贏過比中位數、t 用每日平均＋Newey-West。
    """
    out = []
    for pname, (label, side, spec, ex) in exits.items():
        recs = res["xtrades"][pname]
        cnt = res["xcounts"][pname]
        row = {"key": pname, "name": label, "side": side, "exit": exit_label(ex),
               "days": res["xusable"][pname],
               "avg_picks": (sum(cnt) / len(cnt)) if cnt else 0,
               "trades": len(recs)}
        if not recs:
            row["hold"] = None
            row["conv"] = {}
            out.append(row)
            continue
        sign = -1 if side == "空" else 1
        holds = [r[6] for r in recs]
        row["hold"] = sum(holds) / len(holds)
        row["hold_med"] = sorted(holds)[len(holds) // 2]
        rc = defaultdict(int)
        for r in recs:
            rc[r[7]] += 1
        row["reasons"] = {k: v for k, v in sorted(rc.items(), key=lambda x: -x[1])}
        row["conv"] = {}
        for tag, ri, bi, mi, fi in (("O", 3, 4, 5, 10), ("C", 0, 1, 2, 9)):
            rets = [r[ri] for r in recs]
            exc = [r[ri] - r[bi] for r in recs if r[bi] is not None]
            beatm = [1 if r[ri] > r[mi] else 0 for r in recs if r[mi] is not None]
            n = len(rets)
            mean_r = sum(rets) / n
            # 成本：當天收盤出場而且只抱一天＝當沖，其他都是隔夜
            costs = [COST_DAY if (tag == "C" and r[6] == 1) else COST_OVERNIGHT
                     for r in recs]
            cost = sum(costs) / len(costs)
            hl = max(1, int(row["hold"] + 0.999))   # 重疊期＝平均持有天數
            by_day = defaultdict(list)
            for r in recs:
                if r[bi] is not None:
                    by_day[r[8]].append(r[ri] - r[bi])
            daily = [sum(v) / len(v) for _, v in sorted(by_day.items())]
            t = nw_t(daily, hl)

            # 配對比較：同一筆單，條件出場 vs 抱滿 maxd 天。
            # 這才是「出場規則有沒有加分」的答案 —— 跟大盤比會被持有天數的
            # 選擇效應污染（提早跑的本來就是走壞的那些）。
            by_day_d = defaultdict(list)
            dl = []
            for r in recs:
                if r[fi] is not None:
                    dl.append(sign * (r[ri] - r[fi]))
                    by_day_d[r[8]].append(sign * (r[ri] - r[fi]))
            vs_hold = (sum(dl) / len(dl)) if dl else None
            dailyd = [sum(v) / len(v) for _, v in sorted(by_day_d.items())]
            t_vs_hold = nw_t(dailyd, hl)

            srt = sorted(rets)
            med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
            row["conv"][tag] = dict(
                n=n, mean=mean_r, median=med,
                win=sum(1 for r in rets if sign * r > 0) / n * 100,
                excess=(sum(exc) / len(exc)) if exc else None,
                beat=(sum(beatm) / len(beatm) * 100) if beatm else None,
                t=t, n_days=len(daily), cost=cost,
                net=sign * mean_r - cost,
                vs_hold=vs_hold, t_vs_hold=t_vs_hold)
        out.append(row)
    return out


def exit_label(ex):
    bits = []
    if ex.get("leaveup") is not None:
        bits.append("離開上軌黑K")
    if ex.get("leavelo") is not None:
        bits.append("離開下軌紅K")
    if ex.get("belowma"):
        bits.append("跌破月線")
    if ex.get("abovema"):
        bits.append("站上月線")
    if ex.get("belowpos") is not None:
        bits.append("位階 < %g" % ex["belowpos"])
    if ex.get("stop") is not None:
        bits.append("停損 %g%%" % ex["stop"])
    if ex.get("take") is not None:
        bits.append("停利 %g%%" % ex["take"])
    bits.append("最多 %d 天" % min(ex.get("maxd", MAXD_CAP), MAXD_CAP))
    return "、".join(bits)


def fmt_exit_table(rows):
    lines = []
    lines.append("進場＝訊號隔天開盤。之後每天收盤檢查出場條件，觸發就出場。")
    lines.append("「隔天開」＝條件觸發的隔天開盤賣（盤後才看得到，這是做得到的）。")
    lines.append("「當天收」＝觸發當天收盤就賣（做不到，當上限參考）。")
    lines.append("超額＝同一天、同樣抱 n 天的全市場平均；贏過＝比同條件的中位數。")
    lines.append("淨＝原始 − 來回成本。空方的數字都是「做空」的角度。")
    lines.append("")
    lines.append("⚠ 超額不能單獨看。出場條件本身就是一個壞日子（黑K），")
    lines.append("  所以每一列的超額天生就偏低。要看的是它跟「同一種出場規則的對照組」差多少 ——")
    lines.append("  對照組就是最底下那幾列，隨機挑 30 檔套一模一樣的出場規則。")
    lines.append("")
    head = ("%-32s %-3s %5s %6s %5s │ %-42s │ %-22s"
            % ("情境（出場方式）", "方向", "天數", "筆數", "平均抱",
               "隔天開：原始／超額／贏過／t／淨", "當天收：原始／超額／t"))
    lines.append(head)
    lines.append("─" * 135)
    for r in rows:
        if not r["conv"]:
            lines.append("%-32s %-3s     —" % (r["name"], r["side"]))
            continue
        sign = -1 if r["side"] == "空" else 1
        o, c = r["conv"]["O"], r["conv"]["C"]

        def cell(x, full):
            if x["excess"] is None:
                return "—"
            s = "%+6.2f%% %+6.2f%%" % (sign * x["mean"], sign * x["excess"])
            if full:
                s += " %5.1f%%" % (x["beat"] if sign > 0 else 100 - x["beat"])
            s += " t%s" % (("%+5.1f" % (sign * x["t"])) if x["t"] is not None else "  —  ")
            if full:
                s += " 淨%+6.2f%%" % x["net"]
            return s
        lines.append("%-32s %-3s %5d %6d %5.1f │ %-42s │ %-22s"
                     % (r["name"], r["side"], r["days"], r["trades"], r["hold"],
                        cell(o, True), cell(c, False)))
    lines.append("")
    lines.append("出場規則到底有沒有加分？同一筆單，條件出場 vs 抱滿上限天數（隔天開盤價）")
    lines.append("  （提早出場＝暴露在市場裡的時間變短，多頭期間光這件事就會讓這欄變負，")
    lines.append("    所以一樣要跟對照組那幾列比，不是看它自己是正是負。）")
    for r in rows:
        if not r["conv"]:
            continue
        x = r["conv"]["O"]
        if x["vs_hold"] is None:
            continue
        lines.append("  %-32s %+6.2f%%  t%s   （抱滿＝%d 天）"
                     % (r["name"], x["vs_hold"],
                        ("%+5.1f" % x["t_vs_hold"]) if x["t_vs_hold"] is not None else "  —  ",
                        min(EXIT_PRESETS[r["key"]][3].get("maxd", MAXD_CAP), MAXD_CAP)
                        if r["key"] in EXIT_PRESETS else 0))
    lines.append("")
    lines.append("出場原因分布")
    for r in rows:
        if r.get("reasons"):
            tot = sum(r["reasons"].values())
            bits = "　".join("%s %.0f%%" % (k, v / tot * 100)
                             for k, v in r["reasons"].items())
            lines.append("  %-30s %s" % (r["name"], bits))
    return "\n".join(lines)


def fmt_table(rows, res):
    lines = []
    bench_avg = {h: (sum(res["bench"][h]) / len(res["bench"][h]) if res["bench"][h] else None)
                 for h in HORIZONS}
    lines.append("基準（每天成交金額 ≥ 1 億的全市場平均報酬）：" +
                 "　".join("%d 天 %+.2f%%" % (h, bench_avg[h]) for h in HORIZONS
                           if bench_avg[h] is not None))
    lines.append("")
    lines.append("進場價＝訊號隔天的開盤（盤後選股最快只能這樣買），出場＝第 N 天收盤。")
    lines.append("每格＝超額報酬／贏過當天中位數的比率（隨機挑是 50%）／t 值（已修正重疊）。")
    lines.append("t 值絕對值 < 2 就跟雜訊分不出來，別當真。空方的數字都是「做空」的角度。")
    lines.append("")
    head = "%-18s %-3s %5s %7s" % ("情境", "方向", "天數", "平均檔數")
    for h in HORIZONS:
        head += " │ %-24s" % ("%d 天" % h)
    lines.append(head)
    lines.append("─" * len(head))
    for r in rows:
        line = "%-18s %-3s %5d %8.1f" % (r["name"], r["side"], r["days"], r["avg_picks"])
        for h in HORIZONS:
            x = r["h"][h]
            if not x:
                line += " │ %-24s" % "—"
            else:
                sign = -1 if r["side"] == "空" else 1
                tv = x["t"]
                line += " │ %+6.2f%% %5.1f%% t%s" % (
                    sign * x["excess"],
                    x["beat"] if sign > 0 else 100 - x["beat"],
                    ("%+5.1f" % (sign * tv)) if tv is not None else "  —  ")
        lines.append(line)
    return "\n".join(lines)


def run_rules():
    """跑拆解測試。把 RULES 攤平成一組 PRESETS 重用同一套流程。"""
    global PRESETS, NEED_CHIP, NEED_MA240
    saved = (PRESETS, NEED_CHIP, NEED_MA240)
    flat, groups = {}, []
    for gi, (gname, why, items) in enumerate(RULES):
        keys = []
        for ii, (label, side, spec) in enumerate(items):
            key = "g%d_%d" % (gi, ii)
            flat[key] = (label, side, spec)
            keys.append(key)
        groups.append({"name": gname, "why": why, "keys": keys})
    flat["rndR"] = ("隨機對照 30 檔", "多", dict(val=1, _random=30, _seed=99))
    groups.append({"name": "對照組", "why": "隨機挑 30 檔。這一列越接近 0，"
                                        "上面各組的差異才越可信。", "keys": ["rndR"]})
    PRESETS, NEED_CHIP, NEED_MA240 = flat, set(), set()
    try:
        res = run(verbose=True)
        rows = summarize(res)
    finally:
        PRESETS, NEED_CHIP, NEED_MA240 = saved
    return rows, groups, res


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    limit = None
    for a in args:
        if a.isdigit():
            limit = int(a)
    here = os.path.dirname(os.path.abspath(__file__))

    want_exits = "--exits" in args
    res = run(limit_days=limit, exits=EXIT_PRESETS if want_exits else None)
    rows = summarize(res)
    print()
    print(fmt_table(rows, res))
    out = {"rows": rows,
           "bench": {str(h): res["bench"][h] for h in KEYS},
           "days": len(res["eval_days"]),
           "from": res["eval_days"][0], "to": res["eval_days"][-1]}

    if want_exits:
        xrows = summarize_exits(res, EXIT_PRESETS)
        print("\n\n=== 條件出場：照講義的方式賣 ===")
        print()
        print(fmt_exit_table(xrows))
        out["exit_rows"] = xrows

    if "--rules" in args:
        print("\n\n=== 拆解測試：一次只動一個條件 ===")
        rrows, groups, rres = run_rules()
        print()
        print(fmt_table(rrows, rres))
        out["rule_rows"] = rrows
        out["rule_groups"] = groups

    with open(os.path.join(here, "backtest.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
