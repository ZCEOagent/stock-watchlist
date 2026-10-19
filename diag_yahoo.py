"""Read-only diagnostic: does Yahoo serve data to GitHub-hosted runners?"""
import datetime as dt, logging, time
import requests, yfinance as yf

logging.basicConfig(level=logging.WARNING)
UA = {'User-Agent': 'Mozilla/5.0'}
print('utc', dt.datetime.utcnow().isoformat(), 'yfinance', yf.__version__)
try:
    print('egress ip', requests.get('https://api.ipify.org', timeout=10).text)
except Exception as exc:
    print('ip lookup failed', type(exc).__name__)
for sym in ('AAPL', '2330.TW', '6274.TWO'):
    for host in ('query1', 'query2'):
        try:
            r = requests.get(f'https://{host}.finance.yahoo.com/v8/finance/chart/{sym}', params={'range': '5d', 'interval': '1d'}, headers=UA, timeout=20)
            body = r.text[:160].replace('\n', ' ')
            print('raw', host, sym, r.status_code, body)
        except Exception as exc:
            print('raw', host, sym, 'EXC', type(exc).__name__, str(exc)[:120])
for batch in (['AAPL', 'MSFT', 'NVDA', 'BRK-B', 'GOOGL'], ['2330.TW', '2317.TW', '6274.TWO']):
    t = time.time()
    try:
        d = yf.download(batch, period='1mo', auto_adjust=False, group_by='ticker', threads=True, progress=False)
        for s in batch:
            try:
                f = d[s].dropna(how='all')
                print('yf', s, len(f), f.index[-1].date() if len(f) else None)
            except Exception as exc:
                print('yf', s, 'missing', type(exc).__name__)
    except Exception as exc:
        print('yf batch EXC', type(exc).__name__, str(exc)[:200])
    print('elapsed', round(time.time() - t, 1))
    try:
        h = yf.Ticker(batch[0]).history(period='5d')
        print('ticker.history', batch[0], len(h))
    except Exception as exc:
        print('ticker.history EXC', type(exc).__name__, str(exc)[:200])
