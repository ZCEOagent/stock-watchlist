# 外部排程派送與 dead-man switch（Cloudflare Worker）

## 為什麼需要

2026-09-30 至 10-19 實測，這個 repo 的 GitHub `schedule` 觸發延遲中位數 3–5.5 小時，雷達 workflow 平日約 122 次觸發只送達 8–13 次（證據見 `docs/diagnosis-2026-10-19.md`）。`workflow_dispatch` 不經過 schedule 佇列，所以改由這個 Worker 每 5 分鐘檢查一次、準時派送；同時要求每個時段回報 heartbeat，**沒有回報本身就是告警**，即使 GitHub 完全沒跑也能發現。

## 時段（台灣時間）

| 時段 | 派送 | 工作 | 必須開始 | 必須完成 | 失敗重派 |
|---|---|---|---|---|---|
| radar-weekly | 週一 07:40 | market-radar `weekly` | +20 分 | +50 分 | 1 次 |
| intraday | 平日 08:45 | market-radar `session`（09:00–13:36 長駐，每 75 秒） | +20 分 | 13:50 | 否 |
| tw-close | 平日 14:17 | daily-tw | +20 分 | +150 分 | 1 次 |
| radar-daily | 每日 18:20 | market-radar `daily` | +20 分 | +50 分 | 1 次 |
| tw-evening | 平日 21:17 | daily-tw | +20 分 | +150 分 | 1 次 |
| us-close | 週二至六 11:00（避開 Yahoo 美股盤後重算時段，見診斷文件第五節） | daily-us | +20 分 | +90 分 | 1 次 |

盤中另外檢查：09:10–13:30 若超過 12 分鐘沒有心跳就告警（每 30 分鐘最多一次）；回報 `degraded`（連續 3 輪有效快照不到 50%）也會告警。台股休市日由工作回報 `closed`，該日其餘台股時段會略過。GitHub cron 只保留作為延遲備援；時段帳本會讓晚到的備援直接結束、不重做。

告警只在狀態改變時發送：派送失敗、整段時窗漏跑、未開始（會自動重派一次）、重派後仍未開始、失敗、逾時未完成、盤中沈默、行情不足；異常後完成會發一則恢復通知。

## 部署步驟（需要你本人操作）

**順序很重要：先合併 PR，再部署 Worker。** master 合併前沒有 `session` 這個 mode，派送會被 GitHub 以 422 拒絕。

1. **建立 GitHub fine-grained token**：Settings → Developer settings → Fine-grained tokens。
   - Repository access：只選 `ZCEOagent/stock-watchlist`
   - Permissions：**Actions: Read and write**（Metadata: Read 會自動帶入），其他都不要給
   - 設定到期日，並記下更新日期。token 到期時 Worker 會回報「派送失敗」
2. 安裝並登入 wrangler：
   ```bash
   npx wrangler@4 login
   ```
3. 建立 KV，把印出的 id 填進 `wrangler.toml` 的 `REPLACE_WITH_KV_NAMESPACE_ID`：
   ```bash
   npx wrangler@4 kv namespace create RADAR_KV
   ```
4. 設定 secrets（逐一執行，值不要寫進任何檔案）：
   ```bash
   npx wrangler@4 secret put GH_PAT
   ```
   ```bash
   npx wrangler@4 secret put HEARTBEAT_TOKEN
   ```
   ```bash
   npx wrangler@4 secret put TELEGRAM_BOT_TOKEN
   ```
   ```bash
   npx wrangler@4 secret put TELEGRAM_CHAT_ID
   ```
   `HEARTBEAT_TOKEN` 請自己產生一組長亂數（例如 `python -c "import secrets;print(secrets.token_urlsafe(32))"`）。
5. 把 `wrangler.toml` 的 `ACTIVE_FROM` 設成部署當下的時間（例如 `2026-10-20T07:00:00+08:00`），避免把部署前的時段誤報成漏跑；然後部署：
   ```bash
   npx wrangler@4 deploy
   ```
6. 在 GitHub repo → Settings → Secrets and variables → Actions 新增：
   - `HEARTBEAT_URL`：Worker 網址（例如 `https://stock-watchlist-dispatcher.<帳號>.workers.dev`）
   - `HEARTBEAT_TOKEN`：與第 4 步相同的值
7. 驗證：
   ```bash
   curl -s -H "Authorization: Bearer $HEARTBEAT_TOKEN" "$HEARTBEAT_URL/status"
   ```
   下一個時段開始後，`/status` 應該能看到 `worker.dispatches[0].ok=true`，以及 heartbeats 的 `started`。

## 運作驗收（部署後第一個完整交易日）

本機測試只證明邏輯正確，**不代表** Cloudflare 與 GitHub Actions 實際運作。必須逐項確認：

- [ ] 08:45–08:50 之間出現 `market-radar` 的 workflow_dispatch run，`mode=session`
- [ ] 盤中 run 的日誌每 75 秒有一輪檢查；`/status` 的 intraday 心跳間隔 ≤ 5 分鐘
- [ ] `radar-validation` artifact 的 `watch-health.quote_classes` 中 `fresh` 的比例明顯高於修正前
- [ ] 14:17、18:20、21:17 各自有 dispatch run，`/status` 顯示 `completed`
- [ ] 故意測試：在 GitHub 停用 `daily-tw.yml` 一次，確認 14:37 收到「未開始」告警，再重新啟用

## 費用與限制

- Workers 免費方案：cron 每 5 分鐘一次，每天 288 次請求；KV 寫入估計每天約 150 次（免費額度 1,000 次）。
- KV 是最終一致性儲存；每個 key 只有一個寫入者（cron 寫 `w:*`、heartbeat 寫 `hb:*`），避免互相覆蓋。
- Worker 本身停擺時無法自我告警。Cloudflare 的可用性遠高於這個 repo 實測的 GitHub schedule，但仍不是 100%；GitHub 端的 `radar-monitor` 仍然保留作為第二道防線。

## 回滾

1. 停用 Worker 排程：把 `wrangler.toml` 的 `crons` 改成 `[]` 後重新 deploy（或在 Cloudflare 主控台刪除 Worker）。
2. 還原 PR 的 merge commit，GitHub cron 就會恢復原狀。資料不需要遷移；時段帳本只是 SQLite 狀態裡新增的 `slot:*` meta key。

## 測試

```bash
node --test cloudflare/test/*.test.mjs
```
