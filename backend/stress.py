"""The stress test: today's holdings through the market's past falls, in the base currency at each day's rate.

Each scenario is a window around a known fall in world shares. Today's mix is bought at the window's start and held;
the test reports its own worst fall inside the window (peak to low), what that would take from today's value, how long
the mix took to get back to its peak, and, with the plan's monthly amount still going in, how long until the value was
back above everything put in. A holding with no prices that far back is left out and named (the rest scaled up to
100%); a scenario covering less than MIN_COVER of the portfolio isn't worked out. Funds younger than their index take
an older fund's moves (`universe.STAND_INS`, as Simulate does), and world funds younger than 2008 take a blend of
Vanguard's US and international index funds held as at the stand-in's start (`BLENDS`), checked against the stand-in's
own falls. Past falls aren't a forecast: the next one will differ in size, length and what falls most."""
import datetime as dt
import logging

import numpy as np
import pandas as pd

from . import data, db, portfolio, simulate
from .universe import STAND_INS

log = logging.getLogger("clearyield.stress")

# The windows: the world market's peak and low (S&P 500 dates, from Yahoo's closes) with PAD days either side, so a
# mix that fell earlier or later than the US market is still caught
SCENARIOS = [
    {"key": "gfc", "name": "2008 financial crisis", "from": "2007-10-09", "to": "2009-03-09",
     "what": "The US housing bust brought down banks; world shares more than halved over 17 months."},
    {"key": "euro", "name": "Euro debt crisis", "from": "2011-05-02", "to": "2011-10-03",
     "what": "Greece, then Italy and Spain, struggled to borrow, and the US lost its AAA rating."},
    {"key": "china", "name": "China slowdown and oil slump", "from": "2015-05-21", "to": "2016-02-11",
     "what": "China's market crashed and devalued the yuan; oil fell below $30."},
    {"key": "late2018", "name": "Late-2018 sell-off", "from": "2018-09-20", "to": "2018-12-24",
     "what": "Rising US rates and a trade war with China."},
    {"key": "covid", "name": "Covid crash", "from": "2020-02-19", "to": "2020-03-23",
     "what": "The fastest fall on record as the world locked down, and among the fastest recoveries."},
    {"key": "rates", "name": "Inflation and rate rises", "from": "2022-01-03", "to": "2022-10-12",
     "what": "Inflation and the fastest rate rises in decades: shares and bonds fell together."},
]
PAD = 45                # days either side of a window
MIN_COVER = 0.6         # a scenario needs prices for at least this share of the portfolio
WORLD = "VT"            # the world market, shown beside each scenario
# A world fund younger than 2008 takes US + international index funds held as at `anchor`, with `us` the US share
# fitted to the fund's weekly moves over its first years. Checked (Nov 2026): VT against the blend in six falls,
# 2008 -50.2/-50.3%, 2011 -23.8/-23.4, 2015 -19.8/-18.6, 2018 -17.8/-18.4, 2020 -34.2/-34.5, 2022 -26.2/-26.0 (fitted
# on 2008-10 only); URTH 2015 -18.9/-17.6, 2018 -18.2/-19.1, 2020 -34.0/-34.4, 2022 -26.1/-26.0.
BLENDS = {"VT": {"parts": ("VTSMX", "VGTSX"), "us": 0.49, "anchor": "2008-07-01"},
          "URTH": {"parts": ("VTSMX", "EFA"), "us": 0.64, "anchor": "2012-01-17"}}
CACHE_FOR = 6 * 3600


def _join(own, older):
    """`older` before `own` begins, scaled to meet it; None if `older` doesn't start earlier."""
    if older is None or older.index[0] >= own.index[0] - pd.Timedelta(days=30):
        return None
    at = older[older.index <= own.index[0]]
    if not len(at):
        return None
    return pd.concat([older[older.index < own.index[0]] * (own.iloc[0] / at.iloc[-1]), own])


def _blend(spec, base):
    """The blend's value in the base currency, buy and hold with the US share `us` on the anchor date."""
    us, intl = (simulate._in_base(t, base) for t in spec["parts"])
    df = pd.concat([us, intl], axis=1).sort_index().ffill().dropna()
    at = df[df.index <= pd.Timestamp(spec["anchor"])]
    if not len(at):
        return None
    pu, pi = at.iloc[-1]
    return spec["us"] / pu * df.iloc[:, 0] + (1 - spec["us"]) / pi * df.iloc[:, 1]


DEMO_FALLS = {"gfc": -0.55, "euro": -0.19, "china": -0.16, "late2018": -0.16, "covid": -0.33, "rates": -0.24}


def _demo_long(t):
    """Demo mode: twenty years of made-up closes (demo prices go back only five) in which each scenario's fall
    happens to a made-up market, which every holding follows by its own sensitivity plus its own noise."""
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=252 * 20)
    market = np.random.default_rng(1).normal(0.0004, 0.009, len(idx))
    for sc in SCENARIOS:
        inside = (idx >= pd.Timestamp(sc["from"])) & (idx <= pd.Timestamp(sc["to"]))
        market[inside] += np.log(1 + DEMO_FALLS[sc["key"]]) / inside.sum()
    rng = np.random.default_rng(data._seed(t))
    beta = 2.5 if t.endswith("-USD") else rng.uniform(0.6, 1.5)
    rets = beta * market + rng.normal(0, 0.008 * beta, len(idx))
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)


def extended(t, base):
    """A holding's closes in the base currency, reaching back through its stand-ins where it has them: an older fund
    on the same index, then a world fund's blend. Returns the series and the stand-ins used: [{ticker, standIn, until}]."""
    if data.DEMO:
        return _demo_long(t), []
    own, used = simulate._in_base(t, base), []
    stand = STAND_INS.get(t)
    steps = ([("fund", stand)] if stand else []) + [("blend", x) for x in (stand, t) if x in BLENDS]
    for kind, x in steps:
        try:
            older = simulate._in_base(x, base) if kind == "fund" else _blend(BLENDS[x], base)
        except data.DataError:
            continue
        joined = _join(own, older)
        if joined is not None:
            used.append({"ticker": t, "standIn": x if kind == "fund" else " + ".join(BLENDS[x]["parts"]),
                         "until": own.index[0].strftime("%Y-%m-%d")})
            own = joined
    return own, used


def _months(a, b):
    return round((b - a).days / 30.44, 1)


def scenario(series, weights, sc, value, monthly=0.0, pad=PAD, min_cover=MIN_COVER):
    """One scenario for a mix: `series` {ticker: closes in the base currency}, `weights` {ticker: share, adding to 1}.
    The mix is bought at the window's start and held. Returns the fall (peak to low inside the window), its dates,
    the worst day, the loss on `value` (today's portfolio value), the months to get back to the peak (None while it
    hasn't), and with `monthly` added on each month's first trading day, the months until the value was back above
    everything put in. Holdings with no price by the window's start are left out (`missing`) and the rest scaled up;
    under `min_cover` of the weight covered, nothing is worked out (`enough` False)."""
    start = pd.Timestamp(sc["from"]) - pd.Timedelta(days=pad)
    end = pd.Timestamp(sc["to"]) + pd.Timedelta(days=pad)
    have = {t: s for t, s in series.items() if s is not None and len(s) and s.index[0] <= start + pd.Timedelta(days=7)}
    missing = [{"ticker": t, "weight": weights[t], "from": series[t].index[0].strftime("%Y-%m-%d") if series.get(t) is not None and len(series[t]) else None}
               for t in weights if t not in have]
    cover = sum(weights[t] for t in have)
    out = {**{k: sc[k] for k in ("key", "name", "what", "from", "to")}, "coverage": cover, "missing": missing}
    if cover < min_cover:
        return {**out, "enough": False}
    w = {t: weights[t] / cover for t in have}
    px = pd.concat(have, axis=1).sort_index()
    px = px[px.index >= px.index[px.index <= start][-1] if (px.index <= start).any() else px.index[0]].ffill().dropna()
    level = sum(w[t] * px[t] / px[t].iloc[0] for t in w)  # bought at the start and held
    inside = level[level.index <= end]
    dd = inside / inside.cummax() - 1
    low_at = dd.idxmin()
    peak_at = inside[inside.index <= low_at].idxmax()
    fall = float(dd.min())
    after = level[level.index > low_at]
    back = after[after >= level[peak_at]]
    days = inside.pct_change().dropna()
    out.update({"enough": True, "fall": fall, "peak": peak_at.strftime("%Y-%m-%d"), "low": low_at.strftime("%Y-%m-%d"),
                "fallMonths": _months(peak_at, low_at), "worstDay": float(days.min()) if len(days) else None,
                "worstDayOn": days.idxmin().strftime("%Y-%m-%d") if len(days) else None,
                "loss": fall * value if value else None, "recovered": back.index[0].strftime("%Y-%m-%d") if len(back) else None,
                "recoverMonths": _months(peak_at, back.index[0]) if len(back) else None,
                "sinceLow": _months(low_at, level.index[-1])})
    if monthly and value:
        out.update(_with_plan(px[px.index >= peak_at], w, value, monthly, low_at))
    return out


def _with_plan(px, w, value, monthly, low_at):
    """`value` invested at the peak, `monthly` added on each month's first trading day at the same weights, all held:
    the first day after the scenario's low that the value was back above everything put in, and the lowest it got
    against what was put in until then."""
    units = {t: value * w[t] / px[t].iloc[0] for t in w}
    put_in, month, worst, worst_at, back = value, px.index[0].to_period("M"), 0.0, None, None
    for d, row in px.iterrows():
        if d.to_period("M") != month:
            month = d.to_period("M")
            for t in w:
                units[t] += monthly * w[t] / row[t]
            put_in += monthly
        gap = float(sum(units[t] * row[t] for t in w) - put_in)
        if gap < worst:
            worst, worst_at = gap, d
        if d > low_at and gap >= 0:
            back = d
            break
    return {"planWorst": worst, "planWorstAt": worst_at.strftime("%Y-%m-%d") if worst_at is not None else None,
            "planBack": back.strftime("%Y-%m-%d") if back is not None else None,
            "planBackMonths": _months(px.index[0], back) if back is not None else None,
            "planPutIn": put_in if back is not None else None}


def stress(settings, summary=None):
    """Every scenario for today's holdings, with the world market beside each, kept CACHE_FOR (the inputs are
    today's holdings, values and the plan, so a change to any is a new key)."""
    summary = summary or portfolio.summary(settings)
    base = summary.get("base") or settings.get("base_currency") or "EUR"
    mix = simulate.current_mix(summary)
    value = sum(r.get("value") or 0 for r in summary.get("positions") or [])
    monthly = float(((settings.get("plan") or {}).get("monthly")) or 0)
    if not mix:
        return {"empty": True, "scenarios": SCENARIOS}
    key = "stress:v1:" + base + ":" + ",".join(f"{m['ticker']}={m['weight']}" for m in mix) + f":{round(value)}:{monthly}"
    kept = db.cache_get(key, CACHE_FOR)
    if kept is not None:
        return kept
    weights = {m["ticker"]: m["weight"] / 100 for m in mix}
    series, used, failed = {}, [], []
    for t in weights:
        try:
            series[t], u = extended(t, base)
            used += u
        except data.DataError:
            series[t] = None
            failed.append(t)
    try:
        world, _ = extended(WORLD, base)
    except data.DataError:
        world = None
    rows = []
    for sc in SCENARIOS:
        r = scenario(series, weights, sc, value, monthly)
        if world is not None:
            wr = scenario({WORLD: world}, {WORLD: 1.0}, sc, 0)
            r["world"] = wr.get("fall") if wr.get("enough") else None
        rows.append(r)
    done = [r for r in rows if r["enough"]]
    worst = min(done, key=lambda r: r["fall"]) if done else None
    out = {"base": base, "value": value, "monthly": monthly, "scenarios": rows, "standIns": used, "failed": failed,
           "worst": worst["key"] if worst else None, "minCover": MIN_COVER, "pad": PAD,
           "asOf": dt.date.today().isoformat(), "worldName": "World shares (VT)"}
    db.cache_set(key, out)
    return out
