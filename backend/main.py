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

from . import ai, backtest, data, db, insights, portfolio, scoring  # noqa: E402
from . import indicators as ind  # noqa: E402
from .universe import MACRO, UNIVERSE, name_of, search as uni_search  # noqa: E402

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
    return ok({"demo": data.DEMO, "ai": ai.available(), "aiModel": ai.MODEL if ai.available() else None,
               "settings": db.get_settings(), "markets": list(UNIVERSE), "alertKinds": insights.ALERT_KINDS,
               "strategies": backtest.STRATEGIES})


@app.get("/api/search")
def search(q: str = ""):
    hits = uni_search(q)
    raw = T(q)
    if raw and not any(h["ticker"] == raw for h in hits) and " " not in raw:
        hits.append({"ticker": raw, "name": "Look up this symbol on Yahoo Finance", "market": "Any"})
    return ok(hits)


@app.get("/api/macro")
def macro():
    out = []
    for t, label in MACRO.items():
        try:
            c = data.history(t, "1y")["Close"]
            out.append({"ticker": t, "name": label, "price": float(c.iloc[-1]), "day": ind.ret_over(c, 1),
                        "ret1m": ind.ret_over(c, 21), "spark": [float(v) for v in c.iloc[-60:]]})
        except Exception:
            continue
    return ok(out)


# ---------------------------------------------------------------- stock research
@app.get("/api/stock/{ticker}")
def stock(ticker: str, period: str = "1y"):
    t = T(ticker)
    settings = db.get_settings()
    full = _hist_or_404(t, "5y")
    inf = data.info(t)
    sc = scoring.evaluate(inf, full["Close"], settings.get("weights"))
    ib = ind.bundle(full)
    view = data.slice_period(full, period)
    ib = ib.loc[view.index]
    c = full["Close"]
    price, prev = float(c.iloc[-1]), float(c.iloc[-2]) if len(c) > 1 else None  # some Yahoo indices have one row
    candles = {"time": [d.strftime("%Y-%m-%d") for d in view.index], "open": view["Open"].tolist(), "high": view["High"].tolist(),
               "low": view["Low"].tolist(), "close": view["Close"].tolist(), "volume": view["Volume"].tolist(),
               **{k: ib[k].tolist() for k in ib.columns}}
    stats = ind.stats(c, settings.get("risk_free_rate", 0.04))
    last_year = ind.window(full, 252)
    stats.update({"price": price, "change": price - prev if prev else None, "changePct": price / prev - 1 if prev else None,
                  "high52": float(last_year["High"].max()), "low52": float(last_year["Low"].min()),
                  "rsi": float(ind.rsi(c).iloc[-1]), "avgVolume": float(full["Volume"].iloc[-60:].mean())})
    try:
        bench = data.history(settings.get("benchmark", "SPY"), "5y")["Close"]
        stats["betaCalc"] = ind.beta(ind.window(c, 504), bench)
    except data.DataError:
        pass
    watched = bool(db.q("SELECT 1 FROM watchlist WHERE ticker=?", (t,)))
    return ok({"ticker": t, "info": inf, "stats": stats, "scores": sc, "candles": candles,
               "financials": data.financials(t), "watched": watched, "events": insights.upcoming(inf)})


@app.get("/api/stock/{ticker}/news")
def stock_news(ticker: str):
    return ok(data.news(T(ticker)))


@app.get("/api/stock/{ticker}/brief")
def stock_brief(ticker: str):
    t = T(ticker)
    settings = db.get_settings()
    full = _hist_or_404(t)
    inf = data.info(t)
    ctx = {"ticker": t, "info": inf, "scores": scoring.evaluate(inf, full["Close"], settings.get("weights")),
           "stats": ind.stats(full["Close"]), "news": data.news(t), "financials": data.financials(t)}
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


@app.post("/api/holdings")
def add_holding(body: dict = Body(...)):
    t = _ticker(body)
    shares, cost = _number(body, "shares"), _number(body, "cost")
    _hist_or_404(t, "1y")
    if shares <= 0 or cost < 0:
        raise HTTPException(400, "Shares must be positive and cost non-negative.")
    if body.get("bought"):
        try:
            dt.date.fromisoformat(body["bought"])
        except ValueError:
            raise HTTPException(400, "Purchase date must look like 2025-01-31.")
    hid = db.x("INSERT INTO holdings (ticker, shares, cost, bought, note) VALUES (?,?,?,?,?)",
               (t, shares, cost, body.get("bought", ""), body.get("note", "")))
    return ok({"id": hid})


@app.delete("/api/holdings/{hid}")
def del_holding(hid: int):
    db.x("DELETE FROM holdings WHERE id=?", (hid,))
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
    return ok(db.set_settings(body))


# ---------------------------------------------------------------- backup
@app.get("/api/backup")
def backup():
    name = f"stockpilot-backup-{dt.date.today().isoformat()}.json"
    return JSONResponse(db.export_user_data(), headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/backup/restore")
def backup_restore(body: dict = Body(...)):
    try:
        db.restore_user_data(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ok({"ok": True})


@app.post("/api/cache/clear")
def cache_clear():
    db.cache_clear()
    return ok({"ok": True})
