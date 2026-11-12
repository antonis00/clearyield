"""Orders prepared, you approve. The app drafts orders from your own targets (the Rebalancing card's rules), you change
or untick any of them and approve the rest, you place them at your broker yourself, and you mark each one done with the
price you got, which records it in the ledger. ClearYield never places a trade: it has no broker connection.

The rules, all on the page (ORDER_RULES in app.js; keep them in step):
- New money goes to the asset classes below target, each in proportion to how far below it is (the same rule as the
  portfolio simulation's monthly money), so a class at or over target gets none.
- Rebalancing by selling is only when you ask for it, and only once a class has drifted more than BAND (2 points, the
  Rebalancing card's "Hold" band): then every class is brought to its target. Inside the band nothing is sold and any
  new money follows the rule above.
- Within a class, money goes to (or comes from) what you already hold there, in proportion to its value, so the mix
  inside the class stays as you chose it. A class with a target but nothing held gets no order until you name what
  to buy there (`picks`, a ticker per class, checked to be of that class): the app never picks it for you.
- Shares are whole unless you allow fractions; crypto is always fractional. Rounding is down, so a sale never sells
  more than held. Buys never spend more than the new money plus what the sales bring in: rounding a sale down would
  otherwise leave the buys short of cash. An order worth under MIN_ORDER of the base currency is dropped.
- Orders approved and not yet done count as if filled, so preparing again doesn't repeat them.
- The limit price offered is the latest close; change it before approving.
"""
import csv
import datetime as dt
import io
import math
import time

from . import data, db, ledger, portfolio
from .tax import LONG_DAYS

BAND = 0.02
MIN_ORDER = 1.0
CRYPTO_DECIMALS = 6
FRACTION_DECIMALS = 4
STATUSES = ("approved", "done", "cancelled")
MOVED = 0.03  # an approved order whose price has since moved more than this is flagged: the limit may be out of date


class OrderError(ValueError):
    """An order that can't be right. The message is for the user."""


def exact(x):
    """A quantity or price written in full: no rounding to six digits, no 1.2e+06."""
    return f"{x:.8f}".rstrip("0").rstrip(".")


def _round_down(shares, decimals):
    f = 10 ** decimals
    return math.floor(shares * f + 1e-9) / f


def _split(amount, weights):
    """`amount` shared out in proportion to `weights` (all >= 0)."""
    total = sum(weights.values())
    return {k: amount * w / total for k, w in weights.items()} if total > 0 else {}


def _class_amounts(val, tgt, total, new_money, sell):
    """How much each asset class should gain (+) or lose (-), in the base currency, and the rule that said so."""
    after = total + new_money
    if sell:
        drift = max(abs(val[c] / total - tgt[c]) for c in tgt) if total else 0
        if drift > BAND:
            return ({c: tgt[c] * after - val[c] for c in tgt},
                    "Every class is brought to its target: what's over is sold, what's under is bought.")
        if new_money <= 0:
            return {}, f"Every class is within {BAND * 100:.0f} points of its target, so nothing needs selling."
        within = f"Every class is within {BAND * 100:.0f} points of its target, so nothing is sold. "
    else:
        within = ""
    if new_money <= 0:
        return {}, "Enter an amount of new money, or allow selling to rebalance."
    short = {c: max(0.0, tgt[c] * after - val[c]) for c in tgt}  # these add up to at least the new money
    return _split(new_money, short), within + "New money goes to the classes below target, each in proportion to how far below it is."


def _row(ticker, base):
    """A holding row with nothing held yet: for a ticker bought by an approved order, or picked for an empty class."""
    inf = data.info(ticker)
    close = data.history(ticker, "1y")["Close"]
    return {"ticker": ticker, "name": inf.get("name"), "shares": 0.0, "price": float(close.iloc[-1]),
            "fx": data.fx_rate(inf.get("currency"), base), "currency": inf.get("currency"), "assetClass": inf.get("assetClass"),
            "value": 0.0, "asOf": close.index[-1].strftime("%Y-%m-%d"), "stale": data.is_stale(ticker)}


def _pending(rows, base):
    """Approved orders not yet done, applied to the holdings as if filled at today's price: {ticker: change in shares},
    and the rows for tickers bought but not yet held."""
    change, extra = {}, []
    for o in open_orders():
        n = o["shares"] if o["side"] == "buy" else -o["shares"]
        change[o["ticker"]] = change.get(o["ticker"], 0.0) + n
        if o["ticker"] not in {r["ticker"] for r in rows} | {r["ticker"] for r in extra}:
            extra.append(_row(o["ticker"], base))
    return change, extra


def plan(settings, new_money=0.0, sell=False, fractional=False, picks=None):
    """Draft orders. Nothing is saved: approving saves them. `picks` is {asset class: ticker} for classes you hold
    nothing in: what to buy there."""
    if not isinstance(new_money, (int, float)) or not math.isfinite(new_money) or new_money < 0:
        raise OrderError("New money must be zero or more.")
    if picks is not None and not isinstance(picks, dict):
        raise OrderError("Picks are a ticker for each asset class.")
    picks = {str(c): str(t).strip().upper() for c, t in (picks or {}).items() if isinstance(t, str) and t.strip()}
    s = portfolio.summary(settings)
    s.pop("_series", None)
    base = s["base"]
    out = {"base": base, "newMoney": new_money, "sell": sell, "fractional": fractional, "orders": [], "notes": [], "rule": "",
           "classes": [], "spend": 0.0, "left": new_money, "pending": 0, "empty": []}
    if s["incomplete"]:
        out["notes"].append(f"No orders while {', '.join(s['missing'])} has no current data: the weights would be wrong.")
        return out
    if not s["positions"]:
        out["notes"].append("Add your holdings first: orders are worked out from what you hold.")
        return out
    targets = settings.get("targets") or {}
    tsum = sum(targets.values())
    if tsum <= 0:
        out["notes"].append("Set asset class targets in Settings first.")
        return out

    # Holdings as they'll be once the approved orders are done
    rows = [dict(r) for r in s["positions"]]
    change, extra = _pending(rows, base)
    rows += extra
    for r in rows:
        r["shares"] = max(0.0, r["shares"] + change.get(r["ticker"], 0.0))
        r["value"] = r["shares"] * r["price"] * r["fx"]
    rows = [r for r in rows if r["shares"] > ledger.TINY]
    out["pending"] = len(open_orders())
    if out["pending"]:
        out["notes"].append(f"Counts the {out['pending']} approved order{'s' if out['pending'] > 1 else ''} not yet done as if filled at today's price.")
    total = sum(r["value"] for r in rows)
    if total <= 0:
        out["notes"].append("Nothing would be left held once the approved orders are done.")
        return out
    classes = sorted(set(targets) | {r["assetClass"] for r in rows})
    tgt = {c: targets.get(c, 0) / tsum for c in classes}
    val = {c: sum(r["value"] for r in rows if r["assetClass"] == c) for c in classes}
    if not sell and any(val[c] > 0 and tgt[c] == 0 for c in classes):
        out["notes"].append("Something you hold has a 0% target: only selling moves it towards that.")
    amounts, out["rule"] = _class_amounts(val, tgt, total, new_money, sell)
    # Classes with money to receive and nothing held: the page asks what to buy there
    held_before = {r["assetClass"] for r in rows}
    out["empty"] = [{"assetClass": c, "amount": a, "target": tgt[c], "pick": None} for c, a in amounts.items()
                    if a >= MIN_ORDER and c not in held_before]
    for e in out["empty"]:
        t = picks.get(e["assetClass"])
        if not t:
            continue
        try:
            row = _row(t, base)
        except Exception:  # a symbol Yahoo doesn't know, or no prices
            out["notes"].append(f"{t} wasn't found with prices, so nothing goes to {e['assetClass']} yet.")
            continue
        if row["assetClass"] != e["assetClass"]:
            out["notes"].append(f"{t} is in {row['assetClass'] or 'no asset class'}, not {e['assetClass']}: pick one from {e['assetClass']}.")
            continue
        e["pick"] = t
        rows.append(row)

    def orders_for(c, amount, budget=None):
        """The orders for one class's amount, split by what's held there; returns their value (buys +, sales -)."""
        mine = [r for r in rows if r["assetClass"] == c]
        if not mine:
            if amount > 0:
                out["notes"].append(f"{c} is under target ({val[c] / total:.0%} of {tgt[c]:.0%}) but you hold nothing there: "
                                    f"about {amount:,.0f} {base} would go to one you choose. Enter its ticker to include it.")
            return 0.0
        moved = 0.0
        weights = {r["ticker"]: r["value"] for r in mine}
        if not sum(weights.values()):  # the ticker you picked for a class you hold nothing in
            weights = {r["ticker"]: 1.0 for r in mine}
        for t, part in _split(amount, weights).items():
            row = next(x for x in mine if x["ticker"] == t)
            per_share = row["price"] * row["fx"]
            if per_share <= 0:
                continue
            decimals = CRYPTO_DECIMALS if c == "Crypto" else (FRACTION_DECIMALS if fractional else 0)
            shares = _round_down(abs(part) / per_share, decimals)
            if part < 0:
                shares = min(shares, row["shares"])
            worth = shares * per_share
            if shares <= 0 or worth < MIN_ORDER:
                if abs(part) >= MIN_ORDER and worth < abs(part):
                    out["notes"].append(f"{t}: {abs(part):,.0f} {base} is less than one share ({per_share:,.2f} {base}), so no order. "
                                        "Allow fractional shares if your broker has them.")
                continue
            side = "buy" if part > 0 else "sell"
            order = {"ticker": t, "name": row.get("name"), "side": side, "shares": shares, "limitPrice": row["price"],
                     "currency": row["currency"], "value": worth, "assetClass": c, "asOf": row["asOf"], "stale": row["stale"],
                     "reason": f"{c} {val[c] / total:.1%} of the portfolio, target {tgt[c]:.1%}. "
                               f"{'Sells' if side == 'sell' else 'Buys'} {abs(amount):,.0f} {base} of {c}"
                               + (f"; {t} is your choice, since you hold nothing in {c} yet." if not val[c] else
                                  f"; {t} is {weights[t] / sum(weights.values()):.0%} of your {c}, so it takes that share." if len(mine) > 1 else ".")}
            if side == "sell":
                order["gain"] = _sale_gain(t, shares, row["price"])
            out["orders"].append(order)
            held_after[c] += worth if side == "buy" else -worth
            moved += worth if side == "buy" else -worth
        return moved

    held_after = dict(val)
    # Sales first: what they actually bring in, after rounding, pays for the buys with the new money
    raised = -sum(orders_for(c, a) for c, a in amounts.items() if a <= -MIN_ORDER)
    buys = {c: a for c, a in amounts.items() if a >= MIN_ORDER}
    held_in = {r["assetClass"] for r in rows}
    for c in [c for c in buys if c not in held_in]:  # named in a note; its money can't be spent, so it's left out of the cap
        orders_for(c, buys.pop(c))
    cash, wanted = new_money + raised, sum(buys.values())
    if wanted > cash + 1e-9:
        if cash < MIN_ORDER:
            buys = {}
        else:
            buys = {c: a * cash / wanted for c, a in buys.items()}
            out["notes"].append(f"Buys are scaled to the {cash:,.0f} {base} available: rounding the sales down raised less than the targets need.")
    spent = sum(orders_for(c, a) for c, a in buys.items())
    out["classes"] = [{"assetClass": c, "target": tgt[c], "now": val[c] / total,
                       "after": held_after[c] / (total + spent - raised) if total + spent - raised else 0} for c in classes]
    out["spend"] = spent - raised  # buys less sales, in the base currency
    out["left"] = new_money + raised - spent  # cash left over (never negative: buys are capped at what's available)
    if not out["orders"] and not out["notes"] and amounts:
        out["notes"].append("Nothing to do: every amount came to less than one order.")
    return out


def _sale_gain(ticker, shares, price):
    """A sale's gain before costs, first in first out, in the quote currency at today's price (the tax year counts it
    in your base currency at each date's rate, so a foreign holding's figure differs there). Sales already approved
    and not yet done take the oldest lots first. Whether every share sold was held over a year (None when a lot has
    no date)."""
    lots = [dict(lot) for lot in portfolio.positions().get(ticker, {}).get("lots", [])]
    ahead = sum(o["shares"] for o in open_orders() if o["ticker"] == ticker and o["side"] == "sell")
    left, cost, dates = shares, 0.0, []
    for lot in lots:
        skip = min(ahead, lot["shares"])
        ahead -= skip
        have = lot["shares"] - skip
        if left <= ledger.TINY:
            break
        if have <= ledger.TINY:
            continue
        take = min(left, have)
        cost += take * lot["cost"]
        dates.append(lot["bought"])
        left -= take
    today = dt.date.today()
    over = None if not dates or not all(dates) else all((today - dt.date.fromisoformat(d)).days > LONG_DAYS for d in dates)
    return {"amount": shares * price - cost, "heldOverYear": over}


# ---- approved orders ----
def _clean(o):
    if not isinstance(o, dict):
        raise OrderError("An order isn't readable.")
    t = str(o.get("ticker") or "").strip().upper()
    side = o.get("side")
    try:
        shares, price = float(o.get("shares")), float(o.get("limitPrice", o.get("limit_price")))
    except (TypeError, ValueError):
        raise OrderError(f"{t or 'An order'}: shares and the limit price must be numbers.")
    if not t:
        raise OrderError("Every order needs a symbol.")
    if side not in ("buy", "sell"):
        raise OrderError(f"{t}: an order is a buy or a sale.")
    if not (math.isfinite(shares) and shares > 0) or not (math.isfinite(price) and price > 0):
        raise OrderError(f"{t}: shares and the limit price must be above zero.")
    return t, side, shares, price, str(o.get("reason") or "")[:500]


def approve(orders):
    """Save the orders you approved. A sale can't be for more than you hold, less sales already approved and open."""
    if not isinstance(orders, list) or not orders:
        raise OrderError("Tick at least one order to approve.")
    clean = [_clean(o) for o in orders]
    held = {t: p["shares"] for t, p in portfolio.positions().items()}
    selling = {}
    for r in open_orders():
        if r["side"] == "sell":
            selling[r["ticker"]] = selling.get(r["ticker"], 0) + r["shares"]
    for t, side, shares, _, _ in clean:
        if side == "sell":
            selling[t] = selling.get(t, 0) + shares
            if selling[t] > held.get(t, 0) + ledger.TINY:
                raise OrderError(f"{t}: selling {exact(selling[t])} shares, counting approved sales not yet done, is more than the {exact(held.get(t, 0))} held.")
        data.info(t)  # a symbol Yahoo doesn't know fails here, before anything is saved
    now = time.time()
    db.x_many("INSERT INTO orders (created, ticker, side, shares, limit_price, currency, reason, status) VALUES (?,?,?,?,?,?,?, 'approved')",
              [(now, t, side, shares, price, data.info(t).get("currency") or "", reason) for t, side, shares, price, reason in clean])
    return len(clean)


def open_orders():
    return db.q("SELECT * FROM orders WHERE status='approved' ORDER BY created, id")


def approved_on(o):
    return dt.date.fromtimestamp(o["created"]).isoformat()


def split_since(o, until=None):
    """How many of today's shares one share of the order has become through splits since it was approved: an order
    for 10 approved before a 2-for-1 split is filled as 20 at half the price."""
    until = until or dt.date.today().isoformat()
    factor = 1.0
    for when, ratio in data.splits(o["ticker"]):
        if approved_on(o) < when <= until:
            factor *= ratio
    return factor


def listing():
    """Open orders with today's price beside the limit (restated for any split since approval), and the latest done
    or cancelled."""
    out = []
    for r in open_orders():
        split = split_since(r)
        try:
            px = float(data.history(r["ticker"], "1y")["Close"].iloc[-1])
        except data.DataError:
            px = None
        limit = r["limit_price"] / split
        move = px / limit - 1 if px else None
        out.append({**r, "approvedOn": approved_on(r), "split": split, "fillShares": r["shares"] * split, "fillPrice": limit,
                    "price": px, "move": move, "moved": move is not None and abs(move) > MOVED})
    closed = db.q("SELECT * FROM orders WHERE status!='approved' ORDER BY closed DESC, id DESC LIMIT 20")
    return {"open": out, "closed": closed, "movedAt": MOVED}


def _get(oid):
    rows = db.q("SELECT * FROM orders WHERE id=?", (oid,))
    if not rows:
        raise OrderError("There's no such order.")
    return rows[0]


def _num(v, default, what):
    if v in (None, ""):
        return default
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise OrderError(f"The {what} must be a number.")
    if not math.isfinite(n):
        raise OrderError(f"The {what} must be a number.")
    return n


def mark_done(oid, shares=None, price=None, date=None, fee=None):
    """You placed it and it filled: record it in the ledger. A fee is folded into the price, as the tax year counts
    cost and proceeds (a buy costs more, a sale brings in less). Left empty, shares and price are the order's, restated
    for any split between approval and the fill. Returns the transaction's id."""
    o = _get(oid)
    if o["status"] != "approved":
        raise OrderError("This order is already closed.")
    date = date or dt.date.today().isoformat()
    try:
        dt.date.fromisoformat(date)
    except (TypeError, ValueError):
        raise OrderError("The date must look like 2026-01-31.")
    if date < approved_on(o):
        raise OrderError(f"The fill can't be before the order was approved ({approved_on(o)}).")
    split = split_since(o, date)
    shares = _num(shares, o["shares"] * split, "shares")
    price = _num(price, o["limit_price"] / split, "price")
    fee = _num(fee, 0.0, "fee")
    if not (shares > 0 and price > 0 and fee >= 0):
        raise OrderError("Shares and price must be above zero, and the fee zero or more.")
    # The fee is in the main currency (pounds), the price may be in its minor unit (pence)
    in_quote = fee * data.MINOR_UNITS.get(o["currency"], (None, 1.0))[1]
    paid = (shares * price + in_quote) / shares if o["side"] == "buy" else (shares * price - in_quote) / shares
    if paid <= 0:
        raise OrderError("The fee is more than the sale brought in.")
    note = f"Order #{oid}" + (f", fee {exact(fee)} {data.main_unit(o['currency']) if o['currency'] else ''}".rstrip() + " included in the price" if fee else "")
    # Claimed first, so a second click (or a second tab) can't record the same fill twice
    if not db.x_count("UPDATE orders SET status='done', closed=? WHERE id=? AND status='approved'", (time.time(), oid)):
        raise OrderError("This order is already closed.")
    try:
        tx = ledger.add(o["ticker"], o["side"], date, shares, paid, None, note)  # LedgerError if the ledger wouldn't add up
    except Exception:
        db.x("UPDATE orders SET status='approved', closed=NULL WHERE id=?", (oid,))
        raise
    db.x("UPDATE orders SET fill_shares=?, fill_price=?, fill_date=?, fee=?, tx_id=? WHERE id=?", (shares, price, date, fee, tx, oid))
    return tx


def cancel(oid):
    _get(oid)
    if not db.x_count("UPDATE orders SET status='cancelled', closed=? WHERE id=? AND status='approved'", (time.time(), oid)):
        raise OrderError("This order is already closed.")


def _cell(text):
    """Text a spreadsheet won't run as a formula."""
    text = str(text or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def to_csv(rows):
    """The open orders as a ticket list for your broker, quantities and prices in full (restated for any split since
    approval). Yahoo's symbol carries the exchange as a suffix (VWCE.DE is VWCE on Xetra); your broker may want the
    symbol without it."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order", "Action", "Symbol (Yahoo)", "Quantity", "Order type", "Limit price", "Currency", "Approved", "Reason"])
    for r in rows:
        split = split_since(r)
        w.writerow([r["id"], r["side"].upper(), _cell(r["ticker"]), exact(r["shares"] * split), "LIMIT", exact(r["limit_price"] / split), r["currency"],
                    dt.datetime.fromtimestamp(r["created"]).strftime("%Y-%m-%d %H:%M"), _cell(r["reason"])])
    return buf.getvalue()


def check_rows(rows):
    """Orders from a backup, before they're restored: anything the pages rely on. Raises ValueError."""
    for r in rows:
        ok = (r.get("side") in ("buy", "sell") and r.get("status") in STATUSES and str(r.get("ticker") or "").strip()
              and all(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) and math.isfinite(r[k]) and r[k] > 0
                      for k in ("shares", "limit_price", "created"))
              and (r["status"] != "done" or isinstance(r.get("tx_id"), int)))
        if not ok:
            raise ValueError(f"An order in the backup isn't usable (order {r.get('id', '?')}).")
