# 每日股票觀察清單

自動抓台股（上市+上櫃）與美股（S&P 500）的股價、技術指標、財經新聞，整理成一份「當日觀察清單」網頁報告。台股、美股各自在收盤後獨立排程更新（台股約14:00、美股約台灣時間06:30），報告會同時顯示兩邊各自最新的結果。

**本工具只負責蒐集與整理資料，不做任何 AI 判斷或投資建議。** 想要深入分析，請自己把報告拿去問 Claude 或其他工具。

---

## 檔案說明

| 檔案 | 做什麼 |
|---|---|
| `main.py` | 主程式，執行這個就會跑完整流程 |
| `config.py` | 篩選門檻、均線天數等可調整的數字 |
| `fetch_tw_universe.py` / `fetch_us_universe.py` | 抓股票代號清單 |
| `fetch_tw_official.py` | 台股上市（TWSE）股價，用證交所官方免費 API，一次抓全市場，很快 |
| `fetch_tw_stock.py` | 台股上櫃（TPEx）股價，用 FinMind 一檔一檔查；也是上市股票的備援來源 |
| `fetch_tw_all.py` | 把上面兩個台股來源組起來，自動判斷要不要切換備援 |
| `fetch_us_stock.py` | 美股（S&P 500）股價，用 yfinance 批次抓取 |
| `indicators.py` | 算均線、漲跌幅、成交量比 |
| `screener.py` | 依門檻篩出值得注意的標的 |
| `fetch_news.py` | 抓財經新聞標題 |
| `screener.py`（`pick_highlights`）| 從觀察清單裡挑出「今日焦點」，並算訊號評分、貼標籤（漲停/跌停、爆量、均線同步）|
| `tracking.py` | 記錄「今日焦點」後續股價表現（純資料記錄，不做預測或建議）|
| `data_cache.py` | 台股、美股分開排程執行，各自把結果存成 `docs/tw_cache.json` / `docs/us_cache.json`，讓網站隨時能組合出「兩邊各自最新」的報告 |
| `report.py` | 產生 `docs/index.html` 報告網頁 |
| `.github/workflows/daily-tw.yml` | 台股收盤排程（約台灣時間 14:00）|
| `.github/workflows/daily-us.yml` | 美股收盤排程（約台灣時間 06:30）|

---

## 第一步：本機安裝與測試

1. 安裝 [Python](https://www.python.org/downloads/)（3.10 以上）
2. 打開終端機（cmd 或 PowerShell），切換到這個資料夾，安裝套件：
   ```
   pip install -r requirements.txt
   ```
3. 先跑小規模測試（只抓 20 檔台股、30 檔美股），確認沒有錯誤：
   ```
   python main.py --tw-limit 20 --us-limit 30
   ```
   （不加 `--market` 參數時，預設兩個市場都跑，方便本機測試）
4. 跑完後，打開 `docs/index.html`（直接用瀏覽器開啟這個檔案）看看報告長什麼樣子。
5. 確認沒問題後，可以跑正式全量版本：
   ```
   python main.py
   ```
   正常情況下（上市股票走官方 API）**幾分鐘內就會跑完**，只有上櫃股票（約900檔）要透過 FinMind 一檔一檔查，會佔掉大部分時間，抓完全部大約 **1.5~2 小時**。如果哪天官方 API 剛好故障，程式會自動切換成全部用 FinMind 查（約 3~4 小時），報告一樣會產生出來，只是比較慢，這是設計好的容錯機制，不是錯誤，詳見下方「常見問題」。

   正式排程時，台股、美股是分開執行的（見下方第三步），本機也可以個別測試：
   ```
   python main.py --market tw     只跑台股
   python main.py --market us     只跑美股
   ```

---

## 第二步：申請 FinMind 免費 token（強烈建議）

台股上市股票走官方 API，不需要 token。但上櫃股票（約900檔）、以及官方 API 故障時的備援，都還是要用 FinMind，FinMind 有查詢次數限制：沒有 token 是每小時 300 次，註冊免費帳號拿到 token 後可以提高到每小時 600 次。沒有 token 的話，萬一觸發備援機制查全部台股，會超過 GitHub 排程「單次執行最長 6 小時」的限制，跑到一半被強制中斷，所以這一步還是建議要做。

1. 到 [FinMind 官網](https://finmindtrade.com) 免費註冊帳號（只要 email，不需要信用卡）
2. 登入後到帳號頁面，複製你的 token（一長串英數字）
3. 先留著，等一下設定 GitHub 排程時會用到

---

## 第三步：把專案放到 GitHub，變成一個自動更新的網站

### 3-1　建立 GitHub 帳號與 repository

1. 到 [github.com](https://github.com) 免費註冊帳號（不需要信用卡）
2. 右上角點 `+` → `New repository`，取個名字（例如 `stock-watchlist`），選 **Public**，其他選項預設即可，按 `Create repository`

### 3-2　把程式碼上傳上去

在這個資料夾的終端機輸入（把 `你的帳號` 和 `stock-watchlist` 換成你自己的）：
```
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/你的帳號/stock-watchlist.git
git push -u origin main
```
（如果沒安裝過 git，需要先安裝 [Git for Windows](https://git-scm.com/download/win)；第一次 push 時瀏覽器會跳出來要你登入 GitHub 帳號授權）

### 3-3　把 FinMind token 設定成 GitHub 密鑰

1. 到你的 repository 頁面 → `Settings` → 左側選單 `Secrets and variables` → `Actions`
2. 點 `New repository secret`
3. Name 填 `FINMIND_TOKEN`，Value 貼上第二步拿到的 token，按 `Add secret`

### 3-4　開啟 GitHub Pages

1. 同樣在 `Settings` → 左側選單 `Pages`
2. `Build and deployment` 底下的 `Source` 選 `Deploy from a branch`
3. `Branch` 選 `main`，資料夾選 `/docs`，按 `Save`
4. 存檔後 GitHub 會給你一個網址，長得像 `https://你的帳號.github.io/stock-watchlist/`（可能要等 1-2 分鐘才會生效）

### 3-5　手動測試一次排程

台股、美股是兩個獨立的排程（`Daily TW Close Report`、`Daily US Close Report`），建議都手動測試一次：

1. 到 repository 頁面上方的 `Actions` 分頁
2. 左側點 `Daily TW Close Report`，右邊點 `Run workflow`，`tw_limit` 欄位可以先填 `20`（快速測試用），按綠色的 `Run workflow`
3. 跑完後（頁面會顯示綠勾勾），打開你的 GitHub Pages 網址，應該就能看到台股部分的報告了
4. 左側點 `Daily US Close Report`，一樣按 `Run workflow`（`us_limit` 填 `20` 快速測試），跑完後美股部分也會出現
5. 確認都沒問題後，之後可以再各自手動跑一次不填數量限制（跑全部），或者直接放著讓它們照排程自動跑

之後每天約台灣時間 **14:00（台股收盤後）** 和 **06:30（美股收盤後、台股開盤前）**，GitHub 會自動執行、更新報告，你只要打開網址看就好，完全不用自己動手。

---

## 第四步：設定 Telegram 推播通知（optional，但推薦）

報告更新完成時，自動發訊息到你的 Telegram，不用自己記得去開網站看。

1. 在 Telegram 搜尋 `@BotFather`，傳送 `/newbot`，照指示取名字，完成後它會給你一組 **bot token**（長得像 `123456789:ABCdefGhIJKlmNoPQRstuVWXyz`）
2. 找到你剛建立的 bot，隨便傳一句話給它（例如「hi」），這是為了讓它有你的對話紀錄
3. 到瀏覽器打開這個網址（把 `<TOKEN>` 換成你的 bot token）：`https://api.telegram.org/bot<TOKEN>/getUpdates`，在回傳的內容裡找到 `"chat":{"id":數字,...}`，這串數字就是你的 **chat id**
4. 回到 GitHub repository → `Settings` → `Secrets and variables` → `Actions`，新增兩個密鑰：
   - `TELEGRAM_BOT_TOKEN`：貼上步驟1的 bot token
   - `TELEGRAM_CHAT_ID`：貼上步驟3的 chat id
5. 設定好之後，下次排程跑完就會自動發訊息到你的 Telegram。沒設定這兩個密鑰的話，程式會自動跳過這一步，不影響報告正常產生。

---

## 想調整篩選條件？

打開 `config.py`，可以改的幾個常用數字：

- `PRICE_CHANGE_PCT_THRESHOLD`：漲跌幅超過多少 % 才列入（預設 3.0）
- `VOLUME_RATIO_THRESHOLD`：成交量是均量幾倍以上才列入（預設 1.5）
- `MA_SHORT` / `MA_LONG`：均線天數（預設 5 日、20 日）

改完存檔，下次執行（本機或排程）就會套用新設定，不需要動其他程式。

想調整排程時間，打開 `.github/workflows/daily-tw.yml`（台股）或 `daily-us.yml`（美股），修改 `cron` 那一行（時間是 UTC，台灣時間要減 8 小時反推）。

---

## 常見問題

**為什麼有時候跑很快、有時候跑很久？**
台股上市股票（約1,200檔）走證交所官方 API，一次就能抓到全市場資料，很快。上櫃股票（約900檔）沒有官方的歷史查詢 API，只能用 FinMind 一檔一檔查，這部分固定要 1.5~2 小時。如果哪天官方 API 故障，程式會自動偵測到，改成上市股票也用 FinMind 查，全部跑完會拉長到 3~4 小時——這是設計好的容錯機制（報告上會註明「今日使用FinMind備援」），確保故障當天報告還是會產生出來，只是比較晚。

**怎麼知道今天的報告是用官方 API 還是備援模式？**
報告最上面的說明區塊會寫「台股資料來源：官方API（上市）＋FinMind（上櫃）」或「FinMind 備援」，還會顯示「成功取得 X/Y 檔」，如果失敗檔數突然變多，代表當天資料不完整，篩選結果可能不準，判斷時要多留意。

**報告資料準不準、即時嗎？**
資料來源是證交所官方 API、FinMind、yfinance、Yahoo 新聞 RSS，都是收盤後的資料，不是即時報價，僅供參考，不構成投資建議。

**想只看自己在意的股票，不要全市場，可以嗎？**
可以，把 `fetch_tw_universe.py` / `fetch_us_universe.py` 回傳的清單改成你自己列的清單即可，或跟我說一聲，我可以幫你加一個「自訂清單模式」的開關。

**加密貨幣、當沖訊號之類的功能呢？**
第一版刻意不做，之後想擴充可以再討論。

**為什麼台股、美股更新時間不一樣？**
因為兩邊收盤時間本來就不同，各自在「自己收盤後」更新，資料才會是完整的當天資料，而不是等到隔天才看到前一天的東西。網站會同時顯示「台股最新一次」和「美股最新一次」的結果，就算兩邊不是同一時間更新的也沒關係。

**手機上要怎麼看？**
直接用手機瀏覽器打開 GitHub Pages 網址就是手機版排版，不需要另外裝 App。想要桌面圖示的話，瀏覽器選單裡通常有「加到主畫面」的功能，加下去就有個像 App 一樣的圖示可以點。
