# SUS 程式凍結紀錄 v3（2026-10-25）

狀態：**v3 程式已 commit，等 GPT 驗收**。研究設計仍是 v2.4（SHA-256 8ffa6f3121ff31733c6f2f7685fa9beb14e64ce71311cdad4d832db40d3a78d8），沒有改公式、成本、門檻、持有期、判定。
程式在第一次讀真 SUS 結果之前凍結；v2（efdeb3d）沒有跑過真資料，所以前瞻起點不受影響（第一個前瞻營收月仍是 2026-10，第一份正式快照 2026-11-11 19:30 以後）。

## 1. 凍結身分

- commit：2409516e8f9e20410cb789805a0563b54b368126（2026-10-25T11:03:53+08:00）
- sus_study.py：f3c196a9722ca7f8f0c6b4b943bd48e4e322d5fc26917c8cd476cd272fde71db
- test_sus_study.py：5d626563f4add21b02eb422b67a95d8a66b3f37f299a51d546bc285133f54744（100 項通過）
- corporate_actions.py：1a72b61013fad14cc6475a78a9d0d17d021b14f28414afe0c9a75934357e06e3；test 2cdf91a7…94d6（30 項通過）
- revenue_fetch.py：69820adfdb6302f8162df31af9d6d0ccd96579fbfdf98a43af461e4ad28b05cc；test 6a107550…06d0（68 項通過）
- market_calendar.py：67aa84aec85cd75586d3f611397b6013b45eb491f56fe77cd8eec796b147f959
- data/market_closures.json：a278f9830496b143fbffda61316cee74d4412c1bde8bbaeda28da7a3da3576d6

## 2. 對 GPT 08 的逐項修正（實作口徑，請 GPT 核）

| 項 | 修法 | 對應測試 |
|---|---|---|
| P1a 完成時間 | decide() 接受 clock 函式；分組算完才重讀時間當 calculation_completed_at＝decision_at。算完已過預定進場開盤 → 只寫 `attempts/`（status late_after_open），不寫正式紀錄，之後仍可重新決策 | P1a、P1a2 |
| P1b 當時母體 | 正式紀錄存 universe（代號→基準日成交額，含沒有營收的 ETF）、sus（SUS 值）、groups、universe_rows_sha256、procedure_sha256、registration_sha256、calendar_sha256。前瞻一律用紀錄的母體與名單；重算母體不同標 universe_recompute_mismatch、價量列被修訂標 price_revised_after_decision，只報告 | P1b0、P1b（GPT 的 ETF 20,000→5,000 萬反例：母體平均不變） |
| P1 讀取端 | pick_decision 驗：快照雜湊、登記雜湊、decision_at ≥ 快照 cutoff、算完 ≤ decision_at < 實際進場開盤、實際進場日＝預定進場日、價量基準日＝流動性基準日＝實際進場前一交易日 | P1c（18:00 對 19:30）、P1d、P1e、A1f、A1g |
| P2 first_seen | 事件分開存 ann（官方公告時間）與 seen（該內容版本第一次抓到的時間）。歷史月只信 ann（空白＝announce_unknown）；前瞻月 ann 或 seen 取早的。first_seen 登錄改成「事件 → 內容版本清單（列內容 SHA-256＋時間）」，官方修訂內容 → 新版本另記時間；舊格式第一次重建時繫結當下內容（無法回溯，已知限制） | P2a～P2d、公司行動 P2a～P2c |
| P3 快照 | 正式快照只在 M+1 月 11 日 19:30～12 日 09:00（台北）建立與讀取；時窗外不建，讀取端也拒收（不用晚到版本補）。meta 加 formal、captured_at、panel_generation、sources_sha256／sources_count；pit_verified 拆成 snapshot_pit（這份快照確實在這時取得）與 history_pit_verified＝false。repo 持久 meta 必須存在且整份相同（不是只比 panel 雜湊） | 營收 K1c、K4a～c；SUS A1h～A1k |
| P4 日曆 | data/market_closures.json 記已核實的臨時休市（日期、原因、公告日、官方網址、原檔雜湊），併入休市日；缺網址或公告日 → 建表失敗。日曆輸出 schedule_years、built_at。`--decide`：沒有官方日曆、超過 36 小時沒更新、下一個市場日年度不在休市表 → 不寫決策並標紅（不再退回過半股價推算）。紀錄寫好後才臨時休市：舊紀錄 decision_stale、保留；當晚另寫新紀錄（記 superseded_attempts）；讀取端用最早一份實際可執行的。corporate.yml 日曆失敗也標紅 | P4a～P4h（10/22 → 10/27） |
| P5a 發布 | 事件表、first_seen、coverage 依序換檔，coverage 最後寫、記前兩者 SHA-256，是唯一發布點。讀取端雜湊不符 → 事件表不可用（前瞻未解、歷史改暫代） | 公司行動 P5a-1～3（三個步驟各注入中斷）；SUS P5a |
| P5b asof | coverage.verified 記每個「來源_月份」的抓取時間。前瞻要求持有期每個月、每個來源的抓取日期晚於 min(出場日, 該月月底) | P5b、P5c |
| P6 trim10 | 前瞻 trim10 用紀錄的 SUS 值排除 \|SUS\|>10 後重新分組（不足 50 家＝too_few） | P6a |
| P6 QC | 讀價量：短列、重複日期（保留第一筆）記 PRICE_QC；落在基準日～最長持有期內 → 該月資料錯誤。持有期內非正價格、進場開盤 ≤ 0 → 資料錯誤（不當停牌）。no_exit 附最後收盤價。報告分開列「完全沒有營收／當月無營收」 | P6b～P6f |

這些口徑是 Claude 的實作選擇，**還沒經 GPT 確認**；尤其 P3 的「12 日 09:00」上限與 P4 的「36 小時」新鮮度，若 GPT 認為應不同，改了就是程式 v4，前瞻起點不受影響（仍在第一次讀真資料之前）。

## 3. 已知限制補述（存活偏誤，2026-10-25 新發現；請 GPT 確認要不要寫進下一版登記）

本機核對 100 個早年缺碼時發現（只讀 Actions 快取、不連網，結果在 early_check/universe_check.md、universe_diag.md）：

1. **營收原始檔是現在重新產生的**：185 份原始檔的出表日期全部是 2026-10-12～10-25，只有 39 個代碼不在現行市場清單。例：5371 中光電到 2026-08 都在交易，但 2020～2026 每一份營收檔都沒有它（2026-09 換成 3718 中光電投控，只從 2026-08 營收有資料）；2867 三商壽同樣全無。也就是說，**已經下市、合併、改碼的公司，從歷史月份的檔案裡消失了**。
2. **長期價量只收回補當時還在市場上的代碼**（finmind_backfill 用 market.txt）：100 個缺碼裡 93 個沒有價量；另有 25 個近期下市代碼只有部分歷史，4 個現存代碼（2937、3067、4183、6624）長期歷史完全沒補到。
3. 影響：SUS 歷史段（2021～2026-08，只算探索）與月營收 R1～R5 的歷史數字，母體都缺「後來消失的公司」，方向不確定（下市多半表現差，但被收購的常有溢價）。**前瞻不受影響**：每月正式快照存的是當時的版本，決策紀錄存的是當時的母體。
4. 補抓 2017-12～2019-12 的 48 份不能修這件事：同樣是現在重新產生的檔，缺的公司一樣缺。937／749 與官方 907／744 的差異、100 個缺碼，主要可由這點解釋（尚未逐檔證明）。

## 4. 沒有做的事

沒有跑真 SUS、沒有補抓 48 份早年營收、沒有改研究期間或母體、沒有動 Collector／看板／Spark。
