"""ClearYield API + static frontend.  Run:  uvicorn backend.main:app --reload --port 8001"""
import asyncio
import datetime as dt
import json
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def _load_env(env=ROOT / ".env"):
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():  # -sig: Notepad can start a file with a byte-order mark
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                k = "CLEARYIELD_" + k[len("STOCKPILOT_"):] if k.startswith("STOCKPILOT_") else k  # the app's old name
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


_load_env()


SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


def _cert_names(der):
    """A certificate's (subject, issuer), read by Python's own parser."""
    import ssl
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="ascii") as f:
        f.write(ssl.DER_cert_to_PEM_cert(der))
    try:
        d = ssl._ssl._test_decode_cert(f.name)
        return d.get("subject"), d.get("issuer")
    finally:
        os.unlink(f.name)


def _chained_intermediates(roots, store):
    """The certificates in `store` whose chain leads up to one of `roots`, through any number of intermediates."""
    trusted = {_cert_names(der)[0] for der in roots}
    # Only those Windows allows for websites, as with the roots: a code-signing intermediate isn't wanted here
    candidates = [(der, *_cert_names(der)) for der, enc, trust in store if enc == "x509_asn" and der not in roots and (trust is True or SERVER_AUTH in trust)]
    out, added = [], True
    while added:  # an intermediate can be issued by another intermediate that's already trusted
        added = False
        for der, subject, issuer in candidates:
            if der not in out and subject != issuer and issuer in trusted:
                out.append(der)
                trusted.add(subject)
                added = True
    return out


def _trust_windows_certificates(bundle=ROOT / "data" / "ca-bundle.pem"):
    """On Windows, trust the certificates Windows trusts, as the browser does. Antivirus HTTPS scanning (Kaspersky, for
    one) and company proxies re-sign secure connections with their own certificate, which Windows has but Python's
    own list doesn't, so every call to Yahoo or a cloud AI provider would fail. Both yfinance's curl_cffi and httpx
    read SSL_CERT_FILE, so one bundle covers them. A SSL_CERT_FILE you set yourself is left alone."""
    if os.name != "nt" or os.getenv("SSL_CERT_FILE"):
        return None
    try:
        import ssl

        import certifi
        pems = [Path(certifi.where()).read_text(encoding="ascii")]
        # Root certificates Windows trusts for websites (server authentication), not ones meant only for code signing
        # or email, so nothing is trusted here that Windows itself wouldn't trust for a website
        roots = [der for der, enc, trust in ssl.enum_certificates("ROOT") if enc == "x509_asn" and (trust is True or SERVER_AUTH in trust)]
        pems += [ssl.DER_cert_to_PEM_cert(der) for der in roots]
        # Plus the intermediates in Windows' CA store that chain up to one of those roots. Windows finds a missing
        # intermediate by itself; Python doesn't, so a network that inspects HTTPS through an intermediate (issued by a
        # root it installed) fails without it. One that doesn't chain to a trusted root is left out.
        pems += [ssl.DER_cert_to_PEM_cert(der) for der in _chained_intermediates(roots, ssl.enum_certificates("CA"))]
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text("\n".join(pems), encoding="ascii")
        os.environ["SSL_CERT_FILE"] = str(bundle)
        return bundle
    except Exception as e:  # never stop the app over this: it just behaves as before
        print(f"Could not add the Windows certificates: {e}")
        return None


_trust_windows_certificates()

import httpx  # noqa: E402
import numpy as np  # noqa: E402
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import agent, ai, backtest, data, db, insights, ledger, mailer, orders, portfolio, scorecheck, simulate, startup, tax  # noqa: E402
from . import indicators as ind  # noqa: E402
from .universe import COUNTRY_GROUPS, MACRO, REGIONS, UNIVERSE, countries_in, search as uni_search  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("clearyield")
# The page checks /api/progress every second or two while data downloads; those checks stay out of the request log
logging.getLogger("uvicorn.access").addFilter(lambda r: "/api/progress" not in r.getMessage() and "/api/ask/job/" not in r.getMessage())
ALERT_INTERVAL = int(os.getenv("ALERT_CHECK_MINUTES", "15")) * 60


def clean(o):
    """Make data JSON-safe (NaN/inf -> null, numpy -> python)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def ok(payload):
    return JSONResponse(clean(payload))


def _auto_backup():
    """This week's automatic backup, if it's due (db.auto_backup). A failure is logged and never stops the app."""
    try:
        written = db.auto_backup()
        if written:
            log.info("automatic backup written: %s", written)
    except Exception as e:
        log.warning("automatic backup failed: %s", e)


async def _alert_loop():
    while True:
        await asyncio.sleep(ALERT_INTERVAL)
        try:
            settings = db.get_settings()
            fired = await asyncio.to_thread(insights.check_alerts, settings)
            for a in fired:
                log.info("ALERT: %s", a["message"])
            await asyncio.to_thread(mailer.send_alerts, fired, settings)  # before the slower score recording
            await asyncio.to_thread(insights.record_today, settings)
            await asyncio.to_thread(_auto_backup)  # due once a week; a running app needn't be restarted for it
        except Exception as e:
            log.warning("alert check failed: %s", e)


@asynccontextmanager
async def lifespan(app):
    db.init()
    try:
        gone = db.cache_prune()
        if gone:
            log.info("removed %d cache entries not refreshed for %d days", gone, db.CACHE_KEEP_DAYS)
    except Exception as e:  # never stop the app over housekeeping
        log.warning("cache pruning failed: %s", e)
    _auto_backup()
    tasks = [asyncio.create_task(_alert_loop())]
    if not data.DEMO and os.environ.get("CLEARYIELD_WARM", "1") != "0":
        tasks.append(asyncio.create_task(_warm_loop()))
    yield
    for task in tasks:
        task.cancel()


WARM_EVERY = 6 * 3600  # company details are kept six hours, so scoring again then finds most of them still fresh


async def _warm_loop():
    """Scores every stock in the list in the background, soon after the app starts and then every six hours, so the
    Screener, Opportunities and sector ranks don't wait about a minute for the day's first downloads, and the table
    of sector ranks is always ready."""
    await asyncio.sleep(20)
    while True:
        try:
            started = time.time()
            # The background can wait out Yahoo's pauses: nobody is waiting for it
            table = await asyncio.to_thread(insights.peer_table, db.get_settings(), True, 900)
            log.info("background scoring: %d stocks with a sector in %.0f s", len(table), time.time() - started)
        except Exception as e:  # never stop the app over a warm-up
            log.warning("background scoring failed: %s", e)
        await asyncio.sleep(WARM_EVERY)


app = FastAPI(title="ClearYield", lifespan=lifespan)
FRONT = ROOT / "frontend"


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}


@app.middleware("http")
async def only_local_addresses(request, call_next):
    """The app has no login and only listens locally. Refusing other Host headers stops a website from
    pointing its own domain name at this machine to read and change your data through your browser."""
    host = request.headers.get("host", "")
    if (host if host.endswith("]") else host.rsplit(":", 1)[0]) not in LOCAL_HOSTS:
        return JSONResponse({"detail": "ClearYield only answers on localhost."}, status_code=400)
    if request.url.path.startswith("/api/") and _from_another_site(request, host):
        return JSONResponse({"detail": "ClearYield only answers its own page."}, status_code=403)
    return await call_next(request)


def _from_another_site(request, host):
    """True for a request another website (or another app on this machine) made through your browser. Browsers say
    where a request came from, and a page can't change that, so this stops a site you visit from clearing the cache,
    changing your data or spending your AI allowance. Your own page, a typed address and scripts with neither header
    (the tests, curl) pass."""
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    return bool(origin) and urlsplit(origin).netloc != host


@app.middleware("http")
async def always_revalidate(request, call_next):
    """Without this, browsers can reuse an old page, or another app's page on the same port, without asking."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FRONT / "favicon.svg", media_type="image/svg+xml")


def T(t):
    return t.strip().upper()


def _ticker(body):
    t = T(str(body.get("ticker") or ""))
    if not t:
        raise HTTPException(400, "Enter a ticker.")
    return t


def _number(body, key, default=None):
    """A finite number from the request, or a clear refusal. `default` applies only when the field wasn't sent."""
    v = body.get(key)
    if v is None and default is not None:
        return default
    try:
        if isinstance(v, bool):  # float(True) is 1.0, which would pass for a real number
            raise TypeError
        n = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key.replace('_', ' ').capitalize()} must be a number.")
    if not math.isfinite(n):
        raise HTTPException(400, f"{key.replace('_', ' ').capitalize()} must be a real number.")
    return n


def _hist_or_404(t, period="5y"):
    try:
        return data.history(t, period)
    except data.DataError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- meta / search / macro
def _tiles(settings):
    """Every tile you can pick: the built-in ones, then your own. Ticker -> display name, in display order."""
    return {**MACRO, **{c["ticker"]: c["name"] for c in settings.get("custom_tiles") or [] if c.get("ticker") not in MACRO}}


def _tile_list(settings):
    custom = {c["ticker"] for c in settings.get("custom_tiles") or []}
    return [{"ticker": t, "name": n, "custom": t in custom} for t, n in _tiles(settings).items()]


@app.get("/api/meta")
def meta():
    settings = db.get_settings()
    return ok({"demo": data.DEMO, "ai": ai.available(), "aiInfo": {**ai.describe(), "web": agent.web_source()}, "tiles": _tile_list(settings),
               "settings": settings, "markets": [*insights.followed(settings), "ETFs", "Crypto"], "stockMarkets": insights.followed(settings), "regions": REGIONS,
               "countries": {r: countries_in(r) for r in insights.followed(settings)}, "countryGroups": COUNTRY_GROUPS, "currencies": data.CURRENCIES, "alertKinds": insights.ALERT_KINDS,
               "email": mailer.status(), "startup": startup.status(), "autoBackup": db.auto_backup_status(), "strategies": backtest.STRATEGIES})


@app.get("/api/search")
def search(q: str = ""):
    hits = uni_search(q)
    known = {h["ticker"] for h in hits}
    hits += [h for h in data.search_symbols(q) if h["ticker"] not in known]
    raw = T(q)
    # Words the Screener's keyword rules read ("cheap", "banks", "dividend", in any case) are offered as a screen; first
    # when every word was understood, so "CHEAP" isn't taken for a symbol, and after the stocks when some weren't
    rules = ai._parse_rules(q.strip().lower()) if q.strip() else {}
    if any(k != "ignored" for k in rules):
        row = {"ticker": q.strip(), "name": "Ask the Screener", "market": "Screener", "screen": True}
        hits = [row, *hits] if not rules.get("ignored") else [*hits[:11], row]
    elif raw and not hits and " " not in raw:
        hits.append({"ticker": raw, "name": "Look up this symbol on Yahoo Finance", "market": "Any"})
    return ok(hits[:12])


@app.get("/api/macro")
def macro():
    out = []
    settings = db.get_settings()
    chosen = set(settings.get("dashboard_tiles") or [])
    for t, label in _tiles(settings).items():
        if t not in chosen:
            continue
        try:
            c = data.history(t, "1y")["Close"]
            out.append({"ticker": t, "name": label, "price": float(c.iloc[-1]), "day": ind.ret_over(c, 1),
                        "ret1m": ind.ret_over(c, 21), "spark": [float(v) for v in c.iloc[-60:]],
                        "stale": data.is_stale(t), "asOf": c.index[-1].strftime("%Y-%m-%d")})
        except Exception:
            continue
    return ok(out)


MAX_CUSTOM_TILES = 12


@app.post("/api/tiles")
def add_tile(body: dict = Body(...)):
    """Your own dashboard tile. It's only saved once Yahoo has returned at least two days of prices for it,
    because some symbols (the Gulf indexes, for one) answer with a single day and would show an empty tile."""
    t = _ticker(body)
    if any(c in t for c in " ,/\\"):
        raise HTTPException(400, "Enter a single Yahoo symbol, like ^AEX or IWDA.AS.")
    settings = db.get_settings()
    custom = list(settings.get("custom_tiles") or [])
    if t in MACRO:
        raise HTTPException(400, f"{t} is already in the list as \"{MACRO[t]}\". Tick it above.")
    if any(c["ticker"] == t for c in custom):
        raise HTTPException(400, f"You already have a tile for {t}.")
    if len(custom) >= MAX_CUSTOM_TILES:
        raise HTTPException(400, f"You can have up to {MAX_CUSTOM_TILES} custom tiles. Remove one first.")
    try:
        close = data.history(t, "1y")["Close"].dropna()
    except data.DataError:
        raise HTTPException(400, f"Yahoo has no price history for {t}. Check the symbol on finance.yahoo.com.")
    if len(close) < 2:
        raise HTTPException(400, f"Yahoo only returns {len(close)} day of prices for {t}, so the tile would be empty. Try a fund that tracks it.")
    name = str(body.get("name") or "").strip()[:40] or data.info(t).get("name") or t
    custom.append({"ticker": t, "name": name})
    shown = list(settings.get("dashboard_tiles") or []) + [t]
    saved = db.set_settings({"custom_tiles": custom, "dashboard_tiles": [x for x in _tiles({"custom_tiles": custom}) if x in shown]})
    return ok({"settings": saved, "tiles": _tile_list(saved)})


@app.delete("/api/tiles/{ticker}")
def remove_tile(ticker: str):
    t = T(ticker)
    settings = db.get_settings()
    custom = [c for c in settings.get("custom_tiles") or [] if c["ticker"] != t]
    if len(custom) == len(settings.get("custom_tiles") or []):
        raise HTTPException(404, f"There's no custom tile for {t}.")
    saved = db.set_settings({"custom_tiles": custom, "dashboard_tiles": [x for x in settings.get("dashboard_tiles") or [] if x != t]})
    return ok({"settings": saved, "tiles": _tile_list(saved)})


# ---------------------------------------------------------------- stock research
def _candles(view, times, lines=None):
    return {"time": times, "open": view["Open"].tolist(), "high": view["High"].tolist(), "low": view["Low"].tolist(),
            "close": view["Close"].tolist(), "volume": view["Volume"].tolist(), **({k: lines[k].tolist() for k in lines.columns} if lines is not None else {})}


def _chart(t, full, period, bars):
    if period in data.INTRADAY:
        # Times are the exchange's wall clock written as UTC seconds, so the chart's axis reads in local market time.
        # The indicators are left out: they're computed on daily prices and mean something else on 5-minute candles.
        try:
            view = data.intraday(t, period)
        except data.DataError as e:
            return {**_candles(full.iloc[:0], []), "intraday": data.INTRADAY[period], "note": str(e)}
        return {**_candles(view, [int(d.timestamp()) for d in view.index]), "intraday": data.INTRADAY[period]}
    view = data.slice_period(full, period)
    lines = ind.bundle(full).loc[view.index]
    if bars == "week":
        view, lines = data.weekly(view, lines, t)
    return {**_candles(view, [d.strftime("%Y-%m-%d") for d in view.index], lines), "bars": "week" if bars == "week" else "day"}


@app.get("/api/stock/{ticker}")
def stock(ticker: str, period: str = "1y", bars: str = "day"):
    t = T(ticker)
    settings = db.get_settings()
    try:
        full, inf, sc = insights.evaluate_ticker(t, settings)
    except data.DataError as e:
        options = [h["ticker"] for h in data.search_symbols(t)][:4]
        raise HTTPException(404, f"Yahoo has no data for {t}. Did you mean {', '.join(options)}?" if options else str(e))
    c = full["Close"]
    price, prev = float(c.iloc[-1]), float(c.iloc[-2]) if len(c) > 1 else None  # some Yahoo indices have one row
    candles = _chart(t, full, period, bars)
    stats = ind.stats(c, settings.get("risk_free_rate", 0.04))
    last_year = ind.window(full, 252)
    stats.update({"price": price, "change": price - prev if prev else None, "changePct": price / prev - 1 if prev else None,
                  # Yahoo's published range, because the history here is adjusted for dividends
                  "high52": inf.get("fiftyTwoWeekHigh") or float(last_year["High"].max()),
                  "low52": inf.get("fiftyTwoWeekLow") or float(last_year["Low"].min()),
                  "asOf": c.index[-1].strftime("%Y-%m-%d"), "stale": data.is_stale(t),
                  "rsi": float(ind.rsi(c).iloc[-1])})
    try:
        bench = data.history(settings.get("benchmark", "SPY"), "5y")["Close"]
        stats["betaCalc"] = ind.beta(ind.window(c, 504), bench)
    except data.DataError:
        pass
    watched = bool(db.q("SELECT 1 FROM watchlist WHERE ticker=?", (t,)))
    return ok({"ticker": t, "info": inf, "stats": stats, "scores": sc, "candles": candles,
               "financials": data.financials(t), "watched": watched, "events": insights.upcoming(inf), "scoreHistory": insights.score_history(t)})


@app.get("/api/quote/{ticker}")
def quote(ticker: str):
    t = T(ticker)
    return ok({"ticker": t, "price": float(_hist_or_404(t, "1mo")["Close"].iloc[-1]), "currency": data.info(t).get("currency")})


@app.get("/api/stock/{ticker}/earnings")
def stock_earnings(ticker: str):
    return ok(data.earnings_record(T(ticker)))


@app.get("/api/stock/{ticker}/ownership")
def stock_ownership(ticker: str):
    """Insiders and institutions, as Yahoo reports them; null when it has nothing (funds, coins, many markets)."""
    t = T(ticker)
    own = data.ownership(t)
    return ok({**own, "summary": insights.insider_summary(own), "currency": data.main_unit(data.info(t).get("currency"))} if own else None)


@app.get("/api/stock/{ticker}/peers")
def stock_peers(ticker: str):
    """Where the stock's scores stand within its sector; null for funds, crypto and stocks without a sector."""
    try:
        return ok(insights.sector_ranks(T(ticker), db.get_settings()))
    except data.DataError as e:
        raise HTTPException(404, str(e))


@app.get("/api/stock/{ticker}/fund")
def stock_fund(ticker: str):
    t = T(ticker)
    inf = data.info(t)
    if inf.get("assetClass") != "ETFs":
        return ok({"sectors": {}, "holdings": [], "expenseRatio": None})
    return ok({**data.fund(t), "expenseRatio": inf.get("netExpenseRatio")})


@app.get("/api/stock/{ticker}/news")
def stock_news(ticker: str):
    return ok(data.news(T(ticker)))


@app.post("/api/stock/{ticker}/brief")  # a POST because it can spend your AI allowance
def stock_brief(ticker: str):
    t = T(ticker)
    settings = db.get_settings()
    try:
        full, inf, sc = insights.evaluate_ticker(t, settings)
    except data.DataError as e:
        raise HTTPException(404, str(e))
    ctx = {"ticker": t, "info": inf, "scores": sc, "stats": ind.stats(full["Close"], settings.get("risk_free_rate", 0.04)),
           "news": data.news(t), "financials": data.financials(t), "price": float(full["Close"].iloc[-1])}
    return ok(ai.brief(ctx))


def _ask_error(e):
    """(status, message) for what stopped a question."""
    if isinstance(e, ValueError):
        return 400, str(e)
    if isinstance(e, httpx.TimeoutException):
        return 504, "The AI model didn't answer in time. Try again, perhaps with a narrower question."
    if isinstance(e, (RuntimeError, httpx.HTTPError)):
        return 503, str(e)[:300]
    log.exception("Ask failed", exc_info=e)
    return 500, "Something went wrong while answering. Try again."


def _follow(body):
    """The ids of earlier answers a question follows up, as the page sends them."""
    f = body.get("follow") or []
    return [str(x) for x in f if isinstance(x, str)][:agent.FOLLOW_MAX] if isinstance(f, list) else []


@app.post("/api/ask")  # a POST because it spends the AI allowance
def ask(body: dict = Body(...)):
    try:
        return ok(agent.ask(str(body.get("question") or ""), _follow(body)))
    except Exception as e:
        raise HTTPException(*_ask_error(e))


# A question runs in the background and the page checks on it twice a second, showing each look-up, wait and check as
# it happens (a question can take half a minute on a free plan, and one spinner line said nothing about why)
ASK_JOBS = {}       # id -> {"status", "events", "result" | "error", "at"}
ASK_JOBS_KEEP = 20


@app.post("/api/ask/start")
def ask_start(body: dict = Body(...)):
    question, follow = str(body.get("question") or "").strip(), _follow(body)
    if not question:
        raise HTTPException(400, "Ask a question.")
    if not ai.available():
        raise HTTPException(503, ai.config()["problem"] or "Ask needs an AI provider. Set AI_PROVIDER in .env (see .env.example).")
    jid = __import__("uuid").uuid4().hex[:12]
    job = ASK_JOBS[jid] = {"status": "running", "events": [], "at": time.time()}
    while len(ASK_JOBS) > ASK_JOBS_KEEP:
        ASK_JOBS.pop(next(iter(ASK_JOBS)))

    def run():
        try:
            job["result"] = agent.ask(question, follow, lambda ev: job["events"].append({**ev, "t": round(time.time() - job["at"], 1)}))
            job["status"] = "done"
        except Exception as e:
            job["code"], job["error"] = _ask_error(e)
            job["status"] = "error"
    __import__("threading").Thread(target=run, daemon=True).start()
    return ok({"id": jid})


@app.get("/api/ask/job/{jid}")
def ask_job(jid: str):
    job = ASK_JOBS.get(jid)
    if not job:
        raise HTTPException(404, "That question isn't running any more. Ask it again.")
    return ok({"status": job["status"], "events": job["events"], "elapsed": round(time.time() - job["at"], 1),
               **({"result": job["result"]} if "result" in job else {}), **({"error": job["error"], "code": job["code"]} if "error" in job else {})})


@app.get("/api/dcf/{ticker}")
def dcf(ticker: str, growth: float | None = None, years: int = 5, discount: float = 0.09, terminal: float = 0.025):
    t = T(ticker)
    full = _hist_or_404(t, "1y")
    fin = data.financials(t)
    inf = {**data.info(t), "revenueGrowth3y": insights.multi_year_growth(fin, "revenue")}  # the default growth's basis
    res = insights.dcf(inf, growth, years, discount, terminal)
    if "fairValue" in res:
        price = float(full["Close"].iloc[-1])
        res["price"] = price
        res["marginOfSafety"] = res["fairValue"] / price - 1
        res["impliedGrowth"] = insights.implied_growth(inf, price, years, discount, terminal)
        res["grid"] = insights.sensitivity(inf, res["growth"], years, discount, terminal)
    return ok(res)


@app.get("/api/compare")
def compare(tickers: str, period: str = "1y"):
    ts = [T(x) for x in tickers.split(",") if x.strip()][:8]
    return ok(insights.compare(ts, period, db.get_settings().get("risk_free_rate", 0.04)))


# ---------------------------------------------------------------- screener


@app.get("/api/fx")
def fx(src: str = "EUR", dst: str = "USD"):
    """Today's rate between two currencies, for the converter: units of `dst` per one `src`."""
    src, dst = src.strip().upper(), dst.strip().upper()
    if src not in data.CURRENCIES or dst not in data.CURRENCIES:
        raise HTTPException(400, "Choose both currencies from the list.")
    try:
        rate = data.fx_rate(src, dst)
    except data.DataError as e:
        raise HTTPException(404, str(e))
    return ok({"from": src, "to": dst, "rate": rate, "stale": data.is_stale_fx(src) or data.is_stale_fx(dst)})


@app.get("/api/progress")
def progress():
    """How far the long scorings under way have got (the day's first downloads), for the page's loader."""
    return ok(insights.progress())


@app.get("/api/tax")
def tax_year(year: str = ""):
    """A tax year from the ledger: gains and losses on sales and the dividends received, in the base currency (tax.py)."""
    if year and not (year.isdigit() and len(year) == 4):
        raise HTTPException(400, "The year must look like 2026.")
    try:
        return ok(tax.summary(db.get_settings(), year or None))
    except ledger.LedgerError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tax.csv")
def tax_csv(year: str = ""):
    if year and not (year.isdigit() and len(year) == 4):
        raise HTTPException(400, "The year must look like 2026.")
    s = tax.summary(db.get_settings(), year or None)
    return Response(tax.to_csv(s), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="clearyield-tax-{s["year"]}.csv"'})


@app.get("/api/scorecheck")
def score_check():
    """Do the scores rank stocks by what happens next? The research in docs/score-research.md (scorecheck.py), over
    every stock in the list whatever Settings follows: the design needs all five regions, to decide on two and confirm
    on three. Built in the background the first time; until then this says how far it's got. Weights don't enter."""
    out = scorecheck.cached_or_start(universe_tickers(list(REGIONS)), "all")
    return ok({**out, "regions": list(REGIONS)})


def universe_tickers(regions):
    from .universe import tickers_for
    return tickers_for(regions)


@app.get("/api/opportunities")
def opportunities(markets: str = ""):
    return ok(insights.opportunities(db.get_settings(), [m.strip() for m in markets.split(",") if m.strip()]))


@app.post("/api/screener")
def screener(body: dict = Body(...)):
    settings = db.get_settings()
    if body.get("query"):
        parsed = ai.parse_query(body["query"])
        body = {**body, "filters": {**(body.get("filters") or {}), **parsed}}
        if parsed.get("countries") and not parsed.get("markets"):
            body.pop("markets", None)  # "Gulf banks" scans the Gulf's region, not every market the page has ticked
    return ok(insights.screen(body, settings))


# ---------------------------------------------------------------- watchlist
@app.get("/api/watchlist")
def watchlist():
    items = db.q("SELECT * FROM watchlist ORDER BY added")
    rows = {r["ticker"]: r for r in insights.rows_for([i["ticker"] for i in items], db.get_settings())}
    return ok([{**i, **rows.get(i["ticker"], {})} for i in items])


@app.post("/api/watchlist")
def watch_add(body: dict = Body(...)):
    t = T(body["ticker"])
    _hist_or_404(t, "1y")
    db.x("INSERT OR REPLACE INTO watchlist (ticker, tags, note, added) VALUES (?,?,?,?)",
         (t, body.get("tags", ""), body.get("note", ""), time.time()))
    return ok({"ok": True})


@app.delete("/api/watchlist/{ticker}")
def watch_del(ticker: str):
    db.x("DELETE FROM watchlist WHERE ticker=?", (T(ticker),))
    return ok({"ok": True})


# ---------------------------------------------------------------- portfolio
@app.get("/api/portfolio")
def get_portfolio():
    p = portfolio.summary(db.get_settings())
    p.pop("_series")
    return ok(p)


@app.post("/api/buckets")
def set_bucket(body: dict = Body(...)):
    t = _ticker(body)
    try:
        portfolio.set_bucket(t, body.get("bucket"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ok({"ticker": t, "bucket": portfolio.bucket_lookup()(t)})


@app.post("/api/holdings")
def add_holding(body: dict = Body(...)):
    # A shorter way to record a purchase, used by the form on Research
    return _record({"ticker": body.get("ticker"), "kind": "buy", "date": body.get("bought", ""), "shares": body.get("shares"),
                    "price": body.get("cost"), "note": body.get("note", "")})


def _record(body):
    t, kind = _ticker(body), body.get("kind", "buy")
    if kind == "dividend":
        shares, price, amount = None, None, _number(body, "amount")
    else:
        shares, price, amount = _number(body, "shares"), _number(body, "price"), None
    try:
        if kind == "buy":
            _hist_or_404(t, "1y")
        return ok({"id": ledger.add(t, kind, body.get("date", ""), shares, price, amount, body.get("note", ""))})
    except ledger.LedgerError as e:
        raise HTTPException(400, str(e))


@app.get("/api/transactions")
def transactions():
    return ok(ledger.transactions())


@app.post("/api/transactions")
def add_transaction(body: dict = Body(...)):
    return _record(body)


@app.post("/api/transactions/import")
def import_transactions(body: dict = Body(...)):
    try:
        rows = ledger.parse_csv(str(body.get("text") or ""))
        for t in {r["ticker"] for r in rows if r["kind"] == "buy"}:
            try:
                data.history(t, "1y")
            except data.DataError:
                raise ledger.LedgerError(f"No price data for {t}. Check the symbol.")
        return ok({"added": ledger.add_many(rows)})
    except ledger.LedgerError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/transactions/ticker/{ticker}")
def delete_holding(ticker: str):
    """Every transaction of one holding: its purchases, sales and dividends."""
    n = ledger.delete_ticker(T(ticker))
    if not n:
        raise HTTPException(404, f"There are no transactions of {T(ticker)}.")
    return ok({"deleted": n})


@app.delete("/api/transactions/{tid}")
@app.delete("/api/holdings/{tid}")
def delete_transaction(tid: int):
    try:
        ledger.delete(tid)
    except ledger.LedgerError as e:
        raise HTTPException(400, f"A later sale depends on this. Delete the sale first. ({e})")
    return ok({"ok": True})


@app.post("/api/pretrade")
def pretrade(body: dict = Body(...)):
    t = T(body["ticker"])
    _hist_or_404(t, "1y")

    def num(k):
        v = body.get(k)
        return float(v) if v not in (None, "") else None
    try:
        return ok(portfolio.pretrade(db.get_settings(), t, amount=num("amount"), entry=num("entry"), stop=num("stop"), risk=num("risk")))
    except ValueError as e:
        msg = str(e)
        raise HTTPException(400, msg if not msg.startswith("could not convert") else "Amounts must be numbers.")
    except data.DataError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- orders
# Prepared by the app from your targets, approved by you, placed by you at your broker. Nothing here trades.
@app.post("/api/orders/plan")
def orders_plan(body: dict = Body(...)):
    try:
        return ok(orders.plan(db.get_settings(), _number(body, "new_money", 0.0), bool(body.get("sell")), bool(body.get("fractional")),
                              body.get("picks")))
    except orders.OrderError as e:
        raise HTTPException(400, str(e))


@app.get("/api/orders")
def orders_list():
    return ok(orders.listing())


@app.post("/api/orders")
def orders_approve(body: dict = Body(...)):
    try:
        return ok({"approved": orders.approve(body.get("orders"))})
    except orders.OrderError as e:
        raise HTTPException(400, str(e))
    except data.DataError as e:
        raise HTTPException(404, str(e))


@app.post("/api/orders/{oid}/done")
def orders_done(oid: int, body: dict = Body(...)):
    try:
        return ok({"transaction": orders.mark_done(oid, body.get("shares"), body.get("price"), body.get("date"), body.get("fee"))})
    except (TypeError, ValueError) as e:  # OrderError and LedgerError are ValueErrors with a message for the user
        msg = str(e)
        raise HTTPException(400, msg if not msg.startswith("could not convert") else "Shares, price and fee must be numbers.")


@app.post("/api/orders/{oid}/cancel")
def orders_cancel(oid: int):
    try:
        orders.cancel(oid)
    except orders.OrderError as e:
        raise HTTPException(400, str(e))
    return ok({"ok": True})


@app.get("/api/orders.csv")
def orders_csv():
    return Response(orders.to_csv(orders.open_orders()), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="clearyield-orders-{dt.date.today()}.csv"'})


# ---------------------------------------------------------------- backtest
@app.post("/api/backtest")
def run_backtest(body: dict = Body(...)):
    t = _ticker(body)
    cost_bps = _number(body, "cost_bps", 10.0)
    df = _hist_or_404(t, "max")  # all of it, so indicators are warmed up when the chosen period starts
    start = data.slice_period(df, body.get("period", "5y")).index[0]
    try:
        res = backtest.run(df, body.get("strategy", "sma_cross"), body.get("params") or {}, cost_bps,
                           db.get_settings().get("risk_free_rate", 0.04), start=start)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ok({"ticker": t, **res})


# ---------------------------------------------------------------- portfolio simulations
@app.get("/api/simulate/mix")
def simulate_mix():
    """Your holdings today as a mix to start from, with the base currency and benchmark the tests use."""
    settings = db.get_settings()
    return ok({"mix": simulate.current_mix(portfolio.summary(settings)), "base": settings["base_currency"],
               "benchmark": settings.get("benchmark"), "rebalance": simulate.REBALANCE, "periods": list(simulate.PERIODS)})


@app.post("/api/simulate/backtest")
def simulate_backtest(body: dict = Body(...)):
    settings = db.get_settings()
    bench = str(body.get("benchmark", settings.get("benchmark")) or "").strip().upper() or None
    try:
        res = simulate.backtest(body.get("mix"), settings["base_currency"], bench, str(body.get("period", "10y")),
                                str(body.get("rebalance", "yearly")), _number(body, "cost_bps", 10.0),
                                _number(body, "start_value", 10000.0), _number(body, "monthly", 0.0),
                                settings.get("risk_free_rate", 0.04), body.get("stand_ins", True) is not False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except data.DataError as e:
        raise HTTPException(404, str(e))
    return ok(res)


@app.post("/api/simulate/future")
def simulate_future(body: dict = Body(...)):
    settings = db.get_settings()
    opt = lambda k: _number(body, k) if body.get(k) not in (None, "") else None
    try:
        res = simulate.monte_carlo(body.get("mix"), settings["base_currency"], _number(body, "years", 10.0),
                                   _number(body, "start_value", 10000.0), _number(body, "monthly", 0.0),
                                   str(body.get("rebalance", "yearly")), _number(body, "cost_bps", 10.0), str(body.get("period", "max")),
                                   int(_number(body, "paths", 5000.0)), opt("expected"), _number(body, "inflation", 0.0), opt("goal"),
                                   rf=settings.get("risk_free_rate", 0.04), stand_ins=body.get("stand_ins", True) is not False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except data.DataError as e:
        raise HTTPException(404, str(e))
    return ok(res)


# ---------------------------------------------------------------- journal
@app.get("/api/journal")
def journal():
    return ok(db.q("SELECT * FROM journal ORDER BY ts DESC"))


@app.post("/api/journal")
def journal_add(body: dict = Body(...)):
    def num(k):
        return _number(body, k) if body.get(k) not in (None, "") else None
    t, settings = _ticker(body), db.get_settings()
    try:
        _, _, sc = insights.evaluate_ticker(t, settings)
    except data.DataError:  # the note matters more than the snapshot
        sc = {"composite": None, "stance": None, "factors": None}
    jid = db.x("INSERT INTO journal (ts, ticker, action, price, thesis, invalidation, stop_price, target_price, review_date, "
               "score, stance, factors, benchmark) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (time.time(), t, body.get("action", "buy"), num("price"), body.get("thesis", ""),
                body.get("invalidation", ""), num("stop_price"), num("target_price"), body.get("review_date", ""),
                sc["composite"], sc["stance"], json.dumps(sc["factors"]) if sc["factors"] else None, settings.get("benchmark", "SPY")))
    return ok({"id": jid})


@app.get("/api/journal/scorecard")
def journal_scorecard():
    return ok(insights.scorecard(db.get_settings()))


@app.patch("/api/journal/{jid}")
def journal_update(jid: int, body: dict = Body(...)):
    db.x("UPDATE journal SET outcome=? WHERE id=?", (body.get("outcome", ""), jid))
    return ok({"ok": True})


@app.delete("/api/journal/{jid}")
def journal_del(jid: int):
    db.x("DELETE FROM journal WHERE id=?", (jid,))
    return ok({"ok": True})


# ---------------------------------------------------------------- alerts / digest
@app.get("/api/alerts")
def alerts():
    return ok(db.q("SELECT * FROM alerts ORDER BY active DESC, created DESC"))


@app.post("/api/alerts")
def alert_add(body: dict = Body(...)):
    if body.get("kind") not in insights.ALERT_KINDS:
        raise HTTPException(400, "Unknown alert kind")
    t, value = _ticker(body), _number(body, "value")
    _hist_or_404(t, "1y")
    aid = db.x("INSERT INTO alerts (ticker, kind, value, created) VALUES (?,?,?,?)", (t, body["kind"], value, time.time()))
    return ok({"id": aid})


@app.delete("/api/alerts/{aid}")
def alert_del(aid: int):
    db.x("DELETE FROM alerts WHERE id=?", (aid,))
    return ok({"ok": True})


@app.post("/api/alerts/{aid}/dismiss")
def alert_dismiss(aid: int):
    db.x("UPDATE alerts SET dismissed = 1 WHERE id=?", (aid,))
    return ok({"ok": True})


@app.post("/api/alerts/check")
def alert_check(background: BackgroundTasks):
    settings = db.get_settings()
    fired = insights.check_alerts(settings)
    background.add_task(mailer.send_alerts, fired, settings)  # after the reply: a slow mail server doesn't hold up the page
    return ok(fired)


@app.post("/api/email/test")
def email_test(body: dict = Body(default={})):
    """A test email to the address typed on the page (saved or not), so the setup can be checked before relying on it."""
    to = body.get("to") if isinstance(body, dict) else None
    to = to.strip() if isinstance(to, str) and to.strip() else db.get_settings()["alert_email"]
    try:
        mailer.send(to, "ClearYield test email", "This is a test from ClearYield. Alert emails will arrive like this one "
                    "when email alerts are on in Settings.\n\nDecision support, not financial advice.\n")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ok({"sent": True, "to": to})


@app.get("/api/digest")
def digest():
    settings = db.get_settings()
    shown = set(settings.get("dashboard_tiles") or [])
    return ok(insights.digest(settings, {t: name for t, name in _tiles(settings).items() if t in shown}))


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def settings_get():
    return ok(db.get_settings())


@app.post("/api/settings")
def settings_set(body: dict = Body(...)):
    body.pop("custom_tiles", None)  # added and removed through /api/tiles, which checks them
    if "dashboard_tiles" in body:
        tiles = body["dashboard_tiles"]
        if not isinstance(tiles, list):
            raise HTTPException(400, "Dashboard tiles must be a list of symbols.")
        body["dashboard_tiles"] = [t for t in _tiles(db.get_settings()) if t in tiles]  # known ones only, in display order
    if body.get("email_alerts") is True and not str(body.get("alert_email", db.get_settings()["alert_email"]) or "").strip():
        raise HTTPException(400, "Enter the email address to send alerts to.")
    if "core_target" in body:
        v = _number(body, "core_target")
        if not 0 <= v <= 100:
            raise HTTPException(400, "The core target is a percentage from 0 to 100.")
        body["core_target"] = v
    try:
        return ok(db.set_settings(body))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- start with Windows
@app.get("/api/startup")
def startup_get():
    return ok(startup.status())


@app.post("/api/startup")
def startup_set(body: dict = Body(...)):
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(400, "Say whether to start with Windows: enabled true or false.")
    try:
        return ok(startup.enable() if body["enabled"] else startup.disable())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/stop")
def stop():
    """Stop this copy of the app, for `run.bat --restart` to replace one running in the background with new code.
    Only this computer can ask (the middleware), and a page on another site can't."""
    import threading
    log.info("stopping, as asked")
    threading.Timer(0.5, lambda: os._exit(0)).start()  # after the reply has gone
    return ok({"stopping": True})


# ---------------------------------------------------------------- backup
@app.get("/api/backup")
def backup():
    name = f"clearyield-backup-{dt.date.today().isoformat()}.json"
    return JSONResponse(db.export_user_data(), headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/backup/restore")
def backup_restore(body: dict = Body(...)):
    try:
        ledger.replay(db.read_backup(body)["transactions"])  # LedgerError is a ValueError
        db.restore_user_data(body)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return ok({"ok": True})


@app.post("/api/cache/clear")
def cache_clear():
    db.cache_expire()
    return ok({"ok": True})
