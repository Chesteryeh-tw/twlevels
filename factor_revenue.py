#!/usr/bin/env python3
"""月營收因子測試（研究_免費資料因子 第 5 節，2026-10-19 事先登記；格子和門檻不改）。

R1 創 12 個月新高且年增 > 0（多）  R2 年增由負轉正（多）  R3 年增連 3 個月 > 20%（多）
R4 年增連 3 個月 < 0（空）         R5 年增五分組，最高組 − 最低組（預期正）

進場：M 月營收 → M+1 月 11 日（含）以後第一個交易日開盤（穩健性：16 日以後）。
出場：持有 h 天（第 h 個交易日收盤；跟 backtest.py 一樣，進場當天算第 1 天）。
宇宙：進場前一個交易日成交金額 ≥ 1 億，且個股的進場日＝市場交易日（沒停牌）。
超額：減掉同一進場日、同一宇宙的平均。t：每個進場日先平均，ddof=1；60 天用 Newey-West 落後 3 期。
資料是「現在查得到的版本」（pit_verified=false），報告一律標「探索」。

用法：python factor_revenue.py  → 寫 revenue_report.md、revenue_report.json
"""
import csv, glob, json, math, os, statistics as st, sys
from collections import defaultdict

PANEL = os.path.join("data", "revenue", "panel.csv")
COST = 0.386
H = (5, 20, 60)
MAIN_H = 20
EXPLORE = ("202101", "202412")       # 進場日所在年月
VALIDATE = ("202501", "202608")


def load_prices():
    px = {}
    for f in glob.glob(os.path.join("data", "stock", "*.txt")):
        rows = []
        for l in open(f, encoding="utf-8"):
            if l[0] == "#":
                continue
            p = l.strip().split(",")
            try:
                rows.append((p[0], float(p[1]), float(p[4]), float(p[6] or 0)))   # 日期、開、收、成交金額（萬元）
            except (ValueError, IndexError):
                pass
        if len(rows) > 30:
            px[os.path.basename(f)[:-4]] = rows
    return px


def check_panel_meta():
    """panel_meta.json 記了 panel.csv 的雜湊；兩者對不上（只發布到一半）就不跑，免得用到新舊混合的資料。"""
    mp = os.path.join(os.path.dirname(PANEL), "panel_meta.json")
    if not os.path.exists(mp):
        print("沒有 panel_meta.json（舊版快取產生的 panel），略過一致性檢查")
        return
    import hashlib
    meta = json.load(open(mp, encoding="utf-8"))
    sha = hashlib.sha256(open(PANEL, "rb").read()).hexdigest()
    if meta.get("panel_sha256") and meta["panel_sha256"] != sha:
        raise SystemExit("panel.csv 跟 panel_meta.json 的雜湊對不上（%s ≠ %s），不執行" % (sha[:12], str(meta["panel_sha256"])[:12]))


def load_panel():
    check_panel_meta()
    rev = defaultdict(dict)     # code -> ym -> (rev, rev_ly)
    for r in csv.DictReader(open(PANEL, encoding="utf-8")):
        try:
            a = float(r["rev"]); b = float(r["rev_ly"]) if r["rev_ly"] else None
        except ValueError:
            continue
        rev[r["code"]][r["ym"]] = (a, b)
    return rev


def ym_add(ym, k):
    y, m = int(ym[:4]), int(ym[4:])
    m += k
    while m > 12: y, m = y + 1, m - 12
    while m < 1: y, m = y - 1, m + 12
    return "%d%02d" % (y, m)


def yoy(rv, code, ym):
    x = rv[code].get(ym)
    if not x or x[1] is None or x[1] <= 0:
        return None
    return x[0] / x[1] - 1


def nw_t(xs, lag):
    n = len(xs)
    if n < 10:
        return None
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(e * e for e in d) / (n - 1)
    for L in range(1, lag + 1):
        var += 2 * (1 - L / (lag + 1)) * sum(d[j] * d[j - L] for j in range(L, n)) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else None


def main(entry_day=11):
    px = load_prices()
    rv = load_panel()
    # 市場交易日：超過一半股票有的日子
    cnt = defaultdict(int)
    for rows in px.values():
        for r in rows:
            cnt[r[0]] += 1
    days = sorted(d for d, c in cnt.items() if c > len(px) * 0.5)
    pos = {c: {r[0]: i for i, r in enumerate(rows)} for c, rows in px.items()}
    didx = {d: i for i, d in enumerate(days)}

    yms = sorted({ym for c in rv for ym in rv[c]})
    events = []          # (entry_date, ym, code, {h: ret}, flags)
    for ym in yms:
        nm = ym_add(ym, 1)
        cut = "%s%02d" % (nm, entry_day)
        E = next((d for d in days if d >= cut), None)
        if E is None:
            continue
        ei = didx[E]
        if ei == 0:
            continue
        S = days[ei - 1]                   # 進場前一個交易日（宇宙判斷用）
        uni = []
        for c, rows in px.items():
            p = pos[c]
            if E not in p or S not in p:
                continue
            i = p[E]
            if rows[i - 1][0] != S:        # 對不上市場日曆（停牌）就不算
                continue
            if rows[i - 1][3] < 10000:     # 成交金額欄是「萬元」：10000 萬 = 1 億
                continue
            fw = {}
            for h in H:
                j = i + h - 1
                if j < len(rows) and rows[i][1] > 0 and didx.get(rows[j][0]) == ei + h - 1:
                    fw[h] = (rows[j][2] / rows[i][1] - 1) * 100
            uni.append((c, fw))
        if len(uni) < 50:
            continue
        bench = {h: st.mean([fw[h] for _, fw in uni if h in fw]) for h in H
                 if any(h in fw for _, fw in uni)}
        for c, fw in uni:
            y0 = yoy(rv, c, ym)
            if y0 is None:
                continue
            y1, y2 = yoy(rv, c, ym_add(ym, -1)), yoy(rv, c, ym_add(ym, -2))
            prev11 = [rv[c].get(ym_add(ym, -k)) for k in range(1, 12)]
            cur = rv[c][ym][0]
            f = {}
            f["R1"] = all(prev11) and cur > max(x[0] for x in prev11) and y0 > 0
            f["R2"] = y1 is not None and y1 <= 0 < y0
            f["R3"] = None not in (y1, y2) and min(y0, y1, y2) > 0.20
            f["R4"] = None not in (y1, y2) and max(y0, y1, y2) < 0
            f["yoy"] = y0
            ex = {h: fw[h] - bench[h] for h in fw if h in bench}
            events.append(dict(E=E, ym=ym, c=c, fw=fw, ex=ex, f=f))
        # R5 五分組
        es = [e for e in events if e["E"] == E]
        es.sort(key=lambda e: e["f"]["yoy"])
        n = len(es)
        for k, e in enumerate(es):
            e["f"]["q"] = min(4, k * 5 // n) + 1

    def summarize(sel, side=1, period=None, h=MAIN_H):
        by = defaultdict(list); raw = []
        for e in sel:
            if period and not (period[0] <= e["E"][:6] <= period[1]):
                continue
            if h not in e["ex"]:
                continue
            by[e["E"]].append(side * e["ex"][h]); raw.append(e["fw"][h])
        if not by:
            return None
        dm = [st.mean(v) for _, v in sorted(by.items())]
        lag = 0 if h <= 20 else 3
        t = nw_t(dm, lag)
        m_raw = st.mean(raw)
        return dict(n=len(raw), dates=len(dm), excess=st.mean(dm), t=t,
                    raw=m_raw, med=st.median(raw),
                    net=(m_raw - COST) if side == 1 else (-m_raw - COST),
                    win=sum(1 for x in raw if side * x > 0) / len(raw) * 100)

    def q5q1(period, h=MAIN_H):
        by = defaultdict(lambda: defaultdict(list))
        for e in events:
            if period and not (period[0] <= e["E"][:6] <= period[1]):
                continue
            if h in e["ex"] and e["f"]["q"] in (1, 5):
                by[e["E"]][e["f"]["q"]].append(e["ex"][h])
        dm = [st.mean(v[5]) - st.mean(v[1]) for _, v in sorted(by.items()) if v[1] and v[5]]
        if not dm:
            return None
        return dict(dates=len(dm), diff=st.mean(dm), t=nw_t(dm, 0 if h <= 20 else 3))

    out = dict(entry_day=entry_day, pit_verified=False, n_events=len(events),
               entry_dates=len({e["E"] for e in events}),
               first=min(e["E"] for e in events) if events else None,
               last=max(e["E"] for e in events) if events else None, cells={})
    spec = [("R1", "創 12 個月新高且年增 > 0", 1), ("R2", "年增由負轉正", 1),
            ("R3", "年增連 3 個月 > 20%", 1), ("R4", "年增連 3 個月 < 0（做空）", -1)]
    for key, name, side in spec:
        sel = [e for e in events if e["f"][key]]
        out["cells"][key] = dict(name=name, side=side, by_period={
            pn: {str(h): summarize(sel, side, per, h) for h in H}
            for pn, per in (("explore", EXPLORE), ("validate", VALIDATE), ("all", None))})
    out["cells"]["R5"] = dict(name="年增五分組：最高 − 最低", side=1, by_period={
        pn: {str(h): q5q1(per, h) for h in H}
        for pn, per in (("explore", EXPLORE), ("validate", VALIDATE), ("all", None))})
    # 逐筆清單給 GPT 複核（只放 Actions 下載區，不 commit：報酬由 FinMind 股價算出，不能轉散布）
    with open("revenue_events_%d.csv" % entry_day, "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(["entry", "ym", "code", "R1", "R2", "R3", "R4", "yoy", "q5"]
                   + ["ret%d" % h for h in H] + ["ex%d" % h for h in H])
        for e in events:
            f = e["f"]
            w.writerow([e["E"], e["ym"], e["c"]] + [int(bool(f[k])) for k in ("R1", "R2", "R3", "R4")]
                       + ["%.6f" % f["yoy"], f.get("q", "")]
                       + ["%.6f" % e["fw"][h] if h in e["fw"] else "" for h in H]
                       + ["%.6f" % e["ex"][h] if h in e["ex"] else "" for h in H])
    return out


def verdict(c, side):
    """驗證期：同方向、t ≥ 2、淨 > 0 → 通過。"""
    v = c["by_period"]["validate"][str(MAIN_H)]
    if not v:
        return "沒有資料"
    if "diff" in v:
        ok = v["diff"] > 0 and (v["t"] or 0) >= 2
        return "通過" if ok else "測不出來"
    ok = v["excess"] > 0 and (v["t"] or 0) >= 2 and v["net"] > 0
    return "通過" if ok else "測不出來"


def fmt(x):
    if not x:
        return "—"
    if "diff" in x:
        return "%d 個進場日、差 %+.2f%%、t %s" % (x["dates"], x["diff"], "—" if x["t"] is None else "%+.1f" % x["t"])
    return "%d 筆／%d 個進場日、超額 %+.2f%%（t %s）、原始 %+.2f%%、中位 %+.2f%%、淨 %+.2f%%、照方向賺的比例 %.0f%%" % (
        x["n"], x["dates"], x["excess"], "—" if x["t"] is None else "%+.1f" % x["t"],
        x["raw"], x["med"], x["net"], x["win"])


if __name__ == "__main__":
    if not os.path.exists(PANEL):
        print("沒有 %s，先跑 revenue_fetch.py" % PANEL); sys.exit(1)
    res = {"main": main(11), "robust16": main(16)}
    json.dump(res, open("revenue_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = ["# 月營收因子測試（探索，資料是現在查得到的版本）", "",
         "事先登記：研究_免費資料因子 第 5 節。主要看 %d 天；探索 %s～%s、驗證 %s～%s（進場日）。" % (
             MAIN_H, EXPLORE[0], EXPLORE[1], VALIDATE[0], VALIDATE[1]), ""]
    m = res["main"]
    L.append("事件 %d 筆、%d 個進場日（%s～%s）。" % (m["n_events"], m["entry_dates"], m["first"], m["last"]))
    for key, c in m["cells"].items():
        side = c["side"]
        v11, v16 = verdict(c, side), verdict(res["robust16"]["cells"][key], side)
        # 事先登記：11 日和 16 日進場結論不同時，以 16 日為準
        L += ["", "## %s %s（%s）—— 驗證期：**%s**（11 日進場：%s；16 日進場：%s）" % (
            key, c["name"], "多" if side == 1 else "空", v16, v11, v16)]
        for pn, lab in (("explore", "探索"), ("validate", "驗證"), ("all", "全部")):
            for h in H:
                L.append("- %s %d 天：%s" % (lab, h, fmt(c["by_period"][pn][str(h)])))
        r = res["robust16"]["cells"][key]["by_period"]["validate"][str(MAIN_H)]
        L.append("- 穩健性（16 日以後才進場）驗證 %d 天：%s" % (MAIN_H, fmt(r)))
    L += ["", "格子 5 個（另外 2 個濾網在 backtest 的 x2by／x2bn、x5y／x5n），純運氣預期約 0.35 格過 t ≥ 2。",
          "資料是現在查得到的版本（之後的更正會蓋掉原值），所以就算通過也只算探索。"]
    open("revenue_report.md", "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
