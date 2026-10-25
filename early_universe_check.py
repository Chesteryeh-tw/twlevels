#!/usr/bin/env python3
"""早年營收缺碼 vs 研究母體（GPT 07 第 3 節）：只看範圍與資料可用性，不算 SUS、不看績效、不連網。

輸入：early_check/missing_in_early_revenue_20171229.csv（官方 2017/12/29 日行情有、2017-12 兩份營收原檔都沒有的 100 個代碼）
      data/stock（2021 起的長期價量，Actions 快取 finmind-hist）、data/revenue 營收 panel（2020-01 起）
輸出：early_check/universe_check.csv、universe_check.md；只有代碼、日期、次數，不含營收金額或逐日價量。

分類（每個代碼只落一類）：
  no_price_history   長期價量裡沒有這個代碼（多半已下市、合併、改碼；無法證明 2021 後是否可交易）
  ended_before_2021  最後一個價格日在 2021-01-01 之前，不會進場
  never_liquid       有價量，但 2021-01 起沒有任何一天成交金額 ≥ 1 億（萬元欄 ≥ 10000）→ 不進母體
  liquid_with_panel  進得了母體，2020 起的營收表有這個代碼（早年缺口只影響最前面的月份）
  liquid_no_panel    進得了母體，但 2020 起的營收表也沒有 → 實際會缺營收，要補或明講排除
"""
import csv, datetime as dt, glob, os, sys

LIQ_WAN = 10000          # 1 億 = 10000 萬元
FROM = "20210101"
EARLY_END = "20230131"   # 早年檔（2017-12～2019-12）只影響 SUS 月 2020-12～2022-12，對應進場約 2021-01～2023-01


def load_codes(path):
    return [(r["market"], r["code"], r["name"]) for r in csv.DictReader(open(path, encoding="utf-8-sig"))]


def price_stats(path):
    first = last = None
    liq_months, liq_months_early, liq_days = set(), set(), 0
    for l in open(path, encoding="utf-8"):
        if not l[:1].isdigit():
            continue
        p = l.strip().split(",")
        if len(p) < 7:
            continue
        d = p[0]
        first = d if first is None or d < first else first
        last = d if last is None or d > last else last
        try:
            wan = float(p[6] or 0)
        except ValueError:
            continue
        if d >= FROM and wan >= LIQ_WAN:
            liq_days += 1
            liq_months.add(d[:6])
            if d <= EARLY_END:
                liq_months_early.add(d[:6])
    return dict(first=first, last=last, liq_days=liq_days, liq_months=len(liq_months), liq_months_early=len(liq_months_early))


def classify(ps, n_rev):
    if ps is None:
        return "no_price_history"
    if ps["last"] < FROM:
        return "ended_before_2021"
    if ps["liq_days"] == 0:
        return "never_liquid"
    return "liquid_with_panel" if n_rev > 0 else "liquid_no_panel"


def run(codes, stock_dir, rev):
    out = []
    for mk, c, nm in codes:
        f = os.path.join(stock_dir, c + ".txt")
        ps = price_stats(f) if os.path.exists(f) else None
        yms = sorted(rev.get(c, {}))
        cat = classify(ps, len(yms))
        out.append(dict(market=mk, code=c, name=nm, category=cat,
                        price_first=ps["first"] if ps else "", price_last=ps["last"] if ps else "",
                        liquid_days=ps["liq_days"] if ps else 0, liquid_months=ps["liq_months"] if ps else 0,
                        liquid_months_in_early_window=ps["liq_months_early"] if ps else 0,
                        rev_months=len(yms), rev_first=yms[0] if yms else "", rev_last=yms[-1] if yms else ""))
    return out


def report(rows, n_codes_hist, n_panel):
    cnt = {}
    for r in rows:
        cnt[r["category"]] = cnt.get(r["category"], 0) + 1
    L = ["# 早年營收缺碼 vs 研究母體（範圍與可用性，沒有看 SUS 排名或績效）", "",
         "缺碼 %d 個（官方 2017/12/29 日行情有、2017-12 兩份營收原檔都沒有）；長期價量檔 %d 個代碼；2020 起營收表 %d 個代碼。" % (len(rows), n_codes_hist, n_panel), "",
         "| 分類 | 代碼數 | 意思 |", "|---|---:|---|"]
    meaning = dict(no_price_history="長期價量沒有此代碼：無法證明 2021 後可交易（多為下市、合併、改碼；長期價量只收現在還在市場上的代碼，這是已知的存活偏誤）",
                   ended_before_2021="2021 前已無價格，不影響研究",
                   never_liquid="2021 起從未達成交額 1 億，不進母體",
                   liquid_with_panel="進得了母體，2020 起營收表有此代碼；早年缺口只影響最前面的月份",
                   liquid_no_panel="進得了母體，但 2020 起營收表也沒有：實際缺營收")
    for k in ("no_price_history", "ended_before_2021", "never_liquid", "liquid_with_panel", "liquid_no_panel"):
        L.append("| %s | %d | %s |" % (k, cnt.get(k, 0), meaning[k]))
    imp = [r for r in rows if r["category"] in ("liquid_with_panel", "liquid_no_panel")]
    L += ["", "## 進得了母體的代碼（%d 個）" % len(imp), "", "| 市場 | 代碼 | 名稱 | 分類 | 達標月數 | 其中早年窗口(≤2023-01) | 營收月數 | 營收起訖 |", "|---|---|---|---|---:|---:|---:|---|"]
    for r in imp:
        L.append("| %s | %s | %s | %s | %d | %d | %d | %s～%s |" % (r["market"], r["code"], r["name"], r["category"], r["liquid_months"], r["liquid_months_in_early_window"], r["rev_months"], r["rev_first"], r["rev_last"]))
    L += ["", "限制：看的是代碼是否有價量／營收，不證明新舊代碼、合併是否同一家公司；沒有價量的代碼不能說它「當時沒交易」。"]
    return "\n".join(L) + "\n"


def main():
    codes = load_codes(os.path.join("early_check", "missing_in_early_revenue_20171229.csv"))
    import revenue_panel
    pp, _mp, how = revenue_panel.resolve(os.path.join("data", "revenue"))
    if not pp:
        print("沒有營收 panel，先跑 revenue_fetch.py --panel-only")
        sys.exit(1)
    rev = {}
    for r in csv.DictReader(open(pp, encoding="utf-8")):
        rev.setdefault(r["code"], {})[r["ym"]] = 1
    stock = os.path.join("data", "stock")
    files = glob.glob(os.path.join(stock, "*.txt"))
    # 長期價量必須真的是 2021 起，否則分類會全錯
    ex = min((l for l in open(files[0], encoding="utf-8") if l[:1].isdigit()), default="99999999,").split(",")[0]
    print("長期價量樣本檔最早日期 %s（%s）" % (ex, os.path.basename(files[0])))
    rows = run(codes, stock, rev)
    with open(os.path.join("early_check", "universe_check.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    md = report(rows, len(files), len(rev))
    open(os.path.join("early_check", "universe_check.md"), "w", encoding="utf-8").write(md)
    cnt = {}
    for r in rows:
        cnt[r["category"]] = cnt.get(r["category"], 0) + 1
    print("::notice::早年缺碼核對（%s）：%s" % (how, cnt))
    print(md)


if __name__ == "__main__":
    main()
