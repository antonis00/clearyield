"""Do the scores rank stocks by what happens next? The research in docs/score-research.md, as code.

Every month-start of the past years, each followed stock is scored with today's rules (`scoring.factor_breakdown`)
fed only what was public that day: prices up to the day, and annual statements once they'd been out 90 days. Each
score is judged by its rank IC (Spearman correlation with what followed: the return, or for risk the swings),
averaged over the months. The following windows overlap (3 months, sampled monthly), so the t-statistic scales the
variance by the overlap and is judged against Student's t with as many degrees of freedom as there are separate
windows: measured on made-up noise, that passes t >= 2 about as often as it should, where Newey-West didn't with
this few months. Candidate changes, fixed in advance, must work on their own, beat today's score after a Holm
correction for testing several, and beat it again on a hold-out half of the months nobody looked at first."""
import datetime as dt
import hashlib
import logging
import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import data, db, scoring
from . import indicators as ind

log = logging.getLogger("clearyield.scorecheck")

LAG_DAYS = 90               # a fiscal year's statements count as public this long after it ends
ROBUST_LAG_DAYS = 120       # a longer lag, as a robustness check (EU and UK rules allow four months)
HORIZONS = (1, 3, 12)       # months ahead
PRIMARY = 3                 # the horizon decisions rest on
MIN_STOCKS = 15             # fewest stocks a date needs for its IC to count
MIN_DATES = 12              # fewest dates a half (or a whole) needs for a verdict
MIN_DF = 4                  # fewest separate windows (degrees of freedom) for a verdict
SHARE_BAND = (2 / 3, 1.5)   # the statement's shares times the price must be this close to today's market value
MIN_PEERS = 8               # fewest other stocks in a sector for a within-sector rank; fewer, the whole list
MIN_HISTORY = 252           # a year of prices before a date, as the price-based scores need
ALPHA = 0.05                # two-sided: a one-sided p under 0.025 "works"
SUGGEST_P = 0.16            # one-sided p under this (about t >= 1) is "suggestive"
FACTORS = ("value", "quality", "growth", "momentum", "risk")
FUNDAMENTAL = ("value", "quality", "growth")
LABELS = {"value": "Value", "quality": "Quality", "growth": "Growth (1-year)", "momentum": "Momentum", "risk": "Risk (calm)",
          "composite": "Overall score"}
CACHE_HOURS = 12
CAVEATS = [
    "Each past date is scored with today's rules, fed only what was public that day: prices up to it, and a year's "
    "statements from 90 days after the year ended (also checked at 120 days). Beta and forward estimates have no "
    "past figure, so they're left out.",
    "Value, quality and growth rest on Yahoo's last four annual statements: tested over a shorter span than momentum "
    "and risk, on annual figures that are older than the live ones. Growth here is one year's; the live score uses "
    "three years where it can, which four statements can't test.",
    "The overall score counts only stocks with all five factors, so it starts once two years of statements are out.",
    "The stocks are today's list, chosen today: companies that failed or left since are missing.",
    "Returns are in US dollars with dividends, so a currency's fall isn't read as a stock signal; local-currency "
    "results are shown beside them. Past market values are the shares in that year's statement times that day's price.",
    "Past patterns are no promise: a score that ranked well for a few years can stop.",
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
    windows among them (12-month windows over four years make one or two), give no verdict."""
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


def _parts(bd):
    """Every metric that scored, as (factor, label, value, bad, good): the raw material for within-sector ranks."""
    return [(f, p["label"], p["value"], p["bad"], p["good"]) for f, ps in bd.items() for p in ps if p["score"] is not None]


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


def _load(t):
    """Everything one stock needs, or None: closes (adjusted, local and in dollars), quoted closes in the main unit,
    today's record and statements. A stock whose statement shares don't match its listing (an ADR or a second
    listing) keeps its price-based scores but gets no value score."""
    try:
        h = data.history(t, "5y")["Close"].dropna()
        inf = data.info(t)
        if inf.get("assetClass") != "Stocks" or len(h) < MIN_HISTORY + 21:
            return None
        rate = data.fx_series(inf.get("currency"), "USD", h.index)
        if rate is None:
            return None
        stmts = data.statements(t)
        divisor = data.MINOR_UNITS.get(inf.get("currency"), (None, 1.0))[1]
        quoted = data.plain_closes(t) / divisor if stmts else None
        if quoted is not None and not _shares_fit(t, stmts, float(quoted.iloc[-1]), inf.get("marketCap")):
            quoted = None  # the statement's shares aren't this listing's, or a year's count can't be trusted
        return {"ticker": t, "close": h, "usd": (h * rate).dropna(), "quoted": quoted, "info": inf, "stmts": stmts}
    except data.DataError:
        return None
    except Exception as e:  # one stock's surprise mustn't stop the whole research
        log.warning("score research skipped %s: %s", t, e)
        return None


def _forward(series, d, end):
    """The return over [d, end] from the close the score used, or None when the window lacks prices at its end."""
    start = series[series.index <= d]
    window = series[(series.index > d) & (series.index <= end)]
    if start.empty or window.empty or window.index[-1] < end - pd.Timedelta(days=7):
        return None, None
    path = pd.concat([start.iloc[-1:], window])
    return float(path.iloc[-1] / path.iloc[0] - 1), float(path.pct_change().std() * math.sqrt(252))


def panel(tickers, patience=120):
    """One row per (date, stock): the five factor scores and the composite as today's rules would have given them
    (fundamentals also at the longer lag), the candidates' inputs, and what followed over each horizon. Downloads stop
    while Yahoo asks for a long pause."""
    loaded, skipped = [], 0
    with ThreadPoolExecutor(4) as pool:
        for got in pool.map(lambda t: _load(t) if data.rate_limited_for() < patience else "skipped", tickers):
            if got == "skipped":
                skipped += 1  # Yahoo asked for a long pause: said in the results, not dropped silently
            elif got:
                loaded.append(got)
    if not loaded:
        return [], 0, skipped
    last = max(s["close"].index[-1] for s in loaded)
    first = min(s["close"].index[0] for s in loaded)
    dates = [d for d in _month_starts(first + pd.DateOffset(days=365), last) if d + pd.DateOffset(months=1) <= last]
    rows = []
    for s in loaded:
        close, usd, quoted, inf, stmts = s["close"], s["usd"], s["quoted"], s["info"], s["stmts"]
        quote_main = data.main_unit(inf.get("currency")) or inf.get("currency")
        for d in dates:
            upto = close[close.index <= d]
            if len(upto) < MIN_HISTORY:
                continue
            price, fx = None, None
            if stmts and quoted is not None:
                q = quoted[quoted.index <= d]
                price = float(q.iloc[-1]) if not q.empty else None
                try:
                    fx = data.fx_on(inf.get("financialCurrency") or quote_main, quote_main, d.date().isoformat())
                except data.DataError:
                    fx = None
            row = {"date": d.date().isoformat(), "ticker": s["ticker"], "sector": inf.get("sector") or "Unknown"}
            for lag, suffix in ((LAG_DAYS, ""), (ROBUST_LAG_DAYS, "_lag120")):
                bd = scoring.factor_breakdown(info_at(inf, stmts, d.date(), price, fx, lag), upto)
                scores = {f: scoring._avg(bd[f]) for f in FACTORS}
                if not suffix:
                    row.update(scores)
                    row["parts"] = _parts(bd)
                    # The overall score only where all five factors are there, so it means the same thing on every date
                    row["composite"] = scoring.combine(scores) if all(v is not None for v in scores.values()) else None
                else:
                    row.update({f + suffix: scores[f] for f in FUNDAMENTAL})
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
    return rows, len(loaded), skipped


# ---------------------------------------------------------------- candidate: within-sector ranks
def sector_rank_scores(rows):
    """Each row's factors scored by within-sector percentiles of the same metrics today's rules use (the whole list
    where a sector has fewer than MIN_PEERS other stocks with the metric), averaged into factors exactly as now. Added
    to each row as `<factor>_sector`, and `composite_sector` where all five are there. `rows` must be one date's."""
    by_metric = {}
    for i, r in enumerate(rows):
        for f, label, value, bad, good in r["parts"]:
            by_metric.setdefault((f, label), []).append((i, r["sector"], value, good > bad))
    per_row = [{f: [] for f in FACTORS} for _ in rows]
    for (f, _), items in by_metric.items():
        for i, sector, value, higher_better in items:
            if value is None:  # a loss where P/E would be: the worst, as today's rule scores it 0
                per_row[i][f].append(0.0)
                continue
            peers = [v for _, s, v, _ in items if s == sector and v is not None]
            pool = peers if len(peers) - 1 >= MIN_PEERS else [v for _, _, v, _ in items if v is not None]
            below = sum(v < value for v in pool) + 0.5 * (sum(v == value for v in pool) - 1)
            pct = below / (len(pool) - 1) if len(pool) > 1 else 0.5
            per_row[i][f].append(100 * (pct if higher_better else 1 - pct))
    for r, scores in zip(rows, per_row):
        fs = {f: (sum(v) / len(v) if v else None) for f, v in scores.items()}
        r.update({f"{f}_sector": s for f, s in fs.items()})
        r["composite_sector"] = scoring.combine(fs) if all(v is not None for v in fs.values()) else None


# ---------------------------------------------------------------- judging
def _outcome(factor, h, on="return"):
    """The row key and sign of a factor's outcome. Risk promises a calmer ride: its outcome is the swings, sign
    reversed, so a positive IC is good for every factor, unless `on` asks for the return."""
    if factor.startswith("risk") and on != "return-for-all":
        return f"vol{h}", -1
    return (f"local{h}" if on == "local" else f"ret{h}"), 1


def _pairs(rows, factor, h, on="return"):
    key, sign = _outcome(factor, h, on)
    return [(r[factor], sign * r[key], r["ticker"]) for r in rows if r.get(factor) is not None and r.get(key) is not None]


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


def compare(by_date, candidate, today, h, dates):
    """The candidate's IC minus today's, date by date, over `dates` where both have one: {n, meanDiff, t, p}."""
    sub = {d: by_date[d] for d in dates}
    a = {d: ic for d, ic, *_ in daily_stats(sub, candidate, h)}
    b = {d: ic for d, ic, *_ in daily_stats(sub, today, h)}
    diffs = [a[d] - b[d] for d in sorted(set(a) & set(b))]
    mean, t, df = overlap_t(diffs, h)
    return {"n": len(diffs), "meanDiff": mean, "t": t, "p": p_one_sided(t, df) if len(diffs) >= MIN_DATES else None}


def judge(rows, h=PRIMARY):
    """The research on a panel of rows (see `panel`): each score's evidence and the candidates' results."""
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    for d_rows in by_date.values():
        sector_rank_scores(d_rows)
    keys = [*FACTORS, "composite"]
    primary_dates = sorted(d for d, rs in by_date.items() if any(r.get(f"ret{h}") is not None for r in rs))
    disc, hold = split(primary_dates, h)
    on = lambda ds: {d: by_date[d] for d in ds}  # noqa: E731
    factors = {}
    for f in keys:
        factors[f] = {"label": LABELS[f], "horizons": {str(k): summarise(daily_stats(by_date, f, k), k) for k in HORIZONS},
                      "discovery": summarise(daily_stats(on(disc), f, h), h), "holdout": summarise(daily_stats(on(hold), f, h), h),
                      "local": summarise(daily_stats(by_date, f, h, "local"), h),
                      "byDate": [{"date": d, "ic": ic} for d, ic, *_ in daily_stats(by_date, f, h)]}
        if f in FUNDAMENTAL:
            factors[f]["lag120"] = summarise(daily_stats(by_date, f + "_lag120", h), h)
        factors[f]["verdict"] = factors[f]["horizons"][str(h)]["verdict"]

    # The overall score from the factors that predict returns, chosen on the discovery half alone (risk judged on its
    # return too here: what it predicts about swings isn't what this candidate is about)
    ret_works = {f: summarise(daily_stats(on(disc), f, h, "return-for-all"), h) for f in FACTORS}
    working = [f for f in FACTORS if ret_works[f]["verdict"] == "works"]
    for r in rows:
        vals = {f: r.get(f) for f in working}
        # Only where the overall score it's compared with exists, so both rank the same stocks on the same dates
        r["composite_working"] = scoring.combine(vals) if working and r.get("composite") is not None and all(v is not None for v in vals.values()) else None

    plan = [*[(f"sector_{f}", f"Within-sector ranks: {LABELS[f]}", f"{f}_sector", f) for f in keys],
            ("riskadj_momentum", "Momentum adjusted for risk", "momRiskAdj", "momentum"),
            ("working_composite", "Overall score from the factors that predict returns" + (f" ({', '.join(LABELS[f] for f in working)})" if working else ""),
             "composite_working", "composite")]
    candidates = []
    for key, label, cand, today in plan:
        c = {"key": key, "label": label, "against": LABELS[today], "factor": cand}
        if key == "working_composite" and not working:
            c.update({"adopted": False, "why": "No factor predicted returns on the first half, so there's nothing to build it from."})
        else:
            c.update({"own": summarise(daily_stats(by_date, cand, h), h), "ownHoldout": summarise(daily_stats(on(hold), cand, h), h),
                      "discovery": compare(by_date, cand, today, h, disc), "holdout": compare(by_date, cand, today, h, hold),
                      "all": compare(by_date, cand, today, h, primary_dates)})
        candidates.append(c)
    # Testing several changes: each improvement must pass Holm's correction over all of them
    tested = [c for c in candidates if "all" in c]
    holm_ok = dict(zip((c["key"] for c in tested), holm([c["all"]["p"] for c in tested])))
    for c in tested:
        works_alone, holds_up = c["own"]["verdict"] == "works", (c["ownHoldout"]["meanIC"] or 0) > 0
        gain = c["all"]["meanDiff"]
        hold_ok = c["holdout"]["p"] is not None and c["holdout"]["p"] < ALPHA
        c["holm"] = holm_ok[c["key"]]
        c["adopted"] = works_alone and holds_up and c["holm"] and hold_ok
        # The first rule it failed, in plain words
        c["why"] = ("Works on its own, beats today's score after the correction for testing several, and again on the second half." if c["adopted"]
                    else f"Doesn't rank the right way on its own ({c['own']['verdict']})." if not works_alone
                    else "Works over all months, but not on the second half." if not holds_up
                    else "Ranks no better than today's score." if gain is None or gain <= 0
                    else f"Ranks a little better than today's score (IC {gain:+.3f}), but not by enough to survive the correction for testing several changes." if not c["holm"]
                    else "Its gain over today's score isn't confirmed on the second half.")
    return {"factors": factors, "candidates": candidates, "dates": len(by_date), "from": min(by_date), "to": max(by_date),
            "primaryHorizon": h, "discovery": [disc[0], disc[-1]] if disc else None, "holdout": [hold[0], hold[-1]] if hold else None,
            "working": working, "caveats": CAVEATS}


def check(tickers, weights=None):
    """The whole research over `tickers` (see docs/score-research.md). `weights` aren't used: the evidence is about
    each score, and weights stay the owner's choice."""
    rows, loaded, skipped = panel(tickers)
    if not rows:
        return {"error": "No price history to check against." + (f" {skipped} stocks were skipped while Yahoo asked for a pause: try again later." if skipped else "")}
    out = judge(rows)
    latest = [r for r in rows if r["date"] == out["to"]]
    out.update({"stocks": loaded, "skipped": skipped, "withStatements": sum(r.get("quality") is not None for r in latest),
                "withValue": sum(r.get("value") is not None for r in latest)})
    return out


def cached_check(tickers, weights, key):
    """The research, kept CACHE_HOURS: it reads five years of prices and four years of statements for every stock."""
    k = f"scorecheck:v3:{key}"
    hit = db.cache_get(k, CACHE_HOURS * 3600)
    if hit is not None:
        return hit
    out = check(tickers, weights)
    out["checked"] = dt.datetime.now().isoformat(timespec="minutes")
    if "error" not in out:
        db.cache_set(k, out)
    return out
