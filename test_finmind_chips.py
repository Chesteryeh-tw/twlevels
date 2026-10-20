#!/usr/bin/env python3
"""finmind_chips.py 的離線測試（沙箱連不到 FinMind）。

做法：拿 repo 裡官方的 data/chip/ 日檔，反推出「FinMind 會給的格式」
（三大法人用股、融資融券用張），餵給程式，檢查：
  1. 對帳能認出正確單位；單位故意弄錯時會擋下來
  2. 正式抓會照時間上限停下、重跑會接著抓、不重抓
  3. 併入只補官方沒有的日子，數字跟官方一致；官方有的日子一個位元都不動
用法：python test_finmind_chips.py <repo 的 data 資料夾>
"""
import os, sys, shutil, tempfile, glob, json

SRC = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "data")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(SRC))       # twse.py 在 repo 根目錄

tmp = tempfile.mkdtemp()
os.chdir(tmp)
os.makedirs("data/chip")
days = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(SRC, "chip", "*.txt")))
assert len(days) >= 80, "官方日檔太少"
ALL = days[-80:]
OFFICIAL, HIDDEN = ALL[20:], ALL[:20]          # 前 20 天假裝官方沒有，要靠 FinMind 補
for d in OFFICIAL:
    shutil.copy(os.path.join(SRC, "chip", d + ".txt"), "data/chip/")
shutil.copy(os.path.join(SRC, "market.txt"), "data/")

import twse as T
import finmind_chips as F

def read(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        head = fh.readline().strip().split(",")
        for line in fh:
            v = line.rstrip("\n").split(",")
            if len(v) == len(head):
                out[v[0]] = {k: T.pn(x) for k, x in zip(head[1:], v[1:])}
    return out

TRUTH = {d: read(os.path.join(SRC, "chip", d + ".txt")) for d in ALL}
CODES = ["2330", "2317", "6488", "8069", "2454", "3008"]
with open("data/market.txt", "w", encoding="utf-8") as fh:      # 縮小市場，測試跑得快
    fh.write("header\n" + "".join("%s,x,1\n" % c for c in CODES))

calls = []
def fake_fetch(dataset, code, start, end, token, insti_unit=1000, margin_unit=1):
    calls.append((dataset, code))
    s, e = start.replace("-", ""), end.replace("-", "")
    rows = []
    for d in ALL:
        if not (s <= d <= e):
            continue
        r = TRUTH[d].get(code)
        if not r:
            continue
        ds = "%s-%s-%s" % (d[:4], d[4:6], d[6:])
        if dataset.startswith("TaiwanStockInstitutional"):
            for k, name in (("fgn", "Foreign_Investor"), ("inv", "Investment_Trust"),
                            ("dlrSelf", "Dealer_self"), ("dlrHedge", "Dealer_Hedging")):
                if r.get(k) is None:
                    continue
                net = r[k] * insti_unit
                rows.append({"date": ds, "stock_id": code, "name": name,
                             "buy": max(net, 0) + 500, "sell": max(-net, 0) + 500})
            rows.append({"date": ds, "stock_id": code, "name": "Foreign_Dealer_Self", "buy": 9e9, "sell": 0})
        else:
            if r.get("mgn") is None:
                continue
            rows.append({"date": ds, "stock_id": code,
                         "MarginPurchaseTodayBalance": r["mgn"] * margin_unit,
                         "MarginPurchaseYesterdayBalance": (r.get("mgnPrev") or 0) * margin_unit,
                         "ShortSaleTodayBalance": (r.get("shrt") or 0) * margin_unit,
                         "ShortSaleYesterdayBalance": (r.get("shrtPrev") or 0) * margin_unit})
    return rows

F.PAUSE = 0
os.environ["FINMIND_TOKEN"] = "dummy-for-test"
F.time.sleep = lambda s: None

# 1. 對帳：正確單位
F.fetch = fake_fetch
u = F.calibrate("x", verbose=False)
assert u == {"insti": 0.001, "margin": 1.0}, u
print("1a 對帳認出單位：三大法人 ×0.001（股→張）、融資券 ×1（本來就是張） ✓")
# 單位都弄成我們沒預期的（法人 ×7）→ 要擋下
F.fetch = lambda *a: fake_fetch(*a, insti_unit=7)
assert F.calibrate("x", verbose=False) is None
print("1b 單位不對時擋下來 ✓")

# 2. 正式抓：時間上限 → 中斷 → 重跑接著抓
F.fetch = fake_fetch
start = "%s-%s-%s" % (ALL[0][:4], ALL[0][4:6], ALL[0][6:])
calls.clear()
clock = [0.0]
F.time.time = lambda: clock[0]
real_fetch_retry = F.fetch_retry
def ticking(*a):
    clock[0] += 60                    # 每抓一次假裝過了 1 分鐘
    return real_fetch_retry(*a)
F.fetch_retry = ticking
F.run(start, max_min=8, only=None)     # 對帳 6 次 + 正式約 2 次就超時
first = len([c for c in calls if c[1] in CODES]) - 6
cov = json.load(open("data/chip_fm/_covered.json"))
n1 = sum(len(v) for v in cov.values())
assert 0 < n1 < 12, n1
clock[0] = 0; calls.clear()
F.run(start, max_min=999, only=None)
cov = json.load(open("data/chip_fm/_covered.json"))
assert sum(len(v) for v in cov.values()) == 12
refetch = len(calls) - 6
assert refetch == 12 - n1, (refetch, n1)
clock[0] = 0; calls.clear()
F.run(start, max_min=999, only=None)
assert len(calls) == 6                 # 只剩對帳，沒有重抓
print("2  中斷後重跑接著抓（第一趟 %d 個、第二趟補 %d 個、第三趟 0 個） ✓" % (n1, refetch))

# 3. 併入
before = {d: open("data/chip/%s.txt" % d, "rb").read() for d in OFFICIAL}
n = F.merge()
assert n == len(HIDDEN), (n, len(HIDDEN))
for d in OFFICIAL:
    assert open("data/chip/%s.txt" % d, "rb").read() == before[d], d
bad = tot = 0
for d in HIDDEN:
    got = read("data/chip/%s.txt" % d)
    for c in CODES:
        t = TRUTH[d].get(c)
        if not t:
            continue
        for k in ("fgn", "inv", "dlrSelf", "dlrHedge", "mgn", "mgnPrev", "shrt", "shrtPrev"):
            if t.get(k) is None:
                continue
            tot += 1
            g = got.get(c, {}).get(k)
            if g is None or abs(g - t[k]) > 1e-6:
                bad += 1
assert tot > 100 and bad == 0, (tot, bad)
print("3  併入 %d 天、%d 格全部等於官方；官方原有 %d 天一個位元都沒動 ✓" % (n, tot, len(OFFICIAL)))

# 4. backtest 讀得到
import chips as C
assert C.chip_days()[0] == HIDDEN[0] and len(C.chip_days()) == len(ALL)
print("4  chips.chip_days() 從 %s 開始、共 %d 天（回測用的就是這個） ✓" % (HIDDEN[0], len(ALL)))
shutil.rmtree(tmp)
print("全部通過")
