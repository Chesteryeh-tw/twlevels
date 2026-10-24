#!/usr/bin/env python3
"""warrant_fetch.py、warrant_study.py 的離線驗收（不連網）。用法：python test_warrant.py"""
import io, json, os, shutil, sys, tempfile, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import warrant_fetch as wf
import warrant_study as ws

wf.time.sleep = lambda s: SLEEPS.append(s)
SLEEPS = []
FAILS = []


def check(name, cond, detail=""):
    print(("通過  " if cond else "失敗  ") + name + ("" if cond else "：" + str(detail)))
    if not cond:
        FAILS.append(name)


DAYS = ["20241001", "20241002", "20241004", "20241007"]
T86F = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
        "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
        "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數",
        "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
        "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]


def t86_row(code, name, dealer_net):
    return [code, name] + ["0"] * 9 + ["{:,}".format(dealer_net)] + ["0"] * 7


def respond(url):
    if "FMTQIK" in url:
        return 200, {"stat": "OK", "data": [["113/10/%s" % d[6:], "1", "1", "1", "1", "1"] for d in DAYS]}
    day = url.split("date=")[1][:8]
    if "T86" in url:
        if url.endswith("0999P"):
            return 200, {"stat": "OK", "fields": T86F, "data": [t86_row("03001P", "台積電元大5A售01", -2000)]}
        return 200, {"stat": "OK", "fields": T86F, "data": [
            t86_row("030001", "台積電元大5A購01", -5000000),     # 非自營商淨買 500 萬股
            t86_row("030002", "臻鼎KY凱基5A購01", -1000000),
            t86_row("030003", "不存在公司元大購01", -10),
            t86_row("030004", "舊名字元大購01", -10),
            t86_row("030005", "長榮太統一6C購01", -10),
            t86_row("030006", "T50正2富邦63購05", -10),
            t86_row("030007", "T50正2國泰63購02", -10)]}
    tb = {"fields": ["暫停交易", "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "標的代號"],
          "data": [["", "030001", "台積電元大5A購01", "6,000,000", "1", "6,000,000", "1", "1", "1", "1.00", "2330"],
                   ["", "030002", "臻鼎KY凱基5A購01", "1,000,000", "1", "2,000,000", "2", "2", "2", "2.00", ""],
                   ["", "030003", "不存在公司元大購01", "10", "1", "10", "1", "1", "1", "1", ""],
                   ["", "030004", "舊名字元大購01", "10", "1", "10", "1", "1", "1", "1", "1101"],
                   ["", "030005", "長榮太統一6C購01", "10", "1", "10", "1", "1", "1", "1", None],
                   ["", "030006", "T50正2富邦63購05", "10", "1", "10", "1", "1", "1", "1", "00631L"],
                   ["", "030007", "T50正2國泰63購02", "10", "1", "10", "1", "1", "1", "1", None],
                   ["", "03001P", "台積電元大5A售01", "2,000", "1", "1,000", "0.5", "0.5", "0.5", "0.5", "2330"]]}
    return 200, {"stat": "OK", "tables": [{"title": "x", "fields": ["指數"], "data": [["a"]]}, tb]}


def fake_urlopen(responses_fn, calls, override=None):
    def f(req, timeout=60):
        calls.append(req.full_url)
        if override:
            code, body = override(len(calls))
            if code:
                if code >= 400:
                    raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
                return R(code, body)
        code, js = responses_fn(req.full_url)
        return R(code, json.dumps(js, ensure_ascii=False).encode("utf-8"))
    return f


class R:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


d = tempfile.mkdtemp()
os.chdir(d)
os.makedirs("data/stock")
with open("data/market.txt", "w", encoding="utf-8") as f:
    f.write("TWLV1|20241001|202410012100|3|code,name,mkt,high\n2330,台積電,1,1\n4958,臻鼎-KY,1,1\n6488,環球晶,2,1\n2603,長榮,1,1\n2645,長榮航太,1,1\n00631L,元大台灣50正2,1,1\n")   # 跟真檔同格式
wf.START = "20241001"
real_now = wf.dt.datetime


class FakeDT(real_now):
    @classmethod
    def now(cls, tz=None):
        return real_now(2024, 10, 8, 12, 0, tzinfo=tz)


wf.dt.datetime = FakeDT

calls = []
wf.urllib.request.urlopen = fake_urlopen(respond, calls)
rc = wf.run_fetch()
man = wf.load_manifest()
check("W1 抓 4 天 × 4 份全部 ok", rc == 0 and sum(v["status"] == "ok" for v in man.values()) == 16, (rc, len(man)))
check("W1b 帳本存原始檔雜湊與檔案", all(os.path.exists(os.path.join(wf.RAW, v["file"])) for v in man.values()))
n = len(calls)
wf.run_fetch()
check("W2 再跑只重抓本月開市日、不重抓已有資料", len(calls) - n == 1, len(calls) - n)

nd = wf.derive()
import csv
rows = list(csv.DictReader(open(os.path.join(wf.RAW, "derived", "20241001.csv"), encoding="utf-8")))
r1 = [r for r in rows if r["warrant"] == "030001"][0]
r2 = [r for r in rows if r["warrant"] == "030002"][0]
r3 = [r for r in rows if r["warrant"] == "030003"][0]
check("W3 非自營商淨股數＝−自營商淨；估計金額＝淨股 × 均價", r1["nondealer_net_sh"] == "5000000" and float(r1["est_net_amt_twd"]) == 5000000, r1)
check("W4 名稱對標的：台積電→2330、臻鼎KY→4958（去掉 -KY）", r1["underlying"] == "2330" and r2["underlying"] == "4958", (r1["underlying"], r2["underlying"]))
check("W5 對不到的留空，不硬猜", r3["underlying"] == "", r3)
r4 = [r for r in rows if r["warrant"] == "030004"][0]
check("W5b 官方標的代號優先（名稱對不到也用官方）", r4["underlying"] == "1101", r4)
r5 = [r for r in rows if r["warrant"] == "030005"][0]
check("W5c 官方欄是 null → 用簡稱；長榮太 對到 長榮航太 2645（不是長榮）", r5["underlying"] == "2645" and r5["map_method"] == "rule", r5)
r7 = [r for r in rows if r["warrant"] == "030007"][0]
check("W5d 從有官方代號的權證學簡稱：T50正2 → 00631L", r7["underlying"] == "00631L" and r7["map_method"] == "learned", r7)
check("W5e 拆簡稱：發行商前面那段", wf.split_name("國票金元大5A購01") == "國票金" and wf.split_name("統一統一5A購01") == "統一" and wf.split_name("台積電華南永昌5A售03") == "台積電", "")
check("W6 完整日 4 天", nd == 4, nd)

# 被擋：第 1 次請求就回 403 → 停、帳本不壞
shutil.rmtree(wf.RAW)
calls = []
wf.urllib.request.urlopen = fake_urlopen(respond, calls, override=lambda k: (403, b"no") if k == 2 else (None, None))
rc = wf.run_fetch()
check("W7 403 → 整批停、回傳 2、只打 2 次", rc == 2 and len(calls) == 2, (rc, len(calls)))
check("W7b 被擋後間隔上限 +1 秒（4～8）", wf.load_pace() == (4.0, 8.0), wf.load_pace())
check("W7c 間隔在範圍內隨機", SLEEPS and all(4 <= x <= 8 for x in SLEEPS[-3:]) and len(set(SLEEPS)) > 1, SLEEPS[-5:])
wf.save_pace(4.0, 12.0, "blocked")
calls = []
wf.urllib.request.urlopen = fake_urlopen(respond, calls, override=lambda k: (403, b"no") if k == 2 else (None, None))
rc = wf.run_fetch()
check("W7d 已到 12 秒上限又被擋 → 回傳 3、記下不再自動續抓", rc == 3 and wf.load_pace() == (4.0, 12.0), (rc, wf.load_pace()))
calls = []
wf.urllib.request.urlopen = fake_urlopen(respond, calls)
rc = wf.run_fetch()
check("W7e 之後的排程不再連線", rc == 3 and len(calls) == 0, (rc, len(calls)))
os.remove(wf.PACE_FILE)
# 網頁回應 → 停
shutil.rmtree(wf.RAW)
calls = []
wf.urllib.request.urlopen = fake_urlopen(respond, calls, override=lambda k: (200, b"<html>captcha") if k == 3 else (None, None))
rc = wf.run_fetch()
man = wf.load_manifest()
check("W8 回應是網頁 → 停，已抓的 1 份留在帳本", rc == 2 and len(calls) == 3 and len(man) == 1, (rc, len(calls), len(man)))
# 5xx 重試
shutil.rmtree(wf.RAW)
calls = []
wf.urllib.request.urlopen = fake_urlopen(respond, calls, override=lambda k: (520, b"error 520") if k == 2 else (None, None))
rc = wf.run_fetch()
man = wf.load_manifest()
check("W9 520 重試後成功", rc == 0 and sum(v["status"] == "ok" for v in man.values()) == 16, (rc, len(man)))
# 同名檔案內容不同 → 停
v = man["t86c/20241001"]
p = os.path.join(wf.RAW, v["file"])
open(p, "wb").write(wf.gzip.compress(b"tampered"))
try:
    wf.read_raw(man, "t86c", "20241001")
    check("W10 原始檔被改 → 停", False)
except wf.Stop:
    check("W10 原始檔被改 → 讀取時停", True)

# ---- H5 回測：台積電被選、超額＝報酬 − 同日流動股平均
shutil.rmtree(wf.RAW)
wf.urllib.request.urlopen = fake_urlopen(respond, [])
wf.run_fetch()
wf.derive()
cal = DAYS + ["20241008", "20241009", "20241011", "20241014", "20241015", "20241016", "20241017", "20241018",
              "20241021", "20241022", "20241023", "20241024", "20241025", "20241028", "20241029", "20241030",
              "20241101", "20241104", "20241105", "20241106", "20241107", "20241108", "20241111", "20241112"]
def write_stock(code, f_open, f_close, wan):
    with open("data/stock/%s.txt" % code, "w") as f:
        f.write("# date,open,high,low,close,lot,wan\n")
        for i, dd in enumerate(cal):
            f.write("%s,%s,0,0,%s,1,%s\n" % (dd, f_open(i), f_close(i), wan))
write_stock("2330", lambda i: 100 + i, lambda i: 100 + i, 50000)       # 每天 +1
write_stock("4958", lambda i: 100, lambda i: 100, 50000)               # 不動
write_stock("1101", lambda i: 100, lambda i: 100, 5000)                # 成交金額不到 1 億，不進基準
rc = ws.main()
ev = list(csv.DictReader(open("warrant_h5_events.csv", encoding="utf-8")))
e = [x for x in ev if x["group"] == "C_top20" and x["T"] == "20241001" and x["code"] == "2330"][0]
# T=20241001(i0)，E=20241002(i1) open 101；h=5 → X=i5 close 105 → +3.9604%；基準 = (3.9604 + 0)/2
check("H1 台積電第 1 名、5 日報酬與超額正確", e["rank"] == "1" and abs(float(e["r5"]) - 3.9604) < 1e-3 and abs(float(e["x5"]) - 1.9802) < 1e-3, e)
check("H2 跳空＝T+1 開 ÷ T 收", abs(float(e["gap"]) - 1.0) < 1e-6, e["gap"])
check("H3 成交金額 < 1 億的不進候選與基準", not any(x["code"] == "1101" for x in ev))
check("H4 認售前 20 有台積電（淨買 > 0）", any(x["group"] == "P_top20" and x["code"] == "2330" for x in ev))
check("H5 報告產生", os.path.exists("warrant_study_report.md") and "主要格判定" in open("warrant_study_report.md", encoding="utf-8").read())

wf.dt.datetime = real_now
print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
