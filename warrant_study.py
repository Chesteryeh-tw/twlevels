#!/usr/bin/env python3
"""H5「認購買超排行」回測（研究_權證分點 第 2 節 H5）。

== 事先登記（2026-10-24，Claude 擬、使用者同意先做；寫在抓資料之前，以這個檔案的 commit 時間為準）==
資料：warrant_fetch.py 的 derived/（證交所 T86 權證類別＋MI_INDEX 權證行情），只有上市權證。
訊號日 T：每檔上市標的，加總它所有「認購」權證的 估計淨買金額＝非自營商淨股數 × 當日權證均價（成交金額÷成交股數）。
  候選：T 日標的成交金額 ≥ 1 億；排名取前 20 名且金額 > 0。
進場：T 的下一個開市日開盤（標的當天沒成交就不算）。持有 1／5／10／20 個開市日，收盤出場。
  主要格：10 日（兩週，跟講義「波段」與既有籌碼研究一致）；1、5、20 日只當參考。
比較：超額＝事件報酬 − 同一進場日、T 日成交金額 ≥ 1 億的全部上市櫃股票同期報酬平均；
  t＝每個訊號日平均超額的序列，Newey-West（重疊 h 期，同 backtest.py nw_t）；
  淨＝事件原始報酬平均 − 0.386%（隔夜來回成本）；跳空（T 收→T+1 開）另列，吃不到。
期間（以訊號日分）：探索 2024-10-01～2025-09-30；驗證 2025-10-01 起。
判定：驗證期 10 日 超額 > 0、t ≥ 2、淨 > 0 三個都成立才算通過。
參考（不判定）：排名 21～40；認售前 20（預期方向：超額 < 0，只能當濾網）。
（10-24 探測後、抓資料前補：標的改用 MI_INDEX 官方「標的代號」，名稱比對只當備援；不影響上面的條件與判定。）
（10-24 14:40 補登記，驗證期資料抓到之前、只看過探索期：
  ①持有期間（進場日之後到出場日）標的或基準股出現「開盤比前一天收盤差超過 11%」——漲跌幅限制 10%，
    超過就是面額變更、減資等公司行動（例：緯穎 2026-09-02 7800→2790）——該筆不算，基準也排除；
  ②另報「同規模超額」：基準改成同一天、同一成交金額十分位的股票平均（探索期認購、認售前 20 都贏大盤，
    懷疑是權證熱門股本來就強）。主要判定不變；但同規模超額 ≤ 0 時，結論寫成「熱門股效應、不是認購買超」。）
已知限制：非自營商＝自營商以外所有人，
  不是特定分點；股價未還原除權息；上櫃權證不在內。
"""
import csv, datetime as dt, glob, json, os, sys

RAW_DER = os.path.join("data", "warrant_raw", "derived")
STOCK = os.path.join("data", "stock")
HS = (1, 5, 10, 20)
MAIN_H = 10
COST = 0.386
MIN_WAN = 10000          # 萬元 → 1 億
TOPN = 20
EXPLORE_END = "20250930"


def nw_t(daily, hl):
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


ACTION_GAP = 11.0      # %；漲跌幅 10%，開盤跳超過 11% 視為公司行動


def load_stocks():
    """回傳 (px, action)。action[code]＝開盤比前一天收盤差超過 11% 的日期集合。"""
    px, action = {}, {}
    for p in glob.glob(os.path.join(STOCK, "*.txt")):
        code = os.path.basename(p)[:-4]
        d = {}
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                r = line.strip().split(",")
                try:
                    d[r[0]] = (float(r[1]), float(r[4]), float(r[6]) if len(r) > 6 and r[6] else 0.0)
                except (ValueError, IndexError):
                    continue
        if d:
            px[code] = d
            prev = None
            for day in sorted(d):
                o, c, _ = d[day]
                if prev and o > 0 and abs(o / prev * 100 - 100) > ACTION_GAP:
                    action.setdefault(code, set()).add(day)
                prev = c
    return px, action


def load_signals():
    by_day = {}
    for p in sorted(glob.glob(os.path.join(RAW_DER, "*.csv"))):
        d = os.path.basename(p)[:-4]
        agg = {"C": {}, "P": {}}
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not r["underlying"] or not r["est_net_amt_twd"]:
                    continue
                a = agg[r["cp"]]
                a[r["underlying"]] = a.get(r["underlying"], 0.0) + float(r["est_net_amt_twd"])
        by_day[d] = agg
    return by_day


def main():
    sig = load_signals()
    if not sig:
        print("::notice::H5：沒有權證資料，跳過")
        return 4
    px, action = load_stocks()

    def clean(c, a, b):          # (a, b] 之間沒有公司行動
        return not any(a < x <= b for x in action.get(c, ()))
    cal = sorted({d for v in px.values() for d in v if d >= min(sig)})
    pos = {d: i for i, d in enumerate(cal)}
    # 每個 (訊號日, h) 的基準：T 日成交金額 ≥ 1 億的股票平均報酬
    bench, bench_dec = {}, {}
    events = []
    groups = {"C_top20": ("C", 0, TOPN), "C_21_40": ("C", TOPN, 2 * TOPN), "P_top20": ("P", 0, TOPN)}
    for T in sorted(sig):
        if T not in pos or pos[T] + 1 >= len(cal):
            continue
        E = cal[pos[T] + 1]
        liquid = [c for c, d in px.items() if T in d and d[T][2] >= MIN_WAN and E in d]
        order = sorted(liquid, key=lambda c: px[c][T][2])
        dec = {c: min(9, i * 10 // len(order)) for i, c in enumerate(order)} if order else {}
        for h in HS:
            if pos[E] + h - 1 >= len(cal):
                continue
            X = cal[pos[E] + h - 1]
            by = {}
            for c in liquid:
                if X in px[c] and px[c][E][0] > 0 and clean(c, E, X):
                    r = px[c][X][1] / px[c][E][0] * 100 - 100
                    by.setdefault(dec[c], []).append(r)
            rs = [r for v in by.values() for r in v]
            if rs:
                bench[(T, h)] = sum(rs) / len(rs)
                for k, v in by.items():
                    bench_dec[(T, h, k)] = sum(v) / len(v)
        liq = set(liquid)
        for g, (cp, a, b) in groups.items():
            ranked = sorted(((v, c) for c, v in sig[T][cp].items() if c in liq and v > 0), reverse=True)[a:b]
            for rank, (v, c) in enumerate(ranked, a + 1):
                d = px[c]
                ev = dict(group=g, T=T, E=E, code=c, rank=rank, est_amt=round(v),
                          gap=round(d[E][0] / d[T][1] * 100 - 100, 4) if d[T][1] > 0 else "")
                for h in HS:
                    if (T, h) not in bench:
                        continue
                    X = cal[pos[E] + h - 1]
                    if X in d and d[E][0] > 0 and clean(c, E, X):
                        r = d[X][1] / d[E][0] * 100 - 100
                        ev["r%d" % h] = round(r, 4)
                        ev["x%d" % h] = round(r - bench[(T, h)], 4)
                        if (T, h, dec[c]) in bench_dec:
                            ev["m%d" % h] = round(r - bench_dec[(T, h, dec[c])], 4)
                events.append(ev)

    lines = ["# H5 認購買超排行（事先登記見 warrant_study.py 開頭）", "",
             "訊號日 %s～%s；探索 ≤ %s，驗證之後。超額＝比同日成交金額 ≥ 1 億全部股票；t＝每日平均 Newey-West；淨＝扣 0.386%%。" % (
                 min(sig), max(sig), EXPLORE_END), "",
             "| 組 | 期間 | 持有 | 筆數 | 天數 | 原始 | 超額 | t | 淨 | 跳空 | 同規模超額 | t |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    verdict = None
    for g in groups:
        for per, lo, hi in (("探索", "", EXPLORE_END), ("驗證", EXPLORE_END + "~", "99999999")):
            ev = [e for e in events if e["group"] == g and (lo == "" or e["T"] > EXPLORE_END) and e["T"] <= hi]
            for h in HS:
                k, kx = "r%d" % h, "x%d" % h
                es = [e for e in ev if k in e]
                if not es:
                    continue
                byd = {}
                for e in es:
                    byd.setdefault(e["T"], []).append(e[kx])
                daily = [sum(v) / len(v) for _, v in sorted(byd.items())]
                raw = sum(e[k] for e in es) / len(es)
                exc = sum(daily) / len(daily)
                t = nw_t(daily, h)
                gaps = [e["gap"] for e in es if e["gap"] != ""]
                net = raw - COST
                bym = {}
                for e in es:
                    if "m%d" % h in e:
                        bym.setdefault(e["T"], []).append(e["m%d" % h])
                dm = [sum(v) / len(v) for _, v in sorted(bym.items())]
                mexc = sum(dm) / len(dm) if dm else None
                mt = nw_t(dm, h) if dm else None
                lines.append("| %s | %s | %d | %d | %d | %+.2f%% | %+.2f%% | %s | %+.2f%% | %+.2f%% | %s | %s |" % (
                    g, per, h, len(es), len(daily), raw, exc, "—" if t is None else "%+.2f" % t, net,
                    sum(gaps) / len(gaps) if gaps else 0,
                    "—" if mexc is None else "%+.2f%%" % mexc, "—" if mt is None else "%+.2f" % mt))
                if g == "C_top20" and per == "驗證" and h == MAIN_H:
                    ok = exc > 0 and t is not None and t >= 2 and net > 0
                    verdict = "通過" if ok else "沒通過"
                    print("::notice::H5 主要格（認購前 20、驗證期、10 日）：%s｜筆數 %d、天數 %d、超額 %+.2f%%、t %s、淨 %+.2f%%；同規模超額 %s、t %s" % (
                        verdict, len(es), len(daily), exc, "—" if t is None else "%+.2f" % t, net,
                        "—" if mexc is None else "%+.2f%%" % mexc, "—" if mt is None else "%+.2f" % mt))
    lines += ["", "主要格判定：%s" % (verdict or "樣本不足")]
    open("warrant_study_report.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    keys = (["group", "T", "E", "code", "rank", "est_amt", "gap"] + ["r%d" % h for h in HS] +
            ["x%d" % h for h in HS] + ["m%d" % h for h in HS])
    with open("warrant_h5_events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(events)
    # Actions 每一步最多顯示 10 個註記：每組併成一個
    for g in groups:
        rows = [l.replace("| " + g + " ", "").replace(" |", ";") for l in lines if l.startswith("| " + g + " ")]
        print("::notice::H5 %s（期間 持有 筆數 天數 原始 超額 t 淨 跳空 同規模超額 t）%s" % (g, " ".join(rows))[:3800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
