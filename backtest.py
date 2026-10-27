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
import csv
import bisect
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


# 公司行動調整層（2026-10-27，`--adjust`）：不改原始價，只在算報酬時乘上調整因子。
# 因子＝除權息（減資、面額變更）前收盤 ÷ 參考價，生效日起累乘；等於假設股利再投入。
# 預設關閉：關閉時 af() 一律回 1.0，所有數字跟以前逐位相同。
ADJUST = False
CA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "corporate_actions.csv")


def load_ca_factors():
    """code -> [(生效日, 因子)]。前收盤或參考價缺、或因子不合理（< 0.02 或 > 50）的不用。"""
    import csv as _csv
    out = defaultdict(list)
    if not os.path.exists(CA_PATH):
        return out
    with open(CA_PATH, encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            try:
                pc, rp = float(r["prev_close"]), float(r["ref_price"])
            except (TypeError, ValueError):
                continue
            if pc <= 0 or rp <= 0:
                continue
            f = pc / rp
            if 0.02 < f < 50 and abs(f - 1) > 1e-9:      # 面額變更、ETF 分割可到 10～24 倍（例：寶雅 5904 2026-08-10）
                out[r["code"]].append((r["effective_date"], f))
    return out


def adj_series(dates, evs):
    """累乘因子：cum[k]＝生效日 ≤ dates[k] 的所有因子相乘。"""
    cum, k, m = [], 0, 1.0
    evs = sorted(evs)
    for d in dates:
        while k < len(evs) and evs[k][0] <= d:
            m *= evs[k][1]
            k += 1
        cum.append(m)
    return cum


def af(st, a, b):
    """從第 a 天的價格基準換到第 b 天：c[b] × af(st, a, b) 跟 a 天的價格可以直接比。"""
    cum = st.get("adj")
    if not cum:
        return 1.0
    return cum[b] / cum[a]


def load_stocks():
    """code -> dict(dates, o, h, l, c, lot, wan)，皆由舊到新。"""
    out = {}
    ca = load_ca_factors() if ADJUST else {}
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
                             pos=pos, ma20=ma20, bbUp=bbup, bbLo=bblo,
                             adj=adj_series(dates, ca[code]) if ca.get(code) else None)
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


def load_revenue():
    """月營收（revenue_fetch.py 產生的 data/revenue/panel.csv）。沒有就回空的，回測照舊。
    回傳 code -> 依可用日排序的 [(cut, yoy, 創12個月新高)]：M 月營收在 M+1 月 11 日（含）以後的開盤才用得到。"""
    import revenue_panel
    p, _mp, _how = revenue_panel.resolve(os.path.join("data", "revenue"))   # 核過雜湊的 panel；不一致 → 停
    out = defaultdict(list)
    if not p:
        return out
    raw = defaultdict(dict)
    for r in csv.DictReader(open(p, encoding="utf-8")):
        try:
            raw[r["code"]][r["ym"]] = (float(r["rev"]), float(r["rev_ly"]) if r["rev_ly"] else None)
        except (ValueError, KeyError):
            continue
    for code, mm in raw.items():
        yms = sorted(mm)
        for ym in yms:
            a, b = mm[ym]
            if b is None or b <= 0:
                continue
            y, m = int(ym[:4]), int(ym[4:])
            # 創 12 個月新高：大於前 11 個月最大值（前 11 個月要齊）
            prev = []
            yy, mo = y, m
            for _ in range(11):
                yy, mo = (yy - 1, 12) if mo == 1 else (yy, mo - 1)
                prev.append(mm.get("%d%02d" % (yy, mo)))
            new_high = all(prev) and a > max(x[0] for x in prev)
            y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
            out[code].append(("%d%02d11" % (y2, m2), a / b - 1, new_high))
    for v in out.values():
        v.sort()
    return out


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
    # ── 2026-10-10：把「往上帶量開布林」拆成好、壞兩種（門檻在 2025-09~2026-10 約 280 天上找的）──
    # 這兩條是樣本外驗證：2021-02~2025-08 的 1,100 天完全沒被拿來挑門檻。
    # b1g：收在高點（收盤位置 > 0.71）、上影短（≤ 0.27）、量放大但沒爆（均量比 ≤ 5.26）、
    #      當天大漲（> 6.8%）、成交金額 > 7 億。外資條件需要籌碼歷史，這裡先不放。
    "b1g": ("開布林·好的那種", "多", dict(val=7.14, wchg=0.5, supt2=1, bpos1=0, chg1=6.8, vr=1.5,
                                    vr2=5.26, wmin=10, bwy=15, pos1=0.71, ush2=0.27)),
    # b1x：收在中低位置、上影長、爆量 > 5 倍 —— 盤中拉高被倒貨的樣子
    "b1x": ("開布林·爆量長上影", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=5.26,
                                     wmin=10, bwy=15, pos2=0.71, ush1=0.27)),
    # ── 2026-10-18：使用者定義「盤整後第一天開口打開，隔天追」。近一年約 230 天探索（study/b1_cond.py）：
    #    b1 裡約 27% 昨天其實就已經在開（不是首日），10 天超額 −2.88%（t −3.1）；首日 −1.15%（t −1.4）。
    #    量增昨日、投信、外資、主力分個股版／市場版都沒把它翻正。
    #    事先登記（樣本外 2021-02～2025-08）：b1f 的 10 天超額仍高於 b1n。b1fi、b1ff 要等籌碼歷史補到 2021。
    "b1f": ("開布林·首日", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                wmin=10, bwy=15, first=1)),
    "b1n": ("開布林·非首日（對照）", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                      wmin=10, bwy=15, first=0)),
    "b1fi": ("開布林·首日＋投信買", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                     wmin=10, bwy=15, first=1, inv=1)),
    "b1ff": ("開布林·首日＋外資買", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                     wmin=10, bwy=15, first=1, fgn=1)),
    # ── 2026-10-19 和碩 4938 案例（研究_開布林 第 7 節）：發動的關鍵是 9 月營收創新高，不是籌碼前兆。
    #    事先登記：b1fr（首日＋最近一期營收創 12 個月新高且年增 > 0）的 10 天超額高於 b1fq（首日、營收沒創新高）。
    "b1fr": ("開布林·首日＋營收創新高", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                        wmin=10, bwy=15, first=1, rnew=1)),
    "b1fq": ("開布林·首日·營收沒創新高（對照）", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                            wmin=10, bwy=15, first=1, rnew=0)),
    # ── 2026-10-27：研究_開布林 第 4 節「不要碰」②③ 補進回測。原始研究程式不在 10-25 備份裡，
    #    定義照研究_開布林_濾網測試 第 4 節的文字重建（判定時要註明是重建）：
    #    ② 前 10 天均量 ÷ 前 60 天均量（都不含今天）> 1.2，且今天均量比 > 5；
    #    ③ 到昨天為止連續「帶寬 ≤ 10%」≥ 20 天才打開。
    #    事先登記（研究_開布林 第 6 節）：樣本外 2021-02～2025-08 的 10 天超額仍 < 0。
    "b1v": ("開布林·前已放量＋爆量 5 倍（不要碰②）", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=5,
                                                   wmin=10, bwy=15, pv10=1.2)),
    "b1s": ("開布林·擠壓 ≥ 20 天才開（不要碰③）", "多", dict(val=1, wchg=0.5, supt2=1, bpos1=0, chg1=1, vr=1.5,
                                                  wmin=10, bwy=15, sqd=20)),
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
                        retC=(pc * af(st, a, j) / entry - 1) * 100,
                        retO=(nxt * af(st, a, j + 1) / entry - 1) * 100,
                        fullC=((fc * af(st, a, jf) / entry - 1) * 100) if (fc and fc > 0) else None,
                        fullO=((fo * af(st, a, jf + 1) / entry - 1) * 100) if (fo and fo > 0) else None)
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
    # ── 2026-10-19 月營收濾網（研究_免費資料因子 第 5 節事先登記）：最近一期月營收年增 > 0 vs ≤ 0 ──
    #    看兩組淨報酬的差；驗證期 2025-01～2026-08。沒有 data/revenue/panel.csv 時兩組都是 0 筆。
    "x2by": ("貼上軌強勢走→跌破月線賣·營收年增>0", "多",
             dict(val=1, bpos1=8, supt2=3, ryoy=1),
             dict(belowma=True, maxd=20)),
    "x2bn": ("貼上軌強勢走→跌破月線賣·營收年增≤0", "多",
             dict(val=1, bpos1=8, supt2=3, ryoy=0),
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
    "x5y": ("投信認養股→跌破月線賣·營收年增>0", "多",
            dict(val=1, invd=3, slope1=0, bpos1=0, ryoy=1),
            dict(belowma=True, maxd=20)),
    "x5n": ("投信認養股→跌破月線賣·營收年增≤0", "多",
            dict(val=1, invd=3, slope1=0, bpos1=0, ryoy=0),
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
EXIT_NEED_CHIP = {"x5", "x5y", "x5n"}
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
    if g("ush2") is not None and (base.get("ush") is None or base["ush"] > g("ush2")):
        return False
    if g("ush1") is not None and (base.get("ush") is None or base["ush"] < g("ush1")):
        return False
    if g("vr2") is not None and (d.get("vr20") is None or d["vr20"] > g("vr2")):
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
    # first：1＝今天是開口第一天（昨天帶寬還沒變寬 0.5 點以上、或上軌還沒往上 1%）；
    #        0＝昨天就已經在開（對照組）。昨天沒有指標（例如鎖漲停沒振幅）一律不選。
    if g("first") is not None:
        pw, ps = d.get("_pW"), d.get("_pS")
        if pw is None or ps is None:
            return False
        opened = pw >= 0.5 and ps >= 1
        if g("first") == 1 and opened:
            return False
        if g("first") == 0 and not opened:
            return False
    # pv10：前 10 天均量 ÷ 前 60 天均量下限（不要碰②）；sqd：到昨天為止連續帶寬 ≤ 10% 的天數下限（不要碰③）
    if g("pv10") is not None and (d.get("_pv10") is None or d["_pv10"] <= g("pv10")):
        return False
    if g("sqd") is not None and (d.get("_sq") is None or d["_sq"] < g("sqd")):
        return False
    # ryoy：1＝最近一期月營收年增 > 0；0＝≤ 0。沒有營收資料一律不選。
    if g("ryoy") is not None:
        y = d.get("_ryoy")
        if y is None:
            return False
        if g("ryoy") == 1 and not y > 0:
            return False
        if g("ryoy") == 0 and y > 0:
            return False
    # rnew：1＝最近一期月營收創 12 個月新高且年增 > 0；0＝不是。沒有營收資料一律不選。
    if g("rnew") is not None:
        y = d.get("_rnew")
        if y is None:
            return False
        if g("rnew") == 1 and not y:
            return False
        if g("rnew") == 0 and y:
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
    o = st["o"][i] if st.get("o") else None
    lot = st["lot"][i] or 0.0
    wan = st["wan"][i]
    return {
        # 振幅以「昨收」為分母（券商軟體的算法）；沒有昨收才退回今收
        "ampPct": (a / prev * 100) if (prev and prev > 0) else (a / c * 100 if c > 0 else 0),
        "pos": (c - l) / a,
        # 上影線佔當日振幅的比例：長上影 = 盤中拉高被賣回
        "ush": ((h - max(o, c)) / a) if o is not None else None,
        "chgPct": ((c / prev - 1) * 100) if (prev and prev > 0) else None,
        "lot": lot,
        "valE": (wan / 10000) if wan is not None else None,
        "turn": (lot / kshares * 100) if (kshares and kshares > 0) else None,
    }


def run(limit_days=None, verbose=True, exits=None, collect=None):
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
    # 市場的下一個交易日（T+1 用市場日曆，不用個股價量的下一列——個股停牌時下一列可能跨好幾天）
    next_day = {d: all_days[j + 1] for j, d in enumerate(all_days[:-1])}
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

    last_d = {}   # code -> (i, 指標)，前一天的指標
    sq_run = {}   # code -> (i, 到 i 為止連續帶寬 ≤ 10% 的天數；None＝不明)
    revenue = load_revenue()
    for dn_i, day in enumerate(eval_days):
        # 這一天所有股票的指標
        rows = []
        for code, st in stocks.items():
            i = pos_in[code].get(day)
            if i is None or i < WARMUP:
                continue
            if i + max_h >= len(st["c"]):
                continue
            if st["dates"][i + 1] != next_day.get(day):   # 隔天停牌：進場日對不上市場 T+1，不算
                continue
            mkt, name, ksh = info.get(code, ("1", code, None))
            base = base_metrics(st, i, ksh)
            if base is None:
                continue
            d = I.compute(st["c"][:i + 1], st["lot"][:i + 1])
            # 昨天的帶寬變化、上通斜率（給 first：判斷「今天是不是開口的第一天」）
            pv = last_d.get(code)
            if pv is not None and pv[0] == i - 1:
                d["_pW"], d["_pS"] = pv[1].get("bbWChg"), pv[1].get("slopeUp")
            last_d[code] = (i, d)
            # 不要碰③：連續擠壓天數（帶寬 ≤ 10%）；_sq 是「到昨天為止」，今天的存進 sq_run 給明天用
            ps_ = sq_run.get(code)
            d["_sq"] = ps_[1] if (ps_ is not None and ps_[0] == i - 1) else None
            bw_ = d.get("bbW")
            if bw_ is None:
                run_ = None
            elif bw_ > 10:
                run_ = 0
            else:
                run_ = None if d["_sq"] is None else d["_sq"] + 1   # 前面斷掉或不明：連續天數不明
            sq_run[code] = (i, run_)
            # 不要碰②：前 10 天均量 ÷ 前 60 天均量（都不含今天）
            if i >= 60:
                a10 = sum(st["lot"][i - 10:i]) / 10
                a60 = sum(st["lot"][i - 60:i]) / 60
                d["_pv10"] = a10 / a60 if a60 > 0 else None
            # 進場（隔天開盤）時已經看得到的最近一期月營收年增率
            rl = revenue.get(code)
            if rl:
                nd = next_day.get(day)
                k = bisect.bisect_right(rl, (nd, float("inf"), True)) if nd else 0
                d["_ryoy"] = rl[k - 1][1] if k > 0 else None
                d["_rnew"] = (rl[k - 1][2] and rl[k - 1][1] > 0) if k > 0 else None
            # 進場價用「隔天開盤」，不是訊號當天的收盤 ——
            # 這是盤後選股，名單出來的時候今天已經收了，最快也只能明天開盤才買得到。
            # 用當天收盤當進場價會憑空多賺一段開盤跳空，那是騙自己。
            entry = st["o"][i + 1] if (i + 1) < len(st["o"]) else None
            fwd = {}
            for h in HORIZONS:
                c1 = st["c"][i + h] * af(st, i + 1, i + h)
                fwd[h] = (c1 / entry - 1) * 100 if (entry and entry > 0) else None
            # 跳空：訊號日收盤 → 隔天開盤。這一段吃不到，但要看得到。
            c0 = st["c"][i]
            fwd[GAP] = (entry * af(st, i, i + 1) / c0 - 1) * 100 if (entry and c0 and c0 > 0) else None
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
                        vc.append((cj * af(st_, i_ + 1, j_) / e_ - 1) * 100)
                    oj = st_["o"][j_ + 1]
                    if oj and oj > 0:
                        vo.append((oj * af(st_, i_ + 1, j_ + 1) / e_ - 1) * 100)
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

        # 2026-10-27：給 bt_explore.py（回測工具）收集每檔每天的條件與報酬；只讀不改
        if collect is not None:
            collect(day, rows, bm, krows)

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


def summarize(res, series=None):
    """series：傳一個 dict 進來，就順便收集各情境的逐日序列（給主站「事件」頁畫累計圖）。
    2026-10-26 加：只多輸出，不改任何既有欄位的算法。"""
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
            row["h"][h].update(_dir_stats(rets, by_day, side))
            if series is not None and h in HORIZONS:
                series.setdefault(pname, {})[str(h)] = _cum_series(recs, side)
        out.append(row)
    return out


def _sign(side):
    return -1 if side == "空" else 1


def _dir_stats(rets, by_day, side):
    """照情境方向（空方取負號）的勝率、平均賺、平均賠、賺賠比，和累計超額的最大回落。
    win（原欄位）是「報酬 > 0」，空方情境要看 win_dir 才是「照方向做有賺」的比例。"""
    s = _sign(side)
    d = [s * r for r in rets]
    n = len(d)
    wins = [x for x in d if x > 0]
    loss = [x for x in d if x < 0]
    aw = sum(wins) / len(wins) if wins else None
    al = sum(loss) / len(loss) if loss else None
    cum = peak = mdd = 0.0
    for _, v in sorted(by_day.items()):
        cum += s * sum(v) / len(v)
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return dict(win_dir=len(wins) / n * 100 if n else None,
                avg_win=aw, avg_loss=al,
                payoff=(aw / -al) if (aw is not None and al) else None,
                mdd=mdd if by_day else None)


def _cum_series(recs, side):
    """逐日 {日期: 當天平均超額（照方向，%）}。超額＝個股報酬 − 同日大盤（b）。
    畫圖時逐日累加；持有期重疊，不是資金曲線。"""
    s = _sign(side)
    by_day = defaultdict(list)
    for r, b, bmd, dd in recs:
        if b is not None:
            by_day[dd].append(r - b)
    return {dd: round(s * sum(v) / len(v), 2) for dd, v in by_day.items()}


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


# ---------------------------------------------------------------- 疑似起漲／疑似起跌事件（--rally）
#
# 2026-10-20 加：研究_起漲與型態 4-1、4-2 的事件，搬進正式回測做樣本外驗證。
# 定義跟 study/rally_trade.py、study/rally_down.py 一模一樣（逐行對過，容器近一年數字對得上）：
#   疑似起漲日 i＝收盤第一次 ≥ 前 20 天最低收盤 × 1.10（昨天還 < ）；疑似起跌日＝收盤第一次 ≤ 前 20 天最高收盤 × 0.90。
#   前 20 天平均成交金額 ≥ 5,000 萬；個股至少 80 列歷史；隔天要是市場日曆的下一個交易日、有開盤價。
#   隔天開盤進；k 天報酬＝第 i+k 列收盤 ÷ 隔天開盤；超額＝減同日成交金額 ≥ 1 億股票的同期間平均。
#   t 用每日平均（Newey-West，重疊 k 期）；淨＝原始報酬（空方取負）− 隔夜成本 0.386%。
#   距年線＝疑似起漲前一天收盤 ÷ 前 240 天均線 − 1。正式版要求滿 240 天才算（不滿就不進用到距年線的分組）；
#   --rally-approx 改成研究程式的近似（不滿 240 天就用手上全部天數），只拿來在容器對數字。
#
# 事先登記（2026-10-19，樣本外 2021-02～2025-08，之前完全沒被拿來挑門檻）：
#   ① 疑似起漲日漲 ≥ 8.8% 且距年線 5.3～29%：40 天超額 > 0
#   ② 年線下方的疑似起漲（距年線 < −2.6%，研究裡的那一組）：40 天超額 < 0
#   ③ 全部疑似起跌，放空方向：40 天超額 > 0（只當「不要做多」濾網，不看淨）
# 其他列只是幫忙解讀，不算登記；看結果時要算多重檢定。

#
# 2026-10-20 再加（使用者同意，研究在 study/chips_sr.py，近一年探索）：
#   ④ 外資、投信同一天都買超（各 ≥ 當天成交量 1%）：20 天超額 > 0
#   ⑤ 非多頭（不是「站上月線且月線上升」）時，今天最高碰到三線壓力、收盤沒站上：5 天超額 < 0
#   ⑥ 非多頭時，今天收盤突破三線壓力：20 天超額 < 0
#   三線壓力＝昨收 + 0.75 ×（昨高 − 昨低）。④ 要籌碼歷史（finmind_chips 併入的 2021 起）。
#   ⑦ 長時間支撐守住：5 天超額 > 0；⑧ 長時間壓力突破：20 天超額 > 0（使用者的說法，study/sr_long.py，近一年看起來都沒效，照原方向登記）
#      轉折低（高）點＝最低（高）價是前後各 5 天裡最低（高）的，只用今天以前已確認的（要再等 5 天）；
#      支撐（壓力）區＝過去 120 個交易日內至少 2 個轉折點落在彼此 ±2% 內，價位＝平均；取離昨收最近、在下方（上方）2% 以外的那一區。
#      回測支撐：今天最低進到支撐 +2% 以內 → 守住＝收盤 ≥ 支撐 −2%；挑戰壓力：今天最高到壓力 −2% 以內 → 突破＝收盤 > 壓力 +2%。
#      同一檔同一區 10 天內只算第一次；個股要有 130 列以上。
# 2026-10-20（GPT 複核）：抱 k 天＝數市場日曆的第 k 個交易日，不是個股第 k 列；門檻比較前四捨五入到 6 位。

RALLY_H = (5, 20, 40)
RALLY_MID = "20260520"          # 研究程式前後半的切點
RALLY_OOS = ("20210201", "20250831")   # 登記的樣本外驗證期
RALLY_GROUPS = [
    # (key, 標籤, 方向, 是否登記, 事件來源, 條件)；來源 U＝疑似起漲、D＝疑似起跌、F＝每天的股票日（④⑤⑥ 用）
    ("u_all", "全部疑似起漲", "多", "", "U", lambda e: True),
    ("u_reg1", "① 疑似起漲日漲 ≥ 8.8% 且距年線 5.3～29%", "多", "①", "U",
     lambda e: e["yl"] is not None and e["chg"] >= 8.8 and 5.3 <= e["yl"] < 29),
    ("u_reg2", "② 年線下方的疑似起漲（距年線 < −2.6%）", "多", "②", "U",
     lambda e: e["yl"] is not None and e["yl"] < -2.6),
    ("u_yl0", "　參考：距年線 < 0（登記 ② 用的是 < −2.6%，這列不算登記）", "多", "", "U",
     lambda e: e["yl"] is not None and e["yl"] < 0),
    ("u_chg", "　參考：疑似起漲日漲 ≥ 8.8%", "多", "", "U", lambda e: e["chg"] >= 8.8),
    ("u_yl", "　參考：距年線 5.3～29%", "多", "", "U",
     lambda e: e["yl"] is not None and 5.3 <= e["yl"] < 29),
    ("d_all", "③ 全部疑似起跌（放空）", "空", "③", "D", lambda e: True),
    ("f_both", "④ 外資投信同買（各 ≥ 量 1%）", "多", "④", "F", lambda e: e["both"]),
    ("f_both_up", "　參考：外資投信同買·多頭", "多", "", "F", lambda e: e["both"] and e["up"]),
    ("f_rfail_dn", "⑤ 非多頭·碰壓力沒過（預期 5 天 < 0）", "多", "⑤", "F", lambda e: e["rfail"] and not e["up"]),
    ("f_rbrk_dn", "⑥ 非多頭·突破壓力（預期 20 天 < 0）", "多", "⑥", "F", lambda e: e["rbrk"] and not e["up"]),
    ("f_rfail_up", "　參考：多頭·碰壓力沒過", "多", "", "F", lambda e: e["rfail"] and e["up"]),
    ("f_rbrk_up", "　參考：多頭·突破壓力", "多", "", "F", lambda e: e["rbrk"] and e["up"]),
    ("s_hold", "⑦ 長時間支撐守住（預期 5 天 > 0）", "多", "⑦", "S", lambda e: e["kind"] == "支撐守住"),
    ("s_brk", "⑧ 長時間壓力突破（預期 20 天 > 0）", "多", "⑧", "S", lambda e: e["kind"] == "壓力突破"),
    ("s_fail", "　參考：長時間支撐跌破", "多", "", "S", lambda e: e["kind"] == "支撐跌破"),
    ("s_block", "　參考：長時間壓力被擋", "多", "", "S", lambda e: e["kind"] == "壓力被擋"),
    # 對照（體溫計）：每天所有符合 5,000 萬流動性的股票（每天一筆＝當天平均）。它的「超額」不是 0 ——
    # 2026-10-20 在容器發現：當天成交 ≥ 1 億的股票本身就比一般流動性股票強（近一年前半 40 天約 +1.9%），
    # 所以上面每一列的「超額」都混了這個基準偏差。要看「比同流動性」那一欄才乾淨。
    ("ctl", "對照：同流動性全部股票（基準偏差）", "多", "", "C", lambda e: True),
]


def _read_chip_flows():
    """data/chip/<日期>.txt → {日期: {代號: (外資, 投信)}}（張）。finmind 的 job 裡會先併入 2021 起的 FinMind 籌碼。"""
    out = {}
    d0 = os.path.join(T.DATA, "chip")
    if not os.path.isdir(d0):
        return out
    for fn in os.listdir(d0):
        if not fn.endswith(".txt"):
            continue
        day = {}
        with open(os.path.join(d0, fn), encoding="utf-8") as fh:
            head = fh.readline().strip().split(",")
            try:
                jf, ji = head.index("fgn"), head.index("inv")
            except ValueError:
                continue
            for line in fh:
                p = line.rstrip("\n").split(",")
                try:
                    day[p[0]] = (float(p[jf]), float(p[ji]))
                except (ValueError, IndexError):
                    continue
        out[fn[:8]] = day
    return out


def run_rally(approx=False, verbose=True):
    info = load_market_meta()
    series = {}
    cnt = defaultdict(int)
    for fn in sorted(os.listdir(T.STOCK_DIR)):
        if not fn.endswith(".txt"):
            continue
        code = fn[:-4]
        if code not in info:
            continue
        rows = []
        for d, line in sorted(T.read_stock(code).items()):
            p = line.split(",")
            if len(p) < 7:
                continue
            try:
                rows.append((d, float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                             float(p[5] or 0), float(p[6] or 0)))
            except ValueError:
                continue
        if len(rows) > 150:
            series[code] = rows
            for x in rows:
                cnt[x[0]] += 1
    days = sorted(d for d, n in cnt.items() if n > len(series) * 0.5)
    nxt = {d: days[j + 1] for j, d in enumerate(days[:-1])}
    cal = {d: j for j, d in enumerate(days)}
    flows = _read_chip_flows()

    # 基準只存「總和、筆數」，五年全市場用 list 會吃掉太多記憶體
    bench = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    liquid = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # 當天符合 5,000 萬流動性（第二把尺）
    ev = {"U": [], "D": [], "F": [], "S": []}

    def _zone(prices, ref, below):
        """至少 2 個轉折點在彼此 ±2% 內的區；回離 ref 最近、在下方（below）或上方 2% 以外的 (價位, 個數)。"""
        best = None
        ps = sorted(prices)
        for p0 in ps:
            grp = [q for q in ps if abs(q / p0 - 1) <= 0.02]
            if len(grp) < 2:
                continue
            lv = sum(grp) / len(grp)
            if (ref > lv * 1.02) if below else (ref < lv * 0.98):
                dist = abs(ref / lv - 1)
                if best is None or dist < best[2]:
                    best = (lv, len(grp), dist)
        return best
    for code, r in series.items():
        o = [x[1] for x in r]; hi = [x[2] for x in r]; lo_ = [x[3] for x in r]
        cl = [x[4] for x in r]; vo = [x[5] for x in r]; wan = [x[6] for x in r]
        n = len(r)
        ci = [cal.get(x[0], -1) for x in r]
        ma = [None] * n
        s = 0.0
        for j in range(n):
            s += cl[j]
            if j >= 20:
                s -= cl[j - 20]
            if j >= 19:
                ma[j] = s / 20
        # 長時間支撐壓力用的轉折點（⑦⑧）
        plo = [j for j in range(5, n - 5) if lo_[j] == min(lo_[j - 5:j + 6])]
        phi = [j for j in range(5, n - 5) if hi[j] == max(hi[j - 5:j + 6])]
        last_s, last_r, zcache = {}, {}, {}
        for i in range(80, n - 1):
            d = r[i][0]
            if r[i + 1][0] != nxt.get(d) or o[i + 1] <= 0 or ci[i] < 0:
                continue
            # 抱 k 天＝市場日曆第 k 個交易日；那天沒成交用之前最後一個收盤價，記下空了幾天
            fw, fx = {}, {}
            for k in RALLY_H:
                t = ci[i] + k
                if t >= len(days):
                    continue
                j = i + k if i + k < n else n - 1
                while j > i + 1 and (ci[j] < 0 or ci[j] > t):
                    j -= 1
                while j + 1 < n and 0 <= ci[j + 1] <= t:
                    j += 1
                if ci[j] < 0 or ci[j] > t:
                    continue
                fw[k] = (cl[j] / o[i + 1] - 1) * 100
                fx[k] = (r[j][0], days[t], t - ci[j])
            if wan[i] >= 10000:
                for k, x in fw.items():
                    b = bench[d][k]; b[0] += x; b[1] += 1
            if sum(wan[i - 20:i]) / 20 < 5000:
                continue
            for k, x in fw.items():
                b = liquid[d][k]; b[0] += x; b[1] += 1
            gap = (o[i + 1] / cl[i] - 1) * 100
            # ④⑤⑥：每個股票日都可能是事件
            up = ma[i] is not None and ma[i - 1] is not None and cl[i] > ma[i] and ma[i] > ma[i - 1]
            fl = flows.get(d, {}).get(code)
            both = bool(fl and vo[i] > 0 and fl[0] >= 0.01 * vo[i] and fl[1] >= 0.01 * vo[i])
            A = hi[i - 1] - lo_[i - 1]
            rfail = rbrk = False
            if A > 0:
                R = round(cl[i - 1] + 0.75 * A, 6)
                rfail = hi[i] >= R and cl[i] <= R
                rbrk = hi[i] >= R and cl[i] > R
            if i >= 130:
                a1, b1 = bisect.bisect_left(plo, i - 120), bisect.bisect_right(plo, i - 6)
                a2, b2 = bisect.bisect_left(phi, i - 120), bisect.bisect_right(phi, i - 6)
                kz = (a1, b1, a2, b2, i - 1)
                zs = _zone([lo_[j] for j in plo[a1:b1]], cl[i - 1], True)
                zr = _zone([hi[j] for j in phi[a2:b2]], cl[i - 1], False)
                for zz, isup in ((zs, True), (zr, False)):
                    if not zz:
                        continue
                    lv = zz[0]
                    key = round(lv, 2)
                    last = last_s if isup else last_r
                    if isup and lo_[i] <= lv * 1.02 and last.get(key, -99) < i - 10:
                        kind = "支撐守住" if cl[i] >= lv * 0.98 else "支撐跌破"
                    elif (not isup) and hi[i] >= lv * 0.98 and last.get(key, -99) < i - 10:
                        kind = "壓力突破" if cl[i] > lv * 1.02 else "壓力被擋"
                    else:
                        continue
                    last[key] = i
                    ev["S"].append(dict(c=code, d=d, fw=fw, fx=fx, entry=r[i + 1][0], yl=None, ma240_n=None,
                                        chg=round((cl[i] / cl[i - 1] - 1) * 100, 6), gap=gap, kind=kind,
                                        level=round(lv, 4), touches=zz[1]))
            if both or rfail or rbrk:
                ev["F"].append(dict(c=code, d=d, fw=fw, fx=fx, entry=r[i + 1][0], yl=None, ma240_n=None,
                                    chg=round((cl[i] / cl[i - 1] - 1) * 100, 6), gap=gap,
                                    both=both, up=up, rfail=rfail, rbrk=rbrk))
            lo = min(cl[i - 20:i])
            hh = max(cl[i - 20:i])
            up_ = cl[i] >= lo * 1.10 and cl[i - 1] < lo * 1.10
            dn_ = cl[i] <= hh * 0.90 and cl[i - 1] > hh * 0.90
            if not (up_ or dn_):
                continue
            if i >= 240:
                yl = (cl[i - 1] / (sum(cl[i - 240:i]) / 240) - 1) * 100
            elif approx:
                yl = (cl[i - 1] / (sum(cl[:i]) / i) - 1) * 100
            else:
                yl = None
            # 門檻比較前四捨五入到 6 位：50 → 45.6 剛好 −8.8%，浮點數會算成 −8.7999…（GPT 複核 6127／20260423）
            e = dict(c=code, d=d, fw=fw, fx=fx, entry=r[i + 1][0],
                     yl=None if yl is None else round(yl, 6),
                     ma240_n=240 if i >= 240 else i,
                     chg=round((cl[i] / cl[i - 1] - 1) * 100, 6), gap=gap)
            if up_:
                ev["U"].append(e)
            if dn_:
                ev["D"].append(e)

    mean = lambda b: b[0] / b[1] if b[1] else None
    ctl = [dict(c="", d=d, fw={k: mean(b) for k, b in kk.items() if b[1]}, yl=None, chg=0.0, gap=0.0)
           for d, kk in sorted(liquid.items())]
    ev["C"] = ctl

    periods = [("全部", None, None), ("登記驗證期 2021-02～2025-08", RALLY_OOS[0], RALLY_OOS[1]),
               ("近一年 2025-09 起", "20250901", None),
               ("研究前半（～2026-05-19）", "20250901", "20260519"),
               ("研究後半（2026-05-20 起）", RALLY_MID, None)]
    years = sorted({e["d"][:4] for e in ev["U"] + ev["D"] + ev["F"] + ev["S"]})
    periods += [("%s 年" % y, y + "0101", y + "1231") for y in years]

    def cell(lst, k, sign):
        by = defaultdict(list)
        by2 = defaultdict(list)
        raw = []
        gaps = []
        for e in lst:
            if k not in e["fw"]:
                continue
            b, q = mean(bench[e["d"]][k]), mean(liquid[e["d"]][k])
            if b is None or q is None:
                continue
            by[e["d"]].append(sign * (e["fw"][k] - b))
            by2[e["d"]].append(sign * (e["fw"][k] - q))
            raw.append(sign * e["fw"][k])
            gaps.append(e["gap"])
        if len(raw) < 20:
            return dict(n=len(raw), days=len(by))
        dm = [sum(v) / len(v) for _, v in sorted(by.items())]
        dm2 = [sum(v) / len(v) for _, v in sorted(by2.items())]
        return dict(n=len(raw), days=len(dm), excess=sum(dm) / len(dm), t=nw_t(dm, k),
                    vs_liq=sum(dm2) / len(dm2), t_liq=nw_t(dm2, k),
                    raw=sum(raw) / len(raw), net=sum(raw) / len(raw) - COST_OVERNIGHT,
                    win=sum(1 for x in raw if x > COST_OVERNIGHT) / len(raw) * 100,
                    gap=sum(gaps) / len(gaps))

    out = []
    for key, lab, side, reg, src, fn in RALLY_GROUPS:
        sign = -1 if side == "空" else 1
        g = [e for e in ev[src] if fn(e)]
        row = dict(key=key, name=lab, side=side, reg=reg, periods=[])
        for pl, a, b in periods:
            gg = [e for e in g if (a is None or e["d"] >= a) and (b is None or e["d"] <= b)]
            row["periods"].append(dict(name=pl, h={str(k): cell(gg, k, sign) for k in RALLY_H}))
        out.append(row)

    # 逐筆清單（給 GPT 複核；只放 Actions 下載區，不 commit —— 價格來自 FinMind，不轉散布原始資料）
    try:
        import csv as _csv
        with open("rally_events.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = _csv.writer(fh)
            grp = [g for g in RALLY_GROUPS if g[4] != "C"]
            w.writerow(["來源", "代號", "訊號日", "進場日", "當天漲跌%", "距年線%", "年線天數", "隔天跳空%"]
                       + sum([["%d天出場日" % k, "%d天目標日" % k, "%d天空了幾天" % k, "%d天報酬%%" % k,
                               "%d天基準1億%%" % k, "%d天同流動性%%" % k] for k in RALLY_H], [])
                       + [g[0] for g in grp])
            for src in ("U", "D", "F", "S"):
                for e in sorted(ev[src], key=lambda x: (x["d"], x["c"])):
                    row = [{"U": "疑似起漲", "D": "疑似起跌", "F": "股票日", "S": e.get("kind", "")}[src], e["c"], e["d"], e["entry"], "%.6f" % e["chg"],
                           "" if e["yl"] is None else "%.6f" % e["yl"],
                           "" if e["ma240_n"] is None else e["ma240_n"], "%.4f" % e["gap"]]
                    for k in RALLY_H:
                        if k in e["fw"]:
                            b1, q1 = mean(bench[e["d"]][k]), mean(liquid[e["d"]][k])
                            row += [e["fx"][k][0], e["fx"][k][1], e["fx"][k][2], "%.4f" % e["fw"][k],
                                    "" if b1 is None else "%.4f" % b1, "" if q1 is None else "%.4f" % q1]
                        else:
                            row += [""] * 6
                    row += [int(g[4] == src and bool(g[5](e))) for g in grp]
                    w.writerow(row)
    except OSError as ex:
        print("逐筆清單寫不出來：%s" % ex)
    stale = [e["fx"][40][2] for s_ in ("U", "D") for e in ev[s_] if 40 in e.get("fx", {})]
    meta = dict(n_up=len(ev["U"]), n_dn=len(ev["D"]), n_f=len(ev["F"]), n_s=len(ev["S"]), approx=approx,
                stale40=sum(1 for x in stale if x > 0), n40=len(stale),
                chip_days=len(flows),
                first=days[0] if days else None, last=days[-1] if days else None,
                stocks=len(series), days=len(days))
    if verbose:
        print("疑似起漲／疑似起跌：股票 %d 檔、交易日 %d 天（%s ~ %s）；疑似起漲 %d 筆、疑似起跌 %d 筆、④⑤⑥ 股票日 %d 筆、⑦⑧ 支撐壓力 %d 筆；籌碼 %d 天%s"
              % (len(series), len(days), meta["first"], meta["last"], meta["n_up"], meta["n_dn"], meta["n_f"], meta["n_s"],
                 meta["chip_days"], "（距年線用近似）" if approx else ""))
        print("疑似起漲／疑似起跌有 40 天結果 %d 筆，其中出場日當天沒成交、用之前收盤價的 %d 筆" % (meta["n40"], meta["stale40"]))
    return out, meta


def fmt_rally(rows):
    lines = ["超額＝照方向，減同日成交金額 ≥ 1 億股票平均（登記用的尺）；比同流動性＝減同日 20 日均成交 ≥ 5,000 萬"
             "全部股票平均（沒有基準偏差）；t＝每日平均 Newey-West；淨＝扣 0.386% 成本；"
             "跳空＝訊號日收盤 → 隔天開盤（吃不到）"]
    for r in rows:
        lines.append("")
        lines.append("■ %s（%s）%s" % (r["name"], r["side"], "　← 事先登記 " + r["reg"] if r["reg"] else ""))
        for p in r["periods"]:
            parts = []
            for k in RALLY_H:
                x = p["h"][str(k)]
                if "excess" not in x:
                    parts.append("%2d天 （%d 筆，太少）" % (k, x["n"]))
                    continue
                parts.append("%2d天 %5d筆 超額%+6.2f%% t%s 比同流動性%+6.2f%% t%s 淨%+6.2f%%" % (
                    k, x["n"], x["excess"],
                    "  —" if x["t"] is None else "%+4.1f" % x["t"], x["vs_liq"],
                    "  —" if x["t_liq"] is None else "%+4.1f" % x["t_liq"], x["net"]))
            x40 = p["h"]["40"]
            gap = (" 跳空%+.2f%%" % x40["gap"]) if "gap" in x40 else ""
            lines.append("   %-26s %s%s" % (p["name"], "｜".join(parts), gap))
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    limit = None
    for a in args:
        if a.isdigit():
            limit = int(a)
    here = os.path.dirname(os.path.abspath(__file__))
    # --adjust：公司行動調整版，結果另存 backtest_adj.json／data/bt_series_adj.json，不蓋掉原本的
    if "--adjust" in args:
        ADJUST = True
    sfx = "_adj" if ADJUST else ""

    want_exits = "--exits" in args
    res = run(limit_days=limit, exits=EXIT_PRESETS if want_exits else None)
    series = {}
    rows = summarize(res, series)
    # 主站「事件」頁的累計圖用；照方向、逐日平均，畫圖時累加（不是資金曲線）
    ev = list(res["eval_days"])
    compact = {k: {h: [m.get(d) for d in ev] for h, m in hs.items()} for k, hs in series.items()}
    with open(os.path.join(here, "data", "bt_series%s.json" % sfx), "w", encoding="utf-8") as fh:
        json.dump({"days": len(ev), "from": ev[0], "to": ev[-1], "dates": ev,
                   "note": "照方向的每日平均超額（%），null＝那天沒選到；逐日累加畫圖，不是資金曲線",
                   "series": compact}, fh, ensure_ascii=False, separators=(",", ":"))
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

    if "--rally" in args:
        print("\n\n=== 疑似起漲／疑似起跌事件（研究_起漲與型態 4-1、4-2 的事先登記）===")
        rl_rows, rl_meta = run_rally(approx="--rally-approx" in args)
        print()
        print(fmt_rally(rl_rows))
        out["rally_rows"] = rl_rows
        out["rally_meta"] = rl_meta

    out["adjusted"] = ADJUST
    with open(os.path.join(here, "backtest%s.json" % sfx), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
