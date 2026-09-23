"""Official bulk adapters. Dates describe the source, never the fetch date."""
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import re
import time
import urllib.request

TWSE = 'https://openapi.twse.com.tw/v1/'
TPEX = 'https://www.tpex.org.tw/openapi/v1/'
ENDPOINTS = {
    'twse': {'universe': 'opendata/t187ap03_L', 'revenue': 'opendata/t187ap05_L',
             'financial': 'opendata/t187ap06_L_ci', 'valuation': 'exchangeReport/BWIBBU_ALL',
             'price': 'exchangeReport/STOCK_DAY_ALL', 'events': 'opendata/t187ap04_L'},
    'tpex': {'universe': 'mopsfin_t187ap03_O', 'revenue': 'mopsfin_t187ap05_O',
             'financial': 'mopsfin_t187ap06_O_ci', 'valuation': 'tpex_mainboard_peratio_analysis',
             'price': 'tpex_mainboard_daily_close_quotes', 'events': 'mopsfin_t187ap04_O'},
}


def number(value):
    try:
        value = float(str(value).replace(',', '').replace('%', '').strip())
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def date(value):
    digits = re.sub(r'\D', '', str(value or ''))
    try:
        if len(digits) == 7:
            digits = str(int(digits[:3]) + 1911) + digits[3:]
        if len(digits) != 8:
            return None
        return dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
    except ValueError:
        return None


def month(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 5:
        digits = str(int(digits[:3]) + 1911) + digits[3:]
    return date(digits + '01')[:7] if date(digits + '01') else None


def fetch_json(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'StockWatchlistRadar/1.0', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=25) as response:
                rows = json.load(response)
            if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
                raise ValueError('unexpected schema or empty feed')
            return rows
        except Exception:
            if attempt == 2:
                raise RuntimeError('feed unavailable or schema changed') from None
            time.sleep(1 + attempt)


def normalize(kind, raw, market, url, now):
    r = {k.strip(): v for k, v in raw.items()}
    code = str(r.get('公司代號') or r.get('SecuritiesCompanyCode') or r.get('Code') or '').strip()
    if not re.fullmatch(r'[1-9]\d{3}', code):
        return None
    base = {'code': code, 'market': market, 'source': url, 'fetched_at': now,
            'source_date': date(r.get('出表日期') or r.get('Date'))}
    if kind == 'universe':
        return dict(base, name=r.get('公司簡稱') or r.get('CompanyAbbreviation') or code,
                    sector=str(r.get('產業別') or r.get('SecuritiesIndustryCode') or ''),
                    listed=date(r.get('上市日期') or r.get('DateOfListing')))
    if kind == 'revenue':
        return dict(base, period=month(r.get('資料年月')),
                    yoy=number(r.get('營業收入-去年同月增減(%)')),
                    mom=number(r.get('營業收入-上月比較增減(%)')),
                    ytd_yoy=number(r.get('累計營業收入-前期比較增減(%)')),
                    revenue=number(r.get('營業收入-當月營收')))
    if kind == 'financial':
        year, quarter = number(r.get('年度') or r.get('Year')), number(r.get('季別') or r.get('Season'))
        if year is None or quarter not in (1, 2, 3, 4):
            return None
        year = int(year) + (1911 if year < 1911 else 0)
        q = int(quarter)
        end = dt.date(year + (q == 4), (q % 4) * 3 + 1, 1) - dt.timedelta(days=1)
        return dict(base, period=f'{year}Q{q}', period_end=end.isoformat(), basis='YTD',
                    eps=number(r.get('基本每股盈餘（元）')), revenue=number(r.get('營業收入')),
                    operating=number(r.get('營業利益（損失）')), gross=number(r.get('營業毛利（毛損）')),
                    pretax=number(r.get('稅前淨利（淨損）')), nonoperating=number(r.get('營業外收入及支出')),
                    net=number(r.get('本期淨利（淨損）')))
    if kind == 'valuation':
        return dict(base, pe=number(r.get('PEratio') or r.get('PriceEarningRatio')),
                    pb=number(r.get('PBratio') or r.get('PriceBookRatio')),
                    yield_pct=number(r.get('DividendYield') or r.get('YieldRatio')))
    if kind == 'price':
        return dict(base, date=base['source_date'], close=number(r.get('ClosingPrice') or r.get('Close')),
                    volume=number(r.get('TradeVolume') or r.get('TradingShares')))
    title = str(r.get('主旨') or '').replace('\r', ' ').replace('\n', ' ').strip()
    published = date(r.get('發言日期'))
    event_id = hashlib.sha256((code + str(published) + str(r.get('發言時間')) + title).encode()).hexdigest()
    return dict(base, id=event_id, title=title, published=published,
                occurred=date(r.get('事實發生日')), text=str(r.get('說明') or '')[:14000],
                review_required=any(k in title for k in ('重編', '停止交易', '終止上市', '終止上櫃', '違約',
                    '重大損失', '資金貸與', '背書保證', '訴訟', '會計師', '資安', '火災')))


def collect(now, events_only=False):
    jobs = [(m, k, (TWSE if m == 'twse' else TPEX) + path)
            for m, kinds in ENDPOINTS.items() for k, path in kinds.items()
            if not events_only or k == 'events']
    feeds, health = {}, []
    def one(job):
        market, kind, url = job
        try:
            raw = fetch_json(url)
            rows = [v for r in raw if (v := normalize(kind, r, market, url, now))]
            # Empty event days are legitimate; empty critical feeds are not.
            if not rows and kind != 'events':
                raise ValueError('no normalized rows')
            return (market, kind), rows, {'market': market, 'kind': kind, 'ok': True, 'count': len(rows), 'url': url}
        except Exception:
            return (market, kind), [], {'market': market, 'kind': kind, 'ok': False, 'count': 0, 'url': url}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for key, rows, status in pool.map(one, jobs):
            feeds[key] = rows
            health.append(status)
    return feeds, health
