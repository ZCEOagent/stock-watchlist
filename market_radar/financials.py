"""Same-filing YTD evidence, conservatively dated to first observation."""
import datetime as dt
import hashlib
import time
from decimal import Decimal
from urllib.parse import urlencode

import requests
from lxml import etree

URL = 'https://mopsov.twse.com.tw/server-java/t164sb01'
NS = {'x': 'http://www.xbrl.org/2003/instance', 'ix': 'http://www.xbrl.org/2013/inlineXBRL'}


def parse_filing(content, code, period, observed, report='C'):
    year, quarter = int(period[:4]), int(period[-1])
    end = f'{year}-{(3, 6, 9, 12)[quarter-1]:02d}-{(31, 30, 30, 31)[quarter-1]}'
    text = content.decode('cp950') if b'charset=big5' in content.lower() else content.decode('utf-8')
    text = text[text.index('<html xmlns='):]
    text = text[:text.index('</html>')+7]
    root = etree.fromstring(text.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    def concept(node):
        prefix, local = node.get('name', ':').split(':', 1)
        uri = node.nsmap.get(prefix, '')
        return local if ('/ifrs-full' in uri or uri.startswith('http://www.xbrl.org/tifrs/notes/')) else ''
    notes = {}
    for node in root.findall('.//ix:nonNumeric', NS):
        notes.setdefault(concept(node), set()).add(''.join(node.itertext()).strip())
    category = {'C': 'Consolidated report', 'A': 'Individual report'}[report]
    for key, expected in [('CompanyID', code), ('Year', str(year)), ('Quarter', str(quarter)),
                          ('ReportCategory', category)]:
        if notes.get(key) != {expected}:
            raise ValueError('Filing identity/period/scope mismatch')
    contexts = {}
    for node in root.findall('.//x:context', NS):
        if node.find('.//x:scenario', NS) is not None or node.find('.//x:segment', NS) is not None:
            continue
        if node.findtext('x:entity/x:identifier', namespaces=NS) != code:
            continue
        contexts[node.get('id')] = (node.findtext('x:period/x:startDate', namespaces=NS),
                                    node.findtext('x:period/x:endDate', namespaces=NS))
    units = {u.get('id'): u for u in root.findall('.//x:unit', NS)}
    def measure(node):
        if node is None or not node.text or ':' not in node.text:
            return None
        prefix, local = node.text.strip().split(':', 1)
        return node.nsmap.get(prefix), local
    def value(name, start, finish, eps=False):
        found = set()
        for node in root.findall('.//ix:nonFraction', NS):
            if concept(node) != name or contexts.get(node.get('contextRef')) != (start, finish):
                continue
            unit = units.get(node.get('unitRef'))
            if unit is None:
                raise ValueError('Missing unit')
            if eps:
                numerator = measure(unit.find('x:divide/x:unitNumerator/x:measure', NS))
                denominator = measure(unit.find('x:divide/x:unitDenominator/x:measure', NS))
                valid = numerator == ('http://www.xbrl.org/2003/iso4217', 'TWD') and denominator == (NS['x'], 'shares')
            else:
                valid = measure(unit.find('x:measure', NS)) == ('http://www.xbrl.org/2003/iso4217', 'TWD')
            fmt = node.get('format', '').split(':')[-1]
            if not valid or fmt not in ('', 'numdotdecimal') or node.get('{http://www.w3.org/2001/XMLSchema-instance}nil') in ('true', '1'):
                raise ValueError('Unsupported numeric fact')
            scale = int(node.get('scale', '0'))
            if abs(scale) > 12 or node.get('sign', '') not in ('', '-'):
                raise ValueError('Invalid scale/sign')
            n = Decimal(''.join(node.itertext()).strip().replace(',', '')) * (Decimal(10) ** scale)
            if not n.is_finite():
                raise ValueError('Nonfinite fact')
            found.add(float(-n if node.get('sign') == '-' else n))
        if len(found) != 1:
            raise ValueError('Missing or conflicting comparable facts')
        return found.pop()
    start, prior_end = f'{year}-01-01', str(year-1) + end[4:]
    return {'code': code, 'period': period, 'basis': 'YTD', 'available_at': observed[:10],
            'observed_at': observed, 'fetched_at': observed, 'availability_basis': 'first_observed',
            'report_scope': category,
            'source': URL + '?' + urlencode(dict(step=1, CO_ID=code, SYEAR=year, SSEASON=quarter, REPORT_ID=report)),
            'content_sha256': hashlib.sha256(content).hexdigest(),
            'current_eps': value('BasicEarningsLossPerShare', start, end, True),
            'prior_year_eps': value('BasicEarningsLossPerShare', f'{year-1}-01-01', prior_end, True),
            'operating_cash_flow': value('CashFlowsFromUsedInOperatingActivities', start, end)/1000,
            'net': value('ProfitLoss', start, end)/1000}


def matches(row, financial):
    return (row.get('period') == financial.get('period') and
            all(financial.get(k) is not None and abs(row.get(r, float('inf'))-financial[k]) <= tolerance
                for r, k, tolerance in [('current_eps', 'eps', .011), ('net', 'net', 2)]))


def complete(store, companies, stamp, limit=300):
    """Bounded, resumable single-worker crawl; cache revisions for seven days."""
    today = stamp[:10]
    pending, result = [], {}
    eligible = 0
    for company in companies:
        code = company['code']
        facts = store.history('financial', code)
        if not facts or facts[-1].get('eps') is None or facts[-1].get('net') is None:
            continue
        fin = facts[-1]
        eligible += 1
        cached = next((r for r in store.history('supplement', code) if r['period'] == fin['period']), {})
        age = (dt.date.fromisoformat(today)-dt.date.fromisoformat(cached.get('fetched_at', '1900-01-01')[:10])).days
        if cached and matches(cached, fin) and 0 <= age < 8:
            result[code] = cached
            continue
        key = f"filing-attempt:v2:{code}:{fin['period']}"
        if store.meta(key) != today:
            pending.append((company, fin, key))
    pending.sort(key=lambda x: (x[0]['code'] not in ('2330', '6274'), store.meta(x[2]) or '', x[0]['code']))
    attempted, failed, consecutive_errors = 0, 0, 0
    started = time.monotonic()
    # Leave time for report delivery and artifact checkpoint before the job timeout.
    budget = 5400 if limit > 300 else 480
    for company, fin, key in pending[:limit]:
        if time.monotonic() - started >= budget:
            break
        code = company['code']
        attempted += 1
        store.meta(key, today)
        try:
            response = requests.get(URL, params=dict(step=1, CO_ID=code, SYEAR=fin['period'][:4],
                                    SSEASON=fin['period'][-1], REPORT_ID='C'), timeout=25)
            if response.status_code in (429, 503):
                failed += 1
                break
            response.raise_for_status()
            report = 'C'
            # Only an explicit "file does not exist" permits individual-report fallback.
            # A timeout, limit, parser error or mismatch must never change scope silently.
            if '檔案不存在'.encode('cp950') in response.content:
                time.sleep(1)
                report = 'A'
                response = requests.get(URL, params=dict(step=1, CO_ID=code, SYEAR=fin['period'][:4],
                                        SSEASON=fin['period'][-1], REPORT_ID=report), timeout=25)
                if response.status_code in (429, 503):
                    failed += 1
                    break
                response.raise_for_status()
            row = parse_filing(response.content, code, fin['period'], stamp, report)
            if not matches(row, fin):
                raise ValueError('Bulk and filing disagree')
            old = next((r for r in store.history('supplement', code) if r['period'] == fin['period']), {})
            if old.get('content_sha256') == row['content_sha256']:
                row['observed_at'] = old['observed_at']
                row['available_at'] = old['available_at']
            store.ingest({(company['market'], 'supplement'): [row]})
            result[code] = row
            consecutive_errors = 0
        except (requests.RequestException, ValueError, ArithmeticError, etree.Error, IndexError):
            failed += 1
            consecutive_errors += 1
            store.meta(key + ':error', 'unavailable_or_mismatch')
        finally:
            time.sleep(1)
        if attempted % 25 == 0:
            print(f'Financial completion: attempted={attempted}; verified={len(result)}; failed={failed}', flush=True)
        if consecutive_errors >= 5:
            break
    return result, {'eligible': eligible, 'verified': len(result), 'attempted': attempted,
                    'failed': failed, 'pending': eligible-len(result)}
