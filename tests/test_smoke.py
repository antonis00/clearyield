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


if __name__ == "__main__":
    test_all()
    print("OK - all endpoints passed")
