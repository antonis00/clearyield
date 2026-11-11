"""SQLite storage: cache, watchlist, transactions, journal, alerts, settings."""
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from .universe import DEFAULT_TILES, REGIONS

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "clearyield.db"
DB_PATH = Path(os.getenv("CLEARYIELD_DB", _DEFAULT_DB))
# The database from when the app was called StockPilot moves to the new name once, with everything in it
_OLD_DB = _DEFAULT_DB.with_name("stockpilot.db")
if DB_PATH == _DEFAULT_DB and not DB_PATH.exists() and _OLD_DB.exists():
    for _suffix in ("", "-wal", "-shm"):
        if Path(f"{_OLD_DB}{_suffix}").exists():
            os.replace(f"{_OLD_DB}{_suffix}", f"{DB_PATH}{_suffix}")
_lock = threading.Lock()
log = logging.getLogger("clearyield.db")

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
CREATE TABLE IF NOT EXISTS buckets (ticker TEXT PRIMARY KEY, bucket TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS score_history (
    date TEXT NOT NULL, ticker TEXT NOT NULL, composite REAL, stance TEXT, factors TEXT, PRIMARY KEY (date, ticker));
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
    "dashboard_tiles": DEFAULT_TILES,
    "custom_tiles": [],  # [{"ticker", "name"}], each checked for price history when added
    "core_target": 80,
    "regions": list(REGIONS),  # the regions you follow: only their stocks are downloaded, screened and ranked against
    "alert_email": "",  # where triggered alerts are emailed; the mail server itself is in .env (mailer.py)
    "email_alerts": False,
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
                          "SELECT id, ticker, 'buy', CASE WHEN bought GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN bought ELSE '' END, "
                          "shares, cost, COALESCE(note, '') FROM holdings")
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


def x_many(sql, rows):
    """Insert several rows in one transaction, so a failure adds none of them."""
    with _lock:
        c = _connect()
        try:
            c.executemany(sql, rows)
            c.commit()
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
# A copy in memory of each cache row this process has read or written, as (stored JSON, timestamp). One portfolio
# view reads the same price histories and rates many times, and each read was a new SQLite connection: this skips
# that while keeping the same ages. Every read decodes its own copy, so no caller can change another's data. Only
# this process writes the cache; anything that changes it must go through these functions.
_mem = {}


def _mem_key(key):
    return (str(DB_PATH), key)


def _cache_row(key):
    k = _mem_key(key)
    if k not in _mem:
        rows = q("SELECT value, ts FROM cache WHERE key=?", (key,))
        if not rows:
            return None
        _mem[k] = (rows[0]["value"], rows[0]["ts"])
    return _mem[k]


def cache_get(key, ttl):
    row = _cache_row(key)
    if row and time.time() - row[1] < ttl:
        return json.loads(row[0])
    return None


def cache_get_stale(key):
    row = _cache_row(key)
    return json.loads(row[0]) if row else None


def cache_set(key, value, ts=None):
    text, ts = json.dumps(value), ts or time.time()
    x("INSERT OR REPLACE INTO cache (key, value, ts) VALUES (?,?,?)", (key, text, ts))
    _mem[_mem_key(key)] = (text, ts)


def cache_expire(key=None):
    """Make everything (or one key) refresh on its next use, keeping old values to fall back on if Yahoo fails.
    The time written becomes negative: always too old to use, but still known, so pruning can tell its age."""
    if key is None:
        x("UPDATE cache SET ts = -abs(ts)")
        _mem.clear()
    else:
        x("UPDATE cache SET ts = -abs(ts) WHERE key = ?", (key,))
        _mem.pop(_mem_key(key), None)


CACHE_KEEP_DAYS = 90


def cache_prune(days=CACHE_KEEP_DAYS):
    """Drop what hasn't been refreshed for `days`: a symbol no longer looked at, or a key from an older version.
    Anything in use is refreshed within a week, so only dead entries go. Returns how many rows were removed."""
    cutoff = time.time() - days * 86400
    with _lock:
        c = _connect()
        try:
            gone = c.execute("DELETE FROM cache WHERE abs(ts) < ?", (cutoff,)).rowcount
            c.commit()
            if gone:
                c.execute("VACUUM")  # give the space back to the disk
        finally:
            c.close()
    if gone:
        _mem.clear()
    return gone


def cache_clear():
    x("DELETE FROM cache")
    _mem.clear()


# ---- backup ----
# Score history is here because Yahoo has no past fundamentals, so it could never be rebuilt
USER_TABLES = ("watchlist", "transactions", "journal", "alerts", "score_history", "buckets")
BACKUP_VERSION = 4


def export_user_data():
    """Everything the user entered. The market data cache is left out because it can be downloaded again."""
    out = {"version": BACKUP_VERSION, "exported": time.time(), **{t: q(f"SELECT * FROM {t}") for t in USER_TABLES}}
    out["settings"] = {r["key"]: json.loads(r["value"]) for r in q("SELECT key, value FROM settings")}
    return out


def read_backup(backup):
    """Check a backup's shape, bringing an older version up to date. Raises ValueError if it can't be used."""
    if not isinstance(backup, dict) or backup.get("version") not in (1, 2, 3, BACKUP_VERSION):
        raise ValueError("This isn't a ClearYield backup, or it's from a different version.")
    if backup["version"] == 1:  # before the ledger, when there was a table of current lots
        lots = backup.get("holdings")
        if not isinstance(lots, list) or not all(isinstance(r, dict) for r in lots):
            raise ValueError("The backup's holdings section isn't usable.")
        backup = {**backup, "transactions": [{"id": r.get("id"), "ticker": r.get("ticker"), "kind": "buy", "date": r.get("bought") or "",
                                              "shares": r.get("shares"), "price": r.get("cost"), "note": r.get("note") or ""} for r in lots]}
    if backup["version"] < 3:
        backup = {**backup, "score_history": []}
    if backup["version"] < 4:
        backup = {**backup, "buckets": []}
    for t in USER_TABLES:
        if not isinstance(backup.get(t), list) or not all(isinstance(r, dict) for r in backup[t]):
            raise ValueError(f"The backup's {t} section isn't usable.")
    if not isinstance(backup.get("settings"), dict):
        raise ValueError("The backup's settings section isn't usable.")
    try:
        settings = check_settings(backup["settings"])
    except ValueError as e:
        raise ValueError(f"The backup's settings aren't usable: {e}")
    if any(r.get("bucket") not in ("core", "satellite") for r in backup["buckets"]):
        raise ValueError("Every bucket in the backup must be core or satellite.")
    return {**backup, "settings": settings}


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
            for k, v in backup["settings"].items():  # already checked by read_backup
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
# Every setting is checked before it's stored, whether it comes from the page or a backup, so a bad value can neither
# break the pages that use it nor carry markup into them
FACTOR_KEYS = ("value", "quality", "growth", "momentum", "risk")
TARGET_KEYS = ("Stocks", "ETFs", "Crypto", "Other")
_SYMBOL = re.compile(r"[A-Z0-9^=.\-]{1,24}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_EMAIL = re.compile(r"[^@\s<>\"',;:()\[\]]+@[^@\s<>\"',;:()\[\]]+\.[A-Za-z]{2,}")  # as mailer.EMAIL


def _num(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{what} must be a number from {lo:g} to {hi:g}.")
    return float(v)


def _shares(v, keys, hi, what):
    if not isinstance(v, dict) or not v or any(k not in keys for k in v):
        raise ValueError(f"{what} must be given for {', '.join(keys)} only.")
    out = {k: _num(n, 0, hi, f"Each of the {what.lower()}") for k, n in v.items()}
    if not sum(out.values()) > 0:
        raise ValueError(f"At least one of the {what.lower()} must be above zero.")
    return out


def _symbols(v, what):
    if not isinstance(v, list) or not all(isinstance(t, str) and _SYMBOL.fullmatch(t) for t in v):
        raise ValueError(f"{what} must be a list of symbols.")
    return list(dict.fromkeys(v))


def _custom_tiles(v):
    if not isinstance(v, list) or not all(isinstance(c, dict) and isinstance(c.get("ticker"), str) and _SYMBOL.fullmatch(c["ticker"])
                                          and isinstance(c.get("name"), str) and len(c["name"]) <= 100 for c in v):
        raise ValueError("Custom tiles must each have a symbol and a name.")
    return [{"ticker": c["ticker"], "name": c["name"]} for c in v]


def _text(pattern, what):
    def check(v):
        if not isinstance(v, str) or not pattern.fullmatch(v):
            raise ValueError(what)
        return v
    return check


def _regions(v):
    """At least one of the five regions, in the usual order."""
    if not isinstance(v, list) or not v or any(r not in REGIONS for r in v):
        raise ValueError(f"Follow at least one region, from: {', '.join(REGIONS)}.")
    return [r for r in REGIONS if r in v]


def _email(v):
    """An address, or empty for none."""
    if not isinstance(v, str) or len(v) > 254 or (v.strip() and not _EMAIL.fullmatch(v.strip())):
        raise ValueError("The alert email must be an address such as name@example.com.")
    return v.strip()


def _switch(what):
    def check(v):
        if not isinstance(v, bool):
            raise ValueError(f"{what} must be on or off.")
        return v
    return check


SETTING_CHECKS = {
    "base_currency": _text(_CURRENCY, "The base currency must be a three-letter code such as USD."),
    "risk_free_rate": lambda v: _num(v, -0.05, 0.3, "The risk-free rate"),
    "weights": lambda v: _shares(v, FACTOR_KEYS, 1, "Factor weights"),
    "targets": lambda v: _shares(v, TARGET_KEYS, 100, "Allocation targets"),
    "benchmark": _text(_SYMBOL, "The benchmark must be a symbol such as SPY."),
    "dashboard_tiles": lambda v: _symbols(v, "Dashboard tiles"),
    "custom_tiles": _custom_tiles,
    "core_target": lambda v: _num(v, 0, 100, "The core share"),
    "regions": _regions,
    "alert_email": _email,
    "email_alerts": _switch("Email alerts"),
}


def check_settings(values):
    """The known settings in `values`, checked and tidied; unknown keys are dropped. Raises ValueError on a bad value."""
    if not isinstance(values, dict):
        raise ValueError("Settings must be an object.")
    return {k: SETTING_CHECKS[k](v) for k, v in values.items() if k in SETTING_CHECKS}


def get_settings():
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for r in q("SELECT key, value FROM settings"):
        try:  # a value stored before these checks existed falls back to the default instead of breaking a page
            out.update(check_settings({r["key"]: json.loads(r["value"])}))
        except ValueError as e:
            log.warning("ignoring stored setting %s: %s", r["key"], e)
    return out


def set_settings(values: dict):
    for k, v in check_settings(values).items():
        x("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, json.dumps(v)))
    return get_settings()
