# SUS 日曆修正紀錄 v2.5a（2026-11-19）

狀態：只改官方市場日曆的**保存方式**；登記 v2.5、sus_study.py v4、決策規則都不變。尚未讀過任何真 SUS 結果。

## 1. 發生什麼事

- 2026-11-12 起每晚 `sus_decide.yml` 都在「寫決策紀錄」停下：「官方休市表沒有涵蓋 2026 年」。
- 原因：證交所 OpenAPI `holidaySchedule` 在 11 月改成只回 **2027 年度**休市表；`market_calendar.py` v4 每次把回應**整份覆蓋**同一個檔 → 2026 年度消失 → 登記第 11 節第 3 條（下一個市場日年度要在休市表內）不成立 → 照規則不寫決策。檢查本身照設計運作，錯在保存方式。
- 影響（照登記不補）：營收月 **2026-10 的 16 日版**本該在 11-16 開盤前寫決策，11-12～11-15 每晚都被擋 → **缺失**；2026-10 因此不是兩版共同有效月。11 日版（202610_v11，2026-11-11T21:54Z）已正常寫入，不受影響。

## 2. 改了什麼

| 項目 | 內容 |
|---|---|
| market_calendar.py v5 | 休市表依回應涵蓋的年度分檔存（`holidaySchedule_<年度>.json.gz`），抓取時間／年度／雜湊記在 `data/cal_raw/holiday_index.json`；同年度再抓到就用新的；不同年度互不覆蓋。休市表空的 → 停、不算核驗成功。日曆輸出多一欄 `schedule_year_sources`（每個年度用哪一份、雜湊）。 |
| data/holiday_seed.json | 2026 年度休市表內容，取自 2026-11-11T21:54:09Z 成功核驗的官方回應（原檔 SHA-256 7644c1a8…7d2117，見 commit eb37ff697 的 data/market_calendar.json，同一次執行寫了 202610_v11）。只在沒有更新的官方分檔涵蓋 2026 時才用。臨時休市不在裡面（仍在 market_closures.json）。 |
| 新鮮度 | 不變：仍看 `sources_verified_at`（FMTQIK 當月、休市表最後一次成功抓到並解析）36 小時內。 |
| 揭露 | 2026 年度改由「之前核驗過的官方表」涵蓋，官方 11 月後不再回 2026 年度，若官方事後更動 2026 年度休市日，這裡看不到；臨時休市照舊由 market_closures.json 處理。 |

## 3. 身分

| 檔案 | SHA-256 |
|---|---|
| market_calendar.py（v5） | 21b7ef25ac03d47d71e6ed2cb4ae4745219f087cf89910d830af46ee5f498708 |
| test_market_calendar.py（新，8 項通過） | cc12441c4b254f5150f8fa6d3a9b1e44e9afbd9c3b0a06e9e2ece1ef0387ae65 |
| data/holiday_seed.json | 8a78a41df024c9a93079a0478dcbf6a4c8aacef51853a8dc5ca8409b71557561 |
| 未變：sus_study.py v4 | f2b539c95d3df3d29b4942f4abbffd9d1fb02d2dd1c45c92d0222e0049bf982a |
| 未變：登記 v2.5 | 5b9f1cf5b131d376b1aa0faa364b7c34c0c6f20c7692a8bce72ea03d42af6e14 |

test_sus_study.py（109 項）照跑全部通過。待 GPT 複核。
