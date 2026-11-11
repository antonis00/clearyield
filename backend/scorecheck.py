"""Do the scores rank stocks by what happens next? The research in docs/score-research.md (round 2), as code.

Every month-start of the past ten years, every stock in the list is scored with today's rules
(`scoring.factor_breakdown`) fed only what was public that day: prices up to the day, and annual statements once
they'd been out 90 days. Each score is judged by its rank IC (Spearman correlation with what followed: the return, or
for risk the swings), worked out within each group of regions on each date and averaged over the months. The windows
overlap (3 months, sampled monthly), so the t-statistic scales the variance by the overlap and is judged against
Student's t with as many degrees of freedom as there are separate windows (calibrated: noise passes about 2% of the
time against 2.5% intended). Three candidate changes, fixed in advance, are decided on North America and Asia-Pacific
(after Holm's correction for three tests) and must be confirmed on Europe, the Middle East & Africa and Latin America."""
import datetime as dt
import hashlib
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import data, db, scoring
from . import indicators as ind
from .universe import country_of, region_of

log = logging.getLogger("clearyield.scorecheck")

LAG_DAYS = 90               # a fiscal year's statements count as public this long after it ends
ROBUST_LAG_DAYS = 120       # a longer lag, as a robustness check (EU and UK rules allow four months)
YEARS = 10                  # of prices, for momentum and risk
HORIZONS = (1, 3, 12)       # months ahead
PRIMARY = 3                 # the horizon decisions rest on
MIN_STOCKS = 15             # fewest stocks a date (in a group or region) needs for its IC to count
MIN_COUNTRY = 3             # fewest scored stocks a country needs on a date to count (outcomes are against its average)
COMPLETE_SHARE = 0.95       # a run decides only if every region loaded at least this share of its listed stocks
MIN_DATES = 12              # fewest dates for a verdict
MIN_DF = 4                  # fewest separate windows (degrees of freedom) for a verdict
SHARE_BAND = (2 / 3, 1.5)   # the statement's shares times the price must be this close to today's market value
MIN_HISTORY = 252           # a year of prices before a date, as the price-based scores need
ALPHA = 0.05                # two-sided: a one-sided p under 0.025 "works"
SUGGEST_P = 0.16            # one-sided p under this (about t >= 1) is "suggestive"
PATIENCE = 15 * 60          # how long a download waits while Yahoo asks for a pause, before the stock is skipped
DECIDE = ("North America", "Asia-Pacific")                         # decisions rest on these regions...
CONFIRM = ("Europe", "Middle East & Africa", "Latin America")      # ...and are confirmed on these
FACTORS = ("value", "quality", "growth", "momentum", "risk")
FUNDAMENTAL = ("value", "quality", "growth")
LABELS = {"value": "Value", "quality": "Quality", "growth": "Growth (1-year)", "momentum": "Momentum", "risk": "Risk (calm)",
          "composite": "Overall score"}
# The round 2 candidates (docs/score-research.md): (row key, label, the live score it would replace)
CANDIDATES = [("quality_gp", "Quality as gross profitability", "quality"),
              ("quality_profit", "Quality as profitability only", "quality"),
              ("momRiskAdj", "Momentum adjusted for risk", "momentum")]
GP_BAD, GP_GOOD = 0.0, 0.6  # gross profit / total assets scored 0 at 0 and 100 at 0.6
PROFIT_LABELS = ("Return on equity", "Operating margin", "Net margin")
CACHE_HOURS = 24            # an incomplete run is shown this long (evidence only, no decision); a complete one is frozen
CAVEATS = [
    "Each past date is scored with today's rules, fed only what was public that day: prices up to it, and a year's "
    "statements from 90 days after the year ended (also checked at 120 days). Beta and forward estimates have no "
    "past figure, so they're left out.",
    "Momentum and risk are tested over ten years of prices; value, quality and growth only over Yahoo's last four "
    "annual statements, on annual figures older than the live ones. Growth here is one year's; the live score uses "
    "three years where it can.",
    "The overall score counts only stocks with all five factors, so it starts once two years of statements are out.",
    "The stocks are today's list, chosen today: companies that failed or left since are missing, which weighs more "
    "over ten years than four.",
    "Returns are in US dollars with dividends, so a currency's fall isn't read as a stock signal; local-currency "
    "results are shown beside them. Past market values are the shares in that year's statement times that day's price.",
    "Decisions use North America and Asia-Pacific; Europe, the Middle East & Africa and Latin America confirm. Both "
    "cover the same years, so a worldwide turn touches both: each score's own months are also split in halves. The "
    "quality candidates rest on 2023-26 everywhere, so they're confirmed across stocks, not across time.",
    "Each stock's outcome is measured against the average of its country that month, so a country's market or "
    "currency doing well isn't read as the score picking well. Countries with fewer than three scored stocks that "
    "month are left out.",
    "Past patterns are no promise: a score that ranked well for some years can stop.",
]


# ---------------------------------------------------------------- statistics
def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny, qab, qap, qam = 1e-300, a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + aa * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + aa / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1) < 1e-14:
            break
    return h


def _betainc(a, b, x):
    """The regularised incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return front * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - front * _betacf(b, a, 1 - x) / b


def p_one_sided(t, df):
    """P(T >= t) for Student's t with `df` degrees of freedom."""
    if t is None or df < 1:
        return None
    tail = 0.5 * _betainc(df / 2, 0.5, df / (df + t * t))
    return tail if t > 0 else 1 - tail


def rank_ic(x, y):
    """Spearman rank correlation of two equal-length sequences (ties share their average rank); None if either is flat."""
    a, b = pd.Series(x, dtype=float).rank(), pd.Series(y, dtype=float).rank()
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def overlap_t(values, h):
    """(mean, t, degrees of freedom) for a series of outcomes over `h` months sampled monthly. Neighbours share h - 1
    months, so the variance of the mean is scaled by h (exact when the months are independent), and the t is judged
    with as many degrees of freedom as there are separate windows. t is None without enough values or spread."""
    x = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    n = len(x)
    df = max(n // h - 1, 0)
    if n < 3 or df < 1:
        return (float(x.mean()) if n else None), None, df
    m = x.mean()
    if not np.any(np.abs(x - m) > 1e-12):  # every value the same (up to rounding): no spread, so no t
        return float(m), None, df
    return float(m), float(m / math.sqrt(h * x.var(ddof=1) / n)), df


def verdict(p, n, df=MIN_DF):
    """Plain words for a one-sided p-value (small: the score ranks the right way). Too few months, or too few separate
    windows among them, give no verdict."""
    if p is None or n < MIN_DATES or df < MIN_DF:
        return "not enough data"
    if p < ALPHA / 2:
        return "works"
    if p > 1 - ALPHA / 2:
        return "works backwards"
    if p < SUGGEST_P:
        return "suggestive"
    return "no evidence"


def holm(pvalues, alpha=ALPHA / 2):
    """Which one-sided p-values pass at `alpha` overall when several are tested (Holm's step-down method)."""
    order = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    passed, m = [False] * len(pvalues), len(order)
    for rank, (p, i) in enumerate(order):
        if p > alpha / (m - rank):
            break
        passed[i] = True
    return passed


# ---------------------------------------------------------------- point in time
def published(stmts, day, lag=LAG_DAYS):
    """(latest, the year before) of the statements public on `day`, either None. The year before must really be the
    year before (300 to 430 days earlier), or it's left out."""
    out = [s for s in stmts if dt.date.fromisoformat(s["end"]) + dt.timedelta(days=lag) <= day]
    last = out[-1] if out else None
    prev = out[-2] if len(out) > 1 else None
    if prev and not 300 <= (dt.date.fromisoformat(last["end"]) - dt.date.fromisoformat(prev["end"])).days <= 430:
        prev = None
    return last, prev


def info_at(base, stmts, day, price, fx, lag=LAG_DAYS):
    """The record `scoring.factor_breakdown` reads, as it would have stood on `day`. `base` is today's record for what
    doesn't date (asset class, sector, industry, currencies); `price` that day's quoted close in the quote currency's
    main unit (split-adjusted, dividends not taken out); `fx` quote units per statement unit that day. Market value is
    the shares in the published statement times that price. Quality and growth need neither price nor rate; value
    needs both. Figures with no past version are left out."""
    out = {k: base.get(k) for k in ("assetClass", "sector", "industry", "currency", "financialCurrency")}
    last, prev = published(stmts, day, lag)
    if not last:
        return out  # nothing public yet: only momentum and risk score
    g = last.get

    def ratio(a, b):
        return a / b if a is not None and b else None
    ni, eq, rev = g("netIncome"), g("equity"), g("revenue")
    out.update({"profitMargins": ratio(ni, rev), "returnOnEquity": ratio(ni, eq) if eq and eq > 0 else None,
                "operatingMargins": ratio(g("operatingIncome"), rev), "debtToEquity": ratio(g("totalDebt"), eq) if eq and eq > 0 else None,
                "currentRatio": ratio(g("currentAssets"), g("currentLiabilities"))})
    if prev:
        if rev and prev.get("revenue") and prev["revenue"] > 0:
            out["revenueGrowth"] = rev / prev["revenue"] - 1
        if ni is not None and prev.get("netIncome") and prev["netIncome"] > 0:
            out["earningsGrowth"] = ni / prev["netIncome"] - 1
    mcap = price * g("shares") if price and g("shares") else None
    if mcap and fx:
        debt, cash, ebitda = g("totalDebt"), g("cash"), g("ebitda")
        out.update({"marketCap": mcap, "statementFx": fx, "freeCashflow": g("fcf"),
                    "trailingPE": mcap / (ni * fx) if ni and ni > 0 else None,
                    "priceToBook": mcap / (eq * fx) if eq and eq > 0 else None,
                    "enterpriseToEbitda": (mcap + (debt - cash) * fx) / (ebitda * fx) if debt is not None and cash is not None and ebitda and ebitda > 0 else None})
    return out


def gross_profitability(base, stmts, day, lag=LAG_DAYS):
    """Candidate 1: gross profit / total assets from the statement public that day, scored 0-100 (GP_BAD to GP_GOOD).
    None for financial companies (banks, insurers, asset managers: Novy-Marx leaves them out, and their gross profit and
    assets don't compare) or without the figures. Adopting it would take banks' quality score away."""
    if base.get("sector") == "Financial Services" or "bank" in (base.get("industry") or "").lower():
        return None
    last, _ = published(stmts, day, lag)
    if not last or last.get("grossProfit") is None or not last.get("totalAssets") or last["totalAssets"] <= 0:
        return None
    return scoring.lin(last["grossProfit"] / last["totalAssets"], GP_BAD, GP_GOOD)


def profitability_only(bd):
    """Candidate 2: today's quality metrics without debt / equity and the current ratio, averaged as now."""
    parts = [p["score"] for p in bd["quality"] if p["score"] is not None and p["label"] in PROFIT_LABELS]
    return sum(parts) / len(parts) if parts else None


# ---------------------------------------------------------------- the panel
def _month_starts(first, last):
    return list(pd.date_range(first.to_period("M").start_time, last, freq="MS"))


def _shares_fit(t, stmts, price_now, mcap_now):
    """Whether the statements' share counts can stand for this listing's market value in every year: the latest times
    today's price within SHARE_BAND of today's market value (an ADR or another share class fails), and no year more
    than 1.5 times or under 2/3 of the year before. Yahoo restates past counts for splits (checked on NVDA's 2024
    split); a year it didn't shows as such a jump, and the stock then gets no value score rather than a wrong one."""
    counts = [(s["end"], s.get("shares")) for s in stmts]
    if not counts or not counts[-1][1] or not mcap_now or not price_now:
        return False
    if not SHARE_BAND[0] <= counts[-1][1] * price_now / mcap_now <= SHARE_BAND[1]:
        return False
    for (_, a), (_, b) in zip(counts, counts[1:]):
        # A jump this size is a split Yahoo didn't restate (old units, which today's split-adjusted prices don't fit)
        # or a large issue or cancellation: either way that year's market value can't be trusted
        if not a or not b or not SHARE_BAND[0] <= b / a <= SHARE_BAND[1]:
            return False
    return True


def _wait_for_yahoo():
    """While Yahoo asks for a pause, wait (up to PATIENCE in all); False if the pause outlasts it."""
    waited = 0.0
    while data.rate_limited_for() > 0:
        if waited >= PATIENCE:
            return False
        pause = min(30.0, data.rate_limited_for())
        time.sleep(pause)
        waited += pause
    return True


def _load(t):
    """Everything one stock needs, or why not: "skipped" (Yahoo's pause outlasted PATIENCE), "short" (under a year of
    prices: a recent listing, left out by design) or "failed" (no data, no rate, an error), each counted. Ten years of
    adjusted closes (local and in dollars), quoted closes in the main unit, today's record and statements. A stock
    whose statement shares don't fit its listing keeps its other scores but gets no value score."""
    try:
        if not _wait_for_yahoo():
            return "skipped"
        h = data.long_closes(t).dropna()
        h = h[h.index >= h.index[-1] - pd.DateOffset(years=YEARS)]
        inf = data.info(t)
        if inf.get("assetClass") != "Stocks":
            return "failed"
        if len(h) < MIN_HISTORY + 21:
            return "short"
        rate = data.fx_series(inf.get("currency"), "USD", h.index)
        if rate is None:
            return "failed"
        stmts = data.statements(t)
        divisor = data.MINOR_UNITS.get(inf.get("currency"), (None, 1.0))[1]
        quoted = data.plain_closes(t) / divisor if stmts else None
        if quoted is not None and not _shares_fit(t, stmts, float(quoted.iloc[-1]), inf.get("marketCap")):
            quoted = None  # the statement's shares aren't this listing's, or a year's count can't be trusted
        return {"ticker": t, "close": h, "usd": (h * rate).dropna(), "quoted": quoted, "info": inf, "stmts": stmts}
    except data.DataError:
        return "failed"
    except Exception as e:  # one stock's surprise mustn't stop the whole research
        log.warning("score research couldn't load %s: %s", t, e)
        return "failed"


def _forward(series, d, end):
    """The return over [d, end] from the close the score used, or None when the window lacks prices at its end."""
    start = series[series.index <= d]
    window = series[(series.index > d) & (series.index <= end)]
    if start.empty or window.empty or window.index[-1] < end - pd.Timedelta(days=7):
        return None, None
    path = pd.concat([start.iloc[-1:], window])
    return float(path.iloc[-1] / path.iloc[0] - 1), float(path.pct_change().std() * math.sqrt(252))


PROGRESS = {"running": False, "done": 0, "total": 0, "stage": "", "started": None, "finished": 0.0, "error": None}
RETRY_AFTER = 10 * 60  # after a failed run, wait this long before the page starts another


def panel(tickers):
    """One row per (date, stock): the five factor scores and the composite as today's rules would have given them
    (fundamentals also at the longer lag), the candidates, the region, and what followed over each horizon."""
    loaded, counts = [], {}
    PROGRESS.update({"done": 0, "total": len(tickers), "stage": "downloading"})
    with ThreadPoolExecutor(3) as pool:
        for t, got in zip(tickers, pool.map(_load, tickers)):
            PROGRESS["done"] += 1
            c = counts.setdefault(region_of(t), {"listed": 0, "loaded": 0, "short": 0, "failed": 0, "skipped": 0})
            c["listed"] += 1
            if isinstance(got, dict):
                loaded.append(got)
                c["loaded"] += 1
            else:
                c[got] += 1  # every stock that didn't load is counted, by reason
    if not loaded:
        return [], 0, counts
    PROGRESS.update({"done": 0, "total": len(loaded), "stage": "scoring"})
    last = max(s["close"].index[-1] for s in loaded)
    first = min(s["close"].index[0] for s in loaded)
    dates = [d for d in _month_starts(first + pd.DateOffset(days=365), last) if d + pd.DateOffset(months=1) <= last]
    rows = []
    for s in loaded:
        PROGRESS["done"] += 1
        close, usd, quoted, inf, stmts = s["close"], s["usd"], s["quoted"], s["info"], s["stmts"]
        quote_main = data.main_unit(inf.get("currency")) or inf.get("currency")
        for d in dates:
            upto = close[close.index <= d]
            if len(upto) < MIN_HISTORY:
                continue
            day = d.date()
            has_stmts = bool(stmts) and published(stmts, day, LAG_DAYS)[0] is not None
            price, fx = None, None
            if has_stmts and quoted is not None:
                q = quoted[quoted.index <= d]
                price = float(q.iloc[-1]) if not q.empty else None
                try:
                    fx = data.fx_on(inf.get("financialCurrency") or quote_main, quote_main, day.isoformat())
                except data.DataError:
                    fx = None
            row = {"date": day.isoformat(), "ticker": s["ticker"], "region": inf.get("region") or "Unknown",
                   "country": country_of(s["ticker"]) or inf.get("country") or "Unknown"}  # where it's listed: its currency
            bd = scoring.factor_breakdown(info_at(inf, stmts, day, price, fx), upto)
            scores = {f: scoring._avg(bd[f]) for f in FACTORS}
            row.update(scores)
            # The overall score only where all five factors are there, so it means the same thing on every date
            row["composite"] = scoring.combine(scores) if all(v is not None for v in scores.values()) else None
            if has_stmts:  # fundamentals again with a longer publication lag, as a check
                late = scoring.factor_breakdown(info_at(inf, stmts, day, price, fx, ROBUST_LAG_DAYS), upto)
                row.update({f + "_lag120": scoring._avg(late[f]) for f in FUNDAMENTAL})
                row["quality_gp"] = gross_profitability(inf, stmts, day)
                row["quality_gp_lag120"] = gross_profitability(inf, stmts, day, ROBUST_LAG_DAYS)
                row["quality_profit"] = profitability_only(bd)
            r12, r1, vol = ind.ret_over(upto, 252), ind.ret_over(upto, 21), ind.volatility(upto)
            row["momRiskAdj"] = ((1 + r12) / (1 + r1) - 1) / vol if r12 is not None and r1 is not None and vol else None
            for h in HORIZONS:
                end = d + pd.DateOffset(months=h)
                if end > last:
                    continue
                ret, vol_after = _forward(usd, d, end)
                local, _ = _forward(close, d, end)
                if ret is not None:
                    row[f"ret{h}"], row[f"vol{h}"] = ret, vol_after
                if local is not None:
                    row[f"local{h}"] = local
            rows.append(row)
    return rows, len(loaded), counts


# ---------------------------------------------------------------- judging
def _outcome(factor, h, on="return"):
    """The row key and sign of a factor's outcome. Risk promises a calmer ride: its outcome is the swings, sign
    reversed, so a positive IC is good for every factor."""
    if factor.startswith("risk"):
        return f"vol{h}", -1
    return (f"local{h}" if on == "local" else f"ret{h}"), 1


def _pairs(rows, factor, h, on="return"):
    """(score, outcome, ticker) for one date, the outcome measured against the average of the stock's country among
    those scored, so a country's market or currency doing well isn't read as the score picking well. Countries with
    fewer than MIN_COUNTRY scored stocks are left out: they say nothing about picking within a country."""
    key, sign = _outcome(factor, h, on)
    scored = [r for r in rows if r.get(factor) is not None and r.get(key) is not None]
    by_country = {}
    for r in scored:
        by_country.setdefault(r.get("country", "Unknown"), []).append(r)
    out = []
    for members in by_country.values():
        if len(members) < MIN_COUNTRY:
            continue
        avg = sum(sign * m[key] for m in members) / len(members)
        out += [(m[factor], sign * m[key] - avg, m["ticker"]) for m in members]
    return out


def _spread(pairs, date=""):
    """The top third's average outcome minus the bottom third's. Ties in the score (scores clip at 0 and 100) are
    broken by a fixed shuffle of the tickers, not by their order in the list."""
    tie = lambda t: hashlib.md5(f"{date}{t}".encode()).hexdigest()  # noqa: E731
    pairs = sorted(pairs, key=lambda p: (-p[0], tie(p[2])))
    n = len(pairs) // 3
    if n < 3:
        return None
    return sum(o for _, o, _ in pairs[:n]) / n - sum(o for _, o, _ in pairs[-n:]) / n


def daily_stats(by_date, factor, h, on="return"):
    """Per date with at least MIN_STOCKS scored stocks: (date, IC, spread, stocks)."""
    out = []
    for d, rows in by_date.items():
        pairs = _pairs(rows, factor, h, on)
        if len(pairs) >= MIN_STOCKS:
            ic = rank_ic([p[0] for p in pairs], [p[1] for p in pairs])
            if ic is not None:
                out.append((d, ic, _spread(pairs, d), len(pairs)))
    return sorted(out)


def summarise(series, h):
    if not series:
        return {"n": 0, "meanIC": None, "t": None, "p": None, "df": 0, "positive": None, "spread": None, "stocks": 0, "verdict": "not enough data"}
    mean, t, df = overlap_t([s[1] for s in series], h)
    p = p_one_sided(t, df)
    spreads = [s[2] for s in series if s[2] is not None]
    return {"n": len(series), "meanIC": mean, "t": t, "p": p, "df": df, "positive": sum(s[1] > 0 for s in series) / len(series),
            "spread": sum(spreads) / len(spreads) if spreads else None, "stocks": round(sum(s[3] for s in series) / len(series)),
            "verdict": verdict(p, len(series), df)}


def split(dates, h):
    """Discovery (the first half of the dates) and hold-out (the second), with the last `h - 1` discovery dates left
    out, so no following window spans both halves."""
    dates = sorted(dates)
    half = len(dates) // 2
    return dates[:max(0, half - (h - 1))], dates[half:]


def compare(by_date, candidate, today, h):
    """The candidate's IC minus today's, date by date, on the same stocks (rows where both scored): {n, meanDiff, t, p}."""
    same = {d: [r for r in rows if r.get(candidate) is not None and r.get(today) is not None] for d, rows in by_date.items()}
    a = {d: ic for d, ic, *_ in daily_stats(same, candidate, h)}
    b = {d: ic for d, ic, *_ in daily_stats(same, today, h)}
    diffs = [a[d] - b[d] for d in sorted(set(a) & set(b))]
    mean, t, df = overlap_t(diffs, h)
    return {"n": len(diffs), "meanDiff": mean, "t": t, "p": p_one_sided(t, df) if len(diffs) >= MIN_DATES and df >= MIN_DF else None}


def _group(rows, regions=None):
    """Rows by date, for the given regions (all when None)."""
    out = {}
    for r in rows:
        if regions is None or r["region"] in regions:
            out.setdefault(r["date"], []).append(r)
    return out


def judge(rows, h=PRIMARY):
    """The research on a panel of rows (see `panel`): each score's evidence, worldwide, by group of regions and by
    region, over time halves and robustness checks; and the three candidates against the adoption rule."""
    everywhere, decide, confirm = _group(rows), _group(rows, DECIDE), _group(rows, CONFIRM)
    by_region = {reg: _group(rows, (reg,)) for reg in (*DECIDE, *CONFIRM)}
    only = lambda by, ds: {d: by[d] for d in ds if d in by}  # noqa: E731
    factors = {}
    for f in [*FACTORS, "composite"]:
        # The halves split this score's own months (company figures start in 2023, prices in 2017)
        disc, hold = split([d for d, *_ in daily_stats(everywhere, f, h)], h)
        factors[f] = {"label": LABELS[f], "horizons": {str(k): summarise(daily_stats(everywhere, f, k), k) for k in HORIZONS},
                      "decide": summarise(daily_stats(decide, f, h), h), "confirm": summarise(daily_stats(confirm, f, h), h),
                      "regions": {reg: summarise(daily_stats(by, f, h), h) for reg, by in by_region.items()},
                      "discovery": summarise(daily_stats(only(everywhere, disc), f, h), h), "holdout": summarise(daily_stats(only(everywhere, hold), f, h), h),
                      "local": summarise(daily_stats(everywhere, f, h, "local"), h),
                      "byDate": [{"date": d, "ic": ic} for d, ic, *_ in daily_stats(everywhere, f, h)]}
        factors[f]["halves"] = [disc[0], disc[-1], hold[0], hold[-1]] if disc and hold else None
        if f in FUNDAMENTAL:
            factors[f]["lag120"] = summarise(daily_stats(everywhere, f + "_lag120", h), h)
        factors[f]["verdict"] = factors[f]["horizons"][str(h)]["verdict"]

    candidates = []
    for key, label, today in CANDIDATES:
        candidates.append({"key": key, "label": label, "against": LABELS[today],
                           "own": summarise(daily_stats(everywhere, key, h), h), "ownConfirm": summarise(daily_stats(confirm, key, h), h),
                           "decide": compare(decide, key, today, h), "confirm": compare(confirm, key, today, h),
                           "regions": {reg: compare(by_region[reg], key, today, h) for reg in CONFIRM},
                           **({"lag120": summarise(daily_stats(everywhere, key + "_lag120", h), h)} if key == "quality_gp" else {})})
    holm_ok = holm([c["decide"]["p"] if c["decide"]["p"] is not None else 1.0 for c in candidates])  # a missing p counts as 1: still three tests
    names = {"decide": " and ".join(DECIDE), "confirm": ", ".join(CONFIRM[:-1]) + " and " + CONFIRM[-1]}
    for c, passed in zip(candidates, holm_ok):
        gain_d, gain_c = c["decide"]["meanDiff"], c["confirm"]["meanDiff"]
        counted = {reg: cmp for reg, cmp in c["regions"].items() if cmp["n"] >= MIN_DATES}
        negative = [reg for reg, cmp in counted.items() if (cmp["meanDiff"] or 0) <= 0]
        works_c = c["ownConfirm"]["verdict"] == "works"
        conf_ok = c["confirm"]["p"] is not None and c["confirm"]["p"] < ALPHA
        c["holm"] = passed
        c["countedRegions"] = sorted(counted)
        c["adopted"] = passed and works_c and conf_ok and not negative
        # The first rule it failed, in plain words
        c["why"] = ("Beats today's score on " + names["decide"] + " after the correction for three tests, works on its own on "
                    + names["confirm"] + " and beats today's score there too, in every region." if c["adopted"]
                    else f"On {names['decide']} it ranks no better than today's score." if gain_d is None or gain_d <= 0
                    else f"On {names['decide']} it ranks a little better than today's score (IC {gain_d:+.3f}), but not by enough to pass the correction for three tests." if not passed
                    else f"On {names['confirm']} it doesn't rank the right way on its own ({c['ownConfirm']['verdict']})." if not works_c
                    else f"Its gain over today's score isn't confirmed on {names['confirm']} (IC {gain_c:+.3f})." if not conf_ok
                    else f"Its gain over today's score is negative in {', '.join(negative)}.")
        if counted != c["regions"]:  # say which confirming regions the "every region" rule rested on
            c["why"] += f" Regions with enough months to count: {', '.join(sorted(counted)) or 'none'}."
    return {"factors": factors, "candidates": candidates, "dates": len(everywhere), "from": min(everywhere), "to": max(everywhere),
            "primaryHorizon": h, "decideRegions": list(DECIDE), "confirmRegions": list(CONFIRM), "caveats": CAVEATS}


def check(tickers):
    """The whole research over `tickers` (see docs/score-research.md)."""
    rows, loaded, counts = panel(tickers)
    skipped = sum(c["skipped"] for c in counts.values())
    if not rows:
        return {"error": "No price history to check against." + (f" {skipped} stocks were skipped while Yahoo asked for a pause: try again later." if skipped else "")}
    PROGRESS["stage"] = "judging"
    out = judge(rows)
    # A run missing part of the list decides on a different sample than the one designed: evidence, no decision
    short_of = [r for r, c in counts.items() if c["skipped"] or c["loaded"] < COMPLETE_SHARE * (c["listed"] - c["short"])]
    out["complete"] = not short_of
    if short_of:
        for c in out["candidates"]:
            c["adopted"] = False
            c["why"] = f"No decision: the run was incomplete in {', '.join(short_of)} (see the counts). " + c["why"]
    latest = [r for r in rows if r["date"] == out["to"]]
    per_region = {}
    for r in latest:
        per_region[r["region"]] = per_region.get(r["region"], 0) + 1
    out.update({"stocks": loaded, "skipped": skipped, "counts": counts, "withStatements": sum(r.get("quality") is not None for r in latest),
                "withValue": sum(r.get("value") is not None for r in latest), "stocksByRegion": per_region})
    return out


# ---------------------------------------------------------------- run in the background
_lock = threading.Lock()


def frozen_path():
    """Where the complete run the decision rests on is kept, for good (beside the database, not in the cache, which
    forgets entries after 90 days)."""
    return db.DB_PATH.parent / "research" / "score-round2.json"


def frozen():
    try:
        return json.loads(frozen_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cached_or_start(tickers, key):
    """The decision run if there is one (complete runs are frozen: the decision is made once, not redrawn every day
    until something passes); else an incomplete run from the last CACHE_HOURS (evidence, no decision); else starts the
    research in the background (one at a time) and says how far it's got. A first run downloads ten years of prices
    and four years of statements for every stock, which takes a while and waits whenever Yahoo asks for a pause."""
    k = f"scorecheck:r2b:{key}"  # r2b: after the review's fixes (an earlier run under r2 used the method before them)
    with _lock:  # checked inside the lock, so a request arriving as a build finishes doesn't start another
        done = frozen() or db.cache_get(k, CACHE_HOURS * 3600)
        if done is not None:
            return done
        failed_lately = PROGRESS["error"] and time.time() - PROGRESS["finished"] < RETRY_AFTER
        if failed_lately and not PROGRESS["running"]:
            return {"building": False, "error": PROGRESS["error"] + " It will be tried again in a few minutes."}
        if not PROGRESS["running"]:
            PROGRESS.update({"running": True, "error": None, "started": time.time(), "done": 0, "total": len(tickers), "stage": "starting"})
            threading.Thread(target=_build, args=(tickers, k), daemon=True).start()
    return {"building": True, **{x: PROGRESS[x] for x in ("done", "total", "stage")},
            "elapsed": round(time.time() - (PROGRESS["started"] or time.time()))}


def _build(tickers, k):
    try:
        out = check(tickers)
        out["checked"] = dt.datetime.now().isoformat(timespec="minutes")
        if "error" in out:
            PROGRESS["error"] = out["error"]
        elif out.get("complete"):
            path = frozen_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**out, "frozen": True}, default=str), encoding="utf-8")
            os.replace(tmp, path)
        else:
            db.cache_set(k, out)
    except Exception as e:  # the page says so, and the next visit after RETRY_AFTER tries again
        log.exception("score research failed")
        PROGRESS["error"] = f"The research stopped: {e}"
    finally:
        PROGRESS.update({"running": False, "finished": time.time()})
