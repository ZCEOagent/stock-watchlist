"""
把篩選出來的觀察清單 + 新聞，整理成一份 HTML 報告（docs/index.html）。
純靜態網頁，沒有任何外部連線需求，手機/電腦瀏覽器打開就能看。

視覺設計說明（給以後維護的人看）：
- 顏色是照色盲可辨識度驗證過的組合（紅=categorical slot 8、綠=categorical slot 6），
  而且任何地方都不是「只靠顏色」傳達漲跌——一定會有文字的 +/- 符號和數字同時存在。
- 「今日焦點」用 stat-tile 卡片呈現最值得看的幾檔，下面才是完整表格，
  讓不想看全部 2000 多檔的人也能一眼抓到重點；想看全部的人可以往下捲動。
"""
import html
import datetime

import config
from screener import pick_highlights

# ------- 色票（已用 dataviz 六項檢查工具驗證過色盲可辨識度）-------
COLOR_UP = ("#e34948", "#e66767")      # 紅漲：(亮色模式, 暗色模式)
COLOR_DOWN = ("#008300", "#008300")    # 綠跌
COLOR_NEUTRAL = ("#898781", "#898781")


def _fmt_change(change_pct: float) -> str:
    sign = "+" if change_pct > 0 else ("" if change_pct < 0 else "±")
    return f"{sign}{change_pct}%"


def _change_class(change_pct: float) -> str:
    if change_pct > 0:
        return "up"
    if change_pct < 0:
        return "down"
    return "neutral"


def _ma_status_text(item) -> str:
    parts = [
        f"{d}{n}日線" for d, n in
        [(item["cross_short"], config.MA_SHORT), (item["cross_long"], config.MA_LONG)]
        if d
    ]
    return "、".join(parts) if parts else "—"


def _tag_badges_html(tags) -> str:
    if not tags:
        return ""
    return "".join(f'<span class="badge">{html.escape(t)}</span>' for t in tags)


def _highlight_card_html(item, price_prefix=""):
    cls = _change_class(item["change_pct"])
    volume_ratio = f'量能 {item["volume_ratio"]}倍均量' if item["volume_ratio"] is not None else ""
    blurb = "、".join(item["reasons"])
    ma_text = _ma_status_text(item)
    ma_line = f"，{html.escape(ma_text)}" if ma_text != "—" else ""

    return f"""
      <div class="tile">
        <div class="tile-top">
          <span class="tile-name">{html.escape(item["name"])}<span class="tile-code">{html.escape(item["id"])}</span></span>
          {_tag_badges_html(item["tags"])}
        </div>
        <div class="tile-value {cls}">{_fmt_change(item["change_pct"])}</div>
        <div class="tile-sub">{price_prefix}{item["close"]}｜{html.escape(volume_ratio)}{ma_line}</div>
        <div class="tile-blurb">{html.escape(blurb)}。同時符合 {item["signal_count"]} 項條件。</div>
      </div>"""


def _highlights_section_html(tw_watchlist, us_watchlist):
    tw_hi = pick_highlights(tw_watchlist, config.HIGHLIGHT_COUNT_PER_MARKET)
    us_hi = pick_highlights(us_watchlist, config.HIGHLIGHT_COUNT_PER_MARKET)

    if not tw_hi and not us_hi:
        return '<p class="empty">今天沒有標的符合篩選條件，沒有焦點可顯示。</p>'

    tw_cards = "\n".join(_highlight_card_html(i) for i in tw_hi)
    us_cards = "\n".join(_highlight_card_html(i, price_prefix="$") for i in us_hi)

    blocks = []
    if tw_cards:
        blocks.append(f'<div class="tile-group"><h3>台股</h3><div class="tile-row">{tw_cards}</div></div>')
    if us_cards:
        blocks.append(f'<div class="tile-group"><h3>美股</h3><div class="tile-row">{us_cards}</div></div>')
    return "\n".join(blocks)


def _stock_rows_html(watchlist, price_prefix=""):
    rows = []
    for item in watchlist:
        cls = _change_class(item["change_pct"])
        volume_ratio = f'{item["volume_ratio"]}倍' if item["volume_ratio"] is not None else "—"
        ma_status = _ma_status_text(item)
        reasons = "、".join(item["reasons"])
        badges = _tag_badges_html(item["tags"])

        rows.append(f"""
        <tr>
          <td>{html.escape(item["id"])}</td>
          <td>{html.escape(item["name"])} {badges}</td>
          <td class="num">{price_prefix}{item["close"]}</td>
          <td class="num {cls}">{_fmt_change(item["change_pct"])}</td>
          <td class="num">{volume_ratio}</td>
          <td>{html.escape(ma_status)}</td>
          <td class="muted">{html.escape(reasons)}</td>
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
  .viz-root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page-plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --grid: #e1e0d9;
    --border: rgba(11,11,11,0.10);
    --up: {COLOR_UP[0]};
    --down: {COLOR_DOWN[0]};
    --neutral: {COLOR_NEUTRAL[0]};
    --badge-bg: #f0efec;
    --link: #2a78d6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page-plane: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --grid: #2c2c2a;
      --border: rgba(255,255,255,0.10);
      --up: {COLOR_UP[1]};
      --down: {COLOR_DOWN[1]};
      --neutral: {COLOR_NEUTRAL[1]};
      --badge-bg: #2c2c2a;
      --link: #3987e5;
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page-plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #898781;
    --grid: #2c2c2a;
    --border: rgba(255,255,255,0.10);
    --up: {COLOR_UP[1]};
    --down: {COLOR_DOWN[1]};
    --neutral: {COLOR_NEUTRAL[1]};
    --badge-bg: #2c2c2a;
    --link: #3987e5;
  }}

  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
          max-width: 980px; margin: 0 auto; padding: 16px 16px 48px;
          background: var(--page-plane); color: var(--text-primary); }}
  h1 {{ font-size: 1.4em; margin-bottom: 4px; }}
  h2 {{ margin-top: 2.2em; margin-bottom: 10px; font-size: 1.15em;
        border-bottom: 1px solid var(--grid); padding-bottom: 6px; }}
  h3 {{ font-size: 0.95em; color: var(--text-secondary); margin: 0 0 10px; font-weight: 600; }}
  .meta {{ color: var(--text-secondary); font-size: 0.9em; line-height: 1.7; margin-bottom: 1.5em; }}
  .disclaimer {{ color: var(--text-muted); }}

  /* 今日焦點：stat-tile 卡片 */
  .tile-group {{ margin-bottom: 20px; }}
  .tile-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
               gap: 10px; }}
  .tile {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
           padding: 14px 16px; }}
  .tile-top {{ display: flex; align-items: center; justify-content: space-between;
               flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }}
  .tile-name {{ font-weight: 600; font-size: 0.95em; }}
  .tile-code {{ color: var(--text-muted); font-weight: 400; font-size: 0.85em; margin-left: 6px; }}
  .tile-value {{ font-size: 1.7em; font-weight: 600; font-variant-numeric: proportional-nums;
                 margin: 2px 0 6px; }}
  .tile-sub {{ color: var(--text-secondary); font-size: 0.88em; margin-bottom: 6px; }}
  .tile-blurb {{ color: var(--text-secondary); font-size: 0.85em; line-height: 1.5; }}

  .badge {{ display: inline-block; background: var(--badge-bg); color: var(--text-secondary);
            font-size: 0.75em; padding: 2px 7px; border-radius: 999px; margin-left: 4px;
            border: 1px solid var(--border); }}

  .table-wrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; background: var(--surface-1); font-size: 0.92em; }}
  th, td {{ border-bottom: 1px solid var(--grid); padding: 8px 10px; text-align: left; }}
  th {{ color: var(--text-muted); font-weight: 600; font-size: 0.85em; text-align: left; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.muted {{ color: var(--text-secondary); font-size: 0.93em; }}
  .up {{ color: var(--up); font-weight: 600; }}
  .down {{ color: var(--down); font-weight: 600; }}
  .neutral {{ color: var(--neutral); font-weight: 600; }}

  .empty {{ text-align: center; color: var(--text-muted); padding: 20px; }}
  ul {{ padding-left: 1.2em; }}
  li {{ margin-bottom: 6px; }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body class="viz-root">
  <h1>{today} 每日觀察清單</h1>
  <div class="meta">
    產生時間：{now}<br>
    台股資料來源：{html.escape(tw_data_source)}｜成功取得 {tw_success}/{tw_scanned} 檔{f'（{tw_failed} 檔取得失敗）' if tw_failed else ''}，篩出 {len(tw_watchlist)} 檔<br>
    美股(S&amp;P500)：成功取得 {us_success}/{us_scanned} 檔{f'（{us_failed} 檔取得失敗）' if us_failed else ''}，篩出 {len(us_watchlist)} 檔
    <div class="disclaimer">本報告僅為資料整理，不構成任何投資建議。紅色代表上漲、綠色代表下跌（數字前的 + / − 符號永遠會標示方向，不是只靠顏色分辨）。</div>
  </div>

  <h2>今日焦點</h2>
  {_highlights_section_html(tw_watchlist, us_watchlist)}

  <h2>台股完整清單（共 {len(tw_watchlist)} 檔）</h2>
  <div class="table-wrap">
  <table>
    <tr><th>代號</th><th>名稱</th><th class="num">收盤價</th><th class="num">漲跌幅</th><th class="num">量比</th><th>均線狀態</th><th>符合原因</th></tr>
    {_stock_rows_html(tw_watchlist)}
  </table>
  </div>

  <h2>美股完整清單（S&amp;P 500，共 {len(us_watchlist)} 檔）</h2>
  <div class="table-wrap">
  <table>
    <tr><th>代號</th><th>名稱</th><th class="num">收盤價</th><th class="num">漲跌幅</th><th class="num">量比</th><th>均線狀態</th><th>符合原因</th></tr>
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
