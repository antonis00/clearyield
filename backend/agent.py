"""Ask StockPilot: a model that answers a question by using the app's own tools in a loop.

Every tool only reads (the portfolio, a stock, the screener, Opportunities, Compare, the pre-trade what-if, symbol
search, news); nothing can buy, sell, save or delete. Each tool returns a compact summary with every figure already in
its unit ("12.3%", "33,911 EUR"), the way the brief's data is written: small, because every round resends the
conversation and free plans allow few tokens a minute, and in units, because the answer's figures are checked against
these same texts afterwards.
"""
import json
import re
import time

import httpx

from . import ai, data, db, insights, portfolio
from . import indicators as ind
from .ai import _f_money, _f_num, _f_pct, _f_x

USAGE = []            # token counts the provider reported, newest last (for tuning; kept short)
MAX_ROUNDS = 4        # model calls that may use tools; one more call then answers with what was found
TOOL_ROUND_TOKENS = 1500  # a round that only picks tools needs little room; the answer gets the full allowance
PACE_LIMIT = 60       # seconds the agent may wait in all for the provider's per-minute token allowance to refill
MAX_QUESTION = 500    # characters
ANSWER_NOW = "You've used your look-ups. Answer now from what you found, without asking for more."
REPLY_TOKENS = 1200   # per call, before the thinking allowance

SYSTEM = (
    "You are StockPilot's research assistant for one private investor. Answer by looking things up with the tools "
    "first. Plan the look-ups you need and make them together in one round when they don't depend on each other: "
    "usually one or two rounds are enough. "
    "When the question is about the investor, start with get_portfolio.\n"
    "For diversification, use measured correlations (get_opportunities' fits_your_portfolio, or compare with the "
    "investor's holdings); never assume one from a sector's usual behaviour.\n"
    "Rules: every figure in your answer must come from a tool result or the question; never invent or estimate one, "
    "and don't bring in outside benchmarks or averages (a sector median, a 'typical' range, the market's long-run return): "
    "to compare, look the peers up (screen a sector, or compare with an index fund such as SPY or VWCE.DE). When you "
    "work a figure out from others (a sum, a difference), say so. News gives headlines only: never describe what an "
    "article says beyond its headline. "
    "Scores run 0-100 and higher is better; a higher risk score means calmer, LOWER risk. Explain trade-offs and say "
    "what would change the picture; never give a bare buy or sell instruction: this is decision support, not financial "
    "advice. If the tools can't answer, say what's missing.\n"
    "Answer in markdown, under about 250 words: a direct answer first, then the reasons with their figures, then caveats."
)


def _settings():
    return db.get_settings()


# ---------------------------------------------------------------- the tools (read-only)
def get_portfolio():
    p = portfolio.summary(_settings())
    base = p["base"]
    if not p["positions"]:
        return {"base_currency": base, "holdings": "none yet"}
    cs = p.get("coreSatellite") or {}
    dated = sorted(r["date"] for r in db.q("SELECT date FROM transactions WHERE date <> '' AND kind = 'buy'"))
    first = dated[0] if dated else "an undated purchase"
    meaning = {"Above core target": "More in core than the target: the lower-risk side, nothing needs selling. Satellite holdings are optional; "
                                    "single stocks added to a broad world fund raise its risk, they don't lower it.",
               "To core": "Satellite is above its share: the riskier side. New money into core would bring it back without selling.",
               "Hold": "On target."}.get(cs.get("action"))
    trades_in = sorted({r["currency"] for r in p["positions"] if r.get("currency")})
    from .universe import FUND_CURRENCIES
    us_share = sum(r["value"] * (FUND_CURRENCIES[r["ticker"]].get("USD", 0) if r["ticker"] in FUND_CURRENCIES else (1 if r.get("region") == "US" else 0))
                   for r in p["positions"]) / p["total"] if p["total"] else None
    top = lambda d: {k: _f_pct(v / p["total"], 1) for k, v in list(d.items())[:6]} if p["total"] else {}
    out = {
        "base_currency": base, "value": _f_money(p["total"], base), "gain_on_open_positions": f"{_f_money(p['pnl'], base)} ({_f_pct(p['pnlPct'], 2)})",
        "total_gain": _f_money(p["totalGain"], base),
        "return": f"{_f_pct(p['xirr'], 1)} a year" if p.get("xirr") is not None else (f"{_f_pct(p['returnSoFar'], 2)} since the first purchase on {first} (under a year, so not annualised)" if p.get("returnSoFar") is not None else None),
        "holdings": [{"ticker": r["ticker"], "name": r["name"], "kind": r["assetClass"], "value": _f_money(r["value"], base), "weight": _f_pct(r["weight"], 1),
                      "gain": _f_pct(r["pnlPct"], 1), "bucket": r["bucket"], "sector": r["sector"], "region": r["region"]} for r in p["positions"]],
        "core_satellite": {"core": _f_pct(cs.get("core"), 0), "core_target": _f_pct(cs.get("target"), 0), "status": cs.get("action"), "meaning": meaning,
                           "new_money_to_reach_target_if_wanted": _f_money(cs.get("newMoney"), base)} if cs else None,
        "share_in_us_companies": _f_pct(us_share, 1) + " (broad funds by their index's US weight, approximate; other holdings by where they're listed)" if us_share is not None else None,
        "currency_exposure_looking_through_funds": {**top(p["allocation"]["currencyLookThrough"]),
                                                    "note": f"The currencies of the companies held, looking through funds (approximate index weights). The holdings themselves trade in {', '.join(trades_in)}."},
        "sector_exposure_looking_through_funds": {k: _f_pct(v / p["total"], 1) for k, v in p["allocation"]["sectorLookThrough"].items()} if p["total"] else {},
        "warnings": p["warnings"] or "none",
    }
    return {k: v for k, v in out.items() if v not in (None, {}, [])}


def _known(t):
    """None if Yahoo has prices for `t`; otherwise an error naming the symbols a search finds, so the model can retry
    with the right one (it guessed IWDA.DE for a fund listed as IWDA.AS)."""
    try:
        data.history(t, "1y")
        return None
    except data.DataError as e:
        hits = search(t.split(".")[0].split("-")[0])["results"][:5] if " " not in t else []
        return {"ticker": t, "error": str(e), "did_you_mean": [f"{h['ticker']} ({h['name']}, {h['market']})" for h in hits] or "nothing similar"}


def get_stock(ticker):
    t = ticker.strip().upper()
    if (unknown := _known(t)):
        return unknown
    settings = _settings()
    full, inf, sc = insights.evaluate_ticker(t, settings)
    ctx = {"ticker": t, "info": inf, "scores": sc, "stats": ind.stats(full["Close"], settings.get("risk_free_rate", 0.04)),
           "financials": data.financials(t) if inf.get("assetClass") == "Stocks" else [], "news": [], "price": float(full["Close"].iloc[-1])}
    d = ai.brief_data(ctx)  # the brief's own facts: in units, banks handled, missing figures left out
    d.pop("Recent headlines naming the company", None)
    d.pop("What the scores mean", None)  # said once in the instructions
    return d


def screen(filters=None, question=None):
    f = ai._parse_rules(question.lower()) if question else {}
    f = {k: v for k, v in f.items() if k != "ignored"}
    f.update(ai._tidy_filters(filters or {}))
    for k in ("industry", "exclude_industry"):  # the rules' bank filters, which the model may pass on too
        if isinstance((filters or {}).get(k), str):
            f[k] = filters[k]
    d = insights.screen({"filters": f, "markets": f.get("markets") or ["US", "Europe", "Gulf"]}, _settings())
    rows = [{"ticker": r["ticker"], "name": r["name"], "sector": r["sector"], "region": r["region"], "score": _f_num(r["composite"], 1), "stance": r["stance"],
             "pe": _f_x(r["pe"]) if r.get("pe") and r["pe"] > 0 else None, "dividend_yield": _f_pct(r["divYield"], 2), "revenue_growth": _f_pct(r["revGrowth"], 1),
             "roe": _f_pct(r["roe"], 1), "return_1y": _f_pct(r["ret1y"], 1), "volatility": _f_pct(r["vol"], 1)} for r in d["rows"][:10]]
    return {"filters_used": {k: v for k, v in f.items()}, "matches": len(d["rows"]), "scanned": d["scanned"], "top_10": [{k: v for k, v in r.items() if v is not None} for r in rows],
            "notes": d.get("notes") or None}


def get_opportunities(markets=None):
    o = insights.opportunities(_settings(), markets or ["US", "Europe", "Gulf"])
    short = lambda r, extra=(): {"ticker": r["ticker"], "name": r["name"], "sector": r["sector"], "score": _f_num(r["composite"], 1), "stance": r["stance"],
                                 "reasons": r["reasons"], **dict(extra)}
    return {
        "top_ranked": [short(r) for r in o["top"]],
        "fits_your_portfolio": [short(r, {"fit": _f_num(r["fit"], 1), "moves_with_your_portfolio": _f_num(r["corr"], 2)}) for r in o["fit"]],
        "quality_on_sale": [short(r, {"below_52_week_high": _f_pct(r["fromHigh"], 0)}) for r in o["onSale"]],
        "rules": "Top ranked: highest overall score, at most 3 per sector. Fit: average of score and how little it moved with what you hold "
                 "(2 years of weekly returns in your base currency; correlation 1 = in step). Quality on sale: quality 60+, 10%+ below its high, back above its 50-day average.",
    }


def compare(tickers, period="1y"):
    ts = [t.strip().upper() for t in tickers][:5]
    d = insights.compare(ts, period if period in ("6mo", "1y", "2y", "5y") else "1y", _settings().get("risk_free_rate", 0.04))
    rows = [{"ticker": m["ticker"], "name": m["name"], f"return_{period}": _f_pct(m["periodReturn"], 1), "volatility": _f_pct(m.get("vol1y"), 1),
             "max_drawdown_1y": _f_pct(m.get("maxDD1y"), 1), "sharpe_1y": _f_num(m.get("sharpe1y"), 2), "pe": _f_x(m["pe"]) if m.get("pe") else None} for m in d["meta"]]
    corr = d.get("correlation")
    pairs = {f"{a}-{b}": _f_num(corr["matrix"][i][j], 2) for i, a in enumerate(corr["tickers"]) for j, b in enumerate(corr["tickers"]) if i < j} if corr else {}
    return {"assets": [{k: v for k, v in r.items() if v is not None} for r in rows], "correlation_of_weekly_returns": pairs, "no_data": d["failed"] or None}


def pretrade(ticker, amount):
    s = _settings()
    r = portfolio.pretrade(s, ticker.strip().upper(), amount=float(amount))
    b = r["base"]
    sector, cur = r.get("sector"), data.main_unit(r["currency"])
    names = {"position": f"this position's weight", "sectorLookThrough": f"{sector} weight, counting what funds hold",
             "currencyLookThrough": f"{cur} exposure, counting the companies funds hold", "region": f"{r.get('region')} weight, funds counted as their own region",
             "beta": "portfolio beta"}
    w = lambda x: {names[k]: (_f_pct(v, 1) if k != "beta" else _f_num(v, 2)) for k, v in (x or {}).items() if k in names}
    return {"ticker": r["ticker"], "shares": _f_num(r["shares"], 4), "value": _f_money(r["value"], b), "price": _f_num(r["price"], 2), "price_currency": r["currency"],
            "before": w(r["before"]), "after": w(r["after"]), "new_warnings": r["newWarnings"] or "none",
            "sector_mix_counting_what_funds_hold": {"before": {k: _f_pct(v, 1) for k, v in (r.get("sectorMixBefore") or {}).items()},
                                                    "after": {k: _f_pct(v, 1) for k, v in (r.get("sectorMixAfter") or {}).items()}},
            "moves_with_your_holdings": {c["ticker"]: _f_num(c["correlation"], 2) for c in r["correlations"]}}


def get_analysts(ticker):
    t = ticker.strip().upper()
    a = data.analysts(t)
    if not a:
        return {"ticker": t, "analysts": "Yahoo lists no analyst ratings or targets (usual for funds, coins and smaller companies)."}
    inf = data.info(t)
    price = float(data.history(t, "1y")["Close"].iloc[-1])
    cur = inf.get("currency")
    tg = a.get("targets") or {}
    out = {"ticker": t, "name": inf.get("name"), "price": f"{price:,.2f} {cur}",
           "price_targets": {k: f"{v:,.2f} {cur}" for k, v in tg.items()},
           "mean_target_vs_price": _f_pct(tg["mean"] / price - 1, 1) if tg.get("mean") else None,
           "ratings_now": a.get("ratings"), "ratings_a_month_ago": a.get("ratingsMonthAgo"),
           "note": "As Yahoo reports them; analysts' targets are opinions, often optimistic, and differ widely."}
    return {k: v for k, v in out.items() if v is not None}


def get_earnings(ticker):
    t = ticker.strip().upper()
    inf = data.info(t)
    ev = insights.upcoming(inf)
    rec = data.earnings_record(t)
    return {"ticker": t, "next_earnings": ev.get("earnings") or "not announced", "next_ex_dividend": ev.get("exDividend") or "not announced",
            "past_quarters_reported_vs_expected": [{"quarter_ending": r["quarter"], "eps_reported": _f_num(r["actual"], 2), "eps_expected": _f_num(r["estimate"], 2),
                                                   "surprise": _f_pct(r["surprise"], 1)} for r in rec[:6]] or "none listed (usual for funds and coins)"}


def get_dividends(ticker):
    t = ticker.strip().upper()
    paid = data.dividend_payments(t)
    if paid is None:
        return {"ticker": t, "error": "Yahoo didn't answer for dividends; try again later."}
    if not paid:
        return {"ticker": t, "dividends": "none paid (an accumulating fund or a company that doesn't pay)"}
    import pandas as pd
    by_year = {}
    for d, v in paid:
        by_year[d[:4]] = by_year.get(d[:4], 0) + v
    years = sorted(by_year)[-6:]
    price = float(data.history(t, "1y")["Close"].iloc[-1])
    since = (pd.Timestamp.today().normalize() - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    last_year = sum(v for d, v in paid if d > since)
    return {"ticker": t, "per_share_currency": data.info(t).get("currency"), "latest_payment": f"{paid[-1][1]:,.4f} on {paid[-1][0]}",
            "total_per_year": {y: f"{by_year[y]:,.4f}" + (" (year so far)" if y == years[-1] else "") for y in years},
            "paid_in_past_12_months": f"{last_year:,.4f}", "yield_on_past_12_months": _f_pct(last_year / price, 2) if price else None,
            "payments_listed": len(paid)}


def get_price_history(ticker, start, end=None):
    import pandas as pd
    t = ticker.strip().upper()
    c = data.history(t, "max")["Close"]
    s, e = pd.Timestamp(start), pd.Timestamp(end) if end else c.index[-1]
    w = c[(c.index >= s) & (c.index <= e) & (c > 0)]
    if len(w) < 2:
        return {"ticker": t, "error": f"No prices between {start} and {end or 'now'}. Yahoo's history for {t} starts on {c.index[0]:%Y-%m-%d}."}
    cur = data.info(t).get("currency")
    return {"ticker": t, "from": f"{w.index[0]:%Y-%m-%d}", "to": f"{w.index[-1]:%Y-%m-%d}", "currency": cur,
            "start_price": f"{w.iloc[0]:,.2f}", "end_price": f"{w.iloc[-1]:,.2f}",
            "return": f"{_f_pct(w.iloc[-1] / w.iloc[0] - 1, 1)} ({'a rise' if w.iloc[-1] >= w.iloc[0] else 'a fall'})",
            "highest": f"{w.max():,.2f} on {w.idxmax():%Y-%m-%d}", "lowest": f"{w.min():,.2f} on {w.idxmin():%Y-%m-%d}",
            # Named so it isn't read as one day's drop: it's from a peak to the lowest point after it, over any length of time
            "largest_peak_to_later_low_fall": _f_pct(ind.max_drawdown(w), 1), "volatility_a_year": _f_pct(ind.volatility(w), 1),
            "note": "Prices are adjusted for dividends and splits, as Yahoo gives them."}


def get_fund(ticker):
    t = ticker.strip().upper()
    if (unknown := _known(t)):
        return unknown
    inf = data.info(t)
    if inf.get("assetClass") != "ETFs":
        return {"ticker": t, "error": f"{t} isn't a fund; use get_stock."}
    f = data.fund(t)
    from .universe import FUND_CURRENCIES
    out = {"ticker": t, "name": inf.get("name"), "yearly_cost": _f_pct(inf.get("netExpenseRatio"), 2) or "not listed by Yahoo",
            "sectors": {k: _f_pct(v, 1) for k, v in sorted(f["sectors"].items(), key=lambda kv: -kv[1])} or "not listed by Yahoo",
            "top_holdings": [f"{h['name']} ({h['ticker']}) {_f_pct(h['weight'], 1)}" for h in f["holdings"]] or "not listed by Yahoo",
            "currencies_of_what_it_holds": {k: _f_pct(v, 1) for k, v in FUND_CURRENCIES[t].items()} if t in FUND_CURRENCIES else None,
            "note": "Yahoo lists only the top 10 holdings. The currency split, where given, is the index's approximate country weights."}
    return {k: v for k, v in out.items() if v is not None}


def get_market():
    from .universe import MACRO
    out = {}
    for t, name in MACRO.items():
        try:
            c = data.history(t, "2y")["Close"].dropna()  # two years, so a full year's change has a starting price
        except data.DataError:
            continue
        if len(c) < 2:
            continue
        unit = data.price_unit(t, None)
        out[name] = {"level": f"{c.iloc[-1]:,.2f}" + ("%" if unit == "%" else ""), "day": _f_pct(ind.ret_over(c, 1), 2), "month": _f_pct(ind.ret_over(c, 21), 1),
                     "past_year": _f_pct(ind.ret_over(c, 252), 1), "as_of": f"{c.index[-1]:%Y-%m-%d}"}
    return {"markets": out, "note": "day, month (past 21 trading days) and past_year (the last 12 months, not year to date). Indexes in points, "
                                    "yields in percent (their changes are relative, not in percentage points), the rest in their own units."}


def search(text):
    from .universe import search as uni_search
    hits = uni_search(text)
    known = {h["ticker"] for h in hits}
    hits += [h for h in data.search_symbols(text) if h["ticker"] not in known]
    return {"results": hits[:8]}


def get_news(ticker):
    t = ticker.strip().upper()
    heads = ai.relevant_headlines(data.news(t), t, data.info(t).get("name"), limit=6)
    return {"headlines": [f"{(n.get('time') or '')[:10]} {n.get('publisher') or ''}: {n['title']}".strip() for n in heads] or "none name the company",
            "note": "Headlines only: the articles' text isn't available, so nothing beyond a headline is known."}


S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
TOOLS = {
    "get_portfolio": (get_portfolio, "The investor's holdings, value, gains, return, core/satellite split, currency and sector exposure (funds looked through), and warnings.", S({})),
    "get_stock": (get_stock, "One stock, fund or coin: its scores (value, quality, growth, momentum, risk, overall), key figures, returns, risk and signals.",
                  S({"ticker": {"type": "string", "description": "Yahoo symbol, e.g. AAPL, SAP.DE, VWCE.DE, BTC-USD"}}, ["ticker"])),
    "screen": (screen, "Find stocks in the app's list of 109 by filters, best score first (top 10). Give filters, or a short plain question.",
               S({"filters": {"type": "object", "description": "Any of: markets (US/Europe/Gulf), sector, exclude_sector, industry ('Banks'), exclude_industry, "
                                                                     "max_pe, min_div_yield (0.03 = 3%), min_rev_growth, max_de, min_roe, min_score, min_momentum, max_vol, sort"},
                  "question": {"type": "string", "description": "e.g. 'cheap European banks'; read by the app's keyword rules"}})),
    "get_opportunities": (get_opportunities, "The Opportunities page: top ranked stocks; fits_your_portfolio, with each stock's measured correlation "
                                              "with what the investor holds (use it for diversification questions); and good companies below their high.",
                          S({"markets": {"type": "array", "items": {"type": "string", "enum": ["US", "Europe", "Gulf"]}}})),
    "compare": (compare, "Up to 5 assets side by side: return, volatility, drawdown, Sharpe, P/E, and how closely each pair moves together.",
                S({"tickers": {"type": "array", "items": {"type": "string"}}, "period": {"type": "string", "enum": ["6mo", "1y", "2y", "5y"]}}, ["tickers"])),
    "pretrade": (pretrade, "What buying an amount (in the base currency) of one asset would do to the portfolio: its weight, the whole sector mix "
                           "before and after (counting what funds hold), currency exposure, beta and warnings. Use it for 'what if I bought'. Nothing is saved.",
                 S({"ticker": {"type": "string"}, "amount": {"type": "number"}}, ["ticker", "amount"])),
    "get_analysts": (get_analysts, "Analysts' price targets and buy/hold/sell counts for one stock, as Yahoo reports them.", S({"ticker": {"type": "string"}}, ["ticker"])),
    "get_earnings": (get_earnings, "Next earnings and ex-dividend dates, and recent quarters' reported against expected earnings per share.", S({"ticker": {"type": "string"}}, ["ticker"])),
    "get_dividends": (get_dividends, "Dividend history: latest payment, total per year, past 12 months and yield.", S({"ticker": {"type": "string"}}, ["ticker"])),
    "get_price_history": (get_price_history, "Price over any dates: start and end, return, high, low, largest fall, volatility.",
                          S({"ticker": {"type": "string"}, "start": {"type": "string", "description": "YYYY-MM-DD"}, "end": {"type": "string", "description": "YYYY-MM-DD; omit for today"}}, ["ticker", "start"])),
    "get_fund": (get_fund, "What a fund holds: yearly cost, sectors, top 10 holdings, and currencies for broad index funds.", S({"ticker": {"type": "string"}}, ["ticker"])),
    "get_market": (get_market, "Markets today: main indexes, the VIX, yields, gold, oil, bitcoin, currencies: level and day, month and year change.", S({})),
    "search": (search, "Find a symbol by company or fund name.", S({"text": {"type": "string"}}, ["text"])),
    "get_news": (get_news, "Recent headlines that name a company.", S({"ticker": {"type": "string"}}, ["ticker"])),
}


def run_tool(name, args):
    """A tool's result as compact text, or an error the model can read and work around."""
    fn = TOOLS.get(name, (None,))[0]
    if fn is None:
        return json.dumps({"error": f"No tool called {name}."}), False
    try:
        out = fn(**(args or {}))
        # A result that reports a problem (no such symbol, Yahoo not answering) is a failed look-up, shown as one
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str), not (isinstance(out, dict) and "error" in out)
    except TypeError as e:
        return json.dumps({"error": f"Wrong arguments for {name}: {e}"}), False
    except Exception as e:  # a missing symbol, Yahoo not answering: the model can say so or try another way
        return json.dumps({"error": str(e)[:300]}), False


# ---------------------------------------------------------------- the conversation, in each provider's format
PACE = {"remaining": None, "reset": 0.0, "waited": 0.0}


def _reset_seconds(v):
    """'47.182s', '1m3.2s' or '0.5' as seconds."""
    import re
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|m|s|h)?", str(v or "")):
        total += float(num) * {"ms": 0.001, "m": 60, "h": 3600}.get(unit, 1)
    return total


def _pace(need):
    """Free plans allow a few thousand tokens a minute. When the next round won't fit in what's left, wait for the
    allowance to refill (within PACE_LIMIT in all) rather than fail."""
    left, reset = PACE["remaining"], PACE["reset"]
    if left is None or left >= need or reset <= 0:
        return
    wait = min(reset + 0.5, PACE_LIMIT - PACE["waited"])
    if wait > 0:
        time.sleep(wait)
        PACE["waited"] += wait
        PACE["remaining"] = None


def _limit_message(r, wait):
    """What a rate limit means for the investor: a minute's allowance comes back quickly, a day's doesn't."""
    try:
        why = (r.json().get("error") or {}).get("message", "")
    except ValueError:
        why = ""
    when = f" in about {round(wait / 60)} minutes" if wait >= 90 else f" in about {round(wait)} seconds" if wait else " later"
    if "per day" in why or "TPD" in why or "RPD" in why:
        limit = re.search(r"Limit (\d+)", why)
        return (f"Today's AI allowance is used up{f' ({int(limit.group(1)):,} tokens a day on your plan)' if limit else ''}. "
                f"It comes back gradually: try again{when}. Briefs and screener questions draw on the same allowance.")
    return f"The AI provider's rate limit was reached. Try again{when}."


def _post(c, url, headers, body, need=0):
    """One call, paced to the provider's per-minute allowance, waiting once if it says it's rate-limited briefly."""
    _pace(need)
    for attempt in range(2):
        r = httpx.post(url, headers=headers, json=body, timeout=c["timeout"])
        if r.headers.get("x-ratelimit-remaining-tokens"):
            try:
                PACE["remaining"] = int(float(r.headers["x-ratelimit-remaining-tokens"]))
                PACE["reset"] = _reset_seconds(r.headers.get("x-ratelimit-reset-tokens"))
            except ValueError:
                pass
        if r.status_code == 429 and re.search(r"per day|\bTPD\b|\bRPD\b", r.text):
            raise RuntimeError(_limit_message(r, _reset_seconds(r.headers.get("retry-after"))))  # a day's limit isn't waited out
        if r.status_code == 429 and attempt == 0:
            # retry-after, else when the per-minute allowance refills, else ten seconds: a refusal without a hint
            # used to fail at once although the allowance was back within the minute
            wait = _reset_seconds(r.headers.get("retry-after")) or _reset_seconds(r.headers.get("x-ratelimit-reset-tokens")) or 10.0
            if 0 < wait <= PACE_LIMIT - PACE["waited"]:
                time.sleep(wait)
                PACE["waited"] += wait
                continue
            raise RuntimeError(_limit_message(r, wait))
        if r.status_code == 429:
            raise RuntimeError(_limit_message(r, _reset_seconds(r.headers.get("retry-after"))))
        if r.status_code >= 400:  # the provider's own reason, not just the status
            try:
                why = r.json().get("error", {})
                why = why.get("message") if isinstance(why, dict) else str(why)
            except ValueError:
                why = r.text
            raise RuntimeError(f"The AI provider refused the request ({r.status_code}): {str(why)[:300]}")
        j = r.json()
        USAGE.append((j.get("usage") or {}))
        return j


def _call(c, messages, use_tools):
    """One round: returns (text, tool calls as (id, name, args)), and adds the model's turn to `messages`."""
    if c["provider"] == "anthropic":
        body = {"model": c["model"], "max_tokens": REPLY_TOKENS * 2, "temperature": 0, "system": SYSTEM, "messages": messages,
                "tools": [{"name": n, "description": d, "input_schema": s} for n, (_, d, s) in TOOLS.items()]}
        if not use_tools:
            body["tool_choice"] = {"type": "none"}
        j = _post(c, f"{c['base']}/messages", {"x-api-key": c["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}, body)
        blocks = j.get("content", [])
        messages.append({"role": "assistant", "content": blocks})
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return ai.clean(text), [(b["id"], b["name"], b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"]
    headers = {"content-type": "application/json", "X-Title": "StockPilot", **({"authorization": f"Bearer {c['key']}"} if c["key"] else {})}
    body = {"model": c["model"], "temperature": 0, "stream": False, "max_tokens": TOOL_ROUND_TOKENS if use_tools else REPLY_TOKENS,
            "messages": [{"role": "system", "content": SYSTEM}, *messages]}
    if use_tools:
        body["tools"] = [{"type": "function", "function": {"name": n, "description": d, "parameters": s}} for n, (_, d, s) in TOOLS.items()]
        body["tool_choice"] = "auto"
    elif any(m.get("role") == "tool" for m in messages):
        # The answer round offers no tools: told only "none", a model still asked for one and the provider refused
        body["messages"].append({"role": "user", "content": ANSWER_NOW})
    if c["thinking"] == "off":
        body["reasoning_effort"] = "none"
    elif c["thinking"] in ("low", "medium", "high"):
        body["reasoning_effort"] = c["thinking"]
    if c["thinking"] not in ("off", "default") and not use_tools:
        body["max_tokens"] = REPLY_TOKENS * 3  # room to think before answering, within a free plan's minute
    if use_tools and c["thinking"] in ("on", "low", "medium", "high"):  # only where a thinking model is configured
        body["reasoning_effort"] = "low"  # choosing look-ups needs little reasoning; the answer gets the configured effort
    need = len(json.dumps(body)) // 3 + body["max_tokens"] // 4  # a rough count of what this round will use
    msg = ((_post(c, f"{c['base']}/chat/completions", headers, body, need).get("choices") or [{}])[0].get("message") or {})
    calls = msg.get("tool_calls") or []
    messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
    parsed = []
    for tc in calls:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        parsed.append((tc["id"], tc["function"]["name"], args))
    return ai.clean(msg.get("content") or ""), parsed


def _add_results(c, messages, results):
    if c["provider"] == "anthropic":
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": text} for i, text in results]})
    else:
        messages.extend({"role": "tool", "tool_call_id": i, "content": text} for i, text in results)


FINDINGS_LIMIT = 16000  # characters of look-up results the fresh answer may see (a free plan's minute holds about 8,000 tokens)


def _answer_from_findings(c, question, findings):
    """The answer from a fresh conversation: the question and the look-ups' results as plain text, no tools offered."""
    text = "\n".join(findings)[:FINDINGS_LIMIT]
    fresh = [{"role": "user", "content": f"{question}\n\nWhat the look-ups found (all the data there is):\n{text}\n\n{ANSWER_NOW}"}]
    answer, _ = _call(c, fresh, False)
    return answer


def ask(question):
    """Answer a question with the tools. Returns the answer, the steps taken, and the check of its figures."""
    q = (question or "").strip()[:MAX_QUESTION]
    if not q:
        raise ValueError("Ask a question.")
    c = ai.config()
    if not ai.available():
        raise RuntimeError(c["problem"] or "Ask needs an AI provider. Set AI_PROVIDER in .env (see .env.example).")
    import datetime as dt
    today = dt.date.today()
    # The model doesn't know today's date, and without it read an earnings date eleven weeks away as "over a year"
    dated = f"(Today is {today:%A %d %B %Y}.)\n{q}"
    messages, steps, facts, calls = [{"role": "user", "content": dated}], [], [q], 0
    PACE["waited"] = 0.0
    text = ""
    for rnd in range(MAX_ROUNDS + 1):
        use_tools = rnd < MAX_ROUNDS
        try:
            text, tool_calls = _call(c, messages, use_tools)
        except RuntimeError as e:
            # A malformed tool request ("Parsing failed", "tool_use_failed") usually comes out right the second time
            if not any(w in str(e) for w in ("Parsing failed", "tool_use_failed", "but model called a tool")):
                raise
            try:
                text, tool_calls = _call(c, messages, use_tools)
            except RuntimeError:
                if use_tools and rnd == 0:
                    raise
                # Still asking for look-ups when it should answer: start afresh with only the question and what the
                # look-ups returned, so there's no tool history to continue
                text, tool_calls = _answer_from_findings(c, q, facts[1:]), []
                calls += 1
                break
        calls += 1
        if not tool_calls or not use_tools:
            break
        results = []
        for cid, name, args in tool_calls:
            out, ok = run_tool(name, args)
            facts.append(out)
            steps.append({"tool": name, "args": args, "ok": ok, **({} if ok else {"error": json.loads(out).get("error")})})
            results.append((cid, out))
        _add_results(c, messages, results)
    if not text:
        raise RuntimeError("The model returned no answer. Try asking again, perhaps more specifically.")
    known = "\n".join(facts)
    check, revised = ai.check_figures(text, known), None
    if check["unmatched"]:
        # Figures neither in the data nor worked out from it: the model gets one chance to correct or drop them. The
        # revision is kept only if it really has fewer; a figure still unsupported is marked in the answer.
        try:
            draft = _revise(c, q, facts[1:], text, check["unmatched"])
            calls += 1
            again = ai.check_figures(draft, known) if draft else None
            if again and len(again["unmatched"]) < len(check["unmatched"]):
                revised = {"before": check["unmatched"], "fixed": len(check["unmatched"]) - len(again["unmatched"])}
                text, check = draft, again
        except (RuntimeError, httpx.HTTPError):
            pass  # the first answer stands, with its figures marked
    return {"answer": text, "steps": steps, "calls": calls, "by": f"{c['model']} via {c['server']}", "waited": round(PACE["waited"]),
            "check": check, "revised": revised}


def _revise(c, question, findings, draft, figures):
    """The draft with the listed figures corrected from the data or removed, from a fresh conversation."""
    text = "\n".join(findings)[:FINDINGS_LIMIT]
    ask_ = (f"{question}\n\nWhat the look-ups found (all the data there is):\n{text}\n\nYour draft answer:\n{draft}\n\n"
            f"These figures in the draft aren't in the data and can't be worked out from figures in it: {', '.join(figures)}. "
            "Rewrite the draft so each of them is either replaced by the right figure from the data or removed (with any claim "
            "that rests on it). Keep everything else as it is, in the same format. Reply with the rewritten answer only.")
    answer, _ = _call(c, [{"role": "user", "content": ask_}], False)
    return answer
