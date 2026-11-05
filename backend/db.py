"""SQLite storage: cache, watchlist, transactions, journal, alerts, settings."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.getenv("STOCKPILOT_DB", Path(__file__).resolve().parent.parent / "data" / "stockpilot.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist (ticker TEXT PRIMARY KEY, tags TEXT DEFAULT '', note TEXT DEFAULT '', added REAL);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'buy', date TEXT DEFAULT '',
    shares REAL, price REAL, amount REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ticker TEXT, action TEXT, price REAL,
    thesis TEXT DEFAULT '', invalidation TEXT DEFAULT '', stop_price REAL, target_price REAL,
    review_date TEXT DEFAULT '', outcome TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, kind TEXT, value REAL,
    active INTEGER DEFAULT 1, created REAL, triggered REAL, message TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

ADDED_COLUMNS = {
    # The score and benchmark when a decision was recorded, so the scorecard can judge it later
    "journal": {"score": "REAL", "stance": "TEXT", "factors": "TEXT", "benchmark": "TEXT"},
    # A fired alert stays in the digest until it's dismissed
    "alerts": {"dismissed": "INTEGER DEFAULT 0"},
}

DEFAULT_SETTINGS = {
    "base_currency": "USD",
    "risk_free_rate": 0.04,
    "weights": {"value": 0.2, "quality": 0.2, "growth": 0.2, "momentum": 0.2, "risk": 0.2},
    "targets": {"Stocks": 65, "ETFs": 30, "Crypto": 5},
    "benchmark": "SPY",
}


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock:
        c = _connect()
        try:
            c.executescript(SCHEMA)
            # Columns added after the first release: databases created earlier gain them here
            # Before the ledger there was only a table of current lots: each one becomes a buy
            if c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'holdings'").fetchone():
                c.execute("INSERT INTO transactions (id, ticker, kind, date, shares, price, note) "
                          "SELECT id, ticker, 'buy', COALESCE(bought, ''), shares, cost, COALESCE(note, '') FROM holdings")
                c.execute("DROP TABLE holdings")
            for table, added in ADDED_COLUMNS.items():
                have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                for col, kind in added.items():
                    if col not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            c.commit()
        finally:
            c.close()


def q(sql, params=()):
    with _lock:
        c = _connect()
        try:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
        finally:
            c.close()


def x(sql, params=()):
    with _lock:
        c = _connect()
        try:
            cur = c.execute(sql, params)
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


# ---- cache ----
def cache_get(key, ttl):
    rows = q("SELECT value, ts FROM cache WHERE key=?", (key,))
    if rows and time.time() - rows[0]["ts"] < ttl:
        return json.loads(rows[0]["value"])
    return None


def cache_get_stale(key):
    rows = q("SELECT value FROM cache WHERE key=?", (key,))
    return json.loads(rows[0]["value"]) if rows else None


def cache_set(key, value):
    x("INSERT OR REPLACE INTO cache (key, value, ts) VALUES (?,?,?)", (key, json.dumps(value), time.time()))


def cache_clear():
    x("DELETE FROM cache")


# ---- backup ----
USER_TABLES = ("watchlist", "transactions", "journal", "alerts")
BACKUP_VERSION = 2


def export_user_data():
    """Everything the user entered. The market data cache is left out because it can be downloaded again."""
    out = {"version": BACKUP_VERSION, "exported": time.time(), **{t: q(f"SELECT * FROM {t}") for t in USER_TABLES}}
    out["settings"] = {r["key"]: json.loads(r["value"]) for r in q("SELECT key, value FROM settings")}
    return out


def read_backup(backup):
    """Check a backup's shape, bringing an older version up to date. Raises ValueError if it can't be used."""
    if not isinstance(backup, dict) or backup.get("version") not in (1, BACKUP_VERSION):
        raise ValueError("This isn't a StockPilot backup, or it's from a different version.")
    if backup["version"] == 1:  # before the ledger, when there was a table of current lots
        lots = backup.get("holdings")
        if not isinstance(lots, list) or not all(isinstance(r, dict) for r in lots):
            raise ValueError("The backup's holdings section isn't usable.")
        backup = {**backup, "transactions": [{"id": r.get("id"), "ticker": r.get("ticker"), "kind": "buy", "date": r.get("bought") or "",
                                              "shares": r.get("shares"), "price": r.get("cost"), "note": r.get("note") or ""} for r in lots]}
    for t in USER_TABLES:
        if not isinstance(backup.get(t), list) or not all(isinstance(r, dict) for r in backup[t]):
            raise ValueError(f"The backup's {t} section isn't usable.")
    if not isinstance(backup.get("settings"), dict):
        raise ValueError("The backup's settings section isn't usable.")
    return backup


def restore_user_data(backup):
    """Replace all user data in one transaction, so a bad file changes nothing."""
    backup = read_backup(backup)
    with _lock:
        c = _connect()
        try:
            for t in USER_TABLES:
                known = [r["name"] for r in c.execute(f"PRAGMA table_info({t})")]
                c.execute(f"DELETE FROM {t}")
                for row in backup[t]:
                    cols = [k for k in row if k in known]  # only real columns, so a file can't inject SQL
                    if not cols:
                        raise ValueError(f"A row in the backup's {t} section has no usable fields.")
                    marks = ",".join("?" * len(cols))
                    c.execute(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({marks})", [row[k] for k in cols])
            c.execute("DELETE FROM settings")
            for k, v in backup["settings"].items():
                if k in DEFAULT_SETTINGS:
                    c.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, json.dumps(v)))
            c.commit()
        except sqlite3.Error as e:
            c.rollback()
            raise ValueError(f"The backup couldn't be restored: {e}")
        except ValueError:
            c.rollback()
            raise
        finally:
            c.close()


# ---- settings ----
def get_settings():
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for r in q("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out


def set_settings(values: dict):
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            x("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, json.dumps(v)))
    return get_settings()
