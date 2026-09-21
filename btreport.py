#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讀 backtest.json，產生一頁看得懂的回測報告 backtest.html。

設計原則
  * 畫面上每個數字都要能自己解釋自己。說明放在表格「上面」，不是底下 ——
    手機上底下那段根本滑不到。
  * 顏色照台股慣例：紅＝賺、綠＝賠（跟券商未實現損益一致）。
    但要講明「紅代表照這個方向做有賺」，不是「股價漲」，不然空方會看反。
  * 每個情境自動貼一個時間尺度標籤（隔日／一週／兩週／波段），
    標籤不是我主觀認定，是從回測資料推出來的 —— 哪個持有期的 t 撐得住、
    原始報酬又蓋得過交易成本，就標哪個。
  * 逐月長條跟著標籤的持有期走。以前固定畫 20 天，
    害「高檔出貨紅K」這種週級別的條件被畫成它最爛的那一欄。
  * 窄螢幕不要橫向捲十幾欄的表，改成一個情境一張卡。
"""

import json
import os

HOR = ["1", "5", "10", "20"]
GAP = "g"
CONTROL = {"rnd10", "rnd30", "rnd60"}

# 來回交易成本（％），只拿來在報告裡標「扣完還剩多少」，**不當標籤的判準**。
#   當沖（1 天＝隔天開盤進、隔天收盤出）：手續費 3 折來回 0.0855% + 當沖證交稅 0.15%
#   隔夜以上：手續費 3 折來回 0.0855% + 證交稅 0.3%
# 折數因券商而異，這裡寫死一組合理值。一個條件成不成立是統計問題，
# 不該取決於誰的手續費折數 —— 所以成本只出現在說明，不出現在判準裡。
COST = {"1": 0.235, "5": 0.386, "10": 0.386, "20": 0.386}

# 持有期 → 人話
SCALE = {"1": "隔日", "5": "一週", "10": "兩週", "20": "波段"}

# 布林位階的四個分區，由強到弱。狀態轉移表用。
ST = ["貼上軌", "上半部", "下半部", "貼下軌"]
ST_COL = {"貼上軌": "var(--gain)", "上半部": "var(--gain)",
          "下半部": "var(--loss)", "貼下軌": "var(--loss)"}
ST_OP = {"貼上軌": "1", "上半部": ".45", "下半部": ".45", "貼下軌": "1"}

CSS = """
:root{--bg:#E9EDEF;--surface:#fff;--surface2:#F3F6F7;--sunk:#DFE6EA;--text:#121A1F;
 --muted:#5C6E79;--line:#CFD9DF;--line2:#E4EAEE;--accent:#155E7E;
 --red:#C43A31;--red-bg:#F9E4E1;--mid:#9E7106;--mid-bg:#FAEFD6;--green:#0C8A61;--green-bg:#DAF2E7;
 --warn:#8E5400;--warn-bg:#FAECD6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --bg:#0A1015;--surface:#131C22;--surface2:#17222A;--sunk:#0E161B;--text:#E1EAF0;
 --muted:#8AA1AE;--line:#24323C;--line2:#1B262E;--accent:#5DBEE3;
 --red:#FF7C6C;--red-bg:#331A1A;--mid:#EFC257;--mid-bg:#322913;--green:#43CF91;--green-bg:#0E2E23;
 --warn:#EFB55A;--warn-bg:#312512}}
:root[data-theme="dark"]{--bg:#0A1015;--surface:#131C22;--surface2:#17222A;--sunk:#0E161B;
 --text:#E1EAF0;--muted:#8AA1AE;--line:#24323C;--line2:#1B262E;--accent:#5DBEE3;
 --red:#FF7C6C;--red-bg:#331A1A;--mid:#EFC257;--mid-bg:#322913;--green:#43CF91;--green-bg:#0E2E23;
 --warn:#EFB55A;--warn-bg:#312512}

/* 台股慣例：賺＝紅、賠＝綠。所有損益數字都走這兩個色票，不要直接用 red/green。 */
:root{--gain:var(--red);--loss:var(--green)}

*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);padding:24px 16px 60px;
 font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.7}
.wrap{max-width:1280px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px;letter-spacing:.02em}
h2{font-size:16px;margin:30px 0 10px;padding-top:18px;border-top:1px solid var(--line2)}
h3{font-size:14px;margin:0 0 3px}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:0 0 18px}
.note{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;
 padding:11px 14px;font-size:13px;margin:0 0 18px}
.note b{color:inherit}

/* ── 怎麼看這張表 ───────────────────────────── */
.howto{background:var(--surface);border:1px solid var(--accent);border-radius:10px;
 padding:14px 18px 16px;margin:0 0 18px}
.howto h2{font-size:15px;margin:0 0 10px;padding:0;border:0}
.howto dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:9px 14px;align-items:baseline}
.howto dt{font-size:12.5px;font-weight:600;white-space:nowrap;color:var(--text)}
.howto dd{margin:0;font-size:13px;color:var(--muted);line-height:1.6}
.howto dd b{color:var(--text);font-weight:600}
.sw{display:inline-block;font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;
 padding:0 5px;border-radius:3px;border:1px solid currentColor;white-space:nowrap}
.sw.up{color:var(--gain)} .sw.dn{color:var(--loss)}

table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:right;font-size:11px;letter-spacing:.05em;color:var(--muted);font-weight:500;
 padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:bottom}
th.l,td.l{text-align:left}
th.grp{text-align:center;border-bottom:1px solid var(--line2);padding-bottom:3px;
 font-size:11.5px;color:var(--text);font-weight:600}
td{padding:7px 8px;border-bottom:1px solid var(--line2);text-align:right;
 font-variant-numeric:tabular-nums;white-space:nowrap;vertical-align:top}
tr.ctl td{color:var(--muted);background:var(--surface2)}
tr.ctl td.l::before{content:"對照　";font-size:11px;color:var(--muted)}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace}
.good{color:var(--gain)} .bad{color:var(--loss)} .dim{color:var(--muted);opacity:.7}
.sub2{display:block;font-size:10.5px;color:var(--muted);opacity:.85;font-weight:400;line-height:1.4}
.t{font-size:11px;padding:1px 5px;border-radius:3px;border:1px solid transparent}
.t.on{border-color:var(--accent);color:var(--accent)}
.t.off{color:var(--muted)}
.side{font-size:11px;border:1px solid var(--line);border-radius:3px;padding:0 5px;color:var(--muted)}

/* ── 自動標籤 ──────────────────────────────── */
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:11px;white-space:nowrap;
 border:1px solid currentColor;font-weight:600}
.tag.up{color:var(--gain)} .tag.dn{color:var(--loss)}
.tag.no{color:var(--muted);border-color:var(--line);font-weight:400}
.tag.warn{color:var(--warn);border-color:var(--warn)}
.net{display:inline-block;font-size:11px;margin-left:5px;white-space:nowrap;
 font-family:"IBM Plex Mono",ui-monospace,monospace}

/* ── 逐月長條 ───────────────────────────────── */
.bars{position:relative;display:flex;gap:2px;align-items:flex-start;height:28px;padding-top:1px;width:max-content}
.bars .zero{position:absolute;left:0;right:0;top:14px;height:1px;background:var(--line);opacity:.8}
.bars i{display:block;width:9px;border-radius:1px;position:relative;z-index:1}
/* ── 狀態轉移條 ─────────────────────────────── */
.stbar{display:flex;height:14px;width:150px;border-radius:3px;overflow:hidden;
 border:1px solid var(--line2)}
.stbar i{display:block;height:100%}
.stcap{font-size:10px;color:var(--muted);margin-top:2px;line-height:1.3}
.stkey{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin-top:8px}
.stkey span{display:inline-flex;align-items:center;gap:4px}
.stkey i{width:11px;height:11px;border-radius:2px;display:inline-block}
.legend{font-size:12px;color:var(--muted);margin:8px 0 0;line-height:1.6}
ul{margin:8px 0 0;padding-left:20px;font-size:13.5px}
li{margin-bottom:5px}
.scroll{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}

/* ── 手機：表格換成卡片 ──────────────────────── */
.cards{display:none}
.pc{border:1px solid var(--line);border-radius:9px;padding:11px 13px 12px;margin:0 0 10px;
 background:var(--surface)}
.pc.ctl{background:var(--surface2)}
.pc .hd{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;margin-bottom:3px}
.pc .hd b{font-size:14px}
.pc .meta{font-size:11.5px;color:var(--muted);margin-bottom:7px}
.pc table{font-size:12.5px}
.pc th{padding:3px 6px;font-size:10.5px}
.pc td{padding:4px 6px;border-bottom:1px solid var(--line2)}
.pc .barwrap{margin-top:9px;padding-top:8px;border-top:1px solid var(--line2)}
.pc .barwrap .cap{font-size:11px;color:var(--muted);margin-bottom:3px}
/* 拆解測試的手機版：同一組的條件要上下貼著，才看得出差異 */
.rc{border-top:1px solid var(--line);padding:9px 0 3px}
.rc:first-child{border-top:0;padding-top:2px}
.rc.ctl{opacity:.72}
.rc .hd{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;margin-bottom:6px}
.rc .hd b{font-size:13.5px}
.rc .hd span{font-size:11.5px;color:var(--muted)}
.rc table{font-size:12.5px}
.rc th{padding:3px 6px;font-size:10.5px}
.rc td{padding:4px 6px;border-bottom:1px solid var(--line2)}
.rc tr:last-child td{border-bottom:0}
@media (max-width:760px){
 body{padding:16px 12px 50px;overflow-x:hidden}
 .wrap{max-width:100%}
 .desk{display:none}
 .cards{display:block}
 .howto dl{grid-template-columns:1fr;gap:3px}
 .howto dt{margin-top:7px}
 .benchtab td{white-space:normal}
 .benchtab{display:block}
 .benchtab tr{display:flex;flex-wrap:wrap;gap:4px 18px}}
"""


# ---------------------------------------------------------------- 小工具

def sgn(side):
    """空方的數字要翻過來，才會變成「做空賺不賺」。"""
    return -1 if side == "空" else 1


def cls(v):
    """v 已經是「照這個方向做」的損益。賺＝紅、賠＝綠。"""
    if v is None:
        return "dim"
    return "good" if v > 0.05 else ("bad" if v < -0.05 else "dim")


def net_cls(v):
    """淨報酬的顏色。門檻比一般數字寬 —— 0.07% 這種數字實質上就是零，
    染成紅色會讓人以為有賺。滑價、零股湊整、掛單沒成交都不只這個數。"""
    if v is None:
        return "dim"
    return "good" if v > 0.15 else ("bad" if v < -0.15 else "dim")


def beat_cls(beat, is_ctl):
    """贏過中位數的分界是 50%，不是 0。"""
    if is_ctl or beat is None:
        return ""
    return "good" if beat > 50.2 else ("bad" if beat < 49.8 else "dim")


def pct(v):
    if v is None:
        return "—"
    return "0.00%" if abs(v) < 0.005 else "%+.2f%%" % v


# ---------------------------------------------------------------- 自動標籤

def auto_tag(r, is_ctl):
    """從回測資料推出這個條件該怎麼用。

    回傳 (標籤文字, css class, 拿來畫長條的持有期, 一句話說明, 淨報酬或 None)。

    **淨報酬是這裡最重要的一個數字。** t 值高只代表「穩定贏過大盤」，
    不代表賺錢 —— 大盤漲 1.1%、這檔只漲 0.6%，做空它「超額 +0.5%」
    但實際上賠 0.6%。這種情況要當場標出來，不然 t=+4.8 會害人。

    判準只有兩條，而且都是統計問題：
      * t 值撐得住（|t| ≥ 2），代表不是某幾個月運氣好
      * 超額為正，代表贏得過同一天的全市場

    交易成本刻意**不列入判準**。一個條件是不是真的有效，
    跟你用哪家券商、折數多少無關；把成本寫進判準，
    等於改一下手續費假設標籤就整排翻掉，那不合理。
    成本只在表格裡標成「扣完還剩多少」，讓你自己判斷划不划算。
    """
    if is_ctl:
        return ("對照組", "no", "20", "隨機挑股，本來就不該有標籤。", None)
    s = sgn(r["side"])
    ok, worst = [], None
    for h in HOR:
        x = r["h"].get(h)
        if not x or x.get("t") is None:
            continue
        t = s * x["t"]
        ex = s * x["excess"]
        raw = s * x["mean"]
        if t >= 2.0 and ex > 0:
            ok.append((h, t, ex, raw))
        if t <= -2.0 and ex < 0:
            if worst is None or t < worst[1]:
                worst = (h, t, ex, raw)
    dirn = "空" if r["side"] == "空" else "多"
    if ok:
        # 幾個持有期都過關的話取「最短」的 —— 報酬差不多的時候，
        # 抱越久曝險越久、資金週轉越慢，沒有理由多抱。
        h, t, ex, raw = ok[0]
        extra = ""
        if len(ok) > 1:
            extra = "（%s 也過關，但取最短的）" % "、".join(SCALE[o[0]] for o in ok[1:])
        net = raw - COST[h]
        # 統計上過關，但扣完成本實際是賠的 —— 這種要另外標，不能給正面標籤
        if net <= 0:
            return ("贏大盤但賠錢", "warn", h,
                    "抱 %s 天超額 %s、t=%+.1f，統計上確實穩定贏過大盤——"
                    "但原始報酬只有 %s，扣掉成本 %.3f%% 之後是 %s。"
                    "它漲得比大盤少，不代表做空它會賺。"
                    "這種條件適合當「不要碰」的濾網，不適合直接進場。"
                    % (h, pct(ex), t, pct(raw), COST[h], pct(net)))
        return ("%s%s" % (SCALE[h], dirn), "up", h,
                "隔天開盤進、抱 %s 個交易日：超額 %s、t=%+.1f 撐得住。"
                "原始 %s，扣掉來回成本 %.3f%% 之後淨賺約 %s。%s"
                % (h, pct(ex), t, pct(raw), COST[h], pct(net), extra), net)
    if worst:
        h, _t, ex, _raw = worst
        return ("反指標（%s）" % SCALE[h], "dn", h,
                "照這個方向做，抱 %s 個交易日平均輸大盤 %s，而且是穩定發生的。"
                "這不是叫你反著做，是叫你別照它進場。" % (h, pct(-ex)), None)
    return ("未達標準", "no", "20",
            "四個持有期都沒有同時滿足「t ≥ 2」和「超額為正」。"
            "數字再漂亮也可能是雜訊。", None)


# ---------------------------------------------------------------- 儲存格

def bars(monthly, side, h):
    """長條的正負一律換算成「照這個方向做賺不賺」，賺＝紅、賠＝綠。"""
    if not monthly:
        return ""
    s = sgn(side)
    items = [(m, v * s) for m, v in monthly.items()]
    mx = max(abs(v) for _, v in items) or 1
    # 月份數會隨樣本長度變 —— 一年 12 根、五年 67 根。
    # 固定 9px 的話五年就撐爆手機畫面，所以照根數反推寬度，讓整條維持在 ~270px。
    n = len(items)
    bw = 9 if n <= 20 else max(2, int(270 / n) - 1)
    gap = 2 if n <= 20 else 1
    out = ['<span class="zero"></span>']
    for m, v in items:
        ht = max(2, round(abs(v) / mx * 13))
        top = 14 - ht if v > 0 else 15
        col = "var(--gain)" if v > 0 else "var(--loss)"
        out.append('<i style="width:%dpx;height:%dpx;margin-top:%dpx;background:%s"'
                   ' title="%s　持有 %s 天　做%s %+.2f%%"></i>'
                   % (bw, ht, top, col, m[:4] + "-" + m[4:], h, side, v))
    return '<div class="bars" style="gap:%dpx">%s</div>' % (gap, "".join(out))


def stbar(states, h):
    """選出來的股票，h 天後散到布林哪一區。回傳一條堆疊長條。"""
    if not states:
        return ""
    tot = sum(states.values())
    if tot < 200:          # 樣本太少的分布只是雜訊，不如不畫
        return ""
    segs = []
    for k in ST:
        v = states.get(k, 0)
        if not v:
            continue
        segs.append('<i style="width:%.2f%%;background:%s;opacity:%s" title="%s %.1f%%"></i>'
                    % (v / tot * 100, ST_COL[k], ST_OP[k], k, v / tot * 100))
    down = (states.get("下半部", 0) + states.get("貼下軌", 0)) / tot * 100
    return ('<div class="stbar">%s</div>'
            '<div class="stcap">掉到中軌以下 <b>%.0f%%</b></div>'
            % ("".join(segs), down))


def st_key():
    out = []
    for k in ST:
        out.append('<span><i style="background:%s;opacity:%s"></i>%s</span>'
                   % (ST_COL[k], ST_OP[k], k))
    return '<div class="stkey">%s</div>' % "".join(out)


def cell_vals(r, h, is_ctl):
    """回傳一格要用的所有東西。

    (超額字串, 超額class, 原始字串, 贏過字串, 贏過class, 贏天數字串, t字串, t夠不夠強)
    """
    x = r["h"].get(h)
    if not x:
        return None
    s = sgn(r["side"])
    ex = s * x["excess"]
    raw = s * x["mean"]
    beat = x["beat"] if s > 0 else 100 - x["beat"]
    pos = x.get("pos_days")
    if pos is not None and s < 0:
        pos = 100 - pos
    tv = x["t"]
    tvs = s * tv if tv is not None else None
    strong = (tvs is not None and abs(tvs) > 2.0 and not is_ctl)
    ts = "—"
    if tvs is not None:
        ts = "0.0" if abs(tvs) < 0.05 else "%+.1f" % tvs
    return (pct(ex), cls(ex), pct(raw),
            "%.1f%%" % beat, beat_cls(beat, is_ctl),
            ("%.0f%%" % pos) if pos is not None else "—",
            ts, strong)


def gap_cell(r, is_ctl):
    """隔天跳空：訊號日收盤 → 隔天開盤。這一段盤後選股吃不到。"""
    x = r["h"].get(GAP)
    if not x:
        return None
    s = sgn(r["side"])
    ex = s * x["excess"]
    raw = s * x["mean"]
    return (pct(ex), cls(ex), pct(raw))


# ---------------------------------------------------------------- 主程式

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    d = json.load(open(os.path.join(here, "backtest.json"), encoding="utf-8"))
    rows = d["rows"]
    bench = {h: (sum(d["bench"][h]) / len(d["bench"][h])) for h in HOR + [GAP]
             if d["bench"].get(h)}

    tags = {r["key"]: auto_tag(r, r["key"] in CONTROL) for r in rows}

    # 逐月長條的起訖月份，寫進說明裡，不然沒人知道左右哪邊是新的
    months = []
    for r in rows:
        mm = (r["h"].get("20") or {}).get("monthly") or {}
        if len(mm) > len(months):
            months = list(mm.keys())
    m_from = (months[0][:4] + "-" + months[0][4:]) if months else "?"
    m_to = (months[-1][:4] + "-" + months[-1][4:]) if months else "?"

    body = []
    body.append('<div class="wrap">')
    body.append("<h1>篩選情境回測</h1>")
    body.append('<p class="sub">樣本期間 %s ~ %s，共 %d 個交易日。'
                '一律是盤後選股：訊號當天收盤後名單才出來，所以進場價用<b>隔天的開盤</b>。</p>'
                % (d["from"][:4] + "-" + d["from"][4:6] + "-" + d["from"][6:],
                   d["to"][:4] + "-" + d["to"][4:6] + "-" + d["to"][6:], d["days"]))

    body.append('<div class="note"><b>先看這裡再看數字。</b>'
                '這份是「這些條件在這一年多的台股長什麼樣」，不是「這些條件會賺錢」。'
                '樣本只有一年多、一種盤，而且沒有還原除權息（除息日會被當成下跌）、'
                '沒有考慮流動性與滑價。'
                '最底下三列是<b>隨機挑股的對照組</b> —— 它們的數字就是這套量測方法的雜訊底線，'
                '任何一個情境如果沒有明顯超過對照組，就等於什麼都沒測出來。</div>')

    # ── 怎麼看這張表 ──────────────────────────
    body.append('<div class="howto"><h2>怎麼看這張表</h2><dl>')

    body.append('<dt>進出場的定義</dt><dd>名單是<b>盤後</b>跑出來的，當天已經收盤，'
                '最快只能隔天開盤買得到。所以四個持有期<b>進場都一樣</b>，只差在什麼時候出：'
                '<br>「1 天」＝隔天開盤進、<b>隔天收盤</b>出。'
                '「5／10／20 天」＝隔天開盤進、<b>第 5／10／20 個交易日收盤</b>出。</dd>')

    body.append('<dt>紅色和綠色</dt><dd>跟券商對帳單一樣：<b>紅＝賺、綠＝賠</b>。'
                '<span class="sw up">+1.08%</span> 有賺、<span class="sw dn">−2.86%</span> 賠。'
                '<br><b>注意：紅色是「照這個情境的方向做有賺」，不是「股價漲」。</b>'
                '空方的數字都已經換算成做空的角度，所以空方那幾列看到紅色，'
                '意思是股價跌了、空單賺錢。</dd>')

    body.append('<dt>標籤</dt><dd>每個情境該當成隔日單、週單還是波段，'
                '不是我認定的，是從資料推出來的。要同時滿足兩條：'
                '<b>t ≥ 2</b>（穩定出現）、<b>超額為正</b>（贏得過同一天的全市場）。'
                '幾個持有期都過關就取<b>最短</b>的——報酬差不多時，抱越久曝險越久。'
                '<br>兩條都過不了的就標「未達標準」，不硬給。'
                '標成<span class="tag dn">反指標</span>的意思是：照它的方向做會穩定賠。'
                '這不是叫你反著做，是叫你別照它進場。</dd>')
    body.append('<dt>⚠ 贏大盤 ≠ 賺錢</dt><dd>'
                '<b>這是整份報告最容易害人的地方。</b>'
                '超額是「比大盤多賺多少」，但大盤自己在漲的時候，'
                '一檔<b>漲得比大盤少</b>的股票，超額是正的、做空它卻還是賠錢。'
                '<br>「反彈放空點」就是這樣：抱 20 天超額 +0.56%、t=+4.8（全報告最高），'
                '但原始報酬是 <b>−0.58%</b>——股票其實漲了，空它照樣賠。'
                '<br>所以標籤旁邊一定會附一個<b>「淨」</b>：原始報酬扣掉來回成本之後'
                '真正進口袋的數字。<b>淨是綠的（負的），就代表這個條件不能拿來直接進場</b>，'
                '只能當「不要碰」的濾網。那種情況標籤會變成'
                '<span class="tag warn">贏大盤但賠錢</span>。</dd>')
    body.append('<dt>交易成本</dt><dd><b>刻意不列入標籤的判準。</b>'
                '一個條件是不是真的有效，跟你用哪家券商、折數多少無關；'
                '把成本寫進判準，改一下假設標籤就整排翻掉，那不合理。'
                '<br>這裡用的是手續費 3 折來回 0.0855%%，加上證交稅：'
                '<b>當沖（1 天）%.3f%%</b>、<b>隔夜以上 %.3f%%</b>。'
                '滑鼠移到標籤上會顯示扣完之後大約剩多少。</dd>'
                % (COST["1"], COST["20"]))

    body.append('<dt>超額</dt><dd>比同一天全市場（成交金額 ≥ 1 億）的平均多賺多少。'
                '大盤自己在漲的時候做多本來就會賺，減掉才知道是不是條件有用。'
                '<br>底下灰色的<b>原始</b>是沒減大盤的實際報酬。'
                '兩個要一起看：只看原始會把大盤的功勞算到條件頭上，'
                '只看超額會覺得「才賺 1%」——'
                '對照組「隨機 30 檔」20 天原始也有 +3.87%，那就是躺著不動的部分。</dd>')

    body.append('<dt>隔天跳空</dt><dd>訊號日收盤 → 隔天開盤那一段。'
                '<b>這一段你吃不到</b>（名單出來時已經收盤了），所以不算在上面的報酬裡。'
                '但要知道它有多大：如果一個條件的價值整個在跳空上，'
                '那它實際上是沒辦法用的。'
                '<br>「月線上升強勢股」就是這樣：跳空原始 +0.93%、t=+5.7（整份報告裡最強的數字），'
                '可是開盤之後反而下跌，持有 1 天是 −0.54%。'
                '<b>它的優勢整個發生在你買不到的那一段。</b></dd>')

    body.append('<dt>贏過中位數</dt><dd>選出來的股票裡，有幾成表現超過當天全市場的<b>中間那一檔</b>。'
                '隨機亂挑本來就是 50%，所以<b>這一欄的分界是 50% 不是 0</b>。'
                '<br>底下灰色的<b>贏天數</b>是另一個角度：有幾成的「天」是賺的。'
                '兩個都高＝又穩又準；平均漂亮但贏天數低＝靠少數幾天大賺撐起來的。</dd>')

    body.append('<dt>t</dt><dd>這個結果是穩定出現，還是剛好碰上。'
                '<b>絕對值小於 2 就當作沒測出東西</b>，框起來的才算數。'
                '<br>算法上，同一天選出來的股票會一起漲一起跌，'
                '所以是先把<b>每天的超額平均成一個數字</b>再看這些每日平均穩不穩 ——'
                '某天選到 20 檔同族群一起漲，只算一天一筆，不是 20 筆。'
                '持有期重疊造成的灌水也修正過了。</dd>')

    body.append('<dt>逐月長條</dt><dd>把超額拆成<b>每個月一根</b>，'
                '左邊最舊（%s）、右邊最新（%s），中間那條細線是零。'
                '<b>畫的是標籤選中的那個持有期</b>，不是固定 20 天。'
                '<br>要看的是<b>紅綠哪邊多、連不連貫</b>，不是某一根特別高。'
                '整排幾乎全紅＝一路都行；綠的東一根西一根＝只是某幾個月運氣好。</dd>'
                % (m_from, m_to))

    body.append('<dt>N 天後在布林哪一區</dt><dd>選出來的股票，過了 N 天之後'
                '散落在布林通道的哪一段——<b>貼上軌／中軌之上／中軌之下／貼下軌</b>。'
                '紅色是中軌之上，綠色是之下；顏色深的是貼著上下軌的極端。'
                '<br>這比平均報酬穩定，也更接近你在盤面上實際看到的東西。'
                '<b>看的是「掉到中軌以下的比例」</b>——那是這筆單走壞掉的機率，'
                '拿來設停損和決定部位大小，比看平均報酬有用。'
                '<br>參考值：全市場「貼上軌」的股票，5 天後掉到中軌以下是 15%；'
                '但如果上軌還在往外開（斜率 > 3%），只有 4.4%；上軌沒在漲的話是 28%。</dd>')
    body.append('<dt>平均檔數</dt><dd>這個情境平均每天選出幾檔。'
                '<b>檔數少的要特別小心</b> —— 檔數越少，每天的平均越容易被一兩檔飆股帶著走。'
                '在只有一年樣本的時候，「隨機 10 檔」曾經光靠運氣跑出 +0.98%、t=+2.3；'
                '樣本拉長之後那個數字塌回 +0.07%、t=+0.6。'
                '<b>所以一天只選幾檔、而且樣本天數又短的情境，數字再漂亮也先不要信。</b>'
                '<br>另外，同一天選出的股票很可能是<b>同一個族群</b>，'
                '38 檔不等於 38 個機會，這一點本站目前還看不出來。</dd>')

    body.append("</dl></div>")

    # ── 桌機：寬表 ────────────────────────────
    body.append('<div class="card desk"><div class="scroll"><table>')
    head1 = ('<tr><th class="l" rowspan="2">情境</th><th class="l" rowspan="2">標籤</th>'
             '<th rowspan="2">方向</th><th rowspan="2">平均<br>檔數</th><th rowspan="2">天數</th>'
             '<th class="grp" rowspan="2">隔天<br>跳空</th>')
    for h in HOR:
        head1 += '<th class="grp" colspan="3">持有 %s 天</th>' % h
    head1 += '<th class="l" rowspan="2">N 天後在布林哪一區<br>' \
             '<span style="font-weight:400;color:var(--muted)">紅＝中軌之上，綠＝之下</span></th>'
    head1 += '<th class="l" rowspan="2">逐月超額<br>' \
             '<span style="font-weight:400;color:var(--muted)">一根＝一個月，左舊右新</span></th></tr>'
    head2 = "<tr>"
    for _ in HOR:
        head2 += '<th>超額<span class="sub2">原始</span></th>' \
                 '<th>贏過<br>中位數<span class="sub2">贏天數</span></th><th>t</th>'
    head2 += "</tr>"
    body.append(head1 + head2)

    for r in rows:
        is_ctl = r["key"] in CONTROL
        tag, tcls, tag_h, tag_why, tag_net = tags[r["key"]]
        tr = '<tr class="ctl">' if is_ctl else "<tr>"
        tr += '<td class="l">%s</td>' % r["name"]
        net_html = ""
        if tag_net is not None:
            net_html = ('<span class="net %s" title="原始報酬扣掉來回成本。'
                        '絕對值小於 0.15%% 就當作沒賺 —— 滑價就不只這個數。">'
                        '淨 %s</span>' % (net_cls(tag_net), pct(tag_net)))
        tr += '<td class="l"><span class="tag %s" title="%s">%s</span>%s</td>' % (
            tcls, tag_why, tag, net_html)
        tr += '<td><span class="side">%s</span></td>' % r["side"]
        tr += '<td class="num">%.1f</td><td class="num">%d</td>' % (r["avg_picks"], r["days"])
        g = gap_cell(r, is_ctl)
        if g:
            tr += '<td class="num %s">%s<span class="sub2">%s</span></td>' % (g[1], g[0], g[2])
        else:
            tr += '<td class="dim">—</td>'
        for h in HOR:
            v = cell_vals(r, h, is_ctl)
            if not v:
                tr += '<td class="dim">—</td><td class="dim">—</td><td class="dim">—</td>'
                continue
            ex, exc, raw, bt, btc, pos, ts, strong = v
            tr += '<td class="num %s">%s<span class="sub2">%s</span></td>' % (exc, ex, raw)
            tr += '<td class="num %s">%s<span class="sub2">%s</span></td>' % (btc, bt, pos)
            tr += '<td><span class="t %s">%s</span></td>' % ("on" if strong else "off", ts)
        hx = r["h"].get(tag_h) or {}
        tr += '<td class="l">%s</td>' % stbar(hx.get("states"), tag_h)
        tr += '<td class="l">%s</td>' % bars(hx.get("monthly") or {}, r["side"], tag_h)
        body.append(tr + "</tr>")
    body.append("</table></div>")
    body.append(st_key())
    body.append("</div>")

    # ── 手機：一個情境一張卡 ──────────────────
    body.append('<div class="cards">')
    for r in rows:
        is_ctl = r["key"] in CONTROL
        tag, tcls, tag_h, tag_why, tag_net = tags[r["key"]]
        body.append('<div class="pc%s">' % (" ctl" if is_ctl else ""))
        net_html = ""
        if tag_net is not None:
            net_html = ('<span class="net %s" title="原始報酬扣掉來回成本。'
                        '絕對值小於 0.15%% 就當作沒賺 —— 滑價就不只這個數。">'
                        '淨 %s</span>' % (net_cls(tag_net), pct(tag_net)))
        body.append('<div class="hd"><b>%s</b><span class="tag %s" title="%s">%s</span>%s'
                    '<span class="side">%s</span></div>'
                    % (r["name"], tcls, tag_why, tag, net_html, r["side"]))
        g = gap_cell(r, is_ctl)
        gtxt = ("・隔天跳空 %s" % g[0]) if g else ""
        body.append('<div class="meta">平均 %.1f 檔／天・%d 天%s</div>'
                    % (r["avg_picks"], r["days"], gtxt))
        body.append('<table><tr><th class="l">持有</th>'
                    '<th>超額<span class="sub2">原始</span></th>'
                    '<th>贏過中位數<span class="sub2">贏天數</span></th><th>t</th></tr>')
        for h in HOR:
            v = cell_vals(r, h, is_ctl)
            if not v:
                body.append('<tr><td class="l">%s 天</td><td class="dim">—</td>'
                            '<td class="dim">—</td><td class="dim">—</td></tr>' % h)
                continue
            ex, exc, raw, bt, btc, pos, ts, strong = v
            mark = "　←" if (h == tag_h and tcls != "no") else ""
            body.append('<tr><td class="l">%s 天%s</td>'
                        '<td class="num %s">%s<span class="sub2">%s</span></td>'
                        '<td class="num %s">%s<span class="sub2">%s</span></td>'
                        '<td><span class="t %s">%s</span></td></tr>'
                        % (h, mark, exc, ex, raw, btc, bt, pos,
                           "on" if strong else "off", ts))
        body.append("</table>")
        hx = r["h"].get(tag_h) or {}
        sb = stbar(hx.get("states"), tag_h)
        if sb:
            body.append('<div class="barwrap"><div class="cap">'
                        '%s 天後跑到布林哪一區</div>%s</div>' % (tag_h, sb))
        m = hx.get("monthly") or {}
        if m:
            body.append('<div class="barwrap"><div class="cap">逐月超額（持有 %s 天）・'
                        '一根＝一個月，左舊右新</div>%s</div>' % (tag_h, bars(m, r["side"], tag_h)))
        body.append("</div>")
    body.append("</div>")

    # ── 拆解測試 ──────────────────────────────
    if d.get("rule_rows"):
        rmap = {r["key"]: r for r in d["rule_rows"]}
        body.append("<h2>拆解測試：一次只動一個條件</h2>")
        body.append('<p class="sub">上面那張表每個情境都是好幾條疊在一起，測出來好或不好'
                    '不知道是哪一條的功勞。這裡一次只換一個條件、其他都一樣，'
                    '差異才歸得到那一條身上。<b>看的是同一組裡上下兩列差多少，不是絕對值。</b>'
                    '顏色和欄位的意思跟上面那張表一樣。</p>')
        for g in d["rule_groups"]:
            body.append('<div class="card"><h3>%s</h3>'
                        '<p class="legend" style="margin:0 0 10px">%s</p>'
                        '<div class="scroll desk"><table>' % (g["name"], g["why"]))
            head = '<tr><th class="l">條件</th><th>方向</th><th>平均<br>檔數</th>'
            for h in HOR:
                head += '<th>%s 天<br>超額<span class="sub2">原始</span></th>' \
                        '<th>贏過<br>中位數</th><th>t</th>' % h
            body.append(head + "</tr>")
            for key in g["keys"]:
                rr = rmap.get(key)
                if not rr:
                    continue
                is_ctl = key == "rndR"
                tr = '<tr class="ctl">' if is_ctl else "<tr>"
                tr += '<td class="l">%s</td><td><span class="side">%s</span></td>' % (
                    rr["name"], rr["side"])
                tr += '<td class="num">%.1f</td>' % rr["avg_picks"]
                for h in HOR:
                    v = cell_vals(rr, h, is_ctl)
                    if not v:
                        tr += '<td class="dim">—</td><td class="dim">—</td><td class="dim">—</td>'
                        continue
                    ex, exc, raw, bt, btc, pos, ts, strong = v
                    tr += '<td class="num %s">%s<span class="sub2">%s</span></td>' % (exc, ex, raw)
                    tr += '<td class="num %s">%s</td>' % (btc, bt)
                    tr += '<td><span class="t %s">%s</span></td>' % ("on" if strong else "off", ts)
                body.append(tr + "</tr>")
            body.append("</table></div>")

            # 同一組的手機版：一個條件一小塊，上下疊起來才好比較
            body.append('<div class="cards">')
            for key in g["keys"]:
                rr = rmap.get(key)
                if not rr:
                    continue
                is_ctl = key == "rndR"
                body.append('<div class="rc%s">' % (" ctl" if is_ctl else ""))
                body.append('<div class="hd"><b>%s</b><span class="side">%s</span>'
                            '<span>平均 %.1f 檔／天</span></div>'
                            % (rr["name"], rr["side"], rr["avg_picks"]))
                body.append('<table><tr><th class="l">持有</th>'
                            '<th>超額<span class="sub2">原始</span></th>'
                            '<th>贏過中位數</th><th>t</th></tr>')
                for h in HOR:
                    v = cell_vals(rr, h, is_ctl)
                    if not v:
                        body.append('<tr><td class="l">%s 天</td><td class="dim">—</td>'
                                    '<td class="dim">—</td><td class="dim">—</td></tr>' % h)
                        continue
                    ex, exc, raw, bt, btc, pos, ts, strong = v
                    body.append('<tr><td class="l">%s 天</td>'
                                '<td class="num %s">%s<span class="sub2">%s</span></td>'
                                '<td class="num %s">%s</td>'
                                '<td><span class="t %s">%s</span></td></tr>'
                                % (h, exc, ex, raw, btc, bt, "on" if strong else "off", ts))
                body.append("</table></div>")
            body.append("</div>")
            body.append("</div>")

    # ── 基準 ──────────────────────────────────
    body.append('<h2>基準：這段期間市場本身漲多少</h2><div class="card">'
                '<table class="benchtab"><tr>')
    if GAP in bench:
        body.append('<td class="l">隔天跳空　<b class="num">%+.2f%%</b></td>' % bench[GAP])
    for h in HOR:
        if h in bench:
            body.append('<td class="l">持有 %s 天　<b class="num">%+.2f%%</b></td>' % (h, bench[h]))
    body.append("</tr></table>"
                '<p class="legend">全市場（成交金額 ≥ 1 億）的平均報酬。'
                '這段期間大盤是往上的，所以任何做多的情境本來就會賺，'
                '一定要減掉這個才知道是條件有用還是單純跟著大盤漲。'
                '<br><b>注意「隔天跳空」是正的、「持有 1 天」卻多半是負的</b>：'
                '台股平均而言漲的那一段發生在昨收到今開，開盤到收盤反而偏跌。'
                '所以「隔天開盤追進去」這件事本身就帶著一點逆風，跟你選什麼股票無關。</p></div>')

    body.append("<h2>測不到的部分</h2><div class=\"card\"><ul>"
                "<li><b>族群集中度</b>：站上還沒有產業別資料，所以不知道選出來的 30 檔裡"
                "有幾檔是同一個族群。30 檔可能只是一個部位，實際風險比看起來大。"
                "（t 值的計算已經處理了族群同漲同跌，但實際下單的風險沒有。）</li>"
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
    for r in rows:
        t = tags[r["key"]]
        print("  %-20s %s" % (r["name"], t[0]))


if __name__ == "__main__":
    main()
