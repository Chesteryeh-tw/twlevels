#!/usr/bin/env python3
"""SUS（標準化未預期營收）研究程式 v4。照 preregistration/SUS_事先登記_v2.5.md（SHA-256 5b9f1cf5…6e14）實作，不加研究規則。

v4（GPT 09）：前瞻月當時母體的代碼缺整份價量、缺進場日或預定出場日價格列 → 未解資料（unresolved_data），月不進判定；
只有價格列存在且成交量 0（官方零成交證據）才算不可成交。日曆新鮮度改看必要來源成功核驗時間（sources_verified_at），
不看重建時間；決策紀錄另存日曆來源核驗時間。登記改用 v2.5（第 10、11 節）。

v1（commit 4904407）、v2（efdeb3d）經 GPT 2026-10-25 複核（06、08）不通過實作驗收；v3 修 08 的 P1～P6，凍結紀錄另記
（SUS_程式凍結紀錄_v3）。公式、成本、門檻、判定都沒有改。
v3 實作口徑（08）：
  P1 決策紀錄的 decision_at＝分組算完之後重新讀的時鐘；算完已過開盤 → 只寫嘗試紀錄（attempts/），不寫正式紀錄。
     正式紀錄保存當時完整母體（代號＋基準日成交額）、SUS 值、分組、母體價量列雜湊；前瞻一律用紀錄裡的母體與名單，
     後來價量修訂只另報差異。讀取端再驗：decision_at ≥ 快照 cutoff、算完 ≤ decision_at < 實際進場開盤、
     價量基準日＝實際進場前一交易日、登記雜湊相符。紀錄可以有多份（臨時休市等），用最早一份實際可執行的。
  P2 歷史月只用 announce_at（今天才抓到的 first_seen 不能當當年公告時間）；前瞻月用 announce_at 或 first_seen_at（當時保存的抓取時間）。
  P3 正式快照：cutoff 在 M+1 月 11 日 19:30～12 日 09:00（台北）之間、meta.formal＝true、repo 持久 meta 必須存在且整份相同。
  P4 市場日曆：前瞻決策只用官方日曆（年度休市表＋已核實的臨時休市公告）；日曆缺、超過 36 小時沒更新、下一個市場日的年度不在休市表 → 不寫決策。
  P5 事件表：coverage.json 記事件表與 first_seen 的雜湊（最後寫、當發布點），讀取端雜湊不符＝事件表不可用；
     前瞻另要求每個來源月的抓取時間晚於 min(出場日, 該月月底)。
  P6 前瞻 trim10 用紀錄的 SUS 值排除 |SUS|>10 後重新分組；價量檔短列、重複日期、非正價格記 QC 並當資料錯誤。
第一次讀真 SUS 結果之前先凍結。凍結後任何改動＝新版本登記。

定義（第 1 節）  S_t＝單月營收（千元）；ΔS_t＝S_t−S_{t−12}；μ、σ＝前 24 個 ΔS（不含當月，ddof＝1）；SUS＝(ΔS_t−μ)/σ；
                  需要 S_{t−36}…S_t 共 37 個連續月，缺一個、σ＝0、非有限 → 該公司該月不算。
母體（第 2 節）  進場前一交易日成交金額 ≥ 1 億（萬元欄 ≥ 10000）；先套母體，再在母體內算 SUS 合格與分組。
                  與 R1～R5 相同：data/stock 裡所有代號都算（含 ETF 等），沒有營收的只進對照組平均、不進 Q1～Q5。
分組            平均名次 p＝名次/家數，組別＝⌈5p⌉；合格 < 50 家或 Q1、Q5 任一 < 10 家 → 該進場日不算。主要格＝Q5 做多。
時間（A1）      歷史月：名義 decision_at＝11 日版 max(M+1 月 11 日 19:30, 進場前一交易日 23:59:59)、16 日版＝進場前一交易日 23:59:59
                  （台北時間）；進場＝開盤（09:00）嚴格晚於 decision_at 的第一個市場日，且 11 日版日期 > 11 日、16 日版 ≥ 16 日。
                  前瞻月：一律用 decide() 當時寫下的決策紀錄（實際 decision_at、價量基準日、使用的快照雜湊、當時的分組名單）；
                  沒有紀錄＝缺資料。實際進場日前一交易日 ≠ 紀錄的價量基準日 → 決策過期，該月不進判定。
持有            進場當日算第 1 個交易日，h 日終點＝第 h 個市場交易日收盤（entry+h−1）；出場日停牌順延到下一個有成交的收盤；
                  之後再沒有成交＝無法驗證出場（no_exit，保留最後有效價格日），該月不進判定。20 日判定，5、60 日參考。
可成交          進場日要有有限且 > 0 的開盤價（開盤價本身就是有成交的證據）；沒有＝不可成交，不進績效，另報。
公司行動（A2）  非現金事件生效日落在 (進場, 實際出場]：
                  - 事前已知（可靠公告時間或首次看到時間 ≤ decision_at）→ 跳過該筆（skip_known），該月仍有效。
                  - 決策後才知道 → 未解（unresolved），保留原筆與對照組，該月不進判定。
                  - 沒有任何時間：歷史月＝事後資料品質排除（announce_unknown，刪該筆，月仍有效）；前瞻月＝未解。
                  - 事件表沒涵蓋該持有期的月份：歷史月改用 11% 開盤跳動暫代（標「暫代」）；前瞻月＝未解（不能用暫代判定）。
                  純除息不排除；另報「排除持有期內有除息」的敏感度。
時間解析（A3）  公告／首次看到時間：帶時區的 ISO 照原時區；只有日期（YYYYMMDD）＝該日 23:59:59 台北（保守）；
                  沒有時區的 ISO、無效格式＝視為沒有時間（不猜）。
資料錯誤（A4）  用到的價格或成交額非有限（nan、inf）或報酬非有限 → data_error，該月不進判定、保留原因。
統計            月超額＝進場日內 Q5 平均報酬 − 母體平均；淨＝事件等權平均報酬 − 0.386%；Newey-West（20 日 lag 1，0、2 敏感度；
                  60 日 lag 2）：SE²＝[Σu²+2Σ_l(1−l/(L+1))Σu_t·u_{t−l}]/[n(n−1)]，相隔 l 曆月配對，缺月不壓縮；n<12 或 SE²≤0 不報 t。
判定            只用兩版共同有效的營收月。正式檢視只在前瞻共同有效月累積到 12、24、36 個時，用最前面的 N 個月各做一次；
                  兩版都 超額>0、t≥2、淨>0 → 通過；只有一版 → 未共同通過；都沒有 → 測不出來；不足 12 → 資料不足。
                  其他時候只是「持續追蹤」，不算額外的正式檢定。歷史月一律「探索」。
"""
import bisect
import csv
import datetime as dt
import glob
import hashlib
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

PROGRAM = "sus_study v4"
REGISTRATION = "preregistration/SUS_事先登記_v2.5.md"
REGISTRATION_SHA256 = "5b9f1cf5b131d376b1aa0faa364b7c34c0c6f20c7692a8bce72ea03d42af6e14"
FORWARD_FIRST_YM = "202610"
COST = 0.386
HS = (5, 20, 60)
MAIN_H = 20
MIN_WAN = 10000
MIN_QUALIFIED, MIN_TAIL, MIN_MONTHS = 50, 10, 12
CHECKPOINTS = (12, 24, 36)
GAP_PROXY = 11.0
NW_LAG = {5: 1, 20: 1, 60: 2}
TPE = dt.timezone(dt.timedelta(hours=8))
DECISIONS = os.path.join("data", "sus_decisions")
CAL_MAX_AGE_H = 36
CAL_REQUIRED = ("FMTQIK_current", "holidaySchedule")
PRICE_QC = defaultdict(set)      # code -> 有問題的日期（短列、重複日期）；load_prices 填


# ---------- 小工具 ----------
def ym_add(ym, k):
    y, m = int(ym[:4]), int(ym[4:])
    t = y * 12 + (m - 1) + k
    return "%d%02d" % (t // 12, t % 12 + 1)


def ym_index(ym):
    return int(ym[:4]) * 12 + int(ym[4:]) - 1


def fin(*xs):
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def at_tpe(ymd, hh=23, mm=59, ss=59):
    return dt.datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]), hh, mm, ss, tzinfo=TPE)


def open_at(ymd):
    return at_tpe(ymd, 9, 0, 0)


def parse_time(s):
    """A3：帶時區 ISO → 該時刻；YYYYMMDD → 該日 23:59:59 台北；其餘（含沒時區的 ISO）→ None。"""
    s = (s or "").strip()
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        try:
            return at_tpe(s)
        except ValueError:
            return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def nominal_first(ym, version):
    """名義上最早可進場的日期下限（字串比較用）：11 日版 > 11 日；16 日版 ≥ 16 日。"""
    nm = ym_add(ym, 1)
    return ("%s11" % nm, False) if version == 11 else ("%s16" % nm, True)


def entry_after(days, lower, inclusive, decision_at):
    """第一個 開盤時間 > decision_at、且日期 > lower（inclusive 時 ≥）的市場日。"""
    i = bisect.bisect_left(days, lower) if inclusive else bisect.bisect_right(days, lower)
    while i < len(days) and open_at(days[i]) <= decision_at:
        i += 1
    return days[i] if i < len(days) else None


def historical_entry(ym, version, days):
    """歷史月：名義 decision_at 與進場日。回傳 (decision_at, S, E)；資料不夠回 None。"""
    lower, inc = nominal_first(ym, version)
    i = bisect.bisect_left(days, lower) if inc else bisect.bisect_right(days, lower)
    if i >= len(days) or i == 0:
        return None
    S = days[i - 1]
    dec = at_tpe(S)
    if version == 11:
        dec = max(dec, at_tpe("%s11" % ym_add(ym, 1), 19, 30, 0))
    E = entry_after(days, lower, inc, dec)
    if E is None:
        return None
    S = days[days.index(E) - 1]
    return dec, S, E


def sus_series(rev, code, ym):
    r = rev.get(code)
    if r is None:
        return None, "no_revenue"
    vals = {}
    for k in range(0, 37):
        m = ym_add(ym, -k)
        v = r.get(m)
        if v is None or not fin(v):
            return None, ("no_current_month" if k == 0 else "missing_history_month")
        vals[m] = v
    d = {k: vals[ym_add(ym, -k)] - vals[ym_add(ym, -k - 12)] for k in range(0, 25)}
    prev = [d[k] for k in range(1, 25)]
    sd = st.stdev(prev)
    if not fin(sd) or sd <= 0:
        return None, "sigma_zero"
    z = (d[0] - st.mean(prev)) / sd
    return (z, "ok") if fin(z) else (None, "not_finite")


def quintiles(items):
    n = len(items)
    order = sorted(items, key=lambda x: x[1])
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        r2 = (i + 1) + (j + 1)
        g = -((-5 * r2) // (2 * n))
        for k in range(i, j + 1):
            out[order[k][0]] = min(5, max(1, g))
        i = j + 1
    return out


def nw(series, lag):
    """回傳 (t, se)；n<12 或 SE²≤0 → (None, None)。"""
    n = len(series)
    if n < MIN_MONTHS:
        return None, None
    xb = sum(series.values()) / n
    u = {ym_index(k): v - xb for k, v in series.items()}
    s = sum(e * e for e in u.values())
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * sum(e * u[i - l] for i, e in u.items() if (i - l) in u)
    se2 = s / (n * (n - 1))
    if not (se2 > 0 and math.isfinite(se2)):
        return None, None
    se = math.sqrt(se2)
    return xb / se, se


def nw_t(series, lag):
    return nw(series, lag)[0]


# ---------- 公司行動 ----------
def _sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def load_actions(path, coverage_path=None, first_seen_path=None):
    """data/corporate_actions.csv 與 data/corporate_actions_coverage.json（發布點，最後寫）。
    coverage 記 csv_sha256、first_seen_sha256、months（七個來源都驗過的月份）、verified（來源_月份 → 抓取時間）。
    雜湊對不上（發布中斷、新舊混用）→ ok＝False（前瞻全部未解、歷史改用暫代）。沒有事件表回 None。
    事件：ann＝官方公告時間（目前官方結果表沒有，空白）、seen＝該內容版本第一次被我們抓到的時間（P2：兩者分開存）。"""
    if not os.path.exists(path):
        return None
    cov, ok, why = {}, True, ""
    if coverage_path and os.path.exists(coverage_path):
        cov = json.load(open(coverage_path, encoding="utf-8"))
        if cov.get("csv_sha256") and cov["csv_sha256"] != _sha_file(path):
            ok, why = False, "事件表雜湊跟 coverage 不同（發布中斷或新舊混用）"
        fsp = first_seen_path or os.path.join(os.path.dirname(path), "corporate_actions_first_seen.json")
        if ok and cov.get("first_seen_sha256") and (not os.path.exists(fsp) or cov["first_seen_sha256"] != _sha_file(fsp)):
            ok, why = False, "first_seen 登錄檔雜湊跟 coverage 不同"
    by = defaultdict(list)
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        by[r["code"].strip()].append(dict(eff=r["effective_date"].strip(), ann=parse_time(r.get("announce_at")),
                                          seen=parse_time(r.get("first_seen_at")),
                                          cash=str(r.get("cash_only", "0")).strip() in ("1", "true", "True"),
                                          type=r.get("type", ""), id=r.get("event_id") or "%s|%s|%s" % (
                                              r.get("source", ""), r["code"].strip(), r["effective_date"].strip())))
    ver = {k: parse_time(v) for k, v in (cov.get("verified") or {}).items()}
    return dict(by_code=by, covered=set(cov.get("months", [])), verified=ver or None, sources=cov.get("sources", []),
                ok=ok, why=why)


def months_between(a, b):
    out, y = [], a[:6]
    while y <= b[:6]:
        out.append(y)
        y = ym_add(y, 1)
    return out


def month_end(ym):
    return (dt.date(int(ym[:4]), int(ym[4:]), 1) + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)


def covered_through(actions, entry, exit_date, forward):
    """事件表是否涵蓋 (entry, exit] 的每個月份。前瞻（P5b）另要求每個來源月的抓取時間（台北日期）晚於 min(出場日, 月底)。"""
    if actions is None or not actions.get("ok", True):
        return False
    for m in months_between(entry, exit_date):
        if m not in actions["covered"]:
            return False
        if forward:
            ver = actions.get("verified")
            if not ver or not actions.get("sources"):
                return False
            need = min(exit_date, month_end(m).strftime("%Y%m%d"))
            for src in actions["sources"]:
                t = ver.get("%s_%s" % (src, m))
                if t is None or t.astimezone(TPE).strftime("%Y%m%d") <= need:
                    return False
    return True


def known_time(e, forward):
    """P2：歷史月只信 announce_at；前瞻月 announce_at 或 first_seen_at（當時保存的抓取時間）取早的。
    舊格式（known_at）只給測試相容用。"""
    if "known_at" in e:
        return e["known_at"]
    t = [x for x in ((e.get("ann"), e.get("seen")) if forward else (e.get("ann"),)) if x is not None]
    return min(t) if t else None


def classify_actions(code, entry, exit_date, decision_at, actions, forward, gap):
    """回傳 (狀態, 事件 ID 清單, 是否持有期內有除息)。狀態：None／skip_known／announce_unknown／unresolved／proxy。"""
    covered = covered_through(actions, entry, exit_date, forward)
    if not covered:
        if forward:
            return "unresolved", ["no_coverage"], False
        return ("proxy" if gap else None), [], False
    evs = [e for e in actions["by_code"].get(code, []) if entry < e["eff"] <= exit_date]
    cash_div = any(e["cash"] for e in evs)
    hit = [e for e in evs if not e["cash"]]
    if not hit:
        return None, [], cash_div
    worst, ids = None, [e["id"] for e in hit]
    rank = {"skip_known": 0, "announce_unknown": 1, "unresolved": 2}
    for e in hit:
        k = known_time(e, forward)
        if k is not None and k <= decision_at:
            s = "skip_known"
        elif k is not None:
            s = "unresolved"
        else:
            s = "unresolved" if forward else "announce_unknown"
        worst = s if worst is None or rank[s] > rank[worst] else worst
    return worst, ids, cash_div


# ---------- 前瞻：快照與決策紀錄 ----------
def snapshot_window(ym):
    """P3：正式共同快照的 cutoff 必須在 M+1 月 11 日 19:30 ～ 12 日 09:00（台北）之間；之後才抓到的不能補成這個月的快照。"""
    nm = ym_add(ym, 1)
    return at_tpe("%s11" % nm, 19, 30, 0), at_tpe("%s12" % nm, 9, 0, 0)


def load_snapshots(root, meta_root=None):
    """data/revenue_raw/snapshots/<ym>/{panel.csv, meta.json}。回傳 ym -> dict(rev, cutoff_at, sha)。
    P3：meta.formal 要是 true、cutoff 在正式時窗內、meta_root（repo 裡的持久 meta）必須有該月且整份相同；否則不收。"""
    out = {}
    if not os.path.isdir(root):
        return out
    for ym in sorted(os.listdir(root)):
        mp, pp = os.path.join(root, ym, "meta.json"), os.path.join(root, ym, "panel.csv")
        try:
            meta = json.load(open(mp, encoding="utf-8"))
            sha = hashlib.sha256(open(pp, "rb").read()).hexdigest()
            cut = parse_time(meta["cutoff_at"])
        except (OSError, ValueError, KeyError) as e:
            print("快照 %s 讀不了（%s），不用" % (ym, e))
            continue
        if meta.get("revenue_month") != ym or sha != meta.get("panel_sha256") or cut is None:
            print("快照 %s 月份、雜湊或時間不對，不用" % ym)
            continue
        lo, hi = snapshot_window(ym)
        if not (lo <= cut < hi) or meta.get("formal") is not True:
            print("快照 %s 不是正式時窗（M+1 月 11 日 19:30～12 日 09:00）內的正式快照，不用" % ym)
            continue
        pm = os.path.join(meta_root, ym + ".json") if meta_root else None
        try:
            same = pm is not None and json.load(open(pm, encoding="utf-8")) == meta
        except (OSError, ValueError):
            same = False
        if not same:
            print("快照 %s 沒有 repo 持久 meta 或內容不同（月份、cutoff、來源），不用" % ym)
            continue
        out[ym] = dict(rev=load_rev(pp), cutoff_at=cut, sha=sha)
    return out


def load_decisions(root=DECISIONS):
    """(營收月, 版本) -> 正式決策紀錄清單（依 decision_at 排序）。attempts/ 底下的嘗試紀錄不讀。"""
    out = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
            d["_path"] = p
            d["_decision_at"] = parse_time(d["decision_at"])
            d["_completed_at"] = parse_time(d.get("calculation_completed_at"))
            if d["_decision_at"] is None:
                continue
            out[(d["revenue_month"], int(d["version"]))].append(d)
        except (OSError, ValueError, KeyError):
            continue
    for k in out:
        out[k].sort(key=lambda d: d["_decision_at"])
    return dict(out)


def _as_list(x):
    return x if isinstance(x, list) else [x]


def rows_sha(px, codes, S):
    """母體在價量基準日 S 的價量列雜湊（決策後價量被修訂可以查出來）。"""
    h = hashlib.sha256()
    for c in sorted(codes):
        h.update(("%s,%s,%r\n" % (c, S, tuple(px.get(c, {}).get(S, ())))).encode())
    return h.hexdigest()


def pick_decision(recs, ym, version, sn, days, didx):
    """P1：從紀錄裡挑最早一份「實際可執行」的。回傳 (紀錄, E, S, 狀態)。
    驗：快照雜湊、登記雜湊、decision_at ≥ 快照 cutoff、算完 ≤ decision_at < 實際進場開盤、
    實際進場日＝紀錄的預定進場日、價量基準日＝實際進場前一交易日。"""
    if not recs:
        return None, None, None, "missing_decision"
    lower, inc = nominal_first(ym, version)
    last = "missing_decision"
    for d in recs:
        dec, done = d["_decision_at"], d.get("_completed_at")
        if d.get("snapshot_sha256") != sn["sha"] or d.get("revenue_month") != ym or int(d.get("version", 0)) != version:
            last = "decision_snapshot_mismatch"
            continue
        if d.get("registration_sha256", REGISTRATION_SHA256) != REGISTRATION_SHA256:
            last = "decision_invalid"
            continue
        if dec < sn["cutoff_at"] or done is None or done > dec:
            last = "decision_invalid"
            continue
        E = entry_after(days, lower, inc, dec)
        if E is None:
            last = "immature" if d.get("planned_entry", "") > (days[-1] if days else "") else "decision_stale"
            continue
        S = days[didx[E] - 1]
        if not (dec < open_at(E)) or d.get("planned_entry") != E or S != d.get("liquidity_asof_date") or \
                S != d.get("price_asof_date"):
            last = "decision_stale"          # 實際進場不是當時預定的那天（臨時休市、延誤）→ 這份不能用
            continue
        return d, E, S, "ok"
    return None, None, None, last


def groups_for(px, rev, S, ym, sens_trim=False):
    """在價量基準日 S 套母體、算 SUS、分組。回傳 (母體代號, {代號: 組別}, 原因計數, 資料錯誤代號)。"""
    cand, bad = [], []
    for c, rows in px.items():
        r = rows.get(S)
        if r is None:
            continue
        if not fin(r[3]):
            bad.append(c)
            continue
        if r[3] >= MIN_WAN:
            cand.append(c)
    sus, why = {}, defaultdict(int)
    for c in cand:
        z, w = sus_series(rev, c, ym)
        why[w] += 1
        if z is not None and (not sens_trim or abs(z) <= 10):
            sus[c] = z
    return cand, sus, dict(why), bad


# ---------- 核心 ----------
def build_calendar(px, official=None):
    if official:
        last = max(max(rows) for rows in px.values())
        return [d for d in official if d <= last], "官方"
    cnt = defaultdict(int)
    for rows in px.values():
        for d in rows:
            cnt[d] += 1
    return sorted(d for d, c in cnt.items() if c > len(px) * 0.5), "推算（過半代號有價格）"


def run_version(px, rev, days, version, actions=None, sens_trim=False, snaps=None, decisions=None,
                forward_first=FORWARD_FIRST_YM, excl_cash_div=False):
    """px: code -> {date: (open, close, lot, wan)}。回傳 months: ym -> 記錄；每筆交易另存 trades。"""
    didx = {d: i for i, d in enumerate(days)}
    dts = {c: sorted(rows) for c, rows in px.items()}
    yms = sorted({ym for c in rev for ym in rev[c]} | (set(snaps) if snaps else set()))
    months = {}
    for ym in yms:
        forward = ym >= forward_first
        rec = dict(status="ok", E=None, S=None, decision_at=None, forward=forward, n_cand=0, n_qual=0, why={},
                   untradable=0, unresolved_data=[], data_error=[], h={h: dict(no_exit=0, ca=defaultdict(int), unresolved=0, data_error=0)
                                                    for h in HS}, trades=[], snapshot_sha=None, delay=None)
        if forward:
            if snaps is None or ym not in snaps:
                rec["status"] = "missing_snapshot"
                months[ym] = rec
                continue
            sn = snaps[ym]
            d, E, S, why_d = pick_decision(_as_list((decisions or {}).get((ym, version), [])), ym, version, sn, days, didx)
            if d is None:
                rec["status"] = why_d
                months[ym] = rec
                continue
            dec = d["_decision_at"]
            rv = sn["rev"]
            rec.update(snapshot_sha=sn["sha"], delay=d.get("delay_reason"))
            rec_sus = {c: float(z) for c, z in (d.get("sus") or {}).items()}
            if sens_trim:                                   # P6：用當時的 SUS 值排除極端值後重新分組
                pool_t = {c: z for c, z in rec_sus.items() if abs(z) <= 10}
                g_rec = quintiles(list(pool_t.items())) if pool_t else {}
            else:
                g_rec = {c: int(v) for c, v in d.get("groups", {}).items()}
            rec_universe = list((d.get("universe") or {}).keys())
        else:
            he = historical_entry(ym, version, days)
            if he is None:
                rec["status"] = "immature"
                months[ym] = rec
                continue
            dec, S, E = he
            rv, g_rec, rec_universe = rev, None, None
        rec.update(E=E, S=S, decision_at=dec.isoformat())
        ei = didx[E]
        cand, sus, why, bad = groups_for(px, rv, S, ym, sens_trim)
        if rec_universe is not None:                       # P1b：前瞻用當時保存的完整母體；重算不同只另報
            if set(cand) != set(rec_universe):
                rec["universe_recompute_mismatch"] = True
            if d.get("universe_rows_sha256") and rows_sha(px, rec_universe, S) != d["universe_rows_sha256"]:
                rec["price_revised_after_decision"] = True
            cand, why, bad = rec_universe, d.get("why", {}), list(d.get("turnover_not_finite", []))
        rec.update(n_cand=len(cand), why=why)
        if bad:
            rec["data_error"] += ["turnover_not_finite:%s" % c for c in bad]
        if g_rec is not None and not sens_trim:
            g_now = quintiles(list(sus.items())) if sus else {}
            if g_now != g_rec:
                rec["group_recompute_mismatch"] = True    # 只報告；前瞻一律用當時記下的名單
        last_needed = days[min(ei + max(HS) - 1, len(days) - 1)]
        qc = [c for c in cand if any(S <= q <= last_needed for q in PRICE_QC.get(c, ()))]
        if qc:
            rec["data_error"] += ["price_qc:%s" % c for c in qc]       # P6：短列、重複日期
        trade = {}
        for c in cand:
            rows = px.get(c, {})
            r = rows.get(E)
            if r is None and forward:
                # GPT 09 4.1：當時母體裡的代碼，進場日沒有價格列 ≠ 已證明沒成交（可能是來源缺檔、回補漏掉、後來退出清單）。
                # 只有官方無成交證據（價格列存在且成交量為 0）才走不可成交；否則未解資料、該月不進正式判定。
                rec["unresolved_data"].append(("missing_price_input:%s" if not rows else "missing_entry_row:%s") % c)
                continue
            if r is not None and fin(r[2]) and r[2] == 0:
                rec["untradable"] += 1                                      # 官方零成交（有證據的不可成交）
                continue
            if r is None or not (fin(r[0]) and r[0] > 0):
                if r is not None:
                    rec["data_error"].append("entry_open_bad:%s" % c)      # nan、inf、≤ 0 都是資料錯誤，不當停牌
                rec["untradable"] += 1
                continue
            o = r[0]
            ds = dts.get(c) or sorted(rows)
            i = bisect.bisect_left(ds, E)
            fw = {}
            for h in HS:
                hh = rec["h"][h]
                mi = ei + h - 1
                if mi >= len(days):
                    continue
                tgt = days[mi]
                j = bisect.bisect_left(ds, tgt, i)
                if j >= len(ds):
                    hh["no_exit"] += 1
                    rec["trades"].append(dict(code=c, h=h, entry=E, planned_exit=tgt, actual_exit=None,
                                              last_price_date=ds[-1], last_close=rows[ds[-1]][1], status="no_exit"))
                    continue
                X = ds[j]
                if forward and X != tgt:
                    # 前瞻：預定出場日沒有價格列，沒有官方停牌／零成交證據 → 未解（不自動當停牌順延）
                    hh["unresolved"] += 1
                    rec["trades"].append(dict(code=c, h=h, entry=E, planned_exit=tgt, actual_exit=None,
                                              last_price_date=ds[j - 1] if j > 0 else None, status="missing_exit_row"))
                    continue
                path = [rows[ds[q]] for q in range(i, j + 1)]
                if not all(fin(p[0], p[1]) and p[0] > 0 and p[1] > 0 for p in path):
                    hh["data_error"] += 1
                    rec["trades"].append(dict(code=c, h=h, entry=E, planned_exit=tgt, actual_exit=X, status="data_error"))
                    continue
                gap = any(path[q - 1][1] > 0 and abs(path[q][0] / path[q - 1][1] * 100 - 100) > GAP_PROXY
                          for q in range(1, len(path)))
                stt, ids, cash_div = classify_actions(c, E, X, dec, actions, forward, gap)
                ret = (path[-1][1] / o - 1) * 100
                if not fin(ret):
                    hh["data_error"] += 1
                    continue
                rec["trades"].append(dict(code=c, h=h, entry=E, planned_exit=tgt, actual_exit=X, ret=ret,
                                          status=stt or "ok", events=ids, cash_div=cash_div))
                if stt:
                    hh["ca"][stt] += 1
                if stt == "unresolved":
                    hh["unresolved"] += 1                 # 保留原筆，月不進判定
                elif stt in ("skip_known", "announce_unknown", "proxy"):
                    continue
                if excl_cash_div and cash_div:
                    continue
                fw[h] = ret
            trade[c] = fw
        if forward:
            g = {c: v for c, v in g_rec.items() if c in trade}
        else:
            pool = {c: z for c, z in sus.items() if c in trade}
            g = quintiles(list(pool.items())) if pool else {}
        rec["n_qual"] = len(g)
        n1 = sum(1 for v in g.values() if v == 1)
        n5 = sum(1 for v in g.values() if v == 5)
        if len(g) < MIN_QUALIFIED or n1 < MIN_TAIL or n5 < MIN_TAIL:
            rec["status"] = "too_few"
            months[ym] = rec
            continue
        for t in rec["trades"]:
            t["group"] = g.get(t["code"])
        for h in HS:
            uni = [fw[h] for fw in trade.values() if h in fw]
            q5 = [trade[c][h] for c, v in g.items() if v == 5 and h in trade[c]]
            q1 = [trade[c][h] for c, v in g.items() if v == 1 and h in trade[c]]
            qual = [trade[c][h] for c in g if h in trade[c]]
            if uni and q5 and q1:
                rec["h"][h].update(uni=st.mean(uni), q5=st.mean(q5), q1=st.mean(q1), qual=st.mean(qual), raw_q5=q5)
        months[ym] = rec
    return months


def month_valid(rec, h=MAIN_H):
    """該月可進判定：分組成立、該持有期成熟，沒有資料錯誤、無法驗證出場、未解公司行動。"""
    if rec["status"] != "ok" or rec["data_error"] or rec.get("unresolved_data"):
        return False
    hh = rec["h"][h]
    return "q5" in hh and not (hh["no_exit"] or hh["data_error"] or hh["unresolved"])


def summarize(months, yms, h=MAIN_H, lag=None, ref="uni"):
    if not yms:
        return None
    xs = {ym: months[ym]["h"][h]["q5"] - months[ym]["h"][h][ref] for ym in yms}
    raw = [r for ym in yms for r in months[ym]["h"][h]["raw_q5"]]
    lag = NW_LAG[h] if lag is None else lag
    t, se = nw(xs, lag)
    xb = sum(xs.values()) / len(xs)
    return dict(months=len(xs), first=min(yms), last=max(yms),
                elapsed_calendar_months=ym_index(max(yms)) - ym_index(min(yms)) + 1,
                events=len(raw), excess=xb, t=t, ci95=None if se is None else [xb - 1.96 * se, xb + 1.96 * se],
                raw=st.mean(raw), net=st.mean(raw) - COST)


def verdict(s11, s16, n):
    if n < MIN_MONTHS:
        return "資料不足"

    def ok(s):
        return s["excess"] > 0 and s["t"] is not None and s["t"] >= 2 and s["net"] > 0
    a, b = ok(s11), ok(s16)
    return "通過" if a and b else "未共同通過" if a or b else "測不出來"


def judge(m11, m16, yms_common, h=MAIN_H):
    s11, s16 = summarize(m11, yms_common, h), summarize(m16, yms_common, h)
    return verdict(s11, s16, len(yms_common)), s11, s16


def study(px, rev, actions=None, forward_first=FORWARD_FIRST_YM, snaps=None, decisions=None, official_days=None):
    days, cal_src = build_calendar(px, official_days)
    out = dict(program=PROGRAM, registration_sha256=REGISTRATION_SHA256, pit_verified_history=False, calendar=cal_src,
               ca_source=("corporate_actions（覆蓋 %d 個月）" % len(actions["covered"]) if actions.get("ok", True) else
                          "事件表不可用（%s）→ 歷史暫代、前瞻未解" % actions.get("why")) if actions else "暫代（開盤跳超過 11%）",
               price_qc_codes=len(PRICE_QC), segments={})
    kw = dict(snaps=snaps, decisions=decisions, forward_first=forward_first)
    m = {v: run_version(px, rev, days, v, actions, **kw) for v in (11, 16)}
    mt = {v: run_version(px, rev, days, v, actions, sens_trim=True, **kw) for v in (11, 16)}
    mc = {v: run_version(px, rev, days, v, actions, excl_cash_div=True, **kw) for v in (11, 16)}
    allm = sorted(set(m[11]) & set(m[16]))
    common = [ym for ym in allm if month_valid(m[11][ym]) and month_valid(m[16][ym])]
    for seg, sel in (("探索（歷史月，不判定通過）", [y for y in common if y < forward_first]),
                     ("前瞻", [y for y in common if y >= forward_first])):
        res = dict(common_months=len(sel), first=sel[0] if sel else None, last=sel[-1] if sel else None, ref={}, sens={})
        if seg == "前瞻":
            res["formal_checkpoints"] = []
            for N in CHECKPOINTS:
                if len(sel) >= N:
                    st_, a, b = judge(m[11], m[16], sel[:N])
                    res["formal_checkpoints"].append(dict(N=N, months=[sel[0], sel[N - 1]], status=st_, v11=a, v16=b))
            res["status"] = res["formal_checkpoints"][-1]["status"] if res["formal_checkpoints"] else "資料不足（持續追蹤）"
            res["tracking_v11"], res["tracking_v16"] = summarize(m[11], sel), summarize(m[16], sel)
        else:
            res["status"] = "探索"
            res["v11"], res["v16"] = summarize(m[11], sel), summarize(m[16], sel)
        for v in (11, 16):
            mm = m[v]
            own = [y for y in sorted(mm) if month_valid(mm[y]) and ((y >= forward_first) == (seg == "前瞻"))]
            res["ref"]["v%d_own_valid_months" % v] = summarize(mm, own)
            if not sel:
                continue
            res["ref"]["v%d_vs_qualified" % v] = summarize(mm, sel, ref="qual")
            for h in HS:
                ok = [y for y in sel if month_valid(mm[y], h)]
                res["ref"]["v%d_h%d" % (v, h)] = summarize(mm, ok, h) if ok else None
                res["ref"]["v%d_h%d_excluded_months" % (v, h)] = len(sel) - len(ok)
            for L in (0, 2):
                res["sens"]["v%d_nw%d" % (v, L)] = summarize(mm, sel, lag=L)
            tr = [y for y in sel if month_valid(mt[v][y])]
            res["sens"]["v%d_trim10" % v] = summarize(mt[v], tr) if tr else None
            cd = [y for y in sel if month_valid(mc[v][y])]
            res["sens"]["v%d_excl_cash_div" % v] = summarize(mc[v], cd) if cd else None
            diffs = [mm[y]["h"][MAIN_H]["q5"] - mm[y]["h"][MAIN_H]["q1"] for y in sel]
            res["ref"]["v%d_q5_minus_q1" % v] = dict(months=len(diffs), diff=sum(diffs) / len(diffs))
        out["segments"][seg] = res
    out["months_by_version"] = {"v%d" % v: {ym: dict(
        E=r["E"], S=r["S"], decision_at=r["decision_at"], forward=r["forward"], status=r["status"], delay=r["delay"],
        snapshot_sha=r["snapshot_sha"], n_cand=r["n_cand"], n_qual=r["n_qual"], why=r["why"], untradable=r["untradable"],
        unresolved_data=r.get("unresolved_data", [])[:20],
        data_error=r["data_error"][:20], group_recompute_mismatch=r.get("group_recompute_mismatch", False),
        universe_recompute_mismatch=r.get("universe_recompute_mismatch", False),
        price_revised_after_decision=r.get("price_revised_after_decision", False),
        h={str(h): dict(no_exit=r["h"][h]["no_exit"], data_error=r["h"][h]["data_error"], unresolved=r["h"][h]["unresolved"],
                        ca=dict(r["h"][h]["ca"])) for h in HS},
        valid=month_valid(r)) for ym, r in m[v].items()} for v in (11, 16)}
    out["excluded_common"] = [ym for ym in allm if ym not in common]
    out["_trades"] = {v: [dict(t, ym=ym, version=v) for ym, r in m[v].items() for t in r["trades"]] for v in (11, 16)}
    return out


# ---------- 前瞻決策（每晚跑；寫下當時的名單與時間）----------
def _now():
    return dt.datetime.now(TPE)


def _procedure_sha():
    try:
        return _sha_file(os.path.abspath(__file__))
    except OSError:
        return None


def decide(px, snaps, days, next_day, clock=_now, root=DECISIONS, forward_first=FORWARD_FIRST_YM, calendar_sha=None,
           calendar_verified_at=None):
    """每晚跑。days：到最新價量日為止的官方市場日；next_day：之後的下一個官方市場日。clock：回傳台北時間的函式。
    對每個有正式快照的前瞻營收月與版本：next_day 符合名義下限、還沒有「預定 next_day」或「預定日已是市場日（已執行）」的紀錄時，
    以最新價量日 S 當基準算分組。P1a：算完重新讀時鐘當 decision_at；已過 next_day 開盤 → 只寫 attempts/ 嘗試紀錄。
    紀錄一旦寫下就不改；臨時休市讓預定日沒開市時，下一晚另寫一份（檔名帶時間），舊的保留。回傳新寫的正式紀錄檔名。"""
    os.makedirs(root, exist_ok=True)
    S = days[-1]
    dayset = set(days)
    existing = load_decisions(root)
    written = []
    for ym, sn in sorted(snaps.items()):
        started = clock()
        if ym < forward_first or sn["cutoff_at"] >= started:
            continue
        for v in (11, 16):
            prev = existing.get((ym, v), [])
            if any(d.get("planned_entry") == next_day or d.get("planned_entry") in dayset for d in prev):
                continue                                    # 已有同一預定日的紀錄，或之前的預定日已開市（已執行）
            lower, inc = nominal_first(ym, v)
            if not (next_day >= lower if inc else next_day > lower):
                continue
            started = clock()
            if open_at(next_day) <= started:
                continue
            first_ok = next(d for d in days + [next_day] if (d >= lower if inc else d > lower))
            cand, sus, why, bad = groups_for(px, sn["rev"], S, ym)
            g = quintiles(list(sus.items())) if sus else {}
            rec = dict(revenue_month=ym, version=v, program=PROGRAM, registration_sha256=REGISTRATION_SHA256,
                       procedure_sha256=_procedure_sha(), snapshot_sha256=sn["sha"], revenue_cutoff_at=sn["cutoff_at"].isoformat(),
                       price_asof_date=S, liquidity_asof_date=S, calendar_sha256=calendar_sha,
                       calendar_sources_verified_at=calendar_verified_at,
                       calculation_started_at=started.isoformat(), planned_entry=next_day, nominal_first_entry=first_ok,
                       delay_reason=None if first_ok == next_day else "排程、資料延誤或臨時休市：名義最早 %s" % first_ok,
                       n_universe=len(cand), n_qualified=len(sus), why=why, turnover_not_finite=bad,
                       universe={c: px[c][S][3] for c in sorted(cand)}, universe_rows_sha256=rows_sha(px, cand, S),
                       sus={c: sus[c] for c in sorted(sus)}, groups={c: g[c] for c in sorted(g)},
                       superseded_attempts=[os.path.basename(d.get("_path", "")) for d in prev])
            done = clock()                                  # P1a：算完才讀的時間
            rec["calculation_completed_at"] = rec["decision_at"] = done.isoformat()
            stamp = done.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            if open_at(next_day) <= done:
                rec["status"] = "late_after_open"
                p = os.path.join(root, "attempts", "%s_v%d_%s.json" % (ym, v, stamp))
            else:
                p = os.path.join(root, "%s_v%d_%s.json" % (ym, v, stamp))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            json.dump(rec, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, p)
            if rec.get("status") != "late_after_open":
                written.append(p)
    return written


# ---------- 讀資料 ----------
def load_prices(root=os.path.join("data", "stock")):
    """code -> {date: (open, close, lot, wan)}；讀不成數字的列記成 nan（不默默丟掉，讓後面標 data_error）。"""
    px = {}
    PRICE_QC.clear()
    for f in glob.glob(os.path.join(root, "*.txt")):
        rows = {}
        code = os.path.basename(f)[:-4]
        for l in open(f, encoding="utf-8"):
            if l[0] == "#" or not l.strip():
                continue
            p = l.strip().split(",")
            if len(p) < 7:
                PRICE_QC[code].add(p[0])                 # P6：短列不默默丟，記 QC（該日期落在持有期就是資料錯誤）
                continue
            if p[0] in rows:
                PRICE_QC[code].add(p[0])                 # 重複日期：保留第一筆、記 QC
                continue

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return float("nan")
            rows[p[0]] = (num(p[1]), num(p[4]), num(p[5]), num(p[6] or "0"))
        if len(rows) > 30:
            px[os.path.basename(f)[:-4]] = rows
    return px


def load_rev(panel_path):
    rev = defaultdict(dict)
    for r in csv.DictReader(open(panel_path, encoding="utf-8")):
        try:
            rev[r["code"]][r["ym"]] = float(r["rev"])
        except (ValueError, KeyError):
            continue
    return rev


def load_official_calendar(path=os.path.join("data", "market_calendar.json"), with_meta=False):
    if not os.path.exists(path):
        return (None, None, None) if with_meta else (None, None)
    j = json.load(open(path, encoding="utf-8"))
    out = sorted(j.get("trading_days", [])), sorted(set(j.get("holidays", [])) | set(j.get("closures", [])))
    if with_meta:
        sv = j.get("sources_verified_at") or {}
        return out + (dict(built_at=parse_time(j.get("built_at", "")), schedule_years=j.get("schedule_years", []),
                           verified={k: parse_time(sv.get(k, "")) for k in CAL_REQUIRED}, sha=_sha_file(path)),)
    return out


def calendar_usable(meta, next_day, now):
    """P4：前瞻決策只在官方日曆新鮮、而且涵蓋下一個市場日的年度時才可用。回傳 (可用, 原因)。"""
    if not meta:
        return False, "官方日曆不存在"
    ver = meta.get("verified") or {}
    for k in CAL_REQUIRED:                     # GPT 09 4.2：看來源成功核驗時間，不看重建時間（built_at）
        t = ver.get(k)
        if t is None:
            return False, "官方日曆來源 %s 沒有成功核驗紀錄" % k
        if now - t > dt.timedelta(hours=CAL_MAX_AGE_H):
            return False, "官方日曆來源 %s 超過 %d 小時沒有成功更新（重建不算）" % (k, CAL_MAX_AGE_H)
    if next_day[:4] not in meta.get("schedule_years", []):
        return False, "官方休市表沒有涵蓋 %s 年" % next_day[:4]
    return True, ""


def next_market_day(after, holidays):
    """after 之後的下一個市場日：跳過週末、官方休市日與已核實的臨時休市（呼叫端要先確認日曆可用，見 calendar_usable）。"""
    d = dt.date(int(after[:4]), int(after[4:6]), int(after[6:8])) + dt.timedelta(days=1)
    hs = set(holidays or [])
    while d.weekday() >= 5 or d.strftime("%Y%m%d") in hs:
        d += dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


# ---------- 報告 ----------
def fmt(s):
    if not s:
        return "—"
    if "diff" in s:
        return "%d 個月、Q5−Q1 差 %+.2f%%" % (s["months"], s["diff"])
    ci = "" if not s.get("ci95") else "、95%% 區間 %+.2f～%+.2f%%" % tuple(s["ci95"])
    return "%d 個營收月（%s～%s，經過 %d 曆月）／%d 筆、超額 %+.2f%%（NW t %s%s）、事件等權原始 %+.2f%%、淨 %+.2f%%" % (
        s["months"], s["first"], s["last"], s["elapsed_calendar_months"], s["events"], s["excess"],
        "—" if s["t"] is None else "%+.2f" % s["t"], ci, s["raw"], s["net"])


def report(res):
    L = ["# SUS 研究報告（%s；登記 v2.5 SHA-256 %s…）" % (res["program"], res["registration_sha256"][:8]), "",
         "市場日曆：%s。公司行動：%s。歷史月資料是現在查得到的版本（pit_verified＝false），一律「探索」；"
         "前瞻月（營收月 ≥ %s）只在共同有效月累積到 12、24、36 個時正式檢視。" % (res["calendar"], res["ca_source"], FORWARD_FIRST_YM),
         "對照組＝進場前一交易日成交金額 ≥ 1 億的全部代號（含 ETF 等，與 R1～R5 相同）。"]
    for seg, r in res["segments"].items():
        L += ["", "## %s —— %s" % (seg, r["status"]), "- 共同有效營收月 %d 個（%s～%s）" % (r["common_months"], r["first"], r["last"])]
        if seg == "前瞻":
            for c in r["formal_checkpoints"]:
                L.append("- **正式檢視 N=%d（%s～%s）：%s**；11 日版 %s；16 日版 %s" % (c["N"], c["months"][0], c["months"][1],
                                                                                 c["status"], fmt(c["v11"]), fmt(c["v16"])))
            L.append("- 持續追蹤（不算正式檢定）11 日版：%s" % fmt(r["tracking_v11"]))
            L.append("- 持續追蹤（不算正式檢定）16 日版：%s" % fmt(r["tracking_v16"]))
        else:
            L.append("- 11 日版 20 日：%s" % fmt(r["v11"]))
            L.append("- 16 日版 20 日：%s" % fmt(r["v16"]))
        for k, s in r["ref"].items():
            L.append("- 參考 %s：%s" % (k, s if isinstance(s, int) else fmt(s)))
        for k, s in r["sens"].items():
            L.append("- 敏感度 %s：%s" % (k, fmt(s)))
    L += ["", "兩版共同有效以外的營收月 %d 個。" % len(res["excluded_common"])]
    for v, ms in res["months_by_version"].items():
        L += ["", "## 各營收月覆蓋（%s）" % v,
              "| 營收月 | 決策 | 進場 | 狀態 | 母體 | 分組 | 完全沒有營收／當月無營收 | 37 月不齊 | 不可成交 | 20 日 無法出場／資料錯誤／未解 | 公司行動（20 日）| 延誤 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for ym, r in sorted(ms.items()):
            h = r["h"][str(MAIN_H)]
            L.append("| %s | %s | %s | %s%s | %s | %s | %s／%s | %s | %s | %s／%s／%s | %s | %s |" % (
                ym, (r["decision_at"] or "")[:16], r["E"], r["status"], "（資料錯誤）" if r["data_error"] else "",
                r["n_cand"], r["n_qual"], r["why"].get("no_revenue", 0), r["why"].get("no_current_month", 0),
                r["why"].get("missing_history_month", 0),
                r["untradable"], h["no_exit"], h["data_error"], h["unresolved"], h["ca"] or "", r["delay"] or ""))
    return "\n".join(L) + "\n"


def write_trades(res, path="sus_trades.csv"):
    cols = ["version", "ym", "code", "group", "h", "entry", "planned_exit", "actual_exit", "last_price_date", "last_close", "ret",
            "status", "events", "cash_div"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for v, ts in res["_trades"].items():
            for t in ts:
                t = dict(t)
                t["events"] = "/".join(t.get("events") or [])
                w.writerow(t)


def main(args):
    official, holidays, calmeta = load_official_calendar(with_meta=True)
    px = load_prices()
    snaps = load_snapshots(os.path.join("data", "revenue_raw", "snapshots"), os.path.join("data", "revenue_snapshots_meta"))
    if "--decide" in args:
        if not official:
            print("::error::SUS 決策：沒有官方市場日曆，不寫決策（不用過半股價推算）")
            sys.exit(2)
        last = max(max(rows) for rows in px.values())
        days = [d for d in official if d <= last]
        nd = next_market_day(days[-1], holidays)
        ok, why = calendar_usable(calmeta, nd, _now())
        if not ok:
            print("::error::SUS 決策：%s，不寫決策" % why)
            sys.exit(2)
        w = decide(px, snaps, days, nd, calendar_sha=calmeta["sha"],
                   calendar_verified_at={k: v.isoformat() for k, v in calmeta["verified"].items()})
        print("::notice::SUS 決策：價量基準日 %s、下一個市場日 %s；新寫 %d 份紀錄 %s" % (days[-1], nd, len(w), w))
        return
    import revenue_panel
    pp, _mp, how = revenue_panel.resolve(os.path.join("data", "revenue"))
    if not pp:
        print("沒有營收 panel，先跑 revenue_fetch.py")
        sys.exit(1)
    res = study(px, load_rev(pp),
                load_actions(os.path.join("data", "corporate_actions.csv"), os.path.join("data", "corporate_actions_coverage.json")),
                snaps=snaps, decisions=load_decisions(), official_days=official)
    write_trades(res)
    res.pop("_trades")
    json.dump(res, open("sus_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    open("sus_report.md", "w", encoding="utf-8").write(report(res))
    print(report(res))


if __name__ == "__main__":
    main(sys.argv[1:])
