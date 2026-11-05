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
        assert data.info("EMPTY.X")["trailingPE"] is None and db.cache_get_stale("info:v3:EMPTY.X") is None
        db.cache_set("info:v3:OLD.X", {"trailingPE": 12.0})
        db.x("UPDATE cache SET ts = 0 WHERE key = 'info:v3:OLD.X'")
        assert data.info("OLD.X")["trailingPE"] == 12.0
        assert data.news("EMPTY.X") == [] and db.cache_get_stale("news:EMPTY.X") is None
    finally:
        data._yf, data.DEMO = real_yf, real_demo


def _reset():
    """Clear the user tables so a test starts from an empty app."""
    from backend import db
    db.init()
    for table in ("holdings", "watchlist", "journal", "alerts", "settings"):
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
    db.x("INSERT INTO holdings (ticker, shares, cost) VALUES ('OLD', 100, 100)")
    with _FakeMarket({"OLD": old, "SPY": old}):
        alone = portfolio.summary(db.get_settings())
    db.x("INSERT INTO holdings (ticker, shares, cost) VALUES ('NEW', 0.001, 100)")  # about 0.001% of the value
    with _FakeMarket({"OLD": old, "NEW": new, "SPY": old}):
        both = portfolio.summary(db.get_settings())
    assert len(both["performance"]["dates"]) == len(alone["performance"]["dates"]) == 261
    assert abs(both["risk"]["return1y"] - alone["risk"]["return1y"]) < 0.001
    assert both["risk"]["volatility"] is not None and both["risk"]["sharpe"] is not None
    assert any("NEW" in w and "history since" in w for w in both["warnings"])
    assert not any("history since" in w for w in alone["warnings"])
    # Joining the portfolio must not itself look like a gain: a new position adds value, not return
    db.x("UPDATE holdings SET shares = 100 WHERE ticker = 'NEW'")
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
        db.x("INSERT INTO holdings (ticker, shares, cost) VALUES ('0700.HK', 100, 300)")
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
        db.x("INSERT INTO holdings (ticker, shares, cost, bought) VALUES ('SAP.DE', 10, 100, '2025-01-02')")
        with _FakeMarket({"SAP.DE": flat}, {"SAP.DE": {"currency": "EUR"}}):
            p = portfolio.summary(db.get_settings())
        pos = p["positions"][0]
        assert abs(pos["value"] - 1080) < 1e-6 and abs(pos["cost"] - 1000) < 1e-6 and abs(pos["pnl"] - 80) < 1e-6
        assert pos["fxApprox"] is False and not any("exchange rate" in w for w in p["warnings"])

        # Two lots: one dated at 1.00, one with no date, which can only use today's 1.08 and says so
        db.x("INSERT INTO holdings (ticker, shares, cost, bought) VALUES ('SAP.DE', 10, 100, '')")
        with _FakeMarket({"SAP.DE": flat}, {"SAP.DE": {"currency": "EUR"}}):
            p = portfolio.summary(db.get_settings())
        pos = p["positions"][0]
        assert abs(pos["cost"] - 2080) < 1e-6 and pos["fxApprox"] is True
        assert any("SAP.DE" in w and "exchange rate" in w for w in p["warnings"])
    finally:
        data.fx_on = real

    # Same currency as the base: an undated lot is exact, so no warning
    _reset()
    db.x("INSERT INTO holdings (ticker, shares, cost, bought) VALUES ('AAPL', 10, 100, '')")
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
    db.x("INSERT INTO holdings (ticker, shares, cost) VALUES ('AAA', 30, 100), ('SPY', 1, 100)")
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
        assert backup["version"] == 1 and backup["holdings"][0]["note"] == "core" and backup["settings"]["base_currency"] == "EUR"
        assert "cache" not in backup  # market data can always be downloaded again

        before = {t: db.q(f"SELECT * FROM {t}") for t in ("watchlist", "holdings", "journal", "alerts", "settings")}
        _reset()
        assert not db.q("SELECT * FROM holdings")
        assert c.post("/api/backup/restore", json=backup).status_code == 200
        after = {t: db.q(f"SELECT * FROM {t}") for t in before}
        assert after == before, "a restore must bring back identical rows"

        # Bad files change nothing, even when the problem is in the last table
        bad_row = {**backup, "alerts": [{"nonsense": 1}], "settings": {"base_currency": "USD"}}
        for bad in ({"hello": 1}, {**backup, "version": 99}, {**backup, "journal": "oops"}, bad_row):
            assert c.post("/api/backup/restore", json=bad).status_code == 400, bad
            assert {t: db.q(f"SELECT * FROM {t}") for t in before} == before

        # Unknown columns and settings can't reach the database
        extra = {**backup, "holdings": [{"ticker": "X", "shares": 1, "cost": 1, "dropped": "; DROP TABLE holdings"}], "settings": {"hack": 1}}
        assert c.post("/api/backup/restore", json=extra).status_code == 200
        assert [h["ticker"] for h in db.q("SELECT * FROM holdings")] == ["X"]
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
    # Fully invested from day one: the buy & hold return, less one 10 bps entry cost
    assert abs((1 + r["strategy"]["totalReturn"]) / (1 + r["benchmark"]["totalReturn"]) - 0.999) < 1e-9
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
