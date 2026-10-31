"""舊資料（2015-07～2020-10）驗證判定：照 preregistration/舊資料2016至2020驗證_事先登記_20261101.md。

用法：python holdout_judge.py <backtest.json> <bt_bopen2.json> <revenue_report.json> <bt_series.json> <輸出資料夾>
只讀統計結果、不讀價格；輸出 holdout_result.json、holdout_result.md（可公開）。
"""
import json
import math
import os
import sys

COST = 0.386


def verdict(group, ex, t, net):
    """ex、t 已轉成「照預期方向為正」。"""
    if ex is None or t is None:
        return "沒有樣本"
    if t <= -2:
        return "方向相反"
    if t < 2 or ex <= 0:
        return "測不出來"
    if group == "A" and (net is None or net <= 0):
        return "測不出來（贏大盤但賠錢）"
    return "重現"


def main(bt_p, bo_p, rv_p, se_p, out_dir):
    bt = json.load(open(bt_p, encoding="utf-8"))
    bo = json.load(open(bo_p, encoding="utf-8"))
    rv = json.load(open(rv_p, encoding="utf-8"))
    se = json.load(open(se_p, encoding="utf-8"))
    rows = {r["key"]: r for r in bt["rows"]}
    xrows = {r["key"]: r for r in bt.get("exit_rows", [])}
    rally = {r["key"]: r for r in bt.get("rally_rows", [])}
    res = []

    def add(code, name, group, hold, ex, t, net, sign, extra=None):
        e2 = None if ex is None else sign * ex
        t2 = None if t is None else sign * t
        res.append(dict(code=code, name=name, group=group, hold=hold, excess=ex, t=t, net=net,
                        verdict=verdict(group, e2, t2, net), **(extra or {})))

    # A1、A2 條件出場（開盤進場欄）
    for code, k in (("A1", "x2b"), ("A2", "x2by")):
        x = xrows.get(k)
        o = x and x["conv"]["O"]
        add(code, x["name"] if x else k, "A", "條件出場",
            o and o["excess"], o and o["t"], o and o["net"], 1,
            dict(n=o and o["n"], days=o and o["n_days"], hold_avg=x and x["hold"]))

    def row(k, h):
        r = rows.get(k)
        return r, (r["h"].get(h) if r else None)

    # A3 b1fr 10 天
    r, m = row("b1fr", "10")
    add("A3", r["name"], "A", "10 天", m and m["excess"], m and m["t"],
        m and (m["mean"] - COST), 1, dict(n=m and m["n"], days=m and m["n_days"]))

    # A4、A5 月營收（11 日進場、全期、20 天）
    for code, k in (("A4", "R1"), ("A5", "R3")):
        c = rv["main"]["cells"][k]["by_period"]["all"]["20"] or {}
        add(code, k + " " + rv["main"]["cells"][k]["name"], "A", "20 天",
            c.get("excess"), c.get("t"), c.get("net"), 1, dict(n=c.get("n"), days=c.get("dates")))

    # A6～A12 開布林深度開口 7 格（1 天、全期；原規則：nd ≥ 30、ex > 0、t ≥ 2、net > 0、net_d > 0）
    cells7 = ["D|deep|m|hi|S", "D|deep|s|hi|S", "U|deep|l|hi|S", "U|mid|s|mid|S",
              "U|shallow|m|mid|S", "U|shallow|s|lo|S", "U|shallow|s|mid|S"]
    for i, k in enumerate(cells7):
        m = bo["cells"].get(k, {}).get("h", {}).get("1", {}).get("all")
        net = None
        if m:
            net = m["net"] if (m["net_d"] is not None and m["net_d"] > 0) else min(m["net"], m["net_d"] or 0)
            if m["nd"] < 30:
                m = dict(m, t=None)
        add("A%d" % (6 + i), "開布林深度開口 " + k, "A", "1 天（當沖空）",
            m and m["ex"], m and m["t"], net, 1, dict(n=m and m["n"], days=m and m["nd"]))

    # B1 投信首日大買 10 天（20 天只列）
    r, m = row("c8", "10")
    _r, m20 = row("c8", "20")
    add("B1", r["name"], "B", "10 天", m and m["excess"], m and m["t"], m and (m["mean"] - COST), 1,
        dict(n=m and m["n"], days=m and m["n_days"], ex20=m20 and m20["excess"], t20=m20 and m20["t"]))

    # C1、C2、C5 疑似起漲／起跌、支撐壓力（全部期間）
    for code, k, h, sign in (("C1", "d_all", "40", 1), ("C2", "u_reg2", "20", -1), ("C5", "f_rfail_dn", "5", -1)):
        r = rally.get(k)
        m = r and r["periods"][0]["h"].get(h)
        add(code, r["name"] if r else k, "C", h + " 天", m and m["excess"], m and m["t"], m and m["net"], sign,
            dict(n=m and m["n"], days=m and m["days"]))

    # C3、C4、C6、C7 主表（照預期方向：超額 < 0）
    for code, k, h in (("C3", "hot", "1"), ("C4", "b1", "1"), ("C6", "b3", "20"), ("C7", "p6", "1")):
        r, m = row(k, h)
        side_short = r and r.get("side") == "空"
        net = m and ((-m["mean"] - COST) if side_short else (m["mean"] - COST))
        add(code, r["name"], "C", h + " 天", m and m["excess"], m and m["t"], net, -1,
            dict(n=m and m["n"], days=m and m["n_days"]))

    # 只列出：b1fr − b1fq 同一天相減（10 天）、隨機對照
    a = se["series"].get("b1fr", {}).get("10", [])
    b = se["series"].get("b1fq", {}).get("10", [])
    d = [x - y for x, y in zip(a, b) if x is not None and y is not None]
    pair = None
    if len(d) >= 3:
        mu = sum(d) / len(d)
        sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (len(d) - 1))
        pair = dict(diff=mu, t=mu / (sd / math.sqrt(len(d))) if sd > 0 else None, days=len(d))
    ctl = {}
    for k in ("rnd10", "rnd30", "rnd60"):
        r = rows.get(k)
        if r:
            ctl[k] = {h: dict(excess=r["h"][h]["excess"], t=r["h"][h]["t"]) for h in ("1", "5", "10", "20")}

    n_ok = sum(1 for x in res if x["verdict"] == "重現")
    meta = dict(days=bt["days"], start=bt["from"], end=bt["to"],
                rally=bt.get("rally_meta"), bopen2=bo.get("meta"),
                revenue=dict(first=rv["main"].get("first"), last=rv["main"].get("last"),
                             n_events=rv["main"].get("n_events")),
                n_cells=len(res), lucky=0.5, n_reproduced=n_ok,
                registration="preregistration/舊資料2016至2020驗證_事先登記_20261101.md")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(dict(meta=meta, cells=res, b1fr_minus_b1fq=pair, controls=ctl),
              open(os.path.join(out_dir, "holdout_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    f = lambda v, p="%+.2f": "—" if v is None else p % v
    L = ["# 舊資料驗證結果（歷史穩健性檢查）", "",
         "評估日 %s～%s（%d 天）；登記 %s" % (bt["from"], bt["to"], bt["days"], meta["registration"]), "",
         "| 代號 | 條件 | 持有 | 超額% | t | 淨% | 筆數 | 天數 | 判定 |", "|---|---|---|---|---|---|---|---|---|"]
    for x in res:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            x["code"], x["name"], x["hold"], f(x["excess"]), f(x["t"], "%+.1f"), f(x["net"]),
            x.get("n") or "—", x.get("days") or "—", x["verdict"]))
    L += ["", "重現 %d／%d 格；純運氣約 0.5 格。" % (n_ok, len(res))]
    if pair:
        L.append("b1fr − b1fq（10 天，同一天相減）：%s%%，t %s，%d 天（只列出）" % (f(pair["diff"]), f(pair["t"], "%+.1f"), pair["days"]))
    for k, v in ctl.items():
        L.append("%s：" % k + "、".join("%s 天 %s（t %s）" % (h, f(v[h]["excess"]), f(v[h]["t"], "%+.1f")) for h in v))
    open(os.path.join(out_dir, "holdout_result.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))
    print("::notice title=舊資料驗證::%s～%s，重現 %d／%d 格" % (bt["from"], bt["to"], n_ok, len(res)))


if __name__ == "__main__":
    main(*sys.argv[1:6])
