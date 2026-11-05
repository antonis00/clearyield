# StockPilot

A personal, self-hosted investing dashboard: live prices, charts, transparent factor scores, a screener, portfolio analytics, backtesting, a decision journal and alerts, covering US, European and Gulf stocks, ETFs and crypto.

> **Decision support, not financial advice.** Scores and signals summarise data; they do not predict prices. Use them to structure your own thinking.

## Quick start

Requires Python 3.10+.

```bash
./run.sh            # macOS / Linux  -> http://localhost:8001
run.bat             # Windows
./run.sh --demo     # synthetic offline data, to try the UI without internet
```

Or manually:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8001
```

Optional: copy `.env.example` to `.env` and add an `ANTHROPIC_API_KEY` to turn on AI-written research briefs and smarter plain-language screening. Without a key, the app uses a rule-based brief and a keyword parser.

## Features

| Screen | What it does |
|---|---|
| **Dashboard** | Macro strip (S&P 500, Nasdaq, Euro Stoxx, Tadawul, VIX, 10Y yield, gold, oil, BTC, EUR/USD, DXY), portfolio snapshot, daily digest, watchlist with scores |
| **Research** | Candlestick chart with SMA 50/200, Bollinger bands, volume, RSI and MACD; factor radar with drill-down to every metric; plain-language signals; key stats; 4-year fundamentals; DCF calculator with the growth today's price assumes and a sensitivity grid; next earnings and ex-dividend dates; news with headline sentiment; AI or rule-based bull/bear brief; pre-trade check showing how a purchase would change your weights, warnings and beta, how it moves with your holdings, and a position size from your stop and risk |
| **Compare** | Growth-of-100 chart, metrics table and return-correlation matrix for up to 8 assets |
| **Screener** | ~100-ticker universe across 5 markets; numeric filters or a plain-language query ("cheap profitable dividend stocks in the Gulf") |
| **Portfolio** | Multi-currency holdings converted to your base currency, P&L, 1Y performance vs benchmark, volatility/Sharpe/Sortino/beta/drawdown, allocation by asset class/region/sector/currency, concentration warnings, rebalancing vs target allocation, estimated dividend income |
| **Backtest** | Buy & hold, MA crossover, RSI mean reversion, absolute momentum and monthly DCA, with next-bar execution and trading costs |
| **Journal & Alerts** | Record thesis, invalidation condition, stop/target and review date for every decision, with a scorecard of how your buy and add decisions did against the benchmark, overall and by the stance when recorded; alerts on price, daily move, RSI or score. Both feed the digest |
| **Settings** | Factor weights (balanced by default), base currency, risk-free rate, benchmark, target allocation |

## How the scores work

Each asset gets five factor scores from 0 to 100. Every metric maps linearly between a "bad" and a "good" threshold, and the factor score is the average of its available metrics. Research lists every metric with the values that score 0 and 100, including those with no usable data.

- **Value:** P/E, forward P/E, P/B, EV/EBITDA, FCF yield
- **Quality:** ROE, operating and net margin, debt/equity, current ratio
- **Growth:** revenue growth, earnings growth, forward vs trailing EPS
- **Momentum:** 12-1 month return, price vs 200-day average, 3-month return
- **Risk** (higher = calmer): volatility, 1Y max drawdown, beta

The composite is a weighted average of the factors that have data. ETFs and crypto have no fundamentals, so they are scored on momentum and risk only, and the screener ranks each asset class separately. Stances follow the composite: ≥70 Strong, ≥58 Favorable, ≥45 Neutral, ≥35 Caution, otherwise Weak. The thresholds live in `backend/scoring.py` if you want to tune them.

## Tickers

Any Yahoo Finance symbol works in the search box. Suffix conventions:

- `.QA` Qatar (QNBK.QA)
- `.SR` Saudi (2222.SR)
- `.AE` UAE (EMAAR.AE)
- `.L` London, quoted in pence
- `.PA`, `.DE`, `.AS` European exchanges
- `-USD` crypto (BTC-USD)

Edit `backend/universe.py` to change the screener universe.

## Notes and limits

- Data comes from Yahoo Finance via `yfinance`. It is free, unofficial and occasionally delayed or incomplete, especially for Gulf fundamentals. Responses are cached in `data/stockpilot.db`. Use **Settings → Clear data cache** to force fresh data.
- Portfolio performance applies *current* holdings and *today's* FX rates to the past year, so it approximates rather than records your actual history.
- Yahoo data is for personal use. Check the terms before sharing or redistributing data from the app.
- Run the offline test suite with: `python tests/test_smoke.py`
- Run every check with: `python tests/run_all.py`. It adds a browser walk through every screen in demo mode and with live data, plus a comparison with raw Yahoo figures. It needs Chrome, internet and Node, once you've run `npm install` in `tests/browser`. It uses temporary databases, so your data isn't touched.

## Project layout

```
backend/   main.py (API) · data.py (Yahoo + cache + FX + demo) · scoring.py · indicators.py
           portfolio.py · backtest.py · insights.py (screener, compare, DCF, alerts, digest) · ai.py · db.py · universe.py
frontend/  index.html · app.js · styles.css · vendor/ (charting libraries, bundled for offline use)
tests/     test_smoke.py · check_live.py · run_all.py · browser/ (walk through every screen in Chrome)
```
