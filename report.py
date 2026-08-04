"""
把篩選出來的觀察清單 + 新聞，整理成一份 HTML 報告（docs/index.html）。
純靜態網頁，沒有任何外部連線需求，手機/電腦瀏覽器打開就能看。
"""
import html
import datetime

import config


def _change_color(change_pct: float) -> str:
    # 台灣習慣：紅漲、綠跌
    if change_pct > 0:
        return "#c0392b"
    if change_pct < 0:
        return "#1e8449"
    return "#555555"


def _stock_rows_html(watchlist, price_prefix=""):
    rows = []
    for item in watchlist:
        color = _change_color(item["change_pct"])
        sign = "+" if item["change_pct"] > 0 else ""
        ma_status = "、".join(
            f"{d}{n}日線" for d, n in
            [(item["cross_short"], config.MA_SHORT), (item["cross_long"], config.MA_LONG)]
            if d
        ) or "—"
        volume_ratio = f'{item["volume_ratio"]}倍' if item["volume_ratio"] is not None else "—"
        reasons = "、".join(item["reasons"])

        rows.append(f"""
        <tr>
          <td>{html.escape(item["id"])}</td>
          <td>{html.escape(item["name"])}</td>
          <td>{price_prefix}{item["close"]}</td>
          <td style="color:{color}; font-weight:bold;">{sign}{item["change_pct"]}%</td>
          <td>{volume_ratio}</td>
          <td>{html.escape(ma_status)}</td>
          <td>{html.escape(reasons)}</td>
        </tr>""")
    return "\n".join(rows) if rows else '<tr><td colspan="7" class="empty">今天沒有標的符合篩選條件</td></tr>'


def _news_list_html(news_items):
    if not news_items:
        return "<p class=\"empty\">目前沒有抓到新聞</p>"
    items = "\n".join(
        f'<li><a href="{html.escape(n["link"])}" target="_blank" rel="noopener">{html.escape(n["title"])}</a></li>'
        for n in news_items
    )
    return f"<ul>{items}</ul>"


def generate_html(tw_watchlist, us_watchlist, tw_news, us_news,
                   tw_scanned, tw_success, us_scanned, us_success, tw_data_source):
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    tw_failed = tw_scanned - tw_success
    us_failed = us_scanned - us_success

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{today} 每日觀察清單</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
          max-width: 960px; margin: 0 auto; padding: 16px; background: #fafafa; color: #222; }}
  h1 {{ font-size: 1.4em; }}
  h2 {{ margin-top: 2em; border-bottom: 2px solid #ddd; padding-bottom: 4px; }}
  .meta {{ color: #777; font-size: 0.9em; margin-bottom: 1.5em; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; }}
  th, td {{ border: 1px solid #e0e0e0; padding: 6px 10px; text-align: right; font-size: 0.95em; }}
  th {{ background: #f0f0f0; text-align: center; }}
  td:nth-child(1), td:nth-child(2), td:nth-child(6), td:nth-child(7) {{ text-align: left; }}
  .empty {{ text-align: center; color: #999; padding: 20px; }}
  ul {{ padding-left: 1.2em; }}
  li {{ margin-bottom: 6px; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .table-wrap {{ overflow-x: auto; }}
</style>
</head>
<body>
  <h1>{today} 每日觀察清單</h1>
  <div class="meta">
    產生時間：{now}<br>
    台股資料來源：{html.escape(tw_data_source)}｜成功取得 {tw_success}/{tw_scanned} 檔{f'（{tw_failed} 檔取得失敗）' if tw_failed else ''}，篩出 {len(tw_watchlist)} 檔<br>
    美股(S&amp;P500)：成功取得 {us_success}/{us_scanned} 檔{f'（{us_failed} 檔取得失敗）' if us_failed else ''}，篩出 {len(us_watchlist)} 檔
    <br>本報告僅為資料整理，不構成任何投資建議。
  </div>

  <h2>台股觀察清單</h2>
  <div class="table-wrap">
  <table>
    <tr><th>代號</th><th>名稱</th><th>收盤價</th><th>漲跌幅</th><th>量比(20日均量)</th><th>均線狀態</th><th>符合原因</th></tr>
    {_stock_rows_html(tw_watchlist)}
  </table>
  </div>

  <h2>美股觀察清單（S&amp;P 500）</h2>
  <div class="table-wrap">
  <table>
    <tr><th>代號</th><th>名稱</th><th>收盤價</th><th>漲跌幅</th><th>量比(20日均量)</th><th>均線狀態</th><th>符合原因</th></tr>
    {_stock_rows_html(us_watchlist, price_prefix="$")}
  </table>
  </div>

  <h2>台股相關新聞</h2>
  {_news_list_html(tw_news)}

  <h2>美股相關新聞</h2>
  {_news_list_html(us_news)}

</body>
</html>
"""


def save_report(tw_watchlist, us_watchlist, tw_news, us_news,
                 tw_scanned, tw_success, us_scanned, us_success, tw_data_source, path=None):
    path = path or config.REPORT_HTML_PATH
    content = generate_html(
        tw_watchlist, us_watchlist, tw_news, us_news,
        tw_scanned, tw_success, us_scanned, us_success, tw_data_source,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
