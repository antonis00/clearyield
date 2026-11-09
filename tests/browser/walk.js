// Walks every screen in Chrome with real clicks. Run through tests/run_all.py, which starts a server with its own database.
// Usage: node walk.js <base> <outDir> <live|demo>
const { chromium } = require("playwright-core");
const fs = require("fs");
const [BASE, OUT, MODE] = process.argv.slice(2);
fs.mkdirSync(OUT, { recursive: true });
let pass = 0, fail = 0;
const expect = (name, ok, extra = "") => { ok ? pass++ : fail++; console.log(`${ok ? "PASS" : "FAIL"} ${name}${extra !== "" ? " :: " + String(extra).slice(0, 170) : ""}`); };
const SLOW = 150000;

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const p = await ctx.newPage();
  const errs = [], http = [], expected404 = /NOSUCHTICKER|stock\/VWCE\?/;
  p.on("pageerror", (e) => errs.push(e.message));
  p.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errs.push("console: " + m.text()); });
  let refusalExpected = null; // a step that provokes an error on purpose names the request here
  p.on("response", (r) => { const u = r.url(); if (u.startsWith(BASE) && r.status() >= 400 && !u.endsWith("favicon.ico") && !expected404.test(u) && !(refusalExpected && refusalExpected.test(u))) http.push(`${r.status()} ${u.replace(BASE, "/")}`); });
  let dialogAnswer = null;
  p.on("dialog", (d) => (d.type() === "prompt" ? d.accept(dialogAnswer ?? "") : d.accept()));
  const nav = async (v) => { await p.click(`[data-view="${v}"]`); await p.waitForTimeout(350); };
  const txt = (s) => p.evaluate((x) => document.querySelector(x)?.innerText?.replace(/\s+/g, " ").trim() ?? null, s);
  const shot = (n) => p.screenshot({ path: `${OUT}/${MODE}_${n}.png`, fullPage: true });
  const canvasDrawn = (s) => p.evaluate((x) => { const c = document.querySelector(x); if (!c) return false; const d = c.getContext("2d")?.getImageData(0, 0, c.width, c.height).data; if (!d) return true; for (let i = 3; i < d.length; i += 4) if (d[i]) return true; return false; }, s);
  const research = async (t) => { await p.fill("#search, input[placeholder*='Search']", ""); await p.evaluate((x) => { location.hash = `#research/${encodeURIComponent(x)}`; }, t); await p.waitForFunction((x) => { const e = document.querySelector("#researchEmpty"); if (e && !e.classList.contains("hidden") && e.textContent.trim()) return true; return document.querySelector("#rSym")?.textContent === x && document.querySelector("#researchBody").style.opacity === "1" && !document.querySelector("#newsList .spinner"); }, t, { timeout: SLOW }); await p.waitForTimeout(600); };

  await p.goto(BASE, { waitUntil: "load" }); await p.waitForTimeout(600);
  // A non-USD base exercises the currency conversions
  if (MODE === "live") await p.evaluate(() => fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ base_currency: "EUR" }) }));
  await p.reload(); await p.waitForTimeout(600);
  const meta = await p.evaluate(async () => (await fetch("/api/meta")).json());
  expect(`mode is ${MODE}`, meta.demo === (MODE === "demo"), `base ${meta.settings.base_currency}, AI ${meta.ai}`);

  // ---- Dashboard
  await nav("dashboard");
  await p.waitForFunction(() => document.querySelectorAll("#macroStrip .mcard").length > 5, null, { timeout: SLOW });
  expect("dashboard: macro strip", (await p.locator("#macroStrip .mcard").count()) >= 10, await p.locator("#macroStrip .mcard").count());
  // A middle click opens the stock in a new tab and leaves this one where it was
  const [tab] = await Promise.all([ctx.waitForEvent("page", { timeout: 15000 }), p.locator("#macroStrip .mcard").first().click({ button: "middle" })]);
  const tile = await p.locator("#macroStrip .mcard").first().getAttribute("data-stock");
  await tab.waitForFunction(() => document.querySelector("#rSym")?.textContent, null, { timeout: SLOW });
  expect("dashboard: middle click opens a new tab", (await tab.evaluate(() => document.querySelector("#rSym").textContent)) === tile && (await p.evaluate(() => location.hash)) !== `#research/${tile}`, tile);
  await tab.close();

  // ---- Research: every kind of asset
  for (const t of (MODE === "live" ? ["AAPL", "AZN.L", "SAP.DE", "QNBK.QA", "2222.SR", "EMAAR.AE", "NBK.KW", "SPY", "BTC-USD", "TSM", "^VIX", "^TASI.SR"] : ["AAPL", "AZN.L", "QNBK.QA", "SPY", "BTC-USD"])) {
    await research(t);
    const r = await p.evaluate(() => ({ price: document.querySelector("#rPrice").textContent, cov: document.querySelector("#rCoverage").textContent, stance: document.querySelector("#rStance").innerText.replace(/\s+/g, " "), charts: document.querySelectorAll("#priceChart canvas").length, stats: document.querySelectorAll("#statGrid > div").length, bad: /undefined|NaN|\[object/.test(document.querySelector("#researchBody").innerText) }));
    if (t === "SPY") { // a fund says why value, quality and growth have no score, instead of a bare "n/a"
      const { gaps, why } = await p.evaluate(() => ({ gaps: [...document.querySelectorAll(".factor .gap")].map((g) => g.textContent), why: document.querySelector("#rStance .info")?.dataset.tip || "" }));
      expect("research: a fund explains its missing factors", gaps.length === 3 && gaps.every((g) => /Doesn't apply to funds/.test(g)) && /momentum and risk/.test(why), gaps[0]);
    }
    expect(`research ${t}`, r.price && r.charts > 0 && r.stats >= 2 && !r.bad, `${r.price} | ${r.stance} | ${r.cov}${r.bad ? " | BAD TEXT" : ""}`);
    if (["AAPL", "SPY", "BTC-USD"].includes(t)) {  // company statements and a DCF only for a company; the neighbour fills the row
      const lay = await p.evaluate(() => ({ cls: document.querySelector("#rTags").innerText, fin: !document.querySelector("#finCard").classList.contains("hidden"), dcf: !document.querySelector("#dcfCard").classList.contains("hidden"),
        statsWide: document.querySelector("#statGrid").closest(".card").getBoundingClientRect().width / document.querySelector("#statGrid").closest(".grid2").getBoundingClientRect().width }));
      const company = t === "AAPL";
      expect(`research ${t}: fundamentals and DCF ${company ? "shown" : "hidden"}`, lay.fin === company && lay.dcf === company && (company ? lay.statsWide < 0.6 : lay.statsWide > 0.99), `width ${lay.statsWide.toFixed(2)}`);
    }
  }
  // A yield reads in percent and an index in points, not in dollars
  await research("^TNX");
  expect("research: a yield shows in percent", /^\d[\d,.]*%$/.test(await txt("#rPrice")), await txt("#rPrice"));
  await research("^GSPC");
  expect("research: an index shows in points", /^\d[\d,.]*$/.test(await txt("#rPrice")) && !/\$/.test(await txt("#statGrid")), await txt("#rPrice"));
  // A question typed in the top search box runs in the Screener, instead of being looked up as a symbol
  await p.fill("#globalSearch", "gulf dividend payers yielding over 4%"); await p.waitForTimeout(700); await p.press("#globalSearch", "Enter");
  await p.waitForFunction(() => !document.querySelector("#view-screener").classList.contains("hidden") && /matches out of/.test(document.querySelector("#scrInfo").innerText), null, { timeout: SLOW });
  expect("search: a question runs in the Screener", (await p.inputValue("#scrQuery")) === "gulf dividend payers yielding over 4%" && /Min yield: 4(\.0)?%/.test(await txt("#scrInfo")), (await txt("#scrInfo")).slice(0, 80));
  await p.reload(); await p.waitForTimeout(500);  // the question filled the screener's filters; later steps start clean
  await research("^GSPC");
  // The mouse wheel scrolls the page over a chart; only Ctrl + wheel zooms it (and the RSI and MACD follow)
  const span = () => p.evaluate(() => state.lw.map((c) => { const r = c.timeScale().getVisibleLogicalRange(); return r ? Math.round((r.to - r.from) * 100) / 100 : null; }));
  await p.evaluate(() => window.scrollTo(0, 0));
  const box = await p.locator("#priceChart").boundingBox();
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const before = await span();
  await p.mouse.wheel(0, 300); await p.waitForTimeout(300);
  const plain = { span: await span(), scrolled: await p.evaluate(() => window.scrollY) };
  await p.evaluate(() => window.scrollTo(0, 0)); await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await p.keyboard.down("Control"); await p.mouse.wheel(0, -300); await p.keyboard.up("Control"); await p.waitForTimeout(300);
  const ctrl = { span: await span(), scrolled: await p.evaluate(() => window.scrollY) };
  expect("research: wheel scrolls the page, Ctrl + wheel zooms the charts", plain.span[0] === before[0] && plain.scrolled > 0
    && ctrl.span[0] < before[0] && ctrl.span.every((s) => s === ctrl.span[0]) && ctrl.scrolled === 0, `${before[0]} bars → plain ${plain.span[0]} (page moved ${plain.scrolled}px) → Ctrl ${ctrl.span.join("/")}`);
  if (MODE === "live") {  // demo mode invents data for any symbol, so there is no unknown ticker there
    await research("NOSUCHTICKER");
    expect("research: unknown ticker explains itself", /No price data|didn't return prices/.test(await txt("#researchEmpty")) && !/Expecting value/.test(await txt("#researchEmpty")), await txt("#researchEmpty"));
  }

  if (MODE === "live") {  // a symbol outside the built-in list, the way you'd really search for it
    await p.fill("#globalSearch", "vwce");
    await p.waitForFunction(() => /VWCE\.DE/.test(document.querySelector("#searchResults").textContent), null, { timeout: 30000 });
    await p.press("#globalSearch", "Enter");
    await p.waitForFunction(() => document.querySelector("#rSym")?.textContent === "VWCE.DE" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: SLOW });
    await p.waitForTimeout(800);
    expect("research: search finds an ETF outside the built-in list", /ETFs/.test(await txt("#rTags")) && !/undefined|NaN/.test(await txt("#researchBody")), `${await txt("#rName")} | ${await txt("#rPrice")} | ${await txt("#rTags")}`);
    await research("VWCE");
    expect("research: a symbol with no data suggests the right one", /VWCE\.DE/.test(await txt("#researchEmpty")), await txt("#researchEmpty"));
  }
  await research("AAPL");
  for (const per of ["1d", "5d", "1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y"]) {
    const btn = p.locator(`#periodSeg button[data-p="${per}"]`);
    await btn.click(); await p.waitForFunction(() => document.querySelector("#researchBody").style.opacity === "1", null, { timeout: SLOW }); await p.waitForTimeout(250);
  }
  expect("research: all chart periods", (await p.locator("#priceChart canvas").count()) > 0);
  expect("research: long ranges use weekly candles", await p.evaluate(() => state.stock.candles.bars === "week" && document.querySelector('#barSeg [data-b="week"]').classList.contains("on")));
  await p.click('#barSeg [data-b="day"]'); await p.waitForFunction(() => state.stock.candles.bars === "day", null, { timeout: SLOW });
  expect("research: switch back to daily candles", await p.evaluate(() => state.stock.candles.time.length > 1000));
  for (const k of ["area", "line", "candle"]) {
    await p.click(`#kindSeg [data-k="${k}"]`); await p.waitForTimeout(250);
    expect(`research: ${k} chart drawn`, await p.evaluate((x) => document.querySelector(`#kindSeg [data-k="${x}"]`).classList.contains("on"), k) && await canvasDrawn("#priceChart canvas"));
  }
  await p.locator("#periodSeg button[data-p='1d']").click(); await p.waitForFunction(() => state.stock.candles.intraday, null, { timeout: SLOW }); await p.waitForTimeout(300);
  const intra = await p.evaluate(() => ({ n: state.stock.candles.time.length, note: state.stock.candles.note, panes: document.querySelector("#indPanes").classList.contains("hidden") }));
  expect("research: 1D shows candles within the day, without the daily indicators", intra.n > 0 && intra.panes && (await p.locator("#priceChart canvas").count()) > 0, JSON.stringify(intra));
  await p.locator("#periodSeg button[data-p='1y']").click(); await p.waitForFunction(() => document.querySelector("#researchBody").style.opacity === "1", null, { timeout: SLOW });
  for (const id of ["#tSma", "#tBb", "#tVol"]) { await p.click(id); await p.waitForTimeout(120); }
  // Each factor explains itself on hover, and the icon doesn't open the drill-down
  await p.locator("#factorList .info").nth(3).hover(); await p.waitForTimeout(150);
  const factorTip = await p.evaluate(() => ({ n: document.querySelectorAll("#factorList .info").length, tip: document.querySelector("#tooltip:not(.hidden)")?.textContent || "", open: document.querySelectorAll(".factor.open").length }));
  expect("research: factor info on hover", factorTip.n === 5 && /^Momentum: .*200-day/.test(factorTip.tip) && factorTip.open === 0, factorTip.tip.slice(0, 60));
  await p.mouse.move(0, 0);
  await p.locator(".factor .fh").first().click(); await p.waitForTimeout(100);
  expect("research: factor drill-down shows thresholds", /0 at 40, 100 at 10/.test(await txt(".factor.open .parts")), (await txt(".factor.open .parts"))?.slice(0, 90));
  expect("research: radar drawn", await canvasDrawn("#radar"));
  await p.fill("#dGrowth", "8"); await p.click("#btnDcf"); await p.waitForTimeout(1200);
  expect("research: DCF recalculates", /Fair value|needs positive/.test(await txt("#dcfOut")), await txt("#dcfOut"));
  await p.click("#btnBrief"); await p.waitForFunction(() => !document.querySelector("#btnBrief").disabled, null, { timeout: SLOW }); await p.waitForTimeout(300);
  expect("research: brief renders", (await txt("#briefOut")).length > 80, (await txt("#briefOut")).slice(0, 80));
  expect("research: news listed", (await p.locator("#newsList li").count()) > 0);
  await p.click("#btnWatch"); await p.waitForTimeout(1200);
  expect("research: add to watchlist", /Watching/.test(await txt("#btnWatch")));
  await p.click("#btnAlert"); await p.waitForTimeout(250);
  await p.fill("#modalCard input[name='value']", "1"); await p.click("#modalCard form button.btn:not(.ghost)"); await p.waitForTimeout(800);
  await p.click("#btnHold"); await p.waitForTimeout(250);
  await p.fill("#modalCard input[name='shares']", "10"); await p.fill("#modalCard input[name='cost']", "150"); await p.fill("#modalCard input[name='bought']", "2024-06-03");
  await p.click("#modalCard form button.btn:not(.ghost)"); await p.waitForTimeout(1200);
  await p.fill("#ptAmount", "2000"); await p.fill("#ptStop", "100"); await p.click("#btnPretrade");
  await p.waitForFunction(() => !document.querySelector("#btnPretrade").disabled, null, { timeout: SLOW }); await p.waitForTimeout(300);
  expect("research: pre-trade check", /Weight of this asset/.test(await txt("#ptOut")), (await txt("#ptOut")).slice(0, 110));
  await shot("research");

  // ---- Compare
  await nav("compare");
  await p.fill("#cmpTickers", "AAPL,SAP.DE,QNBK.QA,BTC-USD"); await p.click("#btnCompare");
  await p.waitForFunction(() => !document.querySelector("#btnCompare").disabled, null, { timeout: SLOW }); await p.waitForTimeout(500);
  expect("compare: table and correlation", (await p.locator("#cmpTable tbody tr").count()) === 4 && (await p.locator("#cmpCorr table tr").count()) === 5);
  expect("compare: chart drawn", await canvasDrawn("#cmpChart"));
  const cmpLayout = await p.evaluate(() => {
    const t = document.querySelector("#cmpTable"), a = t.closest(".card").getBoundingClientRect(), b = document.querySelector("#cmpCorr").closest(".card").getBoundingClientRect();
    return { stacked: b.top >= a.bottom - 1, fullWidth: Math.abs(a.width - b.width) < 1, scroll: t.scrollWidth - t.clientWidth };
  });
  expect("compare: metrics and correlation stack, full width, no sideways scroll", cmpLayout.stacked && cmpLayout.fullWidth && cmpLayout.scroll <= 0, JSON.stringify(cmpLayout));
  await shot("compare");

  // ---- Screener
  await nav("screener");
  await p.check('#scrMarkets input[value="Crypto"]');
  await p.fill("#fPe", "25"); await p.fill("#fRoe", "10"); await p.click("#btnScr");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: 400000 }); await p.waitForTimeout(400);
  const scr = await p.evaluate(() => [...document.querySelectorAll("#scrTable tbody tr")].map((r) => r.innerText));
  expect("screener: filters narrow the list", scr.length > 0 && scr.length < 80, `${scr.length} rows | ${await txt("#scrInfo")}`.slice(0, 170));
  await p.click("#scrTable thead th:nth-child(5)"); await p.waitForTimeout(200);
  await p.fill("#fPe", ""); await p.fill("#fRoe", ""); await p.click("#btnScr");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: 400000 }); await p.waitForTimeout(400);
  const allRows = await p.locator("#scrTable tbody tr").count();
  expect("screener: full universe, nothing failed", allRows >= 89 && !/No data:/.test(await txt("#scrInfo")), `${allRows} rows`);
  const width = await p.evaluate(() => { const t = document.querySelector("#scrTable"); return [t.clientWidth, t.scrollWidth]; });
  expect("screener: Score and Stance on screen at 1440px", width[1] <= width[0], width.join("/"));
  await shot("screener");
  await p.fill("#scrQuery", ""); await p.locator("#scrQuery").pressSequentially("cheap profitable dividend stocks in the gulf");
  expect("screener: you can type in the question box", (await p.inputValue("#scrQuery")) === "cheap profitable dividend stocks in the gulf", await p.inputValue("#scrQuery"));
  await p.press("#scrQuery", "Enter");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: SLOW }); await p.waitForTimeout(400);
  expect("screener: plain-language query", /Gulf/.test(await txt("#scrInfo")), await txt("#scrInfo"));
  if (await p.locator("#scrTable tbody tr").count()) { await p.locator("#scrTable tbody tr").first().click(); await p.waitForTimeout(700); expect("screener: row opens Research", (await p.evaluate(() => location.hash)).startsWith("#research")); }

  // ---- Portfolio
  await nav("portfolio");
  for (const [t, s, c, d] of [["AZN.L", 10, 11000, "2024-06-03"], ["QNBK.QA", 500, 15, "2024-06-03"], ["SPY", 5, 500, ""], ["BTC-USD", 0.02, 60000, "2025-01-02"]]) {
    await p.fill("#hTicker", t); await p.fill("#hShares", String(s)); await p.fill("#hCost", String(c)); await p.fill("#hDate", d); await p.click("#btnAddHolding"); await p.waitForTimeout(2500);
  }
  await p.waitForFunction(() => document.querySelectorAll("#pfTable tbody tr").length === 5, null, { timeout: SLOW }); await p.waitForTimeout(900);
  expect("portfolio: date field clears after adding a lot", (await p.inputValue("#hDate")) === "");
  const pf = await p.evaluate(async () => (await fetch("/api/portfolio")).json());
  const sum = pf.positions.reduce((a, r) => a + r.value, 0);
  expect("portfolio: 5 positions, all priced", pf.positions.length === 5 && !pf.incomplete, `total ${Math.round(pf.total)} ${pf.base}, P&L ${Math.round(pf.pnl)}`);
  expect("portfolio: positions add up to the total", Math.abs(sum - pf.total) < 1e-6);
  for (const k of ["assetClass", "region", "sector", "currency", "bucket"]) expect(`portfolio: ${k} allocation adds up`, Math.abs(Object.values(pf.allocation[k]).reduce((a, b) => a + b, 0) - pf.total) < 1e-6);
  expect("portfolio: risk figures present", pf.risk.volatility != null && pf.risk.sharpe != null && pf.risk.beta != null && pf.performance.dates.length > 200, `vol ${pf.risk.volatility?.toFixed(3)} beta ${pf.risk.beta?.toFixed(2)}, ${pf.performance.dates.length} days`);
  expect("portfolio: undated lot warned about", pf.base === "USD" ? true : pf.warnings.some((w) => w.includes("SPY") && w.includes("exchange rate")), pf.warnings.find((w) => w.includes("exchange rate")) ?? "(USD base)");
  expect("portfolio: rebalancing shown", (await p.locator("#pfRebal tbody tr").count()) >= 3);
  for (const id of ["#pfPerf", "#aAsset", "#aRegion", "#aSector", "#aCur"]) expect(`portfolio: chart ${id}`, await canvasDrawn(id));
  // Core and satellite: SPY is core by default, the rest satellite; the risk shares add up; the comparison is drawn
  const cs = pf.coreSatellite, spy = pf.positions.find((r) => r.ticker === "SPY");
  expect("portfolio: core is the index fund", spy.bucket === "core" && Math.abs(cs.core - spy.value / pf.total) < 1e-9 && cs.action, `core ${(cs.core * 100).toFixed(1)}%, ${cs.action}`);
  expect("portfolio: risk shares add up", Math.abs(cs.riskShare.core + cs.riskShare.satellite - 1) < 1e-9, JSON.stringify(cs.riskShare));
  expect("portfolio: core vs satellite compared", cs.performance && (await canvasDrawn("#csPerf")) && /Since \d{4}-\d\d-\d\d/.test(await txt("#csSplit")), (await txt("#csSplit"))?.slice(-120));
  await p.click('[data-bucket="SPY"]'); await p.waitForFunction(() => document.querySelector('[data-bucket="SPY"]')?.textContent === "Satellite", null, { timeout: SLOW });
  expect("portfolio: switching a holding's bucket", /Core 0\.0%/.test(await txt("#csSplit")) && (await p.evaluate(() => location.hash)) === "#portfolio", (await txt("#csSplit"))?.slice(0, 60));
  await p.click('[data-bucket="SPY"]'); await p.waitForFunction(() => document.querySelector('[data-bucket="SPY"]')?.textContent === "Core", null, { timeout: SLOW }); await p.waitForTimeout(400);
  // The day's change names its day unless everything is from today; stocks and crypto can differ at a weekend
  const dayKpi = await p.evaluate(() => [...document.querySelectorAll("#pfKpis .kpi")].map((k) => k.innerText.replace(/\s+/g, " ")).find((t) => /^(Today|Latest day)/i.test(t)) || "");
  const today = await p.evaluate(() => new Date().toLocaleDateString("en-CA"));
  expect("portfolio: the day's change says which day", pf.dayFrom === today && pf.dayTo === today ? /^Today/i.test(dayKpi) : /^Latest day/i.test(dayKpi) && /\d/.test(dayKpi.replace(/^Latest day\s*\S+/i, "")), `${dayKpi} | ${pf.dayFrom}–${pf.dayTo}`);
  expect("portfolio: drift isn't coloured as gain or loss", !(await p.locator("#pfRebal td:nth-child(4) .up, #pfRebal td:nth-child(4) .down").count()));
  expect("portfolio: no bad text", !/undefined|NaN|\[object/.test(await txt("#view-portfolio")));
  await shot("portfolio");
  // A sale and a dividend through the form
  await p.selectOption("#hKind", "sell"); await p.fill("#hTicker", "QNBK.QA"); await p.fill("#hShares", "100"); await p.fill("#hCost", "18"); await p.fill("#hDate", "2025-06-02"); await p.click("#btnAddHolding"); await p.waitForTimeout(3000);
  await p.selectOption("#hKind", "dividend"); await p.fill("#hTicker", "QNBK.QA"); await p.fill("#hCost", "120"); await p.fill("#hDate", "2025-06-02"); await p.click("#btnAddHolding"); await p.waitForTimeout(3000);
  await p.selectOption("#hKind", "buy");
  const led = await p.evaluate(async () => (await fetch("/api/portfolio")).json());
  expect("portfolio: sale and dividend recorded", led.positions.find((x) => x.ticker === "QNBK.QA").shares === 400 && led.realised !== 0 && led.dividends > 0, `realised ${Math.round(led.realised)}, dividends ${Math.round(led.dividends)} ${led.base}`);
  expect("portfolio: no annual return while a lot has no date", led.xirr === null && /Needs a date on every purchase and sale/.test(await txt("#pfKpis")), await txt("#pfKpis"));
  expect("portfolio: transaction list", (await p.locator("#txTable tbody tr").count()) === 7);
  expect("portfolio: look-through adds up", Math.abs(Object.values(led.allocation.sectorLookThrough).reduce((a, b) => a + b, 0) - led.total) < 1e-6, Object.keys(led.allocation.sectorLookThrough).slice(0, 4).join(", "));
  await shot("ledger");
  await p.click("#pfTable thead th:nth-child(6)"); await p.waitForTimeout(200);
  await p.locator('#pfTable tbody tr:has-text("SPY") [data-lots]').click(); await p.waitForTimeout(300);
  await p.locator("#modal [data-del]").first().click(); await p.waitForFunction(() => document.querySelectorAll("#pfTable tbody tr").length === 4, null, { timeout: SLOW });
  expect("portfolio: delete a lot after sorting", true);

  // ---- Backtest
  await nav("backtest");
  for (const s of ["buy_hold", "sma_cross", "rsi_reversion", "momentum", "dca"]) {
    for (const per of s === "sma_cross" ? ["1y", "5y", "max"] : ["2y"]) {
      await p.fill("#bTicker", "SPY"); await p.selectOption("#bStrat", s); await p.selectOption("#bPeriod", per); await p.click("#btnBt");
      await p.waitForFunction(() => !document.querySelector("#btnBt").disabled, null, { timeout: SLOW }); await p.waitForTimeout(250);
      const t = await txt("#btTable");
      expect(`backtest ${s} ${per}`, /Total return/.test(t) && !/undefined|NaN/.test(t) && (await canvasDrawn("#btChart")), t.slice(0, 95));
    }
  }
  await shot("backtest");

  // ---- Journal & alerts
  await nav("journal");
  await p.fill("#jTicker", "MSFT"); await p.selectOption("#jAction", "buy"); await p.fill("#jPrice", "400"); await p.fill("#jThesis", "walk test"); await p.fill("#jInval", "growth slows"); await p.fill("#jStop", "300"); await p.fill("#jTarget", "900"); await p.fill("#jReview", "2020-01-01");
  await p.click("#btnJournalAdd"); await p.waitForFunction(() => /walk test/.test(document.querySelector("#journalList").innerText), null, { timeout: SLOW }); await p.waitForTimeout(400);
  expect("journal: entry shows return since entry", /since entry/.test(await txt("#journalList")), (await txt("#journalList")).slice(0, 110));
  expect("journal: scorecard waits for a day", /check back/.test(await txt("#jScore")));
  dialogAnswer = "learned something"; await p.click("[data-out]"); await p.waitForFunction(() => /learned something/.test(document.querySelector("#journalList").innerText), null, { timeout: 20000 });
  expect("journal: record an outcome", true);
  await p.fill("#aTicker", "SPY"); await p.selectOption("#aKind", "price_above"); await p.fill("#aValue", "0.01"); await p.click("#btnAlertAdd"); await p.waitForTimeout(800);
  await p.click("#btnCheckAlerts"); await p.waitForTimeout(3000);
  expect("alerts: fire on Check now", /triggered/.test(await txt("#alertTable")), await txt("#toast"));
  await shot("journal");
  const del = await p.evaluate(() => { const b = document.querySelector("#alertTable [data-adel]").getBoundingClientRect(), w = document.querySelector("#alertTable").getBoundingClientRect(); return [Math.round(b.right), Math.round(w.right)]; });
  expect("alerts: Delete button in view", del[0] <= del[1], del.join("/"));
  await p.click("#alertTable thead th:nth-child(1)"); await p.waitForTimeout(150);
  const alertsBefore = await p.locator("#alertTable [data-adel]").count();
  await p.locator("#alertTable [data-adel]").first().click(); await p.waitForTimeout(900);
  expect("alerts: delete after sorting", (await p.locator("#alertTable [data-adel]").count()) === alertsBefore - 1);
  await p.locator("[data-jdel]").first().click(); await p.waitForTimeout(900);
  expect("journal: delete an entry", !/walk test/.test(await txt("#journalList")));

  // ---- Dashboard again, now with data
  await nav("dashboard"); await p.waitForTimeout(1000);
  await p.waitForFunction(() => /Value/i.test(document.querySelector("#dashPortfolio").innerText), null, { timeout: SLOW });
  await p.waitForTimeout(4000);
  const gain = await p.evaluate(async () => { const pf = await (await fetch("/api/portfolio")).json(); const k = [...document.querySelectorAll("#dashPortfolio .kpi")].find((x) => /Total gain/i.test(x.innerText)); return { pf: { total: pf.totalGain, parts: pf.pnl + pf.realised + pf.dividends }, text: k?.innerText.replace(/\s+/g, " ") }; });
  expect("dashboard: total gain includes sales and dividends", Math.abs(gain.pf.total - gain.pf.parts) < 1e-6 && /open · .* sold · .* dividends/.test(gain.text || ""), gain.text);
  // The snapshot's core/satellite bar matches the portfolio's own figures; the four figures sit on one row
  const snap = await p.evaluate(async () => {
    const pf = await (await fetch("/api/portfolio")).json(), bar = document.querySelector("#dashExtras .cs-bar");
    const tops = [...document.querySelectorAll("#dashPortfolio .kpi")].map((k) => Math.round(k.getBoundingClientRect().top));
    return { core: pf.coreSatellite.core, width: bar && parseFloat(bar.querySelector(".core").style.width), mark: bar && parseFloat(bar.querySelector("b").style.left), target: pf.coreSatellite.target, oneRow: new Set(tops).size === 1, text: document.querySelector("#dashExtras").innerText };
  });
  expect("dashboard: core/satellite bar in the snapshot, figures on one row", Math.abs(snap.width - snap.core * 100) < 1e-6 && Math.abs(snap.mark - snap.target * 100) < 1e-6 && snap.oneRow, snap.text.replace(/\s+/g, " "));
  await p.click("#refreshDigest");
  await p.waitForFunction(() => /Your portfolio (gained|lost)/.test(document.querySelector("#digestList").innerText), null, { timeout: SLOW });
  const dig = await p.evaluate(() => ({ text: document.querySelector("#digestList").innerText, links: [...document.querySelectorAll("#digestList a")].map((a) => a.getAttribute("href")), tip: document.querySelector("#digestInfo .info")?.dataset.tip || "" }));
  expect("dashboard: digest covers the portfolio, with its rules explained", /Your portfolio (gained|lost) .* against what you paid/.test(dig.text) && /yields 0\.15 or 0\.40 percentage points/.test(dig.tip) && dig.links.every((h) => /^https?:\/\//.test(h)), dig.text.split("\n")[0]);
  // Two tabs: Highlights has no headlines; News has only headlines, each a link; the counts add up; the choice is kept
  const tabs = async () => p.evaluate(() => ({ labels: [...document.querySelectorAll("#digestTabs button")].map((b) => b.textContent), on: document.querySelector("#digestTabs .on")?.dataset.tab,
    items: [...document.querySelectorAll("#digestList li")].map((li) => ({ news: li.classList.contains("k-news"), link: li.querySelector("a")?.getAttribute("href") || null })) }));
  const mineTab = await tabs();
  await p.click('#digestTabs [data-tab="news"]'); await p.waitForTimeout(200);
  const newsTab = await tabs();
  const total = await p.evaluate(async () => (await (await fetch("/api/digest")).json()).items.length);
  const counts = [...mineTab.labels, ...newsTab.labels].slice(0, 2).map((l) => Number(l.match(/\((\d+)\)/)?.[1]));
  expect("dashboard: digest split into Highlights and News", mineTab.on === "highlights" && mineTab.items.every((i) => !i.news) && newsTab.on === "news"
    && newsTab.items.every((i) => (i.news && /^https?:\/\//.test(i.link || "https://")) || i.link === null) && counts[0] + counts[1] === total, `${newsTab.labels.join(" / ")}, ${total} in all`);
  await p.reload(); await p.waitForFunction(() => document.querySelector("#digestTabs .on")?.dataset.tab === "news" && document.querySelector("#digestList li:not(.loading)"), null, { timeout: SLOW });
  expect("dashboard: the digest tab is remembered", true);
  await p.click('#digestTabs [data-tab="highlights"]'); await p.waitForTimeout(200);
  // The same height on either tab; a refresh never empties the list (watched every frame while it runs)
  const heights = [];
  for (const tab of ["news", "highlights"]) { await p.click(`#digestTabs [data-tab="${tab}"]`); await p.waitForTimeout(150); heights.push(await p.evaluate(() => document.querySelector("#digestList").getBoundingClientRect().height)); }
  await p.evaluate(() => { window.__emptied = false; const ul = document.querySelector("#digestList"); const look = () => { if (!ul.querySelector("li")) window.__emptied = true; if (window.__watch) requestAnimationFrame(look); }; window.__watch = true; look(); });
  await p.click("#refreshDigest");
  await p.waitForFunction(() => !document.querySelector("#refreshDigest").disabled, null, { timeout: SLOW });
  const emptied = await p.evaluate(() => { window.__watch = false; return window.__emptied; });
  const gutter = await p.evaluate(() => getComputedStyle(document.querySelector("#digestList")).scrollbarGutter);
  expect("dashboard: the digest keeps one height and doesn't flicker on refresh", heights[0] === heights[1] && heights[0] === 196 && !emptied && gutter === "stable", `${heights.join(" / ")}px, emptied: ${emptied}, gutter: ${gutter}`);
  await p.locator("#digestList li", { hasText: "Your portfolio" }).first().click();
  await p.waitForFunction(() => !document.querySelector("#view-portfolio").classList.contains("hidden"), null, { timeout: SLOW });
  expect("dashboard: a portfolio line in the digest opens the portfolio", true);
  await nav("dashboard"); await p.waitForTimeout(1500);
  expect("dashboard: portfolio snapshot and digest",!/undefined|NaN|\[object/.test(await txt("#view-dashboard")), (await txt("#dashPortfolio")).slice(0, 90));
  await shot("dashboard");
  const wlBtn = p.locator("#watchTable [data-rm]").first();
  if (await wlBtn.count()) { await p.click("#watchTable thead th:nth-child(2)"); await p.waitForTimeout(150); await p.locator("#watchTable [data-rm]").first().click(); await p.waitForTimeout(1500); expect("dashboard: remove from watchlist after sorting", (await p.locator("#watchTable [data-rm]").count()) === 0); }

  // ---- Settings
  await nav("settings");
  const startBase = await p.inputValue("#sBase");
  await p.selectOption("#sBase", startBase === "USD" ? "EUR" : "USD"); await p.locator("[data-w='value']").fill("60"); await p.click("#btnSaveSettings"); await p.waitForTimeout(800);
  expect("settings: saved and weights normalised", /Saved/.test(await txt("#settingsMsg")));
  const after = await p.evaluate(async () => (await fetch("/api/portfolio")).json());
  expect("settings: base currency applies to the portfolio", after.base !== startBase && after.total > 0, `${Math.round(pf.total)} ${pf.base} -> ${Math.round(after.total)} ${after.base}`);
  await research("AAPL");
  expect("settings: custom weights shown on Research", /V \d+ · Q \d+/.test(await txt("#rStance")), await txt("#rStance"));
  await nav("settings"); await p.click("#btnBalanced"); await p.selectOption("#sBase", startBase); await p.click("#btnSaveSettings"); await p.waitForTimeout(600);
  // Dashboard tiles: pick two, see two, then put the defaults back
  await nav("settings");
  const defaults = await p.evaluate(() => $$("[data-tile]").filter((i) => i.checked).map((i) => i.dataset.tile));
  await p.evaluate(() => $$("[data-tile]").forEach((i) => { i.checked = ["^GSPC", "KSA"].includes(i.dataset.tile); }));
  await p.click("#btnSaveSettings"); await p.waitForTimeout(600);
  await nav("dashboard"); await p.waitForFunction(() => document.querySelectorAll("#macroStrip .mcard").length === 2, null, { timeout: SLOW });
  const tiles = await p.evaluate(() => [...document.querySelectorAll("#macroStrip .mcard")].map((c) => c.dataset.t + " " + c.querySelector(".sm").textContent));
  expect("settings: dashboard shows only the chosen tiles", tiles.length === 2 && tiles.every((t) => !/—/.test(t)), tiles.join(" | "));
  await nav("settings");
  await p.evaluate((d) => $$("[data-tile]").forEach((i) => { i.checked = d.includes(i.dataset.tile); }), defaults);
  await p.click("#btnSaveSettings"); await p.waitForTimeout(600);
  await p.locator("#ctTicker").pressSequentially("^AEX"); await p.locator("#ctName").pressSequentially("Amsterdam");
  await p.press("#ctName", "Enter");
  await p.waitForFunction(() => /Added|Yahoo/.test(document.querySelector("#ctMsg").textContent) && !document.querySelector("#btnAddTile").disabled, null, { timeout: SLOW });
  const aex = await p.evaluate(() => ({ msg: document.querySelector("#ctMsg").textContent, ticked: document.querySelector('[data-tile="^AEX"]')?.checked, rm: !!document.querySelector('[data-rmtile="^AEX"]') }));
  expect("settings: custom tile added after the history check", /Added "Amsterdam"/.test(aex.msg) && aex.ticked && aex.rm, JSON.stringify(aex));
  await nav("dashboard"); await p.waitForFunction(() => document.querySelector('#macroStrip [data-stock="^AEX"]'), null, { timeout: SLOW });
  expect("dashboard: custom tile shown", /Amsterdam/.test(await txt('#macroStrip [data-stock="^AEX"]')), await txt('#macroStrip [data-stock="^AEX"]'));
  await nav("settings");
  if (MODE === "live") { // demo mode invents prices for any symbol, so only live data can refuse one
    refusalExpected = /\/api\/tiles$/;
    await p.locator("#ctTicker").pressSequentially("NOSUCHTICKER"); await p.click("#btnAddTile");
    await p.waitForFunction(() => !document.querySelector("#btnAddTile").disabled && !/Checking/.test(document.querySelector("#ctMsg").textContent), null, { timeout: SLOW });
    expect("settings: a symbol without history is refused", /no price history/.test(await txt("#ctMsg")), await txt("#ctMsg"));
    refusalExpected = null; await p.fill("#ctTicker", "");
  }
  await p.click('[data-rmtile="^AEX"]'); await p.waitForFunction(() => !document.querySelector('[data-tile="^AEX"]'), null, { timeout: SLOW });
  expect("settings: custom tile removed", !(await p.evaluate(() => state.meta.settings.dashboard_tiles.includes("^AEX") || state.meta.settings.custom_tiles.length)));
  expect("settings: tiles restored", (await p.evaluate(() => state.meta.settings.dashboard_tiles.length)) === defaults.length && defaults.length >= 10, defaults.length);
  // Light theme: chosen in Settings, followed by the open Research charts, kept after a reload
  const bg = () => p.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect("settings: navy until a theme is chosen", (await p.evaluate(() => document.documentElement.dataset.theme)) === "navy" && (await bg()) === "rgb(9, 20, 48)"
    && (await p.evaluate(() => document.querySelector('#themeSeg [data-theme="navy"]').classList.contains("on"))), await bg());
  await p.click('#themeSeg [data-theme="light"]'); await p.waitForTimeout(200);
  await nav("research"); await p.waitForTimeout(400);
  const light = await p.evaluate(() => ({ theme: document.documentElement.dataset.theme, grid: state.lw[0]?.options().grid.vertLines.color, width: state.lw[0]?.timeScale().width() }));
  expect("settings: light theme reaches the charts", light.theme === "light" && light.grid === "#edf0f4" && light.width > 300 && (await bg()) === "rgb(244, 246, 249)", JSON.stringify(light));
  await p.reload(); await p.waitForTimeout(500);
  expect("settings: theme remembered", await p.evaluate(() => document.documentElement.dataset.theme) === "light");
  await nav("settings"); await p.click('#themeSeg [data-theme="dark"]'); await p.waitForTimeout(200);
  expect("settings: back to dark", (await bg()) === "rgb(13, 17, 23)");
  await p.click('#themeSeg [data-theme="navy"]'); await p.waitForTimeout(200);
  await nav("research"); await p.waitForTimeout(400);
  const navyGrid = await p.evaluate(() => state.lw[0]?.options().grid.vertLines.color);
  await p.reload(); await p.waitForTimeout(500);
  expect("settings: navy chosen, reaches the charts and is remembered", (await bg()) === "rgb(9, 20, 48)" && navyGrid === "#172a52"
    && (await p.evaluate(() => document.documentElement.dataset.theme)) === "navy", navyGrid);
  await nav("settings");
  expect("favicon", (await p.request.get(new URL("favicon.ico", BASE).href)).ok());
  const [dl] = await Promise.all([p.waitForEvent("download"), p.click("#btnBackup")]);
  expect("settings: backup downloads", /stockpilot-backup/.test(dl.suggestedFilename()), dl.suggestedFilename());
  await p.click("#btnClearCache"); await p.waitForTimeout(600);
  await research("SAP.DE");
  expect("settings: app refetches after clearing the cache", (await txt("#rPrice")).length > 0, await txt("#rPrice"));
  const end = await p.evaluate(async () => (await fetch("/api/backup")).json());
  expect("settings: clearing the cache keeps your data", end.transactions.length >= 3, `${end.transactions.length} transactions`);

  expect("no uncaught JS errors in the whole walk", errs.length === 0, errs.slice(0, 4).join(" | "));
  expect("no failed requests in the whole walk", http.length === 0, http.slice(0, 6).join(" | "));
  console.log(`\n${MODE}: ${pass} passed, ${fail} failed`);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log(`DRIVER FAILED after ${pass} passed, ${fail} failed: ${e.message.slice(0, 500)}`); process.exit(2); });
