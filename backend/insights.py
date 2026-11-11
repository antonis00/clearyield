"""Higher-level analysis: stock snapshot, screener, comparison, DCF, alerts and the daily digest."""
import datetime as dt
from collections import defaultdict
import json
import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import data, db, ledger, scoring
from . import indicators as ind
from .universe import CORE_FUNDS, REGIONS, UNIVERSE, region_of, tickers_for

log = logging.getLogger("clearyield.insights")


def multi_year_growth(financials, key, years=3):
    """Average yearly growth of revenue or net income over the last `years` annual statements, or None when there
    are fewer, or either end is a loss (a growth rate from a loss means nothing)."""
    rows = [(int(r["year"]), r.get(key)) for r in financials or [] if r.get(key) is not None]
    rows = rows[-(years + 1):]
    if len(rows) < years or rows[0][1] <= 0 or rows[-1][1] <= 0:
        return None
    span = rows[-1][0] - rows[0][0]
    return (rows[-1][1] / rows[0][1]) ** (1 / span) - 1 if span > 0 else None


def fill_from_statements(t, inf, fin):
    """Ratios Yahoo leaves empty, worked out from the company's own annual statements where they're there: free cash
    flow from the cash flow statement, debt / equity and the current ratio from the balance sheet (0 debt when Yahoo
    says the total debt is nil, which is when it leaves the ratio empty). Each one filled is named in `statementYears`
    with its year, so the score shows where it came from. Banks are left alone: those three aren't scored for banks."""
    if "bank" in (inf.get("industry") or "").lower():
        return inf
    out, years = dict(inf), {}
    if out.get("freeCashflow") is None:
        latest = next((r for r in reversed(fin or []) if r.get("fcf") is not None), None)
        if latest:
            out["freeCashflow"], years["freeCashflow"] = latest["fcf"], latest["year"]
    if out.get("debtToEquity") is None or out.get("currentRatio") is None:
        need_sheet = (out.get("debtToEquity") is None and out.get("totalDebt") != 0) or out.get("currentRatio") is None
        bal = data.balance(t) if need_sheet else {}
        if out.get("debtToEquity") is None:
            debt = 0.0 if out.get("totalDebt") == 0 else bal.get("totalDebt")
            if debt is not None and (bal.get("equity") or 0) > 0:
                out["debtToEquity"], years["debtToEquity"] = debt / bal["equity"], bal["year"]
            elif debt == 0.0:
                out["debtToEquity"], years["debtToEquity"] = 0.0, "no debt"
        if out.get("currentRatio") is None and bal.get("currentAssets") is not None and (bal.get("currentLiabilities") or 0) > 0:
            out["currentRatio"], years["currentRatio"] = bal["currentAssets"] / bal["currentLiabilities"], bal["year"]
    if years:
        out["statementYears"] = years
    return out


def evaluate_ticker(t, settings):
    full = data.history(t, "5y")
    inf = data.info(t)
    if inf.get("assetClass") == "Stocks":
        # Growth over several years of annual statements, not one quarter against a year before: a single quarter
        # read an oil-price swing as +40% growth for companies whose revenue shrank each year since 2022
        fin = data.financials(t)
        inf = {**fill_from_statements(t, inf, fin), "revenueGrowth3y": multi_year_growth(fin, "revenue"), "earningsGrowth3y": multi_year_growth(fin, "netIncome")}
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


def _since_new_year(c):
    """The return since the last close of the previous year, or None without one."""
    before = c[c.index < dt.datetime(c.index[-1].year, 1, 1, tzinfo=getattr(c.index, "tz", None))]
    return float(c.iloc[-1] / before.iloc[-1] - 1) if len(before) else None


def _usd(amount, currency):
    """An amount in US dollars at today's rate. Yahoo gives market cap in the main unit (pounds for a stock quoted in
    pence), so the main unit's rate applies."""
    if not amount or not currency:
        return None
    try:
        return float(amount) * data.fx_rate(data.main_unit(currency), "USD")
    except Exception:
        return None


def _quick_row(t, settings):
    try:
        full, inf, sc = evaluate_ticker(t, settings)
    except Exception as e:
        return {"ticker": t, "error": str(e)}
    c = full["Close"]
    return {
        "ticker": t, "name": inf.get("name"), "region": inf.get("region"), "country": inf.get("country"), "assetClass": inf.get("assetClass"),
        "sector": inf.get("sector"), "industry": inf.get("industry"), "currency": inf.get("currency"), "priceUnit": inf.get("priceUnit"), "price": float(c.iloc[-1]),
        "day": ind.ret_over(c, 1), "ret1m": ind.ret_over(c, 21), "ret1y": ind.ret_over(c, 252),
        "pe": inf.get("trailingPE"), "pb": inf.get("priceToBook"), "divYield": inf.get("dividendYield"),
        # Revenue growth a year over three years where there are the statements, else the latest quarter
        "revGrowth": inf["revenueGrowth3y"] if inf.get("revenueGrowth3y") is not None else inf.get("revenueGrowth"),
        "revGrowthBasis": "3y" if inf.get("revenueGrowth3y") is not None else "quarter", "roe": inf.get("returnOnEquity"), "de": inf.get("debtToEquity"),
        "marketCap": inf.get("marketCap"), "vol": ind.volatility(c), "rsi": float(ind.rsi(c).iloc[-1]),
        "composite": sc["composite"], "stance": sc["stance"], **{f"f_{k}": v for k, v in sc["factors"].items()},
        "scoredOn": "All factors" if inf.get("assetClass") == "Stocks" else "Trend & risk only", "events": upcoming(inf),
        "moveDay": on_which_day(c), "stale": data.is_stale(t), "asOf": c.index[-1].strftime("%Y-%m-%d"),
        "fromHigh": float(c.iloc[-1] / ind.window(c, 252).max() - 1), "fromLow": float(c.iloc[-1] / ind.window(c, 252).min() - 1),
        "retYtd": _since_new_year(c), "marketCapUsd": _usd(inf.get("marketCap"), inf.get("currency")),
        "above50": bool(len(c) >= 50 and c.iloc[-1] > ind.sma(c, 50).iloc[-1]),
    }


BATCH = 40           # a long list is scored this many at a time...
GENTLE_FROM = 100    # ...when it has more than this many


# ---------------------------------------------------------------- progress of long scorings
# The day's first Screener, Opportunities or sector ranking downloads every stock, which takes a while: each long list
# being scored is kept here with how far it has got, overall and per region, so the page can show real progress.
TRACK_FROM = 20  # a shorter list is quick: nothing to show
_jobs, _jobs_lock, _job_ids = {}, threading.Lock(), itertools.count(1)


def progress():
    """The long scorings under way, oldest first: what for (`kind`), how many of how many, per region, seconds so far,
    and the seconds left of a pause Yahoo asked for (the scoring waits it out)."""
    now = time.time()
    with _jobs_lock:
        jobs = [{**j, "regions": {r: list(v) for r, v in j["regions"].items()}, "elapsed": now - j["started"]} for j in _jobs.values()]
    return {"jobs": sorted(jobs, key=lambda j: j["started"]), "pause": data.rate_limited_for()}


def _group(t):
    """Where a ticker counts in the progress: its region for a stock, "ETFs" for a fund (whose region is what it holds,
    such as Global or Emerging, which would read like more regions), "Crypto" for a coin."""
    return "ETFs" if t in UNIVERSE.get("ETFs", {}) else region_of(t)


def _tracked(tickers, kind):
    """A job entry for a long list, and the function that counts one ticker done; None for a short list."""
    if len(tickers) < TRACK_FROM:
        return None, lambda t: None
    regions = {}
    for t in tickers:
        regions.setdefault(_group(t), [0, 0])[1] += 1
    job = {"id": next(_job_ids), "kind": kind, "done": 0, "total": len(tickers), "started": time.time(), "regions": regions}
    with _jobs_lock:
        _jobs[job["id"]] = job

    def tick(t):
        with _jobs_lock:
            job["done"] += 1
            job["regions"][_group(t)][0] += 1
    return job, tick


def rows_for(tickers, settings, workers=8, patience=60, kind="scoring"):
    """Each ticker's quick row. A long list goes in batches, and when Yahoo has just refused for asking too much, the
    next batch waits for the pause to end (up to `patience` seconds in all) rather than asking hundreds more times.
    A long list's progress is kept for `progress()` while it runs, under `kind` (what it's for)."""
    job, tick = _tracked(tickers, kind)

    def row(t):
        try:
            return _quick_row(t, settings)
        finally:
            tick(t)
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            if len(tickers) <= GENTLE_FROM:
                return list(ex.map(row, tickers))
            out, waited = [], 0.0
            for i in range(0, len(tickers), BATCH):
                while (pause := data.rate_limited_for()) > 0 and waited < patience:
                    step = min(pause, 15.0, patience - waited)
                    time.sleep(step)
                    waited += step
                out.extend(ex.map(row, tickers[i:i + BATCH]))
            return out
    finally:
        if job:
            with _jobs_lock:
                _jobs.pop(job["id"], None)


# ---------------------------------------------------------------- ownership
INSIDER_MONTHS = 6  # the window the insider summary covers


def insider_summary(own, months=INSIDER_MONTHS, today=None):
    """Insiders' buying and selling on the market over the past months, from the trades' kinds: awards, option
    exercises and gifts are counted apart, never as buying. With a plain sentence that doesn't overstate a sale."""
    today = pd.Timestamp(today or dt.date.today())
    since = (today - pd.DateOffset(months=months)).strftime("%Y-%m-%d")
    recent = [x for x in (own or {}).get("insiders") or [] if x["date"] >= since]
    side = lambda k: [x for x in recent if x["kind"] == k]
    buys, sells = side("buy"), side("sell")
    total = lambda xs, f: sum(x[f] for x in xs if x.get(f) is not None)
    out = {"months": months, "since": since, "buys": len(buys), "buyers": len({x["insider"] for x in buys}), "buyShares": total(buys, "shares"),
           "buyValue": total(buys, "value"), "sells": len(sells), "sellers": len({x["insider"] for x in sells}), "sellShares": total(sells, "shares"),
           "sellValue": total(sells, "value"), "other": len(side("other"))}
    people = lambda n: f"{n} {'person' if n == 1 else 'people'}"
    if not recent:
        out["headline"] = f"No insider trades reported in the past {months} months."
    elif not buys and not sells:
        out["headline"] = f"No insider bought or sold on the market in the past {months} months ({out['other']} awards, option exercises or gifts, which aren't decisions to buy or sell)."
    elif buys and not sells:
        out["headline"] = f"Insiders only bought: {out['buys']} purchase{'s' if out['buys'] > 1 else ''} by {people(out['buyers'])} in the past {months} months."
    elif sells and not buys:
        out["headline"] = (f"Insiders only sold: {out['sells']} sale{'s' if out['sells'] > 1 else ''} by {people(out['sellers'])} in the past {months} months. "
                           "Selling is common and often routine (taxes, planned sales, spreading their wealth), so it says less than buying.")
    else:
        out["headline"] = (f"Both: {out['buys']} purchase{'s' if out['buys'] > 1 else ''} by {people(out['buyers'])} and {out['sells']} sale{'s' if out['sells'] > 1 else ''} "
                           f"by {people(out['sellers'])} in the past {months} months.")
    return out


# ---------------------------------------------------------------- ranks within a sector
MIN_PEERS = 8          # fewer other companies than this in a sector, and a rank says too little to show
PEERS_FOR = 7 * 3600   # seconds the table of every stock's scores is kept: the background scoring renews it every six hours
RANKED = ("composite", "value", "quality", "growth", "momentum", "risk")


def followed(settings):
    """The regions you follow (Settings), in the usual order: only their stocks are downloaded, screened and ranked."""
    chosen = settings.get("regions") or REGIONS
    return [r for r in REGIONS if r in chosen] or list(REGIONS)


def _peer_markets(settings):
    """The regions you follow, narrowed further by CLEARYIELD_PEER_MARKETS where it's set: the live browser checks start
    with an empty cache, and ranking against all 400 symbols on every run would get the computer refused by Yahoo."""
    import os
    mine = followed(settings)
    named = [m.strip() for m in (os.environ.get("CLEARYIELD_PEER_MARKETS") or "").split(",") if m.strip() in mine]
    return named or mine


def peer_table(settings, fresh=False, patience=60):
    """Every stock in the built-in list with its sector and scores, kept seven hours. Funds and crypto have no sector.
    `fresh` scores again even when a table is kept (the background scoring does, so the table is never old).
    The factors are kept and the overall score worked out with the weights in Settings now, so changing the weights
    doesn't mean scoring every stock again."""
    regions = _peer_markets(settings)
    key = _peer_key(settings)
    table = None if fresh else db.cache_get(key, PEERS_FOR)
    if table is None:
        table = {}
        for r in rows_for(tickers_for(regions), settings, 6, patience, kind="ranks"):
            if "error" in r or r.get("assetClass") != "Stocks" or not r.get("sector"):
                continue
            table[r["ticker"]] = {"sector": r["sector"], "region": r.get("region"), **{k: r.get(f"f_{k}") for k in RANKED[1:]}}
        db.cache_set(key, table)
    weights = settings.get("weights")
    return {t: {**r, "composite": scoring.combine({k: r[k] for k in RANKED[1:]}, weights)} for t, r in table.items()}


def _peer_key(settings):
    return "peers:v3:" + ",".join(_peer_markets(settings))  # another set of followed regions is another table


_building, _build_lock = set(), __import__("threading").Lock()


def peer_table_now(settings):
    """The table if it's ready, else None while it's built in the background (one build at a time). A page never
    waits minutes for it: built cold, it downloads every followed stock, which also draws Yahoo's refusals.
    In demo mode it's built at once, since that takes seconds and keeps tests simple."""
    if data.DEMO or db.cache_get(_peer_key(settings), PEERS_FOR) is not None:
        return peer_table(settings)
    key = _peer_key(settings)
    with _build_lock:
        if key in _building:
            return None
        _building.add(key)

    def build():
        try:
            peer_table(settings, True, 900)
        except Exception as e:  # the next request tries again
            log.warning("building the sector table failed: %s", e)
        finally:
            with _build_lock:
                _building.discard(key)
    __import__("threading").Thread(target=build, daemon=True).start()
    return None


def sector_ranks(ticker, settings, own=None):
    """Where a stock's overall and factor scores stand among the other stocks in its sector, from every market in the
    list: the share of them it beats (a tie counts half), how many there are, and their middle score. A score with
    fewer than MIN_PEERS others to compare with gets no rank. `own` is the stock's quick row, if already worked out.
    {"pending": True} while the table is being built."""
    table = peer_table_now(settings)
    if table is None:
        return {"pending": True}
    row = table.get(ticker)
    if row is None:  # a stock outside the list, ranked against it
        own = own or rows_for([ticker], settings)[0]
        if "error" in own or own.get("assetClass") != "Stocks" or not own.get("sector"):
            return None
        row = {"sector": own["sector"], "composite": own.get("composite"), **{k: own.get(f"f_{k}") for k in RANKED[1:]}}
    others = [r for t, r in table.items() if r["sector"] == row["sector"] and t != ticker]
    ranks = {}
    for k in RANKED:
        mine, vals = row.get(k), sorted(r[k] for r in others if r.get(k) is not None)
        if mine is None or len(vals) < MIN_PEERS:
            ranks[k] = {"beats": None, "of": len(vals)}
            continue
        beats = (sum(v < mine for v in vals) + 0.5 * sum(v == mine for v in vals)) / len(vals)
        mid = len(vals) // 2
        ranks[k] = {"beats": beats, "of": len(vals), "median": vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2}
    regions = {}
    for r in others:
        regions[r.get("region") or "Other"] = regions.get(r.get("region") or "Other", 0) + 1
    return {"sector": row["sector"], "others": len(others), "regions": regions, "ranks": ranks, "minPeers": MIN_PEERS}


def _beats(row, table):
    """(share of the other stocks in its sector whose overall score it beats, how many), or (None, how many)."""
    if row.get("assetClass") != "Stocks" or not row.get("sector") or row.get("composite") is None:
        return None, 0
    vals = [r["composite"] for t, r in table.items() if r["sector"] == row["sector"] and t != row["ticker"] and r.get("composite") is not None]
    if len(vals) < MIN_PEERS:
        return None, len(vals)
    return (sum(v < row["composite"] for v in vals) + 0.5 * sum(v == row["composite"] for v in vals)) / len(vals), len(vals)


# ---------------------------------------------------------------- opportunities
# Every list on the Opportunities page is a rule over the factor scores, with its limits named here and shown on the page
OPP_ZONE = 60          # the map's "cheap and rising" corner: value and momentum both at least this
OPP_TOP = 10           # how many the ranked list shows
OPP_FIT = 8            # how many portfolio-fit candidates
SALE_QUALITY = 60      # "quality on sale": a quality score at least this...
SALE_DROP = -0.10      # ...and at least 10% below its 52-week high, with the fall paused: back above its 50-day
                       # average (a stock still falling tends to keep falling for a while, so it isn't "on sale" yet)
REASON_AT = 65         # a factor this strong is named as a reason
SECTOR_CAP = 3         # at most this many from one sector in a ranked list, so a list isn't one sector's bet
FIT_PERIOD = "2y"      # how far back the portfolio fit compares weekly returns


def _capped(rows, n):
    """The first `n` rows in their order, with at most SECTOR_CAP from one sector."""
    out, seen = [], defaultdict(int)
    for r in rows:
        if seen[r.get("sector") or "Unknown"] < SECTOR_CAP:
            seen[r.get("sector") or "Unknown"] += 1
            out.append(r)
        if len(out) == n:
            break
    return out


def _reasons(r):
    """The strongest factors in words, each with the figure behind it, strongest first."""
    say = {
        "value": lambda: f"Cheap (P/E {r['pe']:.1f})" if r.get("pe") and r["pe"] > 0 else "Cheap",
        "quality": lambda: f"High quality (ROE {r['roe']:.0%})" if r.get("roe") is not None else "High quality",
        "growth": lambda: (f"Growing (revenue {r['revGrowth']:+.0%} a year over 3 years)" if r.get("revGrowthBasis") == "3y" else f"Growing (revenue {r['revGrowth']:+.0%}, latest quarter)")
                          if r.get("revGrowth") is not None else "Growing",
        "momentum": lambda: f"Strong trend ({r['ret1y']:+.0%} in a year)" if r.get("ret1y") is not None else "Strong trend",
        "risk": lambda: f"Calm ({r['vol']:.0%} volatility)" if r.get("vol") is not None else "Calm",
    }
    ranked = sorted(((r.get(f"f_{k}"), k) for k in say if r.get(f"f_{k}") is not None), reverse=True)
    return [say[k]() for s, k in ranked if s >= REASON_AT][:3]


def opportunities(settings, markets):
    """Stocks in the chosen markets, scored on current data, in four views: the value/momentum map, the top
    ranked, the best fit for your portfolio's satellite part, and good companies well below their high.
    Stocks only: funds and crypto are scored on trend and risk alone, so they can't be compared on value."""
    from . import portfolio
    from .universe import LEGACY_MARKETS, REGION_OF_COUNTRY
    mine = followed(settings)
    markets = [REGION_OF_COUNTRY[LEGACY_MARKETS[m][0]] if m in LEGACY_MARKETS else m for m in (markets or [])]  # "US": its region
    markets = [m for m in dict.fromkeys(markets or mine) if m in mine] or mine
    rows = rows_for(tickers_for(markets), settings, kind="opportunities")
    failed = [r["ticker"] for r in rows if "error" in r]
    stocks = [r for r in rows if "error" not in r and r.get("assetClass") == "Stocks" and r.get("composite") is not None]
    for r in stocks:
        r["reasons"] = _reasons(r)

    # What you hold now, valued each day over the past two years in your base currency, to see how much each
    # candidate (also in your currency) would have moved with it. Both in one currency, so the exchange rate
    # doesn't pass for the stock; two years, so the estimate rests on about a hundred weeks rather than fifty.
    p = portfolio.summary(settings)
    base = p["base"]
    held = {r["ticker"] for r in p["positions"]}

    def in_base(t, shares=1.0):
        close = data.history(t, FIT_PERIOD)["Close"]
        cur = data.info(t).get("currency")
        rate = data.fx_series(cur, base, close.index)
        return close * shares * (rate if rate is not None else data.fx_rate(cur, base))
    total = None
    try:
        parts = [in_base(r["ticker"], r["shares"]) for r in p["positions"]]
        total = pd.concat(parts, axis=1).ffill().dropna().sum(axis=1) if parts else None
    except data.DataError:
        total = None
    for r in stocks:
        r["corr"] = None
        if total is not None and len(total) > 1 and r["ticker"] not in held:
            try:
                r["corr"] = ind.correlation(in_base(r["ticker"]), total)
            except data.DataError:
                pass

    pick = lambda r: {k: r.get(k) for k in ("ticker", "name", "region", "sector", "composite", "stance", "f_value", "f_quality", "f_growth",
                                            "f_momentum", "f_risk", "pe", "roe", "revGrowth", "revGrowthBasis", "ret1y", "vol", "fromHigh", "above50", "reasons", "corr", "price", "currency")}
    top = _capped(sorted(stocks, key=lambda r: -r["composite"]), OPP_TOP)
    fit = []
    for r in stocks:
        if r["corr"] is None:
            continue
        # Diversifying: 100 for a stock that moves independently of you (or against you), 0 for one that moves in step
        diversifies = (1 - max(r["corr"], 0)) * 100
        fit.append({**pick(r), "diversifies": diversifies, "fit": (r["composite"] + diversifies) / 2})
    fit = _capped(sorted(fit, key=lambda r: -r["fit"]), OPP_FIT)
    sale = _capped(sorted((r for r in stocks if (r.get("f_quality") or 0) >= SALE_QUALITY and r["fromHigh"] <= SALE_DROP and r.get("above50")),
                          key=lambda r: -r["f_quality"]), OPP_TOP)
    cs = p.get("coreSatellite") or {}
    return {
        "markets": markets, "scanned": len(rows), "failed": failed, "map": [pick(r) for r in stocks], "top": [pick(r) for r in top],
        "fit": fit, "onSale": [pick(r) for r in sale], "holdings": sorted(held),
        "fitWeeks": int((total.index[-1] - total.index[0]).days // 7) if total is not None and len(total) > 1 else None,
        "satellite": {"amount": cs.get("amount"), "action": cs.get("action"), "target": cs.get("target"), "core": cs.get("core"), "base": p["base"]} if cs else None,
        "rules": {"zone": OPP_ZONE, "top": OPP_TOP, "fit": OPP_FIT, "saleQuality": SALE_QUALITY, "saleDrop": SALE_DROP, "reasonAt": REASON_AT, "sectorCap": SECTOR_CAP},
    }


BENCHMARK = "ACWI"  # the world index a screen's "beat the market" is measured against


def _in_industry(r, wanted):
    """Whether a row is in the industries asked for: Yahoo's names (a list), or part of one ("Banks")."""
    have = (r.get("industry") or "").lower()
    if isinstance(wanted, list):
        return have in {w.lower() for w in wanted}
    return str(wanted).lower() in have


def screen(body, settings):
    f = dict(body.get("filters") or {})
    offered = [*followed(settings), "ETFs", "Crypto"]  # a region you don't follow isn't screened
    from .universe import COUNTRY_GROUPS, LEGACY_MARKETS, REGION_OF_COUNTRY
    raw = f.pop("markets", None) or body.get("markets") or []
    old = [c for m in raw for c in LEGACY_MARKETS.get(m, [])]  # "US", "Gulf": the countries they meant
    asked = [m for m in raw if m not in LEGACY_MARKETS] + [REGION_OF_COUNTRY[c] for c in old]
    asked = list(dict.fromkeys(asked)) or None
    countries = [c for x in (f.get("countries") or []) for c in COUNTRY_GROUPS.get(x, [x])] + old  # "Gulf" is its countries
    if countries:
        f["countries"] = list(dict.fromkeys(countries))
    if countries and not asked:  # "Japanese banks": only the regions those countries are in need scanning
        asked = list(dict.fromkeys(REGION_OF_COUNTRY[c] for c in countries if c in REGION_OF_COUNTRY))
    # A region asked for but not followed isn't screened, and the screen says so: showing the followed regions instead
    # answered "chipmakers in Asia" with Europe's
    unfollowed = [m for m in (asked or []) if m not in offered]
    markets = [m for m in (asked or offered) if m in offered]
    notes = [f"Not screened: {', '.join(unfollowed)}, which {'isn' if len(unfollowed) == 1 else 'aren'}'t among the regions you follow. "
             "Add {} in Settings to include {}.".format("it" if len(unfollowed) == 1 else "them", "it" if len(unfollowed) == 1 else "them")] if unfollowed else []
    tickers = (tickers_for(markets) if markets else []) + [t.strip().upper() for t in (body.get("extra") or []) if t.strip()]
    rows = rows_for(list(dict.fromkeys(tickers)), settings, kind="screener")
    ok, failed = [r for r in rows if "error" not in r], [r["ticker"] for r in rows if "error" in r]

    debt_skipped = []
    # "Beat the market": a past-year return above the world index's (ACWI, in dollars; each stock's in its own currency)
    if f.pop("beat_market", None):
        try:
            bench = ind.ret_over(data.history(BENCHMARK, "5y")["Close"], 252)
        except Exception:
            bench = None
        if bench is None:
            notes.append(f"\"Beat the market\" wasn't applied: no past-year return for the world index ({BENCHMARK}).")
        else:
            f["min_ret1y"] = max(bench, f.get("min_ret1y", bench))
            notes.append(f"Beat the market: a past-year return above the world index's ({BENCHMARK}, {bench:+.1%} in US dollars); "
                         "each stock's return is in its own currency.")

    def keep(r):
        def lt(v, lim):
            return lim is None or (v is not None and v <= lim)

        def gt(v, lim):
            return lim is None or (v is not None and v >= lim)
        if f.get("countries") and r.get("country") not in f["countries"]:
            return False
        if f.get("sector") and r.get("sector") != f["sector"]:
            return False
        if f.get("industry") and not _in_industry(r, f["industry"]):
            return False
        if f.get("exclude_sector") and r.get("sector") in ([f["exclude_sector"]] if isinstance(f["exclude_sector"], str) else f["exclude_sector"]):
            return False
        if f.get("exclude_industry") and _in_industry(r, f["exclude_industry"]):
            return False
        # Debt/equity says nothing about a bank, whose deposits and borrowing are its raw material (and Yahoo gives
        # none for most), so a debt limit is set aside for banks and the screen says so
        de_limit = f.get("max_de")
        if de_limit is not None and "bank" in (r.get("industry") or "").lower():
            de_limit = None
            debt_skipped.append(r["ticker"])
        return (lt(r["pe"] if (r["pe"] or 0) > 0 else None, f.get("max_pe")) and gt(r["divYield"], f.get("min_div_yield"))
                and gt(r["revGrowth"], f.get("min_rev_growth")) and lt(r["de"], de_limit) and gt(r["roe"], f.get("min_roe"))
                and gt(r["composite"], f.get("min_score")) and gt(r.get("f_momentum"), f.get("min_momentum"))
                and lt(r["vol"], f.get("max_vol"))
                and all(gt(r.get(f"f_{k}"), f.get(f"min_{k}")) for k in ("value", "quality", "growth", "risk"))
                and gt(r.get("marketCapUsd"), f.get("min_mcap")) and lt(r.get("marketCapUsd"), f.get("max_mcap"))
                and lt(r["pb"] if (r.get("pb") or 0) > 0 else None, f.get("max_pb"))
                and gt(r.get("rsi"), f.get("min_rsi")) and lt(r.get("rsi"), f.get("max_rsi"))
                and gt(r.get("ret1y"), f.get("min_ret1y")) and lt(r.get("ret1y"), f.get("max_ret1y"))
                and gt(r.get("retYtd"), f.get("min_ytd")) and lt(r.get("retYtd"), f.get("max_ytd"))
                and gt(-r["fromHigh"] if r.get("fromHigh") is not None else None, f.get("min_off_high"))
                and lt(-r["fromHigh"] if r.get("fromHigh") is not None else None, f.get("max_off_high"))
                and lt(r.get("fromLow"), f.get("max_above_low")))

    out = [r for r in ok if keep(r)]
    # Each stock's overall score against the rest of its sector, from every market in the list
    table = (peer_table_now(settings) if any(r.get("assetClass") == "Stocks" for r in out) else {})
    ranks_pending = table is None
    table = table or {}
    for r in out:
        r["sectorBeats"], r["sectorOf"] = _beats(r, table)
    sort = f.get("sort", "composite")
    key = {"divYield": "divYield", "size": "marketCapUsd", "return": "ret1y", "composite": "composite"}.get(sort, f"f_{sort}")
    out.sort(key=lambda r: -(r.get(key) if r.get(key) is not None else -1e18))
    if key == "composite":  # ETFs and crypto have no value, quality or growth score, so each class ranks on its own
        order = ["Stocks", "ETFs", "Crypto"]
        out.sort(key=lambda r: order.index(r["assetClass"]) if r["assetClass"] in order else len(order))
    if debt_skipped:
        notes.append(f"The debt limit wasn't applied to {len(debt_skipped)} bank{'s' if len(debt_skipped) > 1 else ''}: debt/equity doesn't measure a bank's risk, since deposits and borrowing are what it lends from.")
    if ranks_pending:
        notes.append("Ranks within each sector are being prepared in the background; they'll show in a minute or two.")
    return {"rows": out, "failed": failed, "filters": {**f, "markets": markets}, "scanned": len(rows), "notes": notes}


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
        # Revenue growth a year over three years where there are the statements: one quarter against a year before
        # turned an oil-price swing into 15% a year for five years
        g = inf.get("revenueGrowth3y") if inf.get("revenueGrowth3y") is not None else inf.get("revenueGrowth")
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
    # Only satellite over its share (the riskier side) is worth a line every day; more in core than the target
    # needs no action, and a daily reminder would only nudge buying single stocks
    cs = p.get("coreSatellite") or {}
    if cs.get("action") == "To core":
        items.append({"tone": -1, "ticker": None, "priority": 4, "kind": "portfolio",
                      "text": f"Satellite is {cs['satellite']:.0%} of your portfolio, above your {1 - cs['target']:.0%} limit. About "
                              f"{money(cs['newMoney'])} of new money into core would bring it back, without selling."})
    for w in p["warnings"]:
        items.append({"tone": -1, "ticker": None, "priority": 4, "kind": "portfolio", "text": w})
    return items


HEADLINE_DAYS = 3


MARKET_NEWS = "^GSPC"  # whose Yahoo feed is the general market news shown when none of yours has any
MARKET_NEWS_COUNT = 6


def recent_headlines(rows, tickers, per_ticker=2):
    """The latest headlines that name a stock or coin you hold or watch, from the past few days, with links. Funds are
    left out: their feed is general market news, and matching "Vanguard" would bring in any story about the firm.
    When none of yours has any (someone holding only funds, say), the day's market headlines take their place,
    marked as such, so the News tab isn't empty."""
    from .ai import relevant_headlines
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=HEADLINE_DAYS)

    def fresh_of(news):
        out = []
        for n in news:
            try:
                when = pd.Timestamp(n.get("time"))
                when = when.tz_localize("UTC") if when.tzinfo is None else when
            except (TypeError, ValueError):
                continue
            if when >= since:
                out.append((when, n))
        return sorted(out, key=lambda x: x[0], reverse=True)

    def item(t, when, n, text):
        url = n.get("url") or ""
        return {"tone": 0, "ticker": t, "priority": 7, "kind": "news", "text": text + (f" ({n['publisher']})" if n.get("publisher") else ""),
                "url": url if url.startswith(("https://", "http://")) else None, "time": when.isoformat()}
    items = []
    for t in tickers:
        r = rows.get(t)
        if not r or r.get("assetClass") not in ("Stocks", "Crypto"):
            continue
        for when, n in fresh_of(relevant_headlines(data.news(t), t, r.get("name")))[:per_ticker]:
            items.append(item(t, when, n, f"{t}: {n['title']}"))
    if not items:
        items = [{**item(None, when, n, f"Markets: {n['title']}"), "market": True} for when, n in fresh_of(data.news(MARKET_NEWS))[:MARKET_NEWS_COUNT]]
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
        # A thesis is about a company: for a fund or crypto the composite is only trend and calm
        if r["stance"] in ("Weak",) and t in held and r.get("assetClass") == "Stocks":
            items.append({"tone": -1, "ticker": t, "priority": 4, "text": f"{t} is a holding with a Weak composite score ({r['composite']}) - review the thesis."})
    record_scores([r for t, r in rows.items() if t in tickers])
    # A broad index fund turning "Neutral" or "Weak" in a falling market is expected, not a reason to rethink it
    for t in [x for x in tickers if x not in CORE_FUNDS]:
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
