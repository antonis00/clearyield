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
.\.venv\Scripts\python tests\test_smoke.py      # offline end-to-end tests (demo mode); add a name part to run some, -v for the app's log
.\.venv\Scripts\python tests\run_all.py         # everything: offline tests, browser checks, demo and live walks, raw-Yahoo comparison
.\.venv\Scripts\python tests\run_all.py --offline   # skip the live parts
.\.venv\Scripts\python tests\run_all.py --only walk-live
```
- The browser checks need `npm install` once in `tests/browser`, and use the installed Chrome (`channel: "chrome"`).
- `run_all.py` gives each browser check its own server and temporary database, so the owner's data isn't touched. It leaves out the app's log lines, ends with each failing step by name, and on a failure prints the end of that check's output and its server's log.
- `requirements.txt` pins the versions the tests last passed with (certifi only has a floor). `run.bat` installs again whenever `requirements.txt` changes (it keeps a copy in `.venv\requirements.installed`). Upgrade on purpose, then run `run_all.py`.
- `uvicorn --reload` has not reliably picked up Python changes on this machine. Restart the server after backend edits.

Optional settings go in `.env` (see `.env.example`):
- `AI_PROVIDER` (`anthropic` | `openai_compatible` | `none`), `AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY`, `AI_TIMEOUT`, `AI_THINKING` choose the model for briefs and screener questions. `.env.example` has a block for Ollama, LM Studio, OpenRouter, Groq and Anthropic. A `.env` with only `ANTHROPIC_API_KEY` (and `ANTHROPIC_MODEL`) still turns on Anthropic.
- The owner's `.env` uses Groq (`openai/gpt-oss-120b`, `AI_THINKING=on`), with Ollama (`gemma4:e4b`, `AI_THINKING=off`) kept commented out. `AI_THINKING`: `off` sends `reasoning_effort: "none"` (on Ollama's `/v1/chat/completions` it's the only switch that works; `think: false` is ignored), `on` gives six times the reply allowance, `low`/`medium`/`high` set the effort. A thinking model given a normal allowance can spend it all reasoning and return nothing: gemma4 with thinking on did that for 7 of 7 briefs. A key left as `my_api_key` counts as not set up.
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
| `ai.py` | The optional model, through httpx. `config()` reads the provider from the environment on every call; `complete()` calls Anthropic's `/messages` or an OpenAI-style `{AI_BASE_URL}/chat/completions` (Ollama, LM Studio, OpenRouter, Groq), with a 180 s timeout for servers on this computer or network and 90 s otherwise, and strips `<think>` blocks. `brief()` falls back to `rule_brief()` on any error or timeout. Accuracy comes first: `brief_data()` gives the model every figure already in its unit ("142.1%", "38.1x", "4.98T USD"), leaves missing ones out, explains the score scale (a high risk score means LOW risk) and keeps only headlines naming the company (`relevant_headlines()`); `BRIEF_RULES` forbids new figures and comparisons that aren't in the data; temperature is 0. `check_figures()` then matches every figure in the reply against that same text (same unit, allowing for rounding and a dropped minus sign) and the page shows how many matched and which didn't. `check_brief()` keeps the "What would change the view" section apart: levels a model suggests there are listed as its own, not as errors. Typographic hyphens and narrow spaces (gpt-oss writes "50‑day", "‑1.0 %") are read as plain ones. `parse_query()` reads the first JSON object from the reply (fences and reasoning removed), checks every value with `_tidy_filters()`, asks once more if the reply is unusable, then falls back to the regex parser. `describe()` feeds the sidebar badge and never includes the key. |
| `db.py` | SQLite tables: cache, watchlist, transactions, journal, alerts, settings, score_history and buckets (only overrides of the default bucket are stored). Settings are a JSON value per key, merged over `DEFAULT_SETTINGS` (incl. `dashboard_tiles`, `custom_tiles`, `core_target`). Backups export every user table plus settings (`BACKUP_VERSION` 4; older versions are upgraded on restore). |
| `universe.py` | The built-in ticker list for screener and search, `MACRO` (every dashboard tile, in display order) and `DEFAULT_TILES`, suffix→region and suffix→currency maps, `REGION_BY_TICKER` (funds whose region is what they hold, e.g. VWCE.DE → Global), and `CORE_FUNDS` (broad index funds that start as core). |

## Frontend
Views are dashboard, research, compare, screener, portfolio, backtest, journal and settings.
- **Routing:** hash-based (`#research/AAPL`, `#portfolio`).
- **Helpers:** `api()`, the `table()` builder (sortable; pass `stock: (row) => ticker` to make rows open a stock), `mkChart()` (destroys and recreates Chart.js instances), `modal()` and `toast()`.
- **Explanations:** `infoIcon(text)` adds an "i" whose text shows in one floating tooltip on hover or keyboard focus. `FACTOR_INFO` describes the five factors wherever they appear (Research factor list, screener headers, Settings weights); keep it in step with the metrics in `scoring.py`.
- **Sidebar status:** round icons for the data source (the Yahoo mark), AI (the letters "AI", whatever the provider; the hover names the model) and the base currency's symbol, outlined in one colour (green when in use, grey when not), with the normal cursor and a light tint on hover, explained in the tooltip. The Yahoo mark is `frontend/icons/yahoo.svg`, used as a CSS mask so it takes the theme's colour; a replacement needs a transparent background (it's a stand-in; the owner may replace it with the official one).
- **Opening stocks:** anything with `data-stock="TICKER"` opens that stock through one delegated handler. A left click opens it here; a middle click or Ctrl/Cmd+click opens a new tab. Buttons and links inside a row keep their own job.
- **Research chart:** ranges 1D/5D (intraday, without the daily indicators) up to 5Y; Day/Week candles (weekly by default on 2Y/5Y); Candles/Area/Line, remembered in localStorage.
- **Theme:** Dark (near-black), Navy (very dark blue, the default), Light or Match system (light or navy), set before first paint from localStorage. Every colour is a CSS token on `:root` (dark), overridden under `:root[data-theme="navy"]` (surfaces only) and `:root[data-theme="light"]`; JS reads them with `C("--token")` and `tint()`. Don't hard-code colours.

## Gotchas
- yfinance `dividendYield` is sometimes a percent and sometimes a fraction; `debtToEquity` comes as a percentage (150 = 1.5x).
- London `.L` prices are in **GBp (pence)**, Kuwait in fils (KWF). See `MINOR_UNITS`. Dividends, though, are entered as the total received in the main currency (pounds, dinars), as brokers pay them: `data.main_unit()`.
- **Splits:** Yahoo's prices are split-adjusted, so `ledger.in_todays_shares()` restates every dated buy and sale before a split (`data.splits()`) in today's shares inside `replay()`, which all holdings, P&L, XIRR, validation and the performance chart go through. Undated lots can't be placed and stay as entered, with a warning. The journal scorecard restates entry, stop and target the same way.
- **Journal returns** include dividends on both sides: a price you entered is multiplied by `data.dividend_factor()` (Yahoo's adjusted / plain close on that date), matching the benchmark's adjusted prices.
- **Weekly candles** for Sunday-to-Thursday markets (`.SR .QA .KW .BH .OM`, `data.SUN_THU`) group Friday to Thursday; the UAE has traded Monday to Friday since 2022.
- **RSI** of a price that doesn't move is 50, not 100. **Portfolio risk** (volatility, drawdown, Sharpe, beta) uses each day's FX rate, like the performance chart. The Core & satellite comparison runs over the whole history, with undated lots from the first dated trade.
- Gulf fundamentals on Yahoo are patchy. Expect `None` values, which the UI shows as "—".
- The Gulf indexes `^TASI.SR` and `^GNRI.QA` return a single day of history, so the dashboard follows the KSA and QAT ETFs instead.
- European funds are listed only with an exchange suffix (VWCE doesn't exist on Yahoo, VWCE.DE does). Search adds Yahoo's results to the built-in list for this reason.
- **Inline event handlers:** an `onkeydown = (e) => cond && action()` handler returns `false` for other keys, which cancels them, so nothing can be typed. Use a block body.
- **Browser tests:** Playwright's `fill()` sets the value without key events. Use `pressSequentially()` when the point is that typing works.
- **Tests and AI:** `tests/test_smoke.py` and the servers started by `run_all.py` set `AI_PROVIDER=none`, so they never wait on the model in `.env`. `_FakeLLM` in the smoke tests is a real local HTTP server standing in for a provider.
- **Certificates on Windows:** Kaspersky on this machine (and company proxies elsewhere) re-signs HTTPS, which Python's own certificate list rejects. At startup `main._trust_windows_certificates()` writes certifi's list plus the Windows root certificates trusted for server authentication (not intermediates, not code-signing-only roots) to `data/ca-bundle.pem` and sets `SSL_CERT_FILE`, which both yfinance's curl_cffi and httpx read, so `run.bat` reaches Yahoo and cloud AI providers. An `SSL_CERT_FILE` set beforehand is kept.
- **Cache:** `db.py` keeps a copy in memory of each cache row (as its JSON text, decoded afresh on every read), so a portfolio view doesn't open SQLite for every history and rate. Change the cache only through `cache_set`/`cache_expire`/`cache_clear`/`cache_prune`, never with SQL, or the copy goes out of step. Clear cache makes the time negative (expired, age kept); at startup `cache_prune()` drops anything not refreshed for 90 days. An empty answer from Yahoo (search, fund holdings, financials, earnings) is kept for an hour, never over what was known, since a rate limit looks the same.
- **Displays say what they show:** `info()` adds `priceUnit` (`"%"` for yields like `^TNX`, `"points"` for indexes, else none) and the page formats prices with `priceOf()`, never `money()` alone. The day's change reads "Today" only when every holding's latest price is from today, else "Latest day" with the dates (`dayFrom`/`dayTo`). Estimated income is what each share actually paid over the past year (`data.dividends_past_year()`, so accumulating funds give 0), with Yahoo's yield only as a fallback. An old price (Yahoo not answering) is marked "old price" wherever it shows. A missing annual return gives its real reason (`xirrWhy`).
- **Security:** the app has no login, so the middleware answers only a localhost `Host` and refuses `/api/` requests another site made through the browser (`Sec-Fetch-Site` other than same-origin/none, or an `Origin` that isn't this host; scripts with neither pass). Anything that spends the AI allowance is a POST (the brief). Every setting goes through `db.check_settings()` on save and restore (a bad stored value falls back to the default), and anything stored is escaped with `esc()` before it goes into HTML.
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
