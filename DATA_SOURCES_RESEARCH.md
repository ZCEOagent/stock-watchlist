# 台股 Radar 財報補充來源查核

查核日期：2026-09-23。範圍：已讀 sources.py、engine.py；官方 OpenAPI、兩份公開 inline XBRL、FinMind 無金鑰樣本與官方說明。沒有讀取任何秘密、沒有部署或更改程式。

## 建議

優先新增 MOPS inline XBRL adapter：每公司每財報期間抓一次當期合併報表，從同一份文件取得本期 YTD EPS、去年同期比較 EPS、YTD 營業現金流及總淨利。避免把歷年不同股數基準的 FinMind 單季 EPS 加總。沿用官方 bulk 最新財報決定需要補齊的公司/period，以 CompanyID、Year、Quarter、ReportCategory、contexts 與目前 bulk EPS/淨利交叉驗證。

## 官方端點與實測

- [6274 2026Q2 inline XBRL](https://mopsov.twse.com.tw/server-java/t164sb01?step=1&CO_ID=6274&SYEAR=2026&SSEASON=2&REPORT_ID=C)
- [2330 2026Q2 inline XBRL](https://mopsov.twse.com.tw/server-java/t164sb01?step=1&CO_ID=2330&SYEAR=2026&SSEASON=2&REPORT_ID=C)
- [官方 XBRL 分類標準說明](https://wwwc.twse.com.tw/rwd/XBRL/standard)
- [TWSE OpenAPI](https://openapi.twse.com.tw/)、[TPEX OpenAPI](https://www.tpex.org.tw/openapi/)

上述兩個 mopsov URL 均 HTTP 200、不需登入；同樣路徑的 mops.twse.com.tw host 實测 404。SYEAR 是西元；REPORT_ID=C 為合併，仍須讀取報表 ReportCategory 驗證，不應自動混用個體與合併。這是公開報表網頁，非承諾穩定的版本化 API；需 schema 失敗關閉、快取、退避。舊 CSV/大量下载入口本次未找到並實測出可取代此路徑的可靠無登入 API；不應把未驗證的端點視為可用。

原始位元組樣本位於本工作區 work/xbrl-samples/2330-2026Q2.html、6274-2026Q2.html，不在 repo。外層宣告 Big5，內部 XML 卻宣告 UTF-8；實際以 CP950 解碼中文正確，直接 UTF-8 有破字。可先解析 ASCII tag/數值但應保留原始 bytes。

|公司|本期YTD EPS|同報表去年同期EPS|本期YTD CFO（千元）|本期總淨利（千元）|
|---|---:|---:|---:|---:|
|2330|49.33|29.31|1,482,341,242|1,279,582,227|
|6274|12.40|4.79|-2,282,493|3,601,714|

這些為上列官方報表實際取樣，不是預測。台燿的負營業現金流必須保留，現行 engine 硬 veto 將導致 REMOVE；不能為補齊而調正或抹除風險。

## 解析契約

- `tifrs-notes:CompanyID`、`Year`、`Quarter`：公司及期別。
- `tifrs-notes:ReportCategory`：兩份都是 `Consolidated report`。
- `ifrs-full:BasicEarningsLossPerShare`：EPS；不要以 `...FromContinuingOperations` 偷代基本EPS全部損益。
- `ifrs-full:CashFlowsFromUsedInOperatingActivities`：營業活動淨現金流。
- `ifrs-full:ProfitLoss`：合併總淨利（與 CFO 的合併範圍一致）。
- 目前兩份本期 context ID 都是 `From20260101To20260630`，比較期 `From20250101To20250630`；不能依 ID 命名硬猜，應解析 xbrli:context 的 entity identifier、startDate、endDate，排除 scenario/segment 維度，避免抓到權益變動表的同名標籤或單季數。
- 同一報表另含 4/1–6/30 單季 EPS，必須排除。exact YTD start=1/1、end=期末；非曆年公司應額外處理，無法確認時不硬填。
- ix:nonFraction 數值 `sign`、`scale` 必須處理：6274 CFO 是文字 `2,282,493`、scale=3、sign=-、unitRef=TWD，真值 -2,282,493,000 元；bulk 財報是千元，所以交給現有 engine 前除 1000。EPS scale=0、unitRef=EarningsPerShare（TWD/shares）。`decimals=-3` 是精度不是乘數。
- 不同 taxonomy 年版仍可採 namespace/local-name 正確辨識；不能只比前綴字串而不核對 namespace。
- 應拒絕不支持 format、nil、衝突重複值、未知單位。可接受一致重複值。

## 日期與回測

2330 有 ReviewAuditDate=2026-08-11；6274=2026-07-29。這是查核/核閱日期，不等於公開上傳 timestamp。董事會通過說明同樣不能無條件作為可取得日期。HTTP Date 為這次回應時間，也不是公告日。本次未在文件找到可直接信賴的公告精確時間。

建議保存 `observed_at` 首次成功取得時間、`fetched_at`、內容 hash、原始URL；`available_at` 若既有 schema 必需則使用保守首次觀測日並標註 basis=observed，不應回填到期末或核閱日假裝歷史可用。財報發布/更正重大訊息可觸發強制刷新。今日可用評分與 point-in-time 回測必須分開。

## FinMind 驗證與限制

[FinMind 官方基本面文件](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)明示 EPS 是各季財報公布當下的單季原始值，配股/分割後股數基準可能不同，不能直接跨季相加。其官方範例說明 3081 配股導致相加與累計不符。因此不推薦 FinMind EPS 加總作本系統 exact YTD 補件。

端點 `https://api.finmindtrade.com/api/v4/data`；參數 dataset、data_id、start_date、end_date；資料欄 date/stock_id/type/value/origin_name。[官方 SDK](https://github.com/FinMind/FinMind/blob/master/FinMind/data/data_loader.py)將季度日期轉季底，不代表實際公告日。

無token取樣成功：
`?dataset=TaiwanStockCashFlowsStatement&data_id=6274&start_date=2026-03-31&end_date=2026-06-30`
回傳 `CashFlowsFromOperatingActivities`：3/31=-1,567,736,000 元、6/30=-2,282,493,000 元。6/30等於官方YTD，證明此樣本 CFO 為累計，不能 Q1+Q2 再加。這是兩個期間一檔公司的實測，不宜推定所有歷史 period 都無例外。

官方文件列按日期取得全市場功能限 backer/sponsor；免費逐檔適合節流補缺，不適合每次全市場反覆上千次請求。本次未查帳戶 tier、未用 token。若採用 FinMind，須單獨來源健康度與權限/用量檢查，不能默默認為現有 token 支援批量。

## 節流與持續刷新建議（工程判斷，不是官方配額）

兩份輕量取樣約 0.78–0.94 秒/檔，大小約 0.5–0.75M字元，不能推估為全市場服務保證。建議單並行、每請求至少间隔1秒、遇429/5xx退避並停止本輪高負載；逐批50–100檔、持續保存游標。首次全市場可能需數十分鐘到數小時，不能塞在每半小時公告任務內。先2330/6274及缺資料候選，再補全一般業；成功的 period 快取至少一日，正常週期每7天刷新及公告重編觸發刷新。每日僅新增period、到期刷新、未完成補件重試，報告標實際覆蓋率。全部覆蓋前不可宣稱全市場財報已補齊。
