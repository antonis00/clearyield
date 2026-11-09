"""Higher-level analysis: stock snapshot, screener, comparison, DCF, alerts and the daily digest."""
import datetime as dt
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import data, db, ledger, scoring
from . import indicators as ind
from .universe import tickers_for

log = logging.getLogger("stockpilot.insights")


def evaluate_ticker(t, settings):
    full = data.history(t, "5y")
    inf = data.info(t)
    sc = scoring.evaluate(inf, full["Close"], settings.get("weights"))
    return full, inf, sc


def upcoming(inf, today=None):
    """Next earnings and ex-dividend dates. Yahoo often leaves past dates in place, so those count as not announced."""
    today = today or dt.date.today()
    out = {}
    for name, key in (("earnings", "earningsTimestampStart"), ("exDividend", "exDividendDate")):
        when = dt.date.fromtimestamp(inf[key]) if inf.get(key) else None
        future = when is not None and when >= today
        out[name] = when.isoformat() if future else None
        out[f"{name}Days"] = (when - today).days if future else None
    return out


def on_which_day(close):
    """How to describe a move in the last bar: markets are often closed today, and Gulf ones on different days."""
    day = close.index[-1].date()
    return "today" if day == dt.date.today() else f"on {day.isoformat()}"


def _quick_row(t, settings):
    try:
        full, inf, sc = evaluate_ticker(t, settings)
    except Exception as e:
        return {"ticker": t, "error": str(e)}
    c = full["Close"]
    return {
        "ticker": t, "name": inf.get("name"), "region": inf.get("region"), "assetClass": inf.get("assetClass"),
        "sector": inf.get("sector"), "currency": inf.get("currency"), "priceUnit": inf.get("priceUnit"), "price": float(c.iloc[-1]),
        "day": ind.ret_over(c, 1), "ret1m": ind.ret_over(c, 21), "ret1y": ind.ret_over(c, 252),
        "pe": inf.get("trailingPE"), "pb": inf.get("priceToBook"), "divYield": inf.get("dividendYield"),
        "revGrowth": inf.get("revenueGrowth"), "roe": inf.get("returnOnEquity"), "de": inf.get("debtToEquity"),
        "marketCap": inf.get("marketCap"), "vol": ind.volatility(c), "rsi": float(ind.rsi(c).iloc[-1]),
        "composite": sc["composite"], "stance": sc["stance"], **{f"f_{k}": v for k, v in sc["factors"].items()},
        "scoredOn": "All factors" if inf.get("assetClass") == "Stocks" else "Trend & risk only", "events": upcoming(inf),
        "moveDay": on_which_day(c), "stale": data.is_stale(t), "asOf": c.index[-1].strftime("%Y-%m-%d"),
    }


def rows_for(tickers, settings, workers=8):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda t: _quick_row(t, settings), tickers))


def screen(body, settings):
    f = dict(body.get("filters") or {})
    markets = f.pop("markets", None) or body.get("markets") or ["US", "Europe", "Gulf", "ETFs", "Crypto"]
    tickers = tickers_for(markets) + [t.strip().upper() for t in (body.get("extra") or []) if t.strip()]
    rows = rows_for(list(dict.fromkeys(tickers)), settings)
    ok, failed = [r for r in rows if "error" not in r], [r["ticker"] for r in rows if "error" in r]

    def keep(r):
        def lt(v, lim):
            return lim is None or (v is not None and v <= lim)

        def gt(v, lim):
            return lim is None or (v is not None and v >= lim)
        if f.get("sector") and r.get("sector") != f["sector"]:
            return False
        return (lt(r["pe"] if (r["pe"] or 0) > 0 else None, f.get("max_pe")) and gt(r["divYield"], f.get("min_div_yield"))
                and gt(r["revGrowth"], f.get("min_rev_growth")) and lt(r["de"], f.get("max_de")) and gt(r["roe"], f.get("min_roe"))
                and gt(r["composite"], f.get("min_score")) and gt(r.get("f_momentum"), f.get("min_momentum"))
                and lt(r["vol"], f.get("max_vol")))

    out = [r for r in ok if keep(r)]
    sort = f.get("sort", "composite")
    key = {"divYield": "divYield"}.get(sort, "composite" if sort == "composite" else f"f_{sort}")
    out.sort(key=lambda r: -(r.get(key) if r.get(key) is not None else -1))
    if key == "composite":  # ETFs and crypto have no value, quality or growth score, so each class ranks on its own
        order = ["Stocks", "ETFs", "Crypto"]
        out.sort(key=lambda r: order.index(r["assetClass"]) if r["assetClass"] in order else len(order))
    return {"rows": out, "failed": failed, "filters": {**f, "markets": markets}, "scanned": len(rows)}


def compare(tickers, period, rf=0.04):
    closes, meta, failed = {}, [], []
    for t in tickers:
        try:
            h = data.history(t, period)
            inf = data.info(t)
        except data.DataError:
            failed.append(t)
            continue
        closes[t] = h["Close"]
        st = ind.stats(data.history(t, "5y")["Close"], rf)
        meta.append({"ticker": t, "name": inf.get("name"), "currency": inf.get("currency"), "pe": inf.get("trailingPE"),
                     "divYield": inf.get("dividendYield"), "periodReturn": float(h["Close"].iloc[-1] / h["Close"].iloc[0] - 1), **st})
    if not closes:
        return {"series": {}, "meta": [], "correlation": None, "failed": failed, "short": []}
    # Each line starts at 100 on its own first day, so a new listing doesn't cut the others short
    df = pd.concat(closes, axis=1).sort_index().ffill()
    norm = df.apply(lambda s: s / s.dropna().iloc[0] * 100).round(3)
    earliest = min(c.index[0] for c in closes.values())

    def pair(a, b):
        if a == b:
            return 1.0
        c = ind.correlation(closes[a], closes[b])
        return None if c is None else round(c, 2)
    names = list(closes)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in norm.index],
        "series": {t: norm[t].tolist() for t in norm.columns}, "meta": meta, "failed": failed,
        "short": [t for t, c in closes.items() if (c.index[0] - earliest).days > 30],
        "correlation": {"tickers": names, "matrix": [[pair(a, b) for b in names] for a in names]},
    }


def dcf(inf, growth=None, years=5, discount=0.09, terminal=0.025, fcf=None):
    fcf = fcf if fcf is not None else inf.get("freeCashflow")
    # sharesOutstanding counts one share class only: Roche by 7.5x, Alphabet by 2x
    shares = inf.get("impliedSharesOutstanding") or inf.get("sharesOutstanding")
    fx = inf.get("statementFx", 1.0)
    if fx is None:
        return {"error": "DCF needs an exchange rate between the statement and quote currencies, which isn't available right now."}
    if not fcf or not shares or fcf <= 0:
        return {"error": "DCF needs positive free cash flow and share count (not available for this asset)."}
    if growth is None:
        g = inf.get("revenueGrowth")
        growth = float(np.clip(g if g is not None else 0.05, 0.0, 0.15))
    if discount <= terminal:
        return {"error": "Discount rate must be above terminal growth."}
    pv = 0.0
    cf = fcf
    for yr in range(1, years + 1):
        cf *= 1 + growth
        pv += cf / (1 + discount) ** yr
    tv = cf * (1 + terminal) / (discount - terminal)
    tv_pv = tv / (1 + discount) ** years
    cash, debt = inf.get("totalCash") or 0, inf.get("totalDebt") or 0
    equity = pv + tv_pv + cash - debt
    # Cash flows are in the statement currency. Convert the result to the price's unit, e.g. USD -> GBp.
    minor = data.MINOR_UNITS.get(inf.get("currency"), (None, 1.0))[1]
    fair = equity / shares * fx * minor
    return {"fairValue": fair, "statementCurrency": inf.get("financialCurrency"), "growth": growth, "years": years, "discount": discount, "terminal": terminal,
            "terminalShare": tv_pv / (pv + tv_pv), "netCash": cash - debt,
            "startFcf": fcf}


def implied_growth(inf, price, years=5, discount=0.09, terminal=0.025):
    """The yearly free-cash-flow growth today's price assumes, by bisection. None if no rate from -50% to +100% gets there."""
    def fair(g):
        return dcf(inf, g, years, discount, terminal).get("fairValue")
    lo, hi = -0.5, 1.0
    low, high = fair(lo), fair(hi)
    if low is None or high is None or not low <= price <= high:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if fair(mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sensitivity(inf, growth, years=5, discount=0.09, terminal=0.025):
    """Fair value across growth and discount rates around the chosen ones."""
    growths = [round(growth + step, 6) for step in (-0.05, -0.025, 0, 0.025, 0.05)]
    discounts = [round(discount + step, 6) for step in (-0.02, -0.01, 0, 0.01, 0.02)]
    return {"growth": growths, "discount": discounts,
            "fairValue": [[dcf(inf, g, years, d, terminal).get("fairValue") for d in discounts] for g in growths]}


# ---------------------------------------------------------------- alerts
ALERT_KINDS = {
    "price_above": "Price rises above", "price_below": "Price falls below", "day_move": "Daily move exceeds (%)",
    "rsi_above": "RSI rises above", "rsi_below": "RSI falls below", "score_below": "Composite score falls below",
}


def check_alerts(settings):
    fired = []
    for a in db.q("SELECT * FROM alerts WHERE active=1"):
        try:
            h = data.history(a["ticker"], "1y")
        except data.DataError:
            continue
        if data.is_stale(a["ticker"]):  # don't fire on a price that may no longer be true
            continue
        c = h["Close"]
        price, v, k = float(c.iloc[-1]), a["value"], a["kind"]
        msg = None
        if k == "price_above" and price >= v:
            msg = f"{a['ticker']} at {price:,.2f} rose above {v:,.2f}"
        elif k == "price_below" and price <= v:
            msg = f"{a['ticker']} at {price:,.2f} fell below {v:,.2f}"
        elif k == "day_move":
            d = ind.ret_over(c, 1) or 0
            if abs(d) * 100 >= v:
                msg = f"{a['ticker']} moved {d:+.1%} {on_which_day(c)}"
        elif k in ("rsi_above", "rsi_below"):
            r = float(ind.rsi(c).iloc[-1])
            if (k == "rsi_above" and r >= v) or (k == "rsi_below" and r <= v):
                msg = f"{a['ticker']} RSI is {r:.0f}"
        elif k == "score_below":
            _, _, sc = evaluate_ticker(a["ticker"], settings)
            if sc["composite"] is not None and sc["composite"] < v:
                msg = f"{a['ticker']} composite score dropped to {sc['composite']}"
        if msg:
            db.x("UPDATE alerts SET active=0, triggered=?, message=? WHERE id=?", (time.time(), msg, a["id"]))
            fired.append({**a, "message": msg})
    return fired


# ---------------------------------------------------------------- journal scorecard
JUDGED_ACTIONS = ("buy", "add")
STANCE_ORDER = ["Strong", "Favorable", "Neutral", "Caution", "Weak"]


def _since(ticker, date):
    """(last close at or before `date`, latest close). Raises DataError when the ticker has no data."""
    c = data.history(ticker, "max")["Close"]
    past = c[c.index <= pd.Timestamp(date)]
    return (float(past.iloc[-1]) if not past.empty else None), float(c.iloc[-1])


def _dividend_adjustment(ticker, date):
    ratio = data.dividend_factor(ticker)
    if ratio is None:
        return 1.0
    past = ratio[ratio.index <= pd.Timestamp(date)]
    return float(past.iloc[-1]) if not past.empty else 1.0


def _entry_result(j, default_benchmark):
    date = dt.date.fromtimestamp(j["ts"])
    out = {"id": j["id"], "ticker": j["ticker"], "action": j["action"], "stance": j["stance"], "score": j["score"],
           "benchmark": j["benchmark"] or default_benchmark, "counted": False, "status": "Closed" if j["outcome"] else "Open"}
    try:
        then, now = _since(j["ticker"], date)
    except data.DataError as e:
        return {**out, "error": str(e)}
    # Prices written down before a split are restated, as Yahoo's prices are: 1,200 before a 10-for-1 split is 120 now
    factor = ledger.split_factor(data.splits(j["ticker"]), date.isoformat())
    # Your own price, restated like Yahoo's adjusted prices for the dividends paid since, so the return includes them
    # as the benchmark's does. Without this a dividend payer always looked worse than it was. Stop and target stay
    # plain price levels.
    entry = j["price"] / factor * _dividend_adjustment(j["ticker"], date) if j["price"] else then
    if not entry:
        return {**out, "error": "No price from the day of this entry."}
    out["returnSince"] = now / entry - 1
    if j["stop_price"] and now <= j["stop_price"] / factor:
        out["status"] = "Stop hit"
    elif j["target_price"] and now >= j["target_price"] / factor:
        out["status"] = "Target reached"
    try:
        b_then, b_now = _since(out["benchmark"], date)
    except data.DataError:
        return out
    if b_then:
        out["benchmarkReturn"] = b_now / b_then - 1
        out["excess"] = out["returnSince"] - out["benchmarkReturn"]
        # Only decisions to own something, and only once they have had time to work
        out["counted"] = j["action"] in JUDGED_ACTIONS and date < dt.date.today()
    return out


def _stats(rows):
    if not rows:
        return {"counted": 0, "hitRate": None, "avgReturn": None, "avgExcess": None}
    return {"counted": len(rows), "hitRate": sum(r["excess"] > 0 for r in rows) / len(rows),
            "avgReturn": sum(r["returnSince"] for r in rows) / len(rows), "avgExcess": sum(r["excess"] for r in rows) / len(rows)}


def scorecard(settings):
    """How journalled decisions have done against the benchmark, overall and by the stance when they were made."""
    entries = [_entry_result(j, settings.get("benchmark", "SPY")) for j in db.q("SELECT * FROM journal ORDER BY ts DESC")]
    counted = [e for e in entries if e["counted"]]
    by_stance = []
    for stance in STANCE_ORDER + ["Not recorded"]:
        rows = [e for e in counted if (e["stance"] or "Not recorded") == stance]
        if rows:
            st = _stats(rows)
            by_stance.append({"stance": stance, "n": st["counted"], "hitRate": st["hitRate"], "avgReturn": st["avgReturn"], "avgExcess": st["avgExcess"]})
    return {"entries": entries, "summary": {**_stats(counted), "benchmarks": sorted({e["benchmark"] for e in counted}), "byStance": by_stance}}


# ---------------------------------------------------------------- digest
# ---------------------------------------------------------------- score history
def record_scores(rows, day=None):
    """Keep one score a day per asset. Yahoo has no past fundamentals, so this can only build up from now on."""
    day = day or dt.date.today().isoformat()
    for r in rows:
        if r.get("composite") is not None:
            factors = {k[2:]: v for k, v in r.items() if k.startswith("f_")}
            db.x("INSERT OR IGNORE INTO score_history (date, ticker, composite, stance, factors) VALUES (?,?,?,?,?)",
                 (day, r["ticker"], r["composite"], r["stance"], json.dumps(factors)))


def record_today(settings):
    """Keep the daily score going while the app runs, even if the dashboard isn't opened."""
    tickers = list(dict.fromkeys(ledger.held_tickers() + [r["ticker"] for r in db.q("SELECT ticker FROM watchlist")]))
    record_scores([r for r in rows_for(tickers, settings) if "error" not in r])


def score_history(ticker):
    return db.q("SELECT date, composite, stance FROM score_history WHERE ticker = ? ORDER BY date", (ticker,))


def score_change(ticker):
    """A sentence if the stance changed between the last two recorded days, naming the factor that moved most."""
    last = db.q("SELECT * FROM score_history WHERE ticker = ? ORDER BY date DESC LIMIT 2", (ticker,))
    if len(last) < 2 or last[0]["stance"] == last[1]["stance"]:
        return None
    now, was = json.loads(last[0]["factors"]), json.loads(last[1]["factors"])
    moved = [(k, was[k], now[k]) for k in now if now.get(k) is not None and was.get(k) is not None]
    text = f"{ticker} went from {last[1]['stance']} to {last[0]['stance']} ({last[1]['composite']:.0f} to {last[0]['composite']:.0f})"
    if moved:
        k, a, b = max(moved, key=lambda m: abs(m[2] - m[1]))
        text += f", mostly on {k} ({a:.0f} to {b:.0f})"
    return text + "."


# What counts as a notable market move, by kind of tile: (one day, one month). A 2% day is big for the S&P 500 but
# ordinary for bitcoin or the VIX. Yields are measured in percentage points, since "-8%" of a 4.9% yield misleads.
MOVE_LIMITS = {"index": (0.02, 0.08), "volatility": (0.15, 0.30), "crypto": (0.05, 0.15), "fx": (0.01, 0.03), "yield": (0.15, 0.40)}


def _tile_kind(ticker):
    if data.price_unit(ticker, None) == "%":
        return "yield"
    if ticker in ("^VIX", "^VXN", "^V2TX"):
        return "volatility"
    if ticker.endswith("-USD"):
        return "crypto"
    if ticker.endswith("=X"):
        return "fx"
    return "index"


def market_moves(tiles):
    """Dashboard tiles that moved more than usual for their kind, over the latest day or the past month, each with
    its figures and dates. `tiles` is {ticker: name} for the tiles you show."""
    items = []
    for t, name in tiles.items():
        try:
            c = data.history(t, "1y")["Close"].dropna()
        except data.DataError:
            continue
        if len(c) < 23:
            continue
        kind = _tile_kind(t)
        day_limit, month_limit = MOVE_LIMITS[kind]
        # The same day and month as the dashboard tiles (a month is calendar time, as everywhere in the app)
        day, month = ind.ret_over(c, 1), ind.ret_over(c, 21)
        if kind == "yield":  # in percentage points, from the same starting values
            last = float(c.iloc[-1])
            day, month = (None if r is None else last - last / (1 + r) for r in (day, month))
            say = lambda v: f"{'rose' if v > 0 else 'fell'} {abs(v):.2f} percentage points"
        else:
            say = lambda v: f"{'rose' if v > 0 else 'fell'} {abs(v):.1%}"
        parts = []
        if day is not None and abs(day) >= day_limit:
            parts.append(f"{say(day)} {on_which_day(c)}")
        if month is not None and abs(month) >= month_limit:
            parts.append(f"{say(month)} over the past month")
        if parts:
            main = month if month is not None and abs(month) >= month_limit else day
            # Green for good news, red for bad: a rising VIX is fear, so it's red; a yield or a currency moving isn't
            # good or bad in itself, so it's neutral
            tone = {"volatility": -1, "yield": 0, "fx": 0}.get(kind, 1) * (1 if main > 0 else -1)
            items.append({"tone": tone, "ticker": t, "priority": 6.5, "kind": "market",
                          "text": f"{name} {' and '.join(parts)}."})
    return items


def portfolio_points(settings):
    """Your portfolio as a whole: its latest-day change, the core share against your target, and the warnings the
    Portfolio page shows (concentration, old prices, missing rates)."""
    from . import portfolio  # here, since the portfolio module is heavy and only the digest needs it from this one
    p = portfolio.summary(settings)
    if not p["positions"]:
        return []
    base, items = p["base"], []
    symbol = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF ", "JPY": "¥"}.get(base, f"{base} ")
    money = lambda v: f"{'-' if v < 0 else ''}{symbol}{abs(v):,.0f}"
    before = p["total"] - p["dayPnl"]
    if before and not p["incomplete"]:
        when = "today" if p["dayFrom"] == p["dayTo"] == dt.date.today().isoformat() else (
            f"on {p['dayTo']}" if p["dayFrom"] == p["dayTo"] else f"over each holding's latest day ({p['dayFrom']} to {p['dayTo']})")
        items.append({"tone": (p["dayPnl"] > 0) - (p["dayPnl"] < 0), "ticker": None, "priority": 3, "kind": "portfolio",
                      "text": f"Your portfolio {'gained' if p['dayPnl'] >= 0 else 'lost'} {money(abs(p['dayPnl']))} ({p['dayPnl'] / before:+.2%}) {when}. "
                              f"It's worth {money(p['total'])}, {money(p['pnl'])} ({p['pnlPct']:+.2%}) against what you paid."})
    cs = p.get("coreSatellite") or {}
    if cs.get("action") and cs["action"] != "Hold":
        to = "satellite" if cs["action"] == "To satellite" else "core"
        items.append({"tone": 0, "ticker": None, "priority": 4, "kind": "portfolio",
                      "text": f"Core is {cs['core']:.0%} of your portfolio against your {cs['target']:.0%} target. Reaching it means about "
                              f"{money(cs['amount'])} more in {to}, by moving money or putting new money there."})
    for w in p["warnings"]:
        items.append({"tone": -1, "ticker": None, "priority": 4, "kind": "portfolio", "text": w})
    return items


HEADLINE_DAYS = 3


def recent_headlines(rows, tickers, per_ticker=2):
    """The latest headlines that name a stock you hold or watch, from the past few days, with links. Funds are left
    out: their feed is general market news, and matching "Vanguard" would bring in any story about the firm."""
    from .ai import relevant_headlines
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=HEADLINE_DAYS)
    items = []
    for t in tickers:
        r = rows.get(t)
        if not r or r.get("assetClass") != "Stocks":
            continue
        fresh = []
        for n in relevant_headlines(data.news(t), t, r.get("name")):
            try:
                when = pd.Timestamp(n.get("time"))
                when = when.tz_localize("UTC") if when.tzinfo is None else when
            except (TypeError, ValueError):
                continue
            if when >= since:
                fresh.append((when, n))
        for when, n in sorted(fresh, key=lambda x: x[0], reverse=True)[:per_ticker]:
            url = n.get("url") or ""
            items.append({"tone": 0, "ticker": t, "priority": 7, "kind": "news", "text": f"{t}: {n['title']}" + (f" ({n['publisher']})" if n.get("publisher") else ""),
                          "url": url if url.startswith(("https://", "http://")) else None, "time": when.isoformat()})
    return items


def digest(settings, tiles=None):
    """What's worth knowing today: alerts and journal reminders first, then your portfolio, your holdings and
    watchlist, the market's notable moves, and headlines naming your stocks. Every item is a rule with its figures."""
    held = ledger.held_tickers()
    watched = [r["ticker"] for r in db.q("SELECT ticker FROM watchlist")]
    tickers = list(dict.fromkeys(held + watched))
    items = []
    open_entries = db.q("SELECT * FROM journal WHERE outcome='' OR outcome IS NULL")
    journal_only = [j["ticker"] for j in open_entries if j["ticker"] not in tickers]
    rows = {r["ticker"]: r for r in rows_for(list(dict.fromkeys(tickers + journal_only)), settings) if "error" not in r}
    for t, r in rows.items():
        if t not in tickers:  # only here so its stop and target can be checked below
            continue
        tag = "holding" if t in held else "watchlist"
        if r["day"] is not None and abs(r["day"]) >= 0.03:
            items.append({"tone": 1 if r["day"] > 0 else -1, "ticker": t, "priority": 5, "text": f"{t} ({tag}) moved {r['day']:+.1%} {r['moveDay']}."})
        if r["rsi"] >= 70:
            items.append({"tone": -1, "ticker": t, "priority": 6, "text": f"{t} is overbought (RSI {r['rsi']:.0f})."})
        elif r["rsi"] <= 30:
            items.append({"tone": 1, "ticker": t, "priority": 6, "text": f"{t} is oversold (RSI {r['rsi']:.0f})."})
        ev = r["events"]
        if t in held and ev["earningsDays"] is not None and ev["earningsDays"] <= 14:
            items.append({"tone": 0, "ticker": t, "priority": 3, "text": f"{t} reports earnings in {ev['earningsDays']} days ({ev['earnings']})."})
        if t in held and ev["exDividendDays"] is not None and ev["exDividendDays"] <= 7:
            items.append({"tone": 0, "ticker": t, "priority": 3, "text": f"{t} goes ex-dividend in {ev['exDividendDays']} days ({ev['exDividend']})."})
        if r["stance"] in ("Weak",) and t in held:
            items.append({"tone": -1, "ticker": t, "priority": 4, "text": f"{t} is a holding with a Weak composite score ({r['composite']}) - review the thesis."})
    record_scores([r for t, r in rows.items() if t in tickers])
    for t in tickers:
        change = score_change(t)
        if change:
            items.append({"tone": 0, "ticker": t, "priority": 4, "text": change})
    today = dt.date.today().isoformat()
    for j in open_entries:
        r = rows.get(j["ticker"])
        if r:
            if j["stop_price"] and r["price"] <= j["stop_price"]:
                items.append({"tone": -1, "ticker": j["ticker"], "priority": 1, "text": f"{j['ticker']} hit your thesis stop ({j['stop_price']}). Invalidation: {j['invalidation'] or 'n/a'}"})
            if j["target_price"] and r["price"] >= j["target_price"]:
                items.append({"tone": 1, "ticker": j["ticker"], "priority": 1, "text": f"{j['ticker']} reached your target ({j['target_price']}). Time to re-evaluate."})
        if j["review_date"] and j["review_date"] <= today:
            items.append({"tone": 0, "ticker": j["ticker"], "priority": 2, "text": f"Journal review due for {j['ticker']} ({j['action']} on {dt.date.fromtimestamp(j['ts']).isoformat()})."})
    check_alerts(settings)
    fired = db.q("SELECT * FROM alerts WHERE triggered IS NOT NULL AND dismissed = 0 ORDER BY triggered DESC")
    items = [{"tone": 0, "ticker": a["ticker"], "priority": 0, "text": "Alert: " + a["message"], "alertId": a["id"]} for a in fired] + items
    for extra in (lambda: portfolio_points(settings), lambda: market_moves(tiles or {}), lambda: recent_headlines(rows, tickers)):
        try:  # one part failing (Yahoo, say) mustn't lose the rest of the digest
            items += extra()
        except Exception as e:
            log.warning("digest part failed: %s", e)
    items.sort(key=lambda i: i["priority"])  # what needs a decision first, then what's coming, then what's just of note
    if not tickers:
        items.append({"tone": 0, "ticker": None, "priority": 9, "text": "Add stocks to your watchlist or portfolio to get a personalised digest."})
    return {"items": items}
