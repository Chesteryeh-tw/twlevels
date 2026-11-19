#!/usr/bin/env python3
"""market_calendar v5：休市表依年度分檔保存（2026-11 官方改公布下一年度、舊程式整份覆蓋 → SUS 決策停擺）。"""
import datetime as dt, gzip, json, os, re, sys, tempfile
import market_calendar as MC
import sus_study as S

ok = True
def check(name, cond):
    global ok
    print(("通過  " if cond else "失敗  ") + name)
    ok = ok and bool(cond)

def fmtqik(url):
    ym = re.search(r"date=(\d{6})", url).group(1)
    return json.dumps(dict(stat="OK", data=[["%d/%s/02" % (int(ym[:4]) - 1911, ym[4:])]])).encode()

def setup():
    t = tempfile.mkdtemp()
    MC.RAW = os.path.join(t, "raw"); MC.OUT = os.path.join(t, "cal.json"); MC.CLOSURES = os.path.join(t, "cl.json")
    MC.VERIFIED = os.path.join(MC.RAW, "verified.json"); MC.SEED = os.path.join(t, "seed.json")
    os.makedirs(MC.RAW)
    gzip.open(os.path.join(MC.RAW, "FMTQIK_202611.json.gz"), "wb").write(json.dumps(dict(stat="OK", data=[["115/11/18"], ["115/11/19"]])).encode())
    return t

H2026 = [dict(Date="1151225", Name="行憲紀念日", Description=""), dict(Date="1150102", Name="", Description="開始交易")]
H2027 = [dict(Date="1160101", Name="開國紀念日", Description=""), dict(Date="1160105", Name="", Description="開始交易")]
SEED = dict(years={"2026": dict(holidays=["20261225"], trading_marked=["20260102"], source={})})

# 1. 實際發生的情況：快取裡舊單一檔已是 2027，2026 只剩 seed
setup()
gzip.open(os.path.join(MC.RAW, "holidaySchedule.json.gz"), "wb").write(json.dumps(H2027).encode())
json.dump(SEED, open(MC.SEED, "w"))
cj = MC.build()
check("V1 舊單一檔只有 2027 → 用 seed 補回 2026，兩年都涵蓋",
      cj["schedule_years"] == ["2026", "2027"] and "20261225" in cj["holidays"] and "20270101" in cj["holidays"]
      and cj["schedule_year_sources"]["2026"]["file"] == "holiday_seed.json")
check("V1b 標「開始交易」的日子不算休市", "20270105" not in cj["holidays"] and "20260102" not in cj["holidays"])

# 2. fetch：官方只回 2027 → 存成 2027 分檔，2026 分檔不被覆蓋
setup()
calls = []
def fake_get(url):
    calls.append(url)
    if "holidaySchedule" in url:
        return json.dumps(H2027 if hol_calls() > 1 else H2026).encode()
    return fmtqik(url)
def hol_calls():
    return sum("holidaySchedule" in u for u in calls)
MC.get = fake_get; MC.time.sleep = lambda s: None
MC.fetch(refresh_only=True, today=dt.date(2026, 11, 10))      # 第一次：官方還是 2026
MC.fetch(refresh_only=True, today=dt.date(2026, 11, 19))      # 第二次：官方換成 2027
files = sorted(p for p in os.listdir(MC.RAW) if p.startswith("holidaySchedule"))
cj = MC.build()
idx = json.load(open(MC.hol_index_path()))
check("V2 兩次抓到不同年度 → 各存一份，都保留", files == ["holidaySchedule_2026.json.gz", "holidaySchedule_2027.json.gz"]
      and cj["schedule_years"] == ["2026", "2027"] and set(idx) == set(files))
ver = json.load(open(MC.VERIFIED))
check("V2b 核驗紀錄記下這次抓到的年度與檔名", ver["holidaySchedule"]["years"] == ["2027"] and ver["holidaySchedule"]["file"] == "holidaySchedule_2027.json.gz")

# 3. 同年度有新官方檔 → 新的優先，seed 不用
setup()
json.dump(SEED, open(MC.SEED, "w"))
gzip.open(os.path.join(MC.RAW, "holidaySchedule_2026.json.gz"), "wb").write(json.dumps([dict(Date="1151231", Name="新增休市", Description="")]).encode())
json.dump({"holidaySchedule_2026.json.gz": dict(at="2026-12-01T00:00:00Z", years=["2026"], sha256="x")}, open(MC.hol_index_path(), "w"))
cj = MC.build()
check("V3 同年度：抓到的官方分檔優先於 seed（不混用兩份）", "20261231" in cj["holidays"] and "20261225" not in cj["holidays"]
      and cj["schedule_year_sources"]["2026"]["file"] == "holidaySchedule_2026.json.gz")

# 4. 官方回空表 → 停，不寫檔、不更新核驗時間
setup()
MC.get = lambda url: b"[]" if "holidaySchedule" in url else fmtqik(url)
try:
    MC.fetch(refresh_only=True, today=dt.date(2026, 11, 19)); stopped = False
except MC.Stop:
    stopped = True
ver = json.load(open(MC.VERIFIED)) if os.path.exists(MC.VERIFIED) else {}
check("V4 休市表空的 → 停止、不算核驗成功", stopped and "holidaySchedule" not in ver)

# 5. 接到 SUS：修好後 11-19 晚上的決策檢查可用
setup()
gzip.open(os.path.join(MC.RAW, "holidaySchedule.json.gz"), "wb").write(json.dumps(H2027).encode())
json.dump(SEED, open(MC.SEED, "w"))
cj = MC.build()
now = S.parse_time("2026-11-19T23:10:00+08:00"); v = S.parse_time("2026-11-19T23:00:00+08:00")
meta = dict(schedule_years=cj["schedule_years"], verified={"FMTQIK_current": v, "holidaySchedule": v})
check("V5 下一個市場日 2026-11-20、2026 年度由 seed 涵蓋 → 日曆可用；2027-01-04 也可用",
      S.calendar_usable(meta, "20261120", now)[0] and S.calendar_usable(meta, "20270104", now)[0])
check("V5b 沒有 seed、官方只剩 2027 → 照舊不可用（跟修之前一樣停下，不猜）",
      not S.calendar_usable(dict(meta, schedule_years=["2027"]), "20261120", now)[0])

print("\n全部通過" if ok else "\n有失敗")
sys.exit(0 if ok else 1)
