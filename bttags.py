#!/usr/bin/env python3
"""把回測標籤濃縮成 data/bt_tags.json，讓主站在選股當下就看得到「反指標」。

判準完全沿用 btreport.auto_tag()，不另寫一套 —— 報告和主站的標籤永遠一致。
每次 finmind.yml 跑完 backtest.py → btreport.py 之後跑這支。
"""
import json, os
import btreport as R

def main(src="backtest.json", dst=os.path.join("data", "bt_tags.json")):
    with open(src, encoding="utf-8") as fh:
        d = json.load(fh)
    out = {"days": d.get("days"), "from": d.get("from"), "to": d.get("to"), "tags": {}}
    for r in d.get("rows", []):
        k = r.get("key")
        is_ctl = k in getattr(R, "CONTROL", set()) or str(k).startswith("rnd")
        if is_ctl:
            continue
        tag, css, h, why, net = R.auto_tag(r, False)
        x = (r.get("h") or {}).get(h) or {}
        out["tags"][k] = {"name": r.get("name"), "side": r.get("side"), "tag": tag, "cls": css,
                          "h": h, "t": x.get("t"), "excess": x.get("excess"),
                          "net": net, "why": why}
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print("bt_tags：%d 個情境（%s 天）" % (len(out["tags"]), out["days"]))

if __name__ == "__main__":
    main()
