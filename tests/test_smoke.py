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
    azn = data._normalize_info("AZN.L", {"currency": "GBp", "financialCurrency": "USD", "enterpriseToEbitda": 10.0,
                                         "trailingPE": 20.0, "quoteType": "EQUITY", "marketCap": 1e9, "freeCashflow": 1e8})
    usd_gbp = data.DEMO_FX["USD"] / data.DEMO_FX["GBP"]
    assert abs(azn["statementFx"] - usd_gbp) < 1e-3
    assert abs(azn["enterpriseToEbitda"] - 10 / usd_gbp) < 1e-2 and azn["trailingPE"] == 20.0
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
        assert data.info("EMPTY.X")["trailingPE"] is None and db.cache_get_stale("info:v2:EMPTY.X") is None
        db.cache_set("info:v2:OLD.X", {"trailingPE": 12.0})
        db.x("UPDATE cache SET ts = 0 WHERE key = 'info:v2:OLD.X'")
        assert data.info("OLD.X")["trailingPE"] == 12.0
        assert data.news("EMPTY.X") == [] and db.cache_get_stale("news:EMPTY.X") is None
    finally:
        data._yf, data.DEMO = real_yf, real_demo


if __name__ == "__main__":
    test_all()
    test_real_yahoo_shapes()
    print("OK - all endpoints passed")
