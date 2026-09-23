"""
抓「台股有哪些股票代號」的清單。
資料來源：證交所與櫃買中心官方現行公司名冊。
只保留：4碼純數字、上市(twse)或上櫃(tpex)、排除 ETF 和存託憑證(TDR)的一般股票。
"""
import re
import requests
from datetime import date
from market_clock import now_tw




def parse_roster(rows, market, today):
    fields = ('出表日期', '公司代號', '公司簡稱', '產業別') if market == 'twse' else ('Date', 'SecuritiesCompanyCode', 'CompanyAbbreviation', 'SecuritiesIndustryCode')
    result = {}
    if not isinstance(rows, list):
        raise ValueError('Official roster must be a list')
    for row in rows:
        stamp, sid, name, sector = (str(row[key]).strip() for key in fields)
        if len(stamp) != 7 or not stamp.isdigit():
            raise ValueError('Unexpected official roster date')
        published = date(int(stamp[:3]) + 1911, int(stamp[3:5]), int(stamp[5:7]))
        if not 0 <= (today - published).days <= 7:
            raise ValueError('Official roster stale or future dated')
        if re.fullmatch(r'\d{4}', sid):
            if sid in result:
                raise ValueError('Duplicate company in official roster')
            result[sid] = dict(stock_id=sid, stock_name=name, type=market,
                               sector='產業代碼' + sector, roster_date=published.isoformat())
    return result


def get_tw_universe():
    """Current official common-share roster, not a historical ticker directory.

Suspensions and insufficient history still count in quality failures. Never
filter the universe by whether price download happened to succeed.
"""
    today = now_tw().date()
    universe = {}
    sources = [('twse', 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L', 800),
               ('tpex', 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O', 500)]
    for market, url, minimum in sources:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        roster = parse_roster(response.json(), market, today)
        if len(roster) < minimum or universe.keys() & roster.keys():
            raise RuntimeError('官方現行名冊不足或重複，停止掃描')
        universe.update(roster)
    return sorted(universe.values(), key=lambda item: item['stock_id'])


if __name__ == "__main__":
    result = get_tw_universe()
    print(f"共取得 {len(result)} 檔台股（上市+上櫃）")
    for item in result[:5]:
        print(item)
