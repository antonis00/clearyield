"""Portfolio valuation, allocation, risk, warnings and rebalancing."""
import datetime as dt
import re
from collections import defaultdict

import pandas as pd

from . import data, db, ledger
from . import indicators as ind
from .universe import CORE_FUNDS, FUND_CURRENCIES, default_bucket


def positions(extra=None):
    """What's held now, from the ledger. `extra` is a hypothetical purchase, which is never saved."""
    agg = {}
    for t, p in ledger.replay(db.q("SELECT * FROM transactions") + ([extra] if extra else [])).items():
        agg[t] = {**p, "shares": sum(lot["shares"] for lot in p["lots"]), "costTotal": sum(lot["shares"] * lot["cost"] for lot in p["lots"])}
    return agg


NO_SECTOR = "Funds: other or no sector data"
BUCKETS = ("core", "satellite")


def bucket_lookup():
    """A function giving each ticker's bucket: the one you chose, or the default for it."""
    chosen = {r["ticker"]: r["bucket"] for r in db.q("SELECT ticker, bucket FROM buckets")}
    return lambda t: chosen.get(t) or default_bucket(t)


def set_bucket(ticker, bucket):
    """Only choices that differ from the default are stored, so switching back leaves nothing behind."""
    if bucket not in BUCKETS:
        raise ValueError("A holding is either core or satellite.")
    if bucket == default_bucket(ticker):
        db.x("DELETE FROM buckets WHERE ticker=?", (ticker,))
    else:
        db.x("INSERT OR REPLACE INTO buckets (ticker, bucket) VALUES (?,?)", (ticker, bucket))


def _risk_shares(series, bucket_of):
    """Each bucket's share of the portfolio's daily variance, over the past year with today's holdings. A day's
    portfolio return is the sum of each bucket's part of it, so the shares are cov(part, total) / var(total) and add
    up to 1. A small, jumpy satellite can carry far more of the swings than of the money."""
    values = pd.concat(series, axis=1).sort_index().ffill()
    previous = values.shift(1)
    live = previous.notna() & values.notna()
    start = previous.where(live).sum(axis=1).replace(0, float("nan"))
    parts = pd.DataFrame({b: (values[cols] - previous[cols]).where(live[cols]).sum(axis=1) / start
                          for b in BUCKETS if (cols := [t for t in values.columns if bucket_of(t) == b])}).dropna()
    whole = parts.sum(axis=1)
    if len(parts) < 20 or not whole.var() > 0:
        return None
    return {b: float(parts[b].cov(whole) / whole.var()) for b in parts.columns}


def _bucket_performance(rows, base, bucket_of):
    """Each bucket's time-weighted return over every day both were held, so neither gets credit for a market the
    other missed. That's the whole history, not just the past year. Lots with no date count from the first dated
    trade; with no dated trades at all it's the past year, and says so. Closed positions count in their bucket now."""
    dated = sorted(r["date"] for r in rows if r.get("date") and (r.get("kind") or "buy") != "dividend")
    period, since = ("max", pd.Timestamp(dated[0]) - pd.Timedelta(days=7)) if dated else ("1y", None)
    lines = {b: _actual_performance([r for r in rows if bucket_of(r["ticker"]) == b], base, period, since) for b in BUCKETS}
    if any(v is None for v in lines.values()):
        return None
    start = max(v.index[0] for v in lines.values())
    dates = sorted(set(lines["core"].index) | set(lines["satellite"].index))
    dates = [d for d in dates if d >= start]
    out = {"start": start.strftime("%Y-%m-%d"), "pastYearOnly": not dated, "dates": [d.strftime("%Y-%m-%d") for d in dates],
           "days": int(min(len(v[v.index >= start]) for v in lines.values()) - 1)}
    for b, v in lines.items():
        at_start = v.asof(start)
        out[b] = float(v.iloc[-1] / at_start - 1)
        out[f"{b}Line"] = [None if pd.isna(x) else float(x) for x in v.reindex(dates).ffill() / at_start * 100]
    out["edge"] = out["satellite"] - out["core"]
    return out


def _value_index(series):
    """Daily index of the portfolio, starting at 1. Each day uses only positions priced on both that day and
    the one before, so a position with a short history joins when it starts rather than cutting the rest."""
    values = pd.concat(series, axis=1).sort_index().ffill()
    previous = values.shift(1)
    live = previous.notna() & values.notna()
    returns = (values.where(live).sum(axis=1) / previous.where(live).sum(axis=1) - 1).fillna(0)
    return (1 + returns).cumprod()


def _lot_fx(lot, currency, base, today_fx):
    """(rate, exact) for a lot's cost: the rate on its purchase date, or today's when that can't be known."""
    rate = data.fx_on(currency, base, lot["bought"]) if lot["bought"] else None
    if rate is not None:
        return rate, True
    return today_fx, data.fx_is_fixed(currency, base)


def _actual_performance(rows, base, period="1y", since=None):
    """Daily index of what was actually held: the shares at each day's close, carried to the next close, at each day's
    FX rate. Trades don't count as gains, and adjusted prices include dividends. None until something has been held overnight."""
    deltas = defaultdict(list)
    for r in ledger.in_todays_shares(rows):  # the same units as Yahoo's split-adjusted prices
        if r["kind"] != "dividend":
            deltas[r["ticker"]].append((r["date"], r["shares"] if r["kind"] == "buy" else -r["shares"]))
    prices = {}
    for t in deltas:
        try:
            close = data.history(t, period)["Close"]
            if since is not None:
                close = close[close.index >= since]
            cur = data.info(t).get("currency")
            rate = data.fx_series(cur, base, close.index)
            prices[t] = close * (rate if rate is not None else data.fx_rate(cur, base))
        except data.DataError:
            continue  # reported as missing already, if it's still held
    if not prices:
        return None
    values = pd.concat(prices, axis=1).sort_index().ffill()
    shares = pd.DataFrame(0.0, index=values.index, columns=values.columns)
    for t in values.columns:
        for date, delta in deltas[t]:
            shares.loc[shares.index >= pd.Timestamp(date or "1900-01-01"), t] += delta
    held = shares.shift(1).fillna(0.0)
    both = values.notna() & values.shift(1).notna()
    start_of_day = (held * values.shift(1)).where(both).sum(axis=1)
    end_of_day = (held * values).where(both).sum(axis=1)
    active = start_of_day > 0
    if not active.any():
        return None
    returns = (end_of_day / start_of_day.where(active) - 1).fillna(0.0)
    first = list(active).index(True) - 1  # the close before the first day held is where the line starts
    return (1 + returns.iloc[first:]).cumprod()


def _sale_fx(currency, base, date, today_fx):
    rate = data.fx_on(currency, base, date) if date else None
    return (rate, True) if rate is not None else (today_fx, data.fx_is_fixed(currency, base))


def _realised(book, base):
    """Sales and dividends in the base currency, at the rates on their dates, plus every dated cash flow for the annual return."""
    out, unpriced = {}, []
    flows, all_dated = [], True
    for t, p in book.items():
        cur = data.info(t).get("currency")
        try:
            fx = data.fx_rate(cur, base)
        except data.DataError:
            unpriced.append(t)  # one position without a rate mustn't blank the others
            all_dated = False
            continue
        realised = dividends = 0.0
        exact = True
        for s in p["sales"]:
            sold_at, a = _sale_fx(cur, base, s["date"], fx)
            bought_at, b = _lot_fx(s, cur, base, fx)
            realised += s["shares"] * (s["price"] * sold_at - s["cost"] * bought_at)
            exact = exact and a and b
            flows.append((dt.date.fromisoformat(s["date"]), s["shares"] * s["price"] * sold_at))
            if s["bought"]:
                flows.append((dt.date.fromisoformat(s["bought"]), -s["shares"] * s["cost"] * bought_at))
            else:
                all_dated = False
        for d in p["dividends"]:
            # A dividend is the total received, which brokers pay in pounds (dinars...), even for a stock quoted in pence (fils)
            pay = data.main_unit(cur)
            at, a = _sale_fx(pay, base, d["date"], fx if pay == cur else data.fx_rate(pay, base))
            dividends += d["amount"] * at
            exact = exact and a
            flows.append((dt.date.fromisoformat(d["date"]), d["amount"] * at))
        for lot in p["lots"]:
            if lot["bought"]:
                flows.append((dt.date.fromisoformat(lot["bought"]), -lot["shares"] * lot["cost"] * _lot_fx(lot, cur, base, fx)[0]))
            else:
                all_dated = False
        out[t] = {"ticker": t, "name": data.info(t).get("name"), "realised": realised, "dividends": dividends, "exact": exact}
    return out, flows, all_dated, unpriced


def _income(ticker, shares, price, fx, inf):
    """A year's dividends in the base currency at today's holding: what each share actually paid over the past year,
    or Yahoo's yield when the payments aren't available (demo mode, or Yahoo not answering)."""
    paid = data.dividends_past_year(ticker)
    if paid is None:
        return shares * price * fx * (inf.get("dividendYield") or 0)
    return shares * paid * fx


def summary(settings, extra=None):
    """`extra` is a hypothetical lot, included in the figures but never saved."""
    base = settings.get("base_currency", "USD")
    rf = settings.get("risk_free_rate", 0.04)
    bench_t = settings.get("benchmark", "SPY")
    rows, series, errors, missing = [], {}, [], []
    book = positions(extra)
    bucket_of = bucket_lookup()
    for t, p in book.items():
        if p["shares"] <= 0:
            continue
        try:
            h = data.history(t, "1y")
            inf = data.info(t)
            cur = inf.get("currency")
            fx = data.fx_rate(cur, base)
        except data.DataError as e:
            errors.append(f"{t}: {e}")
            missing.append(t)
            continue
        close = h["Close"]
        price, prev = float(close.iloc[-1]), float(close.iloc[-2]) if len(close) > 1 else float(close.iloc[-1])
        value = p["shares"] * price * fx
        lot_fx = [_lot_fx(lot, cur, base, fx) for lot in p["lots"]]
        cost = sum(lot["shares"] * lot["cost"] * rate for lot, (rate, _) in zip(p["lots"], lot_fx))
        rows.append({
            "ticker": t, "name": inf.get("name"), "shares": p["shares"], "avgCost": p["costTotal"] / p["shares"],
            "price": price, "currency": cur, "fx": fx, "value": value, "cost": cost, "pnl": value - cost,
            "pnlPct": (value / cost - 1) if cost else None, "dayPnl": p["shares"] * (price - prev) * fx,
            "dayPct": price / prev - 1 if prev else None, "sector": inf.get("sector") or inf.get("assetClass"),
            "region": inf.get("region"), "assetClass": inf.get("assetClass"),
            "dividendYield": inf.get("dividendYield") or 0, "income": _income(t, p["shares"], price, fx, inf), "lots": p["lots"],
            "bucket": bucket_of(t), "bucketDefault": default_bucket(t),
            "fxApprox": not all(exact for _, exact in lot_fx), "stale": data.is_stale(t), "asOf": close.index[-1].strftime("%Y-%m-%d"),
        })
        # Each day's exchange rate, so risk is what a holder in the base currency lived through, currency moves included
        rate = data.fx_series(cur, base, close.index)
        series[t] = close * p["shares"] * (rate if rate is not None else fx)

    total = sum(r["value"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)

    # What's already been realised, over positions both open and closed
    realised, flows, all_dated, unpriced = {}, [], True, []
    if not extra:
        realised, flows, all_dated, unpriced = _realised({t: p for t, p in book.items() if t not in missing}, base)
    for r in rows:
        r.update({k: realised.get(r["ticker"], {}).get(k, 0.0) for k in ("realised", "dividends")})
    closed = [c for t, c in realised.items() if not book[t]["shares"]]
    realised_total = sum(c["realised"] for c in realised.values())
    dividends_total = sum(c["dividends"] for c in realised.values())
    # The annual return, or the actual reason there isn't one
    annual, annual_why = None, None
    if missing or unpriced:
        annual_why = "Withheld until every holding has data"
    elif not all_dated:
        annual_why = "Needs a date on every purchase and sale"
    elif not flows:
        annual_why = "Needs a dated purchase"
    else:
        dated = flows + ([(dt.date.today(), total)] if total else [])
        span = (max(d for d, _ in dated) - min(d for d, _ in dated)).days
        # Not annualised under a year (the GIPS reporting standard): stretching a few months into a yearly rate
        # magnifies both gains and losses. Until then the page shows the return so far, not annualised.
        if span < 365:
            annual_why = "Shown after a year of history: annualising a shorter period magnifies gains and losses"
        else:
            annual = ledger.xirr(dated)
            if annual is None:
                annual_why = "Can't be worked out from these cash flows"
    for r in rows:
        r["weight"] = r["value"] / total if total else 0

    def alloc(key):
        out = defaultdict(float)
        for r in rows:
            out[r[key] or "Unknown"] += r["value"]
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    allocation = {"assetClass": alloc("assetClass"), "region": alloc("region"), "sector": alloc("sector"), "currency": alloc("currency")}
    allocation["bucket"] = {b.capitalize(): v for b, v in alloc("bucket").items()}

    # Looking through funds: their sectors, and the single stocks among their top holdings (Yahoo only lists 10, so that part is partial)
    through = defaultdict(float)
    for r in rows:
        if r["assetClass"] == "ETFs":
            f = data.fund(r["ticker"])
            for s, w in f["sectors"].items():
                through[s] += r["value"] * w
            through[NO_SECTOR] += r["value"] * (1 - sum(f["sectors"].values()))
            for h in f["holdings"]:
                r_held = next((x for x in rows if x["ticker"] == h["ticker"]), None)
                if r_held:
                    r_held["throughFunds"] = r_held.get("throughFunds", 0.0) + r["value"] * h["weight"]
        else:
            through[r["sector"] or "Unknown"] += r["value"]
    allocation["sectorLookThrough"] = dict(sorted(((k, v) for k, v in through.items() if v > 1e-6), key=lambda kv: -kv[1]))

    # Currencies, looking through funds: a broad fund counts in the currencies of what it holds (approximate index
    # weights), anything else in the currency it trades in (in its main unit, so pence count as pounds)
    by_cur, opened = defaultdict(float), []
    for r in rows:
        weights = FUND_CURRENCIES.get(r["ticker"])
        if weights:
            opened.append(r["ticker"])
        for cur, w in (weights or {data.main_unit(r["currency"]) or "Unknown": 1.0}).items():
            by_cur[cur] += r["value"] * w
    allocation["currencyLookThrough"] = dict(sorted(((k, v) for k, v in by_cur.items() if v > 1e-6), key=lambda kv: -kv[1]))
    allocation["currencyOpened"] = opened

    perf, risk = None, {}
    try:
        bench_close = data.history(bench_t, "1y")["Close"] if series else None
    except data.DataError:
        bench_close = None
    if series:
        # Risk describes today's holdings, as if held for the past year: it's about what you own now
        pv = _value_index(series)
        risk.update({"volatility": ind.volatility(pv), "maxDrawdown": ind.max_drawdown(pv), "sharpe": ind.sharpe(pv, rf), "sortino": ind.sortino(pv, rf)})
        if bench_close is not None:
            risk["beta"] = ind.beta(pv, bench_close.reindex(pv.index).ffill().bfill())
    actual = None if extra else _actual_performance(db.q("SELECT * FROM transactions"), base)
    if actual is not None:
        perf = {"dates": [d.strftime("%Y-%m-%d") for d in actual.index], "portfolio": [float(v * 100) for v in actual], "start": actual.index[0].strftime("%Y-%m-%d")}
        risk["return1y"] = float(actual.iloc[-1] - 1)
        if bench_close is not None:
            b = bench_close.reindex(actual.index).ffill().bfill()
            if b.notna().any():
                perf["benchmark"] = [float(v / b.iloc[0] * 100) for v in b]
                perf["benchmarkName"] = bench_t
                risk["benchmarkReturn"] = float(b.iloc[-1] / b.iloc[0] - 1)

    warnings = []
    for t, p in book.items():
        if p["shares"] > 0 and any(not lot["bought"] for lot in p["lots"]):
            cut = data.splits(t)
            if cut:
                warnings.append(f"{t} has split (latest {cut[-1][0]}, {cut[-1][1]:g} for 1), and it has a lot with no date, so that lot is counted "
                                f"as entered. If you bought it before the split, add the date so its shares and cost are restated.")
    for r in rows:
        if r["stale"]:
            warnings.append(f"{r['ticker']}'s price is old: Yahoo isn't responding, so this uses {r['asOf']}.")
        if data.is_stale_fx(r["currency"]):
            warnings.append(f"{r['ticker']}'s exchange rate is old: Yahoo isn't responding, so this uses the last known rate.")
    for t in unpriced:
        warnings.append(f"Realised figures leave out {t}, because there's no exchange rate for it right now.")
    for t, c in realised.items():
        if not c["exact"]:
            warnings.append(f"{t}'s realised figures use today's exchange rate where a date or a rate for that date is missing.")
    if series:
        earliest = min(s.index[0] for s in series.values())
        for t, s in series.items():
            if (s.index[0] - earliest).days > 30:
                warnings.append(f"{t} only has price history since {s.index[0]:%Y-%m-%d}, so it counts in the risk figures from then on.")
    for r in rows:
        if r["fxApprox"]:
            warnings.append(f"{r['ticker']} has a lot with no purchase date or no exchange rate for that date. Its cost uses today's rate, so its P&L leaves out currency moves on that lot.")
    # Weights are meaningless while a holding is missing from the total, so nothing is advised on them
    incomplete = bool(missing)
    # A broad index fund (VWCE, IWDA, an S&P 500 fund) is thousands of companies, so being most of the portfolio
    # isn't a single-position risk, and holding one isn't a lack of diversification. Sector funds and stocks are.
    for r in rows:
        with_funds = (r["value"] + r.get("throughFunds", 0.0)) / total if total else 0
        if with_funds > 0.20 and not incomplete and r["ticker"] not in CORE_FUNDS:
            detail = f", counting {r['throughFunds'] / total:.0%} through funds" if r.get("throughFunds") else ""
            warnings.append(f"{r['ticker']} is {with_funds:.0%} of the portfolio{detail} - single-position concentration above 20%.")
    for name, v in allocation["sectorLookThrough"].items():
        fund_part = any(r["assetClass"] == "ETFs" for r in rows)
        if fund_part and name != NO_SECTOR and total and v / total > 0.40 and len(rows) > 2 and not incomplete and allocation["sector"].get(name, 0) / total <= 0.40:
            warnings.append(f"{v / total:.0%} of value is in one sector ({name}), counting what funds hold.")
    for k, label in (("sector", "sector"), ("region", "region"), ("currencyLookThrough", "currency")):
        for name, v in allocation[k].items():
            if k == "sector" and name == "ETFs":  # a label for funds, not one sector: six broad funds aren't concentrated
                continue
            if total and v / total > (0.40 if k == "sector" else 0.80) and len(rows) > 2 and not incomplete:
                warnings.append(f"{v / total:.0%} of value is in one {label} ({name}).")
    if 0 < len(rows) < 5 and not incomplete and not any(r["ticker"] in CORE_FUNDS for r in rows):
        warnings.append(f"Only {len(rows)} position{'' if len(rows) == 1 else 's'} - diversification is limited.")
    if risk.get("maxDrawdown") is not None and risk["maxDrawdown"] < -0.25:
        warnings.append(f"Portfolio fell {abs(risk['maxDrawdown']):.0%} from peak in the past year (current holdings).")

    # Rebalancing. The first way offered is new money: how much more, put into what's under target, brings every
    # class to its target without selling anything (no fees or tax on sales). Selling is the alternative, not the
    # advice. Nothing reaches a target of 0% without selling, so then there's no new-money route.
    targets = settings.get("targets", {})
    rebalance, new_money = [], None
    if total and targets and not incomplete:
        tsum = sum(targets.values()) or 1
        classes = sorted(set(targets) | set(allocation["assetClass"]))
        tgt = {c: targets.get(c, 0) / tsum for c in classes}
        val = {c: allocation["assetClass"].get(c, 0) for c in classes}
        if not any(val[c] > 0 and tgt[c] == 0 for c in classes):
            new_money = max(0.0, max(val[c] / tgt[c] - total for c in classes if tgt[c] > 0 and val[c] > 0)) if any(val[c] > 0 for c in classes) else 0.0
        for c in classes:
            diff = tgt[c] - val[c] / total
            rebalance.append({"assetClass": c, "target": tgt[c], "actual": val[c] / total, "drift": -diff, "trade": diff * total,
                              "newMoney": max(0.0, tgt[c] * (total + new_money) - val[c]) if new_money is not None else None,
                              "action": "Add" if diff > 0.02 else ("Over target" if diff < -0.02 else "Hold")})

    # Core and satellite: the split against your target, who carries the risk, and whether the picks are paying off
    core_satellite = None
    if rows:
        target = settings.get("core_target", 80) / 100
        core = sum(r["value"] for r in rows if r["bucket"] == "core") / total if total else 0
        core_satellite = {"target": target, "core": core, "satellite": 1 - core, "drift": core - target,
                          "riskShare": _risk_shares(series, bucket_of) if len(series) > 1 else None}
        if not incomplete:  # the same rule as rebalancing: no amounts while a holding is missing from the total
            # More in core than the target is the lower-risk side: nothing needs selling, and new money could go to
            # satellite if you want any. Satellite over its share is the riskier side: then core is suggested.
            gap = target - core
            core_value = core * total
            core_satellite["action"] = "Hold" if abs(gap) <= 0.02 else ("To core" if gap > 0 else "Above core target")
            core_satellite["amount"] = abs(gap) * total  # what moving money would take
            core_satellite["newMoney"] = (core_value / target - total if gap < 0 and target > 0 else
                                          (total - core_value) / (1 - target) - total if gap > 0 and target < 1 else 0.0)
        if perf and not extra:
            core_satellite["performance"] = _bucket_performance(db.q("SELECT * FROM transactions"), base, bucket_of)

    income = sum(r["income"] for r in rows)
    return {
        "base": base, "positions": sorted(rows, key=lambda r: -r["value"]), "total": total, "cost": total_cost,
        "pnl": total - total_cost, "pnlPct": (total / total_cost - 1) if total_cost else None,
        # The latest day of each holding, which on a weekend is Friday for stocks but Sunday for crypto
        "dayPnl": sum(r["dayPnl"] for r in rows), "dayFrom": min((r["asOf"] for r in rows), default=None),
        "dayTo": max((r["asOf"] for r in rows), default=None), "allocation": allocation, "performance": perf, "risk": risk,
        "warnings": warnings, "rebalance": rebalance, "rebalanceNewMoney": new_money, "coreSatellite": core_satellite, "annualIncome": income, "errors": errors,
        "incomplete": incomplete, "missing": missing, "_series": series,
        "realised": realised_total, "dividends": dividends_total, "totalGain": total - total_cost + realised_total + dividends_total,
        "xirr": annual, "xirrWhy": annual_why, "closed": closed,
        # What you've gained against what you put in (every purchase, at its date's rate), not annualised
        "returnSoFar": (total - total_cost + realised_total + dividends_total) / invested if (invested := -sum(a for _, a in flows if a < 0)) else None,
        "note": "The chart follows what you actually held each day, at each day's FX rate, with dividends included in the prices. Trades themselves aren't gains. Lots with no date count as held from the start.",
    }


def _without_figures(warning):
    """A warning's wording without its percentages, so the same warning at a new level still matches."""
    return re.sub(r"\d+%", "", warning)


def pretrade(settings, ticker, amount=None, entry=None, stop=None, risk=None):
    """What buying `ticker` would do to the portfolio. A what-if only: nothing is saved."""
    base = settings.get("base_currency", "USD")
    inf = data.info(ticker)
    close = data.history(ticker, "1y")["Close"]
    fx = data.fx_rate(inf.get("currency"), base)
    price = entry if entry else float(close.iloc[-1])
    if price <= 0:
        raise ValueError("Entry price must be above zero.")
    if amount is not None and amount <= 0:
        raise ValueError("Amount must be above zero.")
    if stop is not None and stop >= price:
        raise ValueError("The stop must be below the entry price.")

    sized = None
    if risk is not None:
        if stop is None:
            raise ValueError("To size from risk, enter a stop price too.")
        if risk <= 0:
            raise ValueError("The most you'd lose must be above zero.")
        sized = risk / ((price - stop) * fx)
    if amount is None and sized is None:
        raise ValueError("Enter an amount, or a stop and the most you'd lose.")
    shares = amount / (price * fx) if amount is not None else sized

    today = dt.date.today().isoformat()
    before = summary(settings)
    after = summary(settings, {"ticker": ticker, "kind": "buy", "shares": shares, "price": price, "date": today})

    def weights(s):
        if s["incomplete"] or not s["total"]:
            return {"position": 0.0, "sector": 0.0, "region": 0.0, "currencyWeight": 0.0, "sectorLookThrough": 0.0, "currencyLookThrough": 0.0, "beta": s["risk"].get("beta")}
        share = lambda group, key: s["allocation"][group].get(key, 0.0) / s["total"]
        # Direct shares count a fund as one item ("ETFs", the currency it trades in); the look-through ones count
        # what funds hold, so a world fund's technology and dollar exposure are there before the trade too
        return {"position": next((r["weight"] for r in s["positions"] if r["ticker"] == ticker), 0.0),
                "sector": share("sector", inf.get("sector") or inf.get("assetClass")), "region": share("region", inf.get("region")),
                "currencyWeight": share("currency", inf.get("currency")), "beta": s["risk"].get("beta"),
                "sectorLookThrough": share("sectorLookThrough", inf.get("sector") or inf.get("assetClass")),
                "currencyLookThrough": share("currencyLookThrough", data.main_unit(inf.get("currency")))}

    incomplete = before["incomplete"] or after["incomplete"]
    correlations = []
    for t, s in before["_series"].items():
        c = ind.correlation(close, s) if t != ticker else None
        if c is not None:
            correlations.append({"ticker": t, "correlation": c})
    correlations.sort(key=lambda c: -c["correlation"])
    return {
        "ticker": ticker, "base": base, "currency": inf.get("currency"), "price": price, "shares": shares,
        "value": shares * price * fx, "maxLoss": shares * (price - stop) * fx if stop is not None else None,
        "sizedShares": sized, "sector": inf.get("sector") or inf.get("assetClass"), "region": inf.get("region"),
        "before": None if incomplete else weights(before), "after": None if incomplete else weights(after),
        "newWarnings": [w for w in after["warnings"] if _without_figures(w) not in {_without_figures(b) for b in before["warnings"]}],
        "correlations": correlations[:8], "incomplete": incomplete,
        # The whole sector mix counting what funds hold, so "how would my mix change" has its answer in the data
        "sectorMixBefore": None if incomplete or not before["total"] else {k: v / before["total"] for k, v in before["allocation"]["sectorLookThrough"].items()},
        "sectorMixAfter": None if incomplete or not after["total"] else {k: v / after["total"] for k, v in after["allocation"]["sectorLookThrough"].items()}, "missing": sorted(set(before["missing"]) | set(after["missing"])),
    }
