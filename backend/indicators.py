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
def ret_over(close, days):
    if len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - days] - 1)


def ytd_return(close):
    yr = close.index[-1].year
    prior = close[close.index.year < yr]
    if prior.empty:
        return None
    return float(close.iloc[-1] / prior.iloc[-1] - 1)


def volatility(close, days=252):
    r = close.pct_change().dropna().iloc[-days:]
    return float(r.std() * math.sqrt(TRADING_DAYS)) if len(r) > 20 else None


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
    ex = r - rf / TRADING_DAYS
    return float(ex.mean() / r.std() * math.sqrt(TRADING_DAYS))


def sortino(close, rf=0.04):
    r = close.pct_change().dropna()
    down = r[r < 0]
    if len(r) < 20 or len(down) < 5 or down.std() == 0:
        return None
    return float((r.mean() - rf / TRADING_DAYS) / down.std() * math.sqrt(TRADING_DAYS))


def beta(close, bench_close):
    a = pd.concat([close.pct_change(), bench_close.pct_change()], axis=1, join="inner").dropna()
    if len(a) < 30 or a.iloc[:, 1].var() == 0:
        return None
    return float(a.iloc[:, 0].cov(a.iloc[:, 1]) / a.iloc[:, 1].var())


def stats(close, rf=0.04):
    one_y = close.iloc[-252:]
    return {
        "ret1w": ret_over(close, 5), "ret1m": ret_over(close, 21), "ret3m": ret_over(close, 63),
        "ret6m": ret_over(close, 126), "ret1y": ret_over(close, 252), "ytd": ytd_return(close),
        "ret5yCagr": cagr(close.iloc[-1260:]) if len(close) > 500 else None,
        "vol1y": volatility(close), "maxDD1y": max_drawdown(one_y), "maxDD5y": max_drawdown(close.iloc[-1260:]),
        "sharpe1y": sharpe(one_y, rf), "sortino1y": sortino(one_y, rf),
    }
