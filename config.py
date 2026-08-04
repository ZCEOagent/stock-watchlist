"""
所有「你可能會想自己調整」的數字都放在這個檔案。
不用懂程式也可以改：找到對應的數字，改完存檔，下次執行就會套用新設定。
"""

# ------- 篩選門檻：符合下面「任一項」就會被列入報告 -------
PRICE_CHANGE_PCT_THRESHOLD = 3.0      # 漲跌幅超過正負多少 % 才算注意（例如 3.0 代表漲跌超過3%）
VOLUME_RATIO_THRESHOLD = 1.5          # 成交量是「近20日均量」的幾倍以上才算注意
MA_CROSS_LOOKBACK_DAYS = 1            # 判斷「今天剛站上/跌破均線」時，往前比對幾天

# ------- 均線設定 -------
MA_SHORT = 5     # 短期均線天數
MA_LONG = 20     # 長期均線天數（同時也用來算「均量」的天數）

# ------- FinMind（台股資料來源）-------
# 到 https://finmindtrade.com 免費註冊帳號拿 token，可以把每小時查詢上限從 300 次提高到 600 次。
# 本機測試可以先不設定（用預設的 300 次/小時上限）。
# 正式排程建議設定，做法見 README。
#
# 本機執行時，token 優先順序：環境變數 FINMIND_TOKEN > finmind_token.txt 檔案內容。
# finmind_token.txt 已加進 .gitignore，不會被上傳到 GitHub，只放在你自己的電腦。
# GitHub 排程用的 token 是另外設定在 GitHub Secrets，跟這裡無關（見 README 第三步）。
import os


def _load_finmind_token():
    token = os.environ.get("FINMIND_TOKEN", "")
    if token:
        return token
    token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finmind_token.txt")
    if os.path.exists(token_path):
        with open(token_path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


FINMIND_TOKEN = _load_finmind_token()

# 有 token 時可以查得比較快，用這個節流秒數控制「每次查詢間隔幾秒」，避免超過每小時上限
FINMIND_REQUEST_INTERVAL_SEC = 6.5 if FINMIND_TOKEN else 13.0

# ------- 台股清單篩選 -------
# FinMind 的股票清單裡也包含 ETF、特別股等，這裡先只保留「4碼純數字」的一般普通股代號
TW_ONLY_4_DIGIT_COMMON_STOCK = True

# ------- 資料抓取的歷史天數 -------
# 需要抓夠多天才能算出 MA_LONG 均線，這裡抓多一點天數以防假日、停牌等造成資料不足
HISTORY_CALENDAR_DAYS = 60

# ------- 輸出檔案位置 -------
REPORT_HTML_PATH = "docs/index.html"

# ------- 新聞來源（RSS，不需要金鑰）-------
TW_NEWS_RSS_FEEDS = [
    "https://tw.stock.yahoo.com/rss?q=tw-market",
    "https://tw.news.yahoo.com/rss/finance",
]
US_NEWS_RSS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
]
NEWS_ITEMS_PER_FEED = 10   # 每個新聞來源最多取幾則
NEWS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# ------- 美股清單來源（S&P 500 成分股，免費公開清單）-------
SP500_LIST_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
