"""Would the scores have picked winners? A check of the scores that can be rebuilt for past dates.

Momentum and risk come from prices alone, so they can be worked out exactly as they stood on any past day, with the
same code and thresholds as today (`scoring.factor_breakdown`; beta is left out, as Yahoo only has today's). Every
quarter, the followed stocks are scored as they stood that day and split into thirds, and each third's return over the
following year is measured. Value, quality and growth can't be checked this way: Yahoo keeps no past fundamentals.

Caveats the page shows: the list is today's (companies that failed or left are missing, which flatters every third
alike), returns are in each stock's own currency, and the yearly windows overlap, so neighbouring quarters aren't
independent results."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import data, db, scoring
from . import indicators as ind

STEP_MONTHS = 3       # a new set of scores every quarter
FORWARD_DAYS = 365    # each third's return over the following year
MIN_HISTORY = 252     # a year of prices before a date, as the scores need
MIN_STOCKS = 9        # three in each third, at the least
GROUPS = ("top", "middle", "bottom")
CACHE_HOURS = 12
KINDS = {"momentum": "Momentum", "risk": "Risk (calm)", "both": "Momentum and risk together"}
CAVEATS = [
    "Only momentum and risk can be checked: they come from prices, which go back years. Value, quality and growth "
    "need past company figures, which Yahoo doesn't keep.",
    "The stocks are today's list, so companies that failed or were taken over since are missing. That flatters every "
    "third alike, so compare the thirds with each other rather than reading their returns as what you'd have made.",
    "Returns are in each stock's own currency, without dividends, and the yearly windows overlap, so neighbouring "
    "quarters aren't separate results.",
    "Past patterns are no promise: a score that sorted well for five years can stop.",
]


def _price_scores(close):
    """{momentum, risk} as the scores stood at the end of `close`, by today's rules (no beta: no past figure)."""
    bd = scoring.factor_breakdown({"assetClass": "Past"}, close)  # not "Stocks": only the price-based factors
    return {k: scoring._avg(bd[k]) for k in ("momentum", "risk")}


def _dates(first, last):
    """Quarter starts from a year after the first price to a year before the last, so each has a year on each side."""
    start = (first + pd.DateOffset(days=FORWARD_DAYS)).to_period("Q").start_time
    end = last - pd.DateOffset(days=FORWARD_DAYS)
    return list(pd.date_range(start, end, freq=f"{STEP_MONTHS}MS"))


def _close_at(close, day):
    past = close[close.index <= day]
    return float(past.iloc[-1]) if not past.empty else None


MEASURES = ("ret", "vol", "dd")  # following-year return, volatility (a year) and worst fall


def _thirds(rows, key):
    """Each third by `key`, top first (ties keep the list's order): its stocks' average following-year return,
    volatility and worst fall."""
    ranked = sorted((r for r in rows if r[key] is not None), key=lambda r: -r[key])
    n = len(ranked)
    if n < MIN_STOCKS:
        return None
    cut = [0, n // 3, n - n // 3, n]  # the middle takes the remainder, so top and bottom are the same size
    out = {"n": n}
    for g, a, b in zip(GROUPS, cut, cut[1:]):
        part = ranked[a:b]
        out[g] = {m: sum(r[m] for r in part) / len(part) for m in MEASURES}
    return out


def _following(c, day, later):
    """The year after `day`: (return, volatility, worst fall), or None without prices at both ends."""
    then, after = _close_at(c, day), _close_at(c, later)
    year = c[(c.index >= day) & (c.index <= later)]
    if not then or after is None or len(year) < 100:
        return None
    vol = ind.volatility(year)
    return after / then - 1, vol, ind.max_drawdown(year)


def check(tickers, weights=None):
    """The check over `tickers`. Returns per kind of score: each date's thirds and a summary."""
    weights = weights or {k: 0.2 for k in scoring.FACTORS}
    pair = {"momentum": weights.get("momentum", 0.2), "risk": weights.get("risk", 0.2)}
    if not sum(pair.values()) > 0:
        pair = {"momentum": 0.5, "risk": 0.5}

    def load(t):
        try:
            return t, data.history(t, "5y")["Close"].dropna()
        except data.DataError:
            return t, None
    with ThreadPoolExecutor(8) as pool:
        closes = {t: c for t, c in pool.map(load, tickers) if c is not None and len(c) > MIN_HISTORY}
    if not closes:
        return {"error": "No price history to check against."}
    first = min(c.index[0] for c in closes.values())
    last = max(c.index[-1] for c in closes.values())
    per_date = {k: [] for k in KINDS}
    for day in _dates(first, last):
        later = day + pd.DateOffset(days=FORWARD_DAYS)
        rows = []
        for t, c in closes.items():
            upto = c[c.index <= day]
            if len(upto) < MIN_HISTORY or c.index[-1] < later:
                continue
            fwd = _following(c, day, later)
            if fwd is None or fwd[1] is None or fwd[2] is None:
                continue
            sc = _price_scores(upto)
            rows.append({"ticker": t, "ret": fwd[0], "vol": fwd[1], "dd": fwd[2], **sc, "both": scoring.combine(sc, pair)})
        for kind in KINDS:
            th = _thirds(rows, kind)
            if th:
                per_date[kind].append({"date": day.date().isoformat(), **th})
    out = {"kinds": {}, "stocks": len(closes), "caveats": CAVEATS, "forwardDays": FORWARD_DAYS, "stepMonths": STEP_MONTHS}
    for kind, label in KINDS.items():
        rows = per_date[kind]
        if not rows:
            out["kinds"][kind] = {"label": label, "dates": [], "summary": None}
            continue
        avg = {g: {m: sum(r[g][m] for r in rows) / len(rows) for m in MEASURES} for g in GROUPS}
        summary = {**avg, "spread": avg["top"]["ret"] - avg["bottom"]["ret"], "periods": len(rows), "from": rows[0]["date"], "to": rows[-1]["date"],
                   "topBeatBottom": sum(r["top"]["ret"] > r["bottom"]["ret"] for r in rows),
                   "topCalmer": sum(r["top"]["vol"] < r["bottom"]["vol"] for r in rows)}
        summary["verdict"] = _verdict(kind, label, summary)
        out["kinds"][kind] = {"label": label, "dates": rows, "summary": summary}
    return out


def _how(share, gap, small):
    if share >= 0.7 and gap > small:
        return "sorted well"
    if share >= 0.55 and gap > 0:
        return "sorted somewhat"
    if share <= 0.3 and gap < -small:
        return "sorted the wrong way"
    return "didn't sort reliably"


def _verdict(kind, label, s):
    """One plain sentence, with the figures it rests on. The risk score promises a calmer ride, not a higher return,
    so it's judged on how much the calm third swung the next year; the others on what they returned."""
    n, top, bottom = s["periods"], s["top"], s["bottom"]
    if kind == "risk":
        how = _how(s["topCalmer"] / n, bottom["vol"] - top["vol"], 0.03)
        return (f"{label}: {how}. The calmest third swung less than the most volatile third the following year in "
                f"{s['topCalmer']} of {n} quarters: volatility {top['vol'] * 100:.0f}% against {bottom['vol'] * 100:.0f}% a year, "
                f"worst fall {top['dd'] * 100:.0f}% against {bottom['dd'] * 100:.0f}%. It returned {top['ret'] * 100:+.1f}% against "
                f"{bottom['ret'] * 100:+.1f}%: calm isn't promised to pay more.")
    how = _how(s["topBeatBottom"] / n, s["spread"], 0.02)
    return (f"{label}: {how}. The top third beat the bottom third in {s['topBeatBottom']} of {n} quarters, "
            f"by {s['spread'] * 100:+.1f} percentage points a year on average.")


def cached_check(tickers, weights, key):
    """The check, kept CACHE_HOURS, since it reads five years of prices for every followed stock."""
    k = f"scorecheck:v1:{key}"
    hit = db.cache_get(k, CACHE_HOURS * 3600)
    if hit is not None:
        return hit
    out = check(tickers, weights)
    out["checked"] = dt.datetime.now().isoformat(timespec="minutes")
    if "error" not in out:
        db.cache_set(k, out)
    return out
