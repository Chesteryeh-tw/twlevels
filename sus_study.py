#!/usr/bin/env python3
"""SUS（標準化未預期營收）研究程式。照 preregistration/SUS_事先登記_v2.4.md（SHA-256 8ffa6f31…78d8）實作，不加規則。

這支程式連同 test_sus_study.py 在第一次讀真 SUS 結果之前先 commit、記錄雜湊（SUS_程式凍結紀錄）。凍結後任何改動＝新版本登記。

定義（登記第 1 節）  S_t＝單月營收（千元）；ΔS_t＝S_t−S_{t−12}；μ、σ＝前 24 個 ΔS（不含當月，ddof＝1）；SUS＝(ΔS_t−μ)/σ。
                      需要 S_{t−36}…S_t 共 37 個連續月，缺一個、σ＝0、非有限 → 該公司該月不算。
母體（第 2 節）      進場前一交易日成交金額 ≥ 1 億（萬元欄 ≥ 10000）；先套母體，再在母體內算 SUS 合格與分組。
分組                平均名次 p＝名次/家數，組別＝⌈5p⌉；合格 < 50 家或 Q1、Q5 任一 < 10 家 → 該進場日不算。主要格＝Q5 做多。
進場                11 日版＝M+1 月 11 日 19:30 之後的第一個市場開盤（日期 > 11 日）；16 日版＝日期 ≥ 16 日的第一個市場開盤。
持有                進場當日算第 1 個交易日，h 日終點＝第 h 個市場交易日收盤（索引 entry+h−1）；20 日進判定，5、60 日只當參考。
統計                月超額＝進場日內 Q5 平均報酬 − 母體平均（先公司等權，再每個營收月一筆）；淨＝事件等權平均報酬 − 0.386%。
                    Newey-West（lag 1 為主，0、2 敏感度；60 日參考 lag 2）：SE²＝[Σu²+2Σ_l(1−l/(L+1))Σu_t·u_{t−l}]/[n(n−1)]，
                    相隔 l「曆月」配對，缺月不壓縮；n < 12 或 SE² ≤ 0 不報 t。
判定                只用兩版「共同有效」的營收月；兩版都 超額>0、t≥2、淨>0 → 通過；只有一版 → 未共同通過；都沒有 → 測不出來；
                    共同有效月 < 12 → 資料不足。前瞻月（營收月 ≥ FORWARD_FIRST_YM）才判定；歷史月一律標「探索」。
公司行動            data/corporate_actions.csv 存在就用（見 load_actions）；沒有就用「持有期內開盤跳超過 11%」暫代，結果標「暫代」。
"""
import bisect
import datetime as dt
import csv
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

PROGRAM = "sus_study v1"
REGISTRATION = "preregistration/SUS_事先登記_v2.4.md"
REGISTRATION_SHA256 = "8ffa6f3121ff31733c6f2f7685fa9beb14e64ce71311cdad4d832db40d3a78d8"
FORWARD_FIRST_YM = "202610"          # 第一個前瞻營收月（登記凍結紀錄）
COST = 0.386
HS = (5, 20, 60)
MAIN_H = 20
MIN_WAN = 10000                      # 成交金額欄是「萬元」：10000 萬 = 1 億
MIN_QUALIFIED, MIN_TAIL, MIN_MONTHS = 50, 10, 12
GAP_PROXY = 11.0                     # 暫代旗標：開盤比前一天收盤差超過 11%
NW_LAG = {5: 1, 20: 1, 60: 2}


# ---------- 小工具 ----------
def ym_add(ym, k):
    y, m = int(ym[:4]), int(ym[4:])
    t = y * 12 + (m - 1) + k
    return "%d%02d" % (t // 12, t % 12 + 1)


def ym_index(ym):
    return int(ym[:4]) * 12 + int(ym[4:]) - 1


def entry_date(ym, version, days, after_local=None):
    """營收月 ym 的進場日（市場開盤日）。11 日版：日期 > M+1 月 11 日（19:30 之後）；16 日版：日期 ≥ 16 日。
    after_local（台北時間 YYYYMMDDHHMM，前瞻月＝快照的 cutoff_at）：開盤（09:00）要嚴格晚於它，延誤就順延。"""
    nm = ym_add(ym, 1)
    cut = "%s%02d" % (nm, 11 if version == 11 else 16)
    i = bisect.bisect_right(days, cut) if version == 11 else bisect.bisect_left(days, cut)
    while after_local and i < len(days) and days[i] + "0900" <= after_local:
        i += 1
    return days[i] if i < len(days) else None


def sus_series(rev, code, ym):
    """回傳 (SUS, 說明)；任一條件不成立回 (None, 原因)。rev: code -> ym -> 千元營收。"""
    r = rev.get(code)
    if r is None:
        return None, "no_revenue"
    need = [ym_add(ym, -k) for k in range(0, 37)]
    vals = {}
    for m in need:
        v = r.get(m)
        if v is None or not math.isfinite(v):
            return None, "missing_month"
        vals[m] = v
    d = {}
    for k in range(0, 25):                       # ΔS_t … ΔS_{t−24}
        m = ym_add(ym, -k)
        d[k] = vals[m] - vals[ym_add(m, -12)]
    prev = [d[k] for k in range(1, 25)]
    sd = st.stdev(prev)
    if not math.isfinite(sd) or sd <= 0:
        return None, "sigma_zero"
    z = (d[0] - st.mean(prev)) / sd
    return (z, "ok") if math.isfinite(z) else (None, "not_finite")


def quintiles(items):
    """items: [(code, sus)] → {code: 組別 1..5}。平均名次，p＝名次/家數，組別＝⌈5p⌉（用整數避免浮點）。"""
    n = len(items)
    order = sorted(items, key=lambda x: x[1])
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        r2 = (i + 1) + (j + 1)                   # 平均名次的 2 倍
        g = -((-5 * r2) // (2 * n))              # ⌈5·r2/(2n)⌉
        for k in range(i, j + 1):
            out[order[k][0]] = min(5, max(1, g))
        i = j + 1
    return out


def nw_t(series, lag):
    """series: {營收月(YYYYMM): x}。登記第 5 節公式；缺月不壓縮。n<12 或 SE²≤0 → None。"""
    n = len(series)
    if n < MIN_MONTHS:
        return None
    xb = sum(series.values()) / n
    u = {ym_index(k): v - xb for k, v in series.items()}
    s = sum(e * e for e in u.values())
    for l in range(1, lag + 1):
        w = 1 - l / (lag + 1)
        s += 2 * w * sum(e * u[i - l] for i, e in u.items() if (i - l) in u)
    se2 = s / (n * (n - 1))
    if not (se2 > 0 and math.isfinite(se2)):
        return None
    return xb / math.sqrt(se2)


# ---------- 公司行動 ----------
def load_actions(path):
    """data/corporate_actions.csv：code,effective_date(YYYYMMDD),type,announce_at(可空，ISO 或 YYYYMMDD),cash_only(0/1)。
    回傳 code -> [(effective_date, announce_at 或 None, cash_only)]；沒有檔回 None（改用暫代旗標）。"""
    if not os.path.exists(path):
        return None
    out = defaultdict(list)
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        out[r["code"].strip()].append((r["effective_date"].strip(), (r.get("announce_at") or "").strip() or None,
                                       str(r.get("cash_only", "0")).strip() in ("1", "true", "True")))
    return out


def action_status(code, entry, exit_, decision_ymd, actions, rows_gap):
    """回傳 None（無影響）、'skip_known'（決策前已公告，跳過）、'announce_unknown'（無公告時間，事後資料品質排除）、
    'unresolved'（決策後才公告），或 'proxy'（暫代旗標）。純除息（cash_only）不排除。"""
    if actions is None:
        return "proxy" if rows_gap else None
    hit = [(e, a, c) for (e, a, c) in actions.get(code, []) if entry < e <= exit_ and not c]
    if not hit:
        return None
    worst = None
    for _e, a, _c in hit:
        if a is None:
            s = "announce_unknown"
        elif a[:8] <= decision_ymd:
            s = "skip_known"
        else:
            s = "unresolved"
        if s == "unresolved" or worst is None or (worst != "unresolved" and s == "announce_unknown"):
            worst = s
    return worst


# ---------- 前瞻：共同營收快照 ----------
def load_snapshots(root):
    """data/revenue_raw/snapshots/<營收月>/{panel.csv, meta.json}（revenue_fetch.save_snapshot 寫的）。
    回傳 ym -> (rev, cutoff 台北時間 YYYYMMDDHHMM)；雜湊對不上的不收（該月前瞻就是缺資料）。"""
    import hashlib
    out = {}
    if not os.path.isdir(root):
        return out
    for ym in sorted(os.listdir(root)):
        mp, pp = os.path.join(root, ym, "meta.json"), os.path.join(root, ym, "panel.csv")
        try:
            meta = json.load(open(mp, encoding="utf-8"))
            if hashlib.sha256(open(pp, "rb").read()).hexdigest() != meta["panel_sha256"]:
                print("快照 %s 雜湊對不上，不用" % ym)
                continue
            utc = dt.datetime.strptime(meta["cutoff_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            local = utc.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d%H%M")
        except (OSError, ValueError, KeyError) as e:
            print("快照 %s 讀不了（%s），不用" % (ym, e))
            continue
        out[ym] = (load_rev(pp), local)
    return out


# ---------- 核心 ----------
def build_calendar(px):
    cnt = defaultdict(int)
    for rows in px.values():
        for r in rows:
            cnt[r[0]] += 1
    return sorted(d for d, c in cnt.items() if c > len(px) * 0.5)


def run_version(px, rev, days, version, actions=None, sens_trim=False, snaps=None, forward_first=FORWARD_FIRST_YM):
    """px: code -> [(date, open, close, wan)] 依日期排序。回傳 months: ym -> 記錄。
    前瞻月（ym ≥ forward_first）只用該月的共同營收快照（snaps），進場要晚於快照 cutoff；沒有快照 → 缺資料，不用現在的 panel 回填。"""
    didx = {d: i for i, d in enumerate(days)}
    pos = {c: {r[0]: i for i, r in enumerate(rows)} for c, rows in px.items()}
    dts = {c: [r[0] for r in rows] for c, rows in px.items()}
    yms = sorted({ym for c in rev for ym in rev[c]})
    months = {}
    for ym in yms:
        rv, after = rev, None
        if ym >= forward_first and snaps is not None:
            if ym not in snaps:
                months[ym] = dict(E=None, S=None, status="missing_snapshot", untradable=0, delisting=0, missing_exit=0,
                                  ca={}, n_qual=0, by_h={})
                continue
            rv, after = snaps[ym]
        E = entry_date(ym, version, days, after)
        if E is None:
            continue
        ei = didx[E]
        if ei == 0:
            continue
        S = days[ei - 1]
        # 決策＝進場前一交易日的價量都已完成（歷史為假設可得）；11 日版的名義資料截止是 M+1 月 11 日 19:30，取兩者較晚
        decision_ymd = max(S, "%s11" % ym_add(ym, 1)) if version == 11 else S
        rec = dict(E=E, S=S, status="ok", untradable=0, delisting=0, missing_exit=0, ca={}, n_qual=0, by_h={})
        cand = []
        for c, rows in px.items():
            p = pos[c]
            if S not in p:
                continue
            if rows[p[S]][3] < MIN_WAN:
                continue
            cand.append(c)
        sus, why = {}, defaultdict(int)
        for c in cand:
            z, w_ = sus_series(rv, c, ym)
            why[w_] += 1
            if z is not None:
                sus[c] = z
        # 登記第 9 節：每月報母體家數與 SUS 不能算的原因。當月沒有營收的原因（合法延後／其他未公告／來源缺漏）
        # 程式分不出來，一律記「原因未分類」，不猜成合法延後。
        rec["n_cand"] = len(cand)
        rec["why"] = dict(why)
        rec["no_current_month"] = sum(1 for c in cand if rv.get(c, {}).get(ym) is None)
        trade = {}
        for c in cand:
            rows, p = px[c], pos[c]
            if E not in p or rows[p[E]][1] <= 0:
                rec["untradable"] += 1          # 進場日停牌或無有效開盤：不進績效，另報筆數
                continue
            i = p[E]
            fw = {}
            for h in HS:
                mi = ei + h - 1                 # 市場日曆上的第 h 個交易日
                if mi >= len(days):
                    continue                    # 還沒成熟
                tgt = days[mi]
                if tgt in p:
                    j = p[tgt]
                else:                           # 出場日停牌 → 順延到第一個有成交的收盤
                    j = bisect.bisect_left(dts[c], tgt, i)
                    if j >= len(rows):          # 該股在市場日曆上的這天之後再沒有成交：未解下市（最後成交收盤不當已實現出場）
                        if h == MAIN_H:
                            rec["delisting"] += 1
                        continue
                if rows[j][2] <= 0:
                    if h == MAIN_H:
                        rec["missing_exit"] += 1
                    continue
                gap = any(abs(rows[q][1] / rows[q - 1][2] * 100 - 100) > GAP_PROXY
                          for q in range(i + 1, j + 1) if rows[q - 1][2] > 0)
                stt = action_status(c, E, rows[j][0], decision_ymd, actions, gap)
                if stt:
                    if h == MAIN_H:
                        rec["ca"][stt] = rec["ca"].get(stt, 0) + 1
                    continue
                fw[h] = (rows[j][2] / rows[i][1] - 1) * 100
            trade[c] = fw
        pool = {c: z for c, z in sus.items() if c in trade}
        if sens_trim:
            pool = {c: z for c, z in pool.items() if abs(z) <= 10}
        rec["n_qual"] = len(pool)
        g = quintiles(list(pool.items())) if pool else {}
        n1 = sum(1 for v in g.values() if v == 1)
        n5 = sum(1 for v in g.values() if v == 5)
        if len(pool) < MIN_QUALIFIED or n1 < MIN_TAIL or n5 < MIN_TAIL:
            rec["status"] = "too_few"
            months[ym] = rec
            continue
        for h in HS:
            uni = [fw[h] for fw in trade.values() if h in fw]
            q5 = [trade[c][h] for c, v in g.items() if v == 5 and h in trade[c]]
            q1 = [trade[c][h] for c, v in g.items() if v == 1 and h in trade[c]]
            qual = [trade[c][h] for c in pool if h in trade[c]]
            if not uni or not q5 or not q1:
                continue
            rec["by_h"][h] = dict(uni=st.mean(uni), q5=st.mean(q5), q1=st.mean(q1), qual=st.mean(qual),
                                  n_q5=len(q5), raw_q5=q5)
        months[ym] = rec
    return months


def month_unresolved(rec):
    return bool(rec["delisting"] or rec["missing_exit"] or rec["ca"].get("unresolved"))


def month_valid(rec, h=MAIN_H):
    """該月可進判定：資料成熟、分組成立、沒有未解下市／缺價／決策後才公告的公司行動。"""
    return rec["status"] == "ok" and h in rec["by_h"] and not month_unresolved(rec)


def summarize(months, yms, h=MAIN_H, lag=None, ref="uni"):
    xs = {ym: months[ym]["by_h"][h]["q5"] - months[ym]["by_h"][h][ref] for ym in yms}
    raw = [r for ym in yms for r in months[ym]["by_h"][h]["raw_q5"]]
    if not xs:
        return None
    lag = NW_LAG[h] if lag is None else lag
    return dict(months=len(xs), events=len(raw), excess=sum(xs.values()) / len(xs), t=nw_t(xs, lag),
                raw=st.mean(raw), net=st.mean(raw) - COST)


def judge(m11, m16, yms_common, h=MAIN_H):
    """回傳 (狀態, s11, s16)。yms_common 已是兩版共同有效、且屬同一個區段的營收月。"""
    if len(yms_common) < MIN_MONTHS:
        return "資料不足", None, None
    s11, s16 = summarize(m11, yms_common, h), summarize(m16, yms_common, h)

    def ok(s):
        return s["excess"] > 0 and s["t"] is not None and s["t"] >= 2 and s["net"] > 0
    a, b = ok(s11), ok(s16)
    return ("通過" if a and b else "未共同通過" if a or b else "測不出來"), s11, s16


def study(px, rev, actions=None, forward_first=FORWARD_FIRST_YM, snaps=None):
    """snaps＝None 只用於合成測試；真資料一律傳 load_snapshots 的結果（前瞻月沒有快照就不算）。"""
    days = build_calendar(px)
    out = dict(program=PROGRAM, registration_sha256=REGISTRATION_SHA256, pit_verified=False,
               ca_source="corporate_actions" if actions is not None else "暫代（開盤跳超過 11%）", segments={})
    kw = dict(snaps=snaps, forward_first=forward_first)
    m = {v: run_version(px, rev, days, v, actions, **kw) for v in (11, 16)}
    mt = {v: run_version(px, rev, days, v, actions, sens_trim=True, **kw) for v in (11, 16)}
    allm = sorted(set(m[11]) & set(m[16]))
    common = [ym for ym in allm if month_valid(m[11][ym]) and month_valid(m[16][ym])]
    for seg, sel in (("探索（歷史月，不判定通過）", [y for y in common if y < forward_first]),
                     ("前瞻（判定）", [y for y in common if y >= forward_first])):
        res = dict(common_months=len(sel), first=sel[0] if sel else None, last=sel[-1] if sel else None)
        status, s11, s16 = judge(m[11], m[16], sel)
        res.update(status=status if seg.startswith("前瞻") else "探索", v11=s11, v16=s16, ref={}, sens={})
        for v in (11, 16):
            mm = m[v]
            if sel:
                res["ref"]["v%d_vs_qualified" % v] = summarize(mm, sel, ref="qual")
                for h in HS:
                    if all(h in mm[y]["by_h"] for y in sel):
                        res["ref"]["v%d_h%d" % (v, h)] = summarize(mm, sel, h)
                for L in (0, 2):
                    res["sens"]["v%d_nw%d" % (v, L)] = summarize(mm, sel, lag=L)
                tr = [y for y in sel if month_valid(mt[v][y])]
                res["sens"]["v%d_trim10" % v] = summarize(mt[v], tr) if tr else None
                res["ref"]["v%d_q5_minus_q1" % v] = (lambda xs: dict(months=len(xs), diff=sum(xs) / len(xs))
                                                     )([mm[y]["by_h"][MAIN_H]["q5"] - mm[y]["by_h"][MAIN_H]["q1"] for y in sel])
        out["segments"][seg] = res
    out["months_by_version"] = {
        "v%d" % v: {ym: dict(E=r["E"], status=r["status"], n_qual=r["n_qual"], n_cand=r.get("n_cand"),
                            why=r.get("why"), no_current_month_unclassified=r.get("no_current_month"),
                            untradable=r["untradable"],
                            delisting=r["delisting"], missing_exit=r["missing_exit"], ca=r["ca"],
                            valid=month_valid(r)) for ym, r in m[v].items()} for v in (11, 16)}
    out["excluded_common"] = [ym for ym in allm if ym not in common]
    return out


# ---------- 讀資料 ----------
def load_prices(root=os.path.join("data", "stock")):
    px = {}
    import glob
    for f in glob.glob(os.path.join(root, "*.txt")):
        rows = []
        for l in open(f, encoding="utf-8"):
            if l[0] == "#":
                continue
            p = l.strip().split(",")
            try:
                rows.append((p[0], float(p[1]), float(p[4]), float(p[6] or 0)))
            except (ValueError, IndexError):
                pass
        if len(rows) > 30:
            px[os.path.basename(f)[:-4]] = sorted(rows)
    return px


def load_rev(panel_path):
    rev = defaultdict(dict)
    for r in csv.DictReader(open(panel_path, encoding="utf-8")):
        try:
            rev[r["code"]][r["ym"]] = float(r["rev"])
        except (ValueError, KeyError):
            continue
    return rev


def fmt(s):
    if not s:
        return "—"
    if "diff" in s:
        return "%d 個月、Q5−Q1 差 %+.2f%%" % (s["months"], s["diff"])
    return "%d 個營收月／%d 筆、超額 %+.2f%%（NW t %s）、事件等權原始 %+.2f%%、淨 %+.2f%%" % (
        s["months"], s["events"], s["excess"], "—" if s["t"] is None else "%+.2f" % s["t"], s["raw"], s["net"])


def report(res):
    L = ["# SUS 研究報告（%s；登記 v2.4 SHA-256 %s…）" % (res["program"], res["registration_sha256"][:8]), "",
         "公司行動口徑：**%s**。資料是現在查得到的版本（pit_verified＝false）。歷史月一律「探索」，只有前瞻月（營收月 ≥ %s）判定。" % (
             res["ca_source"], FORWARD_FIRST_YM)]
    for seg, r in res["segments"].items():
        L += ["", "## %s —— %s" % (seg, r["status"]), "- 共同有效營收月 %d 個（%s～%s）" % (r["common_months"], r["first"], r["last"])]
        for v, k in ((11, "v11"), (16, "v16")):
            L.append("- %d 日版 20 日：%s" % (v, fmt(r[k])))
        for k, s in r["ref"].items():
            L.append("- 參考 %s：%s" % (k, fmt(s)))
        for k, s in r["sens"].items():
            L.append("- 敏感度 %s：%s" % (k, fmt(s)))
    ex = res["excluded_common"]
    L += ["", "兩版共同有效以外的營收月 %d 個（資料不成熟、分組不成立，或有未解下市／缺價／決策後才公告的公司行動）。" % len(ex)]
    for v, ms in res["months_by_version"].items():
        un = sum(r["untradable"] for r in ms.values())
        dl = sum(r["delisting"] for r in ms.values())
        me = sum(r["missing_exit"] for r in ms.values())
        ca = defaultdict(int)
        for r in ms.values():
            for k, n in r["ca"].items():
                ca[k] += n
        L.append("- %s：不可成交 %d 筆、未解下市 %d 筆、缺價 %d 筆、公司行動 %s" % (v, un, dl, me, dict(ca)))
    L += ["", "## 各營收月覆蓋（11 日版）", "| 營收月 | 進場 | 狀態 | 母體 | SUS 合格 | 當月無營收（原因未分類）|", "|---|---|---|---|---|---|"]
    for ym, r in sorted(res["months_by_version"]["v11"].items()):
        L.append("| %s | %s | %s | %s | %s | %s |" % (ym, r["E"], r["status"], r.get("n_cand"), r["n_qual"],
                                                     r.get("no_current_month_unclassified")))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    import revenue_panel
    pp, _mp, how = revenue_panel.resolve(os.path.join("data", "revenue"))
    if not pp:
        print("沒有營收 panel，先跑 revenue_fetch.py"); sys.exit(1)
    print("營收 panel 來源：%s" % how)
    px = load_prices()
    res = study(px, load_rev(pp), load_actions(os.path.join("data", "corporate_actions.csv")),
                snaps=load_snapshots(os.path.join("data", "revenue_raw", "snapshots")))
    json.dump(res, open("sus_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open("sus_report.md", "w", encoding="utf-8").write(report(res))
    print(report(res))
