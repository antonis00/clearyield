"""Offline smoke test: runs every endpoint against synthetic demo data.

    python tests/test_smoke.py              every test; failures are named, and the run goes on
    python tests/test_smoke.py splits -v    only tests whose name contains "splits", showing the app's log
    STOCKPILOT_DEMO=1 python -m pytest tests -q
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["STOCKPILOT_DEMO"] = "1"
os.environ["STOCKPILOT_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ.pop("ANTHROPIC_API_KEY", None)
for _k in ("AI_BASE_URL", "AI_MODEL", "AI_API_KEY", "AI_TIMEOUT", "AI_THINKING"):
    os.environ.pop(_k, None)
os.environ["AI_PROVIDER"] = "none"  # set before .env is read, which never overrides what's already set
os.environ["WEB_SEARCH"] = "off"  # nor a Tavily key from .env; the tests that need web search set it themselves
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import warnings  # noqa: E402

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient` is deprecated")  # about the library, not the app
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend import db as _db  # noqa: E402

_db.init()  # the tables exist from the start, so any test can run on its own


def test_all():
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        for path in ("/", "/static/app.js", "/api/meta"):
            assert c.get(path).headers["cache-control"] == "no-cache", path
        m = c.get("/api/meta").json()
        assert m["demo"] is True
        assert c.get("/api/search?q=qnb").json()[0]["ticker"] == "QNBK.QA"
        assert len(c.get("/api/macro").json()) > 5

        for t in ["AAPL", "SAP.DE", "QNBK.QA", "SPY", "BTC-USD", "AZN.L"]:
            r = c.get(f"/api/stock/{t}?period=1y")
            assert r.status_code == 200, (t, r.text)
            j = r.json()
            assert len(j["candles"]["close"]) > 200 and j["scores"]["stance"]
            assert c.get(f"/api/stock/{t}/news").status_code == 200
            assert c.post(f"/api/stock/{t}/brief").json()["text"]

        d = c.get("/api/dcf/AAPL").json()
        assert "fairValue" in d or "error" in d
        cmp_ = c.get("/api/compare?tickers=AAPL,MSFT,QNBK.QA&period=1y").json()
        assert len(cmp_["series"]) == 3 and cmp_["correlation"]

        s = c.post("/api/screener", json={"markets": ["US", "Gulf"], "filters": {"min_score": 0}}).json()
        assert s["scanned"] > 40 and s["rows"] and {r["country"] for r in s["rows"]} <= {"United States", "Qatar", "Saudi Arabia", "UAE", "Kuwait"}
        s2 = c.post("/api/screener", json={"query": "cheap profitable dividend stocks in the gulf"}).json()
        assert {"Qatar", "Saudi Arabia", "UAE", "Kuwait"} <= set(s2["filters"]["countries"])

        assert c.post("/api/watchlist", json={"ticker": "nvda"}).status_code == 200
        assert c.get("/api/watchlist").json()[0]["ticker"] == "NVDA"

        for t, sh, cost in [("AAPL", 10, 150), ("QNBK.QA", 500, 15), ("AZN.L", 20, 9000), ("BTC-USD", 0.05, 30000), ("SPY", 5, 400)]:
            assert c.post("/api/holdings", json={"ticker": t, "shares": sh, "cost": cost}).status_code == 200
        p = c.get("/api/portfolio").json()
        assert p["total"] > 0 and p["performance"]["portfolio"] and p["rebalance"]

        for strat in ["buy_hold", "sma_cross", "rsi_reversion", "momentum", "dca"]:
            r = c.post("/api/backtest", json={"ticker": "MSFT", "strategy": strat, "period": "5y"})
            assert r.status_code == 200, (strat, r.text)

        assert c.post("/api/journal", json={"ticker": "AAPL", "action": "buy", "price": 150, "thesis": "x",
                                            "stop_price": 1e9, "review_date": "2000-01-01"}).status_code == 200
        assert c.post("/api/alerts", json={"ticker": "AAPL", "kind": "price_above", "value": 0.01}).status_code == 200
        dg = c.get("/api/digest").json()
        assert any("Alert" in i["text"] for i in dg["items"])
        assert any("stop" in i["text"] for i in dg["items"])

        st = c.post("/api/settings", json={"base_currency": "QAR"}).json()
        assert st["base_currency"] == "QAR"
        p2 = c.get("/api/portfolio").json()
        assert p2["total"] > p["total"] * 3  # QAR value is ~3.64x USD value
        assert c.get("/api/stock/NOTAREALTICKER").status_code in (200, 404)


def test_real_yahoo_shapes():
    """Offline checks for what real Yahoo data does and demo data does not."""
    import numpy as np
    import pandas as pd

    from backend import data, db, insights, scoring
    from backend import indicators as ind

    db.init()

    # Crypto trades every day, so a year is about 365 bars, and "1y" must not be 252 of them
    days = pd.date_range("2021-01-01", periods=2000, freq="D")
    daily = pd.Series(np.arange(1.0, 2001.0), index=days)
    assert ind.bars_per_year(daily) == 365
    assert ind.ret_over(daily, 252) == daily.iloc[-1] / daily.iloc[-1 - 365] - 1
    assert ind.ret_over(daily, 1) == daily.iloc[-1] / daily.iloc[-2] - 1
    assert len(data.slice_period(daily.to_frame("Close"), "1y")) == 366
    assert ind.ret_over(daily.iloc[:100], 252) is None
    weekdays = pd.Series(1.0, index=pd.bdate_range("2021-01-01", periods=1300))
    assert ind.bars_per_year(weekdays) == 252

    # A market that reacts a day late still has a beta of about 1
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2023-01-02", periods=520)
    bench_r = pd.Series(rng.normal(0, 0.01, 520), index=idx)
    bench, late = (1 + bench_r).cumprod(), (1 + bench_r.shift(1).fillna(0)).cumprod()
    assert 0.7 < ind.beta(late, bench) < 1.3

    # dividendYield is a percentage even when tiny
    assert abs(data._normalize_info("MU", {"dividendYield": 0.08})["dividendYield"] - 0.0008) < 1e-12

    # London stock reporting in USD: price in pence, market cap in GBP, statements in USD
    azn = data._normalize_info("AZN.L", {"currency": "GBp", "financialCurrency": "USD", "trailingPE": 20.0,
                                         "quoteType": "EQUITY", "marketCap": 1e9, "freeCashflow": 1e8})
    usd_gbp = data.DEMO_FX["USD"] / data.DEMO_FX["GBP"]
    assert abs(azn["statementFx"] - usd_gbp) < 1e-3
    assert azn["trailingPE"] == 20.0
    fy = [p for p in scoring.factor_breakdown(azn, daily)["value"] if p["label"] == "FCF yield"][0]["value"]
    assert abs(fy - 0.1 * usd_gbp) < 1e-3
    # 1e9 of FCF, no growth, 10% discount, over 1e9 shares is 10 per share before conversion
    args = {"freeCashflow": 1e9, "sharesOutstanding": 1e9, "financialCurrency": "USD"}
    d = insights.dcf({**args, "currency": "GBp", "statementFx": 0.5}, 0.0, 1, 0.1, 0.0)
    assert abs(d["fairValue"] - 500) < 1e-6 and d["statementCurrency"] == "USD"
    assert abs(insights.dcf({**args, "currency": "USD", "statementFx": 1.0}, 0.0, 1, 0.1, 0.0)["fairValue"] - 10) < 1e-6

    # Several share classes: use the count that covers all of them
    assert abs(insights.dcf({**args, "currency": "USD", "impliedSharesOutstanding": 2e9}, 0.0, 1, 0.1, 0.0)["fairValue"] - 5) < 1e-6

    # An empty answer from Yahoo is not cached, and failures do not replace good old data
    class Empty:
        info = {"trailingPegRatio": None}
        income_stmt = cashflow = None

        @property
        def news(self):
            raise RuntimeError("rate limited")

    class YF:
        Ticker = staticmethod(lambda t: Empty())

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    try:
        assert data.info("EMPTY.X")["trailingPE"] is None and db.cache_get_stale("info:v10:EMPTY.X") is None
        db.cache_set("info:v10:OLD.X", {"trailingPE": 12.0})
        db.cache_expire("info:v10:OLD.X")
        assert data.info("OLD.X")["trailingPE"] == 12.0
        assert data.news("EMPTY.X") == [] and db.cache_get_stale("news:EMPTY.X") is None
    finally:
        data._yf, data.DEMO = real_yf, real_demo


def _reset():
    """Clear the user tables so a test starts from an empty app."""
    from backend import db
    db.init()
    for table in ("transactions", "watchlist", "journal", "alerts", "settings", "score_history", "buckets"):
        db.x(f"DELETE FROM {table}")


class _FakeMarket:
    """Replace Yahoo with fixed closes. `prices`: ticker -> Series, or None to make that ticker fail."""

    def __init__(self, prices, infos=None):
        self.prices, self.infos = prices, infos or {}

    def __enter__(self):
        from backend import data
        self.real = data.history, data.info

        def history(t, period="5y"):
            s = self.prices.get(t)
            if s is None:
                raise data.DataError(f"No price data for {t}")
            return data.slice_period(s.to_frame("Close").assign(High=s, Low=s, Open=s, Volume=1.0), period)

        def info(t):
            return {"name": t, "currency": "USD", "assetClass": "Stocks", "region": "US", "sector": "Tech", **self.infos.get(t, {})}
        data.history, data.info = history, info

    def __exit__(self, *exc):
        from backend import data
        data.history, data.info = self.real


def _steps(returns, end="2026-06-01"):
    """A price series starting at 100 with the given daily returns, ending on `end`."""
    import pandas as pd
    px = [100.0]
    for r in returns:
        px.append(px[-1] * (1 + r))
    return pd.Series(px, index=pd.bdate_range(end=end, periods=len(px)))


def test_new_position_keeps_the_portfolio_history():
    """A position bought into recently must not cut the stats of everything else down to its history."""
    from backend import db, portfolio
    _reset()
    old = _steps([0.002, -0.001] * 130)
    new = _steps([0.01, -0.01] * 10)
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('OLD', 100, 100)")
    with _FakeMarket({"OLD": old, "SPY": old}):
        alone = portfolio.summary(db.get_settings())
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('NEW', 0.001, 100)")  # about 0.001% of the value
    with _FakeMarket({"OLD": old, "NEW": new, "SPY": old}):
        both = portfolio.summary(db.get_settings())
    assert len(both["performance"]["dates"]) == len(alone["performance"]["dates"]) == 261
    assert abs(both["risk"]["return1y"] - alone["risk"]["return1y"]) < 0.001
    assert both["risk"]["volatility"] is not None and both["risk"]["sharpe"] is not None
    assert any("NEW" in w and "history since" in w for w in both["warnings"])
    assert not any("history since" in w for w in alone["warnings"])
    # Joining the portfolio must not itself look like a gain: a new position adds value, not return
    db.x("UPDATE transactions SET shares = 100 WHERE ticker = 'NEW'")
    with _FakeMarket({"OLD": old, "NEW": new, "SPY": old}):
        half = portfolio.summary(db.get_settings())
    assert abs(half["risk"]["return1y"]) < 0.5


def test_fx_failure_never_guesses_a_rate():
    """When Yahoo has no FX rate: use the last real one, or a true peg, or say so. Never assume 1:1."""
    from backend import data, db, insights, portfolio, scoring
    _reset()
    db.cache_clear()

    class Down:
        def history(self, **kw):
            raise RuntimeError("rate limited")

    class YF:
        Ticker = staticmethod(lambda t: Down())

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    try:
        # No rate known and no peg: an error, and nothing cached
        try:
            data.fx_rate("HKD", "USD")
            raise AssertionError("HKD should not get a made-up rate")
        except data.DataError as e:
            assert "HKD" in str(e)
        assert db.cache_get_stale("fx:HKDUSD") is None
        # Pegged to the dollar: the fixed rate is fine, but only against the dollar
        assert abs(data.fx_rate("QAR", "USD") - data.DEMO_FX["QAR"]) < 1e-9
        assert db.cache_get_stale("fx:QARUSD") is None
        try:
            data.fx_rate("QAR", "EUR")
            raise AssertionError("QAR/EUR floats with the euro, so the peg does not settle it")
        except data.DataError:
            pass
        # An expired real rate beats any guess, and stays marked as expired
        db.cache_set("fx:CADUSD", 0.71)
        db.cache_expire("fx:CADUSD")
        assert abs(data.fx_rate("CAD", "USD") - 0.71) < 1e-9
        assert db.cache_get("fx:CADUSD", 3600) is None

        # Without a rate the statement figures stay out of the score and the DCF
        hk = data._normalize_info("0700.HK", {"currency": "HKD", "financialCurrency": "CNY", "quoteType": "EQUITY",
                                              "marketCap": 1e9, "freeCashflow": 1e8, "sharesOutstanding": 1e8})
        assert hk["statementFx"] is None
        import pandas as pd
        close = pd.Series(1.0, index=pd.bdate_range("2024-01-01", periods=300))
        assert [p for p in scoring.factor_breakdown(hk, close)["value"] if p["label"] == "FCF yield"][0]["score"] is None
        assert "error" in insights.dcf(hk)
        # A position in that currency is reported as a problem, not valued at 1:1
        db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('0700.HK', 100, 300)")
        with _FakeMarket({"0700.HK": _steps([0.001] * 30)}, {"0700.HK": {"currency": "HKD"}}):
            p = portfolio.summary(db.get_settings())
        assert p["total"] == 0 and not p["positions"] and any("HKD" in e for e in p["errors"])
    finally:
        data._yf, data.DEMO = real_yf, real_demo


def test_cost_uses_the_exchange_rate_on_the_purchase_date():
    """10 shares of a EUR stock at 100, bought at EUR/USD 1.00, now 1.08, price flat: cost 1,000 and P&L +80."""
    from backend import data, db, portfolio
    _reset()
    flat = _steps([0.0] * 300)
    real = data.fx_on
    data.fx_on = lambda currency, base, date: 1.00 if currency == "EUR" else real(currency, base, date)
    try:
        db.x("INSERT INTO transactions (ticker, shares, price, date) VALUES ('SAP.DE', 10, 100, '2025-01-02')")
        with _FakeMarket({"SAP.DE": flat}, {"SAP.DE": {"currency": "EUR"}}):
            p = portfolio.summary(db.get_settings())
        pos = p["positions"][0]
        assert abs(pos["value"] - 1080) < 1e-6 and abs(pos["cost"] - 1000) < 1e-6 and abs(pos["pnl"] - 80) < 1e-6
        assert pos["fxApprox"] is False and not any("exchange rate" in w for w in p["warnings"])

        # Two lots: one dated at 1.00, one with no date, which can only use today's 1.08 and says so
        db.x("INSERT INTO transactions (ticker, shares, price, date) VALUES ('SAP.DE', 10, 100, '')")
        with _FakeMarket({"SAP.DE": flat}, {"SAP.DE": {"currency": "EUR"}}):
            p = portfolio.summary(db.get_settings())
        pos = p["positions"][0]
        assert abs(pos["cost"] - 2080) < 1e-6 and pos["fxApprox"] is True
        assert any("SAP.DE" in w and "exchange rate" in w for w in p["warnings"])
    finally:
        data.fx_on = real

    # Same currency as the base: an undated lot is exact, so no warning
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price, date) VALUES ('AAPL', 10, 100, '')")
    with _FakeMarket({"AAPL": flat}):
        p = portfolio.summary(db.get_settings())
    assert p["positions"][0]["fxApprox"] is False and abs(p["positions"][0]["cost"] - 1000) < 1e-6


def test_fx_on_reads_the_history():
    from backend import data, db
    import pandas as pd
    eur = pd.Series([1.00, 1.05, 1.08], index=pd.to_datetime(["2025-01-02", "2025-01-03", "2026-01-05"]))
    usd_inverse = pd.Series([3.64, 3.64], index=pd.to_datetime(["2025-01-02", "2026-01-05"]))
    real_demo = data.DEMO
    data.DEMO = False
    try:
        with _FakeMarket({"EURUSD=X": eur, "GBPUSD=X": eur, "USDXYZ=X": usd_inverse}):
            assert data.fx_on("EUR", "USD", "2025-01-02") == 1.00
            assert data.fx_on("EUR", "USD", "2025-06-01") == 1.05  # the last rate at or before that date
            assert data.fx_on("EUR", "USD", "2020-01-01") is None  # before the history starts
            assert abs(data.fx_on("GBp", "USD", "2025-01-03") - 0.0105) < 1e-9  # pence
            assert abs(data.fx_on("XYZ", "USD", "2025-01-02") - 1 / 3.64) < 1e-9  # only the inverse pair exists
            assert data.fx_on("GBp", "GBP", "2025-01-02") == 0.01  # no pair needed
            assert data.fx_on("HKD", "USD", "2025-01-02") is None  # no data
            # Yahoo's history for pegged pairs can be off: QAR/USD on 2025-01-02 reads 0.2856, the peg is 0.2747
            with_glitch = pd.Series([0.2856], index=pd.to_datetime(["2025-01-02"]))
            db.cache_set("fx:QARUSD", 0.2747)
            with _FakeMarket({"QARUSD=X": with_glitch}):
                assert data.fx_on("QAR", "USD", "2025-01-02") == 0.2747
    finally:
        data.DEMO = real_demo


def test_holding_date_is_validated():
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app) as c:
        bad = c.post("/api/holdings", json={"ticker": "AAPL", "shares": 1, "cost": 1, "bought": "soon"})
        assert bad.status_code == 400 and "date" in bad.json()["detail"]
        assert c.post("/api/holdings", json={"ticker": "AAPL", "shares": 1, "cost": 1, "bought": ""}).status_code == 200
        assert c.post("/api/holdings", json={"ticker": "AAPL", "shares": 1, "cost": 1, "bought": "2025-01-02"}).status_code == 200


def test_failed_position_marks_the_portfolio_incomplete():
    """Advice based on totals that leave out a holding is wrong, so it must be withheld and the gap stated."""
    from backend import db, portfolio
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('AAA', 30, 100), ('SPY', 1, 100)")
    up = _steps([0.0] * 300)
    etf = {"SPY": {"assetClass": "ETFs", "sector": "ETFs"}}

    with _FakeMarket({"AAA": up, "SPY": up}, etf):
        full = portfolio.summary(db.get_settings())
    assert full["incomplete"] is False and full["missing"] == [] and full["rebalance"]
    assert any(w.startswith("AAA is") for w in full["warnings"])  # a real concentration warning

    with _FakeMarket({"AAA": None, "SPY": up}, etf):
        part = portfolio.summary(db.get_settings())
    assert part["incomplete"] is True and part["missing"] == ["AAA"]
    assert part["rebalance"] == []
    # SPY would read as 100% of the portfolio, so weight-based warnings are withheld too
    assert not any("%" in w for w in part["warnings"])
    assert any("AAA" in e for e in part["errors"])


def test_ev_ebitda_is_calculated_in_one_currency():
    """Yahoo mixes currencies when the listing and the statements differ, in either direction."""
    from backend import data
    base = {"quoteType": "EQUITY", "marketCap": 1000.0, "totalDebt": 300.0, "totalCash": 100.0, "ebitda": 400.0, "enterpriseToEbitda": 3.0}
    # Quote and statements match: Yahoo's own figure is right
    same = data._normalize_info("X", {**base, "currency": "USD", "financialCurrency": "USD"})
    assert same["enterpriseToEbitda"] == 3.0
    # London listing reporting in USD, 1 USD = 1/1.27 GBP: (1000 + 200/1.27) / (400/1.27)
    uk = data._normalize_info("X.L", {**base, "currency": "GBp", "financialCurrency": "USD"})
    assert abs(uk["enterpriseToEbitda"] - (1000 + 200 / 1.27) / (400 / 1.27)) < 1e-2
    # A cheaper statement currency, like an ADR: statements in units worth 0.145 of the USD quote
    adr = data._normalize_info("X", {**base, "currency": "USD", "financialCurrency": "DKK", "marketCap": 1000.0,
                                     "totalDebt": 3000.0, "totalCash": 1000.0, "ebitda": 4000.0})
    assert abs(adr["enterpriseToEbitda"] - (1000 + 2000 * 0.145) / (4000 * 0.145)) < 1e-6
    # Anything missing or negative leaves it blank rather than falling back to the mixed figure
    for gap in ({"ebitda": None}, {"ebitda": -5.0}, {"totalDebt": None}, {"marketCap": None}):
        blank = data._normalize_info("X", {**base, "currency": "USD", "financialCurrency": "DKK", **gap})
        assert blank["enterpriseToEbitda"] is None, gap


def test_screener_ranks_each_asset_class_separately():
    """ETFs and crypto are scored on trend and risk only, so their composite can't be ranked against a stock's."""
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app) as c:
        rows = c.post("/api/screener", json={"markets": ["North America", "ETFs", "Crypto"], "filters": {"sort": "composite"}}).json()["rows"]
        classes = [r["assetClass"] for r in rows]
        assert classes == sorted(classes, key=["Stocks", "ETFs", "Crypto"].index), "asset classes should not be mixed"
        for cls in ("Stocks", "ETFs", "Crypto"):
            scores = [r["composite"] for r in rows if r["assetClass"] == cls]
            assert scores and scores == sorted(scores, reverse=True), cls
        assert all(r["scoredOn"] == "Trend & risk only" for r in rows if r["assetClass"] != "Stocks")
        assert all(r["scoredOn"] == "All factors" for r in rows if r["assetClass"] == "Stocks")
        # Yield means the same thing for every asset, so that sort stays a single list
        mixed = c.post("/api/screener", json={"markets": ["US", "ETFs"], "filters": {"sort": "divYield"}}).json()["rows"]
        yields = [r["divYield"] or 0 for r in mixed]
        assert yields == sorted(yields, reverse=True)


def test_every_score_shows_its_thresholds():
    """Hand-calculated: every metric is listed with the values that score 0 and 100, even when it is missing."""
    import pandas as pd

    from backend import scoring
    assert scoring.lin(25, 40, 10) == 50.0 and scoring.lin(5, 40, 10) == 100.0 and scoring.lin(50, 40, 10) == 0.0
    assert scoring.lin(0.15, 0, 0.30) == 50.0 and scoring.lin(None, 0, 1) is None

    close = pd.Series([100.0 * 1.0005 ** i for i in range(400)], index=pd.bdate_range("2024-01-01", periods=400))
    info = {"assetClass": "Stocks", "trailingPE": 25.0, "forwardPE": -5.0, "priceToBook": -3.0, "returnOnEquity": 0.15, "profitMargins": 0.05}
    bd = scoring.factor_breakdown(info, close)
    assert sum(len(v) for v in bd.values()) == 19
    by_label = {p["label"]: p for parts in bd.values() for p in parts}
    pe = by_label["P/E (trailing)"]
    assert (pe["value"], pe["score"], pe["bad"], pe["good"]) == (25.0, 50.0, 40, 10)
    # A negative multiple means nothing: it is listed, with the figure, but isn't scored
    assert by_label["Forward P/E"]["score"] is None and by_label["Forward P/E"]["value"] == -5.0
    assert by_label["Price / Book"]["score"] is None
    # Missing data is listed too
    assert by_label["Debt / Equity"]["score"] is None and by_label["Debt / Equity"]["value"] is None
    assert by_label["Return on equity"]["score"] == 50.0

    # A loss scores 0 on trailing P/E rather than being skipped
    loss = scoring.factor_breakdown({"assetClass": "Stocks", "profitMargins": -0.1}, close)["value"][0]
    assert loss["fmt"] == "loss" and loss["score"] == 0.0

    # Coverage counts real metrics: 19 for stocks, 5 for everything else
    ev = scoring.evaluate(info, close)
    assert ev["metricsTotal"] == 19 and ev["metricsScored"] == sum(p["score"] is not None for p in by_label.values())
    assert ev["coverage"] == round(ev["metricsScored"] / 19, 2)
    etf = scoring.evaluate({"assetClass": "ETFs"}, close)
    assert etf["metricsTotal"] == 5 and etf["metricsScored"] == 5 and etf["coverage"] == 1.0
    assert etf["factors"]["value"] is None

    # The composite is the weighted average of the factors that have data
    f = etf["factors"]
    assert abs(etf["composite"] - (f["momentum"] + f["risk"]) / 2) < 0.06  # factors are already rounded to 0.1
    tilted = scoring.evaluate({"assetClass": "ETFs"}, close, {"value": 0, "quality": 0, "growth": 0, "momentum": 0.75, "risk": 0.25})
    assert abs(tilted["composite"] - (f["momentum"] * 0.75 + f["risk"] * 0.25)) < 0.06
    assert abs(tilted["composite"] - etf["composite"]) > 1  # the weights really do move it


def test_stance_bands():
    from backend.scoring import stance
    cases = {70: "Strong", 69.9: "Favorable", 58: "Favorable", 57.9: "Neutral", 45: "Neutral", 44.9: "Caution", 35: "Caution", 34.9: "Weak"}
    assert all(stance(k) == v for k, v in cases.items()) and stance(None) == "Insufficient data"


def test_backup_restores_everything_or_nothing():
    from fastapi.testclient import TestClient

    from backend import db
    from backend.main import app
    _reset()
    with TestClient(app) as c:
        c.post("/api/watchlist", json={"ticker": "AAPL", "note": "صبر é"})
        c.post("/api/holdings", json={"ticker": "SPY", "shares": 5, "cost": 400, "bought": "2025-01-02", "note": "core"})
        c.post("/api/journal", json={"ticker": "AAPL", "action": "buy", "price": 150, "thesis": "services growth"})
        c.post("/api/alerts", json={"ticker": "AAPL", "kind": "price_above", "value": 500})
        c.post("/api/settings", json={"base_currency": "EUR"})

        r = c.get("/api/backup")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        backup = r.json()
        assert backup["version"] == 4 and backup["transactions"][0]["note"] == "core" and backup["settings"]["base_currency"] == "EUR"
        assert "cache" not in backup  # market data can always be downloaded again

        before = {t: db.q(f"SELECT * FROM {t}") for t in ("watchlist", "transactions", "journal", "alerts", "settings")}
        _reset()
        assert not db.q("SELECT * FROM transactions")
        assert c.post("/api/backup/restore", json=backup).status_code == 200
        after = {t: db.q(f"SELECT * FROM {t}") for t in before}
        assert after == before, "a restore must bring back identical rows"

        # Bad files change nothing, even when the problem is in the last table
        bad_row = {**backup, "alerts": [{"nonsense": 1}], "settings": {"base_currency": "USD"}}
        for bad in ({"hello": 1}, {**backup, "version": 99}, {**backup, "journal": "oops"}, bad_row):
            assert c.post("/api/backup/restore", json=bad).status_code == 400, bad
            assert {t: db.q(f"SELECT * FROM {t}") for t in before} == before

        # Unknown columns and settings can't reach the database
        extra = {**backup, "transactions": [{"ticker": "X", "shares": 1, "price": 1, "dropped": "; DROP TABLE holdings"}], "settings": {"hack": 1}}
        assert c.post("/api/backup/restore", json=extra).status_code == 200
        assert [h["ticker"] for h in db.q("SELECT * FROM transactions")] == ["X"]
        assert db.q("SELECT * FROM settings WHERE key = 'hack'") == []


def test_settings_are_checked_on_save_and_restore():
    from backend import db
    _reset()
    xss = '<img src=x onerror="alert(1)">'
    bad = [{"base_currency": xss}, {"base_currency": 5}, {"risk_free_rate": "4%"}, {"risk_free_rate": 9},
           {"weights": {"value": 1, xss: 0.5}}, {"weights": {"value": "x"}}, {"weights": {"value": 0, "risk": 0}},
           {"targets": {"Stocks": -5}}, {"targets": "all"}, {"targets": {xss: 10}}, {"benchmark": xss},
           {"dashboard_tiles": "SPY"}, {"core_target": True}, {"risk_free_rate": float("nan")}]
    with TestClient(app) as c:
        before = c.get("/api/settings").json()
        for body in bad:
            r = c.post("/api/settings", content=__import__("json").dumps(body, allow_nan=True), headers={"content-type": "application/json"})
            assert r.status_code == 400, (body, r.status_code, r.text)
            assert c.get("/api/settings").json() == before, body
        good = {"base_currency": "EUR", "risk_free_rate": 0.035, "benchmark": "VWCE.DE",
                "weights": {"value": 0.5, "quality": 0.5, "growth": 0, "momentum": 0, "risk": 0}, "targets": {"Stocks": 70, "ETFs": 30}}
        s = c.post("/api/settings", json=good).json()
        assert all(s[k] == v for k, v in good.items()), s

        # A backup carrying a bad setting or bucket is refused and changes nothing
        backup = c.get("/api/backup").json()
        for broken in ({**backup, "settings": {**backup["settings"], "base_currency": xss}},
                       {**backup, "settings": {**backup["settings"], "targets": {xss: 1}}},
                       {**backup, "buckets": [{"ticker": "AAPL", "bucket": xss}]}):
            r = c.post("/api/backup/restore", json=broken)
            assert r.status_code == 400 and ("usable" in r.text or "core or satellite" in r.text), r.text
            assert c.get("/api/settings").json() == s
        assert c.post("/api/backup/restore", json=backup).status_code == 200 and c.get("/api/settings").json() == s

        # A bad value already in the database, from before these checks, falls back to the default instead of breaking pages
        db.x("INSERT OR REPLACE INTO settings (key, value) VALUES ('targets', ?)", ('"oops"',))
        assert c.get("/api/settings").json()["targets"] == db.DEFAULT_SETTINGS["targets"]
        assert c.get("/api/portfolio").status_code == 200
    _reset()


def test_displays_say_what_they_show():
    import datetime as dt

    from backend import data
    # A yield reads in percent and an index in points, not dollars
    assert data.price_unit("^TNX", "INDEX") == "%" and data.price_unit("^GSPC", "INDEX") == "points"
    assert data.price_unit("DX-Y.NYB", "INDEX") == "points" and data.price_unit("AAPL", "EQUITY") is None and data.price_unit("SPY", "ETF") is None
    assert data.info("^TNX")["priceUnit"] == "%" and data.info("AAPL")["priceUnit"] is None

    _reset()
    import pandas as pd
    stocks = _steps([0.01] * 300, end="2026-05-29")  # weekdays, last close on Friday
    crypto = pd.Series(stocks.values, index=pd.date_range(end="2026-05-31", periods=len(stocks)))  # every day, to Sunday
    real = data.dividends_past_year
    # What was actually paid: nothing for a fund that reinvests, even with a yield on Yahoo; 2 a share for AAPL
    data.dividends_past_year = lambda t: {"VWCE.DE": 0.0, "AAPL": 2.0}.get(t)
    infos = {"VWCE.DE": {"dividendYield": 0.02}, "AAPL": {"dividendYield": 0.5}, "BTC-USD": {"dividendYield": 0.01}}
    try:
        with TestClient(app) as c, _FakeMarket({"VWCE.DE": stocks, "AAPL": stocks, "BTC-USD": crypto}, infos):
            p = c.get("/api/portfolio").json()
            assert p["positions"] == [] and p["dayFrom"] is None and p["xirrWhy"] == "Needs a dated purchase"
            for t in ("VWCE.DE", "AAPL", "BTC-USD"):
                assert c.post("/api/transactions", json={"ticker": t, "kind": "buy", "shares": 10, "price": 100}).status_code == 200
            p = c.get("/api/portfolio").json()
            # Each holding's latest day: the stocks' and crypto's differ, and both are given
            assert (p["dayFrom"], p["dayTo"]) == ("2026-05-29", "2026-05-31"), (p["dayFrom"], p["dayTo"])
            # Payments where known (0 and 2 a share); Yahoo's yield only where they aren't (BTC-USD here)
            assert abs(p["annualIncome"] - (0 + 10 * 2.0 + 10 * crypto.iloc[-1] * 0.01)) < 1e-6, p["annualIncome"]
            assert p["xirr"] is None and p["xirrWhy"] == "Needs a date on every purchase and sale"
        _reset()
        with TestClient(app) as c, _FakeMarket({"AAPL": stocks, "^GSPC": stocks}):
            day = (dt.date.today() - dt.timedelta(days=10)).isoformat()
            assert c.post("/api/transactions", json={"ticker": "AAPL", "kind": "buy", "date": day, "shares": 1, "price": 100}).status_code == 200
            # Under a year: no annualised figure (the GIPS standard), but the return so far, gain over what was put in
            p = c.get("/api/portfolio").json()
            assert p["xirr"] is None and p["xirrWhy"].startswith("Shown after a year of history")
            assert abs(p["returnSoFar"] - (p["totalGain"] / 100)) < 1e-9, (p["returnSoFar"], p["totalGain"])
            # Dashboard tiles say when their price is old
            tile = [x for x in c.get("/api/macro").json() if x["ticker"] == "^GSPC"]
            assert tile and tile[0]["stale"] is False and tile[0]["asOf"] == "2026-05-29", tile
    finally:
        data.dividends_past_year = real
        _reset()
    assert data.dividends_past_year("AAPL") is None, "demo mode uses the yield"


def test_old_database_gains_the_new_columns():
    """Upgrading must keep existing journal entries."""
    import sqlite3
    import tempfile
    from pathlib import Path

    from backend import db
    path = Path(tempfile.mkdtemp()) / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ticker TEXT, action TEXT, price REAL, "
                "thesis TEXT DEFAULT '', invalidation TEXT DEFAULT '', stop_price REAL, target_price REAL, review_date TEXT DEFAULT '', outcome TEXT DEFAULT '')")
    old.execute("INSERT INTO journal (ts, ticker, action, price, thesis) VALUES (1, 'AAPL', 'buy', 150, 'kept')")
    old.commit()
    old.close()
    real = db.DB_PATH
    db.DB_PATH = path
    try:
        db.init()
        db.init()  # running it again must be harmless
        row = db.q("SELECT * FROM journal")[0]
        assert row["thesis"] == "kept" and row["score"] is None and "benchmark" in row and "stance" in row and "factors" in row
    finally:
        db.DB_PATH = real


def test_journal_snapshots_the_score_when_saved():
    from fastapi.testclient import TestClient

    from backend import db
    from backend.main import app
    _reset()
    with TestClient(app) as c:
        c.post("/api/settings", json={"benchmark": "QQQ"})
        assert c.post("/api/journal", json={"ticker": "AAPL", "action": "buy", "price": 150}).status_code == 200
        e = db.q("SELECT * FROM journal")[0]
        assert e["score"] is not None and e["stance"] and "value" in e["factors"] and e["benchmark"] == "QQQ"
        # A symbol with no data still saves: the note matters more than the snapshot
        with _FakeMarket({"AAPL": None}):
            assert c.post("/api/journal", json={"ticker": "AAPL", "action": "watch", "thesis": "no data today"}).status_code == 200
        kept = db.q("SELECT * FROM journal WHERE action = 'watch'")[0]
        assert kept["thesis"] == "no data today" and kept["score"] is None and kept["benchmark"] == "QQQ"


def test_scorecard_is_hand_calculated():
    import datetime as dt

    import pandas as pd

    from backend import db, insights
    _reset()
    idx = pd.bdate_range("2026-01-05", periods=100)
    rising = pd.Series([100 + i * 20 / 99 for i in range(100)], index=idx)  # 100 to 120
    falling = pd.Series([100 - i * 10 / 99 for i in range(100)], index=idx)  # 100 to 90
    bench = pd.Series([100 + i * 10 / 99 for i in range(100)], index=idx)  # 100 to 110
    ts = dt.datetime(2026, 1, 5, 18, 0).timestamp()

    def add(ticker, action, price, stance, **kw):
        cols = {"ts": ts, "ticker": ticker, "action": action, "price": price, "stance": stance, "benchmark": "SPY", **kw}
        db.x(f"INSERT INTO journal ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))

    add("UP", "buy", 100, "Strong", target_price=115)  # +20% against +10%: excess +10%, target reached
    add("DOWN", "add", 100, "Weak", stop_price=95)  # -10% against +10%: excess -20%, stop hit
    add("UP", "sell", 100, "Strong")  # a sale isn't a call to be judged the same way
    add("UP", "watch", None, None)
    add("GONE", "buy", 100, "Strong")  # no data: reported, not counted
    add("UP", "buy", None, "Neutral", ts=dt.datetime(2026, 1, 5, 18, 0).timestamp() - 0)  # no price: uses that day's close, 100

    with _FakeMarket({"UP": rising, "DOWN": falling, "SPY": bench}):
        card = insights.scorecard(db.get_settings())
    rows = {(e["ticker"], e["action"], e["stance"]): e for e in card["entries"]}
    up = rows[("UP", "buy", "Strong")]
    assert abs(up["returnSince"] - 0.20) < 1e-9 and abs(up["benchmarkReturn"] - 0.10) < 1e-9 and abs(up["excess"] - 0.10) < 1e-9
    assert up["status"] == "Target reached" and up["benchmark"] == "SPY" and up["counted"] is True
    down = rows[("DOWN", "add", "Weak")]
    assert abs(down["returnSince"] + 0.10) < 1e-9 and abs(down["excess"] + 0.20) < 1e-9 and down["status"] == "Stop hit"
    assert rows[("UP", "sell", "Strong")]["counted"] is False and rows[("UP", "watch", None)]["counted"] is False
    assert rows[("GONE", "buy", "Strong")]["error"] and rows[("GONE", "buy", "Strong")]["counted"] is False
    assert abs(rows[("UP", "buy", "Neutral")]["returnSince"] - 0.20) < 1e-9

    s = card["summary"]
    assert s["counted"] == 3 and abs(s["hitRate"] - 2 / 3) < 1e-9
    assert abs(s["avgExcess"] - (0.10 - 0.20 + 0.10) / 3) < 1e-9 and abs(s["avgReturn"] - (0.20 - 0.10 + 0.20) / 3) < 1e-9
    assert s["benchmarks"] == ["SPY"]
    bands = {b["stance"]: b for b in s["byStance"]}
    assert list(bands) == ["Strong", "Neutral", "Weak"]  # best to worst, only those with entries
    assert bands["Strong"]["n"] == 1 and abs(bands["Strong"]["avgExcess"] - 0.10) < 1e-9 and bands["Strong"]["hitRate"] == 1.0
    assert bands["Weak"]["hitRate"] == 0.0

    # A decision made today has had no time to work, so it isn't judged yet
    _reset()
    add("UP", "buy", 100, "Strong", ts=dt.datetime.now().timestamp())
    with _FakeMarket({"UP": rising, "SPY": bench}):
        fresh = insights.scorecard(db.get_settings())
    assert fresh["entries"][0]["counted"] is False and fresh["summary"]["counted"] == 0 and fresh["summary"]["hitRate"] is None


def test_pretrade_check_is_hand_calculated_and_changes_nothing():
    import pandas as pd

    from backend import db, portfolio
    _reset()
    idx = pd.bdate_range(end="2026-06-01", periods=300)
    wave = pd.Series([100 + 5 * ((i % 40) - 20) / 20 for i in range(300)], index=idx)
    other = pd.Series([100 - 5 * ((i % 40) - 20) / 20 for i in range(300)], index=idx)
    flat = wave * 0 + 90
    new = wave * 0.5  # same moves as AAA, last close (100 + 5 * ((299 % 40) - 20) / 20) / 2
    price = float(new.iloc[-1])
    prices = {"AAA": wave, "BBB": other, "NEW": new, "SPY": wave, "EUROCO": flat}
    infos = {"AAA": {"sector": "Tech"}, "BBB": {"sector": "Energy"}, "NEW": {"sector": "Tech", "region": "Europe"},
             "EUROCO": {"currency": "EUR", "sector": "Banks"}}
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('AAA', 90, 100), ('BBB', 10, 100)")
    settings = db.get_settings()
    lots_before = db.q("SELECT * FROM transactions")

    with _FakeMarket(prices, infos):
        total = portfolio.summary(settings)["total"]
        aaa_value = 90 * float(wave.iloc[-1])
        r = portfolio.pretrade(settings, "NEW", amount=1000)
        assert abs(r["shares"] - 1000 / price) < 1e-9 and abs(r["value"] - 1000) < 1e-6
        assert abs(r["after"]["position"] - 1000 / (total + 1000)) < 1e-9 and r["before"]["position"] == 0
        # Tech goes from AAA alone to AAA plus the new 1,000
        assert abs(r["before"]["sector"] - aaa_value / total) < 1e-9
        assert abs(r["after"]["sector"] - (aaa_value + 1000) / (total + 1000)) < 1e-9
        assert abs(r["before"]["region"]) < 1e-9 and abs(r["after"]["region"] - 1000 / (total + 1000)) < 1e-9
        # It moves exactly with AAA and against BBB
        corr = {c["ticker"]: c["correlation"] for c in r["correlations"]}
        assert corr["AAA"] > 0.99 and corr["BBB"] < -0.99
        assert r["correlations"][0]["ticker"] == "AAA"
        assert "beta" in r["before"] and "beta" in r["after"]
        assert r["incomplete"] is False

        # Position size from risk: lose 100 if 50 falls to 45 means 20 shares
        sized = portfolio.pretrade(settings, "NEW", entry=50, stop=45, risk=100)
        assert abs(sized["shares"] - 20) < 1e-9 and abs(sized["value"] - 1000) < 1e-6 and abs(sized["maxLoss"] - 100) < 1e-6
        # An amount wins over risk sizing, and the loss at the stop is shown for that amount
        both = portfolio.pretrade(settings, "NEW", amount=500, entry=50, stop=45, risk=100)
        assert abs(both["shares"] - 10) < 1e-9 and abs(both["maxLoss"] - 50) < 1e-6
        assert abs(both["sizedShares"] - 20) < 1e-9

        # In another currency: 1,080 USD at EUR/USD 1.08 and 90 EUR a share is 1000 EUR, 11.11 shares
        fx = portfolio.pretrade(settings, "EUROCO", amount=1080)
        assert abs(fx["shares"] - 1000 / 90) < 1e-3 and fx["currency"] == "EUR"
        # A 100 USD risk with a 10 EUR stop distance is 92.59 EUR of risk, 9.26 shares
        fxr = portfolio.pretrade(settings, "EUROCO", entry=90, stop=80, risk=100)
        assert abs(fxr["shares"] - (100 / 1.08) / 10) < 1e-3

        # Adding to an existing holding counts as more of the same position
        more = portfolio.pretrade(settings, "AAA", amount=1000)
        assert abs(more["after"]["position"] - (aaa_value + 1000) / (total + 1000)) < 1e-9
        assert more["before"]["position"] == more["before"]["sector"]
        assert not [c for c in more["correlations"] if c["ticker"] == "AAA"]

        # New warnings are the ones this trade would cause
        big = portfolio.pretrade(settings, "NEW", amount=total)
        assert any("NEW is" in w for w in big["newWarnings"])
        # AAA was already over 20%. Its figure changes, but this trade didn't cause that warning
        assert not any("AAA is" in w for w in big["newWarnings"])

        for bad in ({}, {"amount": -5}, {"entry": 50, "stop": 55, "risk": 100}, {"entry": 50, "stop": 50, "risk": 100}, {"risk": 100}):
            try:
                portfolio.pretrade(settings, "NEW", **bad)
                raise AssertionError(f"should be refused: {bad}")
            except ValueError as e:
                assert str(e)

    # While a holding has no data the weights would be wrong, so they're withheld
    with _FakeMarket({**prices, "BBB": None}, infos):
        part = portfolio.pretrade(settings, "NEW", amount=1000)
    assert part["incomplete"] is True and part["missing"] == ["BBB"] and part["after"] is None and part["before"] is None

    # It's a what-if: nothing was saved
    assert db.q("SELECT * FROM transactions") == lots_before


def test_pretrade_route():
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app) as c:
        c.post("/api/holdings", json={"ticker": "SPY", "shares": 5, "cost": 400})
        ok = c.post("/api/pretrade", json={"ticker": "aapl", "amount": "1000", "stop": "", "entry": "", "risk": ""})
        assert ok.status_code == 200 and ok.json()["ticker"] == "AAPL" and ok.json()["shares"] > 0
        assert c.post("/api/pretrade", json={"ticker": "AAPL"}).status_code == 400
        assert c.post("/api/pretrade", json={"ticker": "AAPL", "amount": "lots"}).status_code == 400
        assert len(c.get("/api/portfolio").json()["positions"]) == 1


def test_cross_rates_go_through_the_dollar():
    """Yahoo's USD pairs are the reliable ones: its QAR/EUR history is 3.7% off and KWD/EUR 1.1% off."""
    import pandas as pd

    from backend import data, db

    def history_of(rates):
        return pd.Series(rates, index=pd.to_datetime(["2024-06-03", "2026-06-01"]))

    real_demo = data.DEMO
    data.DEMO = False
    db.cache_clear()
    try:
        db.cache_set("fx:QARUSD", 0.2747)
        db.cache_set("fx:EURUSD", 1.12)
        db.cache_set("fx:KWDUSD", 3.25)
        # Today: both legs through USD, even with a wrong direct rate sitting in the cache
        db.cache_set("fx:KWDEUR", 2.86)
        assert abs(data.fx_rate("KWD", "EUR") - 3.25 / 1.12) < 1e-9
        assert abs(data.fx_rate("KWF", "EUR") - 3.25 / 1.12 / 1000) < 1e-12
        assert abs(data.fx_rate("QAR", "EUR") - 0.2747 / 1.12) < 1e-9
        assert abs(data.fx_rate("EUR", "QAR") - 1.12 / 0.2747) < 1e-9

        # A past date: the peg, over the euro on that date. The direct QAR/EUR history here is wrong on purpose
        eur = history_of([1.0855, 1.12])
        gbp = history_of([1.27, 1.34])
        wrong = history_of([0.2625, 0.2453])
        with _FakeMarket({"EURUSD=X": eur, "GBPUSD=X": gbp, "QAREUR=X": wrong}):
            assert abs(data.fx_on("QAR", "EUR", "2024-06-03") - 0.2747 / 1.0855) < 1e-9
            assert abs(data.fx_on("GBp", "EUR", "2024-06-03") - 1.27 / 1.0855 / 100) < 1e-9
            assert abs(data.fx_on("EUR", "USD", "2024-06-03") - 1.0855) < 1e-9
            assert data.fx_on("HKD", "EUR", "2024-06-03") is None  # one leg missing means no rate
    finally:
        data.DEMO = real_demo
        db.cache_clear()


def test_fired_alerts_stay_in_the_digest_until_dismissed():
    """Alerts usually fire in the background loop, so the digest has to show what fired earlier, not just now."""
    from fastapi.testclient import TestClient

    from backend import insights
    from backend.main import app
    _reset()
    with TestClient(app) as c:
        aid = c.post("/api/alerts", json={"ticker": "AAPL", "kind": "price_above", "value": 0.01}).json()["id"]
        c.post("/api/alerts", json={"ticker": "AAPL", "kind": "price_above", "value": 1e12})

        def alert_items():
            return [i for i in c.get("/api/digest").json()["items"] if i["text"].startswith("Alert:")]

        # Fired by the background check, not by the digest
        assert len(insights.check_alerts({})) == 1
        items = alert_items()
        assert len(items) == 1 and items[0]["alertId"] == aid
        assert len(alert_items()) == 1, "still there after a refresh"
        first = c.get("/api/digest").json()["items"][0]
        assert first["text"].startswith("Alert:"), "alerts come first"

        assert c.post(f"/api/alerts/{aid}/dismiss").status_code == 200
        assert alert_items() == []
        # Dismissing only clears it from the digest: the alert's record stays on the Alerts list
        row = [a for a in c.get("/api/alerts").json() if a["id"] == aid][0]
        assert row["active"] == 0 and row["message"]
        assert c.post("/api/alerts/99999/dismiss").status_code == 200


def test_journal_stops_work_for_any_ticker():
    """A stop or target applies whether or not the ticker is held or watched, without adding it to the digest's moves."""
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app) as c:
        c.post("/api/journal", json={"ticker": "MSFT", "action": "buy", "price": 100, "stop_price": 1e9})
        c.post("/api/journal", json={"ticker": "NVDA", "action": "buy", "price": 100, "target_price": 0.01})
        c.post("/api/journal", json={"ticker": "AMD", "action": "buy", "price": 100, "stop_price": 1e9})
        jid = [j for j in c.get("/api/journal").json() if j["ticker"] == "AMD"][0]["id"]
        c.patch(f"/api/journal/{jid}", json={"outcome": "sold"})
        items = c.get("/api/digest").json()["items"]
        texts = " | ".join(i["text"] for i in items)
        assert "MSFT hit your thesis stop" in texts and "NVDA reached your target" in texts
        assert "AMD" not in texts, "a closed decision has nothing to watch"
        assert not any(i["ticker"] in ("MSFT", "NVDA") and ("moved" in i["text"] or "RSI" in i["text"]) for i in items)


def test_backtest_fills_at_the_next_open():
    """A signal from today's close can only trade at tomorrow's open. Hand-calculated with no costs."""
    import pandas as pd

    from backend import backtest
    closes = [100.0] * 60 + [110, 120, 90, 100, 110]
    opens = [100.0] * 60 + [105, 112, 118, 88, 102]
    df = pd.DataFrame({"Open": opens, "Close": closes}, index=pd.bdate_range("2024-01-01", periods=65))
    # Lookback 1 means long the day after any up close:
    #   110 up -> buy at the next open, 112, and that day ends at 120
    #   120 up -> hold, and that day ends at 90
    #    90 down -> sell at the next open, 88
    #   100 up -> buy at the next open, 102, and that day ends at 110
    r = backtest.run(df, "momentum", {"lookback": 1}, 0, 0.0)
    expected = (120 / 112) * (90 / 120) * (88 / 90) * (110 / 102) - 1
    assert abs(r["strategy"]["totalReturn"] - expected) < 1e-9, (r["strategy"]["totalReturn"], expected)
    assert r["strategy"]["trades"] == 3

    # Costs still come off each trade
    costed = backtest.run(df, "momentum", {"lookback": 1}, 100, 0.0)
    assert costed["strategy"]["totalReturn"] < r["strategy"]["totalReturn"]

    # No look-ahead: changing a later price can't change anything before it
    moved = df.copy()
    moved.loc[moved.index[-1], "Close"] = 500.0
    later = backtest.run(moved, "momentum", {"lookback": 1}, 0, 0.0)
    assert later["equity"][:-1] == r["equity"][:-1]


def test_compare_handles_different_calendars_and_histories():
    import pandas as pd

    from backend import insights
    idx = pd.date_range("2025-01-06", periods=500, freq="D")
    daily = pd.Series([100 * (1.01 if i % 7 < 3 else 0.992) ** (i % 13) for i in range(500)], index=idx)
    weekdays = daily[daily.index.day_of_week < 5]  # the same asset, on a stock market's calendar
    short = daily.iloc[-60:]
    with _FakeMarket({"CRYPTO": daily, "STOCK": weekdays, "NEW": short}):
        d = insights.compare(["CRYPTO", "STOCK", "NEW", "GONE"], "1y")
    assert d["failed"] == ["GONE"] and d["short"] == ["NEW"]
    # The same asset correlates fully, whatever days each market trades
    m = dict(zip(d["correlation"]["tickers"], d["correlation"]["matrix"]))
    assert m["CRYPTO"][d["correlation"]["tickers"].index("STOCK")] > 0.97
    assert m["CRYPTO"][d["correlation"]["tickers"].index("CRYPTO")] == 1.0
    # One new listing doesn't cut the others down, and each line starts at 100
    assert len(d["dates"]) > 300
    assert d["series"]["CRYPTO"][0] == 100.0
    first_new = next(v for v in d["series"]["NEW"] if v is not None and v == v)
    assert first_new == 100.0
    assert d["series"]["NEW"][0] != d["series"]["NEW"][0] or d["series"]["NEW"][0] is None


def test_plain_language_parser_reads_what_was_written():
    from backend.ai import parse_query as p
    # Words inside other words don't count
    assert "markets" not in p("generous dividend payers")
    assert "markets" not in p("famous companies")
    assert "markets" not in p("netflix style growth")
    assert "markets" not in p("show us cheap stocks") and "countries" not in p("show us cheap stocks")
    # Real mentions still do: a country is a country, a region a region
    assert p("cheap US stocks")["countries"] == ["United States"] and "markets" not in p("cheap US stocks")
    assert p("american value")["countries"] == ["United States"]
    assert p("etfs from the usa")["markets"] == ["ETFs"] and p("etfs from the usa")["countries"] == ["United States"]
    assert p("european banks")["markets"] == ["Europe"]
    assert p("best stocks in Qatar")["countries"] == ["Qatar"]
    # Ways of writing P/E
    for q in ("stocks with PE ratio under 15", "stocks with a p/e of 15", "P/E below 15", "under 15 pe", "pe < 15"):
        assert p(q).get("max_pe") == 15.0, q
    # Yield and growth are separate things, and dividend growth is neither
    assert p("yield above 4%") == {"min_div_yield": 0.04}
    assert p("revenue growth above 20%") == {"min_rev_growth": 0.20, "sort": "growth"}
    dg = p("dividend growth above 5%")
    assert "min_div_yield" not in dg and "min_rev_growth" not in dg and "dividend" in dg["ignored"] and "growth" in dg["ignored"]
    # Negation isn't silently read as the opposite
    no = p("not cheap")
    assert "max_pe" not in no and "cheap" in no["ignored"]
    assert p("no debt")["max_de"] == 0.5
    assert "min_roe" not in p("not profitable")
    # Sectors
    assert p("tech stocks") == {"sector": "Technology"}
    assert p("gulf banks") == {"countries": ["Qatar", "Saudi Arabia", "UAE", "Kuwait", "Bahrain", "Oman"], "sector": "Financial Services", "industry": "Banks"}
    # Anything else is reported rather than dropped without a word
    assert p("large cap stocks")["ignored"] == ["large", "cap"]
    assert p("low risk")["max_vol"] == 0.25
    assert "ignored" not in p("cheap profitable dividend stocks in the gulf")


def test_screener_sector_filter():
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app) as c:
        rows = c.post("/api/screener", json={"markets": ["US"], "query": "tech stocks"}).json()
        assert rows["rows"] and all(r["sector"] == "Technology" for r in rows["rows"])
        none = c.post("/api/screener", json={"markets": ["US"], "filters": {"sector": "Nope"}}).json()
        assert none["rows"] == []


def test_price_to_book_for_a_different_statement_currency():
    """Market cap over equity in one currency. Yahoo's own figure can mix the two."""
    import pandas as pd

    from backend import data

    class Listing:
        balance_sheet = pd.DataFrame({"2025-12-31": [4000.0]}, index=["Stockholders Equity"])

    class YF:
        Ticker = staticmethod(lambda t: Listing())

    base = {"quoteType": "EQUITY", "marketCap": 1000.0, "priceToBook": 99.0}
    real_yf, real_demo = data._yf, data.DEMO
    real_fx = data.statement_fx
    data._yf, data.DEMO = (lambda: YF), False
    data.statement_fx = lambda d: 0.5
    try:
        d = data._normalize_info("X.L", {**base, "currency": "GBp", "financialCurrency": "USD"})
        assert d["priceToBook"] == 1000.0 / (4000.0 * 0.5)
        # The latest quarter wins over the last annual figure, in whatever order Yahoo lists them
        Listing.quarterly_balance_sheet = pd.DataFrame({"2025-06-30": [3000.0], "2026-03-31": [2000.0]}, index=["Stockholders Equity"])
        d = data._normalize_info("X.L", {**base, "currency": "GBp", "financialCurrency": "USD"})
        assert d["priceToBook"] == 1000.0 / (2000.0 * 0.5)
        del Listing.quarterly_balance_sheet
        # Same currency: Yahoo's own figure stands, and no extra download
        data.statement_fx = lambda d: 1.0
        assert data._normalize_info("X", {**base, "currency": "USD", "financialCurrency": "USD"})["priceToBook"] == 99.0
        # No equity figure, or a negative one: blank rather than the mixed figure
        data.statement_fx = lambda d: 0.5
        Listing.balance_sheet = pd.DataFrame({"2025-12-31": [-10.0]}, index=["Stockholders Equity"])
        assert data._normalize_info("X.L", {**base, "currency": "GBp", "financialCurrency": "USD"})["priceToBook"] is None
        Listing.balance_sheet = None
        assert data._normalize_info("X.L", {**base, "currency": "GBp", "financialCurrency": "USD"})["priceToBook"] is None
    finally:
        data._yf, data.DEMO, data.statement_fx = real_yf, real_demo, real_fx


def test_upcoming_events_only_show_future_dates():
    import datetime as dt

    from backend import data, db, insights
    from fastapi.testclient import TestClient

    from backend.main import app
    today = dt.date(2026, 6, 1)
    ts = lambda d: dt.datetime(d.year, d.month, d.day, 12).timestamp()
    ev = insights.upcoming({"earningsTimestampStart": ts(today + dt.timedelta(days=10)), "exDividendDate": ts(today - dt.timedelta(days=3))}, today)
    assert ev == {"earnings": "2026-06-11", "earningsDays": 10, "exDividend": None, "exDividendDays": None}
    assert insights.upcoming({}, today) == {"earnings": None, "earningsDays": None, "exDividend": None, "exDividendDays": None}
    assert insights.upcoming({"exDividendDate": ts(today)}, today)["exDividendDays"] == 0

    # The digest mentions them for holdings when they're close, and only for holdings
    _reset()
    soon, far = ts(dt.date.today() + dt.timedelta(days=5)), ts(dt.date.today() + dt.timedelta(days=60))
    flat = _steps([0.0] * 300)
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('SOON', 1, 100), ('FAR', 1, 100)")
    db.x("INSERT INTO watchlist (ticker) VALUES ('WATCHED')")
    infos = {"SOON": {"earningsTimestampStart": soon, "exDividendDate": soon}, "FAR": {"earningsTimestampStart": far}, "WATCHED": {"earningsTimestampStart": soon}}
    with _FakeMarket({"SOON": flat, "FAR": flat, "WATCHED": flat}, infos):
        texts = [i["text"] for i in insights.digest(db.get_settings())["items"]]
    assert any("SOON reports earnings in 5 days" in t for t in texts) and any("SOON goes ex-dividend in 5 days" in t for t in texts)
    assert not any("FAR reports" in t or "WATCHED reports" in t for t in texts)

    # Demo mode has them too
    _reset()
    with TestClient(app) as c:
        j = c.get("/api/stock/AAPL").json()
        assert j["events"]["earnings"] and j["events"]["earningsDays"] >= 0


def test_reverse_dcf_and_sensitivity_grid():
    from backend import insights
    inf = {"freeCashflow": 1e9, "sharesOutstanding": 1e9, "currency": "USD", "financialCurrency": "USD"}
    # At 0% growth, 10% discount and 0% terminal, one year is worth exactly 10 a share (see the DCF test)
    assert abs(insights.implied_growth(inf, 10.0, 1, 0.10, 0.0)) < 1e-6
    # A higher price needs more growth: at 1 year and no terminal growth, fair value is 10 x (1 + g)
    assert abs(insights.implied_growth(inf, 12.0, 1, 0.10, 0.0) - 0.20) < 1e-6
    assert abs(insights.implied_growth(inf, 8.0, 1, 0.10, 0.0) + 0.20) < 1e-6
    # Prices no plausible growth rate can reach say so
    assert insights.implied_growth(inf, 1e6, 1, 0.10, 0.0) is None
    assert insights.implied_growth({**inf, "freeCashflow": -1e9}, 10.0, 1, 0.10, 0.0) is None

    g = insights.sensitivity(inf, 0.10, 5, 0.10, 0.02)
    assert g["growth"] == [0.05, 0.075, 0.10, 0.125, 0.15] and g["discount"] == [0.08, 0.09, 0.10, 0.11, 0.12]
    centre = insights.dcf(inf, 0.10, 5, 0.10, 0.02)["fairValue"]
    assert g["fairValue"][2][2] == centre
    # More growth is worth more, and a higher discount rate is worth less
    assert g["fairValue"][4][2] > centre > g["fairValue"][0][2] and g["fairValue"][2][0] > centre > g["fairValue"][2][4]
    # A discount rate at or below terminal growth has no answer
    low = insights.sensitivity(inf, 0.10, 5, 0.04, 0.02)
    assert low["discount"][0] == 0.02 and low["fairValue"][2][0] is None

    from fastapi.testclient import TestClient

    from backend.main import app
    with TestClient(app) as c:
        for t in ("AAPL", "MSFT", "NVDA", "JPM", "KO"):
            d = c.get(f"/api/dcf/{t}").json()
            if "fairValue" in d:
                assert "impliedGrowth" in d and len(d["grid"]["fairValue"]) == 5
                break
        else:
            raise AssertionError("no demo ticker had a DCF")


def test_risk_figures_are_hand_calculated():
    import math

    import pandas as pd

    from backend import indicators as ind
    idx = pd.bdate_range("2024-01-01", periods=61)
    returns = [0.01, -0.02, 0.03, -0.01] * 15
    px = [100.0]
    for r in returns:
        px.append(px[-1] * (1 + r))
    close = pd.Series(px, index=idx)
    r = pd.Series(returns)
    target = 0.04 / 252
    downside = math.sqrt(sum(min(x - target, 0) ** 2 for x in returns) / len(returns))
    assert abs(ind.sortino(close, 0.04) - (r.mean() - target) / downside * math.sqrt(252)) < 1e-9
    assert abs(ind.sharpe(close, 0.04) - (r.mean() - target) / r.std() * math.sqrt(252)) < 1e-9
    assert abs(ind.max_drawdown(pd.Series([100.0, 120, 60, 90], index=idx[:4])) + 0.5) < 1e-12
    assert ind.rsi(pd.Series([1.0, 2, 3, 4, 5] * 10, index=pd.bdate_range("2024-01-01", periods=50))).iloc[-1] < 100

    # "5Y" needs about five years of history
    days = pd.bdate_range(end="2026-06-01", periods=510)
    assert ind.stats(pd.Series(range(100, 610), index=days, dtype=float))["ret5yCagr"] is None
    days = pd.bdate_range(end="2026-06-01", periods=1250)
    assert ind.stats(pd.Series(range(100, 1350), index=days, dtype=float))["ret5yCagr"] is not None


def test_etfs_are_not_called_a_concentrated_sector():
    from backend import db, portfolio
    _reset()
    for t in ("VTI", "VXUS", "BND", "GLD", "VNQ", "SCHD"):
        db.x("INSERT INTO transactions (ticker, shares, price) VALUES (?, 1, 100)", (t,))
    flat = _steps([0.0] * 300)
    etf = {"assetClass": "ETFs", "sector": "ETFs"}
    tickers = ("VTI", "VXUS", "BND", "GLD", "VNQ", "SCHD")
    with _FakeMarket({t: flat for t in tickers}, {t: etf for t in tickers}):
        warnings = portfolio.summary(db.get_settings())["warnings"]
    assert not any("sector" in w for w in warnings), warnings


def test_other_exchanges_are_not_treated_as_us_dollars():
    from backend import data, db, portfolio
    from backend.universe import currency_guess, region_of
    for t, cur, region in (("7203.T", "JPY", "Asia-Pacific"), ("0700.HK", "HKD", "Asia-Pacific"), ("SHOP.TO", "CAD", "North America"), ("BHP.AX", "AUD", "Asia-Pacific"),
                           ("RELIANCE.NS", "INR", "Asia-Pacific"), ("VOW3.F", "EUR", "Europe"), ("AAPL", "USD", "North America"), ("BRK-B", "USD", "North America"),
                           ("PETR4.SA", "BRL", "Latin America"), ("SQM-B.SN", "CLP", "Latin America"), ("NPN.JO", "ZAc", "Middle East & Africa"),
                           ("BBCA.JK", "IDR", "Asia-Pacific"), ("MELI", "USD", "Latin America"), ("NBK.KW", "KWF", "Middle East & Africa"),
                           ("TEVA.TA", "ILA", "Middle East & Africa"), ("THYAO.IS", "TRY", "Middle East & Africa")):
        assert currency_guess(t) == cur and region_of(t) == region, t
    assert currency_guess("ABC.ZZ") is None and region_of("ABC.ZZ") == "Other"
    assert region_of("^GSPC") == "North America" and region_of("EURUSD=X") == "North America"
    # With no currency at all a position is unpriced, not valued 1:1 with USD
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('ABC.ZZ', 100, 300)")
    with _FakeMarket({"ABC.ZZ": _steps([0.001] * 30)}, {"ABC.ZZ": {"currency": None}}):
        p = portfolio.summary(db.get_settings())
    assert p["missing"] == ["ABC.ZZ"] and p["total"] == 0


def test_bad_input_gets_a_clear_refusal():
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app, raise_server_exceptions=False) as c:
        bad = [
            ("/api/holdings", {"ticker": "AAPL", "shares": "", "cost": 100}), ("/api/holdings", {"ticker": "AAPL", "shares": "inf", "cost": 100}),
            ("/api/holdings", {"ticker": "AAPL", "shares": 1, "cost": "abc"}), ("/api/holdings", {"ticker": "", "shares": 1, "cost": 1}),
            ("/api/alerts", {"ticker": "AAPL", "kind": "price_above", "value": ""}), ("/api/alerts", {"ticker": "", "kind": "price_above", "value": 1}),
            ("/api/backtest", {"ticker": "SPY", "cost_bps": ""}), ("/api/backtest", {"ticker": "SPY", "cost_bps": -5}),
            ("/api/backtest", {"ticker": "SPY", "strategy": "rsi_reversion", "params": {"period": 0}}),
            ("/api/backtest", {"ticker": "SPY", "strategy": "sma_cross", "params": {"fast": 0, "slow": 0}}),
            ("/api/backtest", {"ticker": "SPY", "strategy": "sma_cross", "params": {"fast": 200, "slow": 50}}),
            ("/api/backtest", {"ticker": "SPY", "strategy": "momentum", "params": {"lookback": -1}}),
        ]
        for path, body in bad:
            r = c.post(path, json=body)
            assert r.status_code == 400 and isinstance(r.json()["detail"], str), (path, body, r.status_code, r.text[:100])
        assert c.get("/api/portfolio").json()["positions"] == []
        assert c.get("/api/alerts").json() == []


def test_alert_for_an_unknown_ticker_is_refused():
    from fastapi.testclient import TestClient

    from backend.main import app
    _reset()
    with TestClient(app) as c, _FakeMarket({"AAPL": _steps([0.0] * 30)}):
        assert c.post("/api/alerts", json={"ticker": "AAPL", "kind": "price_above", "value": 1}).status_code == 200
        assert c.post("/api/alerts", json={"ticker": "NOPE", "kind": "price_above", "value": 1}).status_code == 404


def test_only_local_addresses_are_served():
    """Stops a website pointing its own domain name at this machine and reading the app through your browser."""
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    from backend import main
    with TestClient(main.app) as c:
        assert c.get("/api/meta", headers={"Host": "evil.example"}).status_code == 400
        assert c.delete("/api/holdings/1", headers={"Host": "evil.example"}).status_code == 400
        for host in ("localhost:8001", "127.0.0.1:8001", "[::1]:8001"):
            assert c.get("/api/meta", headers={"Host": host}).status_code == 200, host

        # A website you visit can't make your browser clear the cache, change data or spend the AI allowance
        local = {"Host": "localhost:8001"}
        for attack in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},  # same-site: another app on this machine
                       {"Origin": "https://evil.example"}, {"Origin": "http://localhost:8000"}, {"Origin": "null"}):
            for call in (lambda h: c.post("/api/cache/clear", headers=h), lambda h: c.post("/api/stock/AAPL/brief", headers=h),
                         lambda h: c.post("/api/settings", json={"base_currency": "EUR"}, headers=h), lambda h: c.get("/api/backup", headers=h)):
                assert call({**local, **attack}).status_code == 403, attack
        assert c.get("/api/settings").json()["base_currency"] == "USD"
        # Your own page, an address typed in the browser and scripts still work; so does opening the page from a link
        for fine in ({"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8001"}, {"Sec-Fetch-Site": "none"}, {}):
            assert c.post("/api/cache/clear", headers={**local, **fine}).status_code == 200, fine
        assert c.get("/", headers={**local, "Sec-Fetch-Site": "cross-site"}).status_code == 200
        assert c.get("/api/stock/AAPL/brief").status_code == 405, "the brief is a POST, so a link or image can't ask for one"

    # A .env saved by Notepad starts with a byte-order mark, which used to rename the key
    env = Path(tempfile.mkdtemp()) / ".env"
    env.write_bytes("\ufeffSTOCKPILOT_TEST_KEY=héllo\n".encode("utf-8"))
    main._load_env(env)
    assert os.environ.pop("STOCKPILOT_TEST_KEY") == "héllo"


def test_ledger_matches_sales_to_the_oldest_lots():
    from backend import ledger
    rows = [
        {"id": 1, "ticker": "A", "kind": "buy", "date": "2024-01-02", "shares": 10, "price": 100, "note": "first"},
        {"id": 2, "ticker": "A", "kind": "buy", "date": "2024-06-03", "shares": 10, "price": 120},
        {"id": 3, "ticker": "A", "kind": "sell", "date": "2025-01-02", "shares": 15, "price": 150},
        {"id": 4, "ticker": "A", "kind": "dividend", "date": "2025-02-03", "amount": 12.5},
    ]
    a = ledger.replay(rows)["A"]
    assert [(l["id"], l["shares"], l["cost"], l["bought"]) for l in a["lots"]] == [(2, 5, 120, "2024-06-03")]
    assert [(s["shares"], s["cost"], s["bought"], s["price"], s["date"]) for s in a["sales"]] == [
        (10, 100, "2024-01-02", 150, "2025-01-02"), (5, 120, "2024-06-03", 150, "2025-01-02")]
    assert a["dividends"] == [{"id": 4, "date": "2025-02-03", "amount": 12.5}]
    # Date order decides, not the order they were typed in, and a same-day buy comes before a sale
    shuffled = ledger.replay([rows[2], rows[1], rows[0]])["A"]
    assert shuffled["lots"][0]["shares"] == 5 and shuffled["sales"][0]["cost"] == 100
    same_day = ledger.replay([{"id": 9, "ticker": "B", "kind": "sell", "date": "2025-01-02", "shares": 1, "price": 5},
                              {"id": 10, "ticker": "B", "kind": "buy", "date": "2025-01-02", "shares": 1, "price": 4}])["B"]
    assert same_day["lots"] == [] and same_day["sales"][0]["cost"] == 4
    # Lots with no date count as the oldest
    undated = ledger.replay([{"id": 1, "ticker": "C", "kind": "buy", "date": "2025-01-02", "shares": 1, "price": 9},
                             {"id": 2, "ticker": "C", "kind": "buy", "date": "", "shares": 1, "price": 7},
                             {"id": 3, "ticker": "C", "kind": "sell", "date": "2025-02-03", "shares": 1, "price": 8}])["C"]
    assert undated["sales"][0]["cost"] == 7 and undated["lots"][0]["cost"] == 9
    # You can't sell more than you held on that date
    for bad in ([rows[0], {**rows[2], "shares": 11}], [{**rows[0], "date": "2025-06-02"}, rows[2]]):
        try:
            ledger.replay(bad)
            raise AssertionError("should have been refused")
        except ledger.LedgerError as e:
            assert "A" in str(e)
    for bad in ({"ticker": "A", "kind": "gift", "shares": 1, "price": 1}, {"ticker": "A", "kind": "buy", "shares": None, "price": 1},
                {"ticker": "A", "kind": "buy", "shares": -1, "price": 1}, {"ticker": "A", "kind": "dividend", "amount": None}):
        try:
            ledger.replay([bad])
            raise AssertionError(f"should have been refused: {bad}")
        except ledger.LedgerError:
            pass
    assert ledger.replay([{"ticker": "A", "shares": 1, "price": 1}])["A"]["lots"][0]["shares"] == 1  # kind defaults to buy


def test_transaction_routes_keep_the_ledger_valid():
    import datetime as dt

    from fastapi.testclient import TestClient

    from backend import db, insights, ledger
    from backend.main import app
    _reset()
    post = lambda c, **b: c.post("/api/transactions", json=b)
    with TestClient(app, raise_server_exceptions=False) as c:
        buy = post(c, ticker="aapl", kind="buy", date="2024-06-03", shares=10, price=150, note="core")
        assert buy.status_code == 200
        assert post(c, ticker="AAPL", kind="sell", date="2024-01-02", shares=1, price=1).status_code == 400  # before the purchase
        assert post(c, ticker="AAPL", kind="sell", date="2025-01-02", shares=11, price=1).status_code == 400  # more than held
        assert post(c, ticker="AAPL", kind="sell", date="", shares=1, price=1).status_code == 400  # a sale needs a date
        future = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        assert post(c, ticker="AAPL", kind="sell", date=future, shares=1, price=1).status_code == 400
        assert post(c, ticker="AAPL", kind="sell", date="soon", shares=1, price=1).status_code == 400
        assert post(c, ticker="AAPL", kind="short", date="2025-01-02", shares=1, price=1).status_code == 400
        assert post(c, ticker="MSFT", kind="sell", date="2025-01-02", shares=1, price=1).status_code == 400  # never held
        assert post(c, ticker="MSFT", kind="dividend", date="2025-01-02", amount=5).status_code == 400
        assert post(c, ticker="AAPL", kind="dividend", date="2025-01-02", amount="").status_code == 400
        sale = post(c, ticker="AAPL", kind="sell", date="2025-01-02", shares=4, price=200)
        assert sale.status_code == 200
        assert post(c, ticker="AAPL", kind="dividend", date="2025-02-03", amount=6.5).status_code == 200

        rows = c.get("/api/transactions").json()
        assert [r["kind"] for r in rows] == ["dividend", "sell", "buy"] and rows[-1]["note"] == "core"  # newest first
        p = c.get("/api/portfolio").json()["positions"][0]
        assert p["shares"] == 6 and p["avgCost"] == 150 and p["lots"][0]["shares"] == 6

        # Deleting a buy its sale depends on is refused, in either route; deleting the sale first is fine
        buy_id = buy.json()["id"]
        assert c.delete(f"/api/transactions/{buy_id}").status_code == 400
        assert c.delete(f"/api/holdings/{buy_id}").status_code == 400
        assert c.delete(f"/api/transactions/{sale.json()['id']}").status_code == 200
        assert c.get("/api/portfolio").json()["positions"][0]["shares"] == 10
        assert c.delete(f"/api/holdings/{buy_id}").status_code == 200

        # The older form still adds a buy
        assert c.post("/api/holdings", json={"ticker": "SPY", "shares": 2, "cost": 400, "bought": "2025-01-02"}).status_code == 200
        assert db.q("SELECT kind, date, shares, price FROM transactions WHERE ticker = 'SPY'") == [{"kind": "buy", "date": "2025-01-02", "shares": 2.0, "price": 400.0}]

    # The digest treats only what's still open as a holding
    _reset()
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('GONE', 'buy', '2024-01-02', 1, 1), ('GONE', 'sell', '2024-06-03', 1, 2), ('KEPT', 'buy', '2024-01-02', 1, 1)")
    assert ledger.held_tickers() == ["KEPT"]


def test_old_holdings_become_transactions():
    import sqlite3
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    from backend import db
    from backend.main import app
    path = Path(tempfile.mkdtemp()) / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE holdings (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, shares REAL NOT NULL, cost REAL NOT NULL, bought TEXT DEFAULT '', note TEXT DEFAULT '')")
    old.execute("INSERT INTO holdings (ticker, shares, cost, bought, note) VALUES ('AAPL', 10, 150, '2024-06-03', 'core'), ('SPY', 5, 400, '', ''), ('MSFT', 1, 9, 'last spring', '')")
    old.commit()
    old.close()
    real = db.DB_PATH
    db.DB_PATH = path
    try:
        db.init()
        db.init()  # running it again must not copy anything twice
        rows = db.q("SELECT * FROM transactions ORDER BY id")
        assert [(r["ticker"], r["kind"], r["date"], r["shares"], r["price"], r["note"]) for r in rows] == [
            ("AAPL", "buy", "2024-06-03", 10.0, 150.0, "core"), ("SPY", "buy", "", 5.0, 400.0, ""), ("MSFT", "buy", "", 1.0, 9.0, "")]
        assert not db.q("SELECT 1 FROM sqlite_master WHERE name = 'holdings'")
    finally:
        db.DB_PATH = real

    # A backup made before the ledger still restores
    _reset()
    v1 = {"version": 1, "watchlist": [], "journal": [], "alerts": [], "settings": {},
          "holdings": [{"id": 7, "ticker": "AAPL", "shares": 10, "cost": 150, "bought": "2024-06-03", "note": "core"}]}
    with TestClient(app) as c:
        assert c.post("/api/backup/restore", json=v1).status_code == 200
        r = db.q("SELECT * FROM transactions")[0]
        assert (r["id"], r["kind"], r["price"], r["date"], r["note"]) == (7, "buy", 150.0, "2024-06-03", "core")
        # A backup whose sales don't add up is refused, and nothing changes
        bad = {"version": 2, "watchlist": [], "journal": [], "alerts": [], "settings": {},
               "transactions": [{"ticker": "X", "kind": "sell", "date": "2025-01-02", "shares": 1, "price": 1}]}
        assert c.post("/api/backup/restore", json=bad).status_code == 400
        assert db.q("SELECT * FROM transactions")[0]["id"] == 7


def test_realised_results_are_hand_calculated():
    """A EUR stock: bought at EUR/USD 1.00, part sold at 1.10, a dividend at 1.10, and 1.08 today."""
    from backend import data, db, portfolio
    _reset()
    rates = {"2024-06-03": 1.00, "2025-06-02": 1.10, "2025-07-01": 1.10, "2024-07-01": 1.0}
    real = data.fx_on
    data.fx_on = lambda currency, base, date: rates[date] if currency == "EUR" else real(currency, base, date)
    try:
        db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('EURO', 'buy', '2024-06-03', 10, 100), ('EURO', 'sell', '2025-06-02', 5, 110)")
        db.x("INSERT INTO transactions (ticker, kind, date, amount) VALUES ('EURO', 'dividend', '2025-07-01', 10)")
        db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('SOLD', 'buy', '2024-07-01', 2, 50), ('SOLD', 'sell', '2025-06-02', 2, 60)")
        prices = {"EURO": _steps([0.0] * 300) * 1.2, "SOLD": _steps([0.0] * 300), "SPY": _steps([0.0] * 300)}
        with _FakeMarket(prices, {"EURO": {"currency": "EUR"}}):
            p = portfolio.summary(db.get_settings())
    finally:
        data.fx_on = real
    pos = {r["ticker"]: r for r in p["positions"]}
    assert list(pos) == ["EURO"], "a fully sold position isn't a holding"
    assert abs(pos["EURO"]["value"] - 5 * 120 * 1.08) < 1e-6 and abs(pos["EURO"]["cost"] - 500) < 1e-6
    assert abs(p["pnl"] - 148) < 1e-6
    # 5 x (110 x 1.10 - 100 x 1.00) = 105 on EURO, plus 2 x (60 - 50) = 20 on SOLD
    assert abs(p["realised"] - 125) < 1e-6
    assert abs(p["dividends"] - 11) < 1e-6
    assert abs(p["totalGain"] - (148 + 125 + 11)) < 1e-6
    closed = {c["ticker"]: c for c in p["closed"]}
    assert list(closed) == ["SOLD"] and abs(closed["SOLD"]["realised"] - 20) < 1e-6
    assert abs(pos["EURO"]["realised"] - 105) < 1e-6 and abs(pos["EURO"]["dividends"] - 11) < 1e-6


def test_annual_return_is_hand_calculated():
    import datetime as dt

    from backend import db, ledger, portfolio
    day = dt.date(2025, 1, 1)
    # 1,000 in, 1,100 out exactly a year later
    r = ledger.xirr([(day, -1000.0), (day + dt.timedelta(days=365), 1100.0)])
    assert abs(r - 0.10) < 1e-6
    # A loss
    assert abs(ledger.xirr([(day, -1000.0), (day + dt.timedelta(days=365), 900.0)]) + 0.10) < 1e-6
    # 1,000 in, 500 back after a year, 605 after two: 10% a year
    two = ledger.xirr([(day, -1000.0), (day + dt.timedelta(days=365), 500.0), (day + dt.timedelta(days=730), 660.0)])
    assert abs(-1000 + 500 / (1 + two) + 660 / (1 + two) ** 2) < 1e-6
    # Too short to annualise, or no money in, means no answer
    assert ledger.xirr([(day, -1000.0), (day + dt.timedelta(days=10), 1100.0)]) is None
    assert ledger.xirr([(day, 1000.0), (day + dt.timedelta(days=365), 1100.0)]) is None
    assert ledger.xirr([]) is None

    _reset()
    today = dt.date.today()
    year_ago = (today - dt.timedelta(days=365)).isoformat()
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('AAA', 'buy', ?, 10, 100)", (year_ago,))
    prices = {"AAA": _steps([0.0] * 300, end=today.isoformat()) * 1.1, "SPY": _steps([0.0] * 300, end=today.isoformat())}
    with _FakeMarket(prices):
        assert abs(portfolio.summary(db.get_settings())["xirr"] - 0.10) < 1e-6
    # An undated lot has no start date, so no annual return can be worked out
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('AAA', 1, 100)")
    with _FakeMarket(prices):
        assert portfolio.summary(db.get_settings())["xirr"] is None
    # Nor while a holding is missing from the total
    with _FakeMarket({**prices, "AAA": None}):
        assert portfolio.summary(db.get_settings())["xirr"] is None


def test_performance_follows_the_actual_transactions():
    import datetime as dt

    import pandas as pd

    from backend import data, db, portfolio
    _reset()
    idx = pd.bdate_range(end="2026-06-01", periods=4)
    d = [x.strftime("%Y-%m-%d") for x in idx]
    a = pd.Series([100.0, 110.0, 110.0, 121.0], index=idx)
    b = pd.Series([50.0, 50.0, 100.0, 100.0], index=idx)
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('A', 'buy', ?, 10, 100), ('B', 'buy', ?, 20, 50)", (d[0], d[1]))
    with _FakeMarket({"A": a, "B": b, "SPY": a}):
        p = portfolio.summary(db.get_settings())
    # Day 2: only A held, +10%. Day 3: 1,100 of A and 1,000 of B become 3,100. Day 4: 3,210.
    expected = [100.0, 110.0, 110 * 3100 / 2100, 110 * 3100 / 2100 * 3210 / 3100]
    assert p["performance"]["dates"] == d
    assert all(abs(x - y) < 1e-6 for x, y in zip(p["performance"]["portfolio"], expected)), p["performance"]["portfolio"]
    assert abs(p["risk"]["return1y"] - (expected[-1] / 100 - 1)) < 1e-9
    assert p["performance"]["start"] == d[0]

    # A sale takes effect the next day, and the chart starts when something was first held
    _reset()
    e = pd.Series([100.0, 100.0, 100.0, 110.0, 121.0], index=pd.bdate_range(end="2026-06-01", periods=5))
    days = [x.strftime("%Y-%m-%d") for x in e.index]
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('E', 'buy', ?, 2, 100), ('E', 'sell', ?, 2, 110)", (days[1], days[3]))
    with _FakeMarket({"E": e, "SPY": e}):
        p = portfolio.summary(db.get_settings())
    assert p["performance"]["dates"] == days[1:]
    assert all(abs(x - y) < 1e-6 for x, y in zip(p["performance"]["portfolio"], [100.0, 100.0, 110.0, 110.0]))

    # Bought today: nothing to chart yet, and no error
    _reset()
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('N', 'buy', ?, 1, 100)", (dt.date.today().isoformat(),))
    with _FakeMarket({"N": _steps([0.0] * 30, end=dt.date.today().isoformat()), "SPY": _steps([0.0] * 30, end=dt.date.today().isoformat())}):
        p = portfolio.summary(db.get_settings())
    assert p["performance"] is None and p["risk"].get("return1y") is None and p["total"] > 0

    # A flat EUR stock in a USD portfolio gains as the euro rises, on each day's rate
    _reset()
    idx = pd.bdate_range(end="2026-06-01", periods=3)
    flat = pd.Series([100.0] * 3, index=idx)
    rates = pd.Series([1.00, 1.05, 1.10], index=idx)
    real = data.fx_series
    data.fx_series = lambda currency, base, index: rates.reindex(index) if currency == "EUR" else real(currency, base, index)
    try:
        db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('EURO', 'buy', ?, 1, 100)", (idx[0].strftime("%Y-%m-%d"),))
        with _FakeMarket({"EURO": flat, "SPY": flat}, {"EURO": {"currency": "EUR"}}):
            p = portfolio.summary(db.get_settings())
    finally:
        data.fx_series = real
    assert all(abs(x - y) < 1e-6 for x, y in zip(p["performance"]["portfolio"], [100.0, 105.0, 110.0]))


def test_fx_series_reads_the_history():
    import pandas as pd

    from backend import data, db
    idx = pd.bdate_range("2025-01-06", periods=4)
    eur = pd.Series([1.0, 1.1], index=[idx[0], idx[2]])
    gbp = pd.Series([1.3, 1.3], index=[idx[0], idx[3]])
    real_demo = data.DEMO
    data.DEMO = False
    db.cache_clear()
    try:
        db.cache_set("fx:QARUSD", 0.2747)
        with _FakeMarket({"EURUSD=X": eur, "GBPUSD=X": gbp}):
            assert data.fx_series("EUR", "USD", idx).tolist() == [1.0, 1.0, 1.1, 1.1]  # gaps carry the last rate
            assert [round(x, 6) for x in data.fx_series("GBp", "USD", idx)] == [0.013] * 4
            assert data.fx_series("USD", "USD", idx).tolist() == [1.0] * 4
            assert data.fx_series("QAR", "USD", idx).tolist() == [0.2747] * 4  # a peg needs no history
            assert [round(x, 6) for x in data.fx_series("GBP", "EUR", idx)] == [1.3, 1.3, round(1.3 / 1.1, 6), round(1.3 / 1.1, 6)]
            assert data.fx_series("HKD", "USD", idx) is None
        # Dates older than five years get their own rates: with five years only, every older date took the oldest
        # rate in them (a 2019 price converted at a 2021 rate)
        today = pd.Timestamp.today().normalize()
        long = pd.bdate_range(end=today, periods=9 * 261)
        rising = pd.Series([0.8 + i * 0.0001 for i in range(len(long))], index=long)
        with _FakeMarket({"EURUSD=X": rising}):
            old = long[:3]
            assert data.fx_series("EUR", "USD", old).round(6).tolist() == rising[:3].round(6).tolist()
            # a date a few days before the first rate takes it; one well before has none, rather than a wrong one
            early = pd.DatetimeIndex([long[0] - pd.Timedelta(days=3), long[0] - pd.Timedelta(days=40)])
            got = data.fx_series("EUR", "USD", early)
            assert round(got.iloc[0], 6) == round(rising.iloc[0], 6) and pd.isna(got.iloc[1]), got.tolist()
    finally:
        data.DEMO = real_demo
        db.cache_clear()


def test_delete_a_whole_holding():
    from backend import db
    _reset()
    with TestClient(app) as c:
        text = "AAPL,10,100,2024-06-03\nAAPL,5,110,2024-09-02\nAAPL,4,150,2025-01-02,sell\nAAPL,,10,2025-02-03,dividend\nMSFT,2,300,2024-06-03"
        assert c.post("/api/transactions/import", json={"text": text}).status_code == 200
        r = c.delete("/api/transactions/ticker/aapl")  # any case, like everywhere else
        assert r.status_code == 200 and r.json()["deleted"] == 4, r.text
        assert [x["ticker"] for x in db.q("SELECT ticker FROM transactions")] == ["MSFT"]
        assert [x["ticker"] for x in c.get("/api/portfolio").json()["positions"]] == ["MSFT"]
        assert c.delete("/api/transactions/ticker/AAPL").status_code == 404, "nothing left to delete says so"
        assert c.delete("/api/transactions/ticker/MSFT", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    _reset()


def test_ledger_refuses_dates_that_would_break_it():
    from fastapi.testclient import TestClient

    from backend import ledger
    from backend.main import app
    for bad in ({"ticker": "A", "kind": "buy", "date": "soon", "shares": 1, "price": 1},
                {"ticker": "A", "kind": "buy", "date": "2024-13-45", "shares": 1, "price": 1}):
        try:
            ledger.replay([bad])
            raise AssertionError(f"should be refused: {bad}")
        except ledger.LedgerError as e:
            assert "date" in str(e)
    buy = {"ticker": "A", "kind": "buy", "date": "", "shares": 1, "price": 1}
    for bad in ({"ticker": "A", "kind": "sell", "date": "", "shares": 1, "price": 1}, {"ticker": "A", "kind": "dividend", "date": "", "amount": 1}):
        try:
            ledger.replay([buy, bad])
            raise AssertionError(f"should be refused: {bad}")
        except ledger.LedgerError as e:
            assert "date" in str(e)
    _reset()
    backup = {"version": 2, "watchlist": [], "journal": [], "alerts": [], "settings": {},
              "transactions": [{"ticker": "AAPL", "kind": "buy", "date": "soon", "shares": 1, "price": 1}]}
    with TestClient(app) as c:
        assert c.post("/api/backup/restore", json=backup).status_code == 400
        assert c.get("/api/portfolio").status_code == 200


def test_realised_figures_say_when_they_are_not_complete():
    from backend import data, db, portfolio
    _reset()
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('OK', 'buy', '2024-06-03', 2, 50), ('OK', 'sell', '2025-06-02', 2, 60)")
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('ODD', 'buy', '2024-06-03', 1, 50), ('ODD', 'sell', '2025-06-02', 1, 60)")
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('HELD', 'buy', '2024-06-03', 1, 50)")
    flat = _steps([0.0] * 300)
    real = data.fx_rate

    def rate(currency, base="USD"):
        if currency == "XXX":
            raise data.DataError("No exchange rate available for XXX to USD.")
        return real(currency, base)
    data.fx_rate = rate
    try:
        with _FakeMarket({"OK": flat, "ODD": flat, "HELD": flat, "SPY": flat}, {"ODD": {"currency": "XXX"}}):
            p = portfolio.summary(db.get_settings())
    finally:
        data.fx_rate = real
    # One closed position without a rate must not wipe out the others, and has to be mentioned
    assert abs(p["realised"] - 20) < 1e-6
    assert any("ODD" in w and "realised" in w.lower() for w in p["warnings"]), p["warnings"]
    assert p["xirr"] is None and p["incomplete"] is False

    # A sale from an undated lot in another currency uses today's rate for its cost, and says so
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('EURO', 2, 50)")
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('EURO', 'sell', '2025-06-02', 2, 60)")
    with _FakeMarket({"EURO": flat, "SPY": flat}, {"EURO": {"currency": "EUR"}}):
        p = portfolio.summary(db.get_settings())
    assert any("EURO" in w and "realised" in w.lower() and "exchange rate" in w for w in p["warnings"]), p["warnings"]


def test_old_data_is_flagged():
    import pandas as pd

    from fastapi.testclient import TestClient

    from backend import data, db, insights, portfolio
    from backend.main import app
    _reset()
    db.cache_clear()
    idx = pd.bdate_range(end="2026-06-01", periods=300)
    old = pd.DataFrame({c: [100.0] * 300 for c in ("Open", "High", "Low", "Close", "Volume")}, index=idx)
    db.cache_set("hist:OLD:5y", data._df_to(old))
    db.cache_expire("hist:OLD:5y")

    class Down:
        def history(self, **kw):
            raise RuntimeError("rate limited")
        info = {"quoteType": "EQUITY", "currency": "USD", "longName": "Old"}
        income_stmt = cashflow = None
        news = []

    class YF:
        Ticker = staticmethod(lambda t: Down())

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    try:
        assert len(data.history("OLD", "5y")) == 300 and data.is_stale("OLD")
        with TestClient(app) as c:
            s = c.get("/api/stock/OLD").json()["stats"]
        assert s["stale"] is True and s["asOf"] == "2026-06-01"
        db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('OLD', 1, 100)")
        p = portfolio.summary(db.get_settings())
        assert any("OLD" in w and "old" in w.lower() for w in p["warnings"]), p["warnings"]
        assert p["positions"][0]["stale"] is True
        # An alert can't fire on a price that may no longer be true
        db.x("INSERT INTO alerts (ticker, kind, value, created) VALUES ('OLD', 'price_above', 1, 1)")
        assert insights.check_alerts(db.get_settings()) == []
        # An old FX rate is flagged too
        db.cache_set("fx:EURUSD", 1.1)
        db.cache_expire("fx:EURUSD")
        assert data.fx_rate("EUR", "USD") == 1.1 and data.is_stale_fx("EUR")
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        data._stale.clear()
        data._stale_fx.clear()
        db.cache_clear()
    # Fresh data clears the flag
    data._stale.add("AAPL")
    data.history("AAPL", "1y")
    assert not data.is_stale("AAPL")


def test_moves_name_the_day_they_happened():
    import datetime as dt

    import pandas as pd

    from backend import db, insights
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('PAST', 1, 100), ('NOW', 1, 100)")
    today = dt.date.today().isoformat()
    past = _steps([0.0] * 300 + [0.05], end="2026-06-01")
    now = _steps([0.0] * 300 + [0.05], end=today)
    now.index += pd.Timestamp(today) - now.index[-1]  # business days end on Friday at weekends; this must end today
    with _FakeMarket({"PAST": past, "NOW": now}):
        texts = [i["text"] for i in insights.digest(db.get_settings())["items"]]
        db.x("INSERT INTO alerts (ticker, kind, value, created) VALUES ('PAST', 'day_move', 1, 1)")
        fired = insights.check_alerts(db.get_settings())
    assert any(t.startswith("NOW (holding) moved +5.0% today") for t in texts), texts
    assert any(t.startswith("PAST (holding) moved +5.0% on 2026-06-01") for t in texts), texts
    assert "on 2026-06-01" in fired[0]["message"]


def test_empty_statements_are_retried_soon():
    import time

    from backend import data, db

    class Listing:
        income_stmt = cashflow = None

    class YF:
        Ticker = staticmethod(lambda t: Listing())

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    db.cache_clear()
    try:
        assert data.financials("X") == []
        ts = db.q("SELECT ts FROM cache WHERE key = 'fin:X'")[0]["ts"]
        age_left = data.TTL["fin"] - (time.time() - ts)
        assert 3000 < age_left <= 3600, age_left  # kept for an hour, not a day
        # Good figures from before aren't replaced by an empty reply
        db.cache_set("fin:Y", [{"year": "2025", "revenue": 1.0}])
        db.cache_expire("fin:Y")
        assert data.financials("Y") == [{"year": "2025", "revenue": 1.0}]
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        db.cache_clear()


def test_clearing_the_cache_keeps_the_fallback():
    from fastapi.testclient import TestClient

    from backend import db
    from backend.main import app
    db.cache_clear()
    db.cache_set("hist:KEEP:5y", {"x": 1})
    with TestClient(app) as c:
        assert c.post("/api/cache/clear").status_code == 200
    assert db.cache_get("hist:KEEP:5y", 10 ** 6) is None, "next load fetches fresh data"
    assert db.cache_get_stale("hist:KEEP:5y") == {"x": 1}, "and old data is there if Yahoo fails"
    db.cache_clear()


def test_cache_in_memory_and_failures_kept_briefly():
    import tempfile
    import time
    from pathlib import Path

    import pandas as pd

    from backend import data, db
    db.cache_clear()
    # The copy in memory: each read is its own copy, ages are the database's, and expiring or clearing reaches it
    db.cache_set("k", {"a": [1, 2]})
    first = db.cache_get("k", 60)
    first["a"].append(3)
    assert db.cache_get("k", 60) == {"a": [1, 2]}, "one caller can't change what another reads"
    assert db.q("SELECT value FROM cache WHERE key = 'k'")[0]["value"] == '{"a": [1, 2]}'
    db.cache_set("old", 1, ts=time.time() - 100)
    assert db.cache_get("old", 50) is None and db.cache_get("old", 200) == 1 and db.cache_get_stale("old") == 1
    db.cache_expire("k")
    assert db.cache_get("k", 60) is None and db.cache_get_stale("k") == {"a": [1, 2]}
    hist = data.history("AAPL", "1y")
    assert data.history("AAPL", "1y").equals(hist), "a history read twice is the same, though the first copy's index was taken"
    # Pruning drops only what hasn't been refreshed for 90 days; Clear cache keeps the age, so an expired fresh entry stays
    db.cache_set("recent", 1)
    db.cache_set("dead", 1, ts=time.time() - 91 * 86400)
    db.cache_set("old-but-expired", 1, ts=time.time() - 91 * 86400)
    db.cache_expire()
    assert db.cache_get("recent", 10 ** 6) is None and db.cache_get_stale("recent") == 1, "expired, with the fallback kept"
    db.cache_expire()  # twice is the same as once
    assert db.cache_prune() == 2
    assert db.cache_get_stale("recent") == 1 and db.cache_get_stale("dead") is None and db.cache_get_stale("old-but-expired") is None
    assert db.cache_prune() == 0
    db.cache_clear()
    assert db.cache_get_stale("k") is None
    # A different database file doesn't see this one's entries
    db.cache_set("only-here", 1)
    real_path = db.DB_PATH
    db.DB_PATH = Path(tempfile.mkdtemp()) / "other.db"
    try:
        db.init()
        assert db.cache_get_stale("only-here") is None
    finally:
        db.DB_PATH = real_path

    def hours_left(key, ttl):
        return (ttl - (time.time() - db.q("SELECT ts FROM cache WHERE key = ?", (key,))[0]["ts"])) / 3600

    class Funds:
        sector_weightings, top_holdings = {}, None

    state = {"fail": False}

    class Listing:
        funds_data = Funds()

        @property
        def earnings_history(self):
            if state["fail"]:
                raise RuntimeError("rate limited")
            return pd.DataFrame({"epsActual": [1.0], "epsEstimate": [0.9], "surprisePercent": [0.11]}, index=[pd.Timestamp("2026-03-31")])

    class NothingFound:
        def __init__(self, q, **kw):
            self.quotes = []

    class YF:
        Ticker = staticmethod(lambda t: Listing())

    YF.Search = NothingFound

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    try:
        # An empty answer is kept for an hour, not a day or a week, since a rate limit looks the same
        assert data.search_symbols("vwce") == [] and 0.8 < hours_left("search:vwce", 24 * 3600) <= 1
        assert data.fund("GLD") == {"sectors": {}, "holdings": []} and 0.8 < hours_left("fund:GLD", 7 * 24 * 3600) <= 1
        # ...and doesn't replace what was known
        good = {"sectors": {"Technology": 0.3}, "holdings": [{"ticker": "AAPL", "name": "Apple", "weight": 0.07}]}
        db.cache_set("fund:SPY", good, ts=1)
        assert data.fund("SPY") == good
        # A failure keeps the last earnings instead of blanking them
        rec = data.earnings_record("AAPL")
        assert rec and rec[0]["actual"] == 1.0
        db.cache_expire("earn:AAPL")
        state["fail"] = True
        assert data.earnings_record("AAPL") == rec
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        db.cache_clear()


def test_rsi_waits_for_enough_data():
    import pandas as pd

    from backend import indicators as ind
    s = pd.Series([100.0, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 87, 88], index=pd.bdate_range("2024-01-01", periods=17))
    r = ind.rsi(s)
    assert r.iloc[:14].isna().all() and r.iloc[14:].notna().all()
    assert r.iloc[14] == 0.0  # 14 straight falls
    # A price that doesn't move is neutral, not overbought; only rises is 100
    flat = ind.rsi(pd.Series([10.0] * 30))
    assert flat.iloc[14:].eq(50.0).all() and flat.iloc[:14].isna().all()
    assert ind.rsi(pd.Series([10.0] * 20 + [10.0 + i for i in range(1, 16)])).iloc[-1] == 100.0
    # And a stock stuck at one price doesn't trigger an "overbought" signal
    from backend import scoring
    texts = " ".join(x["text"] for x in scoring.signals({"assetClass": "Stocks"}, pd.Series([25.0] * 300, index=pd.bdate_range("2024-01-01", periods=300))))
    assert "overbought" not in texts.lower(), texts


def test_market_indicators_are_not_scored_like_investments():
    from backend import scoring
    close = _steps([0.001] * 300)
    vix = scoring.evaluate({"assetClass": "Other"}, close)
    assert vix["composite"] is None and vix["stance"] == "Not scored" and vix["factors"]["momentum"] is not None
    assert scoring.evaluate({"assetClass": "ETFs"}, close)["composite"] is not None


def test_published_52_week_range_is_used():
    from fastapi.testclient import TestClient

    from backend import data
    from backend.main import app
    real = data.info
    data.info = lambda t: {**real(t), "fiftyTwoWeekHigh": 123.0, "fiftyTwoWeekLow": 45.0}
    try:
        with TestClient(app) as c:
            s = c.get("/api/stock/AAPL").json()["stats"]
    finally:
        data.info = real
    assert s["high52"] == 123.0 and s["low52"] == 45.0
    with TestClient(app) as c:
        calc = c.get("/api/stock/AAPL").json()["stats"]
    assert calc["high52"] >= calc["low52"] > 0


def test_idle_cash_earns_the_risk_free_rate_in_backtests():
    import pandas as pd

    from backend import backtest
    # Falling every day, so momentum stays in cash throughout: 4% a year on 79 days
    idx = pd.bdate_range("2024-01-01", periods=80)
    df = pd.DataFrame({"Close": [100 * 0.999 ** i for i in range(80)]}, index=idx)
    r = backtest.run(df, "momentum", {"lookback": 1}, 0, 0.04)
    assert r["strategy"]["trades"] == 0
    assert abs(r["strategy"]["totalReturn"] - ((1 + 0.04 / 252) ** 79 - 1)) < 1e-9
    assert r["strategy"]["sharpe"] is not None and any("risk-free" in c for c in r["caveats"])
    # DCA: money waiting to be invested earns it too
    flat = pd.DataFrame({"Close": [100.0] * 80}, index=idx)
    no_interest = backtest.run(flat, "dca", {}, 0, 0.0)["strategy"]["totalReturn"]
    with_interest = backtest.run(flat, "dca", {}, 0, 0.04)["strategy"]["totalReturn"]
    assert abs(no_interest) < 1e-9 and with_interest > 0
    # Buy & hold pays its one trade
    held = backtest.run(flat, "buy_hold", {}, 10, 0.0)
    assert abs(held["benchmark"]["totalReturn"] + 0.001) < 1e-9 and abs(held["strategy"]["totalReturn"] + 0.001) < 1e-9


def test_digest_puts_urgent_items_first():
    from fastapi.testclient import TestClient

    from backend import db, insights
    from backend.main import app
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('AAA', 1, 100)")
    rising = _steps([0.01] * 300)  # overbought, and +1% is below the move threshold
    db.x("INSERT INTO journal (ts, ticker, action, price, stop_price, review_date) VALUES (1, 'AAA', 'buy', 100, 1000000, '2000-01-01')")
    db.x("INSERT INTO alerts (ticker, kind, value, created) VALUES ('AAA', 'price_above', 1, 1)")
    with _FakeMarket({"AAA": rising}):
        d = insights.digest(db.get_settings())
    texts = [i["text"] for i in d["items"]]
    assert [t.split()[0] for t in texts[:2]] == ["Alert:", "AAA"] and "hit your thesis stop" in texts[1]
    own = [i["text"] for i in d["items"] if i.get("kind") != "news"]  # headlines come after everything else
    assert "review due" in texts[2] and "overbought" in own[-1], texts
    assert "rows" not in d


def test_digest_covers_portfolio_markets_and_headlines():
    import pandas as pd

    from backend import data, db, insights
    _reset()
    flat = _steps([0.0] * 300)
    month_drop = _steps([0.0] * 279 + [-0.005] * 20 + [-0.003])  # -9.8% over the month (compounded), -0.3% on the day
    vix = _steps([0.0] * 279 + [0.012] * 21)  # +28% over the month: ordinary for the VIX, which needs 30%
    tnx = pd.Series([4.0] * 280 + [4.0 + 0.5 * i / 21 for i in range(1, 22)], index=flat.index)  # +0.5 points in a month
    btc = _steps([0.0] * 299 + [0.04])  # +4% in a day: ordinary for bitcoin, which needs 5%
    tiles = {"^IXIC": "Nasdaq", "^VIX": "VIX", "^TNX": "US 10Y yield", "BTC-USD": "Bitcoin", "^GSPC": "S&P 500"}
    moves = insights.market_moves({}) or []
    assert moves == []
    with _FakeMarket({"^IXIC": month_drop, "^VIX": vix, "^TNX": tnx, "BTC-USD": btc, "^GSPC": flat}):
        moves = {m["ticker"]: m["text"] for m in insights.market_moves(tiles)}
    assert set(moves) == {"^IXIC", "^TNX"}, moves
    assert moves["^IXIC"].startswith("Nasdaq fell 9.8% over the past month") and "over the past month" in moves["^IXIC"] and "on " not in moves["^IXIC"], moves
    from backend import indicators as ind
    month_ago = tnx.iloc[-1] / (1 + ind.ret_over(tnx, 21))  # the dashboard tile's own starting value
    assert moves["^TNX"] == f"US 10Y yield rose {tnx.iloc[-1] - month_ago:.2f} percentage points over the past month.", moves
    assert tnx.iloc[-1] - month_ago >= 0.40
    # The colour says good or bad: a rising VIX is red, a moving yield neutral, a falling index red
    with _FakeMarket({"^VIX": _steps([0.0] * 279 + [0.02] * 21), "^TNX": tnx, "^IXIC": month_drop}):
        tones = {m["ticker"]: m["tone"] for m in insights.market_moves({"^VIX": "VIX", "^TNX": "US 10Y yield", "^IXIC": "Nasdaq"})}
    assert tones == {"^VIX": -1, "^TNX": 0, "^IXIC": -1}, tones

    # Your portfolio: the latest day in money and percent, and the core share against the target
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('VWCE.DE', 'buy', '2026-01-05', 100, 100)")
    real_news = data.news
    now = pd.Timestamp.now(tz="UTC")
    news = {"AAPL": [{"title": "Apple unveils a new chip", "url": "https://example.com/a", "publisher": "Wire", "time": (now - pd.Timedelta(hours=5)).isoformat()},
                     {"title": "Apple sales slip", "url": "javascript:alert(1)", "publisher": "Wire", "time": (now - pd.Timedelta(hours=9)).isoformat()},
                     {"title": "Apple's old story", "url": "https://example.com/old", "time": (now - pd.Timedelta(days=5)).isoformat()},
                     {"title": "Markets wrap: stocks rise", "url": "https://example.com/m", "time": now.isoformat()}],
            "VWCE.DE": [{"title": "Vanguard cuts fees", "url": "https://example.com/v", "time": now.isoformat()}]}
    data.news = lambda t, limit=12: news.get(t, [])
    db.x("INSERT INTO watchlist (ticker, added) VALUES ('AAPL', 1)")
    try:
        with _FakeMarket({"VWCE.DE": month_drop, "AAPL": flat, "^IXIC": month_drop}, {"VWCE.DE": {"assetClass": "ETFs", "currency": "EUR", "name": "Vanguard FTSE All-World"}, "AAPL": {"name": "Apple Inc."}}):
            db.set_settings({"base_currency": "EUR"})
            items = insights.digest(db.get_settings(), {"^IXIC": "Nasdaq"})["items"]
    finally:
        data.news = real_news
        _reset()
    texts = [i["text"] for i in items]
    day = [t for t in texts if t.startswith("Your portfolio lost")]
    assert day and "(-0.30%)" in day[0] and "It's worth €" in day[0], texts
    # All core is the lower-risk side: no daily line nudging money into single stocks
    assert not any("satellite" in t.lower() for t in texts), texts
    assert any(t.startswith("Nasdaq fell") for t in texts), texts
    heads = [i for i in items if i.get("kind") == "news"]
    # Only the stock, only what names it, only the past three days, newest first; a link that isn't http(s) is dropped
    assert [h["text"] for h in heads] == ["AAPL: Apple unveils a new chip (Wire)", "AAPL: Apple sales slip (Wire)"], heads
    assert heads[0]["url"] == "https://example.com/a" and heads[1]["url"] is None
    order = [i["priority"] for i in items]
    assert order == sorted(order), "most urgent first, headlines last"
    # A broad world fund held alone isn't a concentrated single position, nor short of diversification
    assert not any("concentration" in t or "diversification is limited" in t for t in texts), texts


def test_currency_converter_and_look_through():
    from backend import data
    from backend.universe import FUND_CURRENCIES
    assert all(abs(sum(w.values()) - 1) < 1e-9 for w in FUND_CURRENCIES.values()), "each fund's currencies add up to all of it"
    _reset()
    with TestClient(app) as c:
        r = c.get("/api/fx?src=eur&dst=usd").json()
        assert (r["from"], r["to"]) == ("EUR", "USD") and abs(r["rate"] - data.fx_rate("EUR", "USD")) < 1e-12
        back = c.get("/api/fx?src=USD&dst=EUR").json()["rate"]
        assert abs(r["rate"] * back - 1) < 1e-9, "the two directions agree"
        assert c.get("/api/fx?src=EUR&dst=XYZ").status_code == 400
        # A world fund counts in the currencies of what it holds; a stock in its own (pence as pounds)
        c.post("/api/settings", json={"base_currency": "EUR"})
        for t, shares in (("VWCE.DE", 100), ("AZN.L", 10)):
            assert c.post("/api/transactions", json={"ticker": t, "kind": "buy", "date": "2025-01-06", "shares": shares, "price": 100}).status_code == 200
        p = c.get("/api/portfolio").json()
    a, vwce = p["allocation"], next(x for x in p["positions"] if x["ticker"] == "VWCE.DE")
    look = a["currencyLookThrough"]
    assert a["currencyOpened"] == ["VWCE.DE"] and set(a["currency"]) == {"EUR", "GBp"}, a["currency"]
    assert abs(sum(look.values()) - p["total"]) < 1e-6 and "GBp" not in look
    assert abs(look["USD"] - vwce["value"] * FUND_CURRENCIES["VWCE.DE"]["USD"]) < 1e-6, look
    azn = next(x for x in p["positions"] if x["ticker"] == "AZN.L")
    assert abs(look["GBP"] - (azn["value"] + vwce["value"] * FUND_CURRENCIES["VWCE.DE"]["GBP"])) < 1e-6
    _reset()


def test_opportunities_follow_their_rules():
    from backend import db, insights
    _reset()
    with TestClient(app) as c:
        empty = c.get("/api/opportunities?markets=Europe").json()
        assert empty["markets"] == ["Europe"] and empty["fit"] == [] and empty["holdings"] == []
        assert c.post("/api/transactions", json={"ticker": "SAP.DE", "kind": "buy", "date": "2025-01-06", "shares": 10, "price": 100}).status_code == 200
        o = c.get("/api/opportunities?markets=US,Europe,Gulf,Crypto,Nonsense").json()
    R = o["rules"]
    # stocks only: funds and crypto can't be compared on value; the old "US" and "Gulf" are read as their regions
    assert o["markets"] == ["North America", "Europe", "Middle East & Africa"], o["markets"]
    assert o["map"] and all(r["f_value"] is not None for r in o["map"]) and len(o["map"]) == o["scanned"] - len(o["failed"])
    # Top ranked: the highest overall scores, best first
    scores = [r["composite"] for r in o["top"]]
    assert len(o["top"]) == R["top"] and scores == sorted(scores, reverse=True) and scores[0] == max(r["composite"] for r in o["map"])
    # No more than the cap from one sector in any ranked list
    from collections import Counter
    for name in ("top", "fit", "onSale"):
        assert max(Counter(r["sector"] for r in o[name]).values(), default=0) <= R["sectorCap"], (name, Counter(r["sector"] for r in o[name]))
    # Quality on sale: every one meets the limits and its fall has paused (back above its 50-day average); best quality first
    assert all(r["f_quality"] >= R["saleQuality"] and r["fromHigh"] <= R["saleDrop"] for r in o["onSale"])
    falling = {r["ticker"] for r in o["map"] if not r.get("above50", True)}
    assert not falling & {r["ticker"] for r in o["onSale"]}
    assert [r["f_quality"] for r in o["onSale"]] == sorted((r["f_quality"] for r in o["onSale"]), reverse=True)
    # Fits your portfolio: never what you hold; fit is the average of the score and how much it diversifies
    assert o["holdings"] == ["SAP.DE"] and o["fit"] and all(r["ticker"] != "SAP.DE" for r in o["fit"])
    assert all(abs(r["fit"] - (r["composite"] + (1 - max(r["corr"], 0)) * 100) / 2) < 1e-9 for r in o["fit"]) and o["fitWeeks"] > 26
    # Every reason names its figure, and only factors at the limit are named
    named = [r for r in o["map"] if r["reasons"]]
    assert named and all(any(ch.isdigit() for ch in reason) for r in named for reason in r["reasons"] if reason not in ("Cheap", "High quality", "Growing", "Strong trend", "Calm"))
    assert all(len(r["reasons"]) <= sum((r[f"f_{k}"] or 0) >= R["reasonAt"] for k in ("value", "quality", "growth", "momentum", "risk")) for r in o["map"])
    _reset()


def test_a_sentence_is_not_a_symbol():
    from backend import data
    asked = []

    class YF:
        @staticmethod
        def Ticker(t):
            asked.append(t)
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    try:
        with TestClient(app) as c:
            r = c.get("/api/stock/GULF DIVIDEND PAYERS YIELDING OVER 4%")
            assert r.status_code == 404 and "isn't a ticker symbol" in r.json()["detail"] and not asked, (r.text, asked)
            # Yahoo's own failure comes back as a plain sentence, not its parser's error
            r = c.get("/api/stock/NOPE.XX")
            assert r.status_code == 404 and r.json()["detail"] == "Yahoo didn't return prices for NOPE.XX. Check the symbol, or try again in a minute.", r.text
    finally:
        data._yf, data.DEMO = real_yf, real_demo


def test_screening_banks():
    from backend import ai, db, insights
    _reset()
    # "Banks" is the bank industry; "financials" the whole sector, insurers included
    assert ai._parse_rules("european banks with low debt") == {"markets": ["Europe"], "sector": "Financial Services", "industry": "Banks", "max_de": 0.5}
    assert "industry" not in ai._parse_rules("european financials") and "ignored" not in ai._parse_rules("banking stocks")
    # A sector or banks ruled out; "no debt" still means low debt; "show us" is the pronoun, "US tech stocks" the market
    assert ai._parse_rules("safe dividend stocks, not tech") == {"exclude_sector": "Technology", "min_div_yield": 0.025, "sort": "divYield", "max_vol": 0.25}
    assert ai._parse_rules("european stocks excluding banks") == {"exclude_industry": "Banks", "markets": ["Europe"]}
    assert ai._parse_rules("cheap stocks with no debt")["max_de"] == 0.5 and "markets" not in ai._parse_rules("show us cheap stocks")
    assert ai._parse_rules("us tech stocks with strong momentum") == {"countries": ["United States"], "sector": "Technology", "min_momentum": 60, "sort": "momentum"}
    assert ai._parse_rules("us banks")["countries"] == ["United States"] and "countries" not in ai._parse_rules("show us banks")
    assert ai._parse_rules("revenue growth above 20%") == {"min_rev_growth": 0.2, "sort": "growth"}
    # A bank's free cash flow and debt aren't given to the model as if they meant something
    bank = ai.brief_data({"ticker": "B", "info": {"name": "B", "industry": "Banks - Regional", "debtToEquity": 3.0, "freeCashflow": -5e9, "returnOnEquity": 0.15},
                          "stats": {}, "scores": {"factors": {}, "signals": []}, "financials": [{"year": "2025", "revenue": 1e9, "netIncome": 2e8, "fcf": -3e9}], "news": []})
    assert "Note on banks" in bank and "Free cash flow" not in str(bank["Annual accounts, oldest first"]) and "Debt to equity" not in str(bank), bank
    flat = _steps([0.001] * 300)
    infos = {"BANK.X": {"sector": "Financial Services", "industry": "Banks - Regional", "debtToEquity": None, "trailingPE": 9.0},
             "INSU.X": {"sector": "Financial Services", "industry": "Insurance - Diversified", "debtToEquity": 0.3, "trailingPE": 12.0},
             "BIGD.X": {"sector": "Financial Services", "industry": "Insurance - Life", "debtToEquity": 1.2, "trailingPE": 10.0},
             "TECH.X": {"sector": "Technology", "industry": "Software", "debtToEquity": 0.1, "trailingPE": 20.0}}
    with _FakeMarket({t: flat for t in infos}, infos):
        screen = lambda q: insights.screen({"filters": ai._parse_rules(q), "extra": list(infos), "markets": []}, db.get_settings())
        banks = screen("banks with low debt")
        fin = screen("financials with low debt")
    # A bank has no meaningful debt/equity, so the limit is set aside for it, and the screen says so
    assert [r["ticker"] for r in banks["rows"]] == ["BANK.X"] and "wasn't applied to 1 bank:" in banks["notes"][0], banks["notes"]
    assert sorted(r["ticker"] for r in fin["rows"]) == ["BANK.X", "INSU.X"], [r["ticker"] for r in fin["rows"]]
    _reset()


def test_quote_for_the_portfolio_form():
    from fastapi.testclient import TestClient

    from backend.main import app
    with TestClient(app) as c:
        q = c.get("/api/quote/azn.l").json()
        assert q["ticker"] == "AZN.L" and q["currency"] == "GBp" and q["price"] > 0
        with _FakeMarket({}):
            assert c.get("/api/quote/NOPE").status_code == 404


def test_news_has_no_keyword_sentiment():
    from backend import ai, data
    item = data.news("AAPL")[0]
    assert "sentiment" not in item and item["title"]
    assert not hasattr(data, "headline_sentiment")
    text = ai.rule_brief({"ticker": "A", "info": {"name": "A"}, "stats": {}, "news": [item], "scores": {"factors": {}, "composite": 1, "stance": "x", "signals": []}})
    assert "headlines" not in text.lower()


def test_brief_uses_your_risk_free_rate():
    from fastapi.testclient import TestClient

    from backend import ai
    from backend.main import app
    _reset()
    seen = {}
    real = ai.brief
    ai.brief = lambda ctx: seen.update(ctx) or {"source": "rules", "text": "x"}
    try:
        with TestClient(app) as c:
            c.post("/api/stock/AAPL/brief")
            low = seen["stats"]["sharpe1y"]
            c.post("/api/settings", json={"risk_free_rate": 0.20})
            c.post("/api/stock/AAPL/brief")
            assert seen["stats"]["sharpe1y"] < low
    finally:
        ai.brief = real


def test_paste_several_transactions():
    from fastapi.testclient import TestClient

    from backend import db
    from backend.main import app
    _reset()
    send = lambda c, text: c.post("/api/transactions/import", json={"text": text})
    with TestClient(app) as c:
        good = """ticker,shares,price,date,type,note
aapl,10,150,2024-06-03
AAPL,4,200,2025-01-02,sell,trim
AAPL,,6.5,2025-02-03,dividend

MSFT,2,"1,000.50",2024-06-03,buy,"note, with comma"
"""
        r = send(c, good)
        assert r.status_code == 200 and r.json()["added"] == 4, r.text
        rows = db.q("SELECT ticker, kind, date, shares, price, amount, note FROM transactions ORDER BY id")
        assert rows[0] == {"ticker": "AAPL", "kind": "buy", "date": "2024-06-03", "shares": 10.0, "price": 150.0, "amount": None, "note": ""}
        assert rows[1]["kind"] == "sell" and rows[1]["note"] == "trim"
        assert rows[2]["kind"] == "dividend" and rows[2]["amount"] == 6.5 and rows[2]["shares"] is None
        assert rows[3]["price"] == 1000.5 and rows[3]["note"] == "note, with comma"
        assert c.get("/api/portfolio").json()["positions"][0]["shares"] in (6.0, 2.0)

        # No header is fine too, and a problem anywhere means nothing is added, naming the line
        count = len(db.q("SELECT * FROM transactions"))
        for text, line in (("SPY,1,100,2025-01-02\nSPY,abc,100,2025-01-03", "Line 2"), ("SPY,1,100,soon", "Line 1"), ("SPY,1", "Line 1"),
                           ("SPY,1,100,2025-01-02\nSPY,1,100,2025-01-03,gift", "Line 2")):
            bad = send(c, text)
            assert bad.status_code == 400 and line in bad.json()["detail"], (text, bad.text)
        assert send(c, "SPY,1,100,2025-01-02\nSPY,5,100,2025-01-03,sell").status_code == 400  # sells more than held
        with _FakeMarket({}):
            unknown = send(c, "NOPE,1,100,2025-01-02")
        assert unknown.status_code == 400 and "NOPE" in unknown.json()["detail"]
        assert send(c, "").status_code == 400 and send(c, "ticker,shares,price,date\n").status_code == 400
        assert len(db.q("SELECT * FROM transactions")) == count
        assert send(c, "SPY,1,100,2025-01-02").json()["added"] == 1


def test_earnings_record():
    import pandas as pd

    from fastapi.testclient import TestClient

    from backend import data, db
    from backend.main import app

    class Listing:
        earnings_history = pd.DataFrame({"epsActual": [1.0, 2.1], "epsEstimate": [1.1, 2.0], "surprisePercent": [-0.0909, 0.05]},
                                        index=pd.to_datetime(["2026-06-30", "2026-09-30"]))

    class YF:
        Ticker = staticmethod(lambda t: Listing())

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    db.cache_clear()
    try:
        r = data.earnings_record("X")
        assert r[0] == {"quarter": "2026-09-30", "actual": 2.1, "estimate": 2.0, "surprise": 0.05}, "newest first"
        assert r[1]["surprise"] == -0.0909
        Listing.earnings_history = pd.DataFrame()
        assert data.earnings_record("EMPTY") == []
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        db.cache_clear()
    with TestClient(app) as c:
        assert c.get("/api/stock/AAPL/earnings").json()[0]["quarter"]
        assert c.get("/api/stock/SPY/earnings").json() == []


def test_fund_facts_and_look_through():
    from fastapi.testclient import TestClient

    from backend import data, db, portfolio
    from backend.main import app
    # From Yahoo's shape to names matching stock sectors
    import pandas as pd

    class Funds:
        top_holdings = pd.DataFrame({"Name": ["Apple Inc"], "Holding Percent": [0.08]}, index=pd.Index(["AAPL"], name="Symbol"))
        sector_weightings = {"technology": 0.4, "realestate": 0.1, "financial_services": 0.2}

    class Listing:
        funds_data = Funds()

    class YF:
        Ticker = staticmethod(lambda t: Listing())

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    db.cache_clear()
    try:
        f = data.fund("SPYX")
        assert f["sectors"] == {"Technology": 0.4, "Real Estate": 0.1, "Financial Services": 0.2}
        assert f["holdings"] == [{"ticker": "AAPL", "name": "Apple Inc", "weight": 0.08}]
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        db.cache_clear()

    # Hand-calculated: 1,000 of a stock in Tech, 2,000 of a fund that's 50% Tech, 25% Energy, top holding 10% that same stock
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('AAA', 10, 100), ('FUND', 10, 200), ('BOND', 10, 100)")
    flat = _steps([0.0] * 300)
    infos = {"AAA": {"sector": "Technology"}, "FUND": {"assetClass": "ETFs", "sector": "ETFs"}, "BOND": {"assetClass": "ETFs", "sector": "ETFs"}}
    funds = {"FUND": {"sectors": {"Technology": 0.5, "Energy": 0.25}, "holdings": [{"ticker": "AAA", "name": "A", "weight": 0.10}]},
             "BOND": {"sectors": {}, "holdings": []}}
    real = data.fund
    data.fund = lambda t: funds.get(t, {"sectors": {}, "holdings": []})
    try:
        with _FakeMarket({"AAA": flat, "FUND": flat * 2, "BOND": flat}, infos):
            p = portfolio.summary(db.get_settings())
    finally:
        data.fund = real
    lt = p["allocation"]["sectorLookThrough"]
    assert lt["Technology"] == 2000 and lt["Energy"] == 500
    assert lt["Funds: other or no sector data"] == 500 + 1000
    assert abs(sum(lt.values()) - p["total"]) < 1e-6
    assert p["allocation"]["sector"]["ETFs"] == 3000, "the plain view stays as it was"
    # 50% in Technology once funds are opened up: a real concentration
    assert any("50% of value is in one sector (Technology), counting what funds hold" in w for w in p["warnings"]), p["warnings"]
    aaa = [r for r in p["positions"] if r["ticker"] == "AAA"][0]
    assert aaa["throughFunds"] == 200
    # AAA alone is 25%, and through the fund 30%
    assert any("AAA is 30% of the portfolio" in w and "through funds" in w for w in p["warnings"]), p["warnings"]

    with TestClient(app) as c:
        d = c.get("/api/stock/SPY/fund").json()
        assert d["sectors"] and d["holdings"] and "expenseRatio" in d
        assert c.get("/api/stock/AAPL/fund").json() == {"sectors": {}, "holdings": [], "expenseRatio": None}
    assert abs(data._normalize_info("SPY", {"quoteType": "ETF", "netExpenseRatio": 0.0945})["netExpenseRatio"] - 0.000945) < 1e-12


def test_score_history_starts_now_and_notes_changes():
    import datetime as dt

    from fastapi.testclient import TestClient

    from backend import db, insights
    from backend.main import app
    _reset()
    row = lambda c, s, m: {"ticker": "AAA", "composite": c, "stance": s, "f_value": 50.0, "f_quality": 50.0, "f_growth": 50.0, "f_momentum": m, "f_risk": 50.0}
    insights.record_scores([row(62.0, "Favorable", 80.0)], "2026-01-02")
    insights.record_scores([row(99.0, "Strong", 90.0)], "2026-01-02")  # one a day: the first stands
    assert db.q("SELECT composite FROM score_history") == [{"composite": 62.0}]
    assert insights.score_change("AAA") is None, "one day has nothing to compare with"
    insights.record_scores([row(48.0, "Neutral", 62.0)], "2026-01-03")
    assert insights.score_change("AAA") == "AAA went from Favorable to Neutral (62 to 48), mostly on momentum (80 to 62)."
    insights.record_scores([row(47.0, "Neutral", 61.0)], "2026-01-04")
    assert insights.score_change("AAA") is None, "only a change of stance is news"
    insights.record_scores([{"ticker": "NONE", "composite": None, "stance": "Not scored"}], "2026-01-04")
    assert not db.q("SELECT 1 FROM score_history WHERE ticker = 'NONE'")

    # The digest records today's score and reports a change
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('AAA', 1, 100)")
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    insights.record_scores([row(1.0, "Weak", 1.0)], yesterday)
    with _FakeMarket({"AAA": _steps([0.001] * 300)}):
        items = insights.digest(db.get_settings())["items"]
        assert any("AAA went from Weak to" in i["text"] for i in items), [i["text"] for i in items]
        assert len(db.q("SELECT * FROM score_history WHERE ticker = 'AAA'")) == 2
        insights.digest(db.get_settings())
        assert len(db.q("SELECT * FROM score_history WHERE ticker = 'AAA'")) == 2

    # Research gets it, and it can't be rebuilt, so backups keep it
    with TestClient(app) as c:
        hist = c.get("/api/stock/AAA").json()["scoreHistory"]
        assert [h["date"] for h in hist][0] == yesterday and hist[0]["composite"] == 1.0
        backup = c.get("/api/backup").json()
        assert len(backup["score_history"]) >= 1
        _reset()
        assert c.post("/api/backup/restore", json=backup).status_code == 200
        assert db.q("SELECT composite FROM score_history WHERE date = ?", (yesterday,)) == [{"composite": 1.0}]
        # An older backup has none, which is fine
        older = {k: v for k, v in backup.items() if k != "score_history"}
        assert c.post("/api/backup/restore", json={**older, "version": 2}).status_code == 200


def test_search_finds_what_yahoo_lists():
    from fastapi.testclient import TestClient

    from backend import data, db
    from backend.main import app
    from backend.universe import currency_guess, region_of
    calls = []

    class FakeSearch:
        def __init__(self, q, **kw):
            calls.append(q)
            if q == "down":
                raise RuntimeError("no connection")
            self.quotes = {
                "vwce": [{"symbol": "VWCE.DE", "exchange": "GER", "quoteType": "ETF", "longname": "Vanguard FTSE All-World UCITS ETF"},
                         {"symbol": "VWCE.MI", "exchange": "MIL", "quoteType": "ETF", "shortname": "Vanguard All-World"},
                         {"symbol": "XVWC=F", "exchange": "CME", "quoteType": "FUTURE", "longname": "A future"},
                         {"symbol": "VWCE.OPT", "exchange": "X", "quoteType": "OPTION"}],
                "apple": [{"symbol": "AAPL", "exchange": "NMS", "quoteType": "EQUITY", "longname": "Apple Inc."},
                          {"symbol": "APLE", "exchange": "NYQ", "quoteType": "EQUITY", "longname": "Apple Hospitality"}],
            }.get(q.lower(), [])

    class YF:
        Search = FakeSearch

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    db.cache_clear()
    try:
        with TestClient(app) as c:
            hits = c.get("/api/search?q=vwce").json()
            assert [h["ticker"] for h in hits] == ["VWCE.DE", "VWCE.MI"], "only things the app can analyse"
            # VWCE.DE is in the built-in list, so that entry comes first and Yahoo's copy of it is dropped
            assert hits[0]["market"] == "ETFs" and hits[1]["name"] == "Vanguard All-World" and hits[1]["market"] == "ETF · MIL"
            c.get("/api/search?q=vwce")
            assert calls.count("vwce") == 1, "repeat searches use the cache"
            # The built-in list comes first, and nothing appears twice
            apple = c.get("/api/search?q=apple").json()
            assert [h["ticker"] for h in apple] == ["AAPL", "APLE"] and apple[0]["market"] == "North America"
            assert apple[1]["name"] == "Apple Hospitality" and apple[1]["market"] == "Stock · NYQ", "Yahoo's long name and type"
            # With Yahoo down or nothing found, a typed symbol can still be tried
            assert c.get("/api/search?q=down").json() == [{"ticker": "DOWN", "name": "Look up this symbol on Yahoo Finance", "market": "Any"}]
            assert not db.cache_get_stale("search:down")
            # A Screener word is offered as a screen, in any case, and first, so it isn't taken for a symbol
            screen = {"ticker": "CHEAP", "name": "Ask the Screener", "market": "Screener", "screen": True}
            assert c.get("/api/search?q=CHEAP").json()[0] == screen
            assert c.get("/api/search?q=cheap").json()[0] == {**screen, "ticker": "cheap"}
            assert c.get("/api/search?q=CHEAP European BANKS").json()[0]["screen"]
            # With a word the rules don't know in there too, any stocks found come first and the screen last
            assert c.get("/api/search?q=apple dividend").json()[-1]["screen"]
            assert not any(h.get("screen") for h in c.get("/api/search?q=apple").json())
            assert c.get("/api/search?q=").json() == []
            # One letter is too broad to send to Yahoo
            before = len(calls)
            c.get("/api/search?q=v")
            assert len(calls) == before

            # Opening a symbol with no data suggests the right one
            with _FakeMarket({}):
                r = c.get("/api/stock/VWCE")
            assert r.status_code == 404 and "VWCE.DE" in r.json()["detail"], r.text
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        db.cache_clear()
    # Germany's other exchanges are European and in euros
    for t in ("VWCE.MU", "VWCE.DU", "X.BE", "X.HM", "X.HA"):
        assert currency_guess(t) == "EUR" and region_of(t) == "Europe", t


def test_weekly_candles():
    import pandas as pd

    from backend import data
    # Two weeks, the second starting on a Tuesday after a holiday
    days = pd.DatetimeIndex(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",
                             "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"])
    close = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18], index=days, dtype=float)
    view = pd.DataFrame({"Open": close - 0.5, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 100.0})
    lines = pd.DataFrame({"sma50": close * 10}, index=days)
    candles, wl = data.weekly(view, lines)
    assert list(candles.index.strftime("%Y-%m-%d")) == ["2026-06-01", "2026-06-09"], "dated by each week's first trading day"
    assert candles.iloc[0].tolist() == [9.5, 15.0, 9.0, 14.0, 500.0], "open, high, low, close, volume"
    assert candles.iloc[1].tolist() == [14.5, 19.0, 14.0, 18.0, 400.0]
    assert wl["sma50"].tolist() == [140.0, 180.0], "daily lines taken at each week's last close"
    assert list(wl.index) == list(candles.index)
    # Tadawul and Qatar trade Sunday to Thursday: a Sunday opens its week rather than closing the one before
    gulf = pd.DatetimeIndex(["2025-01-05", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-12", "2025-01-13"])
    gc = pd.Series([1, 2, 3, 4, 5, 6, 7], index=gulf, dtype=float)
    gv = pd.DataFrame({"Open": gc, "High": gc, "Low": gc, "Close": gc, "Volume": 1.0})
    for t in ("2222.SR", "QNBK.QA", "NBK.KW"):
        c, _ = data.weekly(gv, pd.DataFrame({"sma50": gc}, index=gulf), t)
        assert [(d.strftime("%a"), r.Open, r.Close) for d, r in c.iterrows()] == [("Sun", 1, 5), ("Sun", 6, 7)], t
    c, _ = data.weekly(gv, pd.DataFrame({"sma50": gc}, index=gulf), "EMAAR.AE")  # the UAE trades Monday to Friday
    assert len(c) == 3


def test_intraday_candles():
    import pandas as pd

    from backend import data, db
    calls = []

    def bars(start, periods, freq, tz):
        idx = pd.date_range(start, periods=periods, freq=freq, tz=tz)
        close = pd.Series(range(periods), index=idx, dtype=float) + 100
        return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 10.0})

    class FakeTicker:
        def __init__(self, t):
            self.t = t

        def history(self, period, interval, auto_adjust):
            calls.append((self.t, period, interval))
            if self.t == "DOWN":
                raise RuntimeError("no connection")
            if self.t == "BTC-USD":
                return bars("2026-06-08 00:00", 3 * 96, "15min", "UTC")
            # Two New York sessions of 15-minute candles
            return pd.concat([bars("2026-06-11 09:30", 26, "15min", "America/New_York"),
                              bars("2026-06-12 09:30", 26, "15min", "America/New_York")])

    class YF:
        Ticker = FakeTicker

    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    db.init()
    db.cache_clear()
    try:
        one = data.intraday("AAPL", "1d")
        assert set(one.index.strftime("%Y-%m-%d")) == {"2026-06-12"}, "1D is the latest session"
        assert one.index[0].strftime("%H:%M") == "09:30", "in the exchange's own clock, not UTC"
        assert len(data.intraday("AAPL", "5d")) == 52
        assert calls == [("AAPL", "5d", "5m"), ("AAPL", "5d", "15m")]
        data.intraday("AAPL", "1d")
        assert len(calls) == 2, "cached"
        btc = data.intraday("BTC-USD", "1d")
        assert btc.index[-1] - btc.index[0] == pd.Timedelta(hours=23, minutes=45), "round the clock: the last 24 hours"
        try:
            data.intraday("DOWN", "1d")
            raise AssertionError("expected DataError")
        except data.DataError:
            pass
        # On the page, a symbol without intraday prices still opens, with the reason in place of the chart
        with TestClient(app) as c, _FakeMarket({"DOWN": _steps([0.01] * 300)}):
            j = c.get("/api/stock/DOWN?period=1d").json()["candles"]
            assert j["time"] == [] and j["intraday"] == "5m" and "intraday" in j["note"]
    finally:
        data._yf, data.DEMO = real_yf, real_demo
        db.cache_clear()

    # Through the API, in demo mode: times as seconds, no daily indicators; weekly bars keep them
    with TestClient(app) as c:
        d1 = c.get("/api/stock/AAPL?period=1d").json()["candles"]
        assert d1["intraday"] == "5m" and len(d1["time"]) == 78 and isinstance(d1["time"][0], int) and "sma50" not in d1
        assert pd.Timestamp(d1["time"][0], unit="s").strftime("%H:%M") == "09:30"
        assert c.get("/api/stock/AAPL?period=5d").json()["candles"]["intraday"] == "15m"
        wk = c.get("/api/stock/AAPL?period=5y&bars=week").json()["candles"]
        day = c.get("/api/stock/AAPL?period=5y").json()["candles"]
        assert wk["bars"] == "week" and day["bars"] == "day" and len(day["time"]) / 6 < len(wk["time"]) < len(day["time"]) / 4
        assert wk["close"][-1] == day["close"][-1] and wk["sma50"][-1] == day["sma50"][-1]
        assert all(pd.Timestamp(t).day_of_week == 0 for t in wk["time"][1:]), "demo weeks start on Monday"


def test_fund_regions_follow_what_they_hold():
    from backend import db
    from backend.universe import UNIVERSE, region_of
    assert "VWCE.DE" in UNIVERSE["ETFs"]
    expected = {"VWCE.DE": "Global", "SPY": "North America", "QQQ": "North America", "VWO": "Emerging", "VXUS": "International",
                "EFA": "International", "KSA": "Middle East & Africa", "QAT": "Middle East & Africa"}
    assert {t: region_of(t) for t in expected} == expected
    # Only the named funds: other listings and plain stocks still go by exchange
    assert region_of("VWCE.MI") == "Europe" and region_of("SAP.DE") == "Europe" and region_of("AAPL") == "North America"
    _reset()
    db.cache_clear()  # the region is stored with each symbol's details
    with TestClient(app) as c:
        assert c.get("/api/search?q=vwce").json()[0]["ticker"] == "VWCE.DE", "found in the built-in list first"
        for t in ("VWCE.DE", "AAPL", "SAP.DE"):
            assert c.post("/api/transactions", json={"ticker": t, "kind": "buy", "shares": 10, "price": 100}).status_code == 200
        p = c.get("/api/portfolio").json()
        pos = {r["ticker"]: r for r in p["positions"]}
        regions = p["allocation"]["region"]
        assert set(regions) == {"Global", "North America", "Europe"}, regions
        assert abs(regions["Global"] - pos["VWCE.DE"]["value"]) < 1e-6, "its own slice, not counted as Europe"
        assert abs(regions["Europe"] - pos["SAP.DE"]["value"]) < 1e-6
    _reset()


def test_dashboard_tiles_are_chosen_in_settings():
    from backend.universe import DEFAULT_TILES, MACRO
    _reset()
    with TestClient(app) as c:
        catalog = [t["ticker"] for t in c.get("/api/meta").json()["tiles"]]
        assert catalog == list(MACRO) and "KSA" in catalog and "QAT" in catalog and "^TASI.SR" not in catalog
        assert [m["ticker"] for m in c.get("/api/macro").json()] == DEFAULT_TILES, "a new app shows the default set"
        # Unknown symbols are dropped and the order is the catalogue's, whatever order they were sent in
        saved = c.post("/api/settings", json={"dashboard_tiles": ["QAT", "^GSPC", "NOPE"]}).json()
        assert saved["dashboard_tiles"] == ["^GSPC", "QAT"]
        tiles = c.get("/api/macro").json()
        assert [m["ticker"] for m in tiles] == ["^GSPC", "QAT"] and tiles[1]["name"] == "Qatar (QAT ETF)"
        assert len(tiles[1]["spark"]) > 20, "the Gulf tiles have history, unlike the indexes they replace"
        assert c.post("/api/settings", json={"dashboard_tiles": "^GSPC"}).status_code == 400
        assert c.post("/api/settings", json={"dashboard_tiles": []}).json()["dashboard_tiles"] == []
        assert c.get("/api/macro").json() == []
    _reset()


def test_portfolio_fit_compares_in_one_currency():
    import pandas as pd

    from backend import data, insights
    _reset()
    held = _steps([0.01, -0.012, 0.008, -0.004] * 75)  # a euro holding, in euros
    rate = pd.Series([0.9 + 0.05 * ((i % 7) - 3) / 3 for i in range(len(held))], index=held.index)  # euros per dollar, wandering
    usd_twin = held / rate  # a dollar stock that, in euros, is exactly the holding
    real = data.fx_series
    data.fx_series = lambda cur, base, index: rate.reindex(index).ffill() if (cur, base) == ("USD", "EUR") else real(cur, base, index)
    infos = {"HELD": {"currency": "EUR"}, "TWIN": {"currency": "USD"}}
    try:
        with TestClient(app) as c, _FakeMarket({"HELD": held, "TWIN": usd_twin}, infos):
            c.post("/api/settings", json={"base_currency": "EUR"})
            assert c.post("/api/transactions", json={"ticker": "HELD", "kind": "buy", "date": "2025-01-06", "shares": 10, "price": 100}).status_code == 200
            from backend import db
            real_tickers = insights.tickers_for
            insights.tickers_for = lambda markets: ["TWIN"]
            try:
                o = insights.opportunities(db.get_settings(), ["US"])
            finally:
                insights.tickers_for = real_tickers
        twin = next(r for r in o["map"] if r["ticker"] == "TWIN")
        assert twin["corr"] is not None and twin["corr"] > 0.999, twin["corr"]
    finally:
        data.fx_series = real
        _reset()


def test_dcf_default_growth_uses_several_years():
    from backend import insights
    inf = {"freeCashflow": 1e9, "sharesOutstanding": 1e8, "statementFx": 1.0, "revenueGrowth": 0.41}
    # One quarter's +41% would have been capped at 15% a year; three years of shrinking revenue give 0%, the floor
    assert insights.dcf({**inf, "revenueGrowth3y": -0.115})["growth"] == 0.0
    assert abs(insights.dcf({**inf, "revenueGrowth3y": 0.06})["growth"] - 0.06) < 1e-12
    assert insights.dcf(inf)["growth"] == 0.15, "without three years of statements, the latest quarter, capped"


def test_growth_is_scored_over_several_years():
    from backend import insights, scoring
    fin = [{"year": "2022", "revenue": 200.0, "netIncome": 50.0}, {"year": "2023", "revenue": 150.0, "netIncome": 20.0},
           {"year": "2024", "revenue": 140.0, "netIncome": -5.0}, {"year": "2025", "revenue": 162.0, "netIncome": 30.0}]
    # Revenue fell from 200 to 162 over three years: about -6.8% a year, though the last year was +16%
    assert abs(insights.multi_year_growth(fin, "revenue") - ((162 / 200) ** (1 / 3) - 1)) < 1e-12
    assert insights.multi_year_growth(fin[:2], "revenue") is None, "fewer than three years isn't enough"
    assert insights.multi_year_growth([{**r, "netIncome": -1.0} if r["year"] == "2025" else r for r in fin], "netIncome") is None, "no growth rate from a loss"
    px = _steps([0.001] * 300)
    base = {"assetClass": "Stocks", "quoteType": "EQUITY", "revenueGrowth": 0.41, "earningsGrowth": 1.2}
    multi = {p["label"]: p for p in scoring.evaluate({**base, "revenueGrowth3y": -0.115, "earningsGrowth3y": None}, px)["breakdown"]["growth"]}
    # The quarter's +41% is replaced by the years' -11.5%; earnings, with no three-year rate, fall back and say so
    assert multi["Revenue growth (3y, a year)"]["score"] == 0.0 and "Revenue growth (latest quarter YoY)" not in multi
    assert "Earnings growth (latest quarter YoY)" in multi


def test_banks_quality_rests_on_return_on_equity():
    from backend import scoring
    px = _steps([0.001] * 300)
    figures = {"assetClass": "Stocks", "quoteType": "EQUITY", "returnOnEquity": 0.15, "operatingMargins": 0.55, "profitMargins": 0.33, "debtToEquity": 3.0, "currentRatio": 0.5}
    bank = scoring.evaluate({**figures, "industry": "Banks - Diversified"}, px)
    other = scoring.evaluate({**figures, "industry": "Software"}, px)
    q = {p["label"]: p for p in bank["breakdown"]["quality"]}
    # Only return on equity counts for a bank: 15% is 50 on the 0%-30% scale; the rest is listed, not scored, and says why
    assert bank["factors"]["quality"] == 50.0 and q["Return on equity"]["score"] == 50.0
    assert all(q[k]["score"] is None and "banks" in q[k]["note"] for k in ("Operating margin", "Net margin", "Debt / Equity", "Current ratio"))
    assert all(p["score"] is not None and "note" not in p for p in other["breakdown"]["quality"]), "other companies are scored on all five"
    # Metrics that don't apply aren't counted as missing data
    assert bank["metricsTotal"] == other["metricsTotal"] - 4


def test_one_risk_scale_for_every_asset():
    from backend import scoring
    bumpy = _steps([0.03, -0.028] * 150)
    scores = {k: scoring.factor_breakdown({"assetClass": k}, bumpy)["risk"] for k in ("Crypto", "ETFs")}
    vol_dd = lambda parts: [(p["label"], p["score"]) for p in parts if p["label"] != "Beta"]
    assert vol_dd(scores["Crypto"]) == vol_dd(scores["ETFs"]), "the same ride scores the same, coin or fund"


def test_a_falling_world_fund_does_not_prompt_rethinking_it():
    from backend import db, insights
    _reset()
    crash = _steps([0.0] * 240 + [-0.012] * 60)  # down about a half in three months: weak trend, high risk
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('VWCE.DE', 10, 100), ('AAA', 10, 100)")
    for day, stance, comp in (("2026-01-01", "Strong", 75), ("2026-01-02", "Weak", 30)):
        db.x("INSERT INTO score_history (date, ticker, composite, stance, factors) VALUES (?, 'VWCE.DE', ?, ?, '{}')", (day, comp, stance))
    infos = {"VWCE.DE": {"assetClass": "ETFs"}, "AAA": {"trailingPE": 80.0, "returnOnEquity": -0.2, "profitMargins": -0.1, "revenueGrowth": -0.3}}
    with _FakeMarket({"VWCE.DE": crash, "AAA": crash}, infos):
        rows = {r["ticker"]: r for r in insights.rows_for(["VWCE.DE", "AAA"], db.get_settings())}
        texts = [i["text"] for i in insights.digest(db.get_settings())["items"]]
    assert rows["VWCE.DE"]["stance"] in ("Weak", "Caution") and rows["AAA"]["stance"] == "Weak", {t: r["stance"] for t, r in rows.items()}
    # The company is flagged for a rethink; the world fund isn't, and its stance change isn't reported either
    assert any(t.startswith("AAA is a holding with a Weak composite score") for t in texts), texts
    assert not any(t.startswith("VWCE.DE") and ("thesis" in t or "went from" in t) for t in texts), texts
    _reset()


def test_rebalancing_offers_new_money_before_selling():
    _reset()
    flat = _steps([0.0] * 300)
    with TestClient(app) as c, _FakeMarket({"SPY": flat, "AAPL": flat, "BTC-USD": flat}, {"SPY": {"assetClass": "ETFs"}, "BTC-USD": {"assetClass": "Crypto"}}):
        c.post("/api/settings", json={"targets": {"Stocks": 20, "ETFs": 80, "Crypto": 0}, "core_target": 80})
        assert c.post("/api/transactions", json={"ticker": "SPY", "kind": "buy", "date": "2025-01-06", "shares": 100, "price": 100}).status_code == 200
        p = c.get("/api/portfolio").json()
        rows = {r["assetClass"]: r for r in p["rebalance"]}
        # All in funds against 80/20: €2,500 of new money into stocks reaches 80/20; nothing is to be sold
        assert abs(p["rebalanceNewMoney"] - 2500) < 1e-6 and abs(rows["Stocks"]["newMoney"] - 2500) < 1e-6 and rows["ETFs"]["newMoney"] < 1e-6
        assert rows["ETFs"]["action"] == "Over target" and "Trim" not in str(p["rebalance"])
        cs = p["coreSatellite"]
        assert cs["action"] == "Above core target" and abs(cs["newMoney"] - 2500) < 1e-6
        # Something held with a 0% target can only be reached by selling, so there's no new-money route
        assert c.post("/api/transactions", json={"ticker": "BTC-USD", "kind": "buy", "date": "2025-01-06", "shares": 1, "price": 100}).status_code == 200
        assert c.get("/api/portfolio").json()["rebalanceNewMoney"] is None
    _reset()


def test_core_and_satellite():
    from backend import db
    _reset()
    spy = _steps([0.001] * 300)  # core: a steady climb
    aapl = _steps([0.03, -0.02] * 150)  # satellite: jumpy
    d0, d1 = spy.index[100], spy.index[200]
    day = lambda d: d.date().isoformat()
    buy = lambda c, t, d, n: c.post("/api/transactions", json={"ticker": t, "kind": "buy", "date": day(d), "shares": n, "price": 100}).status_code
    with TestClient(app) as c, _FakeMarket({"SPY": spy, "AAPL": aapl}):
        assert buy(c, "SPY", d0, 10) == 200 and buy(c, "AAPL", d0, 10) == 200
        p = c.get("/api/portfolio").json()
        pos = {r["ticker"]: r for r in p["positions"]}
        assert pos["SPY"]["bucket"] == "core" and pos["AAPL"]["bucket"] == "satellite", "broad funds start as core, stocks as satellite"
        cs = p["coreSatellite"]
        core = pos["SPY"]["value"] / p["total"]
        assert abs(cs["core"] - core) < 1e-9 and abs(cs["satellite"] - (1 - core)) < 1e-9 and cs["target"] == 0.8
        assert cs["action"] == ("To core" if core < 0.78 else "Above core target" if core > 0.82 else "Hold")
        assert abs(cs["amount"] - abs(0.8 - core) * p["total"]) < 1e-6
        # New money that reaches the target without selling: afterwards the core share is exactly the target
        add = cs["newMoney"]
        core_after = (pos["SPY"]["value"] + (add if cs["action"] == "To core" else 0)) / (p["total"] + add)
        assert add >= 0 and abs(core_after - 0.8) < 1e-9, (cs["action"], add, core_after)
        slices = p["allocation"]["bucket"]
        assert set(slices) == {"Core", "Satellite"} and abs(slices["Core"] - pos["SPY"]["value"]) < 1e-6 and abs(slices["Satellite"] - pos["AAPL"]["value"]) < 1e-6
        # Who carries the swings: the shares add up to 1, and the jumpy satellite carries more than its share of the money
        risk = cs["riskShare"]
        assert abs(risk["core"] + risk["satellite"] - 1) < 1e-9 and risk["satellite"] > cs["satellite"], risk
        # Each bucket's return over the time both were held, by hand: no trades in that window, so price over price
        perf = cs["performance"]
        assert perf["start"] == day(d0)
        assert abs(perf["core"] - (spy.iloc[-1] / spy[d0] - 1)) < 1e-9 and abs(perf["satellite"] - (aapl.iloc[-1] / aapl[d0] - 1)) < 1e-9
        assert abs(perf["edge"] - (perf["satellite"] - perf["core"])) < 1e-12
        first = next(i for i, v in enumerate(perf["coreLine"]) if v is not None)
        assert perf["dates"][0] == day(d0) and perf["coreLine"][0] == 100 and perf["satelliteLine"][0] == 100 and not perf["pastYearOnly"]

        # Switching a holding, and switching it back, which leaves nothing stored
        assert c.post("/api/buckets", json={"ticker": "aapl", "bucket": "core"}).json() == {"ticker": "AAPL", "bucket": "core"}
        assert db.q("SELECT bucket FROM buckets WHERE ticker='AAPL'")[0]["bucket"] == "core"
        p2 = c.get("/api/portfolio").json()
        assert p2["coreSatellite"]["core"] == 1 and p2["coreSatellite"]["performance"] is None, "nothing to compare with no satellite"
        assert c.post("/api/buckets", json={"ticker": "AAPL", "bucket": "satellite"}).status_code == 200
        assert db.q("SELECT * FROM buckets") == []
        assert c.post("/api/buckets", json={"ticker": "AAPL", "bucket": "growth"}).status_code == 400
        assert c.post("/api/buckets", json={"ticker": "", "bucket": "core"}).status_code == 400

        # The target is yours; out-of-range values are refused
        for bad in (150, -5, "lots"):
            assert c.post("/api/settings", json={"core_target": bad}).status_code == 400, bad
        assert c.post("/api/settings", json={"core_target": 60}).json()["core_target"] == 60
        assert c.get("/api/portfolio").json()["coreSatellite"]["target"] == 0.6
    _reset()

    # Fairness: when the core is bought later, both are compared only from then on
    with TestClient(app) as c, _FakeMarket({"SPY": spy, "AAPL": aapl}):
        assert buy(c, "AAPL", d0, 10) == 200 and buy(c, "SPY", d1, 10) == 200
        perf = c.get("/api/portfolio").json()["coreSatellite"]["performance"]
        assert perf["start"] == day(d1) and abs(perf["satellite"] - (aapl.iloc[-1] / aapl[d1] - 1)) < 1e-9, perf["start"]
        assert perf["days"] == len(spy[spy.index >= d1]) - 1
    _reset()

    # With a holding's data missing, the split is shown but no amount is suggested
    with TestClient(app) as c:
        with _FakeMarket({"SPY": spy, "AAPL": aapl}):
            assert buy(c, "SPY", d0, 10) == 200 and buy(c, "AAPL", d0, 10) == 200
        with _FakeMarket({"SPY": spy, "AAPL": None}):
            cs = c.get("/api/portfolio").json()["coreSatellite"]
        assert cs["core"] == 1 and "action" not in cs and "amount" not in cs
    _reset()


def test_buckets_are_in_backups():
    from backend import db
    _reset()
    with TestClient(app) as c:
        db.x("INSERT INTO buckets (ticker, bucket) VALUES ('NVDA', 'core')")
        backup = c.get("/api/backup").json()
        assert backup["version"] == 4 and backup["buckets"] == [{"ticker": "NVDA", "bucket": "core"}]
        db.x("DELETE FROM buckets")
        assert c.post("/api/backup/restore", json=backup).status_code == 200
        assert db.q("SELECT * FROM buckets") == [{"ticker": "NVDA", "bucket": "core"}]
        # A backup from before buckets existed restores, and leaves the defaults
        older = {k: v for k, v in backup.items() if k != "buckets"}
        assert c.post("/api/backup/restore", json={**older, "version": 3}).status_code == 200
        assert db.q("SELECT * FROM buckets") == []
    _reset()


def test_custom_and_preset_tiles():
    import pandas as pd

    from backend.universe import DEFAULT_TILES
    _reset()
    one_day = pd.Series([100.0], index=pd.DatetimeIndex(["2026-06-01"]))
    market = {"^AEX": _steps([0.001] * 300), "IWDA.AS": _steps([0.002] * 300), "^TASI.SR": one_day, "NOPE": None}
    with TestClient(app) as c, _FakeMarket(market):
        tiles = {t["ticker"]: t for t in c.get("/api/meta").json()["tiles"]}
        # The two presets are offered, unticked, and named
        assert tiles["VWCE.DE"]["name"] == "FTSE All-World (VWCE)" and tiles["ACWI"]["name"] == "MSCI ACWI (USD)"
        assert not tiles["ACWI"]["custom"] and "ACWI" not in DEFAULT_TILES and "VWCE.DE" not in DEFAULT_TILES

        # A custom tile is saved only after Yahoo returns a price history for it, and goes straight onto the dashboard
        r = c.post("/api/tiles", json={"ticker": " ^aex ", "name": "  Amsterdam AEX  "})
        assert r.status_code == 200, r.text
        saved = r.json()["settings"]
        assert saved["custom_tiles"] == [{"ticker": "^AEX", "name": "Amsterdam AEX"}] and saved["dashboard_tiles"][-1] == "^AEX"
        assert {"ticker": "^AEX", "name": "Amsterdam AEX", "custom": True} in r.json()["tiles"]
        macro = {m["ticker"]: m for m in c.get("/api/macro").json()}
        assert macro["^AEX"]["name"] == "Amsterdam AEX" and len(macro["^AEX"]["spark"]) == 60
        # With no name given, Yahoo's name is used
        assert c.post("/api/tiles", json={"ticker": "IWDA.AS"}).json()["settings"]["custom_tiles"][1] == {"ticker": "IWDA.AS", "name": "IWDA.AS"}

        # Refused: no history, a single day (the Gulf index case), a preset, a duplicate, junk
        for body, why in (({"ticker": "NOPE"}, "no price history"), ({"ticker": "^TASI.SR"}, "only returns 1 day"),
                          ({"ticker": "ACWI"}, "already in the list"), ({"ticker": "^aex"}, "already have"),
                          ({"ticker": ""}, "Enter a ticker"), ({"ticker": "A, B"}, "single Yahoo symbol")):
            r = c.post("/api/tiles", json=body)
            assert r.status_code == 400 and why in r.json()["detail"], (body, r.text)
        assert len(c.get("/api/settings").json()["custom_tiles"]) == 2, "nothing refused was stored"

        # Ticking and unticking works for custom tiles like any other; settings can't add unchecked ones
        assert c.post("/api/settings", json={"dashboard_tiles": ["^AEX", "ACWI", "GONE"]}).json()["dashboard_tiles"] == ["ACWI", "^AEX"]
        c.post("/api/settings", json={"custom_tiles": [{"ticker": "SNEAKY", "name": "x"}]})
        assert [t["ticker"] for t in c.get("/api/settings").json()["custom_tiles"]] == ["^AEX", "IWDA.AS"]

        # Kept in backups, with the rest of the settings
        backup = c.get("/api/backup").json()
        assert backup["settings"]["custom_tiles"][0]["ticker"] == "^AEX"

        # Removing one takes it off the dashboard too
        r = c.delete("/api/tiles/%5EAEX")
        assert r.status_code == 200 and [t["ticker"] for t in r.json()["settings"]["custom_tiles"]] == ["IWDA.AS"]
        assert "^AEX" not in r.json()["settings"]["dashboard_tiles"] and "^AEX" not in {m["ticker"] for m in c.get("/api/macro").json()}
        assert c.delete("/api/tiles/%5EAEX").status_code == 404 and c.delete("/api/tiles/ACWI").status_code == 404, "presets can't be removed"

        assert c.post("/api/backup/restore", json=backup).status_code == 200
        assert [t["ticker"] for t in c.get("/api/settings").json()["custom_tiles"]] == ["^AEX", "IWDA.AS"]

        # There's a limit, so the dashboard can't be flooded
        for i in range(10):
            market[f"T{i}"] = market["^AEX"]
            assert c.post("/api/tiles", json={"ticker": f"T{i}"}).status_code == 200
        assert "up to 12" in c.post("/api/tiles", json={"ticker": "IWDA.AS2"}).json()["detail"]
    _reset()


class _FakeLLM:
    """A real HTTP server standing in for an AI provider. `replies` is a list of (status, body, delay) used in turn;
    every request is recorded as (path, headers, json)."""

    def __init__(self, replies):
        import json
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.replies, self.seen = list(replies), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                status, reply, delay = fake.replies.pop(0) if fake.replies else (500, {"error": "no more replies"}, 0)
                time.sleep(delay)
                data = json.dumps(reply).encode()
                try:
                    self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
                    self.end_headers(); self.wfile.write(data)
                except OSError:
                    pass  # the client gave up waiting

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _chat(text):
    return (200, {"choices": [{"message": {"role": "assistant", "content": text}}]}, 0)


def _with_env(**env):
    """Set AI_* variables for one block, and put back what was there."""
    import contextlib

    @contextlib.contextmanager
    def block():
        keys = ("AI_PROVIDER", "AI_BASE_URL", "AI_MODEL", "AI_API_KEY", "AI_TIMEOUT", "AI_THINKING", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                "TAVILY_API_KEY", "WEB_SEARCH")  # a Tavily key in .env must never be used by a test
        before = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update({k: v for k, v in env.items() if v is not None})
        try:
            yield
        finally:
            for k, v in before.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    return block()


def test_ai_providers():
    from backend import ai
    ctx = {"ticker": "AAPL", "info": {"name": "Apple"}, "stats": {}, "news": [], "financials": [],
           "scores": {"factors": {"value": 50}, "composite": 60, "stance": "Favorable", "signals": []}}

    # Which provider is on, from .env alone
    with _with_env():
        assert not ai.available() and ai.describe()["provider"] == "none" and ai.describe()["problem"] is None
        assert ai.brief(ctx)["source"] == "rules"
    with _with_env(ANTHROPIC_API_KEY="sk-old"):  # an older .env with only the Anthropic key still works
        c = ai.config()
        assert ai.available() and c["provider"] == "anthropic" and c["model"] == ai.ANTHROPIC_DEFAULT_MODEL and c["timeout"] == 90
    with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL="http://localhost:11434/v1", AI_MODEL="llama3.2:3b"):
        d = ai.describe()
        assert ai.available() and d == {"on": True, "provider": "openai_compatible", "server": "Ollama", "model": "llama3.2:3b", "thinking": "default", "problem": None}
        assert ai.config()["timeout"] == 180, "local models get longer"
    for url, name, timeout in (("http://127.0.0.1:1234/v1", "LM Studio", 180), ("https://openrouter.ai/api/v1", "OpenRouter", 90),
                               ("https://api.groq.com/openai/v1", "Groq", 90), ("http://192.168.1.20:8080/v1", "192.168.1.20", 180)):
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=url, AI_MODEL="m"):
            assert (ai.config()["server"], ai.config()["timeout"]) == (name, timeout), url
    with _with_env(AI_PROVIDER="openai_compatible", AI_MODEL="m"):
        assert not ai.available() and "AI_BASE_URL" in ai.describe()["problem"]
    with _with_env(AI_PROVIDER="gemini"):
        assert not ai.available() and "Unknown AI_PROVIDER" in ai.describe()["problem"]
    with _with_env(AI_PROVIDER="anthropic", AI_API_KEY="secret-key"):
        assert "secret-key" not in str(ai.describe()), "the key never leaves the server"

    # openai_compatible: the right URL and body, no key header without a key, and reasoning removed from the brief
    llm = _FakeLLM([_chat("<think>Let me look at the data first.</think>\n**Snapshot** Apple is fine.")])
    try:
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url + "/v1", AI_MODEL="llama3.2:3b"):
            b = ai.brief(ctx)
            assert (b["source"], b["text"], b["by"]) == ("ai", "**Snapshot** Apple is fine.", "llama3.2:3b via 127.0.0.1") and _core(b["check"]) == {"checked": 0, "unmatched": [], "suggested": []}, b
            path, headers, body = llm.seen[0]
            assert path == "/v1/chat/completions" and "authorization" not in headers
            assert body["model"] == "llama3.2:3b" and [m["role"] for m in body["messages"]] == ["system", "user"] and "AAPL" in body["messages"][1]["content"]
            assert "reasoning_effort" not in body, "nothing extra is sent unless asked for"
            assert body["temperature"] == 0, "the same data gives the same brief"
        # Thinking off: asked for the way Ollama understands on this endpoint
        llm.replies = [_chat("Quick answer.")]
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url + "/v1", AI_MODEL="gemma4:e4b", AI_THINKING="off"):
            assert ai.brief(ctx)["text"] == "Quick answer." and ai.describe()["thinking"] == "off"
            assert llm.seen[-1][2]["reasoning_effort"] == "none" and "think" not in llm.seen[-1][2]
        # With a key (OpenRouter, Groq), it's sent as a bearer token
        llm.replies = [_chat("Fine.")]
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_API_KEY="gsk-123"):
            ai.brief(ctx)
            assert llm.seen[-1][1]["authorization"] == "Bearer gsk-123" and llm.seen[-1][0] == "/chat/completions"

        # Anything going wrong gives the rule-based brief: an error, an empty reply, nobody listening, a timeout
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m"):
            for reply in ((500, {"error": "boom"}, 0), _chat("<think>only thinking</think>"), (200, {"choices": []}, 0)):
                llm.replies = [reply]
                b = ai.brief(ctx)
                assert b["source"] == "rules" and "AI call failed" in b["text"] and "composite score 60" in b["text"], b
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL="http://127.0.0.1:9/v1", AI_MODEL="m"):
            assert ai.brief(ctx)["source"] == "rules"
        llm.replies = [(200, {"choices": [{"message": {"content": "late"}}]}, 1.5)]
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_TIMEOUT="0.5"):
            b = ai.brief(ctx)
            assert b["source"] == "rules" and "didn't answer within 0 seconds" not in b["text"] and "didn't answer within" in b["text"], b["text"][-80:]

        # Screener questions: fences and reasoning removed, values put in the screener's units, unknown keys dropped
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m"):
            llm.seen.clear()
            llm.replies = [_chat('<think>gulf means Gulf</think>Here you go:\n```json\n{"markets": ["gulf"], "max_pe": "15", "min_div_yield": 3, "colour": "blue"}\n```')]
            # The rules' values stand (a number typed, or a word's defined meaning: "cheap" is P/E 18); the model's own
            # stricter limits don't replace them, and it's told what the words mean here
            rules = ai._parse_rules("cheap gulf dividend payers")
            assert ai.parse_query("cheap gulf dividend payers") == {k: v for k, v in rules.items() if k != "ignored"}
            assert len(llm.seen) == 1 and "low volatility/low risk/safe/defensive: max_vol 0.25" in llm.seen[0][2]["messages"][1]["content"]
            # The model fills what the rules couldn't read; a sector it only rules out narrowly stays narrow
            llm.replies = [_chat('{"markets": ["Europe"], "max_vol": 0.1, "exclude_sector": "Financial Services"}')]
            got = ai.parse_query("safe stocks on the continent excluding banks")
            assert got["markets"] == ["Europe"] and got["max_vol"] == 0.25 and got["exclude_industry"] == "Banks" and "exclude_sector" not in got, got
            # Unreadable once: asked again, and the second answer is used
            llm.seen.clear()
            llm.replies = [_chat("I think you want Gulf stocks!"), _chat('{"markets": ["US"], "sort": "growth"}')]
            got = ai.parse_query("fast growing american companies")
            # the model still says "US": read as the country it meant
            assert got["countries"] == ["United States"] and got["sort"] == "growth" and got["min_rev_growth"] == 0.10 and "ignored" not in got, got
            assert len(llm.seen) == 2 and "couldn't be read" in llm.seen[1][2]["messages"][1]["content"]
            # Unreadable twice: the regex rules take over
            llm.seen.clear()
            llm.replies = [_chat("no idea"), (500, {}, 0)]
            assert ai.parse_query("cheap gulf banks") == ai._parse_rules("cheap gulf banks") and len(llm.seen) == 2

        # The Anthropic path still works, against the same stand-in
        llm.seen.clear()
        llm.replies = [(200, {"content": [{"type": "text", "text": "Anthropic brief."}]}, 0)]
        with _with_env(AI_PROVIDER="anthropic", AI_BASE_URL=llm.url + "/v1", AI_API_KEY="sk-ant", AI_MODEL="claude-x"):
            b = ai.brief(ctx)
            assert (b["source"], b["text"], b["by"]) == ("ai", "Anthropic brief.", "claude-x via Anthropic")
            path, headers, body = llm.seen[0]
            assert path == "/v1/messages" and headers["x-api-key"] == "sk-ant" and body["model"] == "claude-x" and body["system"] and body["temperature"] == 0
    finally:
        llm.close()

    # The sidebar gets the provider through /api/meta
    with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL="http://localhost:11434/v1", AI_MODEL="llama3.2:3b"):
        with TestClient(app) as c:
            m = c.get("/api/meta").json()
            assert m["ai"] is True and m["aiInfo"]["server"] == "Ollama" and m["aiInfo"]["model"] == "llama3.2:3b"


def test_brief_accuracy():
    import json

    from backend import ai
    info = {"name": "Apple Inc.", "currency": "USD", "financialCurrency": "USD", "assetClass": "Stocks", "sector": "Technology",
            "marketCap": 4.98e12, "returnOnEquity": 1.4213, "trailingPE": 38.127, "debtToEquity": 1.03, "dividendYield": 0.0032,
            "operatingMargins": 0.311, "revenueGrowth": 0.117, "targetMeanPrice": 339.04, "recommendationKey": "buy", "beta": None}
    scores = {"composite": 61.4, "stance": "Favorable", "signals": [{"tone": 1, "text": "Long-term uptrend: 50-day average is above the 200-day."}],
              "factors": {"value": 9.0, "quality": 76.6, "growth": 64.0, "momentum": 81.3, "risk": 76.5}}
    news = [{"title": "Apple unveils new iPhone", "publisher": "Reuters", "time": "2026-11-07T10:00:00Z"},
            {"title": "Cellcom Israel stock still looks reasonable after a 192% run", "publisher": "Simply Wall St"},
            {"title": "Market wrap: Nasdaq falls", "summary": "Apple and Microsoft led the decline.", "time": "2026-11-06T21:00:00Z"}]
    ctx = {"ticker": "AAPL", "info": info, "stats": {"ret1y": 0.2738, "vol1y": 0.2484, "maxDD1y": -0.138, "sharpe1y": 0.94},
           "scores": scores, "news": news, "financials": [{"year": "2025", "revenue": 391.04e9, "netIncome": None, "fcf": 108.8e9}], "price": 342.37}

    # 1. Figures arrive in their units, nothing raw and nothing null
    data = ai.brief_data(ctx)
    flat = json.dumps(data, ensure_ascii=False)
    assert data["Profitability and balance sheet"]["Return on equity"] == "142.1%" and data["Valuation"]["P/E, trailing 12 months"] == "38.1x"
    assert data["Company"]["Market capitalisation"] == "4.98T USD" and data["Dividend"]["Dividend yield"] == "0.32%"
    assert data["Price"]["Target vs last price"] == "-1.0%" and data["Price performance and risk"]["Largest fall from a peak, 1 year"] == "-13.8%"
    assert data["Annual accounts, oldest first"] == [{"Year": "2025", "Revenue": "391.04B USD", "Free cash flow": "108.80B USD"}]
    assert "1.4213" not in flat and "null" not in flat and "Beta" not in flat, "missing figures are left out"
    # 2. What the scores mean, with risk spelled out
    assert "higher means calmer, so LOWER risk" in data["What the scores mean"] and data["StockPilot scores"]["Risk factor"] == "76.5 out of 100"
    # 4. Only headlines that name the company, with their dates
    assert data["Recent headlines naming the company"] == ["2026-11-07 Reuters: Apple unveils new iPhone", "2026-11-06 : Market wrap: Nasdaq falls"]
    assert ai.relevant_headlines([{"title": "Saudi stocks rise"}], "2222.SR", "Saudi Arabian Oil Company") == [], "a country name alone isn't the company"
    assert ai.brief_data({**ctx, "news": news[1:2]})["Recent headlines naming the company"] == "None of the recent headlines name the company."

    # 5. The check: figures must be in the data, in the same unit, allowing for rounding and for "fell" instead of a minus
    facts = json.dumps(data, ensure_ascii=False)
    good = ("ROE is 142.1%, P/E about 38x, a $4.98 trillion company, revenue of $391 billion. It fell 13.8% from its peak; "
            "volatility 24.84%. Risk score 76.5 out of 100. The S&P 500 and the 200-day average, 3 risks, in 2025.")
    assert _core(ai.check_figures(good, facts)) == {"checked": 8, "unmatched": []}, ai.check_figures(good, facts)
    # Written with a non-breaking hyphen and narrow spaces, as some models do
    fancy = "The 50\u2011day average is above the 200\u2011day; the target implies \u20111.0\u202f% and ROE is 142.1\u202f%."
    assert _core(ai.check_figures(fancy, facts)) == {"checked": 2, "unmatched": []}, ai.check_figures(fancy, facts)
    bad = "ROE of 1.42, P/E of 3.8x, margins of 27.6%, and growth well above its 5-year average of 8%."
    assert ai.check_figures(bad, facts)["unmatched"] == ["1.42", "3.8x", "27.6%", "8%"]

    # 3. The rules travel with the prompt, and the result reaches the page
    # Trigger levels are the model's own suggestions, kept apart from figures claimed to be data
    trig = "ROE is 142.1%.\n\n**What would change the view**\n- Revenue growth falling below 10%.\n- ROE dropping from 142.1%."
    assert _core(ai.check_brief(trig, facts)) == {"checked": 3, "unmatched": [], "suggested": ["10%"]}
    llm = _FakeLLM([_chat("**Snapshot** ROE is 142.1% but the P/E of 45x is high.")])
    try:
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m"):
            b = ai.brief(ctx)
        prompt = llm.seen[0][2]["messages"][1]["content"]
        assert "never convert units or calculate new figures" in prompt and '"Return on equity": "142.1%"' in prompt
        assert _core(b["check"]) == {"checked": 2, "unmatched": ["45x"], "suggested": []}
    finally:
        llm.close()


def test_thinking_and_cloud_setup():
    import ssl
    import tempfile

    from backend import ai, main
    ctx = {"ticker": "AAPL", "info": {"name": "Apple"}, "stats": {}, "news": [], "financials": [],
           "scores": {"factors": {"value": 50}, "composite": 60, "stance": "Favorable", "signals": []}}
    groq = dict(AI_PROVIDER="openai_compatible", AI_BASE_URL="https://api.groq.com/openai/v1", AI_MODEL="openai/gpt-oss-120b")

    # The example key from .env.example counts as not set up, so nothing is sent and the sidebar says why
    with _with_env(**groq, AI_API_KEY="my_api_key", AI_THINKING="on"):
        d = ai.describe()
        assert not d["on"] and "placeholder 'my_api_key'" in d["problem"] and d["server"] == "Groq" and ai.config()["timeout"] == 90
        assert ai.brief(ctx)["source"] == "rules"

    # Thinking on: the provider's own reasoning, with six times the room so it can't use up the reply;
    # an effort level is passed through; off still sends "none"; unset sends nothing
    llm = _FakeLLM([_chat("A."), _chat("B."), _chat("C."), _chat("D.")])
    try:
        for thinking, effort, room in (("on", None, 900 * 6), ("high", "high", 900 * 6), ("off", "none", 900), ("", None, 900)):
            with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_API_KEY="gsk_x", AI_THINKING=thinking):
                assert ai.brief(ctx)["source"] == "ai"
            body = llm.seen[-1][2]
            assert body.get("reasoning_effort") == effort and body["max_tokens"] == room, (thinking, body.get("reasoning_effort"), body["max_tokens"])
    finally:
        llm.close()

    # Windows: the app trusts what Windows trusts, so antivirus HTTPS scanning doesn't break Yahoo or the AI provider,
    # and a certificate file you chose yourself is kept
    if os.name == "nt":
        bundle = Path(tempfile.mkdtemp()) / "ca.pem"
        before = os.environ.pop("SSL_CERT_FILE", None)
        try:
            assert main._trust_windows_certificates(bundle) == bundle and os.environ["SSL_CERT_FILE"] == str(bundle)
            text = bundle.read_text(encoding="ascii")
            import certifi
            roots = [der for der, enc, trust in ssl.enum_certificates("ROOT") if enc == "x509_asn" and (trust is True or main.SERVER_AUTH in trust)]
            own = Path(certifi.where()).read_text(encoding="ascii").count("BEGIN CERTIFICATE")
            mids = main._chained_intermediates(roots, ssl.enum_certificates("CA"))
            assert text.count("BEGIN CERTIFICATE") == own + len(roots) + len(mids), "certifi's list, the Windows roots for websites, and intermediates chaining to them"
            # No root Windows trusts only for something else (code signing, email), and no intermediate without a trusted root
            others = [der for der, enc, trust in ssl.enum_certificates("ROOT") if enc == "x509_asn" and trust is not True and main.SERVER_AUTH not in trust]
            others += [der for der, enc, _ in ssl.enum_certificates("CA") if enc == "x509_asn" and der not in roots and der not in mids]
            assert not any(ssl.DER_cert_to_PEM_cert(der) in text for der in others if ssl.DER_cert_to_PEM_cert(der) not in Path(certifi.where()).read_text(encoding="ascii"))
            os.environ["SSL_CERT_FILE"] = "C:/mine.pem"
            assert main._trust_windows_certificates(bundle) is None and os.environ["SSL_CERT_FILE"] == "C:/mine.pem"
        finally:
            os.environ.pop("SSL_CERT_FILE", None)
            if before:
                os.environ["SSL_CERT_FILE"] = before


def test_stock_splits():
    from backend import data, db, insights, ledger
    # A reverse split shrinks the share count; a split on the trade's own date has already happened
    assert ledger.split_factor([("2024-06-10", 10.0)], "2024-06-07") == 10 and ledger.split_factor([("2024-06-10", 10.0)], "2024-06-10") == 1
    assert ledger.split_factor([("2020-01-01", 4.0), ("2024-06-10", 10.0)], "2019-05-01") == 40 and ledger.split_factor([("2024-01-01", 0.1)], "2023-01-01") == 0.1

    _reset()
    px = _steps([0.0] * 300) * 1.2  # Yahoo's prices are split-adjusted: 120 throughout
    d = [px.index[i].date().isoformat() for i in (50, 100, 150, 200)]  # bought, split, bought again, sold
    real_splits = data.splits
    data.splits = lambda t: [(d[1], 10.0)] if t in ("NVDA", "AMD") else []
    try:
        with TestClient(app) as c, _FakeMarket({"NVDA": px, "AMD": px}):
            tx = lambda **k: c.post("/api/transactions", json={"ticker": "NVDA", **k})
            assert tx(kind="buy", date=d[0], shares=10, price=1200).status_code == 200  # before the split
            assert tx(kind="buy", date=d[2], shares=5, price=130).status_code == 200    # after it
            r = tx(kind="sell", date=d[3], shares=50, price=140)
            assert r.status_code == 200, "50 of the 100 post-split shares, not more than the 10 entered: " + r.text
            p = c.get("/api/portfolio").json()
            pos = p["positions"][0]
            assert abs(pos["shares"] - 55) < 1e-9 and abs(pos["cost"] - (50 * 120 + 5 * 130)) < 1e-6, pos
            assert abs(pos["value"] - 55 * 120) < 1e-6 and abs(p["realised"] - 50 * (140 - 120)) < 1e-6
            assert [round(x["shares"], 6) for x in pos["lots"]] == [50, 5] and pos["lots"][0]["split"] == 10 and pos["lots"][1]["split"] == 1
            # The chart: a flat price is a flat line, before and after the split (unadjusted, the holding went negative)
            perf = p["performance"]["portfolio"]
            assert max(perf) - min(perf) < 1e-6, (min(perf), max(perf))
            assert not any("split" in w for w in p["warnings"])
            # An undated lot can't be placed either side of the split, so it's left as entered, with a note
            assert c.post("/api/transactions", json={"ticker": "AMD", "kind": "buy", "shares": 1, "price": 100}).status_code == 200
            notes = [w for w in c.get("/api/portfolio").json()["warnings"] if "has split" in w]
            assert len(notes) == 1 and notes[0].startswith("AMD has split") and "no date" in notes[0], notes

            # Journal: an entry at 1,200 with a stop at 1,000, written before the split, is 120 and 100 in today's prices
            ts = __import__("datetime").datetime.fromisoformat(d[0]).timestamp()
            db.x("INSERT INTO journal (ts, ticker, action, price, stop_price) VALUES (?, 'NVDA', 'buy', 1200, 1000)", (ts,))
            j = db.q("SELECT * FROM journal")[0]
            r = insights._entry_result({**j, "stance": None, "score": None, "benchmark": None, "outcome": "", "target_price": None}, "NVDA")
            assert abs(r["returnSince"]) < 1e-9 and r["status"] == "Open", r
    finally:
        data.splits = real_splits
        _reset()

    # Fetching: none in demo mode, and a failed fetch isn't remembered, so it's tried again
    assert data.splits("AAPL") == []
    calls = []

    class Broken:
        @property
        def splits(self):
            calls.append(1)
            raise RuntimeError("rate limited")

    class YF:
        Ticker = staticmethod(lambda t: Broken())
    real_yf, real_demo = data._yf, data.DEMO
    data._yf, data.DEMO = (lambda: YF), False
    try:
        assert data.splits("XYZ") == [] and data.splits("XYZ") == [] and len(calls) == 2
    finally:
        data._yf, data.DEMO = real_yf, real_demo


def test_dividends_are_in_the_currency_paid():
    from backend import data
    assert data.main_unit("GBp") == "GBP" and data.main_unit("KWF") == "KWD" and data.main_unit("EUR") == "EUR"
    px = _steps([0.0] * 300) * 0.7
    for base, ticker, quote in (("GBP", "VOD.L", "GBp"), ("KWD", "NBK.KW", "KWF")):
        _reset()
        with TestClient(app) as c, _FakeMarket({ticker: px}, {ticker: {"currency": quote}}):
            c.post("/api/settings", json={"base_currency": base})
            day = px.index[100].date().isoformat()
            assert c.post("/api/transactions", json={"ticker": ticker, "kind": "buy", "date": day, "shares": 100, "price": 70}).status_code == 200
            assert c.post("/api/transactions", json={"ticker": ticker, "kind": "dividend", "date": px.index[200].date().isoformat(), "amount": 40}).status_code == 200
            p = c.get("/api/portfolio").json()
            # 40 pounds (dinars) received is 40 in the base, not 0.40 (0.04) as if it were pence (fils)
            assert abs(p["dividends"] - 40) < 1e-9, (ticker, p["dividends"])
            # Prices stay in the quote unit: 100 shares at 70 pence are worth 70 pounds, at 70 fils 7 dinars
            divisor = data.MINOR_UNITS[quote][1]
            assert abs(p["positions"][0]["value"] - 100 * float(px.iloc[-1]) / divisor) < 1e-9, p["positions"][0]["value"]
    _reset()


def test_core_satellite_uses_the_whole_history():
    _reset()
    spy = _steps([0.0008] * 600)  # about two and a half years, ending 2026-06-01
    aapl = _steps([0.02, -0.015] * 300)
    d0 = spy.index[50]  # bought well over a year before the end
    day = d0.date().isoformat()
    with TestClient(app) as c, _FakeMarket({"SPY": spy, "AAPL": aapl}):
        for t in ("SPY", "AAPL"):
            assert c.post("/api/transactions", json={"ticker": t, "kind": "buy", "date": day, "shares": 10, "price": 100}).status_code == 200
        perf = c.get("/api/portfolio").json()["coreSatellite"]["performance"]
        # From the real first day both were held, not from a year ago, and over the whole period
        assert perf["start"] == day and perf["dates"][0] == day and not perf["pastYearOnly"], perf["start"]
        assert abs(perf["core"] - (spy.iloc[-1] / spy[d0] - 1)) < 1e-9 and abs(perf["satellite"] - (aapl.iloc[-1] / aapl[d0] - 1)) < 1e-9
        assert len(perf["dates"]) == len(perf["coreLine"]) == len(perf["satelliteLine"]) == len(spy[spy.index >= d0])
    _reset()
    # With no dated purchase there's no start to go by: the past year, and it says so
    with TestClient(app) as c, _FakeMarket({"SPY": spy, "AAPL": aapl}):
        for t in ("SPY", "AAPL"):
            assert c.post("/api/transactions", json={"ticker": t, "kind": "buy", "shares": 10, "price": 100}).status_code == 200
        perf = c.get("/api/portfolio").json()["coreSatellite"]["performance"]
        assert perf["pastYearOnly"] and (spy.index[-1] - __import__("pandas").Timestamp(perf["start"])).days <= 366, perf["start"]
    _reset()


def test_risk_includes_currency_moves():
    import pandas as pd

    from backend import data
    from backend import indicators as ind
    _reset()
    flat = _steps([0.0] * 300)  # a US fund whose price never moves
    eur_per_usd = pd.Series([0.9 + 0.02 * ((i % 5) - 2) for i in range(len(flat))], index=flat.index)  # the rate does
    real = data.fx_series
    data.fx_series = lambda cur, base, index: eur_per_usd.reindex(index).ffill() if (cur, base) == ("USD", "EUR") else real(cur, base, index)
    try:
        with TestClient(app) as c, _FakeMarket({"SPY": flat}):
            c.post("/api/settings", json={"base_currency": "EUR"})
            assert c.post("/api/transactions", json={"ticker": "SPY", "kind": "buy", "shares": 10, "price": 100}).status_code == 200
            risk = c.get("/api/portfolio").json()["risk"]
        # For a euro investor this was risky, all of it from the dollar: the same volatility as the rate itself
        assert risk["volatility"] > 0.05, risk
        assert abs(risk["volatility"] - ind.volatility(ind.window(eur_per_usd, 252))) < 1e-9, (risk["volatility"], ind.volatility(eur_per_usd))
    finally:
        data.fx_series = real
        _reset()


def test_scorecard_counts_dividends_on_both_sides():
    import datetime as dt

    import pandas as pd

    from backend import data, db, insights
    _reset()
    flat = _steps([0.0] * 300)  # adjusted closes: 100 throughout, for the stock and the benchmark
    entry_day = flat.index[100]
    # 4% paid out since the entry: Yahoo's adjusted price on that day is 0.96 of what was actually paid
    ratio = pd.Series([0.96 if d <= flat.index[150] else 1.0 for d in flat.index], index=flat.index)
    real = data.dividend_factor
    data.dividend_factor = lambda t: ratio if t == "XOM" else None
    try:
        with _FakeMarket({"XOM": flat, "SPY": flat}):
            db.x("INSERT INTO journal (ts, ticker, action, price) VALUES (?, 'XOM', 'buy', 104.1667)",
                 (dt.datetime.combine(entry_day.date(), dt.time(12)).timestamp(),))
            j = {**db.q("SELECT * FROM journal")[0], "stance": None, "score": None, "benchmark": "SPY", "outcome": "", "target_price": None, "stop_price": None}
            r = insights._entry_result(j, "SPY")
        # Paid 104.17, now 100 plus about 4.17 of dividends: even, like the flat benchmark. Before, it read -4%.
        assert abs(r["returnSince"]) < 1e-4 and abs(r["excess"]) < 1e-4, r
    finally:
        data.dividend_factor = real
        _reset()
    assert data.dividend_factor("XOM") is None, "no adjustment in demo mode"


def test_minor_unit_currencies():
    """Kuwaiti prices are in fils, 1/1000 of a dinar. 870 fils is 0.87 KWD."""
    from backend import data
    from backend.universe import currency_guess
    kwd = data.DEMO_FX["KWD"]
    assert abs(data.fx_rate("KWF", "USD") - kwd / 1000) < 1e-9
    assert abs(data.fx_rate("GBp", "USD") - data.DEMO_FX["GBP"] / 100) < 1e-9
    assert currency_guess("NBK.KW") == "KWF"
    nbk = data._normalize_info("NBK.KW", {"currency": "KWF", "financialCurrency": "KWD", "quoteType": "EQUITY"})
    assert nbk["statementFx"] == 1.0


def start_close(df, start):
    return df["Close"][df.index >= start].iloc[0]


def test_backtest_uses_history_before_the_window():
    """A 1y test must be judged on the 1y window with indicators already warmed up on earlier data."""
    import pandas as pd

    from backend import backtest
    idx = pd.bdate_range("2021-01-01", periods=800)
    df = pd.DataFrame({"Close": [100 * 1.001 ** i for i in range(800)]}, index=idx)
    start = idx[-252]
    r = backtest.run(df, "sma_cross", {}, 10, 0.04, start=start)
    # A steady uptrend is above its 200-day average from the first day of the window
    assert r["strategy"]["exposure"] == 1.0
    assert r["dates"][0] == start.strftime("%Y-%m-%d") and len(r["dates"]) == 252
    # Fully invested from day one, with the same 10 bps entry cost as buy & hold, so they match
    assert abs(r["strategy"]["totalReturn"] - r["benchmark"]["totalReturn"]) < 1e-9
    assert abs((1 + r["benchmark"]["totalReturn"]) / (df["Close"].iloc[-1] / start_close(df, start)) - 0.999) < 1e-9
    # Nothing changes position inside the window except that first entry
    assert r["strategy"]["trades"] == 1
    # Signals only use past prices: the same test without the earlier data has to sit in cash for 200 bars
    cold = backtest.run(df.loc[start:], "sma_cross", {}, 10, 0.04)
    assert cold["strategy"]["exposure"] < 0.25


def _tool_call(*calls):
    """A reply asking for tools, OpenAI style: calls are (name, args)."""
    import json
    return (200, {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]}}]}, 0)


def test_ask_reaches_more_yahoo_data():
    import json

    from backend import agent, data
    run = lambda name, **a: (lambda r: (json.loads(r[0]), r[1]))(agent.run_tool(name, a))
    # Analysts: targets and ratings for a company; none for a fund
    a, ok = run("get_analysts", ticker="AAPL")
    assert ok and set(a["price_targets"]) == {"low", "mean", "median", "high"} and sum(a["ratings_now"].values()) > 0 and a["mean_target_vs_price"].endswith("%")
    assert "no analyst ratings" in run("get_analysts", ticker="SPY")[0]["analysts"]
    # Earnings: past quarters with the surprise worked out from reported and expected
    e, _ = run("get_earnings", ticker="AAPL")
    q = e["past_quarters_reported_vs_expected"][0]
    assert abs(float(q["eps_reported"]) / float(q["eps_expected"]) - 1 - float(q["surprise"].rstrip("%")) / 100) < 0.01
    # Dividends: the years add up to every payment, and the past 12 months match the income estimate's figure
    from backend.universe import UNIVERSE
    payer = next(t for t in UNIVERSE["North America"] if data.dividend_payments(t))
    pays = data.dividend_payments(payer)
    d, _ = run("get_dividends", ticker=payer)
    years = {y: sum(v for day, v in pays if day.startswith(y)) for y in {p[0][:4] for p in pays}}
    assert d["total_per_year"] and all(abs(float(v.split()[0].replace(",", "")) - years[y]) < 1e-3 for y, v in d["total_per_year"].items())
    # Price history: the return is end over start, inside the dates asked for; dates before the history say so
    h, _ = run("get_price_history", ticker="AAPL", start="2024-01-01", end="2024-12-31")
    c = data.history("AAPL", "max")["Close"]
    w = c[(c.index >= "2024-01-01") & (c.index <= "2024-12-31")]
    assert h["from"] >= "2024-01-01" and h["to"] <= "2024-12-31" and h["return"].split()[0] == f"{(w.iloc[-1] / w.iloc[0] - 1) * 100:.1f}%" and h["return"].endswith("(a rise)" if w.iloc[-1] >= w.iloc[0] else "(a fall)")
    assert "starts on" in run("get_price_history", ticker="AAPL", start="1900-01-01", end="1900-12-31")[0]["error"]
    # A fund's contents; a stock isn't a fund; the markets give each index's day, month and year
    f, _ = run("get_fund", ticker="SPY")
    assert f["sectors"] and f["top_holdings"] and "isn't a fund" in run("get_fund", ticker="AAPL")[0]["error"]
    m, _ = run("get_market")
    assert len(m["markets"]) >= 10 and all({"level", "day", "month", "as_of"} <= set(v) and "year" not in v for v in m["markets"].values())
    assert "not year to date" in m["note"], "the past 12 months, said plainly, so it isn't read as year to date"
    # A symbol Yahoo doesn't know comes back with what a search finds, so the model can try the right one
    with _FakeMarket({"AAPL": _steps([0.001] * 300)}):
        real_search = agent.search
        agent.search = lambda text: {"results": [{"ticker": "IWDA.AS", "name": "iShares Core MSCI World", "market": "ETF · AMS"}]}
        try:
            miss, _ = run("get_fund", ticker="IWDA.DE")
            assert "IWDA.DE" in miss["error"] and miss["did_you_mean"][0].startswith("IWDA.AS"), miss
            assert agent.run_tool("get_fund", {"ticker": "IWDA.DE"})[1] is False, "shown as a failed look-up"
            assert "did_you_mean" in run("get_stock", ticker="IWDA.DE")[0]
        finally:
            agent.search = real_search
    # Every tool is offered to the model, all of them reading only
    assert {"get_analysts", "get_earnings", "get_dividends", "get_price_history", "get_fund", "get_market"} <= set(agent.TOOLS)


def test_intermediates_must_chain_to_a_trusted_root():
    from backend import main
    names = {b"root": ("Root", "Root"), b"sub": ("Sub", "Root"), b"issuing": ("Issuing", "Sub"), b"stray": ("Stray", "Unknown"), b"self": ("Self", "Self")}
    real = main._cert_names
    main._cert_names = lambda der: names[der]
    try:
        store = [(d, "x509_asn", True) for d in (b"issuing", b"stray", b"sub", b"self")]
        # Found through two levels, in any order; a stray one, or a self-signed one in the intermediate store, is left out
        assert sorted(main._chained_intermediates([b"root"], store)) == [b"issuing", b"sub"]
        assert main._chained_intermediates([b"root"], [(b"sub", "x509_asn", {"1.3.6.1.5.5.7.3.3"})]) == [], "a code-signing-only intermediate isn't kept"
    finally:
        main._cert_names = real


def _core(check):
    """A figure check without its list of worked-out figures, which has its own test."""
    return {k: v for k, v in check.items() if k != "derived"}


def test_figure_check_reads_scaled_amounts():
    from backend import ai
    facts = "Free cash flow 13.98B EUR; new money 8,454.00 EUR; P/E 9.2x"
    ok = lambda t: ai.check_figures(t, facts)["unmatched"] == []
    # Rounded forms of figures in the data match; a different figure doesn't
    assert ok("FCF €13.98 bn") and ok("about €8.5k") and ok("€8,454") and ok("P/E 9.2×")
    assert not ok("about €9.5k") and not ok("a 37.5% rise") and not ok("€2.5 bn")
    # Worked out only from figures written near it, and only by arithmetic that means something: a fund cost a model
    # brought in ("typically ~0.22%") passed as "10.1% of 2.2%", two figures from other rows of a table
    data = "Industrials 10.1%; Amazon 2.2%; EUR 8%; satellite 5.6%; 2,000; 33,816.51; price 100; price 125"
    table = "| Sector | Industrials 10.1% |\n| Amazon | 2.2% |\n| Cost | typically ~0.22% |\n| Others | EUR 8%, others 21% |"
    assert ai.check_figures(table, data)["unmatched"] == ["0.22%", "21%"]
    assert ai.check_figures("Amazon is 2.2% and industrials 10.1%, so 0.22% of it", data)["unmatched"] == ["0.22%"], "a percentage of a percentage"
    assert ai.check_figures("Satellite is 5.6%, so core is 94.4%.", data)["derived"] == [{"figure": "94.4%", "how": "100% − 5.6%"}]
    assert ai.check_figures("From 100 to 125 is a rise of 25%.", data)["derived"] == [{"figure": "25%", "how": "change from 100 to 125"}]
    # An index's level after its name is a figure ("Nasdaq 24,579.43"); its name's number isn't ("S&P 500");
    # a narrow or no-break space between digit groups is a thousands separator, as some models write it
    levels = "Nasdaq 24,579.43; DAX 26,089.93"
    assert _core(ai.check_figures("Nasdaq 24,579.43, the S&P 500 and the Nikkei 225", levels)) == {"checked": 1, "unmatched": []}
    assert ai.check_figures("DAX 26 089.93 and 26 089.93", levels)["unmatched"] == []
    # A date written with the month's name isn't a figure; the same number as a quantity is
    assert _core(ai.check_figures("Lowest $11.19 (Oct 14), from $29.99 (Jan 3, 2022), on 14 October", "11.19 on 2022-10-14; 29.99")) == {"checked": 2, "unmatched": []}
    assert ai.check_figures("it fell 14 points", "11.19")["unmatched"] == ["14"]


def test_ask_uses_the_tools_and_checks_its_figures():
    import json

    from backend import agent, db
    _reset()
    db.x("INSERT INTO transactions (ticker, kind, date, shares, price) VALUES ('VWCE.DE', 'buy', '2025-01-06', 10, 100)")
    before = db.q("SELECT * FROM transactions")
    llm = _FakeLLM([])
    try:
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m"), TestClient(app) as c:
            # Round 1 asks for two tools at once; round 2 answers, quoting a figure from a result and one it made up
            stock = json.loads(agent.run_tool("get_stock", {"ticker": "AAPL"})[0])
            score = stock["StockPilot scores"]["Composite"].split()[0]  # "30.2 out of 100"
            # The latest day's move, dated, so "did it move today" has an answer
            day = stock["Latest trading day"]
            assert set(day) == {"date", "price (the close, or the latest price while trading)", "change from the day before", "is today"} and day["change from the day before"].endswith(")"), day
            llm.replies = [_tool_call(("get_portfolio", {}), ("get_stock", {"ticker": "AAPL"})),
                           _chat(f"Apple scores {score}. It could rise 37.5% next year."),
                           _chat(f"Apple scores {score}. It could rise 37.5% or 50% next year.")]  # a revision that's no better
            r = c.post("/api/ask", json={"question": "Should I add Apple?"})
            assert r.status_code == 200, r.text
            out = r.json()
            assert [s["tool"] for s in out["steps"]] == ["get_portfolio", "get_stock"] and all(s["ok"] for s in out["steps"]) and out["calls"] == 3
            # The unsupported figure went back to the model once, in a fresh conversation with the findings; a revision
            # with as many unsupported figures or more is thrown away
            fix = llm.seen[2][2]
            assert "tools" not in fix and "37.5%" in fix["messages"][1]["content"] and "What the look-ups found" in fix["messages"][1]["content"]
            assert out["answer"].endswith("rise 37.5% next year.") and out["revised"] is None
            # What the model was sent: the tools on offer, then both results back under their call ids
            first, second = llm.seen[0][2], llm.seen[1][2]
            # The model is told today's date, which it can't know otherwise
            import datetime as dt
            assert first["messages"][1]["content"].startswith(f"(Today is {dt.date.today():%A %d %B %Y}.)") and first["messages"][1]["content"].endswith("Should I add Apple?")
            assert {t["function"]["name"] for t in first["tools"]} == set(agent.TOOLS) - {"web_search"} and first["tool_choice"] == "auto"
            back = [m for m in second["messages"] if m["role"] == "tool"]
            assert [m["tool_call_id"] for m in back] == ["c0", "c1"] and "VWCE.DE" in back[0]["content"]
            # The figure check: the score is in the data, the forecast isn't
            assert out["check"]["unmatched"] == ["37.5%"] and out["check"]["checked"] >= 2, out["check"]
            # A revision that corrects it is kept, and says how many it fixed
            llm.seen.clear()
            llm.replies = [_tool_call(("get_stock", {"ticker": "AAPL"})), _chat(f"Apple scores {score}. It could rise 37.5% next year."),
                           _chat(f"Apple scores {score}. The data gives no forecast.")]
            out = c.post("/api/ask", json={"question": "Should I add Apple?"}).json()
            assert out["answer"].endswith("gives no forecast.") and out["check"]["unmatched"] == [] and out["revised"] == {"before": ["37.5%"], "fixed": 1}, out
            # An answer whose figures all check out isn't sent back
            llm.seen.clear()
            llm.replies = [_chat(f"A score of {score} is below the middle.")]
            out = c.post("/api/ask", json={"question": f"Apple scores {score}. Is that good?"}).json()
            assert len(llm.seen) == 1 and out["revised"] is None
            # A tool that fails says why, and the model is told; the round limit then forces an answer
            llm.seen.clear()
            llm.replies = [_tool_call(("get_stock", {"ticker": "NOPE IS NOT A SYMBOL"}))] * agent.MAX_ROUNDS + [_chat("I couldn't find that symbol.")]
            out = c.post("/api/ask", json={"question": "Tell me about nope"}).json()
            assert len(out["steps"]) == agent.MAX_ROUNDS and not out["steps"][0]["ok"] and "isn't a ticker symbol" in out["steps"][0]["error"]
            last = llm.seen[-1][2]
            # The answer round offers no tools at all and says to answer (told "none", a model still asked for one)
            assert out["calls"] == agent.MAX_ROUNDS + 1 and "tools" not in last and last["messages"][-1]["content"] == agent.ANSWER_NOW and out["answer"] == "I couldn't find that symbol."
            # A malformed tool request the provider can't read is asked again once, and the second answer is used
            llm.seen.clear()
            llm.replies = [(400, {"error": {"message": "Parsing failed. The model generated output that could not be parsed.", "code": "tool_use_failed"}}, 0),
                           _chat("Second time lucky.")]
            out = c.post("/api/ask", json={"question": "Anything?"}).json()
            assert out["answer"] == "Second time lucky." and len(llm.seen) == 2, out
            # A day's allowance used up is said plainly, with when it comes back; it isn't waited out
            llm.seen.clear()
            daily = (429, {"error": {"message": "Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 199009, Requested 1783.", "code": "rate_limit_exceeded"}}, 0)
            llm.replies = [daily]
            r = c.post("/api/ask", json={"question": "Anything?"})
            assert r.status_code == 503 and "Today's AI allowance is used up (200,000 tokens a day" in r.json()["detail"] and len(llm.seen) == 1, r.text
            # Refused twice after a look-up: a fresh conversation with the question and what was found, and no tools
            llm.seen.clear()
            refused = (400, {"error": {"message": "Tool choice is none, but model called a tool"}}, 0)
            llm.replies = [_tool_call(("get_portfolio", {})), refused, refused, _chat("Fresh answer.")]
            out = c.post("/api/ask", json={"question": "What do I hold?"}).json()
            fresh = llm.seen[-1][2]
            assert out["answer"] == "Fresh answer." and out["steps"][0]["tool"] == "get_portfolio" and "tools" not in fresh
            assert [m["role"] for m in fresh["messages"]] == ["system", "user"] and "What the look-ups found" in fresh["messages"][1]["content"] and "VWCE.DE" in fresh["messages"][1]["content"]
            # Nothing the tools did changed your data; another website can't make your browser ask
            assert db.q("SELECT * FROM transactions") == before
            assert c.post("/api/ask", json={"question": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
            assert c.post("/api/ask", json={"question": "   "}).status_code == 400
        # The Anthropic format: tool_use blocks out, tool_result blocks back
        llm.seen.clear()
        llm.replies = [(200, {"content": [{"type": "tool_use", "id": "t1", "name": "get_portfolio", "input": {}}]}, 0),
                       (200, {"content": [{"type": "text", "text": "You hold VWCE.DE."}]}, 0)]
        with _with_env(AI_PROVIDER="anthropic", AI_BASE_URL=llm.url, AI_API_KEY="k", AI_MODEL="claude-x"):
            out = agent.ask("What do I hold?")
        assert out["answer"] == "You hold VWCE.DE." and out["steps"][0]["tool"] == "get_portfolio"
        sent = llm.seen[1][2]["messages"]
        assert sent[-1]["content"][0]["type"] == "tool_result" and sent[-1]["content"][0]["tool_use_id"] == "t1" and llm.seen[0][2]["tools"][0]["input_schema"]
        # With no AI set up, it says so instead of guessing
        with _with_env(AI_PROVIDER="none"), TestClient(app) as c:
            r = c.post("/api/ask", json={"question": "Anything?"})
            assert r.status_code == 503 and "AI" in r.json()["detail"]
    finally:
        llm.close()
        _reset()


def test_portfolio_backtest_matches_independent_sums():
    import pandas as pd

    from backend import backtest, data, simulate
    _reset()
    with TestClient(app) as c:
        # A single fund held throughout, no costs: the same as the single-asset buy & hold
        r = c.post("/api/simulate/backtest", json={"mix": [{"ticker": "SPY", "weight": 100}], "period": "5y", "rebalance": "none",
                                                   "cost_bps": 0, "benchmark": ""}).json()
        b = backtest.run(data.history("SPY", "max"), "buy_hold", {}, 0, 0.04, start=pd.Timestamp(r["start"]))
        assert abs(r["value"][-1] - b["buyHold"][-1]) < 1e-6 and abs(r["portfolio"]["cagr"] - b["benchmark"]["cagr"]) < 1e-9
        # 50/50 rebalanced each year with 20 bps: the same as a separate day-by-day sum
        r = c.post("/api/simulate/backtest", json={"mix": [{"ticker": "SPY", "weight": 50}, {"ticker": "AAPL", "weight": 50}],
                                                   "period": "5y", "rebalance": "yearly", "cost_bps": 20, "benchmark": "QQQ"}).json()
        px, _, _ = simulate.prices(["SPY", "AAPL"], "USD", 5)
        u, yr = 10000 * 0.998 * 0.5 / px.iloc[0], px.index[0].year
        for d, row in px.iterrows():
            if d.year != yr:
                v = (u * row).sum()
                v -= (v * 0.5 - u * row).abs().sum() * 0.002
                u, yr = v * 0.5 / row, d.year
        assert abs(r["value"][-1] - float((u * px.iloc[-1]).sum())) < 1e-6 and r["portfolio"]["rebalances"] == len({d.year for d in px.index}) - 1
        assert r["benchmark"]["ticker"] == "QQQ" and len(r["benchmark"]["growth"]) == len(r["dates"])
        # the growth line starts at 100 less the purchase's cost; each year is marked when it's only part of one
        assert abs(r["growth"][0] - 100 * 0.998) < 1e-9 and r["portfolio"]["yearly"][0]["partial"] and r["portfolio"]["worstYear"]["year"] in [y["year"] for y in r["portfolio"]["yearly"] if not y["partial"]]
        assert any("Hindsight" in x for x in r["caveats"])
        # A mix that is the benchmark gets no second column repeating it
        same = c.post("/api/simulate/backtest", json={"mix": [{"ticker": "SPY", "weight": 100}], "benchmark": "SPY"}).json()
        assert "benchmark" not in same and same["portfolio"]["cagr"] is not None
        # The weights must add up to 100%, and a symbol Yahoo lacks is named
        bad = c.post("/api/simulate/backtest", json={"mix": [{"ticker": "SPY", "weight": 60}, {"ticker": "AAPL", "weight": 30}]})
        assert bad.status_code == 400 and "90.00%" in bad.json()["detail"]
        assert c.post("/api/simulate/backtest", json={"mix": [{"ticker": "SPY", "weight": -5}, {"ticker": "AAPL", "weight": 105}]}).status_code == 400
        assert c.post("/api/simulate/backtest", json={"mix": [{"ticker": "SPY", "weight": 100}], "rebalance": "daily"}).status_code == 400
        # Your holdings as a starting mix, adding up to 100%
        c.post("/api/transactions", json={"ticker": "SPY", "kind": "buy", "date": "2025-01-06", "shares": 10, "price": 100})
        c.post("/api/transactions", json={"ticker": "AAPL", "kind": "buy", "date": "2025-01-06", "shares": 3, "price": 100})
        m = c.get("/api/simulate/mix").json()
        assert {x["ticker"] for x in m["mix"]} == {"SPY", "AAPL"} and abs(sum(x["weight"] for x in m["mix"]) - 100) < 1e-9 and m["base"]
    # New money each month at a flat price: no gain, and every euro counted
    flat = pd.DataFrame({"X": 100.0, "Y": 50.0}, index=pd.bdate_range("2020-01-01", "2022-12-30"))
    value, level, flows, _ = simulate._run(flat, {"X": 0.6, "Y": 0.4}, "none", 0, 1000, 100)
    assert abs(value.iloc[-1] - 4500) < 1e-6 and abs(level.iloc[-1] - 1) < 1e-12 and -sum(a for _, a in flows[:-1]) == 4500  # 1,000 + 35 months
    # ...and it goes first to what's below its target: X doubled in January, so February's 100 all buys Y. X then
    # halves again, which shows where the money went: 5 X at 100 and 12 Y at 50 (an even split would leave 1,075)
    idx = pd.bdate_range("2020-01-01", "2020-02-14")  # ends before March brings more money
    x = [100.0 if d.day < 15 and d.month == 1 else 200.0 for d in idx]
    x = [p if d < pd.Timestamp("2020-02-10") else 100.0 for d, p in zip(idx, x)]
    v, _, _, _ = simulate._run(pd.DataFrame({"X": x, "Y": 50.0}, index=idx), {"X": 0.5, "Y": 0.5}, "none", 0, 1000, 100)
    assert abs(v.iloc[-1] - 1100) < 1e-6, v.iloc[-1]
    _reset()


def test_long_scorings_report_their_progress():
    import logging
    import threading
    from backend import insights
    from backend.universe import tickers_for
    _reset()
    tickers = tickers_for(["Europe", "Asia-Pacific"])
    seen, real = {}, insights._quick_row
    with TestClient(app) as c:
        assert c.get("/api/progress").json() == {"jobs": [], "pause": 0.0}, "nothing under way"

        def slow(t, settings):  # look at the progress from the page's side partway through
            r = real(t, settings)
            if t == tickers[60]:
                seen.update(c.get("/api/progress").json())
            return r
        insights._quick_row = slow
        try:
            rows = insights.rows_for(tickers, _db.get_settings(), workers=1, kind="screener")
        finally:
            insights._quick_row = real
        assert len(rows) == len(tickers)
        [job] = seen["jobs"]
        assert job["kind"] == "screener" and job["total"] == len(tickers) and job["done"] == 60, "counted as each one finishes"
        europe = len(tickers_for(["Europe"]))  # the list's European stocks come first, so the 60 done are all European
        assert job["regions"] == {"Europe": [60, europe], "Asia-Pacific": [0, len(tickers) - europe]}, job["regions"]
        assert job["elapsed"] >= 0 and seen["pause"] == 0
        assert c.get("/api/progress").json()["jobs"] == [], "a finished job is gone"
        # A short list is quick: nothing kept; a failed run still clears its entry
        insights.rows_for(tickers[:5], _db.get_settings())
        assert insights.progress()["jobs"] == []
        insights._quick_row = lambda t, s: 1 / 0
        try:
            try:
                insights.rows_for(tickers[:30], _db.get_settings())
            except ZeroDivisionError:
                pass
        finally:
            insights._quick_row = real
        assert insights.progress()["jobs"] == []
        # The three kinds the page names, from the three places that score long lists
        kinds = []
        orig = insights.rows_for
        insights.rows_for = lambda tk, st, *a, kind="scoring", **k: kinds.append(kind) or orig(tk[:3], st, *a, kind=kind, **k)
        try:
            c.post("/api/screener", json={"markets": ["Europe"]})
            c.get("/api/opportunities?markets=Europe")
            insights.peer_table(_db.get_settings(), fresh=True)
        finally:
            insights.rows_for = orig
        assert {"screener", "opportunities", "ranks"} <= set(kinds), kinds
    # The page's frequent checks stay out of the request log
    rec = lambda path: logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "GET %s HTTP/1.1" 200', ("127.0.0.1", path), None)
    access = logging.getLogger("uvicorn.access")
    assert not access.filter(rec("/api/progress")) and access.filter(rec("/api/meta"))


def test_digest_news_and_every_currency():
    from backend import data, insights
    _reset()
    with TestClient(app) as c:
        # Holding only a fund and watching only a coin: the coin's own news counts; a fund's never does
        c.post("/api/transactions", json={"ticker": "VWCE.DE", "kind": "buy", "date": "2025-01-06", "shares": 10, "price": 100})
        c.post("/api/watchlist", json={"ticker": "BTC-USD"})
        news = [i for i in c.get("/api/digest").json()["items"] if i.get("kind") == "news"]
        assert news and all(i["ticker"] == "BTC-USD" and not i.get("market") for i in news), news
        # With nothing of yours in the news, the market's headlines take its place, marked as such
        c.delete("/api/watchlist/BTC-USD")
        news = [i for i in c.get("/api/digest").json()["items"] if i.get("kind") == "news"]
        assert news and all(i.get("market") and i["ticker"] is None and i["text"].startswith("Markets: ") for i in news), news
        assert len(news) <= insights.MARKET_NEWS_COUNT
        # The converter: every currency in use, and an unknown code refused
        meta = c.get("/api/meta").json()
        assert len(meta["currencies"]) >= 150 and meta["currencies"]["JPY"] == "Japanese yen" and meta["currencies"] == data.CURRENCIES
        for src, dst in (("NGN", "EUR"), ("EUR", "VND"), ("XOF", "BRL")):
            r = c.get(f"/api/fx?src={src}&dst={dst}")
            assert r.status_code == 200 and r.json()["rate"] > 0, (src, dst)
        assert c.get("/api/fx?src=ZZZ&dst=EUR").status_code == 400
    _reset()


def test_regions_you_follow_decide_what_is_scanned():
    from backend import insights
    from backend.universe import REGIONS, UNIVERSE, country_of
    _reset()
    with TestClient(app) as c:
        # All five by default; a choice is checked and kept in the usual order
        assert c.get("/api/settings").json()["regions"] == REGIONS
        for bad in ([], ["Mars"], "Europe"):
            assert c.post("/api/settings", json={"regions": bad}).status_code == 400, bad
        assert c.post("/api/settings", json={"regions": ["Asia-Pacific", "Europe"]}).json()["regions"] == ["Europe", "Asia-Pacific"]
        meta = c.get("/api/meta").json()
        assert meta["markets"] == ["Europe", "Asia-Pacific", "ETFs", "Crypto"] and list(meta["countries"]) == ["Europe", "Asia-Pacific"]
        assert meta["regions"] == REGIONS and "Japan" in meta["countries"]["Asia-Pacific"] and meta["countryGroups"]["Gulf"]
        # The Screener scans only those (a region you don't follow is ignored even when asked for)
        s = c.post("/api/screener", json={"markets": ["Europe", "North America"]}).json()
        assert s["scanned"] == len(UNIVERSE["Europe"]) and {r["region"] for r in s["rows"]} == {"Europe"}
        s = c.post("/api/screener", json={}).json()
        assert s["scanned"] == len(UNIVERSE["Europe"]) + len(UNIVERSE["Asia-Pacific"]) + len(UNIVERSE["ETFs"]) + len(UNIVERSE["Crypto"])
        # Opportunities and the sector table too
        assert c.get("/api/opportunities").json()["markets"] == ["Europe", "Asia-Pacific"]
        settings = c.get("/api/settings").json()
        assert {r["region"] for r in insights.peer_table(settings).values()} == {"Europe", "Asia-Pacific"}
        # A country, or a group of them, narrows the screen and scans only its region
        j = c.post("/api/screener", json={"filters": {"countries": ["Japan"]}}).json()
        assert j["rows"] and {r["country"] for r in j["rows"]} == {"Japan"} and j["scanned"] == len(UNIVERSE["Asia-Pacific"])
        c.post("/api/settings", json={"regions": REGIONS})
        g = c.post("/api/screener", json={"filters": {"countries": ["Gulf"]}}).json()
        assert g["rows"] and {r["country"] for r in g["rows"]} <= {"Qatar", "Saudi Arabia", "UAE", "Kuwait"} and g["scanned"] == len(UNIVERSE["Middle East & Africa"])
        assert all(r["country"] == country_of(r["ticker"]) for r in g["rows"])
    _reset()


def test_insider_trades_count_only_market_buys_and_sales():
    from backend import data, insights
    # Yahoo's descriptions, as they come: only a trade at a price on the market is buying or selling
    kinds = {"Purchase at price 210.15 per share.": "buy", "Acquisition in the public market at price 41.20 per share.": "buy",
             "Sale at price 325.75 per share.": "sell", "Sale at price 342.66 - 347.46 per share.": "sell",
             "Disposition in the public market at price 150.10 per share.": "sell", "": "other", None: "other",
             "Stock Award(Grant) at price 0.00 per share.": "other", "Exercise of options at price 98.00 per share.": "other",
             "Stock Gift at price 0.00 per share.": "other", "Redemption, retraction, cancelation, repurchase at price 1.00 per share.": "other",
             "Buy Back at price 12.00 per share.": "other"}
    assert {t: data.insider_kind(t) for t in kinds} == kinds
    day = lambda n: (pd.Timestamp("2026-11-10") - pd.Timedelta(days=n)).strftime("%Y-%m-%d")
    import pandas as pd
    own = {"insiders": [
        {"date": day(5), "insider": "A", "kind": "sell", "shares": 1000.0, "value": 300000.0},
        {"date": day(20), "insider": "A", "kind": "sell", "shares": 500.0, "value": None},   # no value given: shares still count
        {"date": day(30), "insider": "B", "kind": "sell", "shares": 200.0, "value": 60000.0},
        {"date": day(40), "insider": "C", "kind": "other", "shares": 9000.0, "value": None},  # an award: never buying
        {"date": day(200), "insider": "D", "kind": "buy", "shares": 50.0, "value": 9000.0},   # outside six months
    ]}
    s = insights.insider_summary(own, today="2026-11-10")
    assert (s["buys"], s["sells"], s["sellers"], s["sellShares"], s["sellValue"], s["other"]) == (0, 3, 2, 1700.0, 360000.0, 1)
    assert s["headline"].startswith("Insiders only sold: 3 sales by 2 people") and "routine" in s["headline"]
    only_awards = insights.insider_summary({"insiders": [own["insiders"][3]]}, today="2026-11-10")
    assert "No insider bought or sold on the market" in only_awards["headline"] and "aren't decisions" in only_awards["headline"]
    bought = insights.insider_summary({"insiders": [{**own["insiders"][4], "date": day(3)}]}, today="2026-11-10")
    assert bought["headline"] == "Insiders only bought: 1 purchase by 1 person in the past 6 months."
    assert insights.insider_summary(None)["headline"].startswith("No insider trades reported")
    _reset()
    with TestClient(app) as c:
        o = c.get("/api/stock/AAPL/ownership").json()
        assert o["summary"]["headline"] and o["currency"] == "USD" and o["institutions"] and all(x["kind"] in ("buy", "sell", "other") for x in o["insiders"])
        assert c.get("/api/stock/SPY/ownership").json() is None and c.get("/api/stock/BTC-USD/ownership").json() is None
    from backend import agent
    a = agent.get_ownership("AAPL")
    assert a["insiders_past_6_months"]["summary"] == o["summary"]["headline"] and "awards" in a["note"]
    assert "no ownership data" in agent.get_ownership("SPY")["ownership"] and "get_ownership" in agent.TOOLS


def test_yahoo_refusals_pause_long_lists():
    import logging

    from backend import data, insights
    # yfinance only logs a refusal; the log line starts the pause
    logging.disable(logging.NOTSET)
    try:
        data._limited["until"] = 0.0
        logging.getLogger("yfinance").error("HTTP Error 429: Too Many Requests. Rate limited. Try after a while.")
        assert 100 < data.rate_limited_for() <= data.RATE_PAUSE
        logging.getLogger("stockpilot.data").warning("prices failed for X: timed out")  # other trouble doesn't pause anything
        data._limited["until"] = 0.0
        assert data.rate_limited_for() == 0
    finally:
        logging.disable(logging.WARNING)
    # A long list waits out the pause between batches, within its patience; a short one doesn't batch at all
    calls, slept = [], []
    real = (insights._quick_row, insights.time.sleep, insights.data.rate_limited_for)
    clock = {"left": 30.0}
    insights._quick_row = lambda t, s: calls.append(t) or {"ticker": t}
    insights.time.sleep = lambda s: (slept.append(s), clock.__setitem__("left", max(0.0, clock["left"] - s)))
    insights.data.rate_limited_for = lambda: clock["left"] if len(calls) >= insights.BATCH else 0.0
    try:
        rows = insights.rows_for([f"T{i}" for i in range(120)], {}, patience=60)
        assert len(rows) == 120 and sum(slept) == 30.0 and all(s <= 15 for s in slept)  # paused once, after the first batch
        clock["left"], calls[:], slept[:] = 1000.0, [], []
        insights.rows_for([f"T{i}" for i in range(120)], {}, patience=20)
        assert sum(slept) == 20.0  # gives up waiting at its patience, and carries on
        clock["left"], calls[:], slept[:] = 1000.0, [], []
        insights.rows_for([f"T{i}" for i in range(50)], {})
        assert slept == []
    finally:
        insights._quick_row, insights.time.sleep, insights.data.rate_limited_for = real


def test_sector_table_is_built_in_the_background():
    import threading
    import time

    from backend import data, db, insights
    _reset()
    real = (data.DEMO, insights.peer_table)
    started, release = [], threading.Event()

    def slow_build(settings, fresh=False, patience=60):  # stands in for downloading every followed stock
        if fresh:
            started.append(time.time())
            release.wait(5)
            db.cache_set(insights._peer_key(settings), {"T1": {"sector": "Tech", "region": "US", **{k: 50.0 for k in insights.RANKED[1:]}}})
        return {"T1": {"sector": "Tech", "region": "US", "composite": 50.0, **{k: 50.0 for k in insights.RANKED[1:]}}}
    data.DEMO, insights.peer_table = False, slow_build
    db.cache_clear()  # no table left by an earlier test
    try:
        t0 = time.time()
        assert insights.sector_ranks("T1", {}) == {"pending": True} and time.time() - t0 < 1, "answered at once"
        assert insights.sector_ranks("T1", {}) == {"pending": True}
        time.sleep(0.2)
        assert len(started) == 1, "one build at a time"
        release.set()
        for _ in range(50):
            if insights.peer_table_now({}) is not None:
                break
            time.sleep(0.1)
        r = insights.sector_ranks("T1", {})
        assert r["sector"] == "Tech" and r["others"] == 0 and len(started) == 1
    finally:
        data.DEMO, insights.peer_table = real
        release.set()
        db.cache_clear()  # the stand-in table mustn't be what the next test ranks against
        _reset()


def test_sector_ranks_count_the_peers_beaten():
    from backend import insights
    table = {f"T{i}": {"sector": "Tech", "region": "US" if i % 2 else "Asia-Pacific", "composite": 10.0 * i, "value": 10.0 * i,
                       "quality": None, "growth": 50.0, "momentum": 10.0 * i, "risk": 10.0 * i} for i in range(1, 11)}
    table.update({f"E{i}": {"sector": "Energy", "region": "Europe", "composite": 50.0, "value": 50.0, "quality": 50.0, "growth": 50.0,
                            "momentum": 50.0, "risk": 50.0} for i in range(5)})
    real = insights.peer_table
    insights.peer_table = lambda settings: table
    try:
        # T6 scores 60: the other nine Tech stocks score 10..50 and 70..100, so it beats five of nine
        r = insights.sector_ranks("T6", {})
        assert r["sector"] == "Tech" and r["others"] == 9 and r["regions"] == {"US": 5, "Asia-Pacific": 4}
        assert abs(r["ranks"]["composite"]["beats"] - 5 / 9) < 1e-12 and r["ranks"]["composite"]["of"] == 9 and r["ranks"]["composite"]["median"] == 50.0
        # a tie counts half: every other growth score is 50 like its own
        assert abs(r["ranks"]["growth"]["beats"] - 0.5) < 1e-12
        # no score of its own, or too few peers with one: no rank, but how many there were
        assert r["ranks"]["quality"] == {"beats": None, "of": 0}
        e = insights.sector_ranks("E0", {})
        assert e["others"] == 4 and all(v["beats"] is None and v["of"] == 4 for v in e["ranks"].values())
        # a stock outside the list is ranked against it, from its own scores
        out = insights.sector_ranks("NEW", {}, own={"ticker": "NEW", "assetClass": "Stocks", "sector": "Tech", "composite": 95.0,
                                                   "f_value": 5.0, "f_quality": None, "f_growth": 50, "f_momentum": 100.0, "f_risk": 0.0})
        assert out["others"] == 10 and out["ranks"]["composite"]["beats"] == 0.9 and out["ranks"]["value"]["beats"] == 0.0 and out["ranks"]["momentum"]["beats"] == 0.95
        # funds and crypto have no sector to rank in
        assert insights.sector_ranks("FUND", {}, own={"ticker": "FUND", "assetClass": "ETFs", "sector": None}) is None
    finally:
        insights.peer_table = real
    _reset()
    with TestClient(app) as c:
        r = c.get("/api/stock/AAPL/peers").json()
        assert r["sector"] and r["others"] >= 1 and set(r["ranks"]) == set(insights.RANKED) and r["minPeers"] == insights.MIN_PEERS
        assert c.get("/api/stock/SPY/peers").json() is None
        # The Screener shows each stock's overall rank in its sector; funds and crypto have none
        rows = c.post("/api/screener", json={"markets": ["North America", "ETFs"]}).json()["rows"]
        stock = next(x for x in rows if x["ticker"] == "AAPL")
        assert stock["sectorOf"] == r["ranks"]["composite"]["of"] and (stock["sectorBeats"] is None or abs(stock["sectorBeats"] - r["ranks"]["composite"]["beats"]) < 1e-12)
        assert all(x["sectorBeats"] is None for x in rows if x["assetClass"] != "Stocks")
    # Other weights: the overall score is worked out from the kept factors, the same as scoring the stock afresh
    tilted = {"weights": {"value": 0.5, "quality": 0.1, "growth": 0.1, "momentum": 0.2, "risk": 0.1}}
    for t in ("AAPL", "QNBK.QA"):
        assert insights.peer_table(tilted)[t]["composite"] == insights.rows_for([t], tilted)[0]["composite"], t
    # Ask's stock look-up says where the scores stand in the sector
    from backend import agent
    assert "Within its sector" in agent.get_stock("AAPL") and "Within its sector" not in agent.get_stock("SPY")


def test_monte_carlo_matches_formulas():
    import numpy as np

    from backend import simulate
    # Constant returns: every path is the compound-interest sum, money added at each month's start
    r = 0.005
    v, _ = simulate.project(np.log1p(np.full(60, r)), 1000, 100, 120, paths=200)
    fv = 1000 * (1 + r) ** 120 + 100 * sum((1 + r) ** k for k in range(1, 121))
    assert abs(v[:, -1].min() - fv) < 1e-6 and abs(v[:, -1].max() - fv) < 1e-6
    # Nothing earned: exactly what went in
    v, _ = simulate.project(np.zeros(40), 500, 25, 36, paths=100)
    assert np.allclose(v[:, -1], 1400)
    # An assumed yearly return is what the paths grow at on average, keeping history's ups and downs
    hist = np.log1p(np.random.default_rng(3).normal(0.01, 0.04, 80))
    _, idx = simulate.project(hist, 1000, 0, 240, paths=20000, expected=0.05)
    assert abs(np.expm1(np.log(idx[:, -1]).mean() / 20) - 0.05) < 0.002
    # Blocks are 12 months in a row from history, wrapping round at its end; the same seed gives the same futures
    d = simulate._draw(np.arange(30.0), 24, 5, 12, np.random.default_rng(0))
    assert all(((np.diff(row[:12]) == 1) | (np.diff(row[:12]) == -29)).all() for row in d)
    a, _ = simulate.project(hist, 1000, 50, 60, paths=500, seed=7)
    b, _ = simulate.project(hist, 1000, 50, 60, paths=500, seed=7)
    assert (a == b).all()
    # Whole months only: a part-month at either end isn't a month's return
    import pandas as pd
    level = pd.Series(1.0, index=pd.bdate_range("2020-01-15", "2020-05-12"))
    assert [str(p) for p in simulate.monthly_returns(level).index] == ["2020-02", "2020-03", "2020-04"]
    # A fund with a short history takes an older same-index fund's moves before it, scaled to meet its first price;
    # without stand-ins it starts where its own history does
    days = pd.bdate_range("2010-01-04", "2020-12-31")
    older = pd.Series([50.0 * 1.0003 ** i for i in range(len(days))], index=days)
    own = pd.Series([200.0 * 1.0002 ** i for i in range(len(days[days >= "2019-07-29"]))], index=days[days >= "2019-07-29"])
    with _FakeMarket({"VWCE.DE": own, "VT": older}):
        used = []
        px, _, _ = simulate.prices(["VWCE.DE"], "USD", None, True, used)
        assert px.index[0] == days[0] and abs(px["VWCE.DE"].loc[own.index[0]] - 200.0) < 1e-9
        assert abs(px["VWCE.DE"].iloc[0] / px["VWCE.DE"].loc[own.index[0]] - older.iloc[0] / older.loc[own.index[0]]) < 1e-9  # joined on the fund's first day, so the move across the joint is the stand-in's
        assert used == [{"ticker": "VWCE.DE", "standIn": "VT", "from": "2010-01-04", "until": "2019-07-29"}]
        assert simulate.prices(["VWCE.DE"], "USD")[0].index[0] == own.index[0]
    _reset()
    with TestClient(app) as c:
        body = {"mix": [{"ticker": "SPY", "weight": 60}, {"ticker": "AAPL", "weight": 40}], "years": 10, "start_value": 10000,
                "monthly": 200, "paths": 2000, "inflation": 0.02, "goal": 30000}
        out = c.post("/api/simulate/future", json=body).json()
        bands = [out["nominal"][str(p)][-1] for p in simulate.PERCENTILES]
        assert bands == sorted(bands) and len(out["years"]) == 11 and out["invested"][-1] == 10000 + 120 * 200
        assert out["real"]["50"][-1] < out["nominal"]["50"][-1] and 0 <= out["final"]["reachGoal"] <= 1 and out["assumption"]["months"] >= 36
        assert out["assumption"]["usedYearly"] == out["assumption"]["historicalYearly"] and len(out["caveats"]) == 4
        # Assuming a lower return moves the whole range down; history's swings stay
        low = c.post("/api/simulate/future", json={**body, "expected": -0.02}).json()
        assert low["final"]["median"] < out["final"]["median"] or out["assumption"]["historicalYearly"] < -0.02
        assert abs(low["assumption"]["historicalVolatility"] - out["assumption"]["historicalVolatility"]) < 1e-12
        for bad in ({"years": 0}, {"paths": 10}, {"expected": 0.9}, {"inflation": -0.01}):
            assert c.post("/api/simulate/future", json={**body, **bad}).status_code == 400, bad
    _reset()


def test_ask_checks_quotes_and_search_years():
    import datetime as dt
    import json

    from backend import agent, ai
    # Quotes must be the source's words: a rewritten headline in quotation marks is caught; real ones, a headline
    # joined to its summary, and typographic punctuation pass
    facts = json.dumps({"headlines": ["Zacks: Novo Up 4% in a Month: How Should Investors Approach the Stock Now?",
                                      "GuruFocus.com: Novo Nordisk Reveals Surprising New Benefit for Wegovy — The blockbuster treatment could reach an unexpected patient population.",
                                      "Motley Fool: Bank of America Is Doubling Down on Apple. Here’s Its Price Target"]}, ensure_ascii=False)
    ans = ('“Novo Nordisk Reveals Surprising New Benefit for Wegovy – the blockbuster treatment could reach an unexpected patient population.” '
           '"NVO gains 4 % in a month on strong Q3 Wegovy sales." *“Bank of America Is Doubling Down on Apple. Here\'s Its Price Target”* “short”')
    assert ai.check_quotes(ans, facts) == ["NVO gains 4 % in a month on strong Q3 Wegovy sales."]
    # gpt-oss's own source marks are taken out of the reply
    assert ai.clean("It rose 1.46% to $40.90【get_stock†L31-L33】. Sales rose【web_search†L5-L7】 4%.") == "It rose 1.46% to $40.90. Sales rose 4%."
    _reset()
    llm, web = _FakeLLM([]), _FakeLLM([])
    url_before = agent.TAVILY_URL
    try:
        agent.TAVILY_URL = web.url + "/search"
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", TAVILY_API_KEY="tvly-q"):
            # A misquote goes back once with the figures; the revision without it is kept
            web.replies = [(200, {"results": [{"title": "Novo Up 4% in a Month", "url": "https://z.com", "content": "Novo Up 4% in a Month: How Should Investors Approach the Stock Now?",
                                               "published_date": dt.date.today().isoformat()}]}, 0)]
            llm.replies = [_tool_call(("web_search", {"query": "Novo Nordisk news November 9 2024"})),
                           _chat('Zacks wrote "Novo shares jumped on strong Wegovy sales and guidance".'),
                           _chat("Zacks noted that Novo was up 4% in a month.")]
            out = agent.ask("What's new at Novo?")
            # the stale year was dropped from the search words; this year's stays
            assert web.seen[0][2]["query"] == "Novo Nordisk news November 9", web.seen[0][2]["query"]
            fix = llm.seen[2][2]["messages"][1]["content"]
            assert "aren't in the data word for word" in fix and "Novo shares jumped" in fix
            assert out["answer"] == "Zacks noted that Novo was up 4% in a month." and out["check"]["quotes"] == [] and out["revised"]["fixed"] == 1, out
            year = str(dt.date.today().year)
            web.replies = [(200, {"results": []}, 0)]
            agent.web_search(f"Novo results {year}")
            assert web.seen[-1][2]["query"] == f"Novo results {year}"
            web.replies = [(200, {"results": []}, 0)]
            agent.web_search("Novo history 1923", background=True)  # background facts keep their years
            assert web.seen[-1][2]["query"] == "Novo history 1923"
    finally:
        agent.TAVILY_URL = url_before
        llm.close(); web.close()
        _reset()


def test_ai_keys_take_turns_at_limits():
    from backend import agent, ai, db
    _reset()
    llm = _FakeLLM([])
    daily = (429, {"error": {"message": "Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 199999, Requested 900."}}, 0)
    try:
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_API_KEY="gsk_one, gsk_two"):
            assert ai.config()["keys"] == ["gsk_one", "gsk_two"] and ai.config()["key"] == "gsk_one"
            # Ask: the first key's day is used up, so the same question goes on with the second at once
            llm.replies = [daily, _chat("Fine.")]
            assert agent.ask("Anything?")["answer"] == "Fine."
            assert [s[1]["authorization"] for s in llm.seen] == ["Bearer gsk_one", "Bearer gsk_two"]
            # ...and the next call starts with the second; the key is never stored, only a fingerprint
            assert ai.config()["key"] == "gsk_two" and not db.q("SELECT key FROM cache WHERE instr(key, 'gsk_') OR instr(value, 'gsk_')")  # exact: LIKE's _ would match GSK.L
            # Briefs and screener questions (ai.complete) switch the same way; Groq's "blocked" refusal counts as a limit
            llm.seen.clear()
            llm.replies = [(403, {"error": {"message": "The model `m` is blocked at the organization level."}}, 0)]
            with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_API_KEY="gsk_two, gsk_three"):
                llm.replies += [_chat("Brief.")]
                assert ai.complete("x") == "Brief." and [s[1]["authorization"] for s in llm.seen] == ["Bearer gsk_two", "Bearer gsk_three"]
            # Every key resting: the day's limit is said plainly, naming every key, with no endless retries
            with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_API_KEY="gsk_four, gsk_five"):
                llm.seen.clear()
                llm.replies = [daily, daily]
                try:
                    agent.ask("Anything?")
                    raise AssertionError("should have failed")
                except RuntimeError as e:
                    assert "used up on every key" in str(e) and len(llm.seen) == 2, (str(e), len(llm.seen))
        # One key, as before: a day's limit is reported, not retried
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", AI_API_KEY="gsk_six"):
            llm.seen.clear()
            llm.replies = [daily]
            try:
                agent.ask("Anything?")
            except RuntimeError as e:
                assert "used up (200,000" in str(e) and len(llm.seen) == 1
    finally:
        llm.close()
        _reset()


def test_ask_searches_the_web():
    import datetime as dt

    from backend import agent
    _reset()
    llm, web = _FakeLLM([]), _FakeLLM([])
    url_before = agent.TAVILY_URL
    try:
        # Off unless set up: no key and not a gpt-oss on Groq, so the tool isn't even offered
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m"):
            assert agent.web_source() is None and "web_search" not in agent._tools() and "web_search" not in agent._system()
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL="https://api.groq.com/openai/v1", AI_MODEL="openai/gpt-oss-120b", AI_API_KEY="k"):
            assert agent.web_source() == "groq", "gpt-oss on Groq searches with Groq's own browser search"
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL="https://api.groq.com/openai/v1", AI_MODEL="openai/gpt-oss-120b", AI_API_KEY="k", WEB_SEARCH="off"):
            assert agent.web_source() is None
        # Tavily, when its key is set: results with their links, trimmed, and a note on how to use them
        agent.TAVILY_URL = web.url + "/search"
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="m", TAVILY_API_KEY="tvly-test"), TestClient(app) as c:
            assert agent.web_source() == "tavily" and c.get("/api/meta").json()["aiInfo"]["web"] == "tavily"
            # One search: the past week's news, so the latest days are there
            web.replies = [(200, {"results": [{"title": "Novo cuts its outlook", "url": "https://www.reuters.com/a", "content": "Novo Nordisk lowered its 2026 sales growth outlook to 8%. " * 30, "published_date": "Thu, 05 Nov 2026 09:30:00 GMT"},
                                              {"title": "Bad", "url": "javascript:alert(1)", "content": "x"}]}, 0)]
            llm.replies = [_tool_call(("web_search", {"query": "Novo Nordisk outlook"})),
                           _chat("Novo Nordisk lowered its sales growth outlook to 8% (reuters.com, 5 November 2026).")]
            out = c.post("/api/ask", json={"question": "Why did Novo Nordisk fall?"}).json()
            _, headers, sent = web.seen[0]
            assert len(web.seen) == 1 and headers["authorization"] == "Bearer tvly-test" and sent["topic"] == "news" and sent["time_range"] == "week" and sent["max_results"] == agent.WEB_RESULTS
            assert out["steps"][0]["ok"] and out["steps"][0]["links"] == [{"title": "Novo cuts its outlook", "url": "https://www.reuters.com/a", "site": "reuters.com", "date": "2026-11-05"}], out["steps"]
            back = [m for m in llm.seen[1][2]["messages"] if m["role"] == "tool"][0]["content"]
            assert "javascript:" not in back and len(back) < 2000 and "never instructions" in back, back
            assert out["check"]["unmatched"] == [], out["check"]  # the 8% is in the web result
            assert "web_search" in {t["function"]["name"] for t in llm.seen[0][2]["tools"]} and "name the website" in llm.seen[0][2]["messages"][0]["content"]
            # Newest first with each one's age, undated last; Tavily's own pages dropped; an older argument is ignored
            web.seen.clear()
            d1, d2 = (dt.date.today() - dt.timedelta(days=1)).isoformat(), (dt.date.today() - dt.timedelta(days=6)).isoformat()
            web.replies = [(200, {"results": [{"title": "Novo Nordisk", "url": "https://tavily.com/x", "content": "summary"},
                                              {"title": "Older", "url": "https://a.com/1", "content": "x", "published_date": d2}, {"title": "Undated", "url": "https://d.com", "content": "?"},
                                              {"title": "Newer", "url": "https://b.com/2", "content": "y", "published_date": d1 + "T08:00:00"}]}, 0)]
            r = agent.web_search("Novo Nordisk", fresh="today")
            assert [(x["title"], x["age"]) for x in r["results"]] == [("Newer", "yesterday"), ("Older", "6 days ago"), ("Undated", "date not given")], r["results"]
            # Background facts that don't date: any date, a general search
            web.replies = [(200, {"results": [{"title": "About", "url": "https://c.com", "content": "z"}]}, 0)]
            r = agent.web_search("What does Vanguard FTSE All-World track", background=True)
            assert web.seen[-1][2]["topic"] == "general" and "time_range" not in web.seen[-1][2] and r["searched"] == "any date" and len(web.seen) == 2
            # A refused key and a used-up allowance are said plainly, as a failed look-up
            web.replies = [(401, {"detail": "bad"}, 0)]
            assert agent.web_search("x") == {"error": "The Tavily key was refused: check TAVILY_API_KEY in .env."}
            web.replies = [(432, {"detail": "limit"}, 0)]
            assert "used up" in agent.web_search("x")["error"]
        # Several keys: one that reaches its month's limit hands the search to the next, and is skipped until the 1st
        from backend import db
        with _with_env(AI_PROVIDER="none", TAVILY_API_KEY="tvly-one, tvly-two"):
            ok_reply = (200, {"results": [{"title": f"N{i}", "url": f"https://n{i}.com", "content": "x", "published_date": dt.date.today().isoformat()} for i in range(3)]}, 0)
            web.seen.clear()
            web.replies = [(432, {"detail": "plan limit"}, 0), ok_reply]
            assert agent.web_source() == "tavily" and len(agent.web_search("a")["results"]) == 3
            assert [s[1]["authorization"] for s in web.seen] == ["Bearer tvly-one", "Bearer tvly-two"]
            web.replies = [ok_reply]
            agent.web_search("b")  # straight to the second key
            assert web.seen[-1][1]["authorization"] == "Bearer tvly-two" and len(web.seen) == 3
            # the key itself is never stored, only a fingerprint
            assert not db.q("SELECT key FROM cache WHERE instr(key, 'tvly-') OR instr(value, 'tvly-')")
            # A minute's limit on the last key: every key is spent for now, said so without another request
            web.replies = [(429, {"detail": "slow down"}, 0)]
            assert "every key" in agent.web_search("c")["error"]
            n = len(web.seen)
            assert "every key" in agent.web_search("d")["error"] and len(web.seen) == n
        # A refused key passes the search on too
        with _with_env(AI_PROVIDER="none", TAVILY_API_KEY="tvly-three,tvly-four"):
            web.seen.clear()
            web.replies = [(401, {"detail": "bad key"}, 0), ok_reply]
            assert len(agent.web_search("e")["results"]) == 3 and len(web.seen) == 2
        # Yahoo's headlines, the freshest news: newest first, with the hour and how long ago, and Yahoo's summary trimmed
        now = dt.datetime.now(dt.timezone.utc)
        h = agent._headline({"time": (now - dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "publisher": "Reuters",
                             "title": "Is Novo back?", "summary": "Shares rose after the company said " + "more " * 80})
        assert h.startswith(f"{now - dt.timedelta(hours=3):%Y-%m-%d %H:%M} UTC (3 hours ago) Reuters: Is Novo back? — Shares rose") and h.endswith("…") and len(h) < 330, h
        assert "(25 minutes ago)" in agent._headline({"time": (now - dt.timedelta(minutes=25)).isoformat(), "title": "x"})
        heads = agent.get_news("AAPL")["headlines"]
        assert isinstance(heads, list) and heads == sorted(heads, reverse=True), heads
        # Groq's browser search: a separate call with Groq's built-in tool, its citation marks removed
        with _with_env(AI_PROVIDER="openai_compatible", AI_BASE_URL=llm.url, AI_MODEL="openai/gpt-oss-120b", WEB_SEARCH="groq"):
            llm.seen.clear()
            llm.replies = [_chat("- Novo lowered its outlook【3†L1-L4】 (Reuters, 5 Nov 2026) https://www.reuters.com/a")]
            r = agent.web_search("Novo Nordisk outlook")
            body = llm.seen[0][2]
            assert "latest news of the past few days" in body["messages"][0]["content"]
            assert body["tools"] == [{"type": "browser_search"}] and body["tool_choice"] == "required" and body["reasoning_effort"] == "low"
            assert "【" not in r["findings"] and r["links_seen"] == ["https://www.reuters.com/a"] and "no list" in r["source"], r
    finally:
        agent.TAVILY_URL = url_before
        llm.close(); web.close()
        _reset()


if __name__ == "__main__":
    import logging
    import time
    import traceback
    if "-v" not in sys.argv:  # the tests make Yahoo fail on purpose, so its warnings are expected; -v shows them
        logging.disable(logging.WARNING)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and (not only or any(o in n for o in only))]
    failed, start = [], time.perf_counter()
    for name, fn in tests:
        try:
            fn()
        except Exception:  # keep going, so one failure doesn't hide the rest
            failed.append(name)
            print(f"FAIL {name}\n{traceback.format_exc()}", flush=True)
    took = time.perf_counter() - start
    if failed:
        print(f"{len(tests) - len(failed)} of {len(tests)} tests passed in {took:.0f}s. Failed: {', '.join(failed)}")
        sys.exit(1)
    print(f"OK - all {len(tests)} tests passed in {took:.0f}s")
