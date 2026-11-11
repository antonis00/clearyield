# ClearYield

A personal investing dashboard that runs on your own computer: live prices, charts, explainable scores, a screener, a transaction ledger with real performance, backtesting, a decision journal and alerts. It covers US, European and Gulf stocks, ETFs and crypto, using Yahoo Finance data.

> **Decision support, not financial advice.** Scores and signals summarise data; they do not predict prices. ClearYield never places trades. Use it to structure your own thinking.

- **Every score explains itself.** Each one traces back to named metrics, with the values that score 0 and 100 shown on the page.
- **Your data stays with you.** It runs on your machine in a single SQLite file, with no account and no login, and only answers on localhost.
- **It judges your decisions.** Journal a decision, and the scorecard later shows how it did against the benchmark.
- **Nothing to build.** It's a FastAPI backend and a plain JavaScript page, with the chart libraries included.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/antonis00/clearyield.git
cd clearyield
./run.sh            # macOS / Linux
run.bat             # Windows
```

Then open http://localhost:8001. The first run installs the dependencies.

To try it with made-up data and no internet, use `./run.sh --demo` or `run.bat --demo`.

On Windows, **Settings → Start with Windows** runs it in the background whenever you sign in, with no window, so alerts and emails keep working with the page closed (its log is `data/server.log`). `run.bat` then just opens the page; after an update, `run.bat --restart` replaces the background copy with the new code.

Or manually:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --port 8001
```

Optional: copy `.env.example` to `.env` and add an `ANTHROPIC_API_KEY` to turn on AI-written research briefs and smarter plain-language screening. Without a key, the app uses a rule-based brief and a keyword parser.

## Features

| Screen | What it does |
|---|---|
| **Dashboard** | Market tiles you choose in Settings (indexes, Saudi and Qatar country ETFs, VIX, 10Y yield, commodities, crypto, currencies), portfolio snapshot, daily digest, watchlist with scores |
| **Research** | Candlestick chart from one day (5-minute candles) to five years (daily or weekly candles), with SMA 50/200, Bollinger bands, volume, RSI and MACD; factor radar with drill-down to every metric; plain-language signals; key stats; 4-year fundamentals; DCF calculator with the growth today's price assumes and a sensitivity grid; next earnings and ex-dividend dates and the record against expectations; for funds, their cost, sectors and top holdings; daily score history; news; AI or rule-based bull/bear brief; pre-trade check showing how a purchase would change your weights, warnings and beta, how it moves with your holdings, and a position size from your stop and risk |
| **Compare** | Growth-of-100 chart, metrics table and return-correlation matrix for up to 8 assets |
| **Screener** | 95-ticker universe across 5 markets; numeric filters or a plain-language query ("cheap profitable dividend stocks in the Gulf") |
| **Portfolio** | Ledger of buys, sales and dividends, entered one at a time or pasted as a list, and matched first in, first out. Multi-currency holdings converted to your base currency, unrealised and realised P&L at the FX rate on each date, annual return (XIRR), your actual performance vs benchmark over the past year, volatility/Sharpe/Sortino/beta/drawdown, allocation by asset class/region/sector/currency, looking through funds where Yahoo has the data, concentration warnings, rebalancing vs target allocation, estimated dividend income; core and satellite buckets (broad index funds are core by default, switchable per holding) with the split against your target, each bucket's share of the swings, and whether your satellites beat the core over the time you held both |
| **Backtest** | Buy & hold, MA crossover, RSI mean reversion, absolute momentum and monthly DCA, with trades filled at the next day's open and trading costs. A portfolio tab tests a mix of assets and simulates its possible futures; Score check shows whether the momentum and risk scores, rebuilt for past quarters, would have sorted the stocks you follow |
| **Journal & Alerts** | Record thesis, invalidation condition, stop/target and review date for every decision, with a scorecard of how your buy and add decisions did against the benchmark, overall and by the stance when recorded; alerts on price, daily move, RSI or score, shown in the tab title, as optional browser notifications while the page is open, and optionally by email (your own mail account's SMTP server in `.env`, switched on in Settings). Both feed the digest |
| **Settings** | Theme, factor weights (balanced by default), base currency, risk-free rate, benchmark, target allocation, core share target, dashboard tiles, backup and restore |

## How the scores work

Each asset gets five factor scores from 0 to 100. Every metric maps linearly between a "bad" and a "good" threshold, and the factor score is the average of its available metrics. Research lists every metric with the values that score 0 and 100, including those with no usable data.

- **Value:** P/E, forward P/E, P/B, EV/EBITDA, FCF yield
- **Quality:** ROE, operating and net margin, debt/equity, current ratio
- **Growth:** revenue growth, earnings growth, forward vs trailing EPS
- **Momentum:** 12-1 month return, price vs 200-day average, 3-month return
- **Risk** (higher = calmer): volatility, 1Y max drawdown, beta

The composite is a weighted average of the factors that have data. ETFs and crypto have no fundamentals, so they are scored on momentum and risk only, and the screener ranks each asset class separately. Stances follow the composite: ≥70 Strong, ≥58 Favorable, ≥45 Neutral, ≥35 Caution, otherwise Weak. The thresholds live in `backend/scoring.py` if you want to tune them.

## Tickers

The search box finds anything Yahoo Finance lists, by symbol or name. Funds and stocks outside the US usually have an exchange suffix, so searching `vwce` offers `VWCE.DE`, `VWCE.MI` and so on. Suffix conventions:

- `.QA` Qatar (QNBK.QA)
- `.SR` Saudi (2222.SR)
- `.AE` UAE (EMAAR.AE)
- `.L` London, quoted in pence
- `.PA`, `.DE`, `.AS` European exchanges
- `-USD` crypto (BTC-USD)

Edit `backend/universe.py` to change the screener universe.

## Notes and limits

- Data comes from Yahoo Finance via `yfinance`. It is free, unofficial and occasionally delayed or incomplete, especially for Gulf fundamentals. Responses are cached in `data/clearyield.db`. Use **Settings → Refresh all data** to force fresh data. If Yahoo fails, the app keeps using the last data it has and says so.
- The performance chart follows your actual transactions and each day's FX rate over the past year. Risk figures instead apply today's holdings to the past year, because they describe what you own now.
- Yahoo data is for personal use. Check the terms before sharing or redistributing data from the app.
- Sales are matched to your oldest shares first. Check whether that matches the rule that applies to you before using the figures for tax.
- Dividends are recorded by hand, and fees are included in the price you enter.
- Your holdings and journal exist only in `data/clearyield.db`, which git ignores. Use **Settings → Download backup** to keep a copy.

## Tests

```bash
python tests/test_smoke.py     # offline, on made-up data
python tests/run_all.py        # everything
```

`run_all.py` adds a browser walk through every screen, in demo mode and with live data, and a comparison with figures recomputed from raw Yahoo data. It needs Chrome, internet and Node, after running `npm install` in `tests/browser` once. It uses temporary databases, so your data isn't touched.

## Project layout

```
backend/   main.py (API) · data.py (Yahoo + cache + FX + demo) · scoring.py · indicators.py
           portfolio.py · ledger.py (transactions) · backtest.py · insights.py (screener, compare, DCF, alerts, digest, scorecard) · mailer.py (alert emails)
           ai.py · db.py · universe.py
frontend/  index.html · app.js · styles.css · vendor/ (charting libraries, bundled for offline use)
tests/     test_smoke.py · check_live.py · run_all.py · browser/ (walk through every screen in Chrome)
```

## License

MIT. See [LICENSE](LICENSE). The bundled libraries in `frontend/vendor/` keep their own licenses. Price charts are made with [TradingView Lightweight Charts™](https://www.tradingview.com/lightweight-charts/) (Apache 2.0), by [TradingView](https://www.tradingview.com/).
