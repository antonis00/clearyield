"""Simple single-asset strategy backtests with trading costs. A signal from a day's close trades at the next open."""
import numpy as np
import pandas as pd

from . import indicators as ind

STRATEGIES = {
    "buy_hold": {"label": "Buy & hold", "params": {}},
    "sma_cross": {"label": "Moving-average crossover", "params": {"fast": 50, "slow": 200}},
    "rsi_reversion": {"label": "RSI mean reversion", "params": {"period": 14, "buy_below": 30, "sell_above": 55}},
    "momentum": {"label": "Absolute momentum (time-series)", "params": {"lookback": 126}},
    "dca": {"label": "Monthly dollar-cost averaging", "params": {}},
}


def _positions(close, strategy, p):
    if strategy == "buy_hold":
        return pd.Series(1.0, index=close.index)
    if strategy == "sma_cross":
        fast, slow = ind.sma(close, int(p.get("fast", 50))), ind.sma(close, int(p.get("slow", 200)))
        return (fast > slow).astype(float).where(slow.notna(), 0.0)
    if strategy == "rsi_reversion":
        r = ind.rsi(close, int(p.get("period", 14)))
        lo, hi = float(p.get("buy_below", 30)), float(p.get("sell_above", 55))
        pos, state = [], 0.0
        for v in r:
            if v == v:
                if state == 0 and v < lo:
                    state = 1.0
                elif state == 1 and v > hi:
                    state = 0.0
            pos.append(state)
        return pd.Series(pos, index=close.index)
    if strategy == "momentum":
        lb = int(p.get("lookback", 126))
        return (close / close.shift(lb) - 1 > 0).astype(float).where(close.shift(lb).notna(), 0.0)
    raise ValueError(f"Unknown strategy {strategy}")


def _metrics(eq, rf, trades=None):
    return {"totalReturn": float(eq.iloc[-1] / eq.iloc[0] - 1), "cagr": ind.cagr(eq), "volatility": ind.volatility(eq, len(eq)),
            "sharpe": ind.sharpe(eq, rf), "maxDrawdown": ind.max_drawdown(eq), "trades": trades}


def run(df, strategy="sma_cross", params=None, cost_bps=10, rf=0.04, capital=10000, start=None):
    """Backtest from `start` (default: the first bar). Earlier bars of `df` only warm up the indicators."""
    params = params or {}
    full = df["Close"].dropna()
    close = full if start is None else full[full.index >= start]
    if len(close) < 60:
        raise ValueError("Not enough history for a backtest (need 60+ trading days).")
    rets = close.pct_change().fillna(0)
    bh = capital * (1 + rets).cumprod()

    if strategy == "dca":
        months = close.groupby(close.index.to_period("M")).head(1).index
        per = capital / len(months)
        units, cash_in, vals, invested = 0.0, 0.0, [], []
        buy_days = set(months)
        for d, px in close.items():
            if d in buy_days:
                units += per * (1 - cost_bps / 1e4) / px
                cash_in += per
            vals.append(units * px + (capital - cash_in))
            invested.append(cash_in)
        eq = pd.Series(vals, index=close.index)
        trades = len(months)
    else:
        held = _positions(full, strategy, params).shift(1).fillna(0)  # decided at a close, so held from the next day
        pos, before = held.reindex(close.index), held.shift(1).fillna(0).reindex(close.index)
        turnover = pos.diff().abs().fillna(pos.iloc[0])
        previous = close.shift(1)
        opens = df["Open"].reindex(close.index).fillna(previous) if "Open" in df else previous
        day = pos * rets
        day = day.mask(pos > before, close / opens - 1)  # bought at the open, so only that day's open-to-close counts
        day = day.mask(pos < before, opens / previous - 1)  # sold at the open, so only the overnight move counts
        day.iloc[0] = 0.0
        strat_rets = day - turnover * cost_bps / 1e4
        eq = capital * (1 + strat_rets).cumprod()
        trades = int((turnover > 0).sum())
        exposure = float(pos.mean())
    eq.iloc[0] = capital  # returns are measured from the starting capital, so a day-one entry cost still counts

    out = {
        "dates": [d.strftime("%Y-%m-%d") for d in close.index], "equity": [float(v) for v in eq],
        "buyHold": [float(v) for v in bh], "strategy": _metrics(eq, rf, trades), "benchmark": _metrics(bh, rf, 1),
        "label": STRATEGIES.get(strategy, {}).get("label", strategy),
        "caveats": [
            "Single-asset backtest on adjusted prices: excludes taxes, slippage beyond the cost setting, and dividends timing.",
            "Survivorship bias: you are testing a ticker that still exists today.",
            "Parameters tuned on the same history overfit easily - check other periods and tickers before trusting a result.",
        ],
    }
    if strategy != "dca":
        out["strategy"]["exposure"] = exposure
    return out
