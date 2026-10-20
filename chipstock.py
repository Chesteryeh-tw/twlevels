#!/usr/bin/env python3
"""把「一天一檔」的籌碼資料轉成「一檔股票一個檔」，給籌碼看板用。

輸入：data/chip/<日期>.txt  （code,fgn,inv,dlrSelf,dlrHedge,mgn,mgnPrev,shrt,shrtPrev）
      data/tdcc/<日期>.txt  （code,big,small,holders）  每週一期
      data/qfii/<日期>.txt  （code,ratio,held）         外資持股（上市）
輸出：data/chipstock/<代號>.txt
      # chip date,fgn,inv,dlrSelf,dlrHedge,mgn,shrt
      ...
      # tdcc date,big,small,holders
      ...
      # qfii date,ratio,held
單位：法人買賣超為「張」（可能有小數，原樣保留）；融資、融券餘額為張；集保為 %、人。
空值保留空白（= 未知），不補 0。
"""
import os, sys, glob

def main(data_dir, codes=None):
    chip = {}   # code -> list[(date, row)]
    for f in sorted(glob.glob(os.path.join(data_dir, "chip", "*.txt"))):
        d = os.path.basename(f)[:8]
        with open(f, encoding="utf-8") as fh:
            head = fh.readline().strip().split(",")
            ix = {k: i for i, k in enumerate(head)}
            for line in fh:
                p = line.rstrip("\n").split(",")
                c = p[0]
                if codes and c not in codes:
                    continue
                g = lambda k: p[ix[k]] if k in ix and ix[k] < len(p) else ""
                chip.setdefault(c, []).append(
                    (d, [g("fgn"), g("inv"), g("dlrSelf"), g("dlrHedge"), g("mgn"), g("shrt")]))
    tdcc = {}
    for f in sorted(glob.glob(os.path.join(data_dir, "tdcc", "*.txt"))):
        d = os.path.basename(f)[:8]
        with open(f, encoding="utf-8") as fh:
            fh.readline()
            for line in fh:
                p = line.rstrip("\n").split(",")
                if codes and p[0] not in codes:
                    continue
                tdcc.setdefault(p[0], []).append((d, p[1:4]))
    qfii = {}
    for f in sorted(glob.glob(os.path.join(data_dir, "qfii", "*.txt"))):
        d = os.path.basename(f)[:8]
        with open(f, encoding="utf-8") as fh:
            fh.readline()
            for line in fh:
                p = line.rstrip("\n").split(",")
                if codes and p[0] not in codes:
                    continue
                qfii.setdefault(p[0], []).append((d, p[1:3]))
    out = os.path.join(data_dir, "chipstock")
    os.makedirs(out, exist_ok=True)
    n = 0
    for c in sorted(set(chip) | set(tdcc) | set(qfii)):
        with open(os.path.join(out, c + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("# chip date,fgn,inv,dlrSelf,dlrHedge,mgn,shrt\n")
            for d, r in chip.get(c, []):
                fh.write(d + "," + ",".join(r) + "\n")
            fh.write("# tdcc date,big,small,holders\n")
            for d, r in tdcc.get(c, []):
                fh.write(d + "," + ",".join(r) + "\n")
            if c in qfii:
                fh.write("# qfii date,ratio,held\n")
                for d, r in qfii[c]:
                    fh.write(d + "," + ",".join(r) + "\n")
        n += 1
    print("chipstock: 寫出 %d 檔" % n)

def market_codes(data_dir):
    """只做網站上有的股票（data/market.txt），權證、債券等不做。"""
    with open(os.path.join(data_dir, "market.txt"), encoding="utf-8") as fh:
        fh.readline()
        return {l.split(",", 1)[0] for l in fh if l.strip()}

if __name__ == "__main__":
    main(sys.argv[1], set(sys.argv[2:]) or market_codes(sys.argv[1]))
