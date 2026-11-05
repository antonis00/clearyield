"""Higher-level analysis: stock snapshot, screener, comparison, DCF, alerts and the daily digest."""
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import data, db, scoring
from . import indicators as ind
from .universe import tickers_for


def evaluate_ticker(t, settings):
    full = data.history(t, "5y")
    inf = data.info(t)
    sc = scoring.evaluate(inf, full["Close"], settings.get("weights"))
    return full, inf, sc


def _quick_row(t, settings):
    try:
        full, inf, sc = evaluate_ticker(t, settings)
    except Exception as e:
        return {"ticker": t, "error": str(e)}
    c = full["Close"]
    return {
        "ticker": t, "name": inf.get("name"), "region": inf.get("region"), "assetClass": inf.get("assetClass"),
        "sector": inf.get("sector"), "currency": inf.get("currency"), "price": float(c.iloc[-1]),
        "day": ind.ret_over(c, 1), "ret1m": ind.ret_over(c, 21), "ret1y": ind.ret_over(c, 252),
        "pe": inf.get("trailingPE"), "pb": inf.get("priceToBook"), "divYield": inf.get("dividendYield"),
        "revGrowth": inf.get("revenueGrowth"), "roe": inf.get("returnOnEquity"), "de": inf.get("debtToEquity"),
        "marketCap": inf.get("marketCap"), "vol": ind.volatility(c), "rsi": float(ind.rsi(c).iloc[-1]),
        "composite": sc["composite"], "stance": sc["stance"], **{f"f_{k}": v for k, v in sc["factors"].items()},
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
        return (lt(r["pe"] if (r["pe"] or 0) > 0 else None, f.get("max_pe")) and gt(r["divYield"], f.get("min_div_yield"))
                and gt(r["revGrowth"], f.get("min_rev_growth")) and lt(r["de"], f.get("max_de")) and gt(r["roe"], f.get("min_roe"))
                and gt(r["composite"], f.get("min_score")) and gt(r.get("f_momentum"), f.get("min_momentum"))
                and lt(r["vol"], f.get("max_vol")))

    out = [r for r in ok if keep(r)]
    sort = f.get("sort", "composite")
    key = {"divYield": "divYield"}.get(sort, "composite" if sort == "composite" else f"f_{sort}")
    out.sort(key=lambda r: -(r.get(key) if r.get(key) is not None else -1))
    return {"rows": out, "failed": failed, "filters": {**f, "markets": markets}, "scanned": len(rows)}


def compare(tickers, period, rf=0.04):
    closes, meta = {}, []
    for t in tickers:
        try:
            h = data.history(t, period)
            inf = data.info(t)
        except data.DataError:
            continue
        closes[t] = h["Close"]
        st = ind.stats(data.history(t, "5y")["Close"], rf)
        meta.append({"ticker": t, "name": inf.get("name"), "currency": inf.get("currency"), "pe": inf.get("trailingPE"),
                     "divYield": inf.get("dividendYield"), "periodReturn": float(h["Close"].iloc[-1] / h["Close"].iloc[0] - 1), **st})
    if not closes:
        return {"series": {}, "meta": [], "correlation": None}
    df = pd.concat(closes, axis=1).sort_index().ffill().dropna()
    norm = (df / df.iloc[0] * 100).round(3)
    corr = df.pct_change().dropna().corr().round(2)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in norm.index],
        "series": {t: norm[t].tolist() for t in norm.columns}, "meta": meta,
        "correlation": {"tickers": list(corr.columns), "matrix": corr.values.tolist()},
    }


def dcf(inf, growth=None, years=5, discount=0.09, terminal=0.025, fcf=None):
    fcf = fcf if fcf is not None else inf.get("freeCashflow")
    shares = inf.get("sharesOutstanding")
    if not fcf or not shares or fcf <= 0:
        return {"error": "DCF needs positive free cash flow and share count (not available for this asset)."}
    if growth is None:
        g = inf.get("revenueGrowth")
        growth = float(np.clip(g if g is not None else 0.05, 0.0, 0.15))
    if discount <= terminal:
        return {"error": "Discount rate must be above terminal growth."}
    flows, pv = [], 0.0
    cf = fcf
    for yr in range(1, years + 1):
        cf *= 1 + growth
        d = cf / (1 + discount) ** yr
        pv += d
        flows.append({"year": yr, "fcf": cf, "pv": d})
    tv = cf * (1 + terminal) / (discount - terminal)
    tv_pv = tv / (1 + discount) ** years
    cash, debt = inf.get("totalCash") or 0, inf.get("totalDebt") or 0
    equity = pv + tv_pv + cash - debt
    fair = equity / shares
    # Fair value is in the financial-statement currency, which usually matches the quote currency.
    return {"fairValue": fair, "growth": growth, "years": years, "discount": discount, "terminal": terminal,
            "pvFlows": pv, "pvTerminal": tv_pv, "terminalShare": tv_pv / (pv + tv_pv), "netCash": cash - debt,
            "flows": flows, "startFcf": fcf}


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
                msg = f"{a['ticker']} moved {d:+.1%} today"
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


# ---------------------------------------------------------------- digest
def digest(settings):
    held = [r["ticker"] for r in db.q("SELECT DISTINCT ticker FROM holdings")]
    watched = [r["ticker"] for r in db.q("SELECT ticker FROM watchlist")]
    tickers = list(dict.fromkeys(held + watched))
    items = []
    rows = {r["ticker"]: r for r in rows_for(tickers, settings) if "error" not in r}
    for t, r in rows.items():
        tag = "holding" if t in held else "watchlist"
        if r["day"] is not None and abs(r["day"]) >= 0.03:
            items.append({"tone": 1 if r["day"] > 0 else -1, "ticker": t, "text": f"{t} ({tag}) moved {r['day']:+.1%} today."})
        if r["rsi"] >= 70:
            items.append({"tone": -1, "ticker": t, "text": f"{t} is overbought (RSI {r['rsi']:.0f})."})
        elif r["rsi"] <= 30:
            items.append({"tone": 1, "ticker": t, "text": f"{t} is oversold (RSI {r['rsi']:.0f})."})
        if r["stance"] in ("Weak",) and t in held:
            items.append({"tone": -1, "ticker": t, "text": f"{t} is a holding with a Weak composite score ({r['composite']}) - review the thesis."})
    today = dt.date.today().isoformat()
    for j in db.q("SELECT * FROM journal WHERE outcome='' OR outcome IS NULL"):
        r = rows.get(j["ticker"])
        if r:
            if j["stop_price"] and r["price"] <= j["stop_price"]:
                items.append({"tone": -1, "ticker": j["ticker"], "text": f"{j['ticker']} hit your thesis stop ({j['stop_price']}). Invalidation: {j['invalidation'] or 'n/a'}"})
            if j["target_price"] and r["price"] >= j["target_price"]:
                items.append({"tone": 1, "ticker": j["ticker"], "text": f"{j['ticker']} reached your target ({j['target_price']}). Time to re-evaluate."})
        if j["review_date"] and j["review_date"] <= today:
            items.append({"tone": 0, "ticker": j["ticker"], "text": f"Journal review due for {j['ticker']} ({j['action']} on {dt.date.fromtimestamp(j['ts']).isoformat()})."})
    for a in check_alerts(settings):
        items.append({"tone": 0, "ticker": a["ticker"], "text": "Alert: " + a["message"]})
    if not tickers:
        items.append({"tone": 0, "ticker": None, "text": "Add stocks to your watchlist or portfolio to get a personalised digest."})
    return {"items": items, "rows": list(rows.values())}
