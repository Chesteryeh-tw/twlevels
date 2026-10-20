#!/usr/bin/env python3
"""每天產生「爆量長紅＋漲停」清單，給分點補抓用。

定義（跟研究 study/limitup.py 同一件事，漲停改用真正的漲停價）：
  漲停：收盤 = 漲停價（昨收 × 1.1，依升降單位無條件捨去），而且收在最高
  爆量：成交量 ≥ 前 5 日均量 2 倍
  流動性：成交金額 ≥ 1 億
  一字鎖死（開＝高＝低＝收）也算，另標一欄

輸出（每次從 START 整段重建，資料只來自官方價量，可以公開）：
  data/limitup_list.csv   一列一個事件（附昨收、最高、漲停價、量、5 日均量，可自行核對）
  data/limitup_meta.json  定義、單位、版本、來源、還在等下一個交易日的代號
  data/limitup_fetch.csv  要補抓分點的（日期, 代號）：事件當天、隔一個交易日
                          隔天還沒到的，等隔天重跑會自動補上

用法：python limitup_list.py [data 資料夾] [起始日 YYYYMMDD]
"""
import os, sys, glob, csv, math, json, datetime

DATA = sys.argv[1] if len(sys.argv) > 1 else 'data'
START = sys.argv[2] if len(sys.argv) > 2 else '20260911'
VR_MIN, WAN_MIN = 2.0, 10000
VERSION = 'limitup_list v1（2026-10-10）'          # 量 ≥ 5 日均量 2 倍；成交金額（萬元）≥ 1 億


def tick(p):
    if p < 10: return 0.01
    if p < 50: return 0.05
    if p < 100: return 0.1
    if p < 500: return 0.5
    if p < 1000: return 1.0
    return 5.0


def limit_up(prev):
    raw = prev * 1.1
    t = tick(raw)
    p = math.floor(raw / t + 1e-9) * t
    if tick(p) != t:                    # 剛好跨過級距邊界時，用較低級距的單位再算一次
        t = tick(p); p = math.floor(raw / t + 1e-9) * t
    return round(p, 2)


def load_names():
    names = {}
    p = os.path.join(DATA, 'market.txt')
    if not os.path.exists(p): return names
    with open(p, encoding='utf-8') as fh:
        fh.readline()
        for l in fh:
            x = l.rstrip('\n').split(',')
            if len(x) > 2: names[x[0]] = (x[1], '上市' if x[2] == '1' else '上櫃' if x[2] == '2' else x[2])
    return names


def load_px(path):
    rows = []
    with open(path, encoding='utf-8') as fh:
        for l in fh:
            if l[0] == '#': continue
            x = l.strip().split(',')
            try: rows.append((x[0], float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5] or 0), float(x[6] or 0)))
            except (ValueError, IndexError): pass
    return rows


def main():
    names = load_names()
    events = []
    for f in sorted(glob.glob(os.path.join(DATA, 'stock', '*.txt'))):
        code = os.path.basename(f)[:-4]
        if code.startswith('_') or (names and code not in names): continue
        r = load_px(f)
        for i in range(6, len(r)):
            d, o, h, l, c, v, wan = r[i]
            if d < START or wan < WAN_MIN or o <= 0: continue
            prev = r[i - 1][4]
            if prev <= 0 or abs(c - limit_up(prev)) > 1e-6 or c < h: continue
            v5 = sum(x[5] for x in r[i - 5:i]) / 5
            if v5 <= 0 or v / v5 < VR_MIN: continue
            streak = 1
            j = i - 1
            while j >= 1 and r[j - 1][4] > 0 and abs(r[j][4] - limit_up(r[j - 1][4])) < 1e-6:
                streak += 1; j -= 1
            nm, mk = names.get(code, ('', ''))
            events.append(dict(date=d, code=code, name=nm, market=mk, prev_close=prev, open=o, high=h, low=l, close=c,
                               limit_price=limit_up(prev), chg_pct=round((c / prev - 1) * 100, 2),
                               volume_lots=int(v), avg5_lots_excl_today=round(v5, 1), vol_ratio_5d=round(v / v5, 2),
                               turnover_wan=int(wan), one_price=int(o == h == l == c), limit_streak=streak,
                               next_date=r[i + 1][0] if i + 1 < len(r) else '',
                               next_status='已有' if i + 1 < len(r) else '等待（下一個交易日還沒到）'))
    events.sort(key=lambda e: (e['date'], e['code']))
    with open(os.path.join(DATA, 'limitup_list.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(events[0].keys()) if events else ['date'])
        w.writeheader(); w.writerows(events)
    need = {}
    for e in events:
        need.setdefault((e['date'], e['code']), set()).add('事件日')
        if e['next_date']: need.setdefault((e['next_date'], e['code']), set()).add('隔一交易日')
    with open(os.path.join(DATA, 'limitup_fetch.csv'), 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh); w.writerow(['date', 'code', 'name', 'purpose'])
        for (d, c), p in sorted(need.items()):
            w.writerow([d, c, names.get(c, ('', ''))[0], '＋'.join(sorted(p, reverse=True))])
    waiting = [e['code'] for e in events if not e['next_date']]
    last = max((x[0] for f in glob.glob(os.path.join(DATA, 'stock', '2330.txt')) for x in load_px(f)), default='')
    meta = dict(version=VERSION, generated_at=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds'),
                source='官方收盤行情（data/stock/<代號>.txt：證交所 MI_INDEX／櫃買上櫃行情，歷史由 FinMind 補齊），未還原除權息',
                price_data_last_date=last, start=START,
                definition={'漲停': '收盤 = 漲停價（昨收 × 1.1，依升降單位無條件捨去）而且收盤 = 最高',
                            '爆量': '成交量 ≥ 前 5 個交易日平均量 × 2（5 日均量不含事件日）',
                            '流動性': '成交金額 ≥ 1 億（10,000 萬元）',
                            '一字鎖死': '開 = 高 = 低 = 收；一樣算事件，one_price 欄 1/0（不留空）'},
                units={'volume_lots': '張（成交股數 ÷ 1,000 四捨五入）', 'turnover_wan': '萬元（成交金額 ÷ 10,000 四捨五入）',
                       'price': '元', 'chg_pct': '%'},
                fetch_rule='limitup_fetch.csv：事件當天＋隔一個交易日；下一個交易日還沒到的不列入，下一次重建時自動補上',
                waiting_next_day=waiting,
                available_at='GitHub Actions 每交易日台北 16:00 排程後產生；以 commit 時間為實際可用時間')
    with open(os.path.join(DATA, 'limitup_meta.json'), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    days = sorted({e['date'] for e in events})
    print('爆量長紅＋漲停：%d 筆、%d 個交易日（%s ~ %s）；要補抓分點 %d 個（日期, 代號）'
          % (len(events), len(days), days[0] if days else '-', days[-1] if days else '-', len(need)))


if __name__ == '__main__':
    main()
