"""月營收 panel 的唯一讀取入口（GPT 10-24 P4）。

權威來源＝data/revenue/current.json（單一原子替換的指標），指向不可變的 gen/<id>/panel.csv 與 panel_meta.json。
讀取時先讀一次指標，再核對 panel、meta 的雜湊；任何不一致都拒用（SystemExit），不退回根目錄那兩個相容檔。
沒有 current.json（舊快取）才走舊路：根目錄 panel.csv，有 panel_meta.json 就核雜湊。
"""
import hashlib
import json
import os

OUT = os.path.join("data", "revenue")


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def resolve(out=None):
    """回傳 (panel_path, meta_path 或 None, 說明)。沒有 panel 回 (None, None, 說明)。不一致 → SystemExit。"""
    out = out or OUT
    cur = os.path.join(out, "current.json")
    if os.path.exists(cur):
        try:
            ptr = json.load(open(cur, encoding="utf-8"))
            gdir = os.path.join(out, "gen", ptr["generation"])
            pp, mp = os.path.join(gdir, "panel.csv"), os.path.join(gdir, "panel_meta.json")
            want = ptr["panel_sha256"]
        except (OSError, ValueError, KeyError) as e:
            raise SystemExit("current.json 讀不了或欄位不全（%s），不執行" % e)
        if not (os.path.exists(pp) and os.path.exists(mp)):
            raise SystemExit("current.json 指到的 generation %s 不完整，不執行" % ptr["generation"])
        if _sha(pp) != want:
            raise SystemExit("generation %s 的 panel.csv 雜湊跟 current.json 對不上，不執行" % ptr["generation"])
        try:
            if json.load(open(mp, encoding="utf-8")).get("panel_sha256") != want:
                raise SystemExit("generation %s 的 panel_meta 雜湊跟 current.json 對不上，不執行" % ptr["generation"])
        except (OSError, ValueError) as e:
            raise SystemExit("generation %s 的 panel_meta 讀不了（%s），不執行" % (ptr["generation"], e))
        return pp, mp, "generation %s" % ptr["generation"]
    pp, mp = os.path.join(out, "panel.csv"), os.path.join(out, "panel_meta.json")
    if not os.path.exists(pp):
        return None, None, "沒有 panel"
    if os.path.exists(mp):
        meta = json.load(open(mp, encoding="utf-8"))
        if meta.get("panel_sha256") and meta["panel_sha256"] != _sha(pp):
            raise SystemExit("panel.csv 跟 panel_meta.json 的雜湊對不上，不執行")
        return pp, mp, "舊版（無 current.json），核過 meta 雜湊"
    return pp, None, "舊版快取（無 current.json、無 meta），略過一致性檢查"
