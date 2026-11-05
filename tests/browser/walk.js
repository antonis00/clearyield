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
  const errs = [], http = [], expected404 = /NOSUCHTICKER/;
  p.on("pageerror", (e) => errs.push(e.message));
  p.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errs.push("console: " + m.text()); });
  p.on("response", (r) => { const u = r.url(); if (u.startsWith(BASE) && r.status() >= 400 && !u.endsWith("favicon.ico") && !expected404.test(u)) http.push(`${r.status()} ${u.replace(BASE, "/")}`); });
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

  // ---- Research: every kind of asset
  for (const t of (MODE === "live" ? ["AAPL", "AZN.L", "SAP.DE", "QNBK.QA", "2222.SR", "EMAAR.AE", "NBK.KW", "SPY", "BTC-USD", "TSM", "^VIX", "^TASI.SR"] : ["AAPL", "AZN.L", "QNBK.QA", "SPY", "BTC-USD"])) {
    await research(t);
    const r = await p.evaluate(() => ({ price: document.querySelector("#rPrice").textContent, cov: document.querySelector("#rCoverage").textContent, stance: document.querySelector("#rStance").innerText.replace(/\s+/g, " "), charts: document.querySelectorAll("#priceChart canvas").length, stats: document.querySelectorAll("#statGrid > div").length, bad: /undefined|NaN|\[object/.test(document.querySelector("#researchBody").innerText) }));
    expect(`research ${t}`, r.price && r.charts > 0 && r.stats > 20 && !r.bad, `${r.price} | ${r.stance} | ${r.cov}${r.bad ? " | BAD TEXT" : ""}`);
  }
  if (MODE === "live") {  // demo mode invents data for any symbol, so there is no unknown ticker there
    await research("NOSUCHTICKER");
    expect("research: unknown ticker explains itself", /No price data/.test(await txt("#researchEmpty")));
  }

  await research("AAPL");
  for (const per of ["1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y"]) {
    const btn = p.locator(`#periodSeg button[data-p="${per}"]`);
    await btn.click(); await p.waitForFunction(() => document.querySelector("#researchBody").style.opacity === "1", null, { timeout: SLOW }); await p.waitForTimeout(250);
  }
  expect("research: all chart periods", (await p.locator("#priceChart canvas").count()) > 0);
  await p.locator("#periodSeg button[data-p='1y']").click(); await p.waitForFunction(() => document.querySelector("#researchBody").style.opacity === "1", null, { timeout: SLOW });
  for (const id of ["#tSma", "#tBb", "#tVol"]) { await p.click(id); await p.waitForTimeout(120); }
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
  await p.fill("#scrQuery", "cheap profitable dividend stocks in the gulf"); await p.click("#btnScrQuery");
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
  for (const k of ["assetClass", "region", "sector", "currency"]) expect(`portfolio: ${k} allocation adds up`, Math.abs(Object.values(pf.allocation[k]).reduce((a, b) => a + b, 0) - pf.total) < 1e-6);
  expect("portfolio: risk figures present", pf.risk.volatility != null && pf.risk.sharpe != null && pf.risk.beta != null && pf.performance.dates.length > 200, `vol ${pf.risk.volatility?.toFixed(3)} beta ${pf.risk.beta?.toFixed(2)}, ${pf.performance.dates.length} days`);
  expect("portfolio: undated lot warned about", pf.base === "USD" ? true : pf.warnings.some((w) => w.includes("SPY") && w.includes("exchange rate")), pf.warnings.find((w) => w.includes("exchange rate")) ?? "(USD base)");
  expect("portfolio: rebalancing shown", (await p.locator("#pfRebal tbody tr").count()) >= 3);
  for (const id of ["#pfPerf", "#aAsset", "#aRegion", "#aSector", "#aCur"]) expect(`portfolio: chart ${id}`, await canvasDrawn(id));
  expect("portfolio: no bad text", !/undefined|NaN|\[object/.test(await txt("#view-portfolio")));
  await shot("portfolio");
  await p.click("#pfTable thead th:nth-child(6)"); await p.waitForTimeout(200);
  await p.locator("#pfTable [data-lots]").first().click(); await p.waitForTimeout(300);
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
  expect("dashboard: portfolio snapshot and digest", !/undefined|NaN|\[object/.test(await txt("#view-dashboard")), (await txt("#dashPortfolio")).slice(0, 90));
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
  const [dl] = await Promise.all([p.waitForEvent("download"), p.click("#btnBackup")]);
  expect("settings: backup downloads", /stockpilot-backup/.test(dl.suggestedFilename()), dl.suggestedFilename());
  await p.click("#btnClearCache"); await p.waitForTimeout(600);
  await research("SAP.DE");
  expect("settings: app refetches after clearing the cache", (await txt("#rPrice")).length > 0, await txt("#rPrice"));
  const end = await p.evaluate(async () => (await fetch("/api/backup")).json());
  expect("settings: clearing the cache keeps your data", end.holdings.length >= 3, `${end.holdings.length} lots`);

  expect("no uncaught JS errors in the whole walk", errs.length === 0, errs.slice(0, 4).join(" | "));
  expect("no failed requests in the whole walk", http.length === 0, http.slice(0, 6).join(" | "));
  console.log(`\n${MODE}: ${pass} passed, ${fail} failed`);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log(`DRIVER FAILED after ${pass} passed, ${fail} failed: ${e.message.slice(0, 500)}`); process.exit(2); });
