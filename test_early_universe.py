import os, tempfile
import early_universe_check as E

def mk(d, code, rows):
    with open(os.path.join(d, code + ".txt"), "w") as f:
        f.write("# date,open,high,low,close,lot,wan\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")

def row(d, wan):
    return (d, 10, 10, 10, 10, 1, wan)

d = tempfile.mkdtemp()
mk(d, "1111", [row("20200601", 50000)])                       # 2021 前就結束
mk(d, "2222", [row("20210105", 9999), row("20250101", 100)])    # 有價量但從未達 1 億
mk(d, "3333", [row("20210105", 10000), row("20210106", 20000), row("20250101", 5)])
mk(d, "4444", [row("20240102", 99999)])
codes = [("sii", c, c) for c in ("1111", "2222", "3333", "4444", "5555")]
rows = {r["code"]: r for r in E.run(codes, d, {"3333": {"202101": 1}, "4444": {}})}
assert rows["1111"]["category"] == "ended_before_2021"
assert rows["2222"]["category"] == "never_liquid"          # 9999 萬 < 1 億
assert rows["3333"]["category"] == "liquid_with_panel"
assert rows["3333"]["liquid_days"] == 2 and rows["3333"]["liquid_months"] == 1 and rows["3333"]["liquid_months_in_early_window"] == 1
assert rows["4444"]["category"] == "liquid_no_panel" and rows["4444"]["liquid_months_in_early_window"] == 0
assert rows["5555"]["category"] == "no_price_history"
assert "liquid_no_panel" in E.report(list(rows.values()), 4, 1)
print("early_universe 測試 6 項通過")
