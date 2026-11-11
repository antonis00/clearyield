# Making the scores stronger: research design

Written and committed **before** any result was looked at, so the tests below can't be bent to fit the data.
Any change to this plan after results are seen is listed under "Changes after the fact" with its reason.

## The question

Do ClearYield's five factor scores rank the stocks you follow by what happens next, and which changes, if any, make
them rank better? "Better" means more reliable at sorting future returns (value, quality, growth, momentum, the
overall score) or future swings (risk, which promises a calmer ride, not more return).

## What can be tested

| Factor | Rebuilt for past dates from | Left out (no past figure on Yahoo) |
|---|---|---|
| Momentum | daily prices (dividends included) | — |
| Risk | daily prices | beta (Yahoo has only today's) |
| Value | annual statements + price that day: P/E, P/B, FCF yield, EV/EBITDA | forward P/E |
| Quality | annual statements: ROE, operating and net margin, debt/equity, current ratio | — |
| Growth | annual statements: revenue and earnings growth over one year | forward EPS; 3-year growth (only 4 years kept) |

Each past date is scored with **today's code and thresholds** (`scoring.factor_breakdown`), fed a "point-in-time"
record built only from what was public that day.

**No look-ahead.** A fiscal year's statements count as public 90 days after the year ends (most companies publish
within 60-90 days; the Gulf within 90). Prices are the close on or before the date. Sector and industry are today's
(they rarely change). Shares outstanding come from the same annual statement as the figures.

## Data

- The stocks in the regions followed in Settings (today's list: survivorship bias, see limits).
- Five years of daily adjusted closes (total return), converted to US dollars at each day's rate, so a country's
  currency or inflation (Turkey) doesn't pass for a stock signal. Local-currency results are a robustness check.
- Yahoo's last four annual statements (income, balance sheet, cash flow), fetched once and kept 14 days.

## How a score is judged

Scores are taken at the start of every month. For each date:

- **Rank IC**: the Spearman rank correlation between the score and the following return (for risk: the following
  volatility, sign reversed, so positive is good for every factor). Rank, not value, so a few extreme stocks can't
  drive it.
- **Spread**: the top third's average return minus the bottom third's, each against the average of all stocks that
  date (so a rising market doesn't count as skill).

Over all dates: the mean IC, its t-statistic with Newey-West standard errors (the following windows overlap: lags =
horizon in months - 1), the share of dates with IC above 0, and the mean spread.

**Primary horizon: 3 months.** Also reported, not used for decisions: 1 and 12 months.

**A score "works" when** the mean IC is above 0 with t >= 2 at the primary horizon. Between 1 and 2 is "suggestive";
below 1 is "no evidence"; t <= -2 is "works backwards".

## Candidate improvements (the complete list; nothing else is tried)

1. **Within-sector ranks** instead of fixed thresholds: each metric scored by its percentile among the stocks in the
   same sector that date (the idea already in the notes), then averaged into factors exactly as now.
2. **Momentum adjusted for risk**: the 12-1 month return divided by the stock's volatility.
3. **Composite of the factors that work**: the overall score from only the factors judged to work, equally weighted.

Weights stay the owner's choice (Settings). The research reports what the evidence supports; it never changes them.

## Avoiding fooling ourselves

- **Hold-out.** The dates are split in time: the first half is for discovery, the second half is never looked at
  until a decision is made on the first. A candidate is adopted only if it beats today's score on the discovery half,
  and then also on the hold-out half, and over all dates the paired difference in IC (candidate minus today, per date)
  has t >= 2.
- **All results reported**, including candidates that failed.
- **Checks of the method itself** (tests): a planted signal must be found; pure noise must not be (t near 0); scores
  shuffled between stocks must give IC near 0 (placebo); a statement must never be used before its public date; the
  Newey-West and IC arithmetic must match hand calculations.

## Known limits (shown with the results)

- Today's list of stocks: companies that failed or left are missing.
- Four years of statements: fundamentals can be tested over about two and a half years of dates, growth over about
  one and a half. Short; a "works" there is weaker evidence than for momentum.
- The 90-day publication lag is an assumption, the same for every company.
- Yahoo's figures can be revised after first publication; what's tested is today's version of past statements.
- Past results don't promise future ones.

## Changes after the fact

(none yet)
