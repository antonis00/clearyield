"""Cash at the broker: the deposits and withdrawals you record, less what you bought, plus what you sold and the
dividends paid, from your first cash entry on. Kept apart from the ledger (its own table), so holdings, returns and tax
never see it. One balance in the base currency, each movement at its own date's rate (today's when Yahoo has none):
brokers hold several currencies and convert as they go, so this is the cash to invest, approximately.

Nothing here before the first deposit is recorded: without one, the app can't know what cash there is, and the
Invest card falls back to your plan's monthly amount."""
import datetime as dt
import re

from . import data, db

KINDS = ("deposit", "withdrawal")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class CashError(ValueError):
    """A cash entry that can't be right. The message is for the user."""


def _clean(row):
    if not isinstance(row, dict):
        raise CashError("A cash entry isn't readable.")
    kind, date = row.get("kind"), str(row.get("date") or "").strip()
    if kind not in KINDS:
        raise CashError("A cash entry is a deposit or a withdrawal.")
    if not _DATE.fullmatch(date):
        raise CashError("A cash entry needs its date, as YYYY-MM-DD.")
    if date > dt.date.today().isoformat():
        raise CashError("A cash entry can't be dated in the future.")
    try:
        amount = float(row.get("amount"))
    except (TypeError, ValueError):
        raise CashError("The amount must be a number.")
    if not amount > 0 or amount > 1e12:
        raise CashError("The amount must be more than zero.")
    cur = str(row.get("currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise CashError("The currency must be a three-letter code such as EUR.")
    note = str(row.get("note") or "")[:200]
    return {"date": date, "kind": kind, "amount": amount, "currency": cur, "note": note}


def check_rows(rows):
    """Backup rows, checked before a restore."""
    for r in rows:
        _clean(r)


def add(row):
    r = _clean(row)
    return db.x("INSERT INTO cash (date, kind, amount, currency, note) VALUES (?,?,?,?,?)",
                (r["date"], r["kind"], r["amount"], r["currency"], r["note"]))


def delete(cid):
    if not db.x_count("DELETE FROM cash WHERE id=?", (cid,)):
        raise CashError("That cash entry doesn't exist.")


def entries():
    return db.q("SELECT * FROM cash ORDER BY date DESC, id DESC")


def _rate(cur, base, date):
    """Base per unit of `cur` on `date`, today's when Yahoo has none that old; (rate, exact)."""
    r = data.fx_on(cur, base, date) if date else None
    if r is not None:
        return r, True
    return data.fx_rate(cur, base), data.fx_is_fixed(cur, base)


def balance(base, rows=None, cash_rows=None):
    """The cash to invest in the base currency, or None when no cash has been recorded. Trades and dividends count
    from the date of the first cash entry; an undated trade can't be placed and is named instead."""
    cash_rows = entries() if cash_rows is None else cash_rows
    if not cash_rows:
        return None
    rows = db.q("SELECT * FROM transactions") if rows is None else rows
    start = min(r["date"] for r in cash_rows)
    total, approx, undated, moves = 0.0, False, [], {"deposits": 0.0, "withdrawals": 0.0, "bought": 0.0, "sold": 0.0, "dividends": 0.0}
    for r in cash_rows:
        fx, exact = _rate(r["currency"], base, r["date"])
        v = r["amount"] * fx
        approx |= not exact
        moves["deposits" if r["kind"] == "deposit" else "withdrawals"] += v
        total += v if r["kind"] == "deposit" else -v
    for r in rows:
        kind = r.get("kind")
        if kind not in ("buy", "sell", "dividend"):
            continue
        if not r.get("date"):
            undated.append(r["ticker"])
            continue
        if r["date"] < start:
            continue
        cur = data.info(r["ticker"]).get("currency") or base
        if kind == "dividend":
            fx, exact = _rate(data.main_unit(cur), base, r["date"])
            v = (r.get("amount") or 0) * fx
            moves["dividends"] += v
            total += v
        else:
            fx, exact = _rate(cur, base, r["date"])  # handles pence and fils
            v = (r.get("shares") or 0) * (r.get("price") or 0) * fx
            moves["bought" if kind == "buy" else "sold"] += v
            total += -v if kind == "buy" else v
        approx |= not exact
    return {"balance": total, "base": base, "since": start, "approximate": approx, "moves": moves,
            "undated": sorted(set(undated)), "entries": len(cash_rows)}
