#!/usr/bin/env python3
"""sus_study.py 的合成資料測試（不讀真資料）。照 SUS 事先登記 v2.4 檢查每條規則。"""
import datetime as dt, math, os, random, statistics as st, sys, tempfile
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

# T1 進場日：11 日版＝11 日 19:30 之後第一個開盤；16 日版＝≥16 日
days = weekdays((2025, 1, 1), (2025, 12, 31))
check("T1a 11 日是交易日（2025-02-11 週二）→ 11 日版 02-12", S.entry_date("202501", 11, days) == "20250212")
check("T1b 11 日是週六（2025-10-11）→ 11 日版 10-13（週一）", S.entry_date("202509", 11, days) == "20251013")
check("T1c 16 日是週日（2025-11-16）→ 16 日版 11-17", S.entry_date("202510", 16, days) == "20251117")
check("T1d 16 日是交易日（2025-12-16）→ 16 日版 12-16", S.entry_date("202511", 16, days) == "20251216")
check("T1e ym_add 跨年", S.ym_add("202501", -1) == "202412" and S.ym_add("202412", 13) == "202601")

# T2 SUS 定義：手算
rev = {"A": {}}
ym = "202512"
for k in range(37):
    rev["A"][S.ym_add(ym, -k)] = 1000.0 + (36 - k) * 10 + (k * k % 7) * 3.0
d = [rev["A"][S.ym_add(ym, -k)] - rev["A"][S.ym_add(ym, -k - 12)] for k in range(25)]
want = (d[0] - st.mean(d[1:25])) / st.stdev(d[1:25])
z, why = S.sus_series(rev, "A", ym)
check("T2a SUS 跟手算一致（前 24 個 ΔS、ddof=1、不含當月）", why == "ok" and abs(z - want) < 1e-12, (z, want))
r2 = {"A": dict(rev["A"])}; del r2["A"][S.ym_add(ym, -36)]
check("T2b 缺第 t−36 月 → 不算", S.sus_series(r2, "A", ym) == (None, "missing_month"))
r3 = {"A": {S.ym_add(ym, -k): 100.0 + (36 - k) for k in range(37)}}     # ΔS 全部相同 → σ=0
check("T2c σ＝0 → 不算", S.sus_series(r3, "A", ym)[1] == "sigma_zero")
r4 = {"A": dict(rev["A"])}; r4["A"][ym] = float("nan")
check("T2d 當月非有限 → 不算", S.sus_series(r4, "A", ym)[0] is None)

# T3 分組：⌈5p⌉、平均名次
g = S.quintiles([(str(i), float(i)) for i in range(10)])
check("T3a 10 家各 2 家一組", [sum(1 for v in g.values() if v == q) for q in range(1, 6)] == [2, 2, 2, 2, 2], g)
g = S.quintiles([("a", 1.0), ("b", 1.0), ("c", 2.0), ("d", 3.0), ("e", 4.0)])
check("T3b 同值同名次（a、b 平均名次 1.5 → ⌈5·1.5/5⌉＝2）", g["a"] == g["b"] == 2 and g["c"] == 3 and g["e"] == 5, g)

# T4 Newey-West：照登記公式手算；缺月不壓縮
xs = {S.ym_add("202401", k): v for k, v in enumerate([1.0, 2, 0.5, 3, -1, 2, 1.5, 0, 2.5, 1, 0.2, 1.8, 2.2])}
n = len(xs); xb = sum(xs.values()) / n; u = [v - xb for v in xs.values()]
s2 = sum(e * e for e in u) + 2 * 0.5 * sum(u[i] * u[i - 1] for i in range(1, n))
check("T4a lag1 跟手算一致", abs(S.nw_t(xs, 1) - xb / math.sqrt(s2 / (n * (n - 1)))) < 1e-12)
check("T4b lag0＝一般 t", abs(S.nw_t(xs, 0) - xb / (st.stdev(list(xs.values())) / math.sqrt(n))) < 1e-12)
gap = dict(xs); del gap["202403"]; gap["202602"] = 1.0
yms = sorted(gap); n = len(gap); xb = sum(gap.values()) / n
u = {S.ym_index(k): gap[k] - xb for k in yms}
s2 = sum(e * e for e in u.values()) + 2 * 0.5 * sum(e * u[i - 1] for i, e in u.items() if i - 1 in u)
check("T4c 缺月不壓縮（只配真正相隔 1 曆月的）", abs(S.nw_t(gap, 1) - xb / math.sqrt(s2 / (n * (n - 1)))) < 1e-12)
check("T4d n<12 → 不報 t", S.nw_t({k: xs[k] for k in list(xs)[:11]}, 1) is None)
check("T4e 全部相同（SE=0）→ 不報 t", S.nw_t({k: 1.0 for k in xs}, 1) is None)

# T5 判定：兩版都過才過
def fake(excess, t_ok, net, months):
    out = {}
    for k, ym in enumerate(months):
        e = excess + (0.3 if k % 2 else -0.3) * (1 if t_ok else 20)
        out[ym] = dict(status="ok", by_h={20: dict(q5=e + 1, uni=1, qual=1, q1=0, raw_q5=[net + S.COST] * 3)},
                       delisting=0, missing_exit=0, ca={})
    return out
M = [S.ym_add("202610", k) for k in range(12)]
check("T5a 兩版都過 → 通過", S.judge(fake(1, True, 1, M), fake(1, True, 1, M), M)[0] == "通過")
check("T5b 只有一版 → 未共同通過", S.judge(fake(1, True, 1, M), fake(1, False, 1, M), M)[0] == "未共同通過")
check("T5c 淨 ≤ 0 → 不算過（兩版都 → 測不出來）", S.judge(fake(1, True, -0.1, M), fake(1, True, -0.1, M), M)[0] == "測不出來")
check("T5d 共同有效月 < 12 → 資料不足", S.judge(fake(1, True, 1, M), fake(1, True, 1, M), M[:11])[0] == "資料不足")

# ---- 端到端合成世界 ----
def world(n_stock=120, effect=0.0015, seed=7, rev_start="201701", rev_end="202312"):
    rnd = random.Random(seed)
    rev = {}
    yms = []
    y = rev_start
    while y <= rev_end:
        yms.append(y); y = S.ym_add(y, 1)
    for k in range(n_stock):
        c = "%04d" % (1000 + k)
        base = rnd.uniform(500, 5000)
        rev[c] = {ym: base * (1 + 0.002 * i) * (1 + rnd.gauss(0, 0.15)) for i, ym in enumerate(yms)}
    days = weekdays((2020, 1, 1), (2024, 6, 28))
    px = {}
    for c in rev:
        lvl, rows, sus_now = 100.0, [], 0.0
        for d in days:
            ym = S.ym_add(d[:6], -1 if int(d[6:]) > 11 else -2)
            z = S.sus_series(rev, c, ym)[0] if ym in rev[c] else None
            sus_now = z if z is not None else 0.0
            o = lvl
            lvl = lvl * (1 + effect * (1 if sus_now > 0.8 else -1 if sus_now < -0.8 else 0) + rnd.gauss(0, 0.01))
            rows.append((d, o, lvl, 20000.0))
        px[c] = rows
    return px, rev

px, rev = world()
res = S.study(px, rev, forward_first="202101")
fw = res["segments"]["前瞻（判定）"]
check("T6a 有效應的合成世界 → 通過（兩版都是）", fw["status"] == "通過", (fw["status"], fw["v11"], fw["v16"]))
check("T6b 共同有效月 ≥ 12、事件數 > 0", fw["common_months"] >= 12 and fw["v11"]["events"] > 0, fw["common_months"])
px0, rev0 = world(effect=0.0, seed=8)
r0 = S.study(px0, rev0, forward_first="202101")["segments"]["前瞻（判定）"]
check("T6c 沒效應 → 不是通過", r0["status"] != "通過", (r0["status"], r0["v11"], r0["v16"]))
check("T6d 歷史段標探索、不判定", S.study(px, rev, forward_first="203001")["segments"]["探索（歷史月，不判定通過）"]["status"] == "探索")

# T7 進場與出場的價格：手算一個 Q5 股票的 20 日報酬
m11 = S.run_version(px, rev, S.build_calendar(px), 11)
ym0 = sorted(y for y, r in m11.items() if r["status"] == "ok" and 20 in r["by_h"])[0]
E = m11[ym0]["E"]; days_ = S.build_calendar(px); ei = days_.index(E)
grp = S.quintiles([(c, z) for c in px if (z := S.sus_series(rev, c, ym0)[0]) is not None])
q5 = [c for c, v in grp.items() if v == 5]
rows = {r[0]: r for r in px[q5[0]]}
manual = (rows[days_[ei + 19]][2] / rows[E][1] - 1) * 100
check("T7 Q5 報酬＝第 20 個交易日收盤 ÷ 進場日開盤（entry+19）", any(abs(x - manual) < 1e-9 for x in m11[ym0]["by_h"][20]["raw_q5"]))

# T8 母體：成交金額 < 1 億的不算（先套母體再分組）
pxl = {c: [(d, o, cl, (5000.0 if c == q5[0] else w)) for (d, o, cl, w) in rows_] for c, rows_ in px.items()}
m_l = S.run_version(pxl, rev, days_, 11)
check("T8 成交金額不足的股票不進 Q5", all(abs(x - manual) > 1e-9 for x in m_l[ym0]["by_h"][20]["raw_q5"]))

# T9 進場日停牌 → 不可成交、另報；出場日停牌 → 順延到下一個有成交的收盤
c0 = q5[0]
pxs = dict(px); pxs[c0] = [r for r in px[c0] if r[0] != E]
check("T9a 進場日停牌 → untradable +1", S.run_version(pxs, rev, days_, 11)[ym0]["untradable"] >= 1)
X = days_[ei + 19]; X2 = days_[ei + 20]
pxe = dict(px); pxe[c0] = [r for r in px[c0] if r[0] != X]
want2 = ({r[0]: r for r in px[c0]}[X2][2] / rows[E][1] - 1) * 100
check("T9b 出場日停牌 → 用下一個成交日收盤", any(abs(x - want2) < 1e-9 for x in S.run_version(pxe, rev, days_, 11)[ym0]["by_h"][20]["raw_q5"]))

# T10 持有中下市（之後沒有任何成交）→ 未解下市，該月不進判定
pxd = dict(px); pxd[c0] = [r for r in px[c0] if r[0] <= days_[ei + 5]]
md = S.run_version(pxd, rev, days_, 11)[ym0]
check("T10 持有中下市 → delisting、該月不有效", md["delisting"] == 1 and not S.month_valid(md), md["delisting"])

# T11 公司行動事件表
tmp = tempfile.mkdtemp()
def acts(lines):
    p = os.path.join(tmp, "ca.csv")
    open(p, "w", encoding="utf-8").write("code,effective_date,type,announce_at,cash_only\n" + "\n".join(lines) + "\n")
    return S.load_actions(p)
eff = days_[ei + 3]
a1 = acts(["%s,%s,減資,%s,0" % (c0, eff, days_[ei - 5])])
r1 = S.run_version(px, rev, days_, 11, a1)[ym0]
check("T11a 決策前已公告 → 跳過該筆（skip_known），該月仍有效", r1["ca"].get("skip_known") == 1 and S.month_valid(r1) and
      all(abs(x - manual) > 1e-9 for x in r1["by_h"][20]["raw_q5"]), r1["ca"])
r2_ = S.run_version(px, rev, days_, 11, acts(["%s,%s,減資,%s,0" % (c0, eff, days_[ei + 1])]))[ym0]
check("T11b 決策後才公告 → unresolved，該月不進判定", r2_["ca"].get("unresolved") == 1 and not S.month_valid(r2_), r2_["ca"])
r3_ = S.run_version(px, rev, days_, 11, acts(["%s,%s,減資,,0" % (c0, eff)]))[ym0]
check("T11c 沒有公告時間 → announce_unknown（事後資料品質排除），該月仍有效", r3_["ca"].get("announce_unknown") == 1 and S.month_valid(r3_))
r4_ = S.run_version(px, rev, days_, 11, acts(["%s,%s,除息,%s,1" % (c0, eff, days_[ei - 5])]))[ym0]
check("T11d 純除息不排除", not r4_["ca"] and any(abs(x - manual) < 1e-9 for x in r4_["by_h"][20]["raw_q5"]))
r5_ = S.run_version(px, rev, days_, 11, acts(["%s,%s,減資,%s,0" % (c0, E, days_[ei - 5])]))[ym0]
check("T11e 生效日＝進場日不算持有期內（entry < 生效日 ≤ exit）", not r5_["ca"])
# 11 日版：名義截止是 11 日；11 日當天（週六）公告也算已知
a6 = acts(["%s,%s,減資,%s11,0" % (c0, eff, S.ym_add(ym0, 1))])
check("T11f 11 日版：11 日公告算決策前已知", S.run_version(px, rev, days_, 11, a6)[ym0]["ca"].get("skip_known") == 1)

# T12 暫代旗標：沒有事件表時，持有期內開盤跳 >11% 的那筆排除、標 proxy
pxg = dict(px)
rr = list(px[c0]); k = [r[0] for r in rr].index(days_[ei + 4])
rr[k] = (rr[k][0], rr[k - 1][2] * 0.5, rr[k][2], rr[k][3])
pxg[c0] = rr
rg = S.run_version(pxg, rev, days_, 11)[ym0]
check("T12 暫代：開盤跳 >11% → proxy 排除、該月仍有效", rg["ca"].get("proxy") == 1 and S.month_valid(rg), rg["ca"])

# T13 合格 < 50 家 → 該月 too_few
few = {c: px[c] for c in list(px)[:40]}
check("T13 合格 < 50 → too_few", all(r["status"] == "too_few" for r in S.run_version(few, rev, S.build_calendar(few), 11).values()))

# T14 對照組＝母體全部股票（含沒有 SUS 的），不只 SUS 合格股
revn = {c: v for c, v in rev.items()}; extra = "9999"
pxn = dict(px); lv = 100.0; xr = []
for d in days_:                                    # 沒營收、每天 +5%（開盤＝前收，不觸發暫代旗標）
    xr.append((d, lv, lv * 1.05, 20000.0)); lv *= 1.05
pxn[extra] = xr
mm = S.run_version(pxn, revn, days_, 11)[ym0]["by_h"][20]
m_ = m11[ym0]["by_h"][20]
check("T14 沒有 SUS 的股票也在對照組（母體平均變高），不在 SUS 合格平均", mm["uni"] > m_["uni"] and abs(mm["qual"] - m_["qual"]) < 1e-9)

# T16～T19 前瞻：共同營收快照
import csv as _csv, hashlib as _h, json as _j
days_x = weekdays((2025, 1, 1), (2025, 12, 31))
check("T16a 快照 cutoff 延到 12 日 10:00 → 11 日版改 13 日開盤", S.entry_date("202501", 11, days_x, "202502121000") == "20250213")
check("T16b 快照 cutoff 11 日 19:30 → 11 日版 12 日開盤", S.entry_date("202501", 11, days_x, "202502111930") == "20250212")
check("T16c 16 日版也要晚於 cutoff（cutoff 17 日 08:00 → 17 日開盤 OK）", S.entry_date("202501", 16, days_x, "202502170800") == "20250217")
fwd = ym0                                             # 把前面那個月當前瞻月
m_ns = S.run_version(px, rev, days_, 11, snaps={}, forward_first=fwd)
check("T17 前瞻月沒有快照 → missing_snapshot、不用現在的 panel 回填", m_ns[fwd]["status"] == "missing_snapshot" and not S.month_valid(m_ns[fwd]))
root = tempfile.mkdtemp(); d = os.path.join(root, fwd); os.makedirs(d)
buf = ["market,code,ym,rev"] + ["sii,%s,%s,%r" % (c, y, v) for c, mm_ in rev.items() for y, v in mm_.items()]
data = ("\n".join(buf) + "\n").encode()
open(os.path.join(d, "panel.csv"), "wb").write(data)
nm = S.ym_add(fwd, 1)
_j.dump(dict(revenue_month=fwd, cutoff_at="%s-%s-11T11:30:00Z" % (nm[:4], nm[4:]), panel_sha256=_h.sha256(data).hexdigest()),
        open(os.path.join(d, "meta.json"), "w"))
snaps = S.load_snapshots(root)
rev_changed = {c: dict(v) for c, v in rev.items()}
for c in rev_changed:                                   # 快照之後營收被更正（大幅改當月）
    rev_changed[c][fwd] = rev_changed[c][fwd] * (3 if int(c) % 2 else 0.3)
a = S.run_version(px, rev_changed, days_, 11, snaps=snaps, forward_first=fwd)[fwd]
b = S.run_version(px, rev, days_, 11)[fwd]
check("T18 前瞻月用快照、不用之後的更正（結果＝快照當時的營收）", a["E"] == b["E"] and a["by_h"][20]["raw_q5"] == b["by_h"][20]["raw_q5"], (a["E"], b["E"]))
open(os.path.join(d, "panel.csv"), "ab").write(b"x")
check("T19 快照雜湊對不上 → 不收", fwd not in S.load_snapshots(root))

# T15 報告可產生
check("T15 報告產生", "前瞻（判定）" in S.report(res))

print("\n%d 項失敗" % len(FAILS) if FAILS else "\n全部通過")
sys.exit(1 if FAILS else 0)
