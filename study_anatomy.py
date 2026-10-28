#!/usr/bin/env python3
"""大漲、大跌之前發生了什麼（2026-10-28，使用者：測出來都沒賺，那股票上漲／下跌到底是什麼原因）。

這是「事後解剖」，不是交易訊號：先用未來 20 天的結果把股票日分成「之後大漲」「之後大跌」「其他」，
再看它們在當天（與前 20 天）有哪些條件。兩個方向的比例都給，才不會被「大漲的股票 X% 有某條件」誤導：
  ① 大漲裡有幾 % 有這個條件 vs 全部股票日有幾 % 有（倍數＝lift）
  ② 有這個條件的，之後大漲的機率 vs 平常 5%（這才是能不能拿來用）

定義（看結果前寫死）：
  母體＝成交金額 ≥ 1 億的股票日（跟回測一樣）。結果＝隔天開盤進、20 天後收盤的報酬減同日全市場平均（超額）。
  大漲＝超額在全部股票日的前 5%；大跌＝後 5%。另報門檻是多少 %。
  條件＝回測工具同一份 69 個（bt_explore.evaluator）。
  「前 10 天內出現過」：事件日與前 9 個交易日任一天成立（布林開沒開、擠壓過沒有等）。
  軌跡：每檔 20 天內只取第一個大漲（大跌）日當事件，看前後 20 天的帶寬、量、法人、融資平均，跟全部股票日比。
  期間切兩段（2021-02～2025-08／2025-09 以後）看穩不穩。

用法：python study_anatomy.py [天數] → data/study_anatomy.json
"""
import json
import os
import sys
from array import array

import numpy as np

import backtest as B
import bt_explore as X

H = 20
SPLIT = "20250901"
LOOK = 10
TRAJ = 20
FEATS = ["bbW", "vr20", "chg", "bbPos", "fgnV", "invV", "dlrHV", "mgnV", "biasY", "slope20"]
FEAT_LAB = {"bbW": "布林帶寬 %", "vr20": "均量比", "chg": "當天漲跌 %", "bbPos": "布林位階（±10＝上下軌）",
            "fgnV": "外資買賣超 ÷ 20 日均量 %", "invV": "投信 ÷ 均量 %", "dlrHV": "自營避險 ÷ 均量 %",
            "mgnV": "融資增減 ÷ 均量 %", "biasY": "離年線 %", "slope20": "月線斜率 %"}
KEYC = ["bopen", "squeeze", "x_b1", "vol3", "vol15", "rev_new", "rev_up", "upband", "hi20", "limit_up",
        "fgn_3", "inv_3", "three_buy", "lo_open", "loband", "lo20", "ma_bull", "above_y", "dt_30", "dt_50"]


def nz(x):
    return np.nan if x is None else float(x)


def main():
    limit = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
    conds, ev = X.evaluator()
    nC = len(conds)
    keys = [c[0] for c in conds]
    saved = B.PRESETS, B.NEED_CHIP, B.NEED_MA240
    B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = {}, set(), set()
    codes = {}
    A_code, A_day, A_ok = array("i"), array("i"), bytearray()
    A_bits = bytearray()
    A_f = array("f")
    A_ex = array("f")
    days = []
    zero = bytes(nC)

    def collect(day, rws, bm, krows):
        di = len(days)
        days.append(day)
        b20 = bm.get(H)
        for code, mkt, base, d, fwd, i, st in rws:
            ci = codes.setdefault(code, len(codes))
            ok = base["valE"] is not None and base["valE"] >= 1
            k = krows.get(code)
            lots = [x for x in st["lot"][max(0, i - 20):i] if x]
            av = (sum(lots) / len(lots)) if lots else None
            A_code.append(ci)
            A_day.append(di)
            A_ok.append(1 if ok else 0)
            A_bits.extend(ev(base, d, k, st, i, code, day) if ok else zero)

            def pv(key):
                if not k or k.get(key) is None or not av:
                    return np.nan
                return k[key] / av * 100
            A_f.extend([nz(d.get("bbW")), nz(d.get("vr20")), nz(base.get("chgPct")), nz(d.get("bbPos")),
                        pv("fgn"), pv("inv"), pv("dlrH"), pv("mgnChg"), nz(d.get("biasY")), nz(d.get("slope20"))])
            r = fwd.get(H)
            A_ex.append(np.nan if (r is None or b20 is None) else r - b20)

    try:
        B.run(limit_days=limit, verbose=True, collect=collect)
    finally:
        B.PRESETS, B.NEED_CHIP, B.NEED_MA240 = saved

    n = len(A_code)
    code = np.frombuffer(A_code, dtype=np.int32)
    dayi = np.frombuffer(A_day, dtype=np.int32)
    okm = np.frombuffer(bytes(A_ok), dtype=np.uint8).astype(bool)
    bits = np.frombuffer(bytes(A_bits), dtype=np.uint8).reshape(n, nC).astype(bool)
    F = np.frombuffer(A_f, dtype=np.float32).reshape(n, len(FEATS)).astype(np.float64)
    ex = np.frombuffer(A_ex, dtype=np.float32).astype(np.float64)
    split_i = next((i for i, d in enumerate(days) if d >= SPLIT), len(days))

    # 依（股票、日期）排序，方便往前往後看
    order = np.lexsort((dayi, code))
    code, dayi, okm, bits, F, ex = code[order], dayi[order], okm[order], bits[order], F[order], ex[order]
    same_prev = np.r_[False, code[1:] == code[:-1]]

    U = okm & ~np.isnan(ex)
    q_hi, q_lo = np.nanpercentile(ex[U], 95), np.nanpercentile(ex[U], 5)
    up = U & (ex >= q_hi)
    dn = U & (ex <= q_lo)
    pre = dayi < split_i

    # 前 10 天內出現過（同一檔、往前 9 列）
    look = bits.copy()
    for s in range(1, LOOK):
        sh = np.zeros_like(bits)
        sh[s:] = bits[:-s]
        valid = np.r_[np.zeros(s, dtype=bool), code[s:] == code[:-s]]
        look |= sh & valid[:, None]

    def table(B_, mask_ev, mask_all):
        out = {}
        sa = B_[mask_all].mean(axis=0)
        se = B_[mask_ev].mean(axis=0)
        ne = mask_ev.sum()
        for j, kk in enumerate(keys):
            cnt = B_[mask_all, j].sum()
            hit = (B_[:, j] & mask_ev).sum()
            out[kk] = dict(all=round(float(sa[j]) * 100, 2), ev=round(float(se[j]) * 100, 2),
                           lift=round(float(se[j] / sa[j]), 2) if sa[j] > 0 else None,
                           p=round(float(hit / cnt) * 100, 2) if cnt else None, n=int(cnt))
        return out, int(ne)

    res = {}
    for nm, ev_m in (("up", up), ("dn", dn)):
        r = {}
        for per, pm in (("all", np.ones(n, bool)), ("pre", pre), ("post", ~pre)):
            t_today, ne = table(bits, ev_m & pm, U & pm)
            t_look, _ = table(look, ev_m & pm, U & pm)
            r[per] = dict(n=ne, base=round(float((ev_m & pm).sum() / max(1, (U & pm).sum())) * 100, 2),
                          today=t_today, look10=t_look)
        res[nm] = r

    # 軌跡：每檔 20 列內只取第一個
    def events(m):
        idx = np.nonzero(m)[0]
        keep, last_c, last_i = [], -1, -10 ** 9
        for i in idx:
            if code[i] == last_c and i - last_i < TRAJ:
                continue
            keep.append(i)
            last_c, last_i = code[i], i
        return np.array(keep, dtype=np.int64)

    traj = {}
    base_mean = {f: round(float(np.nanmean(F[U, j])), 3) for j, f in enumerate(FEATS)}
    for nm, ev_m in (("up", up), ("dn", dn)):
        e = events(ev_m)
        rows = {}
        for off in range(-TRAJ, TRAJ + 1):
            ii = e + off
            ok_ = (ii >= 0) & (ii < n)
            ii2 = ii[ok_]
            ok2 = code[ii2] == code[e[ok_]]
            sel = ii2[ok2]
            rows[str(off)] = {f: (round(float(np.nanmean(F[sel, j])), 3) if len(sel) else None) for j, f in enumerate(FEATS)}
        traj[nm] = dict(n=int(len(e)), by_off=rows)

    # 帶寬分組：大漲事件前 20 天最窄帶寬
    def min_bw_before(e):
        out = []
        for i in e:
            lo_ = max(0, i - 20)
            seg = slice(lo_, i + 1)
            m = code[seg] == code[i]
            w = F[seg, 0][m]
            w = w[~np.isnan(w)]
            out.append(w.min() if len(w) else np.nan)
        return np.array(out)
    bwgrp = {}
    allE = events(U)
    rng = np.random.default_rng(20261028)
    samp = rng.choice(allE, size=min(60000, len(allE)), replace=False) if len(allE) else allE
    for nm, e in (("up", events(up)), ("dn", events(dn)), ("all", samp)):
        mb = min_bw_before(e)
        mb = mb[~np.isnan(mb)]
        bwgrp[nm] = {lab: round(float(((mb > a) & (mb <= b)).mean() * 100), 1) for lab, a, b in
                     (("≤8%", -1, 8), ("8～10%", 8, 10), ("10～15%", 10, 15), ("15～20%", 15, 20), ("> 20%", 20, 1e9))}

    meta = dict(days=len(days), start=days[0], end=days[-1], split=SPLIT, horizon=H,
                q_hi=round(float(q_hi), 2), q_lo=round(float(q_lo), 2), n_rows=int(U.sum()),
                conditions=[list(c) for c in conds], feats=FEAT_LAB, base_mean=base_mean)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "study_anatomy.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(meta=meta, cond=res, traj=traj, bw_before=bwgrp), fh, ensure_ascii=False, separators=(",", ":"))

    lab = {c[0]: c[1] for c in conds}
    print("::notice title=解剖::%d 天、%d 股票日；大漲＝20 天超額 ≥ %+.1f%%、大跌 ≤ %+.1f%%；事件 大漲 %d、大跌 %d" % (
        len(days), int(U.sum()), q_hi, q_lo, traj["up"]["n"], traj["dn"]["n"]))
    # 方向：有這個條件之後「大漲機率 − 大跌機率」（兩邊平常都是 5%）；只有這個差才有方向的意思
    for per in ("pre", "post"):
        tu, td = res["up"][per]["today"], res["dn"][per]["today"]
        dd = sorted([k for k in tu if tu[k]["p"] is not None and td[k]["p"] is not None and tu[k]["n"] >= 1000],
                    key=lambda k: -(tu[k]["p"] - td[k]["p"]))
        fmt = lambda k: "%s 漲%.1f%%／跌%.1f%%" % (lab[k], tu[k]["p"], td[k]["p"])
        print("::notice title=方向（%s）偏漲前 8::%s" % (per, " ／ ".join(fmt(k) for k in dd[:8])))
        print("::notice title=方向（%s）偏跌前 8::%s" % (per, " ／ ".join(fmt(k) for k in dd[::-1][:8])))
    for nm, t in (("大漲", "up"), ("大跌", "dn")):
        for kind in ("today", "look10"):
            tb = res[t]["all"][kind]
            top = sorted([k for k in tb if tb[k]["lift"] and tb[k]["n"] >= 2000], key=lambda k: -tb[k]["lift"])[:8]
            print("::notice title=%s前·%s 倍數最高::%s" % (nm, "當天" if kind == "today" else "前10天內", " ／ ".join(
                "%s 事件%.1f%%·平常%.1f%%（×%.2f）有它之後%s%.1f%%·樣本外×%s" % (
                    lab[k], tb[k]["ev"], tb[k]["all"], tb[k]["lift"], nm, tb[k]["p"],
                    res[t]["pre"][kind][k]["lift"]) for k in top)))
        kk = res[t]["all"]["look10"]
        print("::notice title=%s前·關鍵條件（前10天內）::%s" % (nm, " ／ ".join(
            "%s %.1f%% vs %.1f%%（有它之後%s %.1f%%）" % (lab[k], kk[k]["ev"], kk[k]["all"], nm, kk[k]["p"]) for k in KEYC if k in kk and kk[k]["p"] is not None)))
        print("::notice title=%s前 20 天最窄帶寬::%s｜隨機 %s" % (nm, json.dumps(bwgrp[t], ensure_ascii=False),
                                                         json.dumps(bwgrp["all"], ensure_ascii=False)))
        tr = traj[t]["by_off"]
        print("::notice title=%s 軌跡（-20、-10、-5、-1、0、+5、+20）::%s｜平常 %s" % (nm, " ／ ".join(
            "%s：%s" % (o, ",".join("%s=%s" % (f, tr[o][f]) for f in ("bbW", "vr20", "fgnV", "invV", "mgnV", "bbPos")))
            for o in ("-20", "-10", "-5", "-1", "0", "5", "20")), json.dumps(base_mean, ensure_ascii=False)))


if __name__ == "__main__":
    main()
