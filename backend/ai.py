"""Optional LLM layer. Everything degrades gracefully to rule-based output when no provider is set up, or a call fails.

Providers, chosen in .env (see .env.example):
- anthropic: the Anthropic Messages API.
- openai_compatible: any server with an OpenAI-style /chat/completions, such as Ollama, LM Studio, OpenRouter or Groq.
"""
import ipaddress
import json
import os
import re
from urllib.parse import urlparse

import httpx

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5-5"
PLACEHOLDER_KEYS = {"my_api_key", "your_api_key", "your-api-key", "api_key", "changeme", "xxx", "..."}
THINKING_ROOM = 6  # times the usual reply allowance, when a model may think first
LOCAL_TIMEOUT, REMOTE_TIMEOUT = 180, 90  # a small model on a laptop can take minutes to write a brief
# Recognisable names for well-known OpenAI-compatible servers, shown in the sidebar
KNOWN_SERVERS = {"11434": "Ollama", "1234": "LM Studio", "openrouter.ai": "OpenRouter", "api.groq.com": "Groq", "api.openai.com": "OpenAI"}
SYSTEM = (
    "You are a careful equity research assistant inside a personal investing dashboard. "
    "Use ONLY the data provided. Be concise, concrete and balanced: always give both the bull and the bear case, "
    "name the key risks, and say what would change the view. Never promise returns and never present output as "
    "personalised financial advice; frame conclusions as considerations for the user's own decision."
)


def _env(name):
    return (os.getenv(name) or "").strip()


def _is_local(url):
    host = urlparse(url).hostname or ""
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _server_name(url):
    parsed = urlparse(url)
    for key, name in KNOWN_SERVERS.items():
        if key in (parsed.hostname or "", str(parsed.port or "")):
            return name
    return parsed.hostname or url


def config():
    """The provider in use, read from the environment on every call so a changed .env needs only a restart.
    With no AI_PROVIDER, an ANTHROPIC_API_KEY on its own still turns on the Anthropic provider, as before."""
    provider = _env("AI_PROVIDER").lower() or ("anthropic" if _env("ANTHROPIC_API_KEY") or _env("AI_API_KEY") else "none")
    if provider == "anthropic":
        base = (_env("AI_BASE_URL") or "https://api.anthropic.com/v1").rstrip("/")
        out = {"provider": provider, "base": base, "key": _env("AI_API_KEY") or _env("ANTHROPIC_API_KEY"),
               "model": _env("AI_MODEL") or _env("ANTHROPIC_MODEL") or ANTHROPIC_DEFAULT_MODEL, "server": "Anthropic"}
        out["problem"] = None if out["key"] else "Anthropic needs AI_API_KEY (or ANTHROPIC_API_KEY)."
    elif provider == "openai_compatible":
        base = _env("AI_BASE_URL").rstrip("/")
        out = {"provider": provider, "base": base, "key": _env("AI_API_KEY"), "model": _env("AI_MODEL"), "server": _server_name(base) if base else ""}
        out["problem"] = None if base and out["model"] else "openai_compatible needs AI_BASE_URL and AI_MODEL."
    else:
        out = {"provider": "none", "base": "", "key": "", "model": "", "server": "",
               "problem": None if provider in ("none", "off", "") else f"Unknown AI_PROVIDER '{provider}'. Use anthropic or openai_compatible."}
    # AI_THINKING=off asks a reasoning model to answer straight away. On Ollama only reasoning_effort "none" does this
    # through /chat/completions (its own "think": false is ignored there); a model left thinking can spend a short
    # reply's whole token allowance on reasoning and return nothing.
    # AI_THINKING=on (or low/medium/high, for models with effort levels such as gpt-oss on Groq) lets it think, with a
    # much larger allowance so the reasoning can't use up the reply.
    t = _env("AI_THINKING").lower()
    out["thinking"] = ("off" if t in ("off", "false", "no", "0", "none") else "on" if t in ("on", "true", "yes", "1")
                       else t if t in ("low", "medium", "high") else "default")
    # AI_API_KEY may hold several keys separated by commas: the first one not resting after a limit is used
    out["keys"] = split_keys(out.get("key", ""))
    out["key"] = next((k for k in out["keys"] if not key_resting(k)), out["keys"][0] if out["keys"] else "")
    # A key still set to the example text would fail on every call, so it counts as not set up
    if out.get("key", "").lower() in PLACEHOLDER_KEYS and not out["problem"]:
        out["problem"] = f"AI_API_KEY in .env is still the placeholder '{out['key']}'. Put your key there and restart the app."
    try:
        out["timeout"] = float(_env("AI_TIMEOUT")) if _env("AI_TIMEOUT") else (LOCAL_TIMEOUT if _is_local(out["base"]) else REMOTE_TIMEOUT)
    except ValueError:
        out["timeout"] = REMOTE_TIMEOUT
    return out


def split_keys(raw):
    return list(dict.fromkeys(k.strip() for k in (raw or "").split(",") if k.strip()))


def _key_id(key):
    """A key's fingerprint for the record of resting keys: the key itself is never stored."""
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def key_resting(key):
    import time
    from . import db
    try:
        until = db.cache_get(f"aikey:rest:{_key_id(key)}", 10 ** 9)
    except Exception:  # a database not set up yet: no key is resting
        return False
    return bool(until) and time.time() < until


def rest_key(key, seconds):
    """Put a key aside after a rate limit, for as long as the provider said (at least a minute)."""
    import time
    from . import db
    db.cache_set(f"aikey:rest:{_key_id(key)}", time.time() + max(60.0, float(seconds or 0)))


def is_limit(status, text):
    """A refusal that another key could get past: a rate limit, or Groq's 'blocked at the organization level',
    which it sent once the day's allowance was used up."""
    return status == 429 or (status == 403 and "blocked at the organization level" in (text or ""))


def switch_key(c, wait):
    """After a limit: rest the key in use and return the next one ready, or None when every key is resting."""
    if c.get("key"):
        rest_key(c["key"], wait if wait else (3600 if len(c.get("keys") or []) > 1 else 60))
    nxt = next((k for k in c.get("keys") or [] if k != c.get("key") and not key_resting(k)), None)
    if nxt:
        c["key"] = nxt
    return nxt


def available():
    c = config()
    return c["provider"] != "none" and not c["problem"]


def describe():
    """For the sidebar: which provider and model, or why there's none. Never includes the key."""
    c = config()
    return {"on": available(), "provider": c["provider"], "server": c["server"], "model": c["model"], "thinking": c["thinking"], "problem": c["problem"]}


def clean(text):
    """A model's reply without reasoning blocks (<think>...</think>, used by some local models)."""
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text or "", flags=re.S | re.I)
    # gpt-oss marks where a fact came from with its own references ("【get_stock†L31-L33】"), meaningless to a reader
    text = re.sub(r"\s?【[^】\n]{0,80}】", "", text)
    return text.strip()


def complete(user, system=SYSTEM, max_tokens=900, temperature=0):
    c = config()
    if not available():
        raise RuntimeError(c["problem"] or "No AI provider is set up.")
    if c["provider"] == "anthropic":
        while True:
            r = httpx.post(
                f"{c['base']}/messages",
                headers={"x-api-key": c["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": c["model"], "max_tokens": max_tokens, "temperature": temperature, "system": system, "messages": [{"role": "user", "content": user}]},
                timeout=c["timeout"],
            )
            if not (is_limit(r.status_code, r.text) and switch_key(c, _retry_after(r))):
                break
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    else:
        headers = {"content-type": "application/json", "X-Title": "StockPilot"}
        body = {"model": c["model"], "max_tokens": max_tokens, "temperature": temperature, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if c["thinking"] == "off":
            body["reasoning_effort"] = "none"  # sent only when asked for, since not every server knows it
        elif c["thinking"] in ("low", "medium", "high"):
            body["reasoning_effort"] = c["thinking"]
        if c["thinking"] not in ("off", "default"):
            body["max_tokens"] = max_tokens * THINKING_ROOM  # reasoning counts against the allowance on most servers
        while True:
            if c["key"]:  # Ollama and LM Studio need none
                headers["authorization"] = f"Bearer {c['key']}"
            r = httpx.post(f"{c['base']}/chat/completions", headers=headers, timeout=c["timeout"], json=body)
            if not (is_limit(r.status_code, r.text) and switch_key(c, _retry_after(r))):
                break
        r.raise_for_status()
        text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = clean(text)
    if not text:
        raise RuntimeError("The model returned an empty reply.")
    return text


def _retry_after(r):
    """Seconds until a limited key can be used again, from retry-after ('1320', '22m0s') if given."""
    v = r.headers.get("retry-after") or ""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|m|s|h)?", v):
        total += float(num) * {"ms": 0.001, "m": 60, "h": 3600}.get(unit, 1)
    return total


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
    lines.append("_Rule-based summary. Set up an AI provider in .env (see .env.example) for AI-written analysis._")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- the brief's data, written for a reader
# Every figure goes to the model already in its unit ("142.1%", "38.1x", "4.98T USD"), with what it means. Raw values
# like 1.4213 for a return on equity get misread, and the checker below reads this same text, so the model and the
# check always agree on what the data says.

def _f_pct(v, d=1):
    return None if v is None else f"{v * 100:.{d}f}%"


def _f_x(v, d=1):
    return None if v is None else f"{v:.{d}f}x"


def _f_money(v, cur):
    if v is None:
        return None
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            return f"{v / div:.2f}{unit} {cur}"
    return f"{v:,.2f} {cur}"


def _f_num(v, d=2):
    return None if v is None else f"{v:.{d}f}"


COMPANY_WORDS = {"inc", "inc.", "corp", "corp.", "corporation", "company", "co", "co.", "ltd", "ltd.", "limited", "plc", "group", "holdings",
                 "holding", "ag", "se", "sa", "s.a.", "nv", "n.v.", "spa", "s.p.a.", "the", "class", "a", "b", "&", "and", "q.p.s.c.", "(q.p.s.c.)", "pjsc", "psc"}
GENERIC_WORDS = {"saudi", "qatar", "qatari", "national", "bank", "first", "general", "american", "united", "international", "global", "emirates",
                 "dubai", "abu", "al", "royal", "state", "street", "vanguard", "ishares", "spdr", "invesco", "etf", "trust", "fund", "index"}


def relevant_headlines(news, ticker, name, limit=8):
    """Headlines that name the company or its symbol. Yahoo's feed for a stock also carries general market stories,
    and a model will happily read an unrelated headline as news about this company."""
    root = ticker.split(".")[0].split("-")[0].upper()
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'.&-]*", name or "") if w.lower() not in COMPANY_WORDS]
    names = [" ".join(words)] if len(words) > 1 else []
    names += [w for w in words[:1] if len(w) >= 4 and w.lower() not in GENERIC_WORDS]
    out = []
    for n in news or []:
        text = f"{n.get('title', '')} {n.get('summary', '')}"
        if (len(root) >= 2 and re.search(rf"(?<![A-Za-z]){re.escape(root)}(?![A-Za-z])", text)) or any(re.search(rf"\b{re.escape(x)}\b", text, re.I) for x in names if x):
            out.append(n)
    return out[:limit]


SCORE_KEY = ("Scores run from 0 to 100, and higher is always better. Value: higher means cheaper. Quality: higher means more "
             "profitable and sounder. Growth: higher means faster growth. Momentum: higher means a stronger price trend. Risk: "
             "higher means calmer, so LOWER risk. Stance bands: 70+ Strong, 58+ Favorable, 45+ Neutral, 35+ Caution, below 35 Weak.")


def is_bank(info):
    """Yahoo files banks under industries such as "Banks - Diversified" and "Banks - Regional"."""
    return "bank" in (info.get("industry") or "").lower()


def brief_data(ctx):
    """The facts given to the model, grouped and labelled, each with its unit. Missing figures are left out rather
    than sent as null, so the model has nothing to guess about."""
    i, st, sc = ctx["info"], ctx["stats"], ctx["scores"]
    cur, fin_cur = i.get("currency") or "", i.get("financialCurrency") or i.get("currency") or ""
    price = ctx.get("price")
    target = i.get("targetMeanPrice")
    groups = {
        "Company": {"Name": i.get("name"), "Symbol": ctx["ticker"], "Asset class": i.get("assetClass"), "Sector": i.get("sector"),
                    "Industry": i.get("industry"), "Region": i.get("region"), "Quote currency": cur,
                    "Market capitalisation": _f_money(i.get("marketCap"), cur)},
        "Price": {"Last price": _f_money(price, cur) if price is not None else None,
                  "Analysts' mean target price": _f_money(target, cur),
                  "Target vs last price": _f_pct(target / price - 1) if target and price else None,
                  "Analyst consensus": i.get("recommendationKey"),
                  "Number of analysts": int(i["numberOfAnalystOpinions"]) if i.get("numberOfAnalystOpinions") else None},
        "Valuation": {"P/E, trailing 12 months": _f_x(i.get("trailingPE")), "P/E, forward": _f_x(i.get("forwardPE")),
                      "Price to book": _f_x(i.get("priceToBook")), "EV to EBITDA": _f_x(i.get("enterpriseToEbitda")),
                      "PEG ratio": _f_num(i.get("pegRatio"))},
        "Profitability and balance sheet": {"Return on equity": _f_pct(i.get("returnOnEquity")), "Operating margin": _f_pct(i.get("operatingMargins")),
                                            "Net margin": _f_pct(i.get("profitMargins")), "Debt to equity": _f_x(i.get("debtToEquity"), 2),
                                            "Current ratio": _f_x(i.get("currentRatio"), 2), "Free cash flow, last 12 months": _f_money(i.get("freeCashflow"), fin_cur)},
        # Several years first, as the growth score uses; the latest quarter separately, so a one-quarter swing isn't
        # read as the trend
        "Growth": {"Revenue growth a year, over 3 years": _f_pct(i.get("revenueGrowth3y")), "Net income growth a year, over 3 years": _f_pct(i.get("earningsGrowth3y")),
                   "Revenue growth, latest quarter vs a year before": _f_pct(i.get("revenueGrowth")),
                   "Earnings growth, latest quarter vs a year before": _f_pct(i.get("earningsGrowth"))},
        "Dividend": {"Dividend yield": _f_pct(i.get("dividendYield"), 2), "Payout ratio": _f_pct(i.get("payoutRatio"))},
        "Price performance and risk": {"Return, 1 month": _f_pct(st.get("ret1m")), "Return, 3 months": _f_pct(st.get("ret3m")),
                                       "Return, year to date": _f_pct(st.get("ytd")), "Return, 1 year": _f_pct(st.get("ret1y")),
                                       "Annual growth rate, 5 years": _f_pct(st.get("ret5yCagr")), "Volatility, 1 year (annualised)": _f_pct(st.get("vol1y")),
                                       "Largest fall from a peak, 1 year": _f_pct(st.get("maxDD1y")), "Sharpe ratio, 1 year": _f_num(st.get("sharpe1y")),
                                       "Beta": _f_num(i.get("beta"))},
        "StockPilot scores": {"Composite": f"{sc['composite']:.1f} out of 100" if sc.get("composite") is not None else "not scored",
                              "Stance": sc.get("stance"),
                              **{f"{k.capitalize()} factor": (f"{v:.1f} out of 100" if v is not None else "doesn't apply or no data") for k, v in sc["factors"].items()}},
    }
    out = {g: {k: v for k, v in rows.items() if v not in (None, "")} for g, rows in groups.items()}
    out["What the scores mean"] = SCORE_KEY
    out["Signals found by StockPilot"] = [s["text"] for s in sc.get("signals") or []]
    # A bank's free cash flow swings with its deposits and loans, and its debt is what it lends from, so neither says
    # much about it: they're left out, and the model is told why, instead of reading them as warning signs
    bank = is_bank(i)
    if bank:
        for k in ("Debt to equity", "Current ratio", "Free cash flow, last 12 months"):
            out.get("Profitability and balance sheet", {}).pop(k, None)
        out["Note on banks"] = ("This is a bank. Free cash flow, debt to equity and the current ratio are left out: a bank's cash "
                                "flow moves with its deposits and loans, and deposits and borrowing are what it lends from, so they "
                                "don't measure its health. Judge it on profitability, valuation, growth and price behaviour.")
    fields = (("Revenue", "revenue"), ("Net income", "netIncome")) + (() if bank else (("Free cash flow", "fcf"),))
    fin = [{"Year": r["year"], **{k: _f_money(r.get(src), fin_cur) for k, src in fields if r.get(src) is not None}}
           for r in ctx.get("financials") or []]
    if fin:
        out["Annual accounts, oldest first"] = fin
    heads = relevant_headlines(ctx.get("news"), ctx["ticker"], i.get("name"))
    out["Recent headlines naming the company"] = [f"{(n.get('time') or '')[:10]} {n.get('publisher') or ''}: {n['title']}".strip() for n in heads] or \
        "None of the recent headlines name the company."
    return {k: v for k, v in out.items() if v}


# No real-looking numbers in here: a small model copied an example figure from an earlier version into a brief about another asset
BRIEF_RULES = (
    "Rules: use only the facts in DATA. Write every figure exactly as DATA gives it, keeping its unit (a percentage stays a "
    "percentage, a multiple stays a multiple); you may round, but never convert units or calculate new figures. Don't "
    "compare with history, peers or averages unless DATA gives that comparison. If something isn't in DATA, say it isn't available "
    "rather than guessing. Read the scores with the key in DATA: a high risk score means LOW risk. Only discuss headlines listed in DATA. "
    "In What would change the view, describe triggers in words or by the figures in DATA. Don't invent price levels, "
    "thresholds or target multiples: every number you write must appear in DATA.")


# ---------------------------------------------------------------- checking the brief's figures against the data
_NUM = re.compile(r"(?<![\w.])([-+−]?)(?:[$€£]\s?)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?(%|x\b|×|times\b|bn\b|[TBMk]\b|trillion\b|billion\b|million\b|thousand\b)?", re.I)
_SCALE = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}
# Numbers that are names or periods, not figures: "S&P 500", "50-day", "12-1 month", dates, list markers
# An index's name with its number ("S&P 500", "Nikkei 225"), but not a level written after the name ("Nasdaq 24,579.43")
_NOT_FIGURES = re.compile(r"\b(?:S&P|Nasdaq|Russell|FTSE|Stoxx|Euro Stoxx|DAX|Dow|Nikkei|MSCI ACWI|ACWI)[ -]?\d+\b(?![.,]\d)|\b\d{4}-\d{2}-\d{2}\b|"
                          r"\b\d+(?:-\d+)?[ -](?:day|days|week|weeks|month|months|year|years|quarter|quarters)\b|\bQ[1-4]\b|^\s*\d+\.\s|"
                          # dates written with the month's name: "Oct 14", "14 October", "Jan 3, 2022"
                          r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b(?![.,]\d)|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\b", re.I | re.M)


# Typographic dashes, minus signs and narrow spaces, which some models use ("50‑day", "‑1.0 %"), read as plain ones
_PLAIN = str.maketrans({**{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2212\ufe63\uff0d"}, **{c: " " for c in "\u00a0\u2007\u2009\u202f"}})


def _figures(text, at=None):
    """(value, unit, decimals, as written) for each figure in a text, with % / x / money scale as its unit. Given a
    list as `at`, each figure's position in the text is added to it (every step below keeps the text's length)."""
    out = []
    # A narrow or no-break space between digit groups is a thousands separator ("24 579.43", as some models write it)
    text = re.sub(r"(?<=\d)[    ](?=\d{3}(?!\d))", ",", text)
    for m in _NUM.finditer(_NOT_FIGURES.sub(lambda x: " " * len(x.group(0)), text.translate(_PLAIN))):
        sign, whole, frac, unit = m.groups()
        value = float(whole.replace(",", "") + ("." + frac if frac else "")) * (-1 if sign in ("-", "−") else 1)
        u = (unit or "").lower()
        u = "%" if u == "%" else "x" if u in ("x", "×", "times") else "money" if u in _SCALE else ""
        if u == "money":
            value *= _SCALE[unit.lower()]
        out.append((value, u, len(frac or ""), m.group(0).strip()))
        if at is not None:
            at.append(m.start())
    return out


NEAR = 200  # characters: a figure worked out from others is written next to them ("5.6%, so core is 94.4%")


def _near(text, a, b):
    """Two positions on the same line, or close together outside a table (a table's rows are separate facts)."""
    lo, hi = sorted((a, b))
    if "\n" not in text[lo:hi]:
        return True
    line = lambda i: text[text.rfind("\n", 0, i) + 1:].lstrip()[:1]
    return hi - lo <= NEAR and "|" not in (line(lo), line(hi))


_QUOTE = re.compile(r"[“\"]([^”\"\n]{20,400})[”\"]")


def _words(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def check_quotes(text, facts):
    """Quoted passages (20+ characters) that aren't in the sources word for word. A model asked for news rewrote a
    headline in its own words and kept the quotation marks. Punctuation and case are ignored, and a quote joining a
    headline to its summary with a dash or an ellipsis is checked part by part."""
    hay, bad = _words(facts), []
    for q in _QUOTE.findall(text or ""):
        parts = [p for p in re.split(r"\s[–—-]\s|…|\.\.\.", q) if len(_words(p).split()) >= 3]
        if parts and not all(_words(p) in hay for p in parts):
            bad.append(q.strip())
    return list(dict.fromkeys(bad))


def check_figures(text, facts):
    """Which figures in the brief can be found in the data, allowing for rounding. A figure must match one with the same
    unit (a ROE of 142.1% doesn't make "1.42" right); a bare number may match any. Years and small counts are skipped."""
    known = _figures(facts)
    checked, unmatched, verified = 0, [], []
    at = []
    for n, (value, unit, dec, written) in enumerate(_figures(text, at)):
        skip = not unit and (1900 <= value <= 2100 and dec == 0 or (abs(value) <= 12 and dec == 0))  # a year, or a count like "3 risks"
        if not skip:
            checked += 1
        scale = 1e12 if abs(value) >= 1e12 else 1e9 if abs(value) >= 1e9 else 1e6 if abs(value) >= 1e6 else 1e3 if abs(value) >= 1e3 and unit == "money" else 1

        def same(k):
            # Rounded to the coarser of the two precisions, and without the sign, since "fell 13.8%" is how -13.8% reads
            kv, ku, kdec, _ = k
            # An amount written with a scale ("8.5k") may match one written in full ("8,454.00 EUR")
            if unit and ku != unit and not (unit == "money" and ku == ""):
                return False
            tol = 0.5 * 10 ** -min(dec, kdec) * (scale if unit == "money" else 1) + 1e-9
            return abs(abs(value) - abs(kv)) <= tol
        found = any(same(k) for k in known)
        if found:
            verified.append(((value, unit, dec, written), at[n]))  # a part a worked-out figure may use, even one not checked ("2,000")
        elif not skip:
            unmatched.append(((value, unit, dec, written), at[n]))
    # A figure not in the data may still be right: worked out from figures the answer itself states and that are in the
    # data ("35,816.51 = 33,816.51 + 2,000", "94% = 100% - 6%"). Only those count as the parts, so a coincidence among
    # all the data's numbers can't pass off a slip like "7 + 31 = 40" as worked out; and only those written near it,
    # since among forty figures across an answer two unrelated ones multiplied to an invented fund cost ("10.1% of 2.2%").
    derived, still = [], []
    for fig, pos in unmatched:
        how = _worked_out(fig, [f for f, p in verified if _near(text, pos, p)])
        (derived if how else still).append((fig[3], how))
    return {"checked": checked, "unmatched": list(dict.fromkeys(w for w, _ in still)),
            "derived": [{"figure": w, "how": h} for w, h in dict(derived).items()]}


def _worked_out(fig, parts):
    """How `fig` follows from two of `parts` (or one and 100), within its rounding, or None."""
    value, unit, dec, _ = fig
    target = abs(value)
    scale = 1e12 if target >= 1e12 else 1e9 if target >= 1e9 else 1e6 if target >= 1e6 else 1e3 if target >= 1e3 and unit == "money" else 1
    tol = 0.5 * 10 ** -dec * scale + 1e-9
    near = lambda x: abs(abs(x) - target) <= max(tol, target * 1e-4)
    vals = list({(abs(p[0]), p[3], p[1]) for p in parts if p[0]})[:40]
    for i, (a, wa, ua) in enumerate(vals):
        if unit == "%" and ua == "%" and near(100 - a):
            return f"100% − {wa}"
        for b, wb, ub in vals[i + 1:] + vals[:i]:
            # a sum or a difference keeps the unit: percentages from percentages, amounts from amounts
            keeps = ua == ub and (ua == unit or {ua, unit} <= {"", "money"})
            if keeps and near(a + b):
                return f"{wa} + {wb}"
            if keeps and near(a - b):
                return f"{wa} − {wb}" if a >= b else f"{wb} − {wa}"
            # a share or a change is of amounts, not of percentages ("from 10.1% to 8%" isn't a figure anyone writes)
            if b and unit == "%" and "%" not in (ua, ub) and near(a / b * 100):
                return f"{wa} as a share of {wb}"
            if b and unit == "%" and "%" not in (ua, ub) and near((a / b - 1) * 100):
                return f"change from {wb} to {wa}"
            if (ua == "%") != (ub == "%") and near(a * b / 100):  # a percentage of an amount
                return f"{wa} of {wb}"
            if b and unit != "%" and near(a / b):
                return f"{wa} ÷ {wb}"
            if unit != "%" and near(a * b):
                return f"{wa} × {wb}"
    return None


def check_brief(text, facts):
    """The figure check, with the triggers kept apart. Levels a model suggests for what would change the view
    ("if growth falls below 10%") aren't claims about the data, so they're listed as its own suggestions rather than
    as figures missing from the data. Every other figure must be found in the data."""
    m = re.search(r"what would change the view", text, re.I)
    claims, triggers = (text[:m.start()], text[m.start():]) if m else (text, "")
    a, b = check_figures(claims, facts), check_figures(triggers, facts)
    return {"checked": a["checked"] + b["checked"], "unmatched": a["unmatched"], "suggested": b["unmatched"], "derived": a["derived"] + b["derived"]}


def brief(ctx):
    if not available():
        return {"source": "rules", "text": rule_brief(ctx)}
    facts = json.dumps(brief_data(ctx), ensure_ascii=False, indent=1)
    prompt = ("Write a research brief for this asset in markdown with these sections: **Snapshot** (2 sentences), "
              "**Bull case** (3 bullets), **Bear case** (3 bullets), **What the numbers say** (valuation, quality, momentum, risk), "
              "**News read**, **What would change the view** (2-3 triggers). Max ~300 words.\n\n" + BRIEF_RULES + "\n\nDATA:\n" + facts)
    c = config()
    try:
        text = complete(prompt)
        return {"source": "ai", "text": text, "by": f"{c['model']} via {c['server']}", "check": check_brief(text, facts)}
    except httpx.TimeoutException:
        return {"source": "rules", "text": rule_brief(ctx) + f"\n\n_(The AI model didn't answer within {c['timeout']:g} seconds.)_"}
    except Exception as e:
        return {"source": "rules", "text": rule_brief(ctx) + f"\n\n_(AI call failed: {str(e)[:200]})_"}


# ---------------------------------------------------------------- natural-language screener
FILTER_SPEC = {
    "markets": "list of any of North America, Europe, Latin America, Middle East & Africa, Asia-Pacific, ETFs, Crypto",
    "countries": "list of countries within those, e.g. United States, Japan, Qatar, Saudi Arabia, UAE (the Gulf is Qatar, Saudi Arabia, UAE, Kuwait)",
    "max_pe": "number", "min_div_yield": "fraction e.g. 0.03", "min_rev_growth": "fraction", "max_de": "number (x)",
    "min_roe": "fraction", "min_score": "0-100", "min_momentum": "0-100 momentum factor score", "max_vol": "fraction annual volatility",
    "sort": "one of composite, value, quality, growth, momentum, risk, divYield",
    "sector": "one of Technology, Financial Services, Energy, Healthcare, Utilities, Real Estate, Industrials, Communication Services, Basic Materials, Consumer Defensive, Consumer Cyclical",
    "exclude_sector": "a sector from the same list that the request rules out, e.g. 'not tech'",
}
# What vague words mean in this app. The model is told, so it reads them the same way as the rules, and where the
# rules found a value it stands: a model left to choose picks its own, often stricter, limits and empties the screen.
MEANINGS = ("cheap/undervalued/value: max_pe 18. low debt: max_de 0.5. profitable/quality: min_roe 0.12. dividend/income: "
            "min_div_yield 0.025. growth/growing: min_rev_growth 0.10. momentum/trending: min_momentum 60. low volatility/low "
            "risk/safe/defensive: max_vol 0.25 (these words describe risk, not a sector). Only set a sector when the request "
            "names an industry. Use a number the request gives in place of these.")

# Regions, and the funds and crypto groups, as the Screener's markets
MARKET_WORDS = {"North America": ["north america", "north american"], "Europe": ["europe", "european", "eu"],
                "Latin America": ["latin america", "latin american", "latam", "south america", "south american"],
                "Middle East & Africa": ["middle east", "mea", "africa", "african"],
                "Asia-Pacific": ["asia", "asian", "asia-pacific", "apac"],
                "ETFs": ["etf", "etfs"], "Crypto": ["crypto", "cryptocurrency", "bitcoin"]}
# Countries, as a filter within the regions. "China" reads as Hong Kong, where the big Chinese companies in the list
# are listed. The Gulf is a group of countries.
COUNTRY_WORDS = {
    "United States": ["usa", "u.s.", "u.s", "american", "america"], "Canada": ["canada", "canadian"],
    "Brazil": ["brazil", "brazilian"], "Mexico": ["mexico", "mexican"], "Chile": ["chile", "chilean"],
    "Argentina": ["argentina", "argentine", "argentinian"], "Peru": ["peru", "peruvian"], "Colombia": ["colombia", "colombian"],
    "United Kingdom": ["uk", "u.k.", "british", "britain", "england"], "Germany": ["germany", "german"], "France": ["france", "french"],
    "Switzerland": ["switzerland", "swiss"], "Netherlands": ["netherlands", "dutch"], "Spain": ["spain", "spanish"],
    "Italy": ["italy", "italian"], "Denmark": ["denmark", "danish"], "Sweden": ["sweden", "swedish"], "Norway": ["norway", "norwegian"],
    "Finland": ["finland", "finnish"], "Belgium": ["belgium", "belgian"],
    "Qatar": ["qatar", "qatari"], "Saudi Arabia": ["saudi", "saudi arabia", "saudi arabian"], "UAE": ["uae", "dubai", "emirati", "emirates"],
    "Kuwait": ["kuwait", "kuwaiti"], "Israel": ["israel", "israeli"], "Turkey": ["turkey", "turkish", "turkiye"],
    "South Africa": ["south africa", "south african"],
    "Japan": ["japan", "japanese"], "Hong Kong": ["hong kong", "china", "chinese"], "India": ["india", "indian"],
    "South Korea": ["korea", "korean", "south korea"], "Taiwan": ["taiwan", "taiwanese"], "Australia": ["australia", "australian"],
    "Singapore": ["singapore", "singaporean"], "Indonesia": ["indonesia", "indonesian"],
}
GROUP_WORDS = {"Gulf": ["gulf", "gcc"]}
SECTOR_WORDS = {"Technology": ["tech", "technology", "software", "semiconductor", "semiconductors"],
                "Financial Services": ["bank", "banks", "financial", "financials"], "Energy": ["energy", "oil"],
                "Healthcare": ["health", "healthcare", "pharma"], "Utilities": ["utility", "utilities"],
                "Real Estate": ["reit", "reits", "property"], "Industrials": ["industrial", "industrials"],
                "Communication Services": ["telecom", "telecoms", "media"], "Basic Materials": ["materials", "mining"]}
# Words the rules act on, and words that don't change a screen, so neither is reported as not understood
RULE_WORDS = {"banking", "strong", "fast", "high", "p/e", "pe", "ratio", "yield", "yielding", "dividend", "dividends", "income", "growth", "growing", "revenue", "cheap", "undervalued", "value",
              "low", "debt", "no", "profitable", "quality", "momentum", "trending", "uptrend", "vol", "volatility", "risk", "safe", "defensive", "under", "below",
              "above", "over", "less", "than", "more", "at", "least", "most", "of", "us", "real", "estate", "middle", "east"}
FILLER_WORDS = {"stock", "stocks", "share", "shares", "company", "companies", "name", "names", "equities", "market", "markets", "listed", "with", "and", "or", "in",
                "the", "a", "an", "for", "from", "that", "have", "has", "is", "are", "me", "show", "find", "give", "get", "tell", "list", "best", "good", "great", "top", "style", "payers", "paying", "pay", "pays", "generous", "famous"}


FRACTIONS = ("min_div_yield", "min_rev_growth", "min_roe", "max_vol")
SORTS = ("composite", "value", "quality", "growth", "momentum", "risk", "divYield")
SECTORS = [x.strip() for x in FILTER_SPEC["sector"].removeprefix("one of ").split(",")]


def _first_json_object(raw):
    """The first {...} in a reply, after removing reasoning blocks and markdown code fences. Small models often
    wrap JSON in ```json fences or add a sentence before or after it."""
    text = re.sub(r"```(?:json)?", "", clean(raw), flags=re.I)
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in the reply")


def _tidy_filters(raw):
    """Keep only what the screener understands, in its units. A small model may write 3 for a 3% yield, give
    numbers as text, or invent keys, so each value is checked rather than trusted."""
    out = {}
    for k in ("max_pe", "max_de", "min_score", "min_momentum", *FRACTIONS):
        v = raw.get(k)
        try:
            v = float(str(v).strip().rstrip("%")) if v not in (None, "", False) else None
        except ValueError:
            v = None
        if v is None or v != v or v < 0:
            continue
        if k in FRACTIONS and v > 1:
            v = v / 100  # "3" or "3%" for a 3% yield
        out[k] = v
    markets = raw.get("markets")
    if isinstance(markets, str):
        markets = [markets]
    if isinstance(markets, list):
        from .universe import LEGACY_MARKETS
        known = {m.lower(): m for m in MARKET_WORDS}
        old = {m.lower(): cs for m, cs in LEGACY_MARKETS.items()}
        picked = [known[str(m).strip().lower()] for m in markets if str(m).strip().lower() in known]
        if picked:
            out["markets"] = list(dict.fromkeys(picked))
        meant = [c for m in markets for c in old.get(str(m).strip().lower(), [])]  # a model that still says "US" or "Gulf"
        if meant:
            out["countries"] = list(dict.fromkeys(meant))
    countries = raw.get("countries")
    if isinstance(countries, str):
        countries = [countries]
    if isinstance(countries, list):
        from .universe import COUNTRY_GROUPS, REGION_OF_COUNTRY
        known = {c.lower(): [c] for c in REGION_OF_COUNTRY} | {g.lower(): cs for g, cs in COUNTRY_GROUPS.items()}
        picked = [c for x in countries for c in known.get(str(x).strip().lower(), [])]
        if picked:
            out["countries"] = list(dict.fromkeys([*out.get("countries", []), *picked]))
    if raw.get("sort") in SORTS:
        out["sort"] = raw["sort"]
    for key in ("sector", "exclude_sector"):
        sector = {x.lower(): x for x in SECTORS}.get(str(raw.get(key) or "").strip().lower())
        if sector:
            out[key] = sector
    return out


def parse_query(text):
    """Turn a natural-language screen into filters. The keyword rules always run, because they reliably catch what was
    literally asked for; a model, when one is set up, adds to them and overrides where it read a value. Small local
    models in particular drop words like "cheap" or "low debt", so their reply alone would screen less than was asked.
    An unusable reply is asked for once more, and after that the rules stand alone."""
    rules = _parse_rules(text.lower())
    if available():
        prompt = ("Convert this stock screen request into a JSON object using only these keys (omit unused ones): "
                  + json.dumps(FILTER_SPEC) + "\nWhat words mean here: " + MEANINGS
                  + "\nReply with the JSON object only, no explanation.\nREQUEST: " + text)
        for attempt in range(2):
            try:
                raw = complete(prompt if attempt == 0 else prompt + "\nYour last reply couldn't be read. Reply with one JSON object and nothing else.",
                               system="You convert screening requests into strict JSON filters.", max_tokens=300)
                filters = _tidy_filters(_first_json_object(raw))
                if filters:
                    # The rules' values stand (a number you typed, or a word's defined meaning); the model fills in
                    # what they couldn't read, such as a market or sector named in other words
                    merged = {**filters, **{k: v for k, v in rules.items() if k != "ignored"}}
                    if merged.get("sector") and merged.get("sector") == merged.get("exclude_sector"):
                        merged.pop("sector")
                    if "exclude_industry" in rules and "exclude_sector" not in rules:  # "excluding banks" rules out banks, not every financial company
                        merged.pop("exclude_sector", None)
                    return merged
            except Exception:
                continue
    return rules


def _has(t, *words):
    return any(re.search(rf"(?<![\w/.]){re.escape(w)}(?![\w/])", t) for w in words)


def _parse_rules(t):
    """Keyword rules, matching whole words. Anything they can't act on comes back under "ignored" to show the user."""
    f, ignored = {}, []

    # Set aside what can't be honoured, rather than reading "not cheap" as cheap or dividend growth as yield
    def set_aside(m):
        ignored.extend(m.group(0).split()[1 if m.group(0).split()[0] in ("not", "non") else 0:])
        return " "

    # "not tech", "no banks", "excluding energy": a sector ruled out, which the screen can apply. Other words after
    # "not" are set aside; after "no" they're left alone, since "no debt" means low debt.
    def rule_out(m):
        neg, word = m.group(1), m.group(2)
        if word in ("bank", "banks", "banking"):  # banks, not insurers and the rest of the financial sector
            f["exclude_industry"] = "Banks"
            return " "
        sector = next((s for s, ws in SECTOR_WORDS.items() if word in ws), None)
        if sector:
            f["exclude_sector"] = sector
            return " "
        return set_aside(m) if neg in ("not", "non") else m.group(0)
    t = re.sub(r"\b(not|non|no|excluding|except|without)[ -](\w+)", rule_out, t)
    t = re.sub(r"\bdividend growth\b", set_aside, t)

    from .universe import COUNTRY_GROUPS, REGION_OF_COUNTRY
    # "Latin American", "North American" and "South American" hold "American", which isn't the US there
    not_latin = re.sub(r"\b(?:latin|north|south)[ -]americ(?:a|an)\b", " ", t)
    countries = [c for c, words in COUNTRY_WORDS.items() if _has(not_latin if c == "United States" else t, *words)]
    countries += [c for g, words in GROUP_WORDS.items() if _has(t, *words) for c in COUNTRY_GROUPS[g]]
    # "South African" holds "African": a region a named country is in isn't asked for as a whole
    markets = [m for m, words in MARKET_WORDS.items() if _has(t, *words) and not any(REGION_OF_COUNTRY.get(c) == m for c in countries)]
    # "us" on its own is usually the pronoun, as in "show us"
    # ...but before "stocks" or a sector ("US banks", "US tech") it's the country
    nouns = "|".join(["stocks?", "shares", "companies", "market", "equities", "listed", "names"] + [re.escape(w) for ws in SECTOR_WORDS.values() for w in ws])
    if re.search(rf"(?<!show )(?<!give )(?<!find )(?<!tell )(?<!get )(?<!let )\bus[ -](?:[a-z]+ ){{0,2}}(?:{nouns})\b|\bin the us\b", t):
        countries.insert(0, "United States")
    if markets:
        f["markets"] = list(dict.fromkeys(m for m in MARKET_WORDS if m in markets))
    if countries:
        f["countries"] = list(dict.fromkeys(countries))
    for sector, words in SECTOR_WORDS.items():
        if _has(t, *words) or (sector == "Real Estate" and "real estate" in t):
            f["sector"] = sector
            break
    # "Banks" means banks, not every financial company: insurers and asset managers are Financial Services too
    if _has(t, "bank", "banks", "banking"):
        f["industry"] = "Banks"

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
        f.setdefault("sort", "growth")
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

    known = RULE_WORDS | FILLER_WORDS | {w for ws in (*MARKET_WORDS.values(), *COUNTRY_WORDS.values(), *GROUP_WORDS.values(), *SECTOR_WORDS.values()) for w in ws}
    ignored += [w for w in re.findall(r"[a-z][a-z/.'-]*", t) if w.strip(".'") not in known]
    if ignored:
        f["ignored"] = list(dict.fromkeys(ignored))
    return f
