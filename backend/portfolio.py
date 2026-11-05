"""Portfolio valuation, allocation, risk, warnings and rebalancing."""
import datetime as dt
import re
from collections import defaultdict

import pandas as pd

from . import data, db, ledger
from . import indicators as ind


def positions(extra=None):
    """What's held now, from the ledger. `extra` is a hypothetical purchase, which is never saved."""
    agg = {}
    for t, p in ledger.replay(db.q("SELECT * FROM transactions") + ([extra] if extra else [])).items():
        agg[t] = {**p, "shares": sum(lot["shares"] for lot in p["lots"]), "costTotal": sum(lot["shares"] * lot["cost"] for lot in p["lots"])}
    return agg


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


def _actual_performance(rows, base):
    """Daily index of what was actually held: the shares at each day's close, carried to the next close, at each day's
    FX rate. Trades don't count as gains, and adjusted prices include dividends. None until something has been held overnight."""
    deltas = defaultdict(list)
    for r in rows:
        if r["kind"] != "dividend":
            deltas[r["ticker"]].append((r["date"], r["shares"] if r["kind"] == "buy" else -r["shares"]))
    prices = {}
    for t in deltas:
        try:
            close = data.history(t, "1y")["Close"]
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
            at, a = _sale_fx(cur, base, d["date"], fx)
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


def summary(settings, extra=None):
    """`extra` is a hypothetical lot, included in the figures but never saved."""
    base = settings.get("base_currency", "USD")
    rf = settings.get("risk_free_rate", 0.04)
    bench_t = settings.get("benchmark", "SPY")
    rows, series, errors, missing = [], {}, [], []
    book = positions(extra)
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
            "dividendYield": inf.get("dividendYield") or 0, "lots": p["lots"],
            "fxApprox": not all(exact for _, exact in lot_fx), "stale": data.is_stale(t), "asOf": close.index[-1].strftime("%Y-%m-%d"),
        })
        series[t] = close * p["shares"] * fx

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
    annual = None
    if all_dated and flows and not missing:
        annual = ledger.xirr(flows + ([(dt.date.today(), total)] if total else []))
    for r in rows:
        r["weight"] = r["value"] / total if total else 0

    def alloc(key):
        out = defaultdict(float)
        for r in rows:
            out[r[key] or "Unknown"] += r["value"]
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    allocation = {"assetClass": alloc("assetClass"), "region": alloc("region"), "sector": alloc("sector"), "currency": alloc("currency")}

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
    for r in rows:
        if r["weight"] > 0.20 and not incomplete:
            warnings.append(f"{r['ticker']} is {r['weight']:.0%} of the portfolio - single-position concentration above 20%.")
    for k, label in (("sector", "sector"), ("region", "region"), ("currency", "currency")):
        for name, v in allocation[k].items():
            if k == "sector" and name == "ETFs":  # a label for funds, not one sector: six broad funds aren't concentrated
                continue
            if total and v / total > (0.40 if k == "sector" else 0.80) and len(rows) > 2 and not incomplete:
                warnings.append(f"{v / total:.0%} of value is in one {label} ({name}).")
    if 0 < len(rows) < 5 and not incomplete:
        warnings.append(f"Only {len(rows)} position{'' if len(rows) == 1 else 's'} - diversification is limited.")
    if risk.get("maxDrawdown") is not None and risk["maxDrawdown"] < -0.25:
        warnings.append(f"Portfolio fell {abs(risk['maxDrawdown']):.0%} from peak in the past year (current holdings).")

    targets = settings.get("targets", {})
    rebalance = []
    if total and targets and not incomplete:
        tsum = sum(targets.values()) or 1
        classes = set(targets) | set(allocation["assetClass"])
        for c in sorted(classes):
            tgt = targets.get(c, 0) / tsum
            act = allocation["assetClass"].get(c, 0) / total
            diff = tgt - act
            rebalance.append({"assetClass": c, "target": tgt, "actual": act, "drift": act - tgt,
                              "trade": diff * total, "action": "Add" if diff > 0.02 else ("Trim" if diff < -0.02 else "Hold")})

    income = sum(r["value"] * r["dividendYield"] for r in rows)
    return {
        "base": base, "positions": sorted(rows, key=lambda r: -r["value"]), "total": total, "cost": total_cost,
        "pnl": total - total_cost, "pnlPct": (total / total_cost - 1) if total_cost else None,
        "dayPnl": sum(r["dayPnl"] for r in rows), "allocation": allocation, "performance": perf, "risk": risk,
        "warnings": warnings, "rebalance": rebalance, "annualIncome": income, "errors": errors,
        "incomplete": incomplete, "missing": missing, "_series": series,
        "realised": realised_total, "dividends": dividends_total, "totalGain": total - total_cost + realised_total + dividends_total,
        "xirr": annual, "closed": closed,
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
            return {"position": 0.0, "sector": 0.0, "region": 0.0, "currencyWeight": 0.0, "beta": s["risk"].get("beta")}
        share = lambda group, key: s["allocation"][group].get(key, 0.0) / s["total"]
        return {"position": next((r["weight"] for r in s["positions"] if r["ticker"] == ticker), 0.0),
                "sector": share("sector", inf.get("sector") or inf.get("assetClass")), "region": share("region", inf.get("region")),
                "currencyWeight": share("currency", inf.get("currency")), "beta": s["risk"].get("beta")}

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
        "correlations": correlations[:8], "incomplete": incomplete, "missing": sorted(set(before["missing"]) | set(after["missing"])),
    }
