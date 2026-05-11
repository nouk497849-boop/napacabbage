# 台灣出發低價機票推播機器人

這是一個 Python 查價機器人，預設從 `TPE/TSA/KHH/RMQ/TNN` 出發，掃描未來 12 個月、來回 3-21 晚、可轉機的全球低價票，並用 Telegram 推播明顯低於基準價的票。

重點設計：

- 優先用免費或低額度資料源，並用 daily/monthly quota 防止 overage。
- Travelpayouts/Amadeus 做低成本探索，SearchApi/Kiwi/Amadeus 做高分候選驗價。
- 商務艙與頭等艙採「最長航段為準」，混艙會在通知中標示。
- Postgres 保存 quote、rolling median baseline、quota usage、24 小時去重紀錄。

## 快速開始

```powershell
python -m pip install -e ".[test]"
Copy-Item .env.example .env
python -m flight_deals_bot --list-sources
python -m flight_deals_bot --dry-run
python -m pytest
```

`.env` 會被自動讀取。本機 dry-run 沒有 `DATABASE_URL` 時會使用記憶體資料庫，只適合測試；GitHub Actions 長期執行請設定 Postgres。

## 必要設定

Telegram：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Postgres：

- `DATABASE_URL`
- 第一次部署後可手動跑：`python -m flight_deals_bot --init-db`

資料源 keys，全部都是選填，缺 key 會自動停用：

- `TRAVELPAYOUTS_TOKEN`
- `AMADEUS_CLIENT_ID`
- `AMADEUS_CLIENT_SECRET`
- `SEARCHAPI_KEY`
- `KIWI_API_KEY`
- `SKYSCANNER_API_KEY` + `SKYSCANNER_ENABLED=true`

## GitHub Actions

已提供 `.github/workflows/flight-deals.yml`：

- 每 3 小時自動執行一次。
- GitHub cron 使用 UTC；目前設定對應台灣時間 10:00、13:00、16:00、19:00、22:00、01:00、04:00、07:00，實際啟動可能被 GitHub 延後數分鐘到數十分鐘。
- 支援 `workflow_dispatch` 手動 dry-run。
- secrets 放 API key、Telegram、`DATABASE_URL`。
- vars 可調整 `ORIGINS`、`STAY_LENGTHS`、`CABINS`、`REQUIRE_VERIFIED_ALERTS`。

建議先在 GitHub 手動執行 dry-run，確認資料源與資料庫正常，再讓排程自動發送。

若只想確認 Telegram 設定是否正確，手動執行 workflow 時把 `test_telegram=true`。這會只發一則測試訊息，不會查價。

預設 `NOTIFY_NO_DEALS=true`，正式排程每次跑完即使沒有低價票，也會發一則「目前沒有找到符合低價門檻的機票」摘要。若覺得太吵，可在 GitHub Variables 設為 `false`。
摘要會列出最多 8 筆候選票；可用 `NO_DEAL_CANDIDATE_LIMIT` 調整，例如 `5` 或 `10`。
正式低價票與候選票都會顯示機場名稱、國家，並附上來源連結；若來源沒有連結，會退回 Google 搜尋連結。

## Quota 與通知邏輯

預設 quota 在 `.env.example`，例如 SearchApi 預設 `3/day`、`100/month`，用於驗證最有價值候選。設為 `0` 可停用某來源。

通知門檻：

- 經濟艙：低於 rolling baseline 約 30%。
- 豪華經濟艙：低於約 40%。
- 商務艙：低於約 45%。
- 頭等艙：低於約 55%。

同一 deal 24 小時內不重複推播，除非新價格比上次推播再低 5% 以上。

## 專案結構

- `src/flight_deals_bot/config.py`：環境變數與搜尋設定。
- `src/flight_deals_bot/storage.py`：Postgres/In-memory storage、quota、baseline、alert cooldown。
- `src/flight_deals_bot/sources/`：Travelpayouts、Amadeus、SearchApi、Kiwi、Skyscanner adapters。
- `src/flight_deals_bot/scoring.py`：低價判定與商務/頭等艙混艙規則。
- `src/flight_deals_bot/pipeline.py`：discovery -> verification -> scoring -> Telegram。
- `tests/`：parser、scoring、quota、pipeline dry-run 測試。

## 注意

這個機器人不自動訂票、不保留座位、不保證票價仍存在。推播中的快取候選票請務必進入來源網站或航空公司頁面重新確認價格、行李、退改、簽證與自轉機風險。

## SearchApi-only 模式

如果你目前只有 `SEARCHAPI_KEY`，機器人會用 SearchApi 的 Google Travel Explore 從台灣出發搜尋 anywhere 候選，再用 Google Flights API 對高分候選驗價。免費額度很小，預設 `SEARCHAPI_DAILY_LIMIT=3`，所以會優先掃 `TPE` 的商務艙、頭等艙、經濟艙；若要涵蓋更多出發機場，請提高 daily/monthly limit 或補上 Travelpayouts/Amadeus/Kiwi 作為探索來源。

`dry_run=true` 仍會打真實 API，也會記錄 quota usage，避免測試時不小心超出免費額度。若要在同一天多測幾次，可在 GitHub Actions Variables 新增或調高 `SEARCHAPI_DAILY_LIMIT`，例如 `6` 或 `9`，前提是你的 SearchApi 帳號額度足夠。
