"""Market data access (Yahoo Finance via yfinance) with SQLite caching and an offline demo mode."""
import datetime as dt
import hashlib
import logging
import math
import os
import time

import numpy as np
import pandas as pd

from . import db
from . import indicators as ind
from .universe import currency_guess, name_of, region_of

log = logging.getLogger("stockpilot.data")

DEMO = os.getenv("STOCKPILOT_DEMO", "0") == "1"
TTL = {"history": 15 * 60, "info": 6 * 3600, "news": 30 * 60, "fin": 24 * 3600, "fx": 3600}
PERIOD_DAYS = {"1mo": 21, "3mo": 63, "6mo": 126, "ytd": None, "1y": 252, "2y": 504, "5y": 1260}


class DataError(Exception):
    pass


def _yf():
    import yfinance as yf
    return yf


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- history
def _df_to(df):
    return {"index": [d.strftime("%Y-%m-%d") for d in df.index],
            **{c: [None if pd.isna(v) else float(v) for v in df[c]] for c in df.columns}}


def _df_from(obj):
    idx = pd.to_datetime(obj.pop("index"))
    return pd.DataFrame(obj, index=idx).astype(float)


def history(ticker, period="5y"):
    """Daily OHLCV. Fetches 5y once (cached) and slices shorter periods from it."""
    fetch_period = "max" if period in ("max", "10y") else "5y"
    key = f"hist:{ticker}:{fetch_period}"
    cached = db.cache_get(key, TTL["history"])
    if cached is None:
        try:
            df = _demo_history(ticker) if DEMO else _fetch_history(ticker, fetch_period)
            db.cache_set(key, _df_to(df))
        except DataError:
            stale = db.cache_get_stale(key)
            if stale is None:
                raise
            df = _df_from(stale)
    else:
        df = _df_from(cached)
    return slice_period(df, period)


def _fetch_history(ticker, period):
    try:
        df = _yf().Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    except Exception as e:  # network / parsing errors
        raise DataError(f"Could not download prices for {ticker}: {e}")
    if df is None or df.empty:
        raise DataError(f"No price data for '{ticker}'. Check the Yahoo Finance symbol (e.g. QNBK.QA, SAP.DE, BTC-USD).")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["Close"])
    return df


def slice_period(df, period):
    if period in ("max", "10y") or df.empty:
        return df
    if period == "ytd":
        start = pd.Timestamp(dt.date.today().year, 1, 1)
        out = df[df.index >= start]
        return out if len(out) > 1 else df.iloc[-5:]
    return ind.window(df, PERIOD_DAYS.get(period, 252))


# ---------------------------------------------------------------- info / fundamentals
INFO_KEYS = [
    "longName", "shortName", "sector", "industry", "country", "currency", "quoteType", "exchange",
    "marketCap", "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda", "freeCashflow",
    "operatingCashflow", "totalRevenue", "profitMargins", "operatingMargins", "grossMargins",
    "returnOnEquity", "returnOnAssets", "debtToEquity", "currentRatio", "revenueGrowth", "earningsGrowth",
    "dividendYield", "payoutRatio", "beta", "sharesOutstanding", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "targetMeanPrice", "recommendationKey", "numberOfAnalystOpinions", "longBusinessSummary",
    "trailingEps", "forwardEps", "pegRatio", "totalCash", "totalDebt", "website", "financialCurrency",
    "impliedSharesOutstanding", "ebitda", "earningsTimestampStart", "exDividendDate",
]

# Quote currencies priced in a fraction of the main unit: (main currency, units per main unit)
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0), "KWF": ("KWD", 1000.0)}


def info(ticker):
    key = f"info:v5:{ticker}"  # bump when the stored fields change, so old entries are refetched
    cached = db.cache_get(key, TTL["info"])
    if cached is not None:
        return cached
    if DEMO:
        out = _demo_info(ticker)
    else:
        raw = {}
        try:
            raw = _yf().Ticker(ticker).info or {}
        except Exception as e:
            log.warning("info failed for %s: %s", ticker, e)
        # Yahoo answers a rate limit or an unknown quote with an almost empty dict. Caching that
        # would blank the fundamentals for hours, so fall back to old data and retry next time.
        if not any(raw.get(k) is not None for k in INFO_KEYS):
            return db.cache_get_stale(key) or _normalize_info(ticker, {k: None for k in INFO_KEYS})
        out = {k: raw.get(k) for k in INFO_KEYS}
        out = _normalize_info(ticker, out)
        if out["statementFx"] is None:
            return out  # retry next time rather than keeping the gap for hours
    db.cache_set(key, out)
    return out


def _normalize_info(ticker, d):
    for k, v in list(d.items()):
        if isinstance(v, (int, float)):
            d[k] = _num(v)
    # yfinance reports dividendYield in percent at every size (0.08 means 0.08%)
    dy = d.get("dividendYield")
    if dy is not None:
        d["dividendYield"] = dy / 100
    # debtToEquity is reported as a percentage (150 => 1.5x)
    de = d.get("debtToEquity")
    if de is not None:
        d["debtToEquity"] = de / 100
    d["name"] = d.get("longName") or d.get("shortName") or name_of(ticker)
    d["currency"] = d.get("currency") or currency_guess(ticker)
    d["financialCurrency"] = d.get("financialCurrency") or MINOR_UNITS.get(d["currency"], (d["currency"],))[0]
    d["statementFx"] = statement_fx(d)
    if d["statementFx"] != 1.0:
        d["enterpriseToEbitda"] = _ev_to_ebitda(d)
        d["priceToBook"] = _price_to_book(ticker, d)
    qt = d.get("quoteType") or ("CRYPTOCURRENCY" if ticker.endswith("-USD") else "EQUITY")
    d["quoteType"] = qt
    d["assetClass"] = asset_class(qt)
    d["region"] = region_of(ticker, qt)
    d["ticker"] = ticker
    return d


def _ev_to_ebitda(d):
    """EV/EBITDA with everything in the quote currency. Yahoo's own figure mixes currencies when the listing and
    the statements differ, so it can't be used then. Its P/E and forward EPS are already converted."""
    fx, mc, debt, cash, ebitda = d["statementFx"], d.get("marketCap"), d.get("totalDebt"), d.get("totalCash"), d.get("ebitda")
    if not fx or not mc or debt is None or cash is None or not ebitda or ebitda <= 0:
        return None
    return (mc + (debt - cash) * fx) / (ebitda * fx)


def _price_to_book(ticker, d):
    """Market cap over balance-sheet equity in one currency. Yahoo's book value per share is in the statement currency
    for some listings and the quote currency for others, so its ratio can't be trusted when the two differ."""
    fx, mc = d["statementFx"], d.get("marketCap")
    if DEMO or not fx or not mc:
        return None
    listing = _yf().Ticker(ticker)
    for sheet_name in ("quarterly_balance_sheet", "balance_sheet"):  # the latest quarter, or else the latest year
        try:
            values = getattr(listing, sheet_name).loc["Stockholders Equity"].dropna()
            equity = float(values.sort_index().iloc[-1])
        except Exception:
            continue
        return mc / (equity * fx) if equity > 0 else None
    log.warning("no balance sheet for %s", ticker)
    return None


def statement_fx(d):
    """Main quote-currency units per 1 unit of the currency the financial statements are in."""
    quote = MINOR_UNITS.get(d["currency"], (d["currency"],))[0]
    if d["financialCurrency"] == quote:
        return 1.0
    try:
        return fx_rate(d["financialCurrency"], "USD") / fx_rate(quote, "USD")
    except DataError as e:
        log.warning("%s: %s", d.get("ticker"), e)
        return None  # figures from the statements can't be compared with the price right now


def asset_class(quote_type):
    return {"EQUITY": "Stocks", "ETF": "ETFs", "MUTUALFUND": "ETFs", "CRYPTOCURRENCY": "Crypto"}.get(quote_type, "Other")


def financials(ticker):
    """Annual revenue / net income / free cash flow, oldest first."""
    key = f"fin:{ticker}"
    cached = db.cache_get(key, TTL["fin"])
    if cached is not None:
        return cached
    if DEMO:
        out = _demo_financials(ticker)
    else:
        out = []
        try:
            t = _yf().Ticker(ticker)
            inc, cf = t.income_stmt, t.cashflow

            def row(df, *names):
                if df is None or df.empty:
                    return {}
                for n in names:
                    if n in df.index:
                        return {c: _num(v) for c, v in df.loc[n].items()}
                return {}

            rev = row(inc, "Total Revenue", "Operating Revenue")
            ni = row(inc, "Net Income", "Net Income Common Stockholders")
            fcf = row(cf, "Free Cash Flow")
            cols = sorted(set(rev) | set(ni) | set(fcf))
            for c in cols:
                # Yahoo often adds an extra old year with every value empty
                if any(x.get(c) is not None for x in (rev, ni, fcf)):
                    out.append({"year": str(pd.Timestamp(c).year), "revenue": rev.get(c), "netIncome": ni.get(c), "fcf": fcf.get(c)})
        except Exception as e:
            log.warning("financials failed for %s: %s", ticker, e)
            return db.cache_get_stale(key) or []
    db.cache_set(key, out)
    return out


def news(ticker, limit=12):
    key = f"news:{ticker}"
    cached = db.cache_get(key, TTL["news"])
    if cached is not None:
        return cached
    if DEMO:
        out = _demo_news(ticker)
    else:
        out = []
        try:
            for item in (_yf().Ticker(ticker).news or [])[:limit]:
                c = item.get("content", item)
                title = c.get("title")
                if not title:
                    continue
                url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link")
                pub = (c.get("provider") or {}).get("displayName") or c.get("publisher")
                when = c.get("pubDate") or c.get("displayTime")
                if not when and c.get("providerPublishTime"):
                    when = dt.datetime.utcfromtimestamp(c["providerPublishTime"]).isoformat() + "Z"
                out.append({"title": title, "url": url, "publisher": pub, "time": when,
                            "summary": c.get("summary") or "", "sentiment": headline_sentiment(title + " " + (c.get("summary") or ""))})
        except Exception as e:
            log.warning("news failed for %s: %s", ticker, e)
            return db.cache_get_stale(key) or []
    db.cache_set(key, out)
    return out


POS_WORDS = {"beat", "beats", "surge", "surges", "soar", "soars", "record", "upgrade", "upgraded", "raises", "raised",
             "growth", "strong", "rally", "rallies", "jump", "jumps", "gain", "gains", "outperform", "buyback", "profit",
             "approval", "approved", "wins", "expands", "bullish", "tops", "higher"}
NEG_WORDS = {"miss", "misses", "plunge", "plunges", "drop", "drops", "fall", "falls", "downgrade", "downgraded", "cuts",
             "cut", "lawsuit", "probe", "investigation", "weak", "loss", "losses", "layoffs", "recall", "bearish",
             "slump", "slumps", "warning", "warns", "fraud", "fine", "fined", "lower", "decline", "declines", "sinks"}


def headline_sentiment(text):
    words = {w.strip(".,:;!?'\"()").lower() for w in text.split()}
    p, n = len(words & POS_WORDS), len(words & NEG_WORDS)
    if p == n:
        return 0
    return 1 if p > n else -1


# ---------------------------------------------------------------- FX
def fx_rate(currency, base="USD"):
    """Units of `base` per 1 unit of `currency`. Handles pence quotes (GBp, ZAc, ILA)."""
    if not currency:
        raise DataError("The currency of this asset isn't known.")
    currency, divisor = MINOR_UNITS.get(currency, (currency, 1.0))
    if currency == base:
        return 1.0 / divisor
    if "USD" not in (currency, base):  # Yahoo's USD pairs are the reliable ones: its KWD/EUR is about 1% off
        return fx_rate(currency, "USD") / fx_rate(base, "USD") / divisor
    key = f"fx:{currency}{base}"
    cached = db.cache_get(key, TTL["fx"])
    if cached is not None:
        return cached / divisor
    if DEMO:
        rate = DEMO_FX.get(currency, 1.0) / DEMO_FX.get(base, 1.0)
    else:
        rate = None
        for sym, invert in ((f"{currency}{base}=X", False), (f"{base}{currency}=X", True)):
            try:
                h = _yf().Ticker(sym).history(period="5d")
                if h is not None and not h.empty:
                    v = float(h["Close"].dropna().iloc[-1])
                    rate = 1 / v if invert else v
                    break
            except Exception:
                continue
        if rate is None:
            log.warning("FX %s->%s unavailable from Yahoo", currency, base)
            stale = db.cache_get_stale(key)
            if stale is not None:
                return stale / divisor
            if currency in USD_PEGGED and base in USD_PEGGED:
                return DEMO_FX[currency] / DEMO_FX[base] / divisor
            raise DataError(f"No exchange rate available for {currency} to {base}.")
    db.cache_set(key, rate)
    return rate / divisor


def fx_on(currency, base, date):
    """Units of `base` per 1 unit of `currency` on a past date, or None if Yahoo has no rate that old."""
    major, divisor = MINOR_UNITS.get(currency, (currency, 1.0))
    if major == base:
        return 1.0 / divisor
    if DEMO or fx_is_fixed(currency, base):  # a peg needs no history, and Yahoo's can be off by several percent
        return fx_rate(currency, base)
    if "USD" not in (major, base):  # as in fx_rate: its QAR/EUR history is about 4% off
        legs = fx_on(major, "USD", date), fx_on(base, "USD", date)
        return legs[0] / legs[1] / divisor if None not in legs else None
    for sym, invert in ((f"{major}{base}=X", False), (f"{base}{major}=X", True)):
        try:
            closes = history(sym, "max")["Close"]
        except DataError:
            continue
        closes = closes[closes.index <= pd.Timestamp(date)]
        if not closes.empty:
            v = float(closes.iloc[-1])
            return (1 / v if invert else v) / divisor
    return None


def fx_is_fixed(currency, base):
    """True when the rate between two currencies doesn't move, so the purchase date doesn't matter."""
    major = MINOR_UNITS.get(currency, (currency,))[0]
    return major == base or (major in USD_PEGGED and base in USD_PEGGED)


# USD value of one unit. These are approximate: demo mode uses them all, live mode only the pegged ones.
USD_PEGGED = {"USD", "QAR", "SAR", "AED", "BHD"}
DEMO_FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CHF": 1.13, "DKK": 0.145, "SEK": 0.095, "NOK": 0.093,
           "QAR": 0.2747, "SAR": 0.2666, "AED": 0.2723, "KWD": 3.25, "BHD": 2.6525, "JPY": 0.0067, "ZAR": 0.055, "ILS": 0.27}


# ---------------------------------------------------------------- demo data
def _seed(t):
    return int(hashlib.md5(t.encode()).hexdigest()[:8], 16)


def _demo_history(ticker):
    rng = np.random.default_rng(_seed(ticker))
    n = 1260
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    crypto = ticker.endswith("-USD")
    vol = rng.uniform(0.55, 0.85) if crypto else rng.uniform(0.14, 0.42)
    mu = rng.uniform(-0.06, 0.28)
    step = 1 / 252
    rets = rng.normal((mu - 0.5 * vol ** 2) * step, vol * math.sqrt(step), n)
    p0 = rng.uniform(5, 60000) if crypto else rng.uniform(15, 450)
    close = p0 * np.exp(np.cumsum(rets))
    opn = close * np.exp(rng.normal(0, vol * math.sqrt(step) / 3, n))
    high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    volume = rng.integers(1_000_000, 50_000_000, n).astype(float)
    return pd.DataFrame({"Open": opn, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


SECTORS = ["Technology", "Healthcare", "Financial Services", "Consumer Defensive", "Energy", "Industrials",
           "Communication Services", "Consumer Cyclical", "Utilities", "Real Estate", "Basic Materials"]


def _demo_info(ticker):
    rng = np.random.default_rng(_seed(ticker) + 1)
    h = _demo_history(ticker)
    price = float(h["Close"].iloc[-1])
    qt = "CRYPTOCURRENCY" if ticker.endswith("-USD") else ("ETF" if ticker in _etfs() else "EQUITY")
    d = {k: None for k in INFO_KEYS}
    d.update({"longName": name_of(ticker), "quoteType": qt, "currency": currency_guess(ticker),
              "fiftyTwoWeekHigh": float(h["High"].iloc[-252:].max()), "fiftyTwoWeekLow": float(h["Low"].iloc[-252:].min()),
              "longBusinessSummary": f"Demo data for {name_of(ticker)}. Prices and fundamentals are synthetic."})
    if qt == "EQUITY":
        shares = rng.uniform(2e8, 5e9)
        mcap = price / MINOR_UNITS.get(d["currency"], (None, 1.0))[1] * shares  # as on Yahoo: never in pence
        rev = mcap * rng.uniform(0.15, 1.5)
        margin = rng.uniform(-0.05, 0.35)
        d.update({
            "sector": SECTORS[_seed(ticker) % len(SECTORS)], "industry": "Demo industry", "country": region_of(ticker),
            "marketCap": mcap, "sharesOutstanding": shares, "totalRevenue": rev,
            "trailingPE": mcap / (rev * margin) if margin > 0.01 else None,
            "forwardPE": rng.uniform(8, 40), "priceToBook": rng.uniform(0.8, 14), "enterpriseToEbitda": rng.uniform(5, 30),
            "freeCashflow": rev * rng.uniform(-0.03, 0.28), "profitMargins": margin, "operatingMargins": margin + rng.uniform(0, 0.1),
            "grossMargins": rng.uniform(0.25, 0.75), "returnOnEquity": rng.uniform(-0.05, 0.45), "debtToEquity": rng.uniform(0.0, 2.5),
            "currentRatio": rng.uniform(0.6, 3.0), "revenueGrowth": rng.uniform(-0.08, 0.35), "earningsGrowth": rng.uniform(-0.2, 0.5),
            "dividendYield": rng.choice([0, 0, rng.uniform(0.005, 0.06)]), "payoutRatio": rng.uniform(0.1, 0.9),
            "beta": rng.uniform(0.5, 1.8), "targetMeanPrice": price * rng.uniform(0.85, 1.35),
            "recommendationKey": rng.choice(["buy", "hold", "strong_buy", "underperform"]), "numberOfAnalystOpinions": int(rng.integers(3, 45)),
            "earningsTimestampStart": time.time() + float(rng.integers(3, 80)) * 86400, "exDividendDate": time.time() + float(rng.integers(3, 80)) * 86400,
        })
    elif qt == "ETF":
        d.update({"dividendYield": rng.uniform(0.0, 0.04), "beta": rng.uniform(0.2, 1.2), "trailingPE": rng.uniform(12, 32)})
    # Same raw units as Yahoo: both of these are percentages until normalised
    pct = {k: d[k] * 100 for k in ("debtToEquity", "dividendYield") if d[k] is not None}
    return _normalize_info(ticker, {**d, **pct})


def _etfs():
    from .universe import UNIVERSE
    return set(UNIVERSE["ETFs"])


def _demo_financials(ticker):
    if ticker.endswith("-USD") or ticker in _etfs():
        return []
    rng = np.random.default_rng(_seed(ticker) + 2)
    rev = rng.uniform(5e9, 3e11)
    out, year = [], dt.date.today().year - 4
    for i in range(4):
        rev *= 1 + rng.uniform(-0.05, 0.25)
        m = rng.uniform(0.02, 0.3)
        out.append({"year": str(year + i), "revenue": rev, "netIncome": rev * m, "fcf": rev * m * rng.uniform(0.6, 1.3)})
    return out


def _demo_news(ticker):
    n = name_of(ticker)
    today = dt.datetime.utcnow()
    heads = [f"{n} beats quarterly expectations on strong demand", f"Analysts debate {n} valuation after rally",
             f"{n} faces regulatory probe in key market", f"{n} announces expanded buyback programme",
             f"Sector rotation weighs on {n} shares"]
    return [{"title": h, "url": None, "publisher": "Demo Wire", "time": (today - dt.timedelta(hours=7 * i)).isoformat() + "Z",
             "summary": "", "sentiment": headline_sentiment(h)} for i, h in enumerate(heads)]
