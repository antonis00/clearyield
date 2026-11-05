"""Offline smoke test: runs every endpoint against synthetic demo data.

    STOCKPILOT_DEMO=1 python -m pytest tests -q      (or: python tests/test_smoke.py)
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["STOCKPILOT_DEMO"] = "1"
os.environ["STOCKPILOT_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402


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
            assert c.get(f"/api/stock/{t}/brief").json()["text"]

        d = c.get("/api/dcf/AAPL").json()
        assert "fairValue" in d or "error" in d
        cmp_ = c.get("/api/compare?tickers=AAPL,MSFT,QNBK.QA&period=1y").json()
        assert len(cmp_["series"]) == 3 and cmp_["correlation"]

        s = c.post("/api/screener", json={"markets": ["US", "Gulf"], "filters": {"min_score": 0}}).json()
        assert s["scanned"] > 40 and s["rows"]
        s2 = c.post("/api/screener", json={"query": "cheap profitable dividend stocks in the gulf"}).json()
        assert "Gulf" in s2["filters"]["markets"]

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
        assert data.info("EMPTY.X")["trailingPE"] is None and db.cache_get_stale("info:v7:EMPTY.X") is None
        db.cache_set("info:v7:OLD.X", {"trailingPE": 12.0})
        db.x("UPDATE cache SET ts = 0 WHERE key = 'info:v7:OLD.X'")
        assert data.info("OLD.X")["trailingPE"] == 12.0
        assert data.news("EMPTY.X") == [] and db.cache_get_stale("news:EMPTY.X") is None
    finally:
        data._yf, data.DEMO = real_yf, real_demo


def _reset():
    """Clear the user tables so a test starts from an empty app."""
    from backend import db
    db.init()
    for table in ("transactions", "watchlist", "journal", "alerts", "settings", "score_history"):
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
        db.x("UPDATE cache SET ts = 0 WHERE key = 'fx:CADUSD'")
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
        rows = c.post("/api/screener", json={"markets": ["US", "ETFs", "Crypto"], "filters": {"sort": "composite"}}).json()["rows"]
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
        assert backup["version"] == 3 and backup["transactions"][0]["note"] == "core" and backup["settings"]["base_currency"] == "EUR"
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
    assert "markets" not in p("show us cheap stocks")
    # Real mentions still do
    assert p("cheap US stocks")["markets"] == ["US"]
    assert p("american value")["markets"] == ["US"]
    assert p("etfs from the usa")["markets"] == ["US", "ETFs"]
    assert p("european banks")["markets"] == ["Europe"]
    assert p("best stocks in Qatar")["markets"] == ["Gulf"]
    # Ways of writing P/E
    for q in ("stocks with PE ratio under 15", "stocks with a p/e of 15", "P/E below 15", "under 15 pe", "pe < 15"):
        assert p(q).get("max_pe") == 15.0, q
    # Yield and growth are separate things, and dividend growth is neither
    assert p("yield above 4%") == {"min_div_yield": 0.04}
    assert p("revenue growth above 20%") == {"min_rev_growth": 0.20}
    dg = p("dividend growth above 5%")
    assert "min_div_yield" not in dg and "min_rev_growth" not in dg and "dividend" in dg["ignored"] and "growth" in dg["ignored"]
    # Negation isn't silently read as the opposite
    no = p("not cheap")
    assert "max_pe" not in no and "cheap" in no["ignored"]
    assert p("no debt")["max_de"] == 0.5
    assert "min_roe" not in p("not profitable")
    # Sectors
    assert p("tech stocks") == {"sector": "Technology"}
    assert p("gulf banks") == {"markets": ["Gulf"], "sector": "Financial Services"}
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
    for t, cur, region in (("7203.T", "JPY", "Asia"), ("0700.HK", "HKD", "Asia"), ("SHOP.TO", "CAD", "Americas"), ("BHP.AX", "AUD", "Asia"),
                           ("RELIANCE.NS", "INR", "Asia"), ("VOW3.F", "EUR", "Europe"), ("AAPL", "USD", "US"), ("BRK-B", "USD", "US")):
        assert currency_guess(t) == cur and region_of(t) == region, t
    assert currency_guess("ABC.ZZ") is None and region_of("ABC.ZZ") == "Other"
    assert region_of("^GSPC") == "US" and region_of("EURUSD=X") == "US"
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
    finally:
        data.DEMO = real_demo
        db.cache_clear()


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
    db.x("UPDATE cache SET ts = 0 WHERE key = 'hist:OLD:5y'")

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
        db.x("UPDATE cache SET ts = 0 WHERE key = 'fx:EURUSD'")
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

    from backend import db, insights
    _reset()
    db.x("INSERT INTO transactions (ticker, shares, price) VALUES ('PAST', 1, 100), ('NOW', 1, 100)")
    today = dt.date.today().isoformat()
    past = _steps([0.0] * 300 + [0.05], end="2026-06-01")
    now = _steps([0.0] * 300 + [0.05], end=today)
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
        db.x("UPDATE cache SET ts = 0 WHERE key = 'fin:Y'")
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


def test_rsi_waits_for_enough_data():
    import pandas as pd

    from backend import indicators as ind
    s = pd.Series([100.0, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 87, 88], index=pd.bdate_range("2024-01-01", periods=17))
    r = ind.rsi(s)
    assert r.iloc[:14].isna().all() and r.iloc[14:].notna().all()
    assert r.iloc[14] == 0.0  # 14 straight falls


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
    assert "review due" in texts[2] and "overbought" in texts[-1], texts
    assert "rows" not in d


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
            c.get("/api/stock/AAPL/brief")
            low = seen["stats"]["sharpe1y"]
            c.post("/api/settings", json={"risk_free_rate": 0.20})
            c.get("/api/stock/AAPL/brief")
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
            assert hits[0]["name"] == "Vanguard FTSE All-World UCITS ETF" and hits[0]["market"] == "ETF · GER"
            c.get("/api/search?q=vwce")
            assert calls.count("vwce") == 1, "repeat searches use the cache"
            # The built-in list comes first, and nothing appears twice
            apple = c.get("/api/search?q=apple").json()
            assert [h["ticker"] for h in apple] == ["AAPL", "APLE"] and apple[0]["market"] == "US"
            # With Yahoo down or nothing found, a typed symbol can still be tried
            assert c.get("/api/search?q=down").json() == [{"ticker": "DOWN", "name": "Look up this symbol on Yahoo Finance", "market": "Any"}]
            assert not db.cache_get_stale("search:down")
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK - all endpoints passed")
