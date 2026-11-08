# StockPilot: project context

Personal, self-hosted investing dashboard. The goal is a smart decision-support tool for the owner's own investing: prices, charts, transparent scores, recommendations and portfolio analytics. It is **not** a signal-selling or trading bot. It never places trades.

## Owner's choices (keep these unless told otherwise)
- **Delivery:** local full-stack app on a Windows PC (run from VS Code / PowerShell), on **port 8001** (8000 belongs to another app).
- **Investing style:** balanced mix. Factor weights default to 20% each across value, quality, growth, momentum and risk. Core and satellite, with an 80% core target by default.
- **Markets:** all of them, meaning US, Europe, Gulf (QSE `.QA`, Tadawul `.SR`, UAE `.AE`), ETFs and crypto (`-USD`).
- **Recommendations:** always explained, never a bare "BUY". Every score must stay traceable to named metrics and thresholds.

## Run
```powershell
.\run.bat                     # creates .venv on first run; live Yahoo data on http://localhost:8001
.\run.bat --demo              # synthetic offline data
.\.venv\Scripts\python tests\test_smoke.py      # offline end-to-end tests (demo mode)
.\.venv\Scripts\python tests\run_all.py         # everything: offline tests, browser checks, demo and live walks, raw-Yahoo comparison
.\.venv\Scripts\python tests\run_all.py --offline   # skip the live parts
.\.venv\Scripts\python tests\run_all.py --only walk-live
```
- The browser checks need `npm install` once in `tests/browser`, and use the installed Chrome (`channel: "chrome"`).
- `run_all.py` gives each browser check its own server and temporary database, so the owner's data isn't touched.
- `uvicorn --reload` has not reliably picked up Python changes on this machine. Restart the server after backend edits.

Optional settings go in `.env` (see `.env.example`):
- `ANTHROPIC_API_KEY` enables AI briefs and the LLM screener parser.
- `ANTHROPIC_MODEL` overrides the model.
- `ALERT_CHECK_MINUTES` sets how often alerts are checked.
- `STOCKPILOT_DB` moves the database; `STOCKPILOT_DEMO=1` switches to synthetic data.

## Stack
- **Backend:** Python, FastAPI, yfinance, pandas/numpy, SQLite. No ORM; raw SQL lives in `db.py`.
- **Frontend:** a single page with no build step: `index.html`, `app.js` and `styles.css` in vanilla JS. Libraries are bundled in `frontend/vendor/` (no CDN):
  - Lightweight Charts v4.2, for the price, RSI and MACD charts. Its logo is hidden (`attributionLogo: false`) and TradingView is credited in the README, as its licence asks.
  - Chart.js v4.4, for everything else
  - marked and DOMPurify, for rendering the brief's markdown safely
- FastAPI serves the frontend at `/` and `/static`, and the favicon at `/favicon.ico`.

## Backend map (`backend/`)
| File | Role |
|---|---|
| `main.py` | All REST routes. Loads `.env`. Runs the background alert loop. Every response goes through `ok()`, which makes NaN/inf/numpy values JSON-safe. Dashboard tiles: `_tiles()` is the catalogue (`MACRO` plus the owner's custom tiles); `/api/tiles` adds a custom tile only after Yahoo returns at least two days of history, and `DELETE /api/tiles/{t}` removes one. `/api/buckets` sets a holding's core/satellite bucket. |
| `data.py` | Yahoo access with SQLite caching, demo mode and FX. `history()` fetches 5y of daily prices once and slices shorter periods from it. `intraday()` gives 5m/15m candles for the 1D/5D charts; `weekly()` builds weekly candles from daily ones. `search_symbols()` is Yahoo's own symbol search (24h cache). `fx_rate()`/`fx_series()` route crosses through USD and handle minor units (GBp, KWF) and pegs. Cache keys carry a version (`info:v8:`); bump it when stored fields or derived values like the region change. |
| `ledger.py` | The transaction ledger: buys, sales and dividends replayed first in, first out; XIRR. |
| `portfolio.py` | Positions from the ledger, base-currency conversion at each date's FX, allocation (incl. fund look-through), risk, warnings, rebalancing, and core/satellite: the split against target, each bucket's share of variance (`_risk_shares`, sums to 1), and each bucket's time-weighted return over the days both were held (`_bucket_performance`). |
| `indicators.py` | SMA/EMA/RSI/MACD/Bollinger, plus returns, volatility, drawdown, CAGR, Sharpe, Sortino and beta. Windows are measured in calendar time (`window()`), not row counts. |
| `scoring.py` | Five factors scored 0-100 via `lin(x, bad, good)` thresholds; each factor is the average of its available metrics. The composite is a weighted average over factors that have data (ETFs and crypto get only momentum and risk). Stance bands: ≥70 Strong, ≥58 Favorable, ≥45 Neutral, ≥35 Caution, else Weak. |
| `insights.py` | Screener (threaded), compare with weekly-return correlation, DCF, alert checks, the daily digest and score history. |
| `backtest.py` | buy_hold, sma_cross, rsi_reversion, momentum and dca. Positions are shifted one bar to avoid look-ahead; costs are in basis points. |
| `ai.py` | Calls the Anthropic Messages API with httpx. `brief()` falls back to `rule_brief()` without a key or on error. `parse_query()` is the LLM or regex natural-language screener parser. |
| `db.py` | SQLite tables: cache, watchlist, transactions, journal, alerts, settings, score_history and buckets (only overrides of the default bucket are stored). Settings are a JSON value per key, merged over `DEFAULT_SETTINGS` (incl. `dashboard_tiles`, `custom_tiles`, `core_target`). Backups export every user table plus settings (`BACKUP_VERSION` 4; older versions are upgraded on restore). |
| `universe.py` | The built-in ticker list for screener and search, `MACRO` (every dashboard tile, in display order) and `DEFAULT_TILES`, suffix→region and suffix→currency maps, `REGION_BY_TICKER` (funds whose region is what they hold, e.g. VWCE.DE → Global), and `CORE_FUNDS` (broad index funds that start as core). |

## Frontend
Views are dashboard, research, compare, screener, portfolio, backtest, journal and settings.
- **Routing:** hash-based (`#research/AAPL`, `#portfolio`).
- **Helpers:** `api()`, the `table()` builder (sortable; pass `stock: (row) => ticker` to make rows open a stock), `mkChart()` (destroys and recreates Chart.js instances), `modal()` and `toast()`.
- **Explanations:** `infoIcon(text)` adds an "i" whose text shows in one floating tooltip on hover or keyboard focus. `FACTOR_INFO` describes the five factors wherever they appear (Research factor list, screener headers, Settings weights); keep it in step with the metrics in `scoring.py`.
- **Opening stocks:** anything with `data-stock="TICKER"` opens that stock through one delegated handler. A left click opens it here; a middle click or Ctrl/Cmd+click opens a new tab. Buttons and links inside a row keep their own job.
- **Research chart:** ranges 1D/5D (intraday, without the daily indicators) up to 5Y; Day/Week candles (weekly by default on 2Y/5Y); Candles/Area/Line, remembered in localStorage.
- **Theme:** Dark, Light or Match system, set before first paint from localStorage. Every colour is a CSS token on `:root`, overridden under `:root[data-theme="light"]`; JS reads them with `C("--token")` and `tint()`. Don't hard-code colours.

## Gotchas
- yfinance `dividendYield` is sometimes a percent and sometimes a fraction; `debtToEquity` comes as a percentage (150 = 1.5x).
- London `.L` prices are in **GBp (pence)**, Kuwait in fils (KWF). See `MINOR_UNITS`.
- Gulf fundamentals on Yahoo are patchy. Expect `None` values, which the UI shows as "—".
- The Gulf indexes `^TASI.SR` and `^GNRI.QA` return a single day of history, so the dashboard follows the KSA and QAT ETFs instead.
- European funds are listed only with an exchange suffix (VWCE doesn't exist on Yahoo, VWCE.DE does). Search adds Yahoo's results to the built-in list for this reason.
- **Inline event handlers:** an `onkeydown = (e) => cond && action()` handler returns `false` for other keys, which cancels them, so nothing can be typed. Use a block body.
- **Browser tests:** Playwright's `fill()` sets the value without key events. Use `pressSequentially()` when the point is that typing works.
- **This machine:** Kaspersky intercepts HTTPS, and Python then rejects Yahoo's certificate. Live runs need `SSL_CERT_FILE` pointing at a bundle built from the Windows certificate store plus certifi (a permanent fix, the `truststore` package, has been suggested but not yet adopted).
- DCF fair value converts statement currency to quote currency, but cross-listed stocks remain approximate.

## Status
- Every feature passes `tests/test_smoke.py` (demo mode), the browser checks and both walks, and the live values match a raw-Yahoo calculation in `tests/check_live.py`.

## Ideas / next steps
- Upgrade the data provider (FMP or Polygon), at least for Gulf fundamentals, behind an interface in `data.py`.
- Sector-relative scoring: percentile within sector instead of absolute thresholds.
- Insider and institutional activity.
- Email or push digest on a schedule.
- Portfolio-level backtest and Monte Carlo simulation.

## Conventions
- Keep the "decision support, not financial advice" framing, and keep scores explainable.
- New metrics must be added to `factor_breakdown` with explicit thresholds, so they show up in the UI drill-down.
- Pass every API response through `ok()`.
- Every new feature must work in demo mode, and needs a check in `tests/test_smoke.py` and, for anything on screen, a step in `tests/browser/walk.js`.
- Commits use the per-repo identity `antonis00 <antonis00@users.noreply.github.com>`; never a company name or email.
- Don't add author or credit lines to files.
