# SUS 凍結紀錄 v2.5（研究設計 v2.5＋程式 v4，2026-10-25）

狀態：**設計 v2.5 與程式 v4 已 commit，等 GPT 驗收**。尚未讀過任何真 SUS 結果；前瞻起點不變（第一個前瞻營收月 2026-10，第一份正式快照 2026-11-11 19:30～11-12 09:00）。

## 1. 身分

| 項目 | 值 |
|---|---|
| 登記 | preregistration/SUS_事先登記_v2.5.md，SHA-256 5b9f1cf5b131d376b1aa0faa364b7c34c0c6f20c7692a8bce72ea03d42af6e14 |
| commit | 3df514a2acbf29984b873693a31a77ebe5d1ebf5（2026-10-25T11:52:01+08:00） |
| sus_study.py（v4） | f2b539c95d3df3d29b4942f4abbffd9d1fb02d2dd1c45c92d0222e0049bf982a |
| test_sus_study.py | bdb9fc5856a59bf55973873b77c000b35dac7a8b6cb490eb5ffb1a9fc30c26f0（109 項通過，含 R1：程式引用的登記雜湊＝登記檔實際雜湊） |
| market_calendar.py | 94455c8fa2df3fddf72380e42395435d78315d92550fd2e5773a737278a59a75 |
| sus_decide.yml | 56879adfd4c344e89987634257b15ae93f2bf2f507527cd70f9c33eb492a6c7e |
| 未變（同 v3 紀錄） | corporate_actions.py 1a72b610…06e3、revenue_fetch.py 69820adf…cc05 |

v2.4 原文、v3 程式凍結紀錄保留不動。

## 2. 與 v3 的差異

| 項目 | 內容 | 測試 |
|---|---|---|
| 登記 v2.5 | 第 1～9 節逐字沿用 v2.4；新增第 10 節（歷史涵蓋與存活選樣限制、R1～R5 用語、48 份維持暫緩）、第 11 節（快照時窗、日曆核驗條件、前瞻保全、缺價量未解規則、事件表發布點） | R1 |
| GPT 09 4.1 | 前瞻月：當時母體代碼缺整份價量（missing_price_input）、缺進場日列（missing_entry_row）、缺預定出場日列 → 未解資料，月不進判定；只有價格列存在且成交量 0 才算有證據的不可成交。歷史段照 v2.4 第 3 節不變 | G1a～G1e |
| GPT 09 4.2 | 日曆輸出 sources_verified_at（FMTQIK 當月、年度休市表各自最後一次成功抓到並解析的時間，存在 data/cal_raw/verified.json）；新鮮度只看這兩個時間，不看 built_at；sus_decide.yml 日曆更新失敗即停、不寫決策；決策紀錄另存 calendar_sources_verified_at | G2a～G2c（更新被拒＋舊檔重建：built_at 新、檢查仍不通過） |

實跑確認：sus_decide.yml run 40159819991（v4）成功；日曆 sources_verified_at 兩個來源都是 2026-10-25T03:5x Z；價量基準日 10/22、下一個市場日 10/27；沒有快照，新寫 0 份紀錄。

## 3. 前瞻遇到缺價就未解的代價（揭露）

本地每日價量管線不存「有掛牌但當天沒成交」的列，所以目前前瞻遇到停牌一律是未解資料、該月不進判定（較保守、可能減少有效月份）。要恢復停牌順延，需要先建官方停牌／零成交證據來源，屬新版本。

## 4. 沒做

沒有跑真 SUS、沒有補抓 48 份、沒有改研究期間、沒有動 Collector／看板／Spark。
