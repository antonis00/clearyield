"""The transaction ledger: every buy, sale and dividend. Open lots and realised results are worked out from it,
matching each sale to the oldest shares still held (first in, first out)."""
import datetime as dt
import math

from . import db

KINDS = ("buy", "sell", "dividend")
TINY = 1e-9


class LedgerError(ValueError):
    """Transactions that can't be right, such as selling more than was held. The message is for the user."""


def _order(t):
    # By date, with undated lots first because they can only be older. On the same day, buys come before sales.
    return t.get("date") or "", 0 if (t.get("kind") or "buy") == "buy" else 1, t.get("id") or 0


def _positive(t, key, allow_zero=False):
    v = t.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or (v == 0 and not allow_zero):
        raise LedgerError(f"{t.get('ticker')}: {key} must be {'zero or more' if allow_zero else 'above zero'}.")
    return v


def replay(rows):
    """{ticker: {"lots": open lots, "sales": each sale per lot it came from, "dividends": [...]}}. Raises LedgerError."""
    book = {}
    for t in sorted(rows, key=_order):
        kind = t.get("kind") or "buy"
        if kind not in KINDS:
            raise LedgerError(f"{t.get('ticker')}: unknown transaction type '{kind}'.")
        p = book.setdefault(t["ticker"], {"lots": [], "sales": [], "dividends": []})
        date = t.get("date") or ""
        if kind == "buy":
            p["lots"].append({"id": t.get("id"), "ticker": t["ticker"], "shares": _positive(t, "shares"), "cost": _positive(t, "price", True),
                              "bought": date, "note": t.get("note") or ""})
        elif kind == "dividend":
            p["dividends"].append({"id": t.get("id"), "date": date, "amount": _positive(t, "amount")})
        else:
            left, price = _positive(t, "shares"), _positive(t, "price", True)
            if sum(lot["shares"] for lot in p["lots"]) < left - TINY:
                raise LedgerError(f"{t['ticker']}: selling {left:g} shares on {date or 'an unknown date'} is more than were held then.")
            while left > TINY:
                lot = p["lots"][0]
                take = min(left, lot["shares"])
                p["sales"].append({"id": t.get("id"), "ticker": t["ticker"], "shares": take, "price": price, "date": date, "cost": lot["cost"], "bought": lot["bought"]})
                left -= take
                lot["shares"] -= take
                if lot["shares"] <= TINY:
                    p["lots"].pop(0)
    return book


def transactions():
    return db.q("SELECT * FROM transactions ORDER BY date DESC, id DESC")


def held_tickers():
    return [t for t, p in replay(transactions()).items() if p["lots"]]


def _check_date(date, required):
    if not date:
        if required:
            raise LedgerError("Sales and dividends need a date.")
        return ""
    try:
        when = dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        raise LedgerError("The date must look like 2025-01-31.")
    if when > dt.date.today():
        raise LedgerError("The date can't be in the future.")
    return date


def add(ticker, kind, date="", shares=None, price=None, amount=None, note=""):
    """Record a transaction if the ledger still adds up with it. Returns its id."""
    if kind not in KINDS:
        raise LedgerError(f"Unknown transaction type '{kind}'.")
    date = _check_date(date, required=kind != "buy")
    rows = db.q("SELECT * FROM transactions")
    if kind != "buy" and not any(r["ticker"] == ticker and r["kind"] == "buy" for r in rows):
        raise LedgerError(f"There's no purchase of {ticker} to go with this.")
    row = {"ticker": ticker, "kind": kind, "date": date, "shares": shares, "price": price, "amount": amount, "note": note, "id": 1 << 60}
    replay(rows + [row])
    return db.x("INSERT INTO transactions (ticker, kind, date, shares, price, amount, note) VALUES (?,?,?,?,?,?,?)",
                (ticker, kind, date, shares, price, amount, note))


def delete(tid):
    """Delete a transaction unless a later sale depends on it."""
    replay([r for r in db.q("SELECT * FROM transactions") if r["id"] != tid])
    db.x("DELETE FROM transactions WHERE id=?", (tid,))
