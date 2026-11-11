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


def _part(parts, label, value, bad, good, fmt="num", usable=True, note=None):
    """List a metric with the values that score 0 and 100. Missing or meaningless figures are listed but not scored;
    `note` says why one isn't scored."""
    score = lin(value, bad, good) if usable else None
    parts.append({"label": label, "value": value, "score": score, "fmt": fmt, "bad": bad, "good": good, **({"note": note} if note else {})})


# A bank's margins are on net interest income, so 30-50% is ordinary for a bank and exceptional for anything else;
# its debt is what it lends from, and it has no current ratio. Scored like other companies, every bank looked high
# quality, so for banks these are listed but not scored: quality rests on return on equity.
BANK_NOTE = "Not scored for banks: a bank's margins, debt and liquidity don't compare with other companies'."


def _avg(parts):
    vals = [p["score"] for p in parts if p["score"] is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def factor_breakdown(info, close):
    g = info.get
    f = {k: [] for k in FACTORS}
    is_equity = info.get("assetClass") == "Stocks"
    years = info.get("statementYears") or {}

    def src(label, key):
        """A figure worked out from the annual statements, where Yahoo had none, says so (insights.fill_from_statements)."""
        y = years.get(key)
        return label if not y else f"{label} (no debt reported)" if y == "no debt" else f"{label} (from the {y} annual statements)"

    # --- value
    if is_equity:
        pe = g("trailingPE")
        if (pe is None or pe <= 0) and (g("profitMargins") or 0) < 0:
            f["value"].append({"label": "P/E (trailing)", "value": None, "score": 0.0, "fmt": "loss", "bad": 40, "good": 10})
        else:
            _part(f["value"], "P/E (trailing)", pe, 40, 10, usable=(pe or 0) > 0)
        _part(f["value"], "Forward P/E", g("forwardPE"), 35, 10, usable=(g("forwardPE") or 0) > 0)
        _part(f["value"], "Price / Book", g("priceToBook"), 8, 1, usable=(g("priceToBook") or 0) > 0)
        _part(f["value"], "EV / EBITDA", g("enterpriseToEbitda"), 25, 6, usable=(g("enterpriseToEbitda") or 0) > 0)
        fcf, mc, fx = g("freeCashflow"), g("marketCap"), g("statementFx", 1.0)
        # FCF is in the statement currency, market cap is not
        _part(f["value"], src("FCF yield", "freeCashflow"), fcf * fx / mc if fcf is not None and mc and fx else None, 0, 0.08, "pct")
        # --- quality
        bank = "bank" in (info.get("industry") or "").lower()
        not_bank = {"usable": not bank, "note": BANK_NOTE if bank else None}
        _part(f["quality"], "Return on equity", g("returnOnEquity"), 0, 0.30, "pct")
        _part(f["quality"], "Operating margin", g("operatingMargins"), 0, 0.30, "pct", **not_bank)
        _part(f["quality"], "Net margin", g("profitMargins"), 0, 0.25, "pct", **not_bank)
        _part(f["quality"], src("Debt / Equity", "debtToEquity"), g("debtToEquity"), 2.0, 0.2, "x", **not_bank)
        _part(f["quality"], src("Current ratio", "currentRatio"), g("currentRatio"), 0.8, 2.0, "x", **not_bank)
        # --- growth
        # Several years of annual statements where there are enough; otherwise the latest quarter, labelled as such
        for name, multi, quarter, bad, good in (("Revenue growth", "revenueGrowth3y", "revenueGrowth", -0.05, 0.25),
                                                ("Earnings growth", "earningsGrowth3y", "earningsGrowth", -0.10, 0.30)):
            if g(multi) is not None:
                _part(f["growth"], f"{name} (3y, a year)", g(multi), bad, good, "pct")
            else:
                _part(f["growth"], f"{name} (latest quarter YoY)", g(quarter), bad, good, "pct")
        fe, te = g("forwardEps"), g("trailingEps")
        _part(f["growth"], "Forward EPS vs trailing", fe / te - 1 if fe is not None and te and te > 0 else None, -0.05, 0.25, "pct")

    # --- momentum (all assets)
    r12 = ind.ret_over(close, 252)
    r1 = ind.ret_over(close, 21)
    _part(f["momentum"], "12-1 month return", (1 + r12) / (1 + r1) - 1 if r12 is not None and r1 is not None else None, -0.20, 0.40, "pct")
    sma200 = ind.sma(close, 200)
    has_sma = len(close) >= 200 and sma200.iloc[-1] == sma200.iloc[-1]
    _part(f["momentum"], "Price vs 200-day avg", float(close.iloc[-1] / sma200.iloc[-1] - 1) if has_sma else None, -0.15, 0.15, "pct")
    _part(f["momentum"], "3-month return", ind.ret_over(close, 63), -0.15, 0.20, "pct")

    # --- risk (higher score = calmer)
    # One scale for every asset, so a risk score means the same risk on a stock, a fund or a coin
    vol = ind.volatility(close)
    _part(f["risk"], "Volatility (1y, ann.)", vol, 0.6, 0.15, "pct")
    _part(f["risk"], "Max drawdown (1y)", ind.max_drawdown(ind.window(close, 252)), -0.5, -0.08, "pct")
    if is_equity:
        _part(f["risk"], "Beta", g("beta"), 2.0, 0.6, "x")
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


def combine(scores, weights=None):
    """The overall score: the weighted average of the factors that have a score, or None when none do."""
    weights = weights or {k: 0.2 for k in FACTORS}
    used = {k: weights.get(k, 0) for k, s in scores.items() if s is not None and weights.get(k, 0) > 0}
    total_w = sum(used.values())
    return round(sum(scores[k] * w for k, w in used.items()) / total_w, 1) if total_w else None


def evaluate(info, close, weights=None):
    bd = factor_breakdown(info, close)
    scores = {k: _avg(v) for k, v in bd.items()}
    composite = combine(scores, weights)
    if info.get("assetClass") == "Other":  # an index, currency or future: its trend is shown, but it isn't a holding to rate
        composite = None
    parts = [p for v in bd.values() for p in v if "note" not in p]  # a metric that doesn't apply isn't missing data
    scored = sum(p["score"] is not None for p in parts)
    return {
        "composite": composite, "stance": "Not scored" if info.get("assetClass") == "Other" else stance(composite), "factors": scores, "breakdown": bd,
        "coverage": round(scored / len(parts), 2), "metricsScored": scored, "metricsTotal": len(parts), "signals": signals(info, close),
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
    hi = float(ind.window(close, 252).max())
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
        # A bank's cash flow follows its deposits and loans, and its debt is what it lends from: neither is a warning
        bank = "bank" in (info.get("industry") or "").lower()
        if (g("freeCashflow") or 0) < 0 and not bank:
            add(-1, "Negative free cash flow: the business is consuming cash.")
        if (g("debtToEquity") or 0) > 2 and not bank:
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
