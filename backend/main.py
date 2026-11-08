"""StockPilot API + static frontend.  Run:  uvicorn backend.main:app --reload --port 8001"""
import asyncio
import datetime as dt
import json
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(env=ROOT / ".env"):
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():  # -sig: Notepad can start a file with a byte-order mark
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

import numpy as np  # noqa: E402
from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import ai, backtest, data, db, insights, ledger, portfolio  # noqa: E402
from . import indicators as ind  # noqa: E402
from .universe import MACRO, UNIVERSE, search as uni_search  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockpilot")
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


async def _alert_loop():
    while True:
        await asyncio.sleep(ALERT_INTERVAL)
        try:
            fired = await asyncio.to_thread(insights.check_alerts, db.get_settings())
            await asyncio.to_thread(insights.record_today, db.get_settings())
            for a in fired:
                log.info("ALERT: %s", a["message"])
        except Exception as e:
            log.warning("alert check failed: %s", e)


@asynccontextmanager
async def lifespan(app):
    db.init()
    task = asyncio.create_task(_alert_loop())
    yield
    task.cancel()


app = FastAPI(title="StockPilot", lifespan=lifespan)
FRONT = ROOT / "frontend"


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}


@app.middleware("http")
async def only_local_addresses(request, call_next):
    """The app has no login and only listens locally. Refusing other Host headers stops a website from
    pointing its own domain name at this machine to read and change your data through your browser."""
    host = request.headers.get("host", "")
    if (host if host.endswith("]") else host.rsplit(":", 1)[0]) not in LOCAL_HOSTS:
        return JSONResponse({"detail": "StockPilot only answers on localhost."}, status_code=400)
    return await call_next(request)


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
@app.get("/api/meta")
def meta():
    return ok({"demo": data.DEMO, "ai": ai.available(), "tiles": [{"ticker": t, "name": n} for t, n in MACRO.items()],
               "settings": db.get_settings(), "markets": list(UNIVERSE), "alertKinds": insights.ALERT_KINDS,
               "strategies": backtest.STRATEGIES})


@app.get("/api/search")
def search(q: str = ""):
    hits = uni_search(q)
    known = {h["ticker"] for h in hits}
    hits += [h for h in data.search_symbols(q) if h["ticker"] not in known]
    raw = T(q)
    if raw and not hits and " " not in raw:
        hits.append({"ticker": raw, "name": "Look up this symbol on Yahoo Finance", "market": "Any"})
    return ok(hits[:12])


@app.get("/api/macro")
def macro():
    out = []
    chosen = set(db.get_settings().get("dashboard_tiles") or [])
    for t, label in MACRO.items():
        if t not in chosen:
            continue
        try:
            c = data.history(t, "1y")["Close"]
            out.append({"ticker": t, "name": label, "price": float(c.iloc[-1]), "day": ind.ret_over(c, 1),
                        "ret1m": ind.ret_over(c, 21), "spark": [float(v) for v in c.iloc[-60:]]})
        except Exception:
            continue
    return ok(out)


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
        view, lines = data.weekly(view, lines)
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


@app.get("/api/stock/{ticker}/brief")
def stock_brief(ticker: str):
    t = T(ticker)
    settings = db.get_settings()
    try:
        full, inf, sc = insights.evaluate_ticker(t, settings)
    except data.DataError as e:
        raise HTTPException(404, str(e))
    ctx = {"ticker": t, "info": inf, "scores": sc, "stats": ind.stats(full["Close"], settings.get("risk_free_rate", 0.04)),
           "news": data.news(t), "financials": data.financials(t)}
    return ok(ai.brief(ctx))


@app.get("/api/dcf/{ticker}")
def dcf(ticker: str, growth: float | None = None, years: int = 5, discount: float = 0.09, terminal: float = 0.025):
    t = T(ticker)
    full = _hist_or_404(t, "1y")
    inf = data.info(t)
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
@app.post("/api/screener")
def screener(body: dict = Body(...)):
    settings = db.get_settings()
    if body.get("query"):
        parsed = ai.parse_query(body["query"])
        body = {**body, "filters": {**(body.get("filters") or {}), **parsed}}
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
def alert_check():
    return ok(insights.check_alerts(db.get_settings()))


@app.get("/api/digest")
def digest():
    return ok(insights.digest(db.get_settings()))


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def settings_get():
    return ok(db.get_settings())


@app.post("/api/settings")
def settings_set(body: dict = Body(...)):
    if "dashboard_tiles" in body:
        tiles = body["dashboard_tiles"]
        if not isinstance(tiles, list):
            raise HTTPException(400, "Dashboard tiles must be a list of symbols.")
        body["dashboard_tiles"] = [t for t in MACRO if t in tiles]  # known ones only, in display order
    if "core_target" in body:
        v = _number(body, "core_target")
        if not 0 <= v <= 100:
            raise HTTPException(400, "The core share is a percentage from 0 to 100.")
        body["core_target"] = v
    return ok(db.set_settings(body))


# ---------------------------------------------------------------- backup
@app.get("/api/backup")
def backup():
    name = f"stockpilot-backup-{dt.date.today().isoformat()}.json"
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
