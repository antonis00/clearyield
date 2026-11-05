"""Transparent factor scoring (0-100) and plain-language signals.

Every score is built from named metrics with explicit thresholds, so the UI can always show *why*
a stock scores the way it does. Scores are decision aids, not predictions.
"""
from . import indicators as ind

FACTORS = ["value", "quality", "growth", "momentum", "risk"]


def lin(x, bad, good):
    """Map x to 0..100 where `bad` -> 0 and `good` -> 100 (works for inverted ranges)."""
    if x is None:
        return None
    t = (x - bad) / (good - bad)
    return round(max(0.0, min(1.0, t)) * 100, 1)


def _part(parts, label, value, score, fmt="num"):
    if score is not None:
        parts.append({"label": label, "value": value, "score": score, "fmt": fmt})


def _avg(parts):
    vals = [p["score"] for p in parts]
    return round(sum(vals) / len(vals), 1) if vals else None


def factor_breakdown(info, close):
    g = info.get
    f = {k: [] for k in FACTORS}
    is_equity = info.get("assetClass") == "Stocks"

    # --- value
    if is_equity:
        pe = g("trailingPE")
        if pe is not None and pe > 0:
            _part(f["value"], "P/E (trailing)", pe, lin(pe, 40, 10))
        elif (g("profitMargins") or 0) < 0:
            _part(f["value"], "P/E (trailing)", None, 0.0, "loss")
        _part(f["value"], "Forward P/E", g("forwardPE"), lin(g("forwardPE"), 35, 10) if (g("forwardPE") or 0) > 0 else None)
        _part(f["value"], "Price / Book", g("priceToBook"), lin(g("priceToBook"), 8, 1) if (g("priceToBook") or 0) > 0 else None)
        _part(f["value"], "EV / EBITDA", g("enterpriseToEbitda"), lin(g("enterpriseToEbitda"), 25, 6) if (g("enterpriseToEbitda") or 0) > 0 else None)
        fcf, mc = g("freeCashflow"), g("marketCap")
        if fcf is not None and mc:
            fy = fcf / mc
            _part(f["value"], "FCF yield", fy, lin(fy, 0, 0.08), "pct")
        # --- quality
        _part(f["quality"], "Return on equity", g("returnOnEquity"), lin(g("returnOnEquity"), 0, 0.30), "pct")
        _part(f["quality"], "Operating margin", g("operatingMargins"), lin(g("operatingMargins"), 0, 0.30), "pct")
        _part(f["quality"], "Net margin", g("profitMargins"), lin(g("profitMargins"), 0, 0.25), "pct")
        _part(f["quality"], "Debt / Equity", g("debtToEquity"), lin(g("debtToEquity"), 2.0, 0.2), "x")
        _part(f["quality"], "Current ratio", g("currentRatio"), lin(g("currentRatio"), 0.8, 2.0), "x")
        # --- growth
        _part(f["growth"], "Revenue growth (YoY)", g("revenueGrowth"), lin(g("revenueGrowth"), -0.05, 0.25), "pct")
        _part(f["growth"], "Earnings growth (YoY)", g("earningsGrowth"), lin(g("earningsGrowth"), -0.10, 0.30), "pct")
        fe, te = g("forwardEps"), g("trailingEps")
        if fe is not None and te and te > 0:
            eg = fe / te - 1
            _part(f["growth"], "Forward EPS vs trailing", eg, lin(eg, -0.05, 0.25), "pct")

    # --- momentum (all assets)
    r12 = ind.ret_over(close, 252)
    r1 = ind.ret_over(close, 21)
    if r12 is not None and r1 is not None:
        r12_1 = (1 + r12) / (1 + r1) - 1
        _part(f["momentum"], "12-1 month return", r12_1, lin(r12_1, -0.20, 0.40), "pct")
    sma200 = ind.sma(close, 200)
    if len(close) >= 200 and sma200.iloc[-1] == sma200.iloc[-1]:
        gap = float(close.iloc[-1] / sma200.iloc[-1] - 1)
        _part(f["momentum"], "Price vs 200-day avg", gap, lin(gap, -0.15, 0.15), "pct")
    r3 = ind.ret_over(close, 63)
    _part(f["momentum"], "3-month return", r3, lin(r3, -0.15, 0.20), "pct")

    # --- risk (higher score = calmer)
    vol = ind.volatility(close)
    crypto = info.get("assetClass") == "Crypto"
    _part(f["risk"], "Volatility (1y, ann.)", vol, lin(vol, 0.9 if crypto else 0.6, 0.35 if crypto else 0.15), "pct")
    dd = ind.max_drawdown(close.iloc[-252:])
    _part(f["risk"], "Max drawdown (1y)", dd, lin(dd, -0.6 if crypto else -0.5, -0.15 if crypto else -0.08), "pct")
    if is_equity and g("beta") is not None:
        _part(f["risk"], "Beta", g("beta"), lin(g("beta"), 2.0, 0.6), "x")
    return f


def stance(score):
    if score is None:
        return "Insufficient data"
    if score >= 70:
        return "Strong"
    if score >= 58:
        return "Favorable"
    if score >= 45:
        return "Neutral"
    if score >= 35:
        return "Caution"
    return "Weak"


def evaluate(info, close, weights=None):
    weights = weights or {k: 0.2 for k in FACTORS}
    bd = factor_breakdown(info, close)
    scores = {k: _avg(v) for k, v in bd.items()}
    used = {k: weights.get(k, 0) for k, s in scores.items() if s is not None and weights.get(k, 0) > 0}
    total_w = sum(used.values())
    composite = round(sum(scores[k] * w for k, w in used.items()) / total_w, 1) if total_w else None
    n_parts = sum(len(v) for v in bd.values())
    expected = 17 if info.get("assetClass") == "Stocks" else 5
    return {
        "composite": composite, "stance": stance(composite), "factors": scores, "breakdown": bd,
        "coverage": round(min(1.0, n_parts / expected), 2), "signals": signals(info, close),
    }


def signals(info, close):
    """Plain-language observations. tone: 1 supportive, -1 cautionary, 0 informational."""
    out = []

    def add(tone, text):
        out.append({"tone": tone, "text": text})

    if len(close) < 30:
        return out
    price = float(close.iloc[-1])
    s50, s200 = ind.sma(close, 50), ind.sma(close, 200)
    if len(close) > 210:
        now_above = s50.iloc[-1] > s200.iloc[-1]
        before_above = s50.iloc[-21] > s200.iloc[-21]
        if now_above and not before_above:
            add(1, "Golden cross in the last month: 50-day average moved above the 200-day.")
        elif before_above and not now_above:
            add(-1, "Death cross in the last month: 50-day average fell below the 200-day.")
        elif now_above:
            add(1, "Long-term uptrend: 50-day average is above the 200-day.")
        else:
            add(-1, "Long-term downtrend: 50-day average is below the 200-day.")
    r = ind.rsi(close).iloc[-1]
    if r == r:
        if r >= 70:
            add(-1, f"RSI {r:.0f}: overbought, short-term pullback risk is elevated.")
        elif r <= 30:
            add(1, f"RSI {r:.0f}: oversold, a rebound is possible but trend may still be down.")
    hi = float(close.iloc[-252:].max())
    off = price / hi - 1
    if off <= -0.30:
        add(0, f"Trading {abs(off):.0%} below its 52-week high.")
    elif off >= -0.02:
        add(1, "Trading at or near its 52-week high.")
    g = info.get
    if info.get("assetClass") == "Stocks":
        tgt = g("targetMeanPrice")
        n = int(g("numberOfAnalystOpinions") or 0)
        if tgt and n >= 3:
            up = tgt / price - 1
            add(1 if up > 0.10 else (-1 if up < -0.05 else 0), f"Analyst mean target implies {up:+.0%} ({n} analysts).")
        if (g("freeCashflow") or 0) < 0:
            add(-1, "Negative free cash flow: the business is consuming cash.")
        if (g("debtToEquity") or 0) > 2:
            add(-1, f"High leverage: debt/equity {g('debtToEquity'):.1f}x.")
        if (g("profitMargins") or 0) < 0:
            add(-1, "Currently unprofitable (negative net margin).")
        dy, pr = g("dividendYield"), g("payoutRatio")
        if dy and dy >= 0.03:
            if pr and pr > 0.9:
                add(-1, f"Dividend yield {dy:.1%} but payout ratio {pr:.0%} may not be sustainable.")
            else:
                add(1, f"Dividend yield {dy:.1%}" + (f" with payout ratio {pr:.0%}." if pr else "."))
        pe, fpe = g("trailingPE"), g("forwardPE")
        if pe and fpe and pe > 0 and fpe > 0 and fpe < pe * 0.8:
            add(1, f"Forward P/E {fpe:.1f} well below trailing {pe:.1f}: earnings expected to grow.")
    d1 = ind.ret_over(close, 1)
    if d1 is not None and abs(d1) >= 0.05:
        add(0, f"Large move today: {d1:+.1%}.")
    return out
