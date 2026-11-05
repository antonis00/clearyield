"""Portfolio valuation, allocation, risk, warnings and rebalancing."""
import datetime as dt
import re
from collections import defaultdict

import pandas as pd

from . import data, db
from . import indicators as ind


def positions(extra=None):
    lots = db.q("SELECT * FROM holdings ORDER BY ticker, id") + ([extra] if extra else [])
    agg = defaultdict(lambda: {"shares": 0.0, "costTotal": 0.0, "lots": []})
    for lot in lots:
        a = agg[lot["ticker"]]
        a["shares"] += lot["shares"]
        a["costTotal"] += lot["shares"] * lot["cost"]
        a["lots"].append(lot)
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


def summary(settings, extra=None):
    """`extra` is a hypothetical lot, included in the figures but never saved."""
    base = settings.get("base_currency", "USD")
    rf = settings.get("risk_free_rate", 0.04)
    bench_t = settings.get("benchmark", "SPY")
    rows, series, errors, missing = [], {}, [], []
    for t, p in positions(extra).items():
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
            "fxApprox": not all(exact for _, exact in lot_fx),
        })
        series[t] = close * p["shares"] * fx

    total = sum(r["value"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    for r in rows:
        r["weight"] = r["value"] / total if total else 0

    def alloc(key):
        out = defaultdict(float)
        for r in rows:
            out[r[key] or "Unknown"] += r["value"]
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    allocation = {"assetClass": alloc("assetClass"), "region": alloc("region"), "sector": alloc("sector"), "currency": alloc("currency")}

    perf, risk = None, {}
    if series:
        pv = _value_index(series)
        try:
            bench = data.history(bench_t, "1y")["Close"].reindex(pv.index).ffill()
        except data.DataError:
            bench = None
        perf = {"dates": [d.strftime("%Y-%m-%d") for d in pv.index], "portfolio": [float(v / pv.iloc[0] * 100) for v in pv]}
        if bench is not None and bench.notna().any():
            b = bench.bfill()
            perf["benchmark"] = [float(v / b.iloc[0] * 100) for v in b]
            perf["benchmarkName"] = bench_t
            risk["beta"] = ind.beta(pv, b)
            risk["benchmarkReturn"] = float(b.iloc[-1] / b.iloc[0] - 1)
        risk.update({"return1y": float(pv.iloc[-1] / pv.iloc[0] - 1), "volatility": ind.volatility(pv),
                     "maxDrawdown": ind.max_drawdown(pv), "sharpe": ind.sharpe(pv, rf), "sortino": ind.sortino(pv, rf)})

    warnings = []
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
            if total and v / total > (0.40 if k == "sector" else 0.80) and len(rows) > 2 and not incomplete:
                warnings.append(f"{v / total:.0%} of value is in one {label} ({name}).")
    if 0 < len(rows) < 5 and not incomplete:
        warnings.append(f"Only {len(rows)} positions - diversification is limited.")
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
        "note": "The performance chart uses current holdings over the past year and today's FX rates (approximation). Cost and P&L use the FX rate on each lot's purchase date.",
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
    after = summary(settings, {"ticker": ticker, "shares": shares, "cost": price, "bought": today})

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
