"""Telegram delivery; acknowledge only successful API responses, no secret logging."""
import json
import time
import urllib.error
import urllib.request
import uuid


class DeliveryRejected(RuntimeError):
    pass


class DeliveryUncertain(RuntimeError):
    pass


def chunks(text, limit=3500):
    # Count UTF-16 code units conservatively for Telegram's 4096 character limit.
    result, current, units = [], '', 0
    for c in text:
        n = len(c.encode('utf-16-le')) // 2
        if units + n > limit:
            result.append(current)
            current, units = '', 0
        current += c
        units += n
    if current:
        result.append(current)
    return result


def call(token, method, data, content_type='application/json'):
    url = f'https://api.telegram.org/bot{token}/{method}'
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, headers={'Content-Type': content_type}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
            if result.get('ok'):
                return
            raise DeliveryRejected('Telegram rejected request')
        except urllib.error.HTTPError as e:
            # 429 is explicitly not accepted; safe bounded retry. Network timeouts are ambiguous.
            if e.code == 429 and attempt < 2:
                try:
                    delay = json.load(e).get('parameters', {}).get('retry_after', 5)
                except Exception:
                    delay = 5
                time.sleep(min(max(float(delay), 1), 60))
                continue
            if e.code >= 500:
                raise DeliveryUncertain('Telegram server response uncertain') from None
            raise DeliveryRejected(f'Telegram HTTP {e.code}; delivery rejected') from None
        except DeliveryRejected:
            raise
        except Exception:
            raise DeliveryUncertain('Telegram delivery unconfirmed; no token or message body logged') from None


def send_once(store, key, now, token, method, body, content_type='application/json'):
    if store.sent(key):
        return 0
    if store.meta('delivery:' + key) in ('sending', 'uncertain'):
        raise DeliveryUncertain('Prior delivery needs manual reconciliation; no automatic resend')
    store.meta('delivery:' + key, 'sending')
    try:
        call(token, method, body, content_type)
    except DeliveryRejected:
        store.meta('delivery:' + key, 'failed')
        raise
    except Exception:
        store.meta('delivery:' + key, 'uncertain')
        raise
    store.mark_sent(key, now)
    store.meta('delivery:' + key, 'sent')
    return 1


def deliver(store, token, chat, key, text, now, document=False):
    if document:
        if store.sent(key):
            return 0
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="radar-report.txt"\r\n'
                f'Content-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n--{boundary}--\r\n').encode()
        return send_once(store, key, now, token, 'sendDocument', body, 'multipart/form-data; boundary=' + boundary)
    count = 0
    for i, part in enumerate(chunks(text)):
        chunk_key = f'{key}:{i}'
        if store.sent(chunk_key):
            continue
        payload = json.dumps({'chat_id': chat, 'text': part, 'link_preview_options': {'is_disabled': True}}).encode()
        count += send_once(store, chunk_key, now, token, 'sendMessage', payload)
    return count
