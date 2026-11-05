"""Technical indicators and risk statistics."""
import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(s, n):
    return s.rolling(n, min_periods=n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s, fast=12, slow=26, signal=9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(s, n=20, k=2):
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std()
    return mid + k * sd, mid, mid - k * sd


def bundle(df):
    c = df["Close"]
    m, sig, hist = macd(c)
    bu, bm, bl = bollinger(c)
    return pd.DataFrame({
        "sma20": sma(c, 20), "sma50": sma(c, 50), "sma200": sma(c, 200), "rsi": rsi(c),
        "macd": m, "macdSignal": sig, "macdHist": hist, "bbUpper": bu, "bbMid": bm, "bbLower": bl,
    }, index=df.index)


# ---------------------------------------------------------------- risk / return stats
def _calendar_days(bars):
    return round(bars * 365.25 / TRADING_DAYS)


def window(obj, bars):
    """The last `bars` trading days' worth of a series or frame, measured in calendar time.

    Counting rows is wrong for real data: crypto has about 365 bars a year and UAE symbols about 208.
    """
    return obj[obj.index >= obj.index[-1] - pd.Timedelta(days=_calendar_days(bars))]


def bars_per_year(s):
    """Observed bars per year, used to annualise. Normal exchange calendars all count as 252."""
    years = (s.index[-1] - s.index[0]).days / 365.25 if len(s) >= 30 else 0
    if years <= 0:
        return TRADING_DAYS
    n = (len(s) - 1) / years
    return TRADING_DAYS if 235 <= n <= 270 else round(n)


def ret_over(close, days):
    """Return over `days` trading days' worth of calendar time (`days=1` is simply the previous bar)."""
    if days <= 1:
        return float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) > 1 else None
    base = close[close.index <= close.index[-1] - pd.Timedelta(days=_calendar_days(days))]
    return None if base.empty else float(close.iloc[-1] / base.iloc[-1] - 1)


def ytd_return(close):
    yr = close.index[-1].year
    prior = close[close.index.year < yr]
    if prior.empty:
        return None
    return float(close.iloc[-1] / prior.iloc[-1] - 1)


def volatility(close, days=252):
    w = window(close, days)
    r = w.pct_change().dropna()
    return float(r.std() * math.sqrt(bars_per_year(w))) if len(r) > 20 else None


def max_drawdown(close):
    if len(close) < 2:
        return None
    peak = close.cummax()
    return float((close / peak - 1).min())


def cagr(close):
    if len(close) < 2:
        return None
    years = (close.index[-1] - close.index[0]).days / 365.25
    if years <= 0 or close.iloc[0] <= 0:
        return None
    return float((close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1)


def sharpe(close, rf=0.04):
    r = close.pct_change().dropna()
    if len(r) < 20 or r.std() == 0:
        return None
    n = bars_per_year(close)
    ex = r - rf / n
    return float(ex.mean() / r.std() * math.sqrt(n))


def sortino(close, rf=0.04):
    r = close.pct_change().dropna()
    down = r[r < 0]
    if len(r) < 20 or len(down) < 5 or down.std() == 0:
        return None
    n = bars_per_year(close)
    return float((r.mean() - rf / n) / down.std() * math.sqrt(n))


def beta(close, bench_close):
    """Beta from weekly returns on shared dates. Same-day returns understate it for markets that
    close before the US does, because they only react to the US session on the next bar."""
    px = pd.concat([close, bench_close], axis=1, join="inner").dropna()
    if px.empty:
        return None
    weekly = px.resample("W-FRI").last().dropna().pct_change().dropna()
    a = weekly if len(weekly) >= 30 else px.pct_change().dropna()
    if len(a) < 30 or a.iloc[:, 1].var() == 0:
        return None
    return float(a.iloc[:, 0].cov(a.iloc[:, 1]) / a.iloc[:, 1].var())


def stats(close, rf=0.04):
    one_y = window(close, 252)
    return {
        "ret1w": ret_over(close, 5), "ret1m": ret_over(close, 21), "ret3m": ret_over(close, 63),
        "ret6m": ret_over(close, 126), "ret1y": ret_over(close, 252), "ytd": ytd_return(close),
        "ret5yCagr": cagr(window(close, 1260)) if len(close) > 500 else None,
        "vol1y": volatility(close), "maxDD1y": max_drawdown(one_y), "maxDD5y": max_drawdown(window(close, 1260)),
        "sharpe1y": sharpe(one_y, rf), "sortino1y": sortino(one_y, rf),
    }
