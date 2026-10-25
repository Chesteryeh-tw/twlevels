#!/usr/bin/env python3
"""缺碼原因診斷（不連網、不算 SUS）：營收原始檔與長期價量是不是只收「出表當時還在的公司」。

輸出 early_check/universe_diag.md：只有代碼、日期、家數，不含營收金額與逐日價量。
"""
import csv, glob, io, os, sys
from collections import Counter, defaultdict

REPO_START = "20250814"
FIVE = ["2809", "2867", "3454", "4130", "5371"]


def market_codes():
    out = {}
    with open(os.path.join("data", "market.txt"), encoding="utf-8") as f:
        f.readline()
        for l in f:
            p = l.split(",")
            if p and p[0]:
                out[p[0]] = p[1]
    return out


def raw_rows():
    """回傳 (market, ym, generated, code) 逐列；讀 data/revenue_raw/{sii,otc}/*.csv 原檔。"""
    for mkt in ("sii", "otc"):
        for f in sorted(glob.glob(os.path.join("data", "revenue_raw", mkt, "*.csv"))):
            b = open(f, "rb").read().decode("utf-8-sig", "replace")
            rd = csv.reader(io.StringIO(b))
            next(rd, None)
            for r in rd:
                if len(r) >= 3:
                    yield mkt, os.path.basename(f), r[0].strip(), r[1].strip(), r[2].strip()


def price_first():
    out = {}
    for f in glob.glob(os.path.join("data", "stock", "*.txt")):
        d = [l.split(",")[0] for l in open(f, encoding="utf-8") if l[:1].isdigit()]
        if d:
            out[os.path.basename(f)[:-4]] = (min(d), max(d), len(d))
    return out


def main():
    mk = market_codes()
    pf = price_first()
    by_code = defaultdict(set)
    gen_by_file = defaultdict(Counter)
    ym_codes = defaultdict(set)
    raw_hit = defaultdict(list)
    for mkt, fn, gen, ym, code in raw_rows():
        by_code[code].add(ym)
        gen_by_file[(mkt, fn)][gen] += 1
        ym_codes[(mkt, ym)].add(code)
    for mkt in ("sii", "otc"):
        for f in sorted(glob.glob(os.path.join("data", "revenue_raw", mkt, "*.csv"))):
            b = open(f, "rb").read()
            for c in FIVE:
                if (",%s," % c).encode() in b or ('"%s"' % c).encode() in b:
                    raw_hit[c].append("%s/%s" % (mkt, os.path.basename(f)))
    rev_codes = set(by_code)
    not_now = sorted(c for c in rev_codes if c not in mk)
    L = ["# 缺碼原因診斷（不連網；只有代碼、日期、家數）", ""]
    gens = Counter()
    for k, cnt in gen_by_file.items():
        gens[min(cnt)] += 1
    L += ["## 1. 營收原始檔的出表日期", "",
          "原始檔 %d 份；每份最早出表日期的分布（民國年/月/日）：" % len(gen_by_file), ""]
    for g, n in sorted(gens.items()):
        L.append("- %s：%d 份" % (g, n))
    L += ["", "## 2. 營收原始檔裡有、現行市場清單（market.txt，%d 檔）沒有的代碼：%d 個" % (len(mk), len(not_now)), "",
          "前 60 個：" + ", ".join(not_now[:60]), ""]
    L += ["## 3. 五檔（現在交易中但營收表一筆都沒有）", "", "| 代碼 | 在現行市場清單 | 原始檔逐字搜尋命中 | 價量起訖 |", "|---|---|---|---|"]
    for c in FIVE:
        p = pf.get(c)
        L.append("| %s | %s | %s | %s |" % (c, "是" if c in mk else "否", "；".join(raw_hit[c][:5]) or "無", "%s～%s" % (p[0], p[1]) if p else "無"))
    short = sorted(c for c, v in pf.items() if v[0] > "20210201")
    cls = Counter()
    rows = []
    for c in short:
        yms = sorted(by_code.get(c, ()), key=lambda x: tuple(int(t) for t in x.split("/")))
        rf = yms[0] if yms else ""
        if c not in mk:
            k = "不在現行市場清單（近期下市／改碼）"
        elif pf[c][0] == REPO_START:
            k = "價量從 repo 每日資料起點才有（長期歷史完全沒補到）"
        else:
            k = "價量 2021-02 之後才開始（多為新上市櫃；未逐檔核掛牌日）"
        cls[k] += 1
        rows.append((k, c, mk.get(c, ""), pf[c][0], rf))
    L += ["", "## 4. 價量歷史比 2021-02 短的 %d 個代碼" % len(short), "", "| 原因推測 | 個數 |", "|---|---:|"]
    for k, n in cls.most_common():
        L.append("| %s | %d |" % (k, n))
    sus = [r for r in rows if r[0].startswith("價量從 repo")]
    L += ["", "長期歷史完全沒補到的代碼（全部 %d 個）：" % len(sus), "", "| 代碼 | 名稱 | 價量起 | 營收最早月（民國） |", "|---|---|---|---|"]
    for r in sus:
        L.append("| %s | %s | %s | %s |" % (r[1], r[2], r[3], r[4]))
    # 每月家數：若原始檔只收現存公司，越早的月份家數應該越少且不含下市公司
    L += ["", "## 5. 每年 1 月營收原始檔的家數（只收現存公司時，早年會偏少）", ""]
    for mkt in ("sii", "otc"):
        L.append("- %s：" % mkt + "、".join("%s %d" % (ym, len(v)) for (m, ym), v in sorted(ym_codes.items(), key=lambda x: (x[0][0], int(x[0][1].split("/")[0]))) if m == mkt and ym.endswith("/1")))
    md = "\n".join(L) + "\n"
    open(os.path.join("early_check", "universe_diag.md"), "w", encoding="utf-8").write(md)
    print("::notice::診斷：營收原始檔 %d 份、不在現行清單的營收代碼 %d 個、短價量 %d 個 %s" % (len(gen_by_file), len(not_now), len(short), dict(cls)))
    print(md)


if __name__ == "__main__":
    main()
