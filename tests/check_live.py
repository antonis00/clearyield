"""Recompute key figures from raw Yahoo data, without any StockPilot calculation code, and compare them with what
the API returns. This found errors no other check could, such as Yahoo's unreliable non-USD FX pairs.

Needs internet. Uses its own temporary database:  python tests/check_live.py
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

os.environ["STOCKPILOT_DB"] = str(Path(tempfile.mkdtemp()) / "check.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

logging.disable(logging.WARNING)

def close(sym, start=None, period="5y"):
    h = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)["Close"].dropna()
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[~h.index.duplicated(keep="last")]

def at(s, date):
    return float(s[s.index <= pd.Timestamp(date)].iloc[-1])

ok = True
def check(name, app_v, raw_v, tol=0.005):
    global ok
    good = abs(app_v - raw_v) <= max(tol * abs(raw_v), 0.002)  # the floor covers prices moving between the two fetches
    ok &= good
    print(f"{'MATCH' if good else 'DIFF '} {name}: app {app_v:,.4f} | raw {raw_v:,.4f}")

with TestClient(app) as c:
    c.post("/api/settings", json={"base_currency": "EUR"})
    lots = [("AAPL", 10, 150, "2024-06-03"), ("AZN.L", 10, 11000, "2024-06-03"), ("QNBK.QA", 500, 15, "2024-06-03"), ("BTC-USD", 0.02, 60000, "2025-01-02"), ("NBK.KW", 100, 800, "2024-06-03")]
    for t, s, k, d in lots:
        assert c.post("/api/holdings", json={"ticker": t, "shares": s, "cost": k, "bought": d}).status_code == 200
    p = c.get("/api/portfolio").json()
    pos = {r["ticker"]: r for r in p["positions"]}

    eurusd = close("EURUSD=X", period="max"); gbpusd = close("GBPUSD=X", period="max"); kwdusd = close("KWDUSD=X", period="max")
    e_now = float(eurusd.iloc[-1]); g_now = float(gbpusd.iloc[-1]); k_now = float(kwdusd.iloc[-1])
    to_eur_now = {"USD": 1 / e_now, "GBp": g_now / 100 / e_now, "QAR": float(close("QARUSD=X", period="5d").iloc[-1]) / e_now, "KWF": k_now / 1000 / e_now}
    cur = {"AAPL": "USD", "AZN.L": "GBp", "QNBK.QA": "QAR", "BTC-USD": "USD", "NBK.KW": "KWF"}
    total = cost_total = 0
    for t, s, k, d in lots:
        px = float(close(t).iloc[-1])
        # Splits and bonus issues since the purchase (Kuwaiti banks give 5% bonus shares most years), straight from Yahoo
        split = 1.0
        for when, ratio in yf.Ticker(t).splits.items():
            if pd.Timestamp(when).tz_localize(None).normalize() > pd.Timestamp(d):
                split *= float(ratio)
        value = s * split * px * to_eur_now[cur[t]]
        e_then = at(eurusd, d)
        then = {"USD": 1 / e_then, "GBp": at(gbpusd, d) / 100 / e_then, "QAR": float(close("QARUSD=X", period="5d").iloc[-1]) / e_then, "KWF": at(kwdusd, d) / 1000 / e_then}[cur[t]]
        cost = s * k * then
        check(f"{t} value in EUR", pos[t]["value"], value)
        check(f"{t} cost at purchase-date FX", pos[t]["cost"], cost)
        total += value; cost_total += cost
    check("portfolio total", p["total"], total); check("portfolio cost", p["cost"], cost_total); check("portfolio P&L", p["pnl"], total - cost_total, 0.02)

    # Stats on Research
    for t in ["AAPL", "BTC-USD", "SAP.DE", "EMAAR.AE"]:
        s = c.get(f"/api/stock/{t}").json()["stats"]
        raw = close(t)
        last = raw.index[-1]
        one_y = raw[raw.index <= last - pd.Timedelta(days=365)].iloc[-1]
        check(f"{t} 1Y return", s["ret1y"], raw.iloc[-1] / one_y - 1, 0.01)
        w = raw[raw.index >= last - pd.Timedelta(days=365)]
        bars = (len(raw) - 1) / ((raw.index[-1] - raw.index[0]).days / 365.25)
        bars = 252 if 235 <= bars <= 270 else round(bars)
        check(f"{t} volatility", s["vol1y"], w.pct_change().dropna().std() * bars ** 0.5, 0.01)
        check(f"{t} max drawdown 1Y", s["maxDD1y"], (w / w.cummax() - 1).min(), 0.01)
        start = raw[raw.index >= last - pd.Timedelta(days=1826)]
        yrs = (start.index[-1] - start.index[0]).days / 365.25
        check(f"{t} CAGR", s["ret5yCagr"], (start.iloc[-1] / start.iloc[0]) ** (1 / yrs) - 1, 0.01)

    # A sale, and the performance line, from a fresh ledger: a EUR-based view of one USD stock
    for t in c.get("/api/transactions").json():
        assert c.delete(f"/api/transactions/{t['id']}").status_code == 200
    raw = close("AAPL")
    bought, sold = raw.index[-400].strftime("%Y-%m-%d"), raw.index[-100].strftime("%Y-%m-%d")
    assert c.post("/api/transactions", json={"ticker": "AAPL", "kind": "buy", "date": bought, "shares": 10, "price": 150}).status_code == 200
    assert c.post("/api/transactions", json={"ticker": "AAPL", "kind": "sell", "date": sold, "shares": 4, "price": 200}).status_code == 200
    assert c.post("/api/transactions", json={"ticker": "AAPL", "kind": "dividend", "date": sold, "amount": 10}).status_code == 200
    p2 = c.get("/api/portfolio").json()
    check("realised on a sale", p2["realised"], 4 * (200 / at(eurusd, sold) - 150 / at(eurusd, bought)))
    check("dividend at that day's rate", p2["dividends"], 10 / at(eurusd, sold))
    window = raw[raw.index >= raw.index[-1] - pd.Timedelta(days=365)]
    eur = eurusd.reindex(eurusd.index.union(window.index)).ffill().reindex(window.index)
    in_eur = window / eur
    held = pd.Series(10.0, index=window.index).where(window.index < pd.Timestamp(sold), 6.0).shift(1).fillna(10.0)
    daily = (held * in_eur).div((held * in_eur.shift(1))).dropna()
    check("performance over the year", p2["risk"]["return1y"], daily.prod() - 1, 0.01)

    # Looking through a fund, in the same base currency: AAPL's share of SPY, on top of AAPL held directly
    funds = yf.Ticker("SPY").funds_data
    assert c.post("/api/transactions", json={"ticker": "SPY", "kind": "buy", "date": bought, "shares": 5, "price": 400}).status_code == 200
    p3 = c.get("/api/portfolio").json()
    pos3 = {r["ticker"]: r for r in p3["positions"]}
    check("AAPL held through SPY", pos3["AAPL"]["throughFunds"], pos3["SPY"]["value"] * float(funds.top_holdings.loc["AAPL", "Holding Percent"]))
    check("Technology through SPY and AAPL", p3["allocation"]["sectorLookThrough"]["Technology"], pos3["SPY"]["value"] * funds.sector_weightings["technology"] + pos3["AAPL"]["value"])
    check("look-through adds up to the total", sum(p3["allocation"]["sectorLookThrough"].values()), p3["total"])
    fund_card = c.get("/api/stock/SPY/fund").json()
    check("SPY's yearly cost", fund_card["expenseRatio"], yf.Ticker("SPY").info["netExpenseRatio"] / 100)
    raw_earn = yf.Ticker("AAPL").earnings_history.iloc[-1]
    latest = c.get("/api/stock/AAPL/earnings").json()[0]
    check("latest earnings surprise", latest["surprise"], float(raw_earn["surprisePercent"]))
    # The published 52-week range, not one from adjusted prices
    check("52-week high", c.get("/api/stock/XOM").json()["stats"]["high52"], yf.Ticker("XOM").info["fiftyTwoWeekHigh"])

    # Weekly correlation, written out by hand, for the pair that looked low
    a, m = close("AAPL"), close("MSFT")
    j = pd.concat([a, m], axis=1, join="inner"); j = j[j.index >= j.index[-1] - pd.Timedelta(days=365)]
    wk = j.resample("W-FRI").last().pct_change().dropna()
    print("INFO AAPL/MSFT 1Y correlation: weekly", round(wk.corr().iloc[0, 1], 3), "| daily", round(j.pct_change().dropna().corr().iloc[0, 1], 3))
    r = c.post("/api/pretrade", json={"ticker": "MSFT", "amount": 1000}).json()
    print("INFO pre-trade MSFT vs AAPL:", [x for x in r["correlations"] if x["ticker"] == "AAPL"])
print("ALL MATCH" if ok else "SOME DIFFER")
sys.exit(0 if ok else 1)
