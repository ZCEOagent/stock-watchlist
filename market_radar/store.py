"""SQLite holds public market data only; never portfolio details or message bodies."""
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS facts(kind TEXT, code TEXT, period TEXT, body TEXT,
             PRIMARY KEY(kind,code,period));
          CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, published TEXT, body TEXT);
          CREATE TABLE IF NOT EXISTS snapshots(day TEXT PRIMARY KEY, body TEXT);
          CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, sent_at TEXT);
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        ''')

    def ingest(self, feeds):
        for (_, kind), rows in feeds.items():
            for r in rows:
                if kind == 'events':
                    self.db.execute('INSERT OR REPLACE INTO events VALUES(?,?,?)', (r['id'], r['published'], json.dumps(r, ensure_ascii=False)))
                else:
                    period = r.get('period') or r.get('date') or r.get('source_date')
                    if not period:
                        continue
                    self.db.execute('INSERT OR REPLACE INTO facts VALUES(?,?,?,?)', (kind, r['code'], period, json.dumps(r, ensure_ascii=False)))
        self.db.commit()

    def history(self, kind, code):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM facts WHERE kind=? AND code=? ORDER BY period', (kind, code))]

    def previous(self, day):
        row = self.db.execute('SELECT body FROM snapshots WHERE day < ? ORDER BY day DESC LIMIT 1', (day,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self):
        row = self.db.execute('SELECT body FROM snapshots ORDER BY day DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self, day, data):
        self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?)', (day, json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    def events_since(self, since):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM events WHERE published >= ? ORDER BY published DESC,id', (since,))]

    def sent(self, key):
        return self.db.execute('SELECT 1 FROM receipts WHERE id=?', (key,)).fetchone() is not None

    def mark_sent(self, key, now):
        self.db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?)', (key, now))
        self.db.commit()

    def meta(self, key, value=None):
        if value is not None:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))
            self.db.commit()
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def prune(self, today):
        # Financial same-quarter comparators need >1 year of history.
        for sql in (
            "DELETE FROM facts WHERE kind IN ('price','valuation','universe') AND period < date(?,'-180 days')",
            "DELETE FROM facts WHERE kind='revenue' AND period < strftime('%Y-%m',date(?,'-3 years'))",
            "DELETE FROM events WHERE published < date(?,'-90 days')",
            "DELETE FROM snapshots WHERE day < date(?,'-90 days')",
            "DELETE FROM receipts WHERE sent_at < date(?,'-90 days')",
        ):
            self.db.execute(sql, (today,))
        self.db.commit()

    def close(self):
        self.db.close()
