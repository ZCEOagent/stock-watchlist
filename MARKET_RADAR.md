# 台股全市場 Radar 摘要（2026-09 整合）

這是現有 `radar.py` 波段策略之外的全市場初篩與 Telegram 摘要層，套件名為 `market_radar`，不取代原策略、模擬績效或私人帳本。原 `main.py`、台股／美股排程、網站及原通知邏輯全部保留。本文件只描述新增功能；既有策略見 RADAR.md、帳本見 PHASE2.md。

## 架構

`官方批次資料 → market_radar.sources → SQLite 公開市場歷史 → engine 評分 → 原波段策略閘門 → reports 四類摘要 → 既有 Telegram Bot`

- 重用 `indicators.compute_indicators()`、`market_clock.last_completed_session()`。
- 從原 Bot 的 `.runtime/tw_history.json` 讀取已驗證歷史價格；透過現有 `radar-tw-` Actions Cache 分享，不重新跑長時間的逐檔行情下載。
- 讀取原 `docs/tw_cache.json` 的策略結果；只有原策略 live、同交易日品質通過、已觸發且未過期的凍結計畫，才可能和基本面初篩一起升級 BUY。原 shadow 設定不改動。
- 新增排程 `.github/workflows/market-radar.yml`，不改原 workflows。專屬 concurrency 防止重疊更新自身狀態，日常不推送 Git commits。
- 公開市場歷史、快照與送達紀錄保存在 `market-radar-state` Artifact（90日）。持股、停損與報告文字不放 Artifact 或公開 Git；報告只暫存 runner 並發至已設定 Telegram chat。

## 排程（Asia/Taipei）

| 工作 | 時間 | 通知 |
|---|---|---|
| 全上市櫃初篩 | 每日18:20 | 四類摘要，沒有訊號也簡短回報 |
| 重大公告 | 週一至五08:17至22:47，每30分鐘 | 只通知新事件；一般追蹤每天最多3則，實際持股另處理；每批至多5則 |
| 週一完整決策 | 每週一08:00 | 四類摘要與完整文字附檔 |

GitHub排程可能延遲，公告API亦非逐筆即時流。這版是排程提醒：盘中只查公告，價格風險使用每日收盤。未提供24小時或毫秒級保證。原14:17/21:17台股、06:37美股及健康檢查不變。

## Telegram只集中四類

1. 今日新進雷達：與前一不同日期、相同模型版本快照比較；首次執行只建立基準，最多3檔。
2. 評分大幅變化：同一組已知項目、完整度至少60%、變動≥10分；新增資料不當成營運改善。重大公告附「待核實，尚未改分」，不冒稱已改分。
3. 持股風險警報：僅針對明確設定持股。資料不足也提示；多於3檔用完整附檔，避免隱藏其餘風險。
4. 本週真正值得考慮交易的標的：最多5個符合全部條件的候選；2330、6274保留優先觀察說明。無BUY就明說不必交易。

原既有健康異常通知保留，避免因追求四類版面而隱藏執行故障。原一般更新通知仍預設關閉。

## 資料來源

[證交所規格](https://openapi.twse.com.tw/v1/swagger.json)；[櫃買規格](https://www.tpex.org.tw/openapi/swagger.json)。兩者根網址為 https://openapi.twse.com.tw/v1/ 與 https://www.tpex.org.tw/openapi/v1/。

| 資料 | 上市端點 | 上櫃端點 |
|---|---|---|
| 名單 | opendata/t187ap03_L | mopsfin_t187ap03_O |
| 月營收 | opendata/t187ap05_L | mopsfin_t187ap05_O |
| 一般業損益 | opendata/t187ap06_L_ci | mopsfin_t187ap06_O_ci |
| PE/PB/殖利率 | exchangeReport/BWIBBU_ALL | tpex_mainboard_peratio_analysis |
| 收盤 | exchangeReport/STOCK_DAY_ALL | tpex_mainboard_daily_close_quotes |
| 重大訊息 | opendata/t187ap04_L | mopsfin_t187ap04_O |

四碼普通股全市場初篩；金融等特殊行業保留在名單，但一般業損益模型不適用的項目標示未知。金額是新台幣千元、EPS是元、成交量是股。出表日不是公告可知悉時點。保留來源網址、資料期間、來源日期與取得時間。

## 評分

| 模組 | 權重 | 得分規則 |
|---|---:|---|
| 單月營收YoY | 20 | ≥30%得20；≥15%得15；>0得10；其餘0 |
| 營收加速代理 | 5 | 單月YoY正且高於累計YoY得5，否則0 |
| 累計EPS正值 | 5 | >0得5，否則0 |
| 同期累計EPS年增 | 15 | ≥30%得15；≥15%得10；>0得5；其餘0。去年≤0不計成長率 |
| 營益率 | 10 | ≥15%得10；≥8%得7；>0得3；其餘0 |
| 業外依賴 | 5 | abs(業外)/abs(稅前)≤20%得5；≤50%得2；其餘0 |
| 現金流 | 5 | 同期CFO/淨利≥1得5；>0得2；其餘0；淨利≤0為未知 |
| 同業PE | 20 | PE/同業中位數≤0.8得20；≤1得15；≤1.3得8；其餘0；至少5家樣本 |
| MA20 | 10 | 收盤介於MA20至1.15倍得10；更高得5；低於0 |
| 量比 | 5 | 1至3倍得5；<1得2；>3得0 |

分數＝已知項目得分÷已知權重×100；完整度＝已知權重。缺值不補零或中性分。候選池要求≥70分、完整度≥60%、來源正常與行情日期符合原交易日曆。

BUY還要求≥80分、完整度≥90%、EPS同期成長與CFO均為正、PE≤同業中位數、收盤≥MA20、無風險旗標，且通過原波段策略。HOLD只用於真實持股；WATCH表示待確認；REMOVE是核實EPS非正／CFO為負，或完整度≥80%且分數<50，供人工檢討，不自動交易。

這些是透明起始規則，不是經績效驗證的勝率模型。事件與競爭優勢不靠詞頻加分。原策略凍結的進出場與期限不因新初篩重設。

## 缺口與風險

- 官方最新損益快照初期缺去年同期累計EPS與CFO；明列缺項，未核實CFO不允許BUY。原逐檔FinMind資料仍由原策略使用，不把單季EPS硬加成累計。
- 歷史MA需原行情快取或每日累積至少21筆有效收盤；初期可無技術分。新資料不回填歷史訊號。
- 營收期間85日／出表45日；財報期末210日／出表10日；PE7日；收盤還必須符合原交易日曆。這些門檻可造成連假或停牌待確認。
- 原還原價格策略仍是交易閘門；全市場初篩用未還原價格，單日斷層≥15%提示核對公司行動，不宣稱解決所有除權息影響。
- 名單任一市場失敗或合計少於1000檔就停止比較、保留上次排名並發失敗摘要。其他來源缺漏不得造成新進訊號。
- 關鍵字只表示需查原文，澄清公告也可能被提示，不等於已證實舞弊／違約。一般公告只追蹤候選前5名、2330、6274與實際持股，並按日限量。
- Telegram成功才記送達；明確拒絕可重試，模糊逾時標為uncertain，需人工核對，不自動重送。Artifact在工作結束保存；極端強制終止／備份失效仍可能失去送達狀態，不承諾exactly-once。
- 市場價格保存180日、營收3年、財報跨年、快照／公告／送達紀錄90日。公開repo的Artifact不是私密保險箱，裡面不放私人持股或報告文字。
- 這層沒有新增下單、持股交易、期貨策略、私人帳本啟動或付費訂阅。

## 使用及設定

沿用TELEGRAM_BOT_TOKEN／TELEGRAM_CHAT_ID。新增選填Secret `RADAR_HOLDINGS_JSON`，格式為股票代號映射到物件，可含正數stop；不提供即表示未啟用此摘要層的持股警報。這是唯讀監控名單，不取代PHASE2私人實際成交帳本，不將2330/6274假設為持股。

選填Secret `RADAR_FINANCIALS_JSON`：每代號可包含period（例如2026Q2）、basis=YTD、source（核實過的HTTPS財報網址）、available_at（YYYY-MM-DD）、prior_year_eps與operating_cash_flow（千元）。期間須精確匹配；缺值、無效值或過期為未知。操作者須確認股本基準及累計口徑，不應填虛構估計。

```
python -m unittest discover -s tests -v
python -m market_radar.cli daily
python -m market_radar.cli weekly
python -m market_radar.cli events
```

預設不發送，報告在`.radar/reports/`；明確加`--send`才通知既有chat。GitHub手動工作預設也不發送，正式排程則發送。只有預設分支可讀寫正式狀態與發送。停用新增workflow即可停止此摘要層，原Bot不受影響。

## 之後可替換的付費來源

- [FinMind](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)：批次歷史財報、現金流與公司行動；全市場批次資料可能有會員等級限制，先確認方案，單季EPS／股本調整須另處理。
- [TEJ](https://www.tejwin.com/)：可比財報、公司行動、研究／回測資料；確認公告時点、歷史版本、欄位與授權，再以sources adapter替換。
- 證交所／櫃買或券商授權行情：盤中報價與風險提醒，需要另加常駐排程與行情介面，不能用GitHub排程宣稱即時。

未購買或啟用額外服務。參考：[Telegram API](https://core.telegram.org/bots/api)、[GitHub排程限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
