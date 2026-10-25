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

# ---- 官方日行情母體核對（universe_official_check）----
import gzip, json
import universe_official_check as U
H = os.path.dirname(os.path.abspath(__file__))
tp = open(os.path.join(H, "testdata", "uni", "tpex_20210115.json"), "rb").read()
tw = open(os.path.join(H, "testdata", "uni", "twse_20171229.json"), "rb").read()
o = U.parse(tp, "tpex", "20210115")
assert o["5371"] == 111308887.0                     # GPT 09 官方樣本：5371 2021-01-15 成交 111,308,887 元
assert all(len(c) == 4 for c in o)
assert U.parse(tw, "twse", "20171229")["2325"] > 0
for bad_day, m, b in (("20210118", "tpex", tp), ("20171228", "twse", tw)):
    try:
        U.parse(b, m, bad_day); raise AssertionError("日期不符應該拒收")
    except ValueError:
        pass
try:
    U.parse(b"<html>x</html>", "twse", "20171229"); raise AssertionError
except ValueError:
    pass
# build：官方達標、本地沒有 → 列為本地缺
U.RAW = tempfile.mkdtemp()
gzip.open(os.path.join(U.RAW, "tpex_20210115.json.gz"), "wb").write(tp)
gzip.open(os.path.join(U.RAW, "twse_20210115.json.gz"), "wb").write(json.dumps(dict(stat="OK", date="20210115", tables=[dict(
    fields=["證券代號", "證券名稱", "成交金額"], data=[["2330", "台積電", "50,000,000,000"], ["1101", "台泥", "99,999,999"]])])).encode())
px = {"2330": {"20210115": (1, 1, 1, 5000000.0)}}
rows, miss, hit, cover = U.build({"20210115": ["202012_v11"]}, px, ["5371", "9999"])
assert cover == 1 and rows[0]["official_liquid"] == 120 and rows[0]["local_liquid"] == 1
assert any(m["code"] == "5371" and m["local_file"] == "no" and m["in_missing100"] == "yes" for m in miss)
assert "1101" not in {m["code"] for m in miss}      # 99,999,999 元 < 1 億
assert hit == {"5371": ["20210115"]}
print("universe_official 測試 6 項通過")
