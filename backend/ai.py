"""Optional LLM layer (Anthropic API). Everything degrades gracefully to rule-based output without a key."""
import json
import os
import re

import httpx

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
SYSTEM = (
    "You are a careful equity research assistant inside a personal investing dashboard. "
    "Use ONLY the data provided. Be concise, concrete and balanced: always give both the bull and the bear case, "
    "name the key risks, and say what would change the view. Never promise returns and never present output as "
    "personalised financial advice; frame conclusions as considerations for the user's own decision."
)


def available():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def complete(user, system=SYSTEM, max_tokens=900):
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
        timeout=90,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()


def _pct(v):
    return "n/a" if v is None else f"{v:+.1%}"


def rule_brief(ctx):
    """Deterministic summary used when no API key is configured (or the API call fails)."""
    sc, st, inf = ctx["scores"], ctx["stats"], ctx["info"]
    fac = sc["factors"]
    ranked = sorted([(k, v) for k, v in fac.items() if v is not None], key=lambda kv: -kv[1])
    lines = [f"**{inf.get('name')} ({ctx['ticker']})** - composite score {sc['composite']} ({sc['stance']})."]
    if ranked:
        lines.append(f"Strongest factor: {ranked[0][0]} ({ranked[0][1]:.0f}); weakest: {ranked[-1][0]} ({ranked[-1][1]:.0f}).")
    lines.append(f"Returns: 1M {_pct(st.get('ret1m'))}, 3M {_pct(st.get('ret3m'))}, 1Y {_pct(st.get('ret1y'))}; "
                 f"volatility {_pct(st.get('vol1y')).lstrip('+')}, 1Y max drawdown {_pct(st.get('maxDD1y'))}.")
    pos = [s["text"] for s in sc["signals"] if s["tone"] > 0]
    neg = [s["text"] for s in sc["signals"] if s["tone"] < 0]
    if pos:
        lines.append("**Supportive:** " + " ".join(pos))
    if neg:
        lines.append("**Cautionary:** " + " ".join(neg))
    news = ctx.get("news") or []
    if news:
        s = sum(n["sentiment"] for n in news)
        tone = "mostly positive" if s > 1 else "mostly negative" if s < -1 else "mixed"
        lines.append(f"Recent headlines ({len(news)}) read {tone}.")
    lines.append("_Rule-based summary. Add an ANTHROPIC_API_KEY in .env for AI-written analysis._")
    return "\n\n".join(lines)


def brief(ctx):
    if not available():
        return {"source": "rules", "text": rule_brief(ctx)}
    payload = {
        "ticker": ctx["ticker"],
        "profile": {k: ctx["info"].get(k) for k in ("name", "sector", "industry", "region", "currency", "assetClass", "marketCap")},
        "fundamentals": {k: ctx["info"].get(k) for k in ("trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda", "freeCashflow",
                                                           "profitMargins", "operatingMargins", "returnOnEquity", "debtToEquity",
                                                           "revenueGrowth", "earningsGrowth", "dividendYield", "payoutRatio",
                                                           "targetMeanPrice", "recommendationKey")},
        "priceStats": ctx["stats"], "scores": {"composite": ctx["scores"]["composite"], "stance": ctx["scores"]["stance"],
                                               "factors": ctx["scores"]["factors"]},
        "signals": [s["text"] for s in ctx["scores"]["signals"]],
        "headlines": [n["title"] for n in (ctx.get("news") or [])[:10]],
        "annualFinancials": ctx.get("financials"),
    }
    prompt = ("Write a research brief for this asset in markdown with these sections: **Snapshot** (2 sentences), "
              "**Bull case** (3 bullets), **Bear case** (3 bullets), **What the numbers say** (valuation, quality, momentum, risk), "
              "**News read**, **What would change the view** (2-3 concrete triggers). Max ~300 words.\n\nDATA:\n"
              + json.dumps(payload, default=str))
    try:
        return {"source": "ai", "text": complete(prompt)}
    except Exception as e:
        return {"source": "rules", "text": rule_brief(ctx) + f"\n\n_(AI call failed: {e})_"}


# ---------------------------------------------------------------- natural-language screener
FILTER_SPEC = {
    "markets": "list of any of US, Europe, Gulf, ETFs, Crypto",
    "max_pe": "number", "min_div_yield": "fraction e.g. 0.03", "min_rev_growth": "fraction", "max_de": "number (x)",
    "min_roe": "fraction", "min_score": "0-100", "min_momentum": "0-100 momentum factor score", "max_vol": "fraction annual volatility",
    "sort": "one of composite, value, quality, growth, momentum, risk, divYield",
    "sector": "one of Technology, Financial Services, Energy, Healthcare, Utilities, Real Estate, Industrials, Communication Services, Basic Materials, Consumer Defensive, Consumer Cyclical",
}

MARKET_WORDS = {"US": ["usa", "u.s.", "u.s", "american", "america"], "Europe": ["europe", "european", "eu"],
                "Gulf": ["gulf", "qatar", "qatari", "saudi", "uae", "gcc", "dubai", "middle east"],
                "ETFs": ["etf", "etfs"], "Crypto": ["crypto", "cryptocurrency", "bitcoin"]}
SECTOR_WORDS = {"Technology": ["tech", "technology", "software", "semiconductor", "semiconductors"],
                "Financial Services": ["bank", "banks", "financial", "financials"], "Energy": ["energy", "oil"],
                "Healthcare": ["health", "healthcare", "pharma"], "Utilities": ["utility", "utilities"],
                "Real Estate": ["reit", "reits", "property"], "Industrials": ["industrial", "industrials"],
                "Communication Services": ["telecom", "telecoms", "media"], "Basic Materials": ["materials", "mining"]}
# Words the rules act on, and words that don't change a screen, so neither is reported as not understood
RULE_WORDS = {"p/e", "pe", "ratio", "yield", "yielding", "dividend", "dividends", "income", "growth", "growing", "revenue", "cheap", "undervalued", "value",
              "low", "debt", "no", "profitable", "quality", "momentum", "trending", "uptrend", "vol", "volatility", "risk", "safe", "defensive", "under", "below",
              "above", "over", "less", "than", "more", "at", "least", "most", "of", "us", "real", "estate", "middle", "east"}
FILLER_WORDS = {"stock", "stocks", "share", "shares", "company", "companies", "name", "names", "equities", "market", "markets", "listed", "with", "and", "or", "in",
                "the", "a", "an", "for", "from", "that", "have", "has", "is", "are", "me", "show", "find", "best", "good", "great", "top", "style", "payers", "paying", "pay", "pays", "generous", "famous"}


def parse_query(text):
    """Turn a natural-language screen into filters. Uses the LLM when available, regex rules otherwise."""
    if available():
        try:
            raw = complete(
                "Convert this stock screen request into a JSON object using only these keys (omit unused ones): "
                + json.dumps(FILTER_SPEC) + "\nReply with JSON only.\nREQUEST: " + text,
                system="You convert screening requests into strict JSON filters.", max_tokens=300)
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                return json.loads(m.group(0))
        except Exception:
            pass
    return _parse_rules(text.lower())


def _has(t, *words):
    return any(re.search(rf"(?<![\w/.]){re.escape(w)}(?![\w/])", t) for w in words)


def _parse_rules(t):
    """Keyword rules, matching whole words. Anything they can't act on comes back under "ignored" to show the user."""
    f, ignored = {}, []

    # Set aside what can't be honoured, rather than reading "not cheap" as cheap or dividend growth as yield
    def set_aside(m):
        ignored.extend(m.group(0).split()[1 if m.group(0).split()[0] in ("not", "non") else 0:])
        return " "
    t = re.sub(r"\b(?:not|non)[ -]\w+", set_aside, t)
    t = re.sub(r"\bdividend growth\b", set_aside, t)

    markets = [m for m, words in MARKET_WORDS.items() if _has(t, *words)]
    # "us" on its own is usually the pronoun, as in "show us"
    if re.search(r"\bus[ -](?:stocks?|shares|companies|market|equities|listed)\b|\bin the us\b", t):
        markets.insert(0, "US")
    if markets:
        f["markets"] = list(dict.fromkeys(m for m in MARKET_WORDS if m in markets))
    for sector, words in SECTOR_WORDS.items():
        if _has(t, *words) or (sector == "Real Estate" and "real estate" in t):
            f["sector"] = sector
            break

    pe = r"p/?e(?:\s+ratio)?"
    below = r"(?:under|below|<=?|less than|at most)"
    if m := re.search(rf"\b{pe}\s*(?:of\s*|{below}\s*|=\s*)(\d+(?:\.\d+)?)|{below}\s*(\d+(?:\.\d+)?)\s*{pe}\b", t):
        f["max_pe"] = float(m.group(1) or m.group(2))
    if m := re.search(r"\b(?:yield\w*|dividends?)\b[^\d]{0,20}(\d+(?:\.\d+)?)\s*%", t):
        f["min_div_yield"] = float(m.group(1)) / 100
    elif _has(t, "dividend", "dividends", "income", "yield"):
        f["min_div_yield"] = 0.025
        f["sort"] = "divYield"
    if m := re.search(r"\bgrowth\b[^\d]{0,20}(\d+(?:\.\d+)?)\s*%", t):
        f["min_rev_growth"] = float(m.group(1)) / 100
    elif _has(t, "growth", "growing"):
        f["min_rev_growth"] = 0.10
        f.setdefault("sort", "growth")
    if _has(t, "cheap", "undervalued", "value"):
        f.setdefault("max_pe", 18)
        f.setdefault("sort", "value")
    if _has(t, "low debt", "no debt"):
        f["max_de"] = 0.5
    if _has(t, "profitable", "quality"):
        f["min_roe"] = 0.12
        f.setdefault("sort", "quality")
    if _has(t, "momentum", "trending", "uptrend"):
        f["min_momentum"] = 60
        f.setdefault("sort", "momentum")
    if _has(t, "low vol", "low volatility", "low risk", "safe", "defensive"):
        f["max_vol"] = 0.25
        f.setdefault("sort", "risk")

    known = RULE_WORDS | FILLER_WORDS | {w for ws in (*MARKET_WORDS.values(), *SECTOR_WORDS.values()) for w in ws}
    ignored += [w for w in re.findall(r"[a-z][a-z/.'-]*", t) if w.strip(".'") not in known]
    if ignored:
        f["ignored"] = list(dict.fromkeys(ignored))
    return f
