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
- **Spread**: the top third's average return minus the bottom third's (ties in the score broken by a fixed shuffle).

Over all dates: the mean IC and its one-sided p-value, the share of dates with IC above 0, and the mean spread. The
following windows overlap (3 months, sampled monthly), so the variance of the mean IC is scaled by the window length
(exact when months are independent) and the t is judged against Student's t with as many degrees of freedom as there
are separate windows (months / horizon - 1). *(Round 1 replaced Newey-West with h - 1 lags, which passed noise far too
often at these sample sizes: see the end.)*

**Primary horizon: 3 months.** Also reported, not used for decisions: 1 and 12 months. Over four years, 12-month
windows give only one or two separate windows, so those figures are descriptive.

**A score "works" when** its one-sided p-value is under 0.025 at the primary horizon (about t >= 2 with many
windows); under 0.16 is "suggestive"; above 0.975 is "works backwards"; otherwise "no evidence". Fewer than 12 months,
or fewer than 4 separate windows, give "not enough data". These factor verdicts describe; they aren't corrected for
the six scores and three horizons shown. Every verdict is also given with returns in local currency and, for company
figures, a 120-day publication lag.

## Candidate improvements (the complete list; nothing else is tried)

1. **Within-sector ranks** instead of fixed thresholds: each metric scored by its percentile among the stocks in the
   same sector that date (the idea already in the notes), then averaged into factors exactly as now. *(Run for each of
   the five factors and the overall score: six of the eight tests. See the end.)*
2. **Momentum adjusted for risk**: the 12-1 month return divided by the stock's volatility.
3. **Composite of the factors that work**: the overall score from only the factors judged to work, equally weighted.

Weights stay the owner's choice (Settings). The research reports what the evidence supports; it never changes them.

## Avoiding fooling ourselves

- **Hold-out.** The dates are split in time: a first (discovery) half and a second (hold-out) half, with the last
  two discovery dates left out (horizon - 1 months) so no 3-month window spans both. The discovery half chooses which
  factors the third candidate is built from; the hold-out half confirms.
- **Adopting a change** needs all of: it works on its own over all months, with a positive IC on the hold-out half;
  its gain over today's score (candidate minus today, date by date, on the same stocks) passes over all months after
  Holm's correction for the number of candidates tested (one-sided 0.025 overall); and that gain is confirmed on the
  hold-out half alone (p < 0.05). *(Round 1 replaced the first rule, which adopted a still-backwards score: see the
  end.)* With this few months the bar is high: adopting nothing means "not shown to be better", not "shown to be no
  better".
- **All results reported**, including candidates that failed.
- **Checks of the method itself** (tests): a planted signal must be found; pure noise must not be; real scores
  shuffled between stocks must give IC near 0 (placebo); noise with the real data's overlap must pass "works" about
  as rarely as it should (measured about 2% against 2.5%); a statement must never be used before its public date; a
  backwards score made less backwards must not be adopted; the IC, Student's t, Holm and overlap arithmetic must match
  hand calculations and published tables.

## Known limits (shown with the results)

- Today's list of stocks: companies that failed or left are missing.
- Four years of statements: fundamentals can be tested over about two and a half years of dates, growth over about
  one and a half. Short; a "works" there is weaker evidence than for momentum.
- The 90-day publication lag is an assumption, the same for every company.
- (Superseded, see below.) A past market value was today's scaled by the price move.
- Yahoo's figures can be revised after first publication; what's tested is today's version of past statements.
- Past results don't promise future ones.

## Changes after the fact

### Round 1 (11 Nov 2026): after the first run and an independent review

The first run (Europe, 93 stocks, 48 months) adopted two candidates. An independent review of the method then found
flaws serious enough that neither adoption could stand. Every change below fixes a flaw in the method, not a result
anyone wanted; each was made before the second run.

1. **The t-statistic was too generous.** Newey-West with `h - 1` lags under-corrects for overlapping 3-month windows
   with this few months: on made-up noise it passed t >= 2 about 6% of the time (all months), 9% (a half) and 15%
   (12-month horizon), against 2.3% intended. Longer lags didn't help at these sample sizes (measured). Now: the
   variance of the mean is scaled by the window length h (exact for independent months), and t is judged against
   Student's t with as many degrees of freedom as there are separate windows (n / h - 1). Measured by the reviewer: about 2% at 21 to 46
   months (6,000 trials each). It assumes months are independent beyond the overlap: with month-to-month persistence
   of 0.3 the rate rises to about 4%. Results are p-values (one-sided); a verdict needs at least 12 months.
2. **"Adopted" didn't require the change to work.** The rule only asked that it beat today's score, so a backwards
   Quality was "improved" into a less backwards one and adopted. Now a candidate must (a) work on its own over all
   months, with a positive IC on the hold-out half, (b) beat today's score over all months after Holm's correction for
   the number of candidates tested, and (c) beat it again on the hold-out half alone (p < 0.05).
3. **Eight candidates were tested, not three.** The plan's "within-sector ranks" was run separately for each of the
   five factors and the overall score: 6 + 2 = 8 tests. This was a departure from the plan; it's now acknowledged here
   and paid for by the Holm correction in point 2.
4. **The working-factors composite chose its factors on the wrong outcome.** Risk "works" for predicting swings, so
   the composite was built from risk alone and judged on returns. Factors are now chosen by how well they predict
   returns on the discovery half (risk included, on its return).
5. **Past market values carried later information.** Today's market value scaled by the dividend-adjusted price
   brought in dividends paid later (14% too low three years back for a 5% yielder) and today's share count (later
   buybacks). Now: the shares in the statement public that day (Yahoo restates them for splits: checked on NVDA's 2024
   split) times that day's quoted close (adjusted for splits, not dividends). A stock whose statement shares don't fit
   its listing (within half to double today's market value) gets no value score.
6. **The overall score changed meaning over time** (2 factors, then 4, then 5). It now counts only stocks with all five.
7. **Smaller fixes:** quality and growth no longer need a price or a rate; growth's "year before" must really be
   about a year before (300-430 days); EV/EBITDA needs real debt and cash figures, not zeros; a sector needs eight
   other stocks (not counting itself) for a within-sector rank; ties in the top-and-bottom-third spread are broken by a
   fixed shuffle, not list order; a return window with stale prices at its end is skipped, not shortened; one stock's
   error no longer stops the run.
8. **Added as planned but missing:** local-currency results beside the dollar ones; a placebo that shuffles real
   scores between stocks. **Added:** a 120-day publication lag as a robustness check (EU and UK rules allow four
   months), and a test that noise passes as rarely as it should.
9. **Growth is labelled "Growth (1-year)".** The live score uses three years where it can; four statements can't test
   that, so the research's growth verdict says nothing about the live growth score.

### Round 1b (11 Nov 2026): after a second look by the same reviewer

The reviewer confirmed the fixes above (noise now passes "works" about 2% of the time at every sample size measured)
and found these, all fixed before the run that's quoted:

1. **The test for "don't adopt a still-backwards score" didn't exercise the rule**: the within-sector step overwrote
   its made-up scores, so it failed for "not enough data". It now keeps them and checks the reason given.
2. **A verdict needs at least 4 separate windows**, not only 12 months: 12-month windows over four years give one or
   two, so those read "not enough data".
3. **The working-factors composite is compared on the same stocks** as the overall score (only where that exists).
4. **Share counts are checked every year**: the latest times today's price must be within 2/3 to 1.5 times today's
   market value (was 1/2 to 2), and no year may jump outside that band against the year before. A jump means a split
   Yahoo didn't restate, or a large issue; either way that stock gets no value score rather than a wrong one. "Share
   Issued" (which counts treasury shares) is no longer used in place of "Ordinary Shares Number".
5. **Stocks skipped while Yahoo asks for a pause are counted** in the results, not dropped silently.
6. **The adoption rule, as stated above, no longer compares on the discovery half**: it's all months plus the hold-out
   alone. That differs from the original "discovery, then confirm" design and is recorded here as such.
7. This section and the main text now describe the method in use, with what each replaced marked in place.

---

# Round 2: design (pre-registered 11 Nov 2026, before any round 2 data was fetched)

Round 1 (Europe, 93 stocks, four years) adopted nothing, and the reviewer's point stands: with so little data,
"not shown to be better" isn't "shown to be no better". Round 2 adds data and independent confirmation, and tests at
most three changes, each with a published basis, so a pass means more than one lucky period in one region.

## What changes from round 1

1. **Every stock in the list**: all five regions (407 stocks), whatever Settings follows.
2. **Ten years of prices** for momentum and risk (Yahoo's full history, cut to ten years). Company figures stay at
   Yahoo's four annual statements, so value, quality and growth gain stocks but not years.
3. **Confirmation on other continents.** Decisions rest on **North America and Asia-Pacific** (220 stocks); they're
   confirmed on **Europe, the Middle East & Africa and Latin America** (187 stocks), which play no part in deciding.
   Each date's IC is worked out within each group separately. The time halves of round 1 are still reported.
4. **Three candidates only, each from published research:**
   1. *Quality as gross profitability*: gross profit / total assets (Novy-Marx, 2013), in place of today's quality
      metrics. Scored 0 at 0 and 100 at 0.6. Banks have no gross profit and get none, as now.
   2. *Quality as profitability only*: today's quality without debt / equity and the current ratio, so return on
      equity and the two margins (profitability predicts returns in the Fama-French five-factor work; leverage and
      liquidity have no comparable record).
   3. *Momentum adjusted for risk*: the 12-1 month return divided by volatility (in the spirit of Barroso and
      Santa-Clara, 2015). Round 1's near miss, retested unchanged on new stocks and years.

## Adoption rule

All three, at the 3-month horizon:
- on the **deciding regions**, the gain over today's score (date by date, same stocks) passes Holm's correction for
  the three candidates (one-sided 0.025 overall);
- on the **confirming regions**, the candidate works on its own (p < 0.025) and its gain over today's score has
  p < 0.05;
- the gain is positive in every confirming region with at least 15 stocks a date.

## Unchanged from round 1

Point-in-time records and the 90-day lag (120 days as a check), past market value from statement shares and quoted
prices, rank IC, the overlap-aware t on Student's t, the 12-month and 4-window minimums, dollar returns with a
local-currency check, and all the method tests.

## Known limits added

- Ten years of today's list: survivorship and hindsight in choosing the list weigh more over ten years than four.
- Asia-Pacific and Latin America have more missing company figures on Yahoo; the stocks scored each date are reported.
- The deciding and confirming regions overlap in time, so a worldwide regime (2022's rate shock) touches both; the
  time halves are shown as well for that reason.

## Round 2 changes after the fact

### Round 2b (11 Nov 2026): after an independent review of the round 2 code, before any round 2 result was seen

A run with the code as first written was started before the review came back; its output was never looked at, and
it's stored under a name the page doesn't read. Every change below was decided from the review alone.

1. **Outcomes are measured against the country's average that month.** As designed, each month's IC was one ranking
   across many countries with dollar returns, so a score leaning towards the country whose market or currency did
   well (the US against Japan, say) would look like a good stock picker. Now each stock's outcome (return, or swings
   for risk) is taken against the average of the scored stocks in its country of listing that month; countries with
   fewer than three scored stocks that month are left out. This applies to every IC: everywhere, by group, by region.
   It departs from the design's "worked out within each group".
2. **A decision needs a complete run.** Every stock that doesn't load is counted, by reason (Yahoo's pause, a recent
   listing with under a year of prices, a failure). If any were skipped, or a region loaded under 95% of its listed
   stocks (recent listings aside), the run shows its evidence but adopts nothing.
3. **The decision is made once.** The first complete run is kept as it is (beside the database, not in the cache)
   and shown from then on; it isn't recomputed daily, which would let a decision flip until something passed.
4. **Holm stays at three tests**: a candidate with no p on the deciding regions counts as p = 1.
5. **"Every confirming region with at least 15 stocks a date"** is read as: a region counts when it has at least 12
   months with 15 or more stocks scored by both the candidate and today's score. The reason given names the regions
   that counted whenever one didn't.
6. **Each score's time halves split its own months.** Company figures start in 2023 and prices in 2017, so one split
   for all left value, quality, growth and both quality candidates with nothing in the first half. The quality
   candidates rest on 2023-26 everywhere: confirmed across stocks, not across time (said in the caveats).
7. **Gross profitability leaves out all financial companies** (sector "Financial Services": banks, insurers, asset
   managers), as Novy-Marx does; their gross profit and assets don't compare. Adopting it would take banks' quality
   score away (today they're scored on return on equity), and the design should have said so. It's also checked at
   the 120-day lag.
8. **The background build**: the cache is checked again inside the lock (so two runs can't start together), and a
   failed run is shown as a failure, not as "building".

