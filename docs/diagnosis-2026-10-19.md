# 系統診斷與修復計畫（2026-10-19）

基準：`master` @ `47a5c5e`。時間皆為台灣時間（UTC+8）。
證據來源：GitHub 公開 API（800 筆執行紀錄，2026-09-30 至 10-19）、jobs 步驟結果、repo 內 `state/`、`docs/` 快照、本機重現、今天 12:53 盤中實際呼叫證交所 MIS。
**限制**：未登入狀態無法讀取 Actions 原始日誌與 artifacts（頁面顯示 "Sign in to view logs"），因此凡需要日誌內容才能確認的項目，下方標為「待日誌確認」。

---

## 一、原有架構盤點

### 1.1 實際部署的工作流程（全部在 `master`，RADAR_MODE=shadow）

| Workflow | 排程（台灣時間） | 每個平日預期觸發次數 | 實際平均 |
|---|---|---:|---:|
| `market-radar.yml` 雷達摘要 | 18:20 日報、週一 08:00、09:02–13:32 每 5 分 quotes、每小時 :35 retry、08:17–22:47 每 30 分 events | **約 122** | **8–13** |
| `radar-maintenance.yml` 補件 | 每小時 :05 | 24 | 3–7 |
| `daily-tw.yml` 台股收盤 | 14:17、21:17 | 2 | 2（但嚴重延遲） |
| `daily-us.yml` 美股收盤 | 06:37 | 1 | 1 |
| `health.yml` 行情時效 | 10:47、23:47 | 2 | 2 |
| `radar-monitor.yml` 監控 | 每 6 小時 + 上述 5 個工作結束觸發 | 4 + N | 2–4 + N |
| `tests.yml` | push / PR | — | — |

### 1.2 資料流程與模組依賴

```
                         ┌──────────── daily-tw.yml（git commit 狀態）─────────────┐
FinMind 交易日 ─┐        │ main.py → fetch_tw_universe（官方名冊）                  │
TWSE/TPEx 官方 ─┼──────► │        → market_radar.history（官方批次日K）            │
Yahoo(yfinance) ┘        │        → radar_pipeline.build_radar → radar.py 五層閘門  │
                         │          （僅 20 檔深審；催化來自 radar_evidence.json）  │
                         │        → docs/tw_cache.json、state/radar*.json（git）   │
                         │        → performance.py 次日開盤對照                    │
                         │        → notify.py（Telegram，git outbox）             │
                         └─────────────────────────────────────────────────────────┘
                                         │ checkout master 讀 tw_cache.json
                                         ▼
┌─ market-radar.yml（artifact: market-radar-state SQLite）──────────────────────────┐
│ cli daily/weekly：sources（官方 OpenAPI）→ engine 評分（另一套 BUY≥80 門檻）      │
│ cli quotes/events：watch.py（MIS 盤中）→ execution.py 提醒後模擬                 │
│ cli retry：若當日報告未送達則重送，否則什麼都不做                                 │
└──────────────────────────────────────────────────────────────────────────────────┘
        ▲ import_public                         ▲ 讀 artifact
┌─ radar-maintenance.yml（artifact: market-radar-maintenance）┐   ┌─ radar-monitor.yml ┐
│ financials（MOPS XBRL）+ history 補件                       │   │ 讀三份 artifact +  │
└─────────────────────────────────────────────────────────────┘   │ workflow runs API  │
                                                                   └────────────────────┘
```

**重複／衝突**
- 兩套選股引擎：`radar.py`（五層閘門、R≥2）與 `market_radar/engine.py`（10 項評分、BUY≥80）。宇宙、門檻、輸出、狀態都不同，`execution.py` 要求兩邊同時成立。
- 三種狀態儲存：git 中的 JSON（`state/`、`docs/`）、`market-radar-state` SQLite artifact、`market-radar-maintenance` SQLite artifact，加上 monitor 自己的 SQLite artifact。
- 兩套 Telegram 去重：`notify.py` 的 git outbox 與 `market_radar/notify.py` 的 SQLite receipts。
- 盤中價格提醒（watch.py ±3/5/8%）與進場提醒（execution.py）共用同一行情抓取，但規則、額度、文案各自獨立。

**未使用或不必要**
- `cli retry`：每小時 :35 觸發，`cli.py:69-71` 在沒有待送報告時直接 return。結果是每天約 24 次「成功但什麼都沒做」的執行。
- `--financial-limit` 參數已標 Deprecated（`cli.py:282`）。
- `run_daily.bat`、`fetch_tw_stock.py` 逐檔 FinMind 下載路徑（RELIABILITY.md 已說改用官方批次）。
- 遠端有 23 個未合併的 feature/fix 分支；「One-time Telegram connection test 20261009」workflow 仍是 active 狀態。

### 1.3 文件與程式不一致

| 文件說法 | 實際程式／行為 |
|---|---|
| README：台股約 14:00 更新、分支 `main` | cron 14:17／21:17，實際觸發中位延遲 5.5 小時；分支是 `master` |
| PHASE2：模擬「固定 20 交易日出場」 | `evaluation.py:44` 已改成 3 日時間出場 |
| MARKET_RADAR：「盤中只查公告，價格風險使用每日收盤」 | 後來加了 5 分鐘 quotes 模式 |
| RADAR：五層檢查含「突破或回測」；使用者規格要求三類訊號 | **「整理承接」在程式中不存在**（`radar.py:113-121` 只有 breakout／pullback） |
| cloud-monitor.md：會偵測「工作漏跑」 | 門檻是 48／96 小時，且被空轉的 retry 執行重置（見 2.3） |
| RADAR：「全市場研究」 | 每天只深審 20 檔（`config.RADAR_CANDIDATE_LIMIT = 20`） |

---

## 二、根因分析

### 2.1 GitHub Actions 排程沒有正常觸發 ── **已證實**

證據（`/actions/runs` API，只計 `event=schedule`）：

| Workflow | 觸發延遲中位數 | 最小 | 最大 |
|---|---:|---:|---:|
| Daily TW Close Report | **5.52 h** | 0.03 h | 8.47 h |
| Radar data health | 5.02 h | 2.17 h | 6.98 h |
| Radar cloud monitor | 3.77 h | 0 h | 5.85 h |
| Daily US Close Report | 3.03 h | 2.08 h | 4.53 h |

- `market-radar.yml` 平日預期約 122 次觸發，實際 8–13 次（例如 10-16 共 13 次）。
- 10-16（週五）09:00–13:30 只有 3 次執行，用 jobs API 檢查步驟（quotes 模式會略過 `Restore maintenance facts`）後，**只有 13:19 那次是 quotes 模式**；預期是 55 次。10-19 今天到 12:46 為止，**零次** quotes 執行。
- 14:17 的收盤報告實際在 19:19–23:18 才建立；21:17 那次落在隔天 01:26–05:45。
- 10-13 稽核（`docs/audit-2026-10-13.md`）已確認延遲發生在 run 被**建立**之前，不是 job 執行慢。

根因判斷：
1. **設計錯誤：把時效性工作建在 GitHub `schedule` 上。** GitHub 官方文件說明 schedule 在高負載時可能延遲或被丟棄，沒有 SLA。這個 repo 平日定義了約 150 個排程觸發點，實測只有一小部分送達，而且延遲數小時。
2. 權限、分支條件、workflow 停用都已排除：所有 workflow 都是 `active`，`if: github.ref == default_branch` 條件成立（run 都有執行），排程 run 的 `head_branch=master`。
3. GitHub 內部為何對這個 repo 延遲到這種程度，無法從外部證實；但不論原因為何，結論都一樣：**盤中與收盤時效不能依賴 GitHub cron。**

### 2.2 盤中行情大量缺少有效最新成交價 ── **已證實（今天盤中實測）**

10-19 12:53:24 以 MIS API 查 20 檔（其中 15 檔有回應）：

- 15 檔中有 **14 檔頂層 `z`（成交價）是 `"-"`**，只有 2444 有值。
- 15 檔**全部**有巢狀的 `trade` 物件，例如 2330：`{"t":"12:51:40","v":1,"z":"2410.0000"}`。頂層 `t`（12:52:25）是**快照時間**，不是成交時間。
- 用現行 `watch.valid_quote()` 判斷這批即時資料：**15 檔只有 1 檔有效（6.7%）**。
- 以 `trade.t` 計算的最新成交年齡：8 檔在 180 秒以內，另外 7 檔是 4–11 分鐘前真的沒有成交（低流動性），買賣報價本身都是新的。

根因：
1. `watch.py:73`、`:94` 和 `execution.py:80,92,110,123` 只讀頂層 `z`。盤中只要快照當下沒有新成交就是 `"-"`，所以絕大多數被判無效。
2. `watch.py:66`、`:93` 和 `execution.py:31` 把頂層 `t` 當成「成交時間」，語意錯誤：資料新鮮度應該分成「報價快照時間」與「最後成交時間」兩個欄位。
3. 收盤後 `z` 會填入收盤價，所以 10-12、10-13 稽核在盤後看到 97% 有效，掩蓋了盤中的問題。
4. 再加上 2.1，quotes 執行本身就很少在盤中發生。

**備援**：MIS 是唯一的盤中來源，沒有第二個官方免費即時來源。現行程式在批次失敗時 `continue`（`watch.py:52-54`），不做重試；整體 0 檔有效才 raise（`watch.py:231`），只要 1 檔有效就算成功。

### 2.3 監控為什麼沒有發現掃描缺席 ── **已證實（程式邏輯）**

1. `monitor.py:17,119`：漏跑判斷是「該 workflow 最新一次 run 的建立時間 > 48 h（雷達）／96 h（收盤）」。雷達 workflow 每天有好幾次空轉的 `retry` run 都算成功，所以 18:20 日報或盤中 quotes 整天沒跑也永遠不會觸發；收盤報告要連續 4 天沒跑才會告警。
2. `monitor.py:91-92`：`quote_coverage_issues` 在**當天完全沒有行情檢查紀錄時直接回傳空清單**，也就是整天 0 次盤中檢查 = 不告警。這正是「當日掃描缺席」沒被抓到的直接原因。
3. 監控只檢查「最近一次」，沒有一份「今天應該完成哪些時段」的預期清單。
4. 監控自己也跑在延遲 3.8 小時的 GitHub schedule 上；`workflow_run` 觸發只在有工作跑完時才會發生，所以上游沒跑，監控也不會跑。
5. 同一個持續中的異常只通知一次（`transition()` 用 `added = current − previous`），因此 health.yml 從 10-14 起連續失敗 11 次以上，最多只會在第一次通知。
6. 沒有任何 GitHub 以外的 dead-man switch，GitHub 延遲或停機時無法自我偵測（`docs/cloud-monitor.md` 也承認這點）。

### 2.4 資料維護 → 研究 → 盤中 → shadow → 通知無法形成閉環

- **晚間重跑把有效結果蓋掉**：`state/radar_events.json` 顯示 9/23、9/30、10/2、10/5、10/14、10/15、10/16 這幾天，其中一次執行的 20 檔**全部**是「還原行情過期」（`radar_data.py:83-84`，Yahoo 最後一根 K 線 ≠ as_of）。目前正式的 `docs/tw_cache.json`（10-17 01:27 產生）20 檔全部是這個狀態，所以 `execution.eligible()` 對所有股票都是 False，即使行情正確也不可能產生進場提醒。本機今天重現 Yahoo 下載正常；runner 上為什麼缺最後一根 K 線，**待日誌確認**（可能是 Yahoo 對 runner IP 限流，或資料尚未更新）。
- 盤中行情的閉環依賴「master 上的 tw_cache 計畫」＋「quotes 執行」＋「有效 z」三者同時成立，而這三者目前都不成立。
- state artifact 還原失敗時會靜默重建空狀態（`restore.py:15-17`「initializing baseline」），去重紀錄可能遺失。

### 2.5 候選池長期沒有有效訊號 ── 分類

統計 9/23–10/16 共 287 筆完成評估的快照（`state/radar_events.json`）：

| 閘門 | 未通過 | 分類 |
|---|---:|---|
| catalyst（催化） | **287 / 287** | **程式／流程問題**：`radar_evidence.json` 為 `{}`，沒有任何自動或半自動的填寫流程，這個閘門在結構上不可能通過 |
| technical（技術） | **287 / 287** | **策略設計問題為主**：`radar.py:44` 依「最貼近 MA20」挑 20 檔，但技術條件要求週線上升且收盤距 20 日高點 1 ATR 內（突破），或回測後收紅。選股方向和觸發條件互相矛盾。也缺少「整理承接」型態 |
| sector / chips / fundamental | 部分 | 混合：資料與市場條件都有 |
| market（廣度 ≥ 0.45） | 少數 | **市場條件**：10-16 廣度 0.455，剛好在門檻上 |
| 另有 152 筆在評估前就被擋下 | 「還原行情過期」等 | **資料缺失**（見 2.4） |

結論：沒有訊號**不是**市場條件造成的。即使市場條件理想，現行程式也不可能產生任何一筆合格訊號。

### 2.6 其他已證實的故障與風險

- **美股報告從 10-14 起連續 4 次失敗**（步驟「產生資料與報告」，約 1 分鐘內結束）；`docs/us_cache.json` 停在 10-12。health.yml 因此從 10-14 起每次都失敗。本機完整重現**成功**（503 檔，品質 99.8%），所以是 runner 環境特有的問題，**待日誌確認**。
- `requirements.txt` 沒有鎖版本：本機安裝到 pandas **3.0.6**、yfinance 1.7.0。runner 每次可能拿到不同的主版本。
- Actions 公告：Node 20 actions 被強制改用 Node 24；**`ubuntu-latest` 從 2026-10-19（今天）開始遷移到 Ubuntu 26**。
- 本機測試：205 個測試全部通過（Python 3.14 + pandas 3.0.6）。**這只代表本機單元測試通過，不代表 Actions 實際運作正常。**

---

## 三、修復計畫（依優先順序）

原則：減少活動零件、所有時效性工作都要能被驗證、失敗時一律不發提醒（fail closed）、狀態只有一份真相來源。交易規格不變，見文末。

### P0 排程（最優先）
1. **改由外部觸發 `workflow_dispatch`**（dispatch 不經過 schedule 佇列）。GitHub cron 只保留為備援觸發，每次執行以 `(交易日, 時段)` 做冪等鎖，重複觸發會直接退出。
2. **盤中改成單一長駐 job**：08:50 啟動，09:00–13:35 每 60–90 秒輪詢一次（遠低於 6 小時 job 上限），取代 66 個 5 分鐘 cron。events 公告輪詢併入同一個迴圈。
3. 刪除 `:35 retry` cron（改由同一時段工作內部重試）以及 monitor 的 `workflow_run` 扇出。
4. 鎖定 `runs-on: ubuntu-24.04`，actions 升級到 Node 24 版本，`requirements.txt` 鎖定版本。

### P0 行情
5. 新的 `quote.py` 解析器：最後成交 = `trade.z@trade.t`（舊格式退回頂層 `z@t`），快照時間 = `tlong`／`t`，最佳一檔買賣價取自 `a`／`b`。每筆標記 `fresh`／`stale_trade`／`invalid` 與原因、來源及兩個時間戳。
6. 進場提醒同時要求：報價快照 ≤ 60 秒、最後成交 ≤ 180 秒、買賣價未倒掛、價差 ≤ 0.5%、成交價落在當日高低區間內，且成交價與買賣價互相一致（作為來源衝突檢查）。任一條件不符就不提醒，**絕不使用舊價格**。
7. 批次重試（每批 3 次，指數退避）；整體有效率低於門檻時，該輪標記為「行情不足」並計入監控，不再算成功。

### P0 監控
8. **時段帳本（slot ledger）**：每個時段工作寫入 `{date, slot, triggered_at, started_at, finished_at, status, quotes_valid_ratio}`。監控依交易日曆算出「今天應有的時段」，逐一比對缺席、遲到（> 15 分鐘）與品質不足。
9. **GitHub 以外的 dead-man switch**：每個時段成功時 ping 外部服務；超時沒收到 ping 由外部服務直接告警。這是唯一能抓到「GitHub 整個沒跑」的方法。
10. 持續中的異常每 24 小時提醒一次並附持續時間，不再只通知一次。

### P1 狀態與閉環
11. 狀態只保留一份：artifact 還原失敗改為**失敗停止**，不再靜默重建；只有明確的 `--bootstrap` 才允許建立空狀態。
12. 同一 as_of 的晚間重跑如果取得的資料比較差，就保留較早的有效評估，不能讓降級結果蓋掉。
13. 修好美股失敗（需要日誌）。

### P2 策略（P0／P1 完成並經過 Actions 實際驗證後才做）
14. 補上「整理承接」型態；依各型態的接近程度挑選深審名單，取代現行「最貼近 MA20」。
15. 狀態分成三級：早期發現 → 等待 → 準備進場。
16. 催化：自動擷取 MOPS 重大訊息作為「待審證據包」，透過 Telegram 回覆核准後寫入。**不會把公告自動當成已審核通過，也不放寬任何門檻**。
17. 合併兩套引擎的重複部分（是否合併由你決定，預設只共用資料層）。

### 測試與驗收（第四階段）
- 單元測試：MIS 新舊格式、`z="-"`、成交過舊、快照過舊、未來時間、買賣倒掛、跨日、重複代號、來源衝突。
- 情境測試：時段漏跑、延遲、API 5xx／逾時後重試恢復、artifact 遺失、重複觸發冪等、提醒去重、通知狀態不明。
- 績效測試：進場價必須晚於提醒時間、不得偷看未來 K 線、成本／停損停利／第 3 日時間出場、樣本不足時不顯示勝率。
- **本機通過 ≠ Actions 運作正常**：PR 合併前用 `workflow_dispatch` 在分支上以 dry-run 驗證；合併後觀察一個完整交易日的時段帳本，才算 Actions 驗證完成。

### 保留不變的交易規格
上市櫃、週一全市場研究、週內維護候選池；持有 2–3 日（第 1 日進場、第 2 日重評、第 3 日時間出場，停損停利可提前）；扣手續費 0.1425%、證交稅 0.3%、滑價 0.1%；進場 R ≥ 2；三類訊號；行情時間戳與來源驗證；候選 ≠ 持股；不下單、維持 shadow；研究候選、次日開盤對照、提醒後模擬、實際成交四本帳分開記錄；樣本不足不宣稱勝率。

---

## 四、P0 實作狀態（分支 `fix/reliability-overhaul-20261019`）

| 項目 | 狀態 | 證據 |
|---|---|---|
| 1 外部派送 | 已實作 `cloudflare/`；**尚未部署**（需要你的 Cloudflare 帳號與 GitHub token） | `cloudflare/test/schedule.test.mjs`，17 個測試 |
| 2 盤中長駐 job | 已實作 `market_radar/session.py` | `tests/test_session.py`，9 個測試 |
| 3 刪除高頻 cron | 已完成；保留 3 個備援 cron | `market-radar.yml` |
| 4 runner／套件鎖定 | 已完成；actions 主版本未升級（目前已被強制改用 Node 24 執行，升級另外評估） | 本機 Python 3.11 + 鎖定版本：238 個測試通過 |
| 5 行情解析 | 已完成 | `tests/test_quote_format.py`，用 10-19 盤中實際資料：舊解析器 1/4 檔有效、新的 3/4 檔有效，剩下 1 檔正確標為成交過舊 |
| 6 進場行情條件 | 快照 ≤ 90 秒、成交 ≤ 180 秒、來源衝突隔離；買賣價差 ≤ 0.5% 沿用原本的 entry_quality | 端到端測試：成交過舊時不提醒；連續兩次新鮮成交只提醒一次 |
| 7 重試 | 已完成，每批 3 次 | `test_one_network_batch_failure_still_returns_other_batch` |
| 8 時段帳本 | 已完成 | `tests/test_session.py`、`tests/test_monitor.py` |
| 9 外部 dead-man switch | 已實作，未部署 | 同 1 |
| 10 持續異常 24 小時重發 | 已完成 | `test_persisting_issue_is_repeated_daily_not_every_run` |

未完成（P1 以後）：artifact 還原失敗時直接停止、晚間重跑不得蓋掉較好的結果、美股失敗（需要日誌）、策略相關項目。


---

## 五、美股失敗與台股「還原行情過期」的共同根因（2026-10-19 補充）

**日誌證據**（登入後讀取）：美股 run 38987749697 在「產生資料與報告」步驟失敗，訊息是 `RuntimeError: 美股有效行情覆蓋率 0.0%`。也就是 503 檔在當次執行全部被品質檢查拒絕。

**排除 IP 封鎖**：在分支 `diag/yahoo-runner` 跑了一個只讀的診斷 workflow（run 39215367469，ubuntu-24.04，對外 IP 52.157.2.249）。Yahoo chart API 的 query1／query2 對 AAPL、2330.TW、6274.TWO 都回 HTTP 200；yfinance 1.7.0 批次下載拿到最新日期（美股 10-16、台股 10-19）。runner 並沒有被擋。

**時間規律**：

| | 失敗 | 成功 |
|---|---|---|
| 美股 daily-us（UTC 開始時間） | 10-01 01:30、10-03 01:23、10-14 01:21、10-15 00:48、10-16 00:41、10-17 00:44（**6/6 落在 00:41–01:30**） | 01:48、01:48、01:54、02:14、02:29、02:32、03:09 |
| 台股 20 檔全部「還原行情過期」（台灣時間） | 09-24 00:01、10-01 02:28、10-03 02:27、10-06 00:19、10-15 02:01、10-16 01:47、10-17 01:27（**7/7 落在 00:01–02:28**） | 這個時段以外的 26 次沒有任何一次發生 |

**結論**：在 Yahoo 各市場的盤後重算時段內，最新一根日 K 會暫時缺失；GitHub 排程延遲 4–8 小時，剛好把執行推進這些時段。美股原本排在 06:37，即使準時執行，也只比觀察到的失敗時段早不到 2 小時，而那個更早的時間從來沒有實際執行過，所以沒有可用性證據。這個結論是時間上的相關性（美股 n=13、台股 n=33），還沒有拿到 Yahoo 官方的說明。

**修正**（分支 `fix/yahoo-eod-window`）：
- us-close 改到台灣時間 11:00（03:00 UTC），GitHub 備援 cron 同步調整。11 月美國改回標準時間後，觀察到的時段可能往後移約 1 小時，到時要重新確認。
- `radar_data.adjusted_histories`：同一個 as_of 若已經成功取得並驗證過還原日 K，晚到的重跑在 Yahoo 本次缺資料時直接沿用那份資料。只沿用**同一個 as_of**，而且收盤價必須與官方收盤相差不到 0.5%，不會用前一天的資料冒充今天。
- 美股品質失敗時印出各拒絕原因的筆數，下次可以直接從日誌確認。
