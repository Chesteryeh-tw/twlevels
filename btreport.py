#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讀 backtest.json，產生一頁看得懂的回測報告 backtest.html。"""

import json
import os

HOR = ["1", "5", "10", "20"]
CONTROL = {"rnd10", "rnd30", "rnd60"}

CSS = """
:root{--bg:#E9EDEF;--surface:#fff;--surface2:#F3F6F7;--sunk:#DFE6EA;--text:#121A1F;
 --muted:#5C6E79;--line:#CFD9DF;--line2:#E4EAEE;--accent:#155E7E;
 --res:#C43A31;--res-bg:#F9E4E1;--mid:#9E7106;--mid-bg:#FAEFD6;--sup:#0C8A61;--sup-bg:#DAF2E7;
 --warn:#8E5400;--warn-bg:#FAECD6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --bg:#0A1015;--surface:#131C22;--surface2:#17222A;--sunk:#0E161B;--text:#E1EAF0;
 --muted:#8AA1AE;--line:#24323C;--line2:#1B262E;--accent:#5DBEE3;
 --res:#FF7C6C;--res-bg:#331A1A;--mid:#EFC257;--mid-bg:#322913;--sup:#43CF91;--sup-bg:#0E2E23;
 --warn:#EFB55A;--warn-bg:#312512}}
:root[data-theme="dark"]{--bg:#0A1015;--surface:#131C22;--surface2:#17222A;--sunk:#0E161B;
 --text:#E1EAF0;--muted:#8AA1AE;--line:#24323C;--line2:#1B262E;--accent:#5DBEE3;
 --res:#FF7C6C;--res-bg:#331A1A;--mid:#EFC257;--mid-bg:#322913;--sup:#43CF91;--sup-bg:#0E2E23;
 --warn:#EFB55A;--warn-bg:#312512}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);padding:24px 16px 60px;
 font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.7}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;letter-spacing:.02em}
h2{font-size:16px;margin:30px 0 10px;padding-top:18px;border-top:1px solid var(--line2)}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:0 0 18px}
.note{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;
 padding:11px 14px;font-size:13px;margin:0 0 18px}
.note b{color:inherit}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:right;font-size:11px;letter-spacing:.05em;color:var(--muted);font-weight:500;
 padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th.l,td.l{text-align:left}
td{padding:8px;border-bottom:1px solid var(--line2);text-align:right;
 font-variant-numeric:tabular-nums;white-space:nowrap}
tr.ctl td{color:var(--muted);background:var(--surface2)}
tr.ctl td.l::before{content:"對照　";font-size:11px;color:var(--muted)}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace}
.good{color:var(--sup)} .bad{color:var(--res)} .dim{color:var(--muted);opacity:.7}
.t{font-size:11px;padding:1px 5px;border-radius:3px;border:1px solid transparent}
.t.on{border-color:var(--accent);color:var(--accent)}
.t.off{color:var(--muted)}
.side{font-size:11px;border:1px solid var(--line);border-radius:3px;padding:0 5px;color:var(--muted)}
.bars{display:flex;gap:2px;align-items:center;height:26px}
.bars i{display:block;width:9px;border-radius:1px}
.lab{font-size:11px;color:var(--muted);margin-left:8px}
.legend{font-size:12px;color:var(--muted);margin:6px 0 0}
ul{margin:8px 0 0;padding-left:20px;font-size:13.5px}
li{margin-bottom:5px}
code{background:var(--sunk);border-radius:3px;padding:0 4px;font-size:12.5px}
.scroll{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
@media (max-width:760px){
 body{padding:16px 12px 50px;overflow-x:hidden}
 .wrap{max-width:100%}
 .scroll table{min-width:760px}
 .benchtab td{white-space:normal}
 .benchtab{display:block}
 .benchtab tr{display:flex;flex-wrap:wrap;gap:4px 18px}}
"""


def cls(v, side):
    if v is None:
        return "dim"
    x = v if side != "空" else -v
    return "good" if x > 0.05 else ("bad" if x < -0.05 else "dim")


def bars(monthly, side):
    """長條的正負一律換算成「照這個方向做賺不賺」，所以空方要翻過來。"""
    if not monthly:
        return ""
    sign = -1 if side == "空" else 1
    vals = [v * sign for v in monthly.values()]
    mx = max(abs(v) for v in vals) or 1
    out = []
    for (m, raw), v in zip(monthly.items(), vals):
        h = max(2, round(abs(v) / mx * 22))
        top = 13 - h if v > 0 else 13
        col = "var(--sup)" if v > 0 else "var(--res)"
        out.append('<i style="height:%dpx;margin-top:%dpx;background:%s" title="%s 做%s %+.2f%%"></i>'
                   % (h, top, col, m[:4] + "-" + m[4:], side, v))
    return '<div class="bars">' + "".join(out) + "</div>"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "backtest.json"), encoding="utf-8"))
    rows = d["rows"]
    bench = {h: (sum(d["bench"][h]) / len(d["bench"][h])) for h in HOR if d["bench"].get(h)}

    ctl = [r for r in rows if r["key"] in CONTROL]
    floor = {}
    for h in HOR:
        ts = [abs(r["h"][h]["t"]) for r in ctl if r["h"].get(h) and r["h"][h]["t"] is not None]
        floor[h] = max(ts) if ts else None

    body = []
    body.append('<div class="wrap">')
    body.append("<h1>篩選情境回測</h1>")
    body.append('<p class="sub">樣本期間 %s ~ %s，共 %d 個交易日。'
                '進場價＝訊號隔天的開盤，出場＝第 N 個交易日的收盤。</p>'
                % (d["from"][:4] + "-" + d["from"][4:6] + "-" + d["from"][6:],
                   d["to"][:4] + "-" + d["to"][4:6] + "-" + d["to"][6:], d["days"]))

    body.append('<div class="note"><b>先看這裡再看數字。</b>'
                '這份是「這些條件在這一年多的台股長什麼樣」，不是「這些條件會賺錢」。'
                '樣本只有一年多、一種盤，而且沒有還原除權息（除息日會被當成下跌）、'
                '沒有算手續費與交易稅、沒有考慮流動性與滑價。'
                '最底下三列是<b>隨機挑股的對照組</b> —— 它們的數字就是這套量測方法的雜訊底線，'
                '任何一個情境如果沒有明顯超過對照組，就等於什麼都沒測出來。</div>')

    body.append('<div class="card"><div class="scroll"><table>')
    head = '<tr><th class="l">情境</th><th>方向</th><th>平均檔數</th><th>天數</th>'
    for h in HOR:
        head += '<th>%s 天<br>超額</th><th>贏過<br>中位數</th><th>t</th>' % h
    head += '<th class="l">逐月超額（20 天）</th></tr>'
    body.append(head)

    for r in rows:
        is_ctl = r["key"] in CONTROL
        tr = '<tr class="ctl">' if is_ctl else "<tr>"
        tr += '<td class="l">%s</td><td><span class="side">%s</span></td>' % (r["name"], r["side"])
        tr += '<td class="num">%.1f</td><td class="num">%d</td>' % (r["avg_picks"], r["days"])
        for h in HOR:
            x = r["h"].get(h)
            if not x:
                tr += '<td class="dim">—</td><td class="dim">—</td><td class="dim">—</td>'
                continue
            sign = -1 if r["side"] == "空" else 1
            ex = sign * x["excess"]
            beat = x["beat"] if sign > 0 else 100 - x["beat"]
            tv = x["t"]
            tvs = sign * tv if tv is not None else None
            strong = (tvs is not None and floor.get(h) is not None
                      and abs(tvs) > max(2.0, floor[h]) and not is_ctl)
            tr += '<td class="num %s">%+.2f%%</td>' % (cls(x["excess"], r["side"]), ex)
            tr += '<td class="num %s">%.1f%%</td>' % ("" if is_ctl else cls(beat - 50, "多"), beat)
            tr += '<td><span class="t %s">%s</span></td>' % (
                "on" if strong else "off",
                ("%+.1f" % tvs) if tvs is not None else "—")
        m = r["h"].get("20") or {}
        tr += '<td class="l">%s</td>' % bars(m.get("monthly") or {}, r["side"])
        tr += "</tr>"
        body.append(tr)
    body.append("</table></div>")
    body.append('<p class="legend">「超額」＝照該方向做的報酬減掉當天全市場（成交金額 ≥ 1 億）的平均報酬。'
                '空方的數字已經換算成做空的角度，正數代表做空有賺。'
                '「贏過中位數」隨機挑股本來就是 50%，超過才有意義。'
                't 值已修正持有期重疊造成的灌水；框起來的表示它同時大於 2、也大於對照組的雜訊底線。'
                '逐月長條是 20 天持有的每月平均超額（綠＝該方向有賺、紅＝賠），用來看是不是只靠某一兩個月。</p>')
    body.append("</div>")

    # ── 拆解測試
    if d.get("rule_rows"):
        rmap = {r["key"]: r for r in d["rule_rows"]}
        body.append("<h2>拆解測試：一次只動一個條件</h2>")
        body.append('<p class="sub">上面那張表每個情境都是好幾條疊在一起，測出來好或不好'
                    '不知道是哪一條的功勞。這裡一次只換一個條件、其他都一樣，'
                    '差異才歸得到那一條身上。看的是同一組裡上下兩列差多少，不是絕對值。</p>')
        for g in d["rule_groups"]:
            body.append('<div class="card"><h3 style="font-size:14px;margin:0 0 3px">%s</h3>'
                        '<p class="legend" style="margin:0 0 10px">%s</p>'
                        '<div class="scroll"><table>' % (g["name"], g["why"]))
            head = '<tr><th class="l">條件</th><th>方向</th><th>平均檔數</th>'
            for h in HOR:
                head += '<th>%s 天<br>超額</th><th>贏過<br>中位數</th><th>t</th>' % h
            body.append(head + "</tr>")
            for key in g["keys"]:
                r = rmap.get(key)
                if not r:
                    continue
                is_ctl = key == "rndR"
                tr = '<tr class="ctl">' if is_ctl else "<tr>"
                tr += '<td class="l">%s</td><td><span class="side">%s</span></td>' % (
                    r["name"], r["side"])
                tr += '<td class="num">%.1f</td>' % r["avg_picks"]
                for h in HOR:
                    x = r["h"].get(h)
                    if not x:
                        tr += '<td class="dim">—</td><td class="dim">—</td><td class="dim">—</td>'
                        continue
                    sign = -1 if r["side"] == "空" else 1
                    ex = sign * x["excess"]
                    beat = x["beat"] if sign > 0 else 100 - x["beat"]
                    tv = x["t"]
                    tvs = sign * tv if tv is not None else None
                    strong = (tvs is not None and abs(tvs) > 2.0 and not is_ctl)
                    tr += '<td class="num %s">%+.2f%%</td>' % (cls(x["excess"], r["side"]), ex)
                    tr += '<td class="num %s">%.1f%%</td>' % (
                        "" if is_ctl else cls(beat - 50, "多"), beat)
                    tr += '<td><span class="t %s">%s</span></td>' % (
                        "on" if strong else "off",
                        ("%+.1f" % tvs) if tvs is not None else "—")
                body.append(tr + "</tr>")
            body.append("</table></div></div>")

    body.append('<h2>基準：這段期間市場本身漲多少</h2><div class="card"><table class="benchtab"><tr>')
    for h in HOR:
        if h in bench:
            body.append('<td class="l">持有 %s 天　<b class="num">%+.2f%%</b></td>' % (h, bench[h]))
    body.append("</tr></table>"
                '<p class="legend">全市場（成交金額 ≥ 1 億）的平均報酬。'
                '這段期間大盤是往上的，所以任何做多的情境本來就會賺，'
                '一定要減掉這個才知道是條件有用還是單純跟著大盤漲。</p></div>')

    body.append("<h2>測不到的部分</h2><div class=\"card\"><ul>"
                "<li><b>集保大戶、散戶、戶數</b>：集保的歷史資料只留最近一期，本站也只存到一期，"
                "所以「大戶吃貨散戶離場」「開布林＋主力確認」這兩個情境完全沒辦法回測。</li>"
                "<li><b>券商分點主力買賣超</b>：本來就拿不到，講義多方四大條件裡的「主力買」一直是缺的。</li>"
                "<li><b>乖離年線</b>要 240 天均線，資料的前 239 天算不出來，"
                "所以「高檔出貨紅K」「價背離空」只剩 9 天可測 —— 那兩列的數字不要當結論看。</li>"
                "<li><b>當沖與盤中訊號</b>：只有日線收盤資料，講義裡「買盤竭盡」「漲停打開」"
                "這類要看盤中逐筆的，這裡一概測不到。</li></ul></div>")
    body.append("</div>")

    html = ("<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>篩選情境回測</title><style>%s</style></head><body>%s</body></html>"
            % (CSS, "".join(body)))
    out = os.path.join(here, "backtest.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已寫入 %s（%d KB）" % (out, os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
