"""Portfolio-level simulations: a backtest of a mix of assets over its shared history, and (next) a Monte Carlo of
its possible futures. Prices are Yahoo's adjusted closes, so dividends are reinvested, converted to the base currency
at each day's rate."""
import pandas as pd

from . import data, ledger
from . import indicators as ind
from .universe import STAND_INS

REBALANCE = {"none": "Never", "monthly": "Every month", "quarterly": "Every quarter", "yearly": "Every year"}
_PERIOD_CODE = {"monthly": "M", "quarterly": "Q", "yearly": "Y"}
PERIODS = {"3y": 3, "5y": 5, "10y": 10, "max": None}
MAX_ASSETS = 15
MAX_MONEY = 1e9


def clean_mix(mix):
    """{ticker: weight as a fraction} from [{"ticker", "weight" in %}]; the same ticker twice is added up. The weights
    must add up to 100%: quietly scaling them would test a different mix from the one written."""
    rows = {}
    for m in mix or []:
        t = str(m.get("ticker") or "").strip().upper()
        try:
            w = float(m.get("weight") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"The weight for {t or 'an asset'} isn't a number.")
        if not t:
            continue
        if w < 0:
            raise ValueError(f"The weight for {t} can't be negative.")
        if w > 0:
            rows[t] = rows.get(t, 0.0) + w
    if not rows:
        raise ValueError("Add at least one asset with a weight above 0%.")
    if len(rows) > MAX_ASSETS:
        raise ValueError(f"At most {MAX_ASSETS} assets.")
    total = sum(rows.values())
    if abs(total - 100) > 0.05:
        raise ValueError(f"The weights add up to {total:.2f}%, not 100%.")
    return {t: w / total for t, w in rows.items()}


def current_mix(summary):
    """Today's holdings as weights of their value in the base currency, from portfolio.summary()."""
    rows = [(r["ticker"], r.get("value") or 0) for r in summary.get("positions") or []]
    total = sum(v for _, v in rows if v > 0)
    if not total:
        return []
    mix = [{"ticker": t, "weight": round(v / total * 100, 2)} for t, v in sorted(rows, key=lambda x: -x[1]) if v > 0]
    mix[0]["weight"] = round(mix[0]["weight"] + 100 - sum(m["weight"] for m in mix), 2)  # rounding can't leave 99.99%
    return mix


def _in_base(t, base):
    close = data.history(t, "max")["Close"].dropna()
    close = close[close > 0]
    if len(close) < 2:
        raise data.DataError(f"Yahoo has no price history for {t}.")
    cur = data.info(t).get("currency")
    rate = data.fx_series(cur, base, close.index)
    return (close * (rate if rate is not None else data.fx_rate(cur, base))).dropna()


def prices(tickers, base, years=None, stand_ins=False, used=None):
    """Daily adjusted closes in the base currency, from the first day every asset has a price. Returns the table, the
    asset whose history starts last, and whether that asset (rather than the period asked for) set the start.
    With `stand_ins`, a fund in STAND_INS takes the older fund's moves before its own history begins, scaled to meet
    it; each one used is added to the list `used`."""
    cols = {}
    for t in tickers:
        own = _in_base(t, base)
        other = STAND_INS.get(t) if stand_ins else None
        if other and other not in tickers:
            try:
                older = _in_base(other, base)
            except data.DataError:
                older = None
            if older is not None and older.index[0] < own.index[0] - pd.Timedelta(days=30):
                joined_at = older[older.index <= own.index[0]]
                if len(joined_at):
                    before = older[older.index < own.index[0]] * (own.iloc[0] / joined_at.iloc[-1])
                    own = pd.concat([before, own])
                    if used is not None:
                        used.append({"ticker": t, "standIn": other, "from": before.index[0].strftime("%Y-%m-%d"),
                                     "until": own.index[len(before)].strftime("%Y-%m-%d")})
        cols[t] = own
    limiting = max(cols, key=lambda t: cols[t].index[0])
    table = pd.concat(cols, axis=1).sort_index()
    start = cols[limiting].index[0]
    wanted = table.index[-1] - pd.DateOffset(years=years) if years else None
    limited = wanted is None or start > wanted
    if not limited:
        start = wanted
    # Markets close on different days: a holiday in one carries its last price
    table = table[table.index >= start].ffill().dropna()
    return table, limiting, limited


def _run(px, weights, rebalance="yearly", cost_bps=0.0, start_value=10000.0, monthly=0.0):
    """Day by day: the units held, valued at each close. New money arrives on each month's first trading day and goes
    first to what's furthest below its target; a rebalance on the first trading day of each period sets every asset
    back to its target. Both trade at that day's close, paying the cost on what changes hands. The index is the
    time-weighted return, which money added doesn't move."""
    w = pd.Series(weights, dtype=float).reindex(px.columns).fillna(0.0)
    cost = cost_bps / 1e4
    first = px.iloc[0]
    units = start_value * (1 - cost) * w / first
    code = _PERIOD_CODE.get(rebalance)
    month, period = px.index[0].to_period("M"), px.index[0].to_period(code) if code else None
    flows, values, index, trades = [(px.index[0], -start_value)], [], [], 1
    level, before = 1.0, start_value  # the first day's value is after the purchase's cost, so the cost counts
    for d, row in px.iterrows():
        held = units * row
        value = float(held.sum())
        if before:
            level *= value / before
        new_month = d.to_period("M") != month
        if new_month and monthly > 0:
            # new money tops up what's below target first: rebalancing that costs no sale
            want = (value + monthly) * w - held
            short = want.clip(lower=0)
            share = short / short.sum() if short.sum() > 0 else w
            units = units + monthly * (1 - cost) * share / row
            flows.append((d, -monthly))
            held = units * row
            value = float(held.sum())
        if code and d.to_period(code) != period:
            moved = float((value * w - held).abs().sum())
            if moved > value * 1e-9:
                value -= moved * cost
                units = value * w / row
                trades += 1
        month = d.to_period("M")
        period = d.to_period(code) if code else None
        before = value
        values.append(value)
        index.append(level)
    flows.append((px.index[-1], values[-1]))
    return pd.Series(values, index=px.index), pd.Series(index, index=px.index), flows, trades


def _underwater(level):
    """The longest stretch below a previous high, in days, and whether it's still going on."""
    peak = level.cummax()
    longest, since, now = 0, None, False
    for d, v in level.items():
        if v < peak[d] - 1e-12:
            since = since or d
        elif since is not None:
            longest, since = max(longest, (d - since).days), None
    if since is not None:
        longest, now = max(longest, (level.index[-1] - since).days), True
    return longest, now


def _metrics(level, rf):
    longest, ongoing = _underwater(level)
    ends = level.groupby(level.index.year).last()
    yearly = ends / ends.shift(1).fillna(level.iloc[0]) - 1
    first, last = level.index[0], level.index[-1]
    # a year the test doesn't cover from its first trading days to its last is marked as part of a year
    partial = {first.year} if (first - pd.Timestamp(first.year, 1, 1)).days > 7 else set()
    if (pd.Timestamp(last.year, 12, 31) - last).days > 7:
        partial.add(last.year)
    return {"totalReturn": float(level.iloc[-1] / level.iloc[0] - 1), "cagr": ind.cagr(level),
            "volatility": ind.volatility(level, 10 ** 6), "maxDrawdown": ind.max_drawdown(level), "sharpe": ind.sharpe(level, rf),
            "longestUnderwaterDays": longest, "underwaterNow": ongoing,
            "bestYear": _extreme(yearly, partial, max), "worstYear": _extreme(yearly, partial, min),
            "yearly": [{"year": int(y), "return": float(r), "partial": int(y) in partial} for y, r in yearly.items()]}


def _extreme(yearly, partial, pick):
    """The best or worst full calendar year, or None when there's none."""
    full = {y: r for y, r in yearly.items() if y not in partial}
    return {"year": int(pick(full, key=full.get)), "return": float(full[pick(full, key=full.get)])} if full else None


def _whose(limiting, used):
    """'VWCE.DE's' or, when a stand-in supplied the early years, 'VT's (standing in for VWCE.DE)'."""
    other = next((u["standIn"] for u in used if u["ticker"] == limiting), None)
    return f"{other}'s (standing in for {limiting})" if other else f"{limiting}'s"


def _stand_in_note(used):
    return ["Before its own history: " + "; ".join(f"{u['ticker']} uses {u['standIn']} (a fund tracking almost the same index) "
                                                   f"from {u['from']} to {u['until']}" for u in used) + "."] if used else []


def backtest(mix, base, benchmark=None, period="10y", rebalance="yearly", cost_bps=10.0, start_value=10000.0, monthly=0.0, rf=0.04,
             stand_ins=True):
    """A mix over the history all its assets share, against the benchmark held the same way."""
    weights = clean_mix(mix)
    if period not in PERIODS:
        raise ValueError(f"The period must be one of {', '.join(PERIODS)}.")
    if rebalance not in REBALANCE:
        raise ValueError(f"Rebalancing must be one of {', '.join(REBALANCE)}.")
    if not 0 <= cost_bps <= 500:
        raise ValueError("The trading cost must be between 0 and 500 basis points.")
    if not 0 < start_value <= MAX_MONEY or not 0 <= monthly <= MAX_MONEY:
        raise ValueError("The starting amount must be above 0, and the monthly addition 0 or more.")
    if benchmark and list(weights) == [benchmark]:
        benchmark = None  # the mix is the benchmark: a second column would only repeat it
    tickers = list(weights) + ([benchmark] if benchmark and benchmark not in weights else [])
    used = []
    px, limiting, limited = prices(tickers, base, PERIODS[period], stand_ins, used)
    if len(px) < 60:
        raise ValueError(f"Not enough shared history: {limiting}'s prices start {px.index[0]:%Y-%m-%d}, leaving under 60 trading days.")
    value, level, flows, trades = _run(px[list(weights)], weights, rebalance, cost_bps, start_value, monthly)
    out = {"dates": [d.strftime("%Y-%m-%d") for d in px.index], "value": [float(v) for v in value],
           "growth": [float(v * 100) for v in level], "start": px.index[0].strftime("%Y-%m-%d"), "end": px.index[-1].strftime("%Y-%m-%d"),
           "limitedBy": limiting if limited else None,
           "weights": {t: round(w * 100, 2) for t, w in weights.items()}, "base": base, "rebalance": REBALANCE[rebalance],
           "standIns": [u for u in used if u["until"] > px.index[0].strftime("%Y-%m-%d")],
           "portfolio": {**_metrics(level, rf), "invested": float(-sum(a for _, a in flows[:-1])), "finalValue": float(value.iloc[-1]),
                         "moneyWeightedReturn": ledger.xirr(flows) if monthly > 0 else None, "rebalances": trades - 1}}
    if benchmark:
        b_value, b_level, b_flows, _ = _run(px[[benchmark]], {benchmark: 1.0}, "none", cost_bps, start_value, monthly)
        out["benchmark"] = {"ticker": benchmark, "growth": [float(v * 100) for v in b_level],
                            **_metrics(b_level, rf), "finalValue": float(b_value.iloc[-1]),
                            "moneyWeightedReturn": ledger.xirr(b_flows) if monthly > 0 else None}
    out["caveats"] = [
        "Hindsight: testing what you hold today assumes you'd have picked it back then.",
        f"Starts {out['start']}" + (f", when {_whose(limiting, out['standIns'])} price history begins" if out["limitedBy"] else "") + ": every asset needs a price on every day.",
        *_stand_in_note(out["standIns"]),
        f"Prices include reinvested dividends and are converted to {base} at each day's rate. Taxes and fund costs beyond the price aren't included.",
        "Past returns don't predict future ones.",
    ]
    return out


# ---------------------------------------------------------------- Monte Carlo: possible futures
BLOCK_MONTHS = 12      # months drawn together, so bad months that came in a row (2022) stay in a row
MIN_MONTHS = 36        # below three years of history there's too little to draw from
SHORT_MONTHS = 120     # under ten years, a full market cycle may be missing: said so
MAX_PATHS = 20000
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)


def monthly_returns(level):
    """Whole calendar months only: the part-month at the start and an unfinished current month are left out."""
    ends = level.groupby(level.index.to_period("M")).last()
    rets = ends.pct_change().dropna()
    last = level.index[-1]
    if (last.to_period("M").end_time.normalize() - last.normalize()).days > 3:
        rets = rets.iloc[:-1]
    return rets


def _draw(log_rets, months, paths, block, rng):
    """Paths of `months` monthly log returns, each built from blocks of consecutive months of history, starting at
    random months (wrapping round at the end), so a crash and its recovery stay together as they happened."""
    import numpy as np
    m = len(log_rets)
    block = max(1, min(block, m))
    n_blocks = -(-months // block)
    starts = rng.integers(0, m, size=(paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)) % m
    return np.asarray(log_rets)[idx.reshape(paths, -1)[:, :months]]


def project(log_rets, start_value, monthly, months, paths=5000, block=BLOCK_MONTHS, seed=1, expected=None):
    """Value paths: the start invested now, `monthly` added at the start of each month, each month's return drawn
    from history. With `expected` (a yearly return), every drawn month is shifted by the same amount so the average
    yearly growth is that instead of history's, keeping history's ups, downs and crashes. Returns the values
    (paths x months+1) and each path's growth index, which money added doesn't move (for its falls)."""
    import numpy as np
    log_rets = np.asarray(log_rets, dtype=float)
    if expected is not None:
        log_rets = log_rets - log_rets.mean() + np.log1p(expected) / 12
    draws = _draw(log_rets, months, paths, block, np.random.default_rng(seed))
    growth = np.exp(draws)
    values = np.empty((paths, months + 1))
    values[:, 0] = start_value
    v = np.full(paths, float(start_value))
    for t in range(months):
        v = (v + monthly) * growth[:, t]
        values[:, t + 1] = v
    index = np.concatenate([np.ones((paths, 1)), np.cumprod(growth, axis=1)], axis=1)
    return values, index


def monte_carlo(mix, base, years=10, start_value=10000.0, monthly=0.0, rebalance="yearly", cost_bps=10.0, period="max",
                paths=5000, expected=None, inflation=0.0, goal=None, seed=1, rf=0.04, stand_ins=True):
    """Possible futures of a mix, drawn from its own history of whole months, rebalanced as chosen."""
    import numpy as np
    weights = clean_mix(mix)
    if rebalance not in REBALANCE or period not in PERIODS:
        raise ValueError("Unknown rebalancing or period.")
    if not 1 <= years <= 50:
        raise ValueError("Simulate between 1 and 50 years.")
    if not 0 < start_value <= MAX_MONEY or not 0 <= monthly <= MAX_MONEY:
        raise ValueError("The starting amount must be above 0, and the monthly addition 0 or more.")
    if not 100 <= paths <= MAX_PATHS:
        raise ValueError(f"Between 100 and {MAX_PATHS:,} paths.")
    if expected is not None and not -0.5 < expected < 0.5:
        raise ValueError("The yearly return to assume must be between -50% and 50%.")
    if not 0 <= inflation < 0.2:
        raise ValueError("Inflation must be between 0% and 20%.")
    used = []
    px, limiting, limited = prices(list(weights), base, PERIODS[period], stand_ins, used)
    used = [u for u in used if u["until"] > px.index[0].strftime("%Y-%m-%d")]
    _, level, _, _ = _run(px, weights, rebalance, cost_bps, 10000.0, 0.0)
    rets = monthly_returns(level)
    if len(rets) < MIN_MONTHS:
        raise ValueError(f"Too little history to draw from: {len(rets)} whole months (since {px.index[0]:%Y-%m-%d}"
                         f"{f', when {limiting} starts' if limited else ''}); the simulation needs {MIN_MONTHS}.")
    log_rets = np.log1p(rets.to_numpy())
    months = int(years * 12)
    values, index = project(log_rets, start_value, monthly, months, paths, BLOCK_MONTHS, seed, expected)
    deflate = (1 + inflation) ** (np.arange(months + 1) / 12)
    real = values / deflate
    invested = start_value + monthly * np.arange(months + 1)
    at = list(range(0, months + 1, 12)) + ([months] if months % 12 else [])
    bands = lambda v: {str(p): [float(x) for x in np.percentile(v[:, at], p, axis=0)] for p in PERCENTILES}
    final, final_real = values[:, -1], real[:, -1]
    falls = (index / np.maximum.accumulate(index, axis=1) - 1).min(axis=1)
    hist_year = float(np.expm1(log_rets.mean() * 12))
    out = {
        "years": [round(m / 12, 2) for m in at], "nominal": bands(values), "real": bands(real) if inflation else None,
        "invested": [float(invested[m]) for m in at],
        "final": {"median": float(np.median(final)), "medianReal": float(np.median(final_real)) if inflation else None,
                  "p10": float(np.percentile(final, 10)), "p90": float(np.percentile(final, 90)),
                  "belowInvested": float((final < invested[-1]).mean()),
                  "reachGoal": float((final_real >= goal).mean()) if goal else None,
                  "medianYearly": float(np.median((index[:, -1]) ** (12 / months) - 1))},
        "falls": {"median": float(np.median(falls)), "p10": float(np.percentile(falls, 10)), "worseThan30": float((falls < -0.3).mean())},
        "assumption": {"historyFrom": px.index[0].strftime("%Y-%m-%d"), "historyTo": px.index[-1].strftime("%Y-%m-%d"),
                       "months": int(len(rets)), "limitedBy": limiting if limited else None,
                       "historicalYearly": hist_year, "historicalVolatility": float(rets.std() * np.sqrt(12)),
                       "worstMonth": float(rets.min()), "expected": expected, "usedYearly": expected if expected is not None else hist_year,
                       "inflation": inflation, "paths": paths, "blockMonths": BLOCK_MONTHS, "seed": seed},
        "weights": {t: round(w * 100, 2) for t, w in weights.items()}, "base": base, "rebalance": REBALANCE[rebalance],
        "startValue": start_value, "monthly": monthly, "goal": goal, "standIns": used,
    }
    out["caveats"] = [
        f"Each future is built from the mix's own {len(rets)} whole months since {out['assumption']['historyFrom']}"
        + (f" (when {_whose(limiting, used)} history starts)" if limited else "") + ", drawn in 12-month blocks: it can't contain a "
        "kind of crash that history doesn't have.",
        ("History of under ten years may miss a full market cycle; a lower yearly return to assume shows the effect."
         if len(rets) < SHORT_MONTHS else "Ten or more years of history, but the future needn't resemble it."),
        "The bands are a range of outcomes, not a forecast: about 1 future in 10 ends below the 10th percentile line.",
        "Before taxes; fund costs are in the prices, trading costs as set. Money is added at the start of each month.",
        *_stand_in_note(used),
    ]
    return out
