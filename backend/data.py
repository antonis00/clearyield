"""Market data access (Yahoo Finance via yfinance) with SQLite caching and an offline demo mode."""
import datetime as dt
import hashlib
import logging
import math
import os
import re
import time

import numpy as np
import pandas as pd

from . import db
from . import indicators as ind
from .universe import MACRO, currency_guess, name_of, region_of

log = logging.getLogger("stockpilot.data")

DEMO = os.getenv("STOCKPILOT_DEMO", "0") == "1"
TTL = {"history": 15 * 60, "info": 6 * 3600, "news": 30 * 60, "fin": 24 * 3600, "fx": 3600}
PERIOD_DAYS = {"1mo": 21, "3mo": 63, "6mo": 126, "ytd": None, "1y": 252, "2y": 504, "5y": 1260}


class DataError(Exception):
    pass


# What's currently being served from old data because Yahoo failed
_stale, _stale_fx = set(), set()


def is_stale(ticker):
    return ticker in _stale


def is_stale_fx(currency):
    return MINOR_UNITS.get(currency, (currency,))[0] in _stale_fx


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
def _df_to(df, fmt="%Y-%m-%d"):
    return {"index": [d.strftime(fmt) for d in df.index],
            **{c: [None if pd.isna(v) else float(v) for v in df[c]] for c in df.columns}}


def _df_from(obj):
    idx = pd.to_datetime(obj.pop("index"))
    return pd.DataFrame(obj, index=idx).astype(float)


_SYMBOL = re.compile(r"[A-Za-z0-9^=.\-&]{1,24}")


def history(ticker, period="5y"):
    """Daily OHLCV. Fetches 5y once (cached) and slices shorter periods from it."""
    if not _SYMBOL.fullmatch(ticker or ""):  # a sentence or stray text: never sent to Yahoo as a symbol
        raise DataError(f"'{ticker[:60]}' isn't a ticker symbol. Search by company name, or ask the Screener a question.")
    fetch_period = "max" if period in ("max", "10y") else "5y"
    key = f"hist:{ticker}:{fetch_period}"
    cached = db.cache_get(key, TTL["history"])
    if cached is None:
        try:
            df = _demo_history(ticker) if DEMO else _fetch_history(ticker, fetch_period)
            db.cache_set(key, _df_to(df))
            _stale.discard(ticker)
        except DataError:
            stale = db.cache_get_stale(key)
            if stale is None:
                raise
            df = _df_from(stale)
            _stale.add(ticker)
    else:
        df = _df_from(cached)
        _stale.discard(ticker)
    return slice_period(df, period)


def _fetch_history(ticker, period):
    try:
        df = _yf().Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    except Exception as e:  # network or parsing errors: the detail goes to the log, a plain sentence to the page
        log.warning("prices failed for %s: %s", ticker, e)
        raise DataError(f"Yahoo didn't return prices for {ticker}. Check the symbol, or try again in a minute.")
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


# Markets whose trading week runs Sunday to Thursday. The UAE moved to Monday to Friday in 2022.
SUN_THU = (".SR", ".QA", ".KW", ".BH", ".OM")


def weekly(view, lines, ticker=""):
    """One candle per week, built from the daily ones and dated by the week's first trading day. The indicator
    lines keep their daily meaning (SMA 50 is still 50 days) and take each week's last daily value. Weeks run Monday
    to Sunday, or Friday to Thursday where trading runs Sunday to Thursday, so a Sunday opens its week there."""
    days = pd.Series(view.index, index=view.index)
    week = view.index.to_period("W-THU" if ticker.endswith(SUN_THU) else "W")
    g = view.groupby(week)
    first, last = days.groupby(week).first(), days.groupby(week).last()
    candles = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                            "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    candles.index = pd.DatetimeIndex(first.values)
    lines = lines.loc[last.values]
    lines.index = candles.index
    return candles, lines


# Ranges drawn with candles from within the day: range -> candle size. Yahoo keeps these for recent days only.
INTRADAY = {"1d": "5m", "5d": "15m"}


def intraday(ticker, period):
    """The latest session (1D) or the last five (5D), in the exchange's own clock."""
    interval = INTRADAY[period]
    key = f"intra:{ticker}:{interval}"
    cached = db.cache_get(key, 5 * 60)
    if cached is None:
        try:
            df = _demo_intraday(ticker, interval) if DEMO else _fetch_intraday(ticker, interval)
            db.cache_set(key, _df_to(df, "%Y-%m-%dT%H:%M"))
        except DataError:
            cached = db.cache_get_stale(key)
            if cached is None:
                raise
            df = _df_from(cached)
    else:
        df = _df_from(cached)
    if period == "1d" and df.index.hour.nunique() == 24:  # trades around the clock, like crypto: the last 24 hours
        return df[df.index > df.index[-1] - pd.Timedelta(hours=24)]
    sessions = sorted(set(df.index.normalize()))[-(1 if period == "1d" else 5):]
    return df[df.index.normalize().isin(sessions)]


def _fetch_intraday(ticker, interval):
    try:
        df = _yf().Ticker(ticker).history(period="5d", interval=interval, auto_adjust=True)
    except Exception as e:
        raise DataError(f"Could not download intraday prices for {ticker}: {e}")
    if df is None or df.empty:
        raise DataError(f"Yahoo has no intraday prices for {ticker}.")
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize(None) if idx.tz is not None else idx  # drops the zone, keeps the exchange's wall-clock time
    return df[~df.index.duplicated(keep="last")]


# ---------------------------------------------------------------- info / fundamentals
INFO_KEYS = [
    "longName", "shortName", "sector", "industry", "currency", "quoteType",
    "marketCap", "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda", "freeCashflow",
    "totalRevenue", "profitMargins", "operatingMargins",
    "returnOnEquity", "debtToEquity", "currentRatio", "revenueGrowth", "earningsGrowth",
    "dividendYield", "payoutRatio", "beta", "sharesOutstanding", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "targetMeanPrice", "recommendationKey", "numberOfAnalystOpinions", "longBusinessSummary",
    "trailingEps", "forwardEps", "pegRatio", "totalCash", "totalDebt", "financialCurrency",
    "impliedSharesOutstanding", "ebitda", "earningsTimestampStart", "exDividendDate", "netExpenseRatio",
]

# Quote currencies priced in a fraction of the main unit: (main currency, units per main unit)
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0), "KWF": ("KWD", 1000.0)}


def main_unit(currency):
    """The currency a payment is made in: pounds for a stock quoted in pence, dinars for one quoted in fils."""
    return MINOR_UNITS.get(currency, (currency,))[0]


# Yahoo gives these a currency, but they're a yield in percent (4.1 means 4.1%), not a price
YIELD_INDEXES = ("^TNX", "^TYX", "^FVX", "^IRX")


def price_unit(ticker, quote_type):
    """How a price should read: "%" for a yield, "points" for an index, None for a price in its currency."""
    if ticker in YIELD_INDEXES:
        return "%"
    return "points" if quote_type == "INDEX" or ticker.startswith("^") else None


def info(ticker):
    out = _info(ticker)
    return {**out, "priceUnit": price_unit(ticker, out.get("quoteType"))}


def _info(ticker):
    key = f"info:v8:{ticker}"  # bump when the stored fields change, so old entries are refetched
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
    if d.get("netExpenseRatio") is not None:  # also a percentage
        d["netExpenseRatio"] /= 100
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
        if not out:  # funds have none, but Yahoo also answers a rate limit this way: keep what's known, and retry within the hour
            old = db.cache_get_stale(key)
            if old:
                return old
            db.cache_set(key, out, ts=time.time() - TTL["fin"] + 3600)
            return out
    db.cache_set(key, out)
    return out


def earnings_record(ticker):
    """Reported against expected earnings per share in recent quarters, newest first."""
    key = f"earn:{ticker}"
    cached = db.cache_get(key, TTL["fin"])
    if cached is not None:
        return cached
    if DEMO:
        return _demo_earnings(ticker)
    try:
        hist = _yf().Ticker(ticker).earnings_history
        out = [{"quarter": pd.Timestamp(q).strftime("%Y-%m-%d"), "actual": _num(r["epsActual"]), "estimate": _num(r["epsEstimate"]), "surprise": _num(r["surprisePercent"])}
               for q, r in hist.iterrows()][::-1] if hist is not None and not hist.empty else []
    except Exception as e:  # Yahoo has none for funds, and answers with an error
        log.info("no earnings history for %s: %s", ticker, e)
        out = []
    if not out:  # none, or a failure: keep what's known, and ask again within the hour
        old = db.cache_get_stale(key)
        if old:
            return old
    db.cache_set(key, out, ts=None if out else time.time() - TTL["fin"] + 3600)
    return out


FUND_SECTORS = {"realestate": "Real Estate", "consumer_cyclical": "Consumer Cyclical", "basic_materials": "Basic Materials",
                "consumer_defensive": "Consumer Defensive", "technology": "Technology", "communication_services": "Communication Services",
                "financial_services": "Financial Services", "utilities": "Utilities", "industrials": "Industrials", "energy": "Energy", "healthcare": "Healthcare"}


def splits(ticker):
    """[(date, ratio)], oldest first: 10.0 for a 10-for-1 split, 0.1 for a 1-for-10 reverse split. Yahoo's prices are
    already adjusted for these, so trades recorded before a split have to be restated to match them."""
    if DEMO:
        return []
    key = f"splits:{ticker}"
    cached = db.cache_get(key, 24 * 3600)
    if cached is not None:
        return [tuple(x) for x in cached]
    try:
        raw = _yf().Ticker(ticker).splits
        out = [(pd.Timestamp(d).strftime("%Y-%m-%d"), float(r)) for d, r in raw.items() if r and float(r) > 0 and float(r) != 1.0]
    except Exception as e:
        log.warning("splits failed for %s: %s", ticker, e)
        stale = db.cache_get_stale(key)
        return [tuple(x) for x in stale] if stale else []  # not cached, so it's asked again next time
    db.cache_set(key, out)
    return out


def dividends_past_year(ticker):
    """Dividends per share paid in the past 365 days, in the quote currency, or None if Yahoo didn't answer.
    What was actually paid: 0 for a fund that reinvests (accumulates) its income, and right for a payer that Yahoo
    gives no yield for. Yahoo's dividends are split-adjusted, like its prices."""
    if DEMO:
        return None
    key = f"divs:{ticker}"
    cached = db.cache_get(key, 24 * 3600)
    if cached is None:
        try:
            raw = _yf().Ticker(ticker).dividends
            idx = pd.to_datetime(raw.index)
            idx = idx.tz_localize(None) if idx.tz is not None else idx
            cached = [[d.strftime("%Y-%m-%d"), float(v)] for d, v in zip(idx, raw) if v and float(v) > 0]
        except Exception as e:
            log.warning("dividends failed for %s: %s", ticker, e)
            cached = db.cache_get_stale(key)
            if cached is None:
                return None  # not cached, so it's asked again next time
        else:
            db.cache_set(key, cached)
    since = (pd.Timestamp.today().normalize() - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    return sum(v for d, v in cached if d > since)


def dividend_factor(ticker):
    """Yahoo's dividend-adjusted price over the plain price, by date (1.0 today, lower before each dividend), or None.
    Multiplying a price paid on a date by it puts that price on the same footing as the adjusted prices the app uses,
    so a return measured from it includes the dividends since, like the benchmark's."""
    if DEMO:
        return None
    key = f"divfactor:{ticker}"
    cached = db.cache_get(key, TTL["history"])
    if cached is None:
        try:
            raw = _yf().Ticker(ticker).history(period="max", interval="1d", auto_adjust=False)
            ratio = (raw["Adj Close"] / raw["Close"]).dropna()
            idx = pd.to_datetime(ratio.index)
            ratio.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
            if ratio.empty:
                return None
            cached = {"index": [d.strftime("%Y-%m-%d") for d in ratio.index], "ratio": [float(v) for v in ratio]}
            db.cache_set(key, cached)
        except Exception as e:
            log.warning("dividend factor failed for %s: %s", ticker, e)
            cached = db.cache_get_stale(key)
            if cached is None:
                return None
    return pd.Series(cached["ratio"], index=pd.to_datetime(cached["index"]))


def fund(ticker):
    """A fund's sector split and top holdings. Yahoo only gives the top 10, and nothing for bond or gold funds."""
    key = f"fund:{ticker}"
    cached = db.cache_get(key, 7 * 24 * 3600)
    if cached is not None:
        return cached
    if DEMO:
        return _demo_fund(ticker)
    try:
        f = _yf().Ticker(ticker).funds_data
        sectors = {FUND_SECTORS[k]: _num(v) for k, v in (f.sector_weightings or {}).items() if k in FUND_SECTORS and _num(v)}
        top = f.top_holdings
        holdings = [{"ticker": str(sym), "name": r["Name"], "weight": _num(r["Holding Percent"])} for sym, r in top.iterrows()] if top is not None else []
    except Exception as e:
        log.info("no fund data for %s: %s", ticker, e)
        return db.cache_get_stale(key) or {"sectors": {}, "holdings": []}
    out = {"sectors": sectors, "holdings": holdings}
    if not sectors and not holdings:
        # Bond and gold funds have none, but Yahoo answers a rate limit the same way: keep what's known, and ask
        # again within the hour instead of a week
        old = db.cache_get_stale(key)
        if old and (old["sectors"] or old["holdings"]):
            return old
        db.cache_set(key, out, ts=time.time() - 7 * 24 * 3600 + 3600)
        return out
    db.cache_set(key, out)
    return out


SEARCH_TYPES = {"EQUITY": "Stock", "ETF": "ETF", "MUTUALFUND": "Fund", "CRYPTOCURRENCY": "Crypto", "INDEX": "Index"}


def search_symbols(text):
    """Yahoo's own symbol search, for anything outside the built-in list. European funds, for example, are only
    listed with an exchange suffix: VWCE doesn't exist, but VWCE.DE does."""
    text = text.strip().lower()
    if DEMO or len(text) < 2:
        return []
    key = f"search:{text}"
    cached = db.cache_get(key, 24 * 3600)
    if cached is not None:
        return cached
    try:
        quotes = _yf().Search(text, max_results=10, news_count=0, timeout=5).quotes
    except Exception as e:
        log.warning("symbol search failed for %s: %s", text, e)
        return db.cache_get_stale(key) or []
    out = [{"ticker": q["symbol"], "name": q.get("longname") or q.get("shortname") or q["symbol"],
            "market": f"{SEARCH_TYPES[q['quoteType']]} · {q.get('exchange', '')}"} for q in quotes if q.get("symbol") and q.get("quoteType") in SEARCH_TYPES]
    # Nothing found is kept for an hour, not a day: Yahoo answers a rate limit with an empty list too
    db.cache_set(key, out, ts=None if out else time.time() - 24 * 3600 + 3600)
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
                            "summary": c.get("summary") or ""})
        except Exception as e:
            log.warning("news failed for %s: %s", ticker, e)
            return db.cache_get_stale(key) or []
    db.cache_set(key, out)
    return out


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
                _stale_fx.add(currency)
                return stale / divisor
            if currency in USD_PEGGED and base in USD_PEGGED:
                return DEMO_FX[currency] / DEMO_FX[base] / divisor
            raise DataError(f"No exchange rate available for {currency} to {base}.")
    db.cache_set(key, rate)
    _stale_fx.discard(currency)
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


def fx_series(currency, base, index):
    """Units of `base` per 1 unit of `currency` on each date of `index`, or None if Yahoo has no history for the pair."""
    major, divisor = MINOR_UNITS.get(currency, (currency, 1.0))
    if major == base or DEMO or fx_is_fixed(currency, base):
        return pd.Series(fx_rate(currency, base), index=index)
    if "USD" not in (major, base):
        legs = fx_series(major, "USD", index), fx_series(base, "USD", index)
        return legs[0] / legs[1] / divisor if legs[0] is not None and legs[1] is not None else None
    for sym, invert in ((f"{major}{base}=X", False), (f"{base}{major}=X", True)):
        try:
            closes = history(sym, "5y")["Close"]
        except DataError:
            continue
        rates = (1 / closes if invert else closes) / divisor
        return rates.reindex(rates.index.union(index)).ffill().bfill().reindex(index)
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


def _demo_intraday(ticker, interval):
    """Five sessions of 09:30-16:00 candles that wander from each demo day's open to its close."""
    minutes = int(interval.rstrip("m"))
    n = 390 // minutes
    rng = np.random.default_rng(_seed(ticker) + minutes)
    frames = []
    for day, row in _demo_history(ticker).iloc[-5:].iterrows():
        walk = np.cumsum(rng.normal(0, 1, n + 1))
        bridge = walk - np.linspace(0, 1, n + 1) * walk[-1]  # starts and ends at zero
        path = np.linspace(row["Open"], row["Close"], n + 1) + bridge * abs(row["Close"]) * 0.0015
        opn, close = path[:-1], path[1:]
        wiggle = np.abs(rng.normal(0, abs(row["Close"]) * 0.0008, n))
        idx = pd.date_range(day + pd.Timedelta(hours=9, minutes=30), periods=n, freq=f"{minutes}min")
        frames.append(pd.DataFrame({"Open": opn, "High": np.maximum(opn, close) + wiggle, "Low": np.minimum(opn, close) - wiggle,
                                    "Close": close, "Volume": rng.integers(20_000, 400_000, n).astype(float)}, index=idx))
    return pd.concat(frames)


SECTORS = ["Technology", "Healthcare", "Financial Services", "Consumer Defensive", "Energy", "Industrials",
           "Communication Services", "Consumer Cyclical", "Utilities", "Real Estate", "Basic Materials"]


def _demo_info(ticker):
    rng = np.random.default_rng(_seed(ticker) + 1)
    h = _demo_history(ticker)
    price = float(h["Close"].iloc[-1])
    qt = "CRYPTOCURRENCY" if ticker.endswith("-USD") else "ETF" if ticker in _etfs() else "INDEX" if ticker in MACRO else "EQUITY"
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
            "sector": SECTORS[_seed(ticker) % len(SECTORS)], "industry": "Banks - Diversified" if SECTORS[_seed(ticker) % len(SECTORS)] == "Financial Services" else "Demo industry",
            "marketCap": mcap, "sharesOutstanding": shares, "totalRevenue": rev,
            "trailingPE": mcap / (rev * margin) if margin > 0.01 else None,
            "forwardPE": rng.uniform(8, 40), "priceToBook": rng.uniform(0.8, 14), "enterpriseToEbitda": rng.uniform(5, 30),
            "freeCashflow": rev * rng.uniform(-0.03, 0.28), "profitMargins": margin, "operatingMargins": margin + rng.uniform(0, 0.1),
            "returnOnEquity": rng.uniform(-0.05, 0.45), "debtToEquity": rng.uniform(0.0, 2.5),
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


def _demo_earnings(ticker):
    if ticker.endswith("-USD") or ticker in _etfs() or ticker in MACRO:
        return []
    rng = np.random.default_rng(_seed(ticker) + 3)
    end = pd.Timestamp.today().to_period("Q").start_time - pd.Timedelta(days=1)
    out = []
    for i in range(4):
        est = float(rng.uniform(0.5, 4))
        act = est * float(rng.uniform(0.85, 1.2))
        out.append({"quarter": (end - pd.DateOffset(months=3 * i)).strftime("%Y-%m-%d"), "actual": round(act, 2), "estimate": round(est, 2), "surprise": act / est - 1})
    return out


def _demo_fund(ticker):
    if ticker not in _etfs():
        return {"sectors": {}, "holdings": []}
    rng = np.random.default_rng(_seed(ticker) + 4)
    names = list(FUND_SECTORS.values())
    weights = rng.dirichlet(np.ones(len(names)))
    picks = rng.choice(["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "JPM", "XOM", "LLY"], 5, replace=False)
    return {"sectors": {n: float(w) for n, w in zip(names, weights)},
            "holdings": [{"ticker": str(p), "name": name_of(str(p)), "weight": float(rng.uniform(0.01, 0.08))} for p in picks]}


def _demo_news(ticker):
    n = name_of(ticker)
    today = dt.datetime.utcnow()
    heads = [f"{n} beats quarterly expectations on strong demand", f"Analysts debate {n} valuation after rally",
             f"{n} faces regulatory probe in key market", f"{n} announces expanded buyback programme",
             f"Sector rotation weighs on {n} shares"]
    return [{"title": h, "url": None, "publisher": "Demo Wire", "time": (today - dt.timedelta(hours=7 * i)).isoformat() + "Z",
             "summary": ""} for i, h in enumerate(heads)]
