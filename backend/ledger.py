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


def _valid_date(t, required):
    date = t.get("date") or ""
    if not date:
        if required:
            raise LedgerError(f"{t.get('ticker')}: sales and dividends need a date.")
        return ""
    try:
        dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        raise LedgerError(f"{t.get('ticker')}: the date must look like 2025-01-31.")
    return date


def replay(rows):
    """{ticker: {"lots": open lots, "sales": each sale per lot it came from, "dividends": [...]}}. Raises LedgerError."""
    book = {}
    for t in sorted(rows, key=_order):
        kind = t.get("kind") or "buy"
        if kind not in KINDS:
            raise LedgerError(f"{t.get('ticker')}: unknown transaction type '{kind}'.")
        p = book.setdefault(t["ticker"], {"lots": [], "sales": [], "dividends": []})
        date = _valid_date(t, required=kind != "buy")
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


def xirr(flows):
    """Annual return from dated cash flows, [(date, amount)] with money in as negative. None if it can't be worked out,
    or if they span under a month, when an annual figure would mislead."""
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    start = min(d for d, _ in flows)
    if (max(d for d, _ in flows) - start).days < 30:
        return None

    def npv(rate):
        return sum(a / (1 + rate) ** ((d - start).days / 365) for d, a in flows)
    lo, hi = -0.99, 100.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def transactions():
    return db.q("SELECT * FROM transactions ORDER BY date DESC, id DESC")


def held_tickers():
    return [t for t, p in replay(transactions()).items() if p["lots"]]


def add(ticker, kind, date="", shares=None, price=None, amount=None, note=""):
    """Record a transaction if the ledger still adds up with it. Returns its id."""
    if kind not in KINDS:
        raise LedgerError(f"Unknown transaction type '{kind}'.")
    date = _valid_date({"ticker": ticker, "date": date}, required=kind != "buy")
    if date and dt.date.fromisoformat(date) > dt.date.today():
        raise LedgerError("The date can't be in the future.")
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
