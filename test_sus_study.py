#!/usr/bin/env python3
"""sus_study.py v3 的合成資料測試（不讀真資料）。照 SUS 事先登記 v2.4 與 GPT 06（A1～A4、B）、08（P1～P6）檢查。"""
import datetime as dt, json, math, os, random, statistics as st, sys, tempfile, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sus_study as S

FAILS = []
def check(name, ok, info=""):
    print(("通過  " if ok else "失敗  ") + name + ("" if ok else "  →  %s" % (info,)))
    if not ok:
        FAILS.append(name)

def weekdays(a, b):
    d, out = dt.date(*a), []
    while d <= dt.date(*b):
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += dt.timedelta(days=1)
    return out

TPE = S.TPE
FAR = S.parse_time("2099-01-01T00:00:00Z")
def VER(months, srcs=("X",), at=FAR):
    """前瞻覆蓋要有每個來源月的抓取時間（P5b）；測試預設抓取時間在很遠的未來＝已核對到出場日之後。"""
    return dict(verified={"%s_%s" % (x, m): at for x in srcs for m in months}, sources=list(srcs), ok=True)
# ---- T1 歷史進場日 ----
days = weekdays((2025, 1, 1), (2025, 12, 31))
check("T1a 11 日是交易日 → 11 日版 12 日", S.historical_entry("202501", 11, days)[2] == "20250212")
check("T1b 11 日週六 → 11 日版 13 日（決策＝11 日 19:30）", S.historical_entry("202509", 11, days)[2] == "20251013" and
      S.historical_entry("202509", 11, days)[0] == dt.datetime(2025, 10, 11, 19, 30, tzinfo=TPE))
check("T1c 16 日週日 → 16 日版 17 日", S.historical_entry("202510", 16, days)[2] == "20251117")
check("T1d 16 日交易日 → 16 日版 16 日、價量基準＝15 日", S.historical_entry("202511", 16, days)[1:] == ("20251215", "20251216"))
check("T1e ym_add 跨年", S.ym_add("202501", -1) == "202412" and S.ym_add("202412", 13) == "202601")

# ---- A3 時間解析 ----
check("A3a 帶時區 ISO", S.parse_time("2026-11-17T10:00:00+08:00") == dt.datetime(2026, 11, 17, 10, tzinfo=TPE))
check("A3b UTC Z 轉換", S.parse_time("2026-11-11T11:30:00Z") == dt.datetime(2026, 11, 11, 19, 30, tzinfo=TPE))
check("A3c 只有日期 → 當日 23:59:59 台北", S.parse_time("20261111") == dt.datetime(2026, 11, 11, 23, 59, 59, tzinfo=TPE))
check("A3d 沒時區的 ISO → 不採用", S.parse_time("2026-11-17T10:00:00") is None)
check("A3e 無效格式 → 不採用", S.parse_time("2026/11/17") is None and S.parse_time("20261340") is None and S.parse_time("") is None)
dec = dt.datetime(2026, 11, 11, 23, 59, 59, tzinfo=TPE)
acts = dict(by_code={"X": [dict(eff="20261120", known_at=S.parse_time("2026-11-17T10:00:00+08:00"), cash=False, type="t", id="a")]},
            covered={"202611", "202612"}, **VER(("202611", "202612")))
check("A3f 17 日才公告、11 日決策 → unresolved（v1 用字串前 8 碼會誤判成已知）",
      S.classify_actions("X", "20261112", "20261210", dec, acts, False, False)[0] == "unresolved")
acts["by_code"]["X"][0]["known_at"] = S.parse_time("20261111")
check("A3g 只知道 11 日公告、11 日晚上決策 → 已知", S.classify_actions("X", "20261112", "20261210", dec, acts, False, False)[0] == "skip_known")
check("A3h 只知道 11 日公告、11 日 19:30 決策 → 不算已知（日期只當 23:59:59）",
      S.classify_actions("X", "20261112", "20261210", dt.datetime(2026, 11, 11, 19, 30, tzinfo=TPE), acts, False, False)[0] == "unresolved")

# ---- A2 前瞻的公司行動 ----
acts2 = dict(by_code={"X": [dict(eff="20261120", known_at=None, cash=False, type="t", id="a")]}, covered={"202611", "202612"},
             **VER(("202611", "202612")))
check("A2a 歷史：沒有時間 → announce_unknown", S.classify_actions("X", "20261112", "20261210", dec, acts2, False, False)[0] == "announce_unknown")
check("A2b 前瞻：沒有時間 → unresolved", S.classify_actions("X", "20261112", "20261210", dec, acts2, True, False)[0] == "unresolved")
acts3 = dict(by_code={}, covered={"202611"}, **VER(("202611",)))
check("A2c 前瞻：事件表沒涵蓋持有期月份 → unresolved", S.classify_actions("X", "20261112", "20261210", dec, acts3, True, False)[0] == "unresolved")
check("A2d 前瞻：沒有事件表 → unresolved（不能用暫代）", S.classify_actions("X", "20261112", "20261210", dec, None, True, True)[0] == "unresolved")
check("A2e 歷史：沒涵蓋 → 暫代旗標", S.classify_actions("X", "20261112", "20261210", dec, acts3, False, True)[0] == "proxy" and
      S.classify_actions("X", "20261112", "20261210", dec, acts3, False, False)[0] is None)
acts4 = dict(by_code={"X": [dict(eff="20261120", known_at=None, cash=True, type="ex_dividend", id="d")]}, covered={"202611", "202612"},
             **VER(("202611", "202612")))
r4 = S.classify_actions("X", "20261112", "20261210", dec, acts4, True, False)
check("A2f 純除息不排除、標 cash_div", r4[0] is None and r4[2] is True)
check("A2g 生效日＝進場日不算持有期內", S.classify_actions("X", "20261120", "20261210", dec, acts2, True, False)[0] is None)

# ---- T2 SUS ----
rev = {"A": {}}
ym = "202512"
for k in range(37):
    rev["A"][S.ym_add(ym, -k)] = 1000.0 + (36 - k) * 10 + (k * k % 7) * 3.0
d = [rev["A"][S.ym_add(ym, -k)] - rev["A"][S.ym_add(ym, -k - 12)] for k in range(25)]
z, why = S.sus_series(rev, "A", ym)
check("T2a SUS 跟手算一致", why == "ok" and abs(z - (d[0] - st.mean(d[1:25])) / st.stdev(d[1:25])) < 1e-12)
r2 = {"A": dict(rev["A"])}; del r2["A"][S.ym_add(ym, -36)]
check("T2b 缺第 t−36 月 → missing_history_month", S.sus_series(r2, "A", ym) == (None, "missing_history_month"))
r2b = {"A": dict(rev["A"])}; del r2b["A"][ym]
check("T2c 當月沒有 → no_current_month（跟 37 月不齊分開）", S.sus_series(r2b, "A", ym) == (None, "no_current_month"))
r3 = {"A": {S.ym_add(ym, -k): 100.0 + (36 - k) for k in range(37)}}
check("T2d σ＝0 → 不算", S.sus_series(r3, "A", ym)[1] == "sigma_zero")

# ---- T3 分組、T4 NW ----
g = S.quintiles([(str(i), float(i)) for i in range(10)])
check("T3a 10 家各 2 家", [sum(1 for v in g.values() if v == q) for q in range(1, 6)] == [2] * 5)
g = S.quintiles([("a", 1.0), ("b", 1.0), ("c", 2.0), ("d", 3.0), ("e", 4.0)])
check("T3b 同值同名次", g["a"] == g["b"] == 2 and g["e"] == 5)
xs = {S.ym_add("202401", k): v for k, v in enumerate([1.0, 2, 0.5, 3, -1, 2, 1.5, 0, 2.5, 1, 0.2, 1.8, 2.2])}
n = len(xs); xb = sum(xs.values()) / n; u = [v - xb for v in xs.values()]
s2 = sum(e * e for e in u) + 2 * 0.5 * sum(u[i] * u[i - 1] for i in range(1, n))
check("T4a NW lag1 手算", abs(S.nw_t(xs, 1) - xb / math.sqrt(s2 / (n * (n - 1)))) < 1e-12)
gap = dict(xs); del gap["202403"]; gap["202602"] = 1.0
yms = sorted(gap); n = len(gap); xb = sum(gap.values()) / n
uu = {S.ym_index(k): gap[k] - xb for k in yms}
s2 = sum(e * e for e in uu.values()) + 2 * 0.5 * sum(e * uu[i - 1] for i, e in uu.items() if i - 1 in uu)
check("T4b 缺月不壓縮", abs(S.nw_t(gap, 1) - xb / math.sqrt(s2 / (n * (n - 1)))) < 1e-12)
check("T4c n<12、SE=0 不報 t", S.nw_t({k: xs[k] for k in list(xs)[:11]}, 1) is None and S.nw_t({k: 1.0 for k in xs}, 1) is None)

# ---- 合成世界 ----
def world(n_stock=120, effect=0.0015, seed=7, rev_end="202312", d_end=(2024, 6, 28)):
    rnd = random.Random(seed)
    rev, yms_, y = {}, [], "201701"
    while y <= rev_end:
        yms_.append(y); y = S.ym_add(y, 1)
    for k in range(n_stock):
        c = "%04d" % (1000 + k)
        base = rnd.uniform(500, 5000)
        rev[c] = {m: base * (1 + 0.002 * i) * (1 + rnd.gauss(0, 0.15)) for i, m in enumerate(yms_)}
    dd = weekdays((2020, 1, 1), d_end)
    px = {}
    for c in rev:
        lvl, rows = 100.0, {}
        for day in dd:
            m = S.ym_add(day[:6], -1 if int(day[6:]) > 11 else -2)
            zz = S.sus_series(rev, c, m)[0] if m in rev[c] else None
            zz = zz or 0.0
            o = lvl
            lvl = lvl * (1 + effect * (1 if zz > 0.8 else -1 if zz < -0.8 else 0) + rnd.gauss(0, 0.01))
            rows[day] = (o, lvl, 100.0, 20000.0)
        px[c] = rows
    return px, rev

px, rev = world()
days_, _src = S.build_calendar(px)
res = S.study(px, rev, forward_first="203001")
ex = res["segments"]["探索（歷史月，不判定通過）"]
check("T6a 有效應的世界：探索段兩版超額 > 0、t ≥ 2", ex["v11"]["excess"] > 0 and ex["v11"]["t"] >= 2 and ex["v16"]["t"] >= 2, (ex["v11"], ex["v16"]))
check("T6b 歷史段標探索、不判定", ex["status"] == "探索")
check("T6c 前瞻段沒有月份 → 資料不足（持續追蹤）", res["segments"]["前瞻"]["status"].startswith("資料不足"))
px0, rev0 = world(effect=0.0, seed=8)
r0 = S.study(px0, rev0, forward_first="203001")["segments"]["探索（歷史月，不判定通過）"]
check("T6d 沒效應 → 不是兩版都 t≥2", not (r0["v11"]["t"] and r0["v11"]["t"] >= 2 and r0["v16"]["t"] and r0["v16"]["t"] >= 2), (r0["v11"], r0["v16"]))

m11 = S.run_version(px, rev, days_, 11)
ym0 = sorted(y for y, r in m11.items() if S.month_valid(r))[0]
E = m11[ym0]["E"]; ei = days_.index(E); Sd = m11[ym0]["S"]
grp = S.quintiles([(c, z) for c in px if (z := S.sus_series(rev, c, ym0)[0]) is not None])
c0 = [c for c, v in grp.items() if v == 5][0]
manual = (px[c0][days_[ei + 19]][1] / px[c0][E][0] - 1) * 100
check("T7 Q5 報酬＝第 20 個交易日收盤 ÷ 進場日開盤", any(abs(x - manual) < 1e-9 for x in m11[ym0]["h"][20]["raw_q5"]))

def mod(code, fn):
    p2 = dict(px); p2[code] = fn(dict(px[code])); return p2
p_low = mod(c0, lambda r: {k: (v[0], v[1], v[2], 5000.0) for k, v in r.items()})
check("T8 成交金額不足 → 不進分組", all(abs(x - manual) > 1e-9 for x in S.run_version(p_low, rev, days_, 11)[ym0]["h"][20]["raw_q5"]))
p_s = mod(c0, lambda r: {k: v for k, v in r.items() if k != E})
check("T9a 進場日停牌 → 不可成交", S.run_version(p_s, rev, days_, 11)[ym0]["untradable"] >= 1)
X, X2 = days_[ei + 19], days_[ei + 20]
p_e = mod(c0, lambda r: {k: v for k, v in r.items() if k != X})
want2 = (px[c0][X2][1] / px[c0][E][0] - 1) * 100
check("T9b 出場日停牌 → 下一個成交日收盤", any(abs(x - want2) < 1e-9 for x in S.run_version(p_e, rev, days_, 11)[ym0]["h"][20]["raw_q5"]))
p_d = mod(c0, lambda r: {k: v for k, v in r.items() if k <= days_[ei + 5]})
rd = S.run_version(p_d, rev, days_, 11)[ym0]
tr = [t for t in rd["trades"] if t["code"] == c0 and t["h"] == 20][0]
check("T10 持有中之後再無成交 → no_exit、保留最後價格日、該月無效", rd["h"][20]["no_exit"] == 1 and not S.month_valid(rd) and
      tr["last_price_date"] == days_[ei + 5])

# ---- A4 非有限價格 ----
p_inf = mod(c0, lambda r: dict(r, **{X: (r[X][0], float("inf"), r[X][2], r[X][3])}))
ri = S.run_version(p_inf, rev, days_, 11)[ym0]
check("A4a 20 日收盤 inf → data_error、該月無效", ri["h"][20]["data_error"] >= 1 and not S.month_valid(ri))
p_nan = mod(c0, lambda r: dict(r, **{Sd: (r[Sd][0], r[Sd][1], r[Sd][2], float("nan"))}))
rn = S.run_version(p_nan, rev, days_, 11)[ym0]
check("A4b 基準日成交額 nan → data_error、該月無效", rn["data_error"] and not S.month_valid(rn))
p_on = mod(c0, lambda r: dict(r, **{E: (float("nan"), r[E][1], r[E][2], r[E][3])}))
check("A4c 進場開盤 nan → data_error、該月無效", not S.month_valid(S.run_version(p_on, rev, days_, 11)[ym0]))

# ---- 歷史段公司行動表 ----
eff = days_[ei + 3]
cov_all = {d[:6] for d in days_}
def A(rows, cov=cov_all):
    by = {}
    for code, e, known, cash in rows:
        by.setdefault(code, []).append(dict(eff=e, known_at=known, cash=cash, type="t", id="%s|%s" % (code, e)))
    return dict(by_code=by, covered=set(cov), **VER(cov))
r1 = S.run_version(px, rev, days_, 11, A([(c0, eff, S.at_tpe(days_[ei - 5]), False)]))[ym0]
check("T11a 歷史：決策前已知 → 跳過、月有效", r1["h"][20]["ca"].get("skip_known") == 1 and S.month_valid(r1) and
      all(abs(x - manual) > 1e-9 for x in r1["h"][20]["raw_q5"]))
r2_ = S.run_version(px, rev, days_, 11, A([(c0, eff, S.at_tpe(days_[ei + 1]), False)]))[ym0]
check("T11b 歷史：決策後才公告 → 未解、保留原筆、月無效", r2_["h"][20]["unresolved"] == 1 and not S.month_valid(r2_) and
      any(abs(x - manual) < 1e-9 for x in r2_["h"][20]["raw_q5"]))
r3_ = S.run_version(px, rev, days_, 11, A([(c0, eff, None, False)]))[ym0]
check("T11c 歷史：沒有時間 → 事後資料品質排除、月有效", r3_["h"][20]["ca"].get("announce_unknown") == 1 and S.month_valid(r3_))
gp = dict(px[c0]); kk = days_[ei + 4]; gp[kk] = (gp[days_[ei + 3]][1] * 0.5, gp[kk][1], gp[kk][2], gp[kk][3])
p_g = dict(px); p_g[c0] = gp
rg = S.run_version(p_g, rev, days_, 11)[ym0]
check("T12 歷史：沒有事件表 → 11% 暫代旗標排除、月有效", rg["h"][20]["ca"].get("proxy") == 1 and S.month_valid(rg))
few = {c: px[c] for c in list(px)[:40]}
check("T13 合格 < 50 → too_few", all(r["status"] in ("too_few", "immature") for r in S.run_version(few, rev, S.build_calendar(few)[0], 11).values()))
pxn = dict(px); lv = 100.0; xr = {}
for day in days_:
    xr[day] = (lv, lv * 1.05, 100.0, 20000.0); lv *= 1.05
pxn["0050"] = xr
mm = S.run_version(pxn, rev, days_, 11)[ym0]["h"][20]
check("T14 沒營收的代號（ETF）進對照組平均、不進 SUS 合格平均", mm["uni"] > m11[ym0]["h"][20]["uni"] and abs(mm["qual"] - m11[ym0]["h"][20]["qual"]) < 1e-9)
cd = S.run_version(px, rev, days_, 11, A([(c0, eff, None, True)]), excl_cash_div=True)[ym0]
check("T15 敏感度：排除持有期內有除息 → 該筆不進", all(abs(x - manual) > 1e-9 for x in cd["h"][20]["raw_q5"]))

# ---- A1 前瞻：快照與決策紀錄 ----
fwd = ym0
root = tempfile.mkdtemp(); sd = os.path.join(root, "snap", fwd); os.makedirs(sd)
data = ("market,code,ym,rev\n" + "".join("sii,%s,%s,%r\n" % (c, y, v) for c, mm_ in rev.items() for y, v in mm_.items())).encode()
open(os.path.join(sd, "panel.csv"), "wb").write(data)
nm = S.ym_add(fwd, 1)
cut = "%s-%s-11T11:40:00Z" % (nm[:4], nm[4:])
mr = os.path.join(root, "metas"); os.makedirs(mr)
def put_meta(dirp, meta, persist=True, persist_meta=None):
    json.dump(meta, open(os.path.join(dirp, "meta.json"), "w"))
    if persist:
        json.dump(persist_meta if persist_meta is not None else meta, open(os.path.join(mr, os.path.basename(dirp) + ".json"), "w"))
M0 = dict(revenue_month=fwd, formal=True, cutoff_at=cut, panel_sha256=hashlib.sha256(data).hexdigest())
put_meta(sd, M0)
snaps = S.load_snapshots(os.path.join(root, "snap"), mr)
check("A1a 快照讀得到", fwd in snaps)
mx = S.run_version(px, rev, days_, 11, snaps=snaps, decisions={}, forward_first=fwd)[fwd]
check("A1b 前瞻月沒有決策紀錄 → missing_decision（不用名義時間回填）", mx["status"] == "missing_decision" and not S.month_valid(mx))
# 用 decide() 在 S 日晚上寫決策紀錄
droot = os.path.join(root, "dec")
nowS = S.at_tpe(Sd, 23, 10, 0)
pxS = {c: {k: v for k, v in r.items() if k <= Sd} for c, r in px.items()}
w = S.decide(pxS, snaps, [d for d in days_ if d <= Sd], E, lambda: nowS, root=droot, forward_first=fwd)
check("A1c decide 寫下 11、16 日版中符合的那版（11 日版）", any(os.path.basename(p).startswith("%s_v11_" % fwd) for p in w) and
      not any("_v16_" in p for p in w), w)
w2 = S.decide(pxS, snaps, [d for d in days_ if d <= Sd], E, lambda: S.at_tpe(Sd, 23, 30, 0), root=droot, forward_first=fwd)
check("A1d 已有同一預定日的紀錄 → 不再寫", not w2, w2)
decs = S.load_decisions(droot)
mf = S.run_version(px, rev, days_, 11, A([]), snaps=snaps, decisions=decs, forward_first=fwd)[fwd]
mh = S.run_version(px, rev, days_, 11, A([]))[fwd]
check("A1e 前瞻用決策紀錄：進場日、名單與歷史名義算法一致時結果相同", mf["E"] == mh["E"] and
      sorted(mf["h"][20]["raw_q5"]) == sorted(mh["h"][20]["raw_q5"]) and S.month_valid(mf), (mf["status"], mf["E"], mh["E"]))
# 延誤：決策在進場日 09:05 才算完 → 順延，但前一交易日已不是當時的價量基準日 → 決策過期
late = dict(decs[(fwd, 11)][0]); late["_decision_at"] = S.open_at(E) + dt.timedelta(minutes=5)
ml = S.run_version(px, rev, days_, 11, snaps=snaps, decisions={(fwd, 11): [late]}, forward_first=fwd)[fwd]
check("A1f 決策晚於開盤 → 不倒填開盤；價量基準日過期 → decision_stale、月無效", ml["status"] == "decision_stale" and ml["E"] is None)
bad = dict(decs[(fwd, 11)][0]); bad["snapshot_sha256"] = "0" * 64
check("A1g 決策紀錄的快照雜湊對不上 → 不用", S.run_version(px, rev, days_, 11, snaps=snaps, decisions={(fwd, 11): [bad]},
                                                    forward_first=fwd)[fwd]["status"] == "decision_snapshot_mismatch")
# 前瞻 + 事件表沒涵蓋 → 未解、月無效；保留原筆
mfc = S.run_version(px, rev, days_, 11, A([], cov=set()), snaps=snaps, decisions=decs, forward_first=fwd)[fwd]
check("A2h 前瞻：事件表沒涵蓋 → 月無效（不能用暫代通過）", not S.month_valid(mfc) and mfc["h"][20]["unresolved"] > 0)
mfn = S.run_version(px, rev, days_, 11, None, snaps=snaps, decisions=decs, forward_first=fwd)[fwd]
check("A2i 前瞻：沒有事件表 → 月無效", not S.month_valid(mfn))
mfu = S.run_version(px, rev, days_, 11, A([(c0, eff, None, False)]), snaps=snaps, decisions=decs, forward_first=fwd)[fwd]
mfk = S.run_version(px, rev, days_, 11, A([(c0, eff, S.at_tpe(days_[ei - 5]), False)]), snaps=snaps, decisions=decs, forward_first=fwd)[fwd]
check("A2k 前瞻：事件決策前已知（有可靠時間）→ 跳過、月有效", mfk["h"][20]["ca"].get("skip_known") == 1 and S.month_valid(mfk))
check("A2j 前瞻：事件沒有時間 → 未解、保留原筆、月無效", mfu["h"][20]["unresolved"] == 1 and not S.month_valid(mfu) and
      any(abs(x - manual) < 1e-9 for x in mfu["h"][20]["raw_q5"]))
# 快照雜湊被改 → 不收
open(os.path.join(sd, "panel.csv"), "ab").write(b"x")
check("A1h 快照雜湊對不上 → 不收", fwd not in S.load_snapshots(os.path.join(root, "snap"), mr))
# 快照 cutoff 早於 M+1 月 11 日 → 不收
sd2 = os.path.join(root, "snap2", fwd); os.makedirs(sd2); open(os.path.join(sd2, "panel.csv"), "wb").write(data)
put_meta(sd2, dict(M0, cutoff_at="%s-%s-10T11:30:00Z" % (nm[:4], nm[4:])))
check("A1i 快照 cutoff 早於 11 日 → 不收", fwd not in S.load_snapshots(os.path.join(root, "snap2"), mr))
sd3 = os.path.join(root, "snap3", fwd); os.makedirs(sd3); open(os.path.join(sd3, "panel.csv"), "wb").write(data)
put_meta(sd3, dict(M0, revenue_month=S.ym_add(fwd, 1)))
check("A1j meta 月份跟資料夾不同 → 不收", fwd not in S.load_snapshots(os.path.join(root, "snap3"), mr))
sd4 = os.path.join(root, "snap4", fwd); os.makedirs(sd4); open(os.path.join(sd4, "panel.csv"), "wb").write(data)
put_meta(sd4, M0, persist_meta=dict(M0, panel_sha256="1" * 64))
check("A1k 跟 repo 裡的持久雜湊不一致 → 不收", fwd not in S.load_snapshots(os.path.join(root, "snap4"), mr))
put_meta(sd4, M0)          # 還原
# decide 延誤原因
droot2 = os.path.join(root, "dec2")
snaps4 = S.load_snapshots(os.path.join(root, "snap4"), mr)
S.decide({c: {k: v for k, v in r.items() if k <= days_[ei]} for c, r in px.items()}, snaps4,
         [d for d in days_ if d <= days_[ei]], days_[ei + 1], lambda: S.at_tpe(days_[ei], 23, 0, 0), root=droot2, forward_first=fwd)
dd2 = S.load_decisions(droot2)
check("A1l 名義最早日已過才決策 → 記延誤原因", (fwd, 11) in dd2 and dd2[(fwd, 11)][0]["delay_reason"])

# ---- 判定：只在 12／24／36 正式檢視 ----
def fake(excess, t_ok, net, months):
    out = {}
    for k, y in enumerate(months):
        e = excess + (0.3 if k % 2 else -0.3) * (1 if t_ok else 20)
        out[y] = dict(status="ok", data_error=[], h={20: dict(q5=e + 1, uni=1, qual=1, q1=0, raw_q5=[net + S.COST] * 3,
                                                              no_exit=0, data_error=0, unresolved=0, ca={})})
    return out
M = [S.ym_add("202610", k) for k in range(12)]
check("J1 兩版都過 → 通過", S.judge(fake(1, True, 1, M), fake(1, True, 1, M), M)[0] == "通過")
check("J2 只有一版 → 未共同通過", S.judge(fake(1, True, 1, M), fake(1, False, 1, M), M)[0] == "未共同通過")
check("J3 淨 ≤ 0 → 測不出來", S.judge(fake(1, True, -0.1, M), fake(1, True, -0.1, M), M)[0] == "測不出來")
check("J4 不足 12 → 資料不足", S.judge(fake(1, True, 1, M), fake(1, True, 1, M), M[:11])[0] == "資料不足")
s = S.summarize(fake(1, True, 1, M), M)
check("J5 摘要有 95% 區間與經過曆月", s["ci95"] and s["ci95"][0] < s["excess"] < s["ci95"][1] and s["elapsed_calendar_months"] == 12)
res_f = S.study(px, rev, forward_first="202101")
fw = res_f["segments"]["前瞻"]
check("J6 前瞻段沒有快照與決策紀錄 → 全部不算（不偷用 panel）", fw["common_months"] == 0)
check("J7 報告與逐筆清單產生", "前瞻" in S.report(res) and len(res["_trades"][11]) > 0)
check("J8 下一個市場日跳過週末與官方休市", S.next_market_day("20261009", ["20261012"]) == "20261013" and
      S.next_market_day("20261023", []) == "20261026")

# ======== GPT 08：P1～P6 反例 ========
def seq(*ts):
    it = iter(ts)
    last = [ts[-1]]
    def f():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return f
# P1a 開始在開盤前、算完已過開盤 → 不寫正式紀錄、只寫嘗試紀錄；之後還能重新決策
dr3 = os.path.join(root, "dec3")
t0, t1 = S.open_at(E) - dt.timedelta(seconds=1), S.open_at(E) + dt.timedelta(seconds=1)
w3 = S.decide(pxS, snaps, [d for d in days_ if d <= Sd], E, seq(t0, t0, t1), root=dr3, forward_first=fwd)
att = os.listdir(os.path.join(dr3, "attempts")) if os.path.isdir(os.path.join(dr3, "attempts")) else []
check("P1a 算完時已過開盤 → 沒有正式紀錄、有嘗試紀錄（late_after_open）", not w3 and not S.load_decisions(dr3) and len(att) == 1 and
      json.load(open(os.path.join(dr3, "attempts", att[0]), encoding="utf-8"))["status"] == "late_after_open", (w3, att))
rec0 = decs[(fwd, 11)][0]
check("P1a2 decision_at＝算完才讀的時間（不是開始時間）", S.decide(pxS, snaps, [d for d in days_ if d <= Sd], E,
      seq(S.at_tpe(Sd, 23, 0, 0), S.at_tpe(Sd, 23, 0, 0), S.at_tpe(Sd, 23, 7, 0)), root=os.path.join(root, "dec3b"), forward_first=fwd) and
      S.load_decisions(os.path.join(root, "dec3b"))[(fwd, 11)][0]["decision_at"].startswith("%s-%s-%sT23:07" % (Sd[:4], Sd[4:6], Sd[6:])))
# P1b 決策後只修訂 ETF 基準日成交額 → 前瞻對照組仍用當時母體；差異另報
pxE = dict(px); donor = sorted(px)[0]
pxE["9999"] = {k: (v[0], v[1] * 1.0, v[2], 20000.0) for k, v in px[donor].items()}
pxE["9999"] = {k: (v[0] * 1.01, v[1] * 1.02, v[2], v[3]) for k, v in pxE["9999"].items()}
dr4 = os.path.join(root, "dec4")
S.decide({c: {k: v for k, v in r.items() if k <= Sd} for c, r in pxE.items()}, snaps, [d for d in days_ if d <= Sd], E, lambda: nowS,
         root=dr4, forward_first=fwd)
d4 = S.load_decisions(dr4)
check("P1b0 決策紀錄存完整母體（含沒有營收的代號）與價量列雜湊", "9999" in d4[(fwd, 11)][0]["universe"] and d4[(fwd, 11)][0]["universe_rows_sha256"])
pxE2 = dict(pxE); pxE2["9999"] = dict(pxE["9999"]); o_ = pxE2["9999"][Sd]; pxE2["9999"][Sd] = (o_[0], o_[1], o_[2], 5000.0)
fa = S.run_version(pxE, rev, days_, 11, A([]), snaps=snaps, decisions=d4, forward_first=fwd)[fwd]
fb = S.run_version(pxE2, rev, days_, 11, A([]), snaps=snaps, decisions=d4, forward_first=fwd)[fwd]
check("P1b 後修價量不改當時對照母體（母體平均不變），另標重算不同與價量被修訂",
      S.month_valid(fa) and S.month_valid(fb) and abs(fa["h"][20]["uni"] - fb["h"][20]["uni"]) < 1e-12 and fb["n_cand"] == fa["n_cand"]
      and fb.get("universe_recompute_mismatch") and fb.get("price_revised_after_decision"), (fa["n_cand"], fb["n_cand"]))
# P1c 讀取端驗時間：decision_at 早於快照 cutoff、算完晚於 decision_at → 不用
bad1 = dict(rec0); bad1["_decision_at"] = snaps[fwd]["cutoff_at"] - dt.timedelta(hours=1); bad1["_completed_at"] = bad1["_decision_at"]
check("P1c decision_at 早於快照 cutoff（11 日 18:00 對 19:30）→ decision_invalid",
      S.run_version(px, rev, days_, 11, A([]), snaps=snaps, decisions={(fwd, 11): [bad1]}, forward_first=fwd)[fwd]["status"] == "decision_invalid")
bad2 = dict(rec0); bad2["_completed_at"] = rec0["_decision_at"] + dt.timedelta(minutes=1)
check("P1d 算完時間晚於 decision_at → decision_invalid",
      S.run_version(px, rev, days_, 11, A([]), snaps=snaps, decisions={(fwd, 11): [bad2]}, forward_first=fwd)[fwd]["status"] == "decision_invalid")
bad3 = dict(rec0); bad3["registration_sha256"] = "f" * 64
check("P1e 登記雜湊不同 → decision_invalid",
      S.run_version(px, rev, days_, 11, A([]), snaps=snaps, decisions={(fwd, 11): [bad3]}, forward_first=fwd)[fwd]["status"] == "decision_invalid")
# P4 臨時休市在紀錄寫好之後才公告：預定進場日沒開市 → 這份過期；當晚重新決策，用新的一份
daysC = [d for d in days_ if d != E]
mc_ = S.run_version(px, rev, daysC, 11, A([]), snaps=snaps, decisions=decs, forward_first=fwd)[fwd]
check("P4a 預定進場日臨時休市 → 舊紀錄 decision_stale（不倒填、不靜默改檔）", mc_["status"] == "decision_stale", mc_["status"])
nxt = days_[ei + 1]
w5 = S.decide(pxS, snaps, [d for d in daysC if d <= Sd], nxt, lambda: S.at_tpe(E, 23, 10, 0), root=droot, forward_first=fwd)
d5 = S.load_decisions(droot)
check("P4b 休市當晚另寫一份新紀錄（舊的保留、記被取代）", len(w5) == 1 and len(d5[(fwd, 11)]) == 2 and
      d5[(fwd, 11)][1]["superseded_attempts"], w5)
mc2 = S.run_version(px, rev, daysC, 11, A([]), snaps=snaps, decisions=d5, forward_first=fwd)[fwd]
check("P4c 用最早一份實際可執行的紀錄：進場順延到下一個市場日", S.month_valid(mc2) and mc2["E"] == nxt, (mc2["status"], mc2["E"]))
w6 = S.decide(pxS, snaps, [d for d in daysC if d <= Sd], nxt, lambda: S.at_tpe(E, 23, 30, 0), root=droot, forward_first=fwd)
check("P4d 同一預定日已有紀錄 → 不重寫", not w6)
hol = ["20261010", "20261023", "20261025", "20261026"]
check("P4e 合併臨時休市：10/22 之後下一個市場日＝10/27", S.next_market_day("20261022", hol) == "20261027" and
      S.next_market_day("20261022", ["20261010", "20261025", "20261026"]) == "20261023")
cm = dict(built_at=S.parse_time("2026-10-25T01:00:00Z"), schedule_years=["2026"])
check("P4f 日曆新鮮且涵蓋年度 → 可用；超過 36 小時、年度沒涵蓋、沒有日曆 → 不可用",
      S.calendar_usable(cm, "20261027", S.parse_time("2026-10-25T20:00:00+08:00"))[0] and
      not S.calendar_usable(cm, "20261027", S.parse_time("2026-10-27T20:00:00+08:00"))[0] and
      not S.calendar_usable(cm, "20270104", S.parse_time("2026-10-25T20:00:00+08:00"))[0] and
      not S.calendar_usable(None, "20261027", S.parse_time("2026-10-25T20:00:00+08:00"))[0])
import market_calendar as MC, gzip
ct = tempfile.mkdtemp(); MC.RAW = os.path.join(ct, "raw"); MC.OUT = os.path.join(ct, "cal.json"); MC.CLOSURES = os.path.join(ct, "cl.json")
os.makedirs(MC.RAW)
gzip.open(os.path.join(MC.RAW, "FMTQIK_202610.json.gz"), "wb").write(json.dumps(dict(stat="OK", data=[["115/10/21"], ["115/10/22"]])).encode())
gzip.open(os.path.join(MC.RAW, "holidaySchedule.json.gz"), "wb").write(json.dumps([dict(Date="1151026", Name="補假", Description="")]).encode())
json.dump([dict(date="20261023", reason="颱風", announced_at="20261022", source_url="https://www.twse.com.tw/x.pdf")], open(MC.CLOSURES, "w"))
cj = MC.build()
check("P4g 日曆建置：臨時休市併入休市日、記來源與涵蓋年度", "20261023" in cj["holidays"] and cj["closures"] == ["20261023"] and
      cj["schedule_years"] == ["2026"] and cj["closure_sources"][0]["source_url"])
json.dump([dict(date="20261023", reason="颱風")], open(MC.CLOSURES, "w"))
try:
    MC.build(); check("P4h 臨時休市紀錄缺官方網址或公告日 → 拒絕", False)
except SystemExit:
    check("P4h 臨時休市紀錄缺官方網址或公告日 → 拒絕", True)
# P2 歷史月不用今天的 first_seen 當公告時間
evh = dict(by_code={"X": [dict(eff="20211120", ann=None, seen=S.parse_time("2026-10-25T00:00:00Z"), cash=False, type="t", id="h")]},
           covered={"202111", "202112"}, **VER(("202111", "202112")))
dh = S.at_tpe("20211111", 23, 59, 59)
check("P2a 歷史：公告時間空白、first_seen＝今天 → announce_unknown（不是 unresolved）",
      S.classify_actions("X", "20211112", "20211210", dh, evh, False, False)[0] == "announce_unknown")
evh["by_code"]["X"][0]["ann"] = S.at_tpe("20211115")
check("P2b 歷史：可靠公告時間晚於決策 → unresolved", S.classify_actions("X", "20211112", "20211210", dh, evh, False, False)[0] == "unresolved")
evf = dict(by_code={"X": [dict(eff="20261120", ann=None, seen=S.parse_time("2026-11-10T12:00:00Z"), cash=False, type="t", id="f")]},
           covered={"202611", "202612"}, **VER(("202611", "202612")))
check("P2c 前瞻：當時保存的 first_seen 早於決策 → skip_known", S.classify_actions("X", "20261112", "20261210", dec, evf, True, False)[0] == "skip_known")
evf["by_code"]["X"][0]["seen"] = S.parse_time("2026-11-20T12:00:00Z")
check("P2d 前瞻：first_seen 晚於決策、沒有公告時間 → unresolved", S.classify_actions("X", "20261112", "20261210", dec, evf, True, False)[0] == "unresolved")
# P5 事件表：不可用、抓取時間早於出場日
bad_a = A([]); bad_a["ok"] = False
check("P5a 事件表與 coverage 雜湊不符（不可用）→ 前瞻未解、歷史改暫代",
      S.classify_actions("X", "20261112", "20261210", dec, bad_a, True, False)[0] == "unresolved" and
      S.classify_actions("X", "20261112", "20261210", dec, bad_a, False, True)[0] == "proxy")
early = dict(by_code={}, covered={"202611", "202612"}, **VER(("202611", "202612"), at=S.parse_time("2026-12-01T00:00:00Z")))
check("P5b 前瞻：來源月抓取時間早於 min(出場日, 月底) → 未解（月份有抓過不等於核對到出場日）",
      S.classify_actions("X", "20261112", "20261210", dec, early, True, False)[0] == "unresolved" and
      S.classify_actions("X", "20261112", "20261130", dec, early, True, False)[0] is None)
check("P5c 歷史：同一份抓取時間（遠晚於出場）照常涵蓋", S.classify_actions("X", "20261112", "20261210", dec, A([]), False, False)[0] is None)
# P6 前瞻 trim10 用紀錄的 SUS 值重新分組
recT = dict(rec0); keep = sorted(rec0["sus"])[:10]
recT["sus"] = {c: (z if c in keep else 50.0) for c, z in rec0["sus"].items()}
mt_ = S.run_version(px, rev, days_, 11, A([]), sens_trim=True, snaps=snaps, decisions={(fwd, 11): [recT]}, forward_first=fwd)[fwd]
mm_ = S.run_version(px, rev, days_, 11, A([]), snaps=snaps, decisions={(fwd, 11): [recT]}, forward_first=fwd)[fwd]
check("P6a 前瞻 trim10：大多數 |SUS|>10 → 排除後不足 50 家＝too_few；主結果不受影響", mt_["status"] == "too_few" and S.month_valid(mm_),
      (mt_["status"], mm_["status"]))
S.PRICE_QC[c0] = {days_[ei + 2]}
rq = S.run_version(px, rev, days_, 11)[ym0]
S.PRICE_QC.clear()
check("P6b 價量 QC（短列或重複日期）落在持有期 → 資料錯誤、月無效", not S.month_valid(rq) and any("price_qc" in x for x in rq["data_error"]))
p0 = mod(c0, lambda r: dict(r, **{days_[ei + 2]: (r[days_[ei + 2]][0], 0.0, r[days_[ei + 2]][2], r[days_[ei + 2]][3])}))
check("P6c 持有期內收盤 0（非正但有限）→ 資料錯誤", S.run_version(p0, rev, days_, 11)[ym0]["h"][20]["data_error"] >= 1)
pz = mod(c0, lambda r: dict(r, **{E: (0.0, r[E][1], r[E][2], r[E][3])}))
check("P6d 進場開盤 0 → 資料錯誤（不當停牌）", not S.month_valid(S.run_version(pz, rev, days_, 11)[ym0]))
qd = tempfile.mkdtemp()
open(os.path.join(qd, "1234.txt"), "w").write("# h\n" + "".join("2025%02d%02d,1,1,1,1,1,20000\n" % (1 + k // 28, 1 + k % 28) for k in range(40)) +
                                              "20250101,9,9,9,9,9,9\n20250301,1,1\n")
S.load_prices(qd)
check("P6e 讀價量：重複日期保留第一筆並記 QC、短列記 QC", S.PRICE_QC["1234"] == {"20250101", "20250301"})
S.PRICE_QC.clear()
check("P6f 報告分開列「完全沒有營收」與「當月無營收」", "完全沒有營收／當月無營收" in S.report(res))

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
