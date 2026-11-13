"""The tax engine: the ledger's trades and dividends in the tax currency, each at its own date's rate, matched by the
method a country's law prescribes, and the holdings' value on any date (for taxes on a deemed return or on wealth).

Countries' rates, allowances and forms are in taxlaw.py; this module only does what every one of them needs:
- `events()`: every trade and dividend, converted to the tax currency at the rate on its own date (an undated purchase
  or a missing rate uses today's and is marked inexact), and classed as shares, funds or crypto.
- matching a ticker's sales to its purchases: first in first out (`fifo`), weighted average cost (`average`), the UK's
  share identification (`uk`: same day, then the next 30 days, then the Section 104 pool) and US first in first out
  with the wash-sale rule (`us`).
- `held_on()` / `value_on()`: what was held on a date and what it was worth, at that day's quoted price and rate.
"""
import datetime as dt

from . import data, ledger

CATEGORY = {"Stocks": "shares", "ETFs": "funds", "Crypto": "crypto"}
TINY = 1e-9
WASH_DAYS = 30  # US: a loss is disallowed when the same security is bought within 30 days before or after
UK_DAYS = 30    # UK: a sale is matched first with the same day's purchases, then with those in the next 30 days


def _day(s):
    return dt.date.fromisoformat(s) if s else None


def held_over_a_year(bought, sold):
    """Held more than a year: sold the day after the anniversary or later (29 February counts from 1 March)."""
    b = _day(bought) if isinstance(bought, str) else bought
    s = _day(sold) if isinstance(sold, str) else sold
    try:
        anniversary = b.replace(year=b.year + 1)
    except ValueError:
        anniversary = dt.date(b.year + 1, 3, 1)
    return s > anniversary


class _Rates:
    """The rate on each date, looked up once (a year of trades asks for the same few days again and again)."""

    def __init__(self, tax_cur):
        self.cur, self.seen, self.today = tax_cur, {}, {}

    def on(self, currency, date):
        """(rate, exact): units of the tax currency per unit of `currency` on `date`, or today's rate, inexact, when
        the date is unknown or Yahoo has no rate that old. Raises data.DataError when there's no rate at all."""
        if date:
            key = (currency, date)
            if key not in self.seen:
                self.seen[key] = data.fx_on(currency, self.cur, date)
            if self.seen[key] is not None:
                return self.seen[key], True
        if currency not in self.today:
            self.today[currency] = data.fx_rate(currency, self.cur)
        return self.today[currency], data.fx_is_fixed(currency, self.cur)


def category_of(inf):
    return CATEGORY.get(inf.get("assetClass"), "shares")


def events(tax_cur, rows=None, splits_of=None, info_of=None):
    """{ticker: {"name", "currency", "category", "trades": [...], "dividends": [...]}} and the tickers left out for
    want of any exchange rate. Trades are in today's shares (split-adjusted), oldest first; each carries its value in
    the tax currency (`value`, shares x price x rate) and whether that rate is the day's own (`exact`)."""
    info_of = info_of or data.info
    rows = ledger.in_todays_shares(ledger.transactions() if rows is None else rows, splits_of)
    rates, out, left = _Rates(tax_cur), {}, set()
    for t in sorted(rows, key=ledger._order):
        tk = t["ticker"]
        if tk in left:
            continue
        if tk not in out:
            inf = info_of(tk) or {}
            out[tk] = {"ticker": tk, "name": inf.get("name") or tk, "currency": inf.get("currency") or "USD", "category": category_of(inf),
                       "assetClass": inf.get("assetClass"), "info": inf, "trades": [], "dividends": []}
        e = out[tk]
        kind = t.get("kind") or "buy"
        try:
            if kind == "dividend":
                pay = data.main_unit(e["currency"])
                rate, exact = rates.on(pay, t.get("date"))
                net = float(t["amount"])
                withheld = float(t.get("withheld") or 0)
                e["dividends"].append({"id": t.get("id"), "date": t["date"], "currency": pay, "rate": rate, "exact": exact,
                                       "netOwn": net, "withheldOwn": withheld, "withheldKnown": t.get("withheld") is not None,
                                       "net": net * rate, "withheld": withheld * rate, "gross": (net + withheld) * rate})
            else:
                rate, exact = rates.on(e["currency"], t.get("date"))
                shares, price = float(t["shares"]), float(t["price"])
                e["trades"].append({"id": t.get("id"), "kind": kind, "date": t.get("date") or "", "shares": shares, "price": price,
                                    "rate": rate, "exact": exact, "value": shares * price * rate})
        except data.DataError:
            left.add(tk)
            out.pop(tk, None)
    return out, sorted(left)


# ---------------------------------------------------------------- matching

def _piece(sale, shares, cost, bought, rule, **extra):
    proceeds = sale["value"] * shares / sale["shares"]
    held = (_day(sale["date"]) - _day(bought)).days if bought else None
    return {"date": sale["date"], "shares": shares, "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
            "bought": bought or None, "heldDays": held, "long": bool(bought) and held_over_a_year(bought, sale["date"]),
            "rule": rule, "exact": sale["exact"], "saleId": sale["id"], **extra}


def fifo(trades):
    """Each sale against the oldest shares still held. Returns (pieces, open lots)."""
    lots, pieces = [], []
    for t in trades:
        if t["kind"] == "buy":
            lots.append({"shares": t["shares"], "cost": t["value"], "bought": t["date"], "exact": t["exact"]})
            continue
        left = t["shares"]
        while left > TINY and lots:
            lot = lots[0]
            take = min(left, lot["shares"])
            cost = lot["cost"] * take / lot["shares"]
            pieces.append(_piece(t, take, cost, lot["bought"], "First in, first out", exactCost=lot["exact"]))
            lot["cost"] -= cost
            lot["shares"] -= take
            left -= take
            if lot["shares"] <= TINY:
                lots.pop(0)
    return pieces, lots


def average(trades):
    """Each sale at the weighted average cost of every share held at the time (France's PMP, Italy's costo medio,
    Austria's gleitender Durchschnittspreis). The average is in the tax currency: each purchase at its own date's rate."""
    shares = cost = 0.0
    pieces, first = [], None
    for t in trades:
        if t["kind"] == "buy":
            if shares <= TINY:
                first = t["date"]
            shares += t["shares"]
            cost += t["value"]
            continue
        take = min(t["shares"], shares)
        if take <= TINY:
            continue
        part = cost * take / shares
        pieces.append(_piece(t, take, part, first, "Weighted average cost", exactCost=True))
        shares -= take
        cost -= part
    return pieces, ([{"shares": shares, "cost": cost, "bought": first, "exact": True}] if shares > TINY else [])


def uk(trades):
    """HMRC's share identification (TCGA 1992 s105, s106A): a sale is matched first with purchases the same day, then
    with purchases in the next 30 days, earliest first (the "bed and breakfast" rule), and only then with the Section
    104 pool, which holds everything else at its average cost."""
    buys = [dict(t, left=t["shares"]) for t in trades if t["kind"] == "buy"]
    sales = [dict(t, left=t["shares"]) for t in trades if t["kind"] == "sell"]
    pieces = []
    # 1. The same day
    for s in sales:
        for b in buys:
            if b["date"] and b["date"] == s["date"] and b["left"] > TINY and s["left"] > TINY:
                take = min(s["left"], b["left"])
                pieces.append(_piece(s, take, b["value"] * take / b["shares"], b["date"], "Same day", exactCost=b["exact"]))
                b["left"] -= take
                s["left"] -= take
    # 2. The next 30 days, earlier sales first
    for s in sales:
        if s["left"] <= TINY or not s["date"]:
            continue
        end = _day(s["date"]) + dt.timedelta(days=UK_DAYS)
        for b in buys:
            if b["date"] and _day(s["date"]) < _day(b["date"]) <= end and b["left"] > TINY and s["left"] > TINY:
                take = min(s["left"], b["left"])
                pieces.append(_piece(s, take, b["value"] * take / b["shares"], b["date"], "Bought back within 30 days",
                                     exactCost=b["exact"], longKnown=False))
                b["left"] -= take
                s["left"] -= take
    # 3. The Section 104 pool, in date order (on one day, what's left of the purchases joins after the sale)
    pool_shares = pool_cost = 0.0
    seq = sorted([("b", b) for b in buys] + [("s", s) for s in sales], key=lambda x: (x[1]["date"] or "", 0 if x[0] == "b" and x[1]["date"] != "" else 1))
    for kind, x in seq:
        if kind == "b":
            if x["left"] > TINY:
                pool_shares += x["left"]
                pool_cost += x["value"] * x["left"] / x["shares"]
        elif x["left"] > TINY and pool_shares > TINY:
            take = min(x["left"], pool_shares)
            part = pool_cost * take / pool_shares
            pieces.append(_piece(x, take, part, None, "Section 104 pool", exactCost=True))
            pool_shares -= take
            pool_cost -= part
            x["left"] -= take
    pieces.sort(key=lambda p: (p["date"], p["saleId"] or 0))
    return pieces, ([{"shares": pool_shares, "cost": pool_cost, "bought": None, "exact": True}] if pool_shares > TINY else [])


def us(trades, wash=True):
    """First in, first out, with the wash-sale rule (IRC s1091): a loss is disallowed to the extent the same security
    was bought within 30 days before or after the sale (shares not sold in it), and added to those replacement shares'
    cost, whose holding period then includes the sold shares'. Crypto is property, not a security: no wash sales."""
    lots = []  # every purchase, with what's still held and how much of it has already served as a replacement
    for t in trades:
        if t["kind"] == "buy":
            lots.append({"shares": t["shares"], "left": t["shares"], "cost": t["value"], "bought": t["date"], "held_from": t["date"],
                         "exact": t["exact"], "used": 0.0, "added": 0.0})
    pieces = []
    for t in trades:
        if t["kind"] != "sell":
            continue
        sold_date = _day(t["date"]) if t["date"] else None
        left, sold_now = t["shares"], []
        for lot in lots:
            if left <= TINY:
                break
            # only lots bought by the sale's date can be sold (a later purchase is a later lot)
            if lot["left"] <= TINY or (lot["bought"] and sold_date and _day(lot["bought"]) > sold_date):
                continue
            take = min(left, lot["left"])
            per = lot["cost"] / lot["left"]
            cost = per * take
            p = _piece(t, take, cost, lot["held_from"], "First in, first out", exactCost=lot["exact"])
            p["acquired"] = lot["bought"] or None
            lot["cost"] -= cost
            lot["left"] -= take
            left -= take
            sold_now.append((lot, take))
            if wash and p["gain"] < -TINY and sold_date:
                loss_per = -p["gain"] / take
                need = take
                for r in lots:
                    if need <= TINY:
                        break
                    if not r["bought"] or abs((_day(r["bought"]) - sold_date).days) > WASH_DAYS:
                        continue
                    # shares of r still held after this sale (all of them for a later purchase), less those that
                    # already replaced an earlier loss: each share replaces once
                    avail = (r["left"] if _day(r["bought"]) <= sold_date else r["shares"]) - r["used"]
                    if avail <= TINY:
                        continue
                    n = min(need, avail)
                    r["used"] += n
                    r["cost"] += loss_per * n  # the disallowed loss is added to the replacement's basis
                    r["added"] += loss_per * n
                    if p["heldDays"] is not None and r["bought"]:  # and the sold shares' holding period to its own
                        r["held_from"] = min(r["held_from"], (_day(r["bought"]) - dt.timedelta(days=p["heldDays"])).isoformat())
                    need -= n
                disallowed = loss_per * (take - need)
                if disallowed > TINY:
                    p["washDisallowed"] = disallowed
                    p["gain"] += disallowed
            pieces.append(p)
    open_lots = [{"shares": lt["left"], "cost": lt["cost"], "bought": lt["bought"], "exact": lt["exact"], "washAdded": lt["added"]}
                 for lt in lots if lt["left"] > TINY]
    return pieces, open_lots


IE_DAYS = 28


def ie(trades):
    """Ireland: shares sold within four weeks of buying them are matched with those purchases, the latest first (s581
    TCA 1997); the rest first in, first out."""
    lots, pieces = [], []
    for t in trades:
        if t["kind"] == "buy":
            lots.append({"shares": t["shares"], "cost": t["value"], "bought": t["date"], "exact": t["exact"]})
            continue
        left = t["shares"]
        recent = [lt for lt in lots if lt["bought"] and t["date"] and 0 <= (_day(t["date"]) - _day(lt["bought"])).days <= IE_DAYS]
        for lot in sorted(recent, key=lambda x: x["bought"], reverse=True) + lots:
            if left <= TINY:
                break
            if lot["shares"] <= TINY:
                continue
            take = min(left, lot["shares"])
            cost = lot["cost"] * take / lot["shares"]
            rule = "Bought within 4 weeks (last in, first out)" if lot in recent else "First in, first out"
            pieces.append(_piece(t, take, cost, lot["bought"], rule, exactCost=lot["exact"]))
            lot["cost"] -= cost
            lot["shares"] -= take
            left -= take
        lots = [lt for lt in lots if lt["shares"] > TINY]
    return pieces, lots


METHODS = {"fifo": fifo, "average": average, "uk": uk, "us": us, "ie": ie}
METHOD_NAMES = {"fifo": "First in, first out", "average": "Weighted average cost", "uk": "Same day, 30 days, then the Section 104 pool",
                "us": "First in, first out, with the wash-sale rule", "ie": "Four-week rule, then first in, first out"}


def match(e, method):
    """A ticker's sales matched by `method` (taxlaw gives a country's per category). Returns (pieces, open lots)."""
    if method == "us":
        return us(e["trades"], wash=e["category"] != "crypto")
    return METHODS[method](e["trades"])


# ---------------------------------------------------------------- holdings on a date

def shares_on(e, date):
    """Shares of one ticker held at the end of `date` (purchases without a date count as held from the start)."""
    n = 0.0
    for t in e["trades"]:
        if (t["date"] or "") <= date:
            n += t["shares"] if t["kind"] == "buy" else -t["shares"]
    return max(n, 0.0)


def price_on(ticker, date):
    """(quoted close on or before `date`, the date it's from), or (None, None). The plain close, not adjusted for later
    dividends, since a value on a date is what it was quoted at then."""
    try:
        s = data.plain_closes(ticker)
    except data.DataError:
        try:
            s = data.history(ticker, "max")["Close"]
        except data.DataError:
            return None, None
    import pandas as pd
    s = s[s.index <= pd.Timestamp(date)]
    if s.empty or (pd.Timestamp(date) - s.index[-1]).days > 10:
        return None, None
    return float(s.iloc[-1]), s.index[-1].date().isoformat()


def value_on(evs, date, tax_cur, which=None):
    """[{ticker, shares, price, rate, value}] for what was held at the end of `date`, in the tax currency at that day's
    rate. `which` limits it to some tickers (funds only, say). A ticker with no price then is listed with value None."""
    rates, out = _Rates(tax_cur), []
    for tk, e in evs.items():
        if which and tk not in which:
            continue
        n = shares_on(e, date)
        if n <= TINY:
            continue
        price, when = price_on(tk, date)
        if price is None:
            out.append({"ticker": tk, "shares": n, "price": None, "rate": None, "value": None, "category": e["category"]})
            continue
        try:
            rate, _ = rates.on(e["currency"], when)
        except data.DataError:
            rate = None
        out.append({"ticker": tk, "shares": n, "price": price, "priceDate": when, "rate": rate,
                    "value": n * price * rate if rate is not None else None, "category": e["category"]})
    return out
