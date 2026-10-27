#!/usr/bin/env python3
"""權證資料兩個來源的核對指紋（2026-10-27）。只輸出筆數、加總、雜湊，不輸出原始資料。

  A：自營商以外的淨股數（≠0 的權證）：筆數、加總、sha（排序後「代號:淨股數」）
  B：有成交的權證（成交股數 > 0）：筆數、成交股數加總、成交金額加總、sha（「代號:股數:金額」）
  C：同 B，但只算淨股數 ≠ 0 的權證（H5 實際用到的）

用法：
  python warrant_check.py --derived 起日 迄日       讀 Actions 快取 data/warrant_raw/derived/（T86 權證類別＋MI_INDEX）
  python warrant_check.py --gpt <原始檔資料夾> 起日 迄日   讀 GPT 交來的 TWT43U＋MI_INDEX 原始 CSV（私人，不進 repo）
兩邊同一天的指紋一樣，就代表兩個來源算出的 H5 輸入一致。
非自營商淨股數＝−自營商買賣超股數（自行＋避險），兩個來源同一個定義。
"""
import csv, glob, hashlib, io, os, sys


def fp(nd, vam):
    a = sorted((c, v) for c, v in nd.items() if v)
    b = sorted((c, v, m) for c, (v, m) in vam.items() if v)
    ha = hashlib.sha256(";".join("%s:%d" % x for x in a).encode()).hexdigest()[:10]
    hb = hashlib.sha256(";".join("%s:%d:%d" % x for x in b).encode()).hexdigest()[:10]
    # C：只看淨股數 ≠ 0 的權證（H5 真正用到的）的成交股數、金額
    c = sorted((k, v, m) for k, (v, m) in vam.items() if v and nd.get(k))
    hc = hashlib.sha256(";".join("%s:%d:%d" % x for x in c).encode()).hexdigest()[:10]
    return "A %d %d %s｜B %d %d %d %s｜C %d %d %d %s" % (len(a), sum(v for _, v in a), ha,
                                         len(b), sum(v for _, v, _ in b), sum(m for _, _, m in b), hb,
                                         len(c), sum(v for _, v, _ in c), sum(m for _, _, m in c), hc)


def n(s):
    s = str(s).replace(",", "").replace('"', "").strip()
    if s in ("", "--", "-", "X"):
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def from_derived(d):
    p = os.path.join("data", "warrant_raw", "derived", d + ".csv")
    if not os.path.exists(p):
        return None
    nd, vam = {}, {}
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = r["warrant"].strip()
            nd[c] = n(r["nondealer_net_sh"]) or 0
            v, m = n(r["vol_sh"]), n(r["amt_twd"])
            if v:
                vam[c] = (v, m or 0)
    return fp(nd, vam)


def read_csv(path):
    raw = open(path, "rb").read()
    for enc in ("cp950", "utf-8-sig"):
        try:
            return list(csv.reader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    raise ValueError(path)


def from_gpt(folder, d):
    def find(pat):
        g = glob.glob(os.path.join(folder, pat))
        return g[0] if g else None
    t = find("TWT43U_%s*.csv" % d)
    mc, mp = find("MI_INDEX_%s_0999.csv" % d), find("MI_INDEX_%s_0999P.csv" % d)
    if not (t and mc and mp):
        return None
    vam, codes = {}, set()
    for path in (mc, mp):
        rows = read_csv(path)
        hdr = next(i for i, r in enumerate(rows) if "證券代號" in [x.strip() for x in r] and "成交股數" in [x.strip() for x in r])
        h = [x.strip() for x in rows[hdr]]
        jc, jv, ja = h.index("證券代號"), h.index("成交股數"), h.index("成交金額")
        for r in rows[hdr + 1:]:
            if len(r) <= ja:
                continue
            c = r[jc].strip().lstrip("=").strip('"')
            if not c:
                continue
            codes.add(c)
            v, m = n(r[jv]), n(r[ja])
            if v:
                vam[c] = (v, m or 0)
    rows = read_csv(t)
    hdr = next(i for i, r in enumerate(rows) if r and r[0].strip() == "證券代號")
    nd = {}
    for r in rows[hdr + 1:]:
        if len(r) < 11:
            continue
        c = r[0].strip().lstrip("=").strip('"')
        if c in codes:
            tot = n(r[10])                     # 自營商（合計）買賣超股數
            if tot is not None:
                nd[c] = -tot
    return fp(nd, vam)


def days_between(a, b, src):
    if src == "derived":
        ds = sorted(os.path.basename(p)[:8] for p in glob.glob(os.path.join("data", "warrant_raw", "derived", "*.csv")))
    else:
        ds = sorted({os.path.basename(p).split("_")[1][:8] for p in glob.glob(os.path.join(src, "TWT43U_*.csv"))})
    return [d for d in ds if a <= d <= b]


def main():
    args = sys.argv[1:]
    if args and args[0] == "--derived":
        a, b = args[1], args[2]
        lines = ["%s %s" % (d, from_derived(d)) for d in days_between(a, b, "derived")]
        for i in range(0, len(lines), 4):          # 一個註記放 4 天（一步最多 10 個註記）
            print("::notice title=權證核對指紋（T86）::" + " ‖ ".join(lines[i:i + 4]))
    elif args and args[0] == "--gpt":
        folder, a, b = args[1], args[2], args[3]
        for d in days_between(a, b, folder):
            print(d, from_gpt(folder, d))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
