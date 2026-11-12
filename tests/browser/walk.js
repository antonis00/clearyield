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
  // Saving settings waits for the save itself, not a fixed time: the server may still be loading what the last page asked for
  const saveSettings = async () => {
    await Promise.all([p.waitForResponse((r) => r.url().endsWith("/api/settings") && r.request().method() === "POST", { timeout: SLOW }), p.click("#btnSaveSettings")]);
    await p.waitForTimeout(200);
  };
  const txt = (s) => p.evaluate((x) => document.querySelector(x)?.innerText?.replace(/\s+/g, " ").trim() ?? null, s);
  const shot = (n) => p.screenshot({ path: `${OUT}/${MODE}_${n}.png`, fullPage: true });
  const canvasDrawn = (s) => p.evaluate((x) => { const c = document.querySelector(x); if (!c) return false; const d = c.getContext("2d")?.getImageData(0, 0, c.width, c.height).data; if (!d) return true; for (let i = 3; i < d.length; i += 4) if (d[i]) return true; return false; }, s);
  const research = async (t) => { await p.fill("#globalSearch", ""); await p.evaluate((x) => { location.hash = `#research/${encodeURIComponent(x)}`; }, t); await p.waitForFunction((x) => { const e = document.querySelector("#researchEmpty"); if (e && !e.classList.contains("hidden") && e.textContent.trim()) return true; return document.querySelector("#rSym")?.textContent === x && document.querySelector("#researchBody").style.opacity === "1" && !document.querySelector("#newsList .spinner"); }, t, { timeout: SLOW }); await p.waitForTimeout(600); };

  // Live: a non-USD base exercises the currency conversions, and two regions keep the run light on Yahoo: every run
  // starts with an empty cache, and downloading all 400 symbols each time gets the computer refused for a while.
  // Set before the first page, whose dashboard would otherwise start scoring every region. Demo covers them all.
  // BASE ends in a slash, so the address is built with URL: `${BASE}/api/...` made "//api/settings", which isn't the
  // route, and every live run quietly followed all five regions and downloaded every stock. It stops at once now.
  if (MODE === "live") {
    const set = await p.request.post(new URL("api/settings", BASE).href, { data: { base_currency: "EUR", regions: ["Europe", "Middle East & Africa"] } });
    const regions = set.ok() ? (await set.json()).regions : null;
    if (JSON.stringify(regions) !== JSON.stringify(["Europe", "Middle East & Africa"])) throw new Error(`live setup: the regions weren't set (${set.status()})`);
  }
  await p.goto(BASE, { waitUntil: "load" }); await p.waitForTimeout(600);
  await p.reload(); await p.waitForTimeout(600);
  const meta = await p.evaluate(async () => (await fetch("/api/meta")).json());
  expect(`mode is ${MODE}`, meta.demo === (MODE === "demo"), `base ${meta.settings.base_currency}, AI ${meta.ai}`);

  // ---- The frame: Ask ClearYieldAI with the app's mark, last and apart at the bottom, by the line; the quote as an icon; a full-width search
  const frame = await p.evaluate(() => {
    const links = [...document.querySelectorAll("nav a")], ask = document.querySelector('nav a[data-view="ask"]');
    const top = document.querySelector("header.top").getBoundingClientRect().width, box = document.querySelector(".searchbox").getBoundingClientRect().width;
    return { ask: ask.innerText.replace("◆", "").trim(), icon: !!ask.querySelector(".nav-mark") && /favicon\.svg$/.test(document.querySelector(".brand img.logo")?.src || ""), last: links[links.length - 1] === ask, gap: ask.getBoundingClientRect().top - links[links.length - 2].getBoundingClientRect().bottom,
             toLine: document.querySelector("#modeBadge").getBoundingClientRect().top - ask.getBoundingClientRect().bottom,
             quote: document.querySelector("#modeBadge .st.quote")?.dataset.tip || "", oldQuote: !!document.querySelector("header .disclaimer"), share: box / top };
  });
  expect("frame: Ask ClearYieldAI with its mark, at the bottom by the line, apart from the rest", frame.ask === "Ask ClearYieldAI" && frame.icon && frame.last && frame.gap > 40 && frame.toLine >= 0 && frame.toLine < 4, JSON.stringify(frame));
  expect("frame: the quote is an icon beside the others, shown on hover", /Plato/.test(frame.quote) && !frame.oldQuote, frame.quote);
  const seen = [];
  for (let i = 0; i < 4; i++) {
    await p.hover("#modeBadge .st.quote"); await p.waitForTimeout(150);
    seen.push(await p.evaluate(() => { const t = document.querySelector("#tooltip"); return t.classList.contains("hidden") ? "" : t.textContent; }));
    await p.mouse.move(700, 450); await p.waitForTimeout(100);
  }
  expect("frame: each hover shows another quote from the pool", seen.every((q) => q && /^“/.test(q)) && seen.every((q, i) => !i || q !== seen[i - 1]), seen.join(" | "));
  expect("frame: the search box takes the top bar's width", frame.share > 0.9, frame.share.toFixed(2));

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
    if (["AAPL", "SPY"].includes(t)) {  // a stock is ranked within its sector, after the page; a fund isn't
      if (t === "AAPL") await p.waitForFunction(() => /In its sector|too few to rank|being prepared/.test(document.querySelector("#rPeers").innerText), null, { timeout: SLOW });
      const peers = await p.evaluate(() => ({ line: document.querySelector("#rPeers").innerText, factors: [...document.querySelectorAll(".factor .peer")].map((x) => x.innerText).filter(Boolean) }));
      // live runs start with no sector table, so there it's being prepared in the background; demo builds it at once
      const preparing = MODE === "live" && /being prepared/.test(peers.line) && peers.factors.length === 0;
      expect(`research ${t}: ${t === "AAPL" ? "ranked within its sector" : "no sector rank for a fund"}`, t === "AAPL"
        ? (preparing || (/In its sector: the overall score is better than \d+% of \d+ other/.test(peers.line) && peers.factors.length >= 3 && peers.factors.every((x) => /better than \d+% of \d+|too few/.test(x))))
        : (peers.line === "" && peers.factors.length === 0), `${peers.line} | ${peers.factors[0] || ""}`);
      // Ownership: a stock's insiders and institutions, each trade named for what it was; a fund has no such card
      if (t === "AAPL") await p.waitForFunction(() => !/Loading/.test(document.querySelector("#rOwn").innerText), null, { timeout: SLOW });
      const own = await p.evaluate(() => ({ shown: !document.querySelector("#rOwnCard").classList.contains("hidden"), text: document.querySelector("#rOwn").innerText }));
      expect(`research ${t}: ${t === "AAPL" ? "ownership card" : "no ownership card for a fund"}`, t === "AAPL"
        ? (own.shown && /Held by insiders/i.test(own.text) && /Largest institutions/i.test(own.text) && /(Bought|Sold|Award, exercise or gift|No insider trades)/i.test(own.text) && /past 6 months|No insider/.test(own.text) && !/undefined|NaN/.test(own.text))
        : !own.shown, own.text.slice(0, 140));
    }
    if (["AAPL", "SPY", "BTC-USD"].includes(t)) {  // company statements and a DCF only for a company; the neighbour fills the row
      const lay = await p.evaluate(() => ({ cls: document.querySelector("#rTags").innerText, fin: !document.querySelector("#finCard").classList.contains("hidden"), dcf: !document.querySelector("#dcfCard").classList.contains("hidden"),
        statsWide: document.querySelector("#statGrid").closest(".card").getBoundingClientRect().width / document.querySelector("#statGrid").closest(".grid2").getBoundingClientRect().width }));
      const company = t === "AAPL";
      const failedWith = await p.evaluate(() => { const e = document.querySelector("#researchEmpty"); return e && !e.classList.contains("hidden") ? e.textContent : ""; });
      expect(`research ${t}: fundamentals and DCF ${company ? "shown" : "hidden"}`, lay.fin === company && lay.dcf === company && (company ? lay.statsWide < 0.6 : lay.statsWide > 0.99),
        `width ${lay.statsWide.toFixed(2)}${failedWith ? ` | the page failed: ${failedWith}` : ""}`);
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
  // One Screener word in capitals, Enter pressed at once (before the search caught up): still a screen, not a symbol
  await p.fill("#globalSearch", "CHEAP"); await p.press("#globalSearch", "Enter");
  await p.waitForFunction(() => !document.querySelector("#view-screener").classList.contains("hidden") && /matches out of/.test(document.querySelector("#scrInfo").innerText), null, { timeout: SLOW });
  expect("search: a Screener word in capitals runs in the Screener", (await p.inputValue("#scrQuery")) === "CHEAP" && /Max P\/E: 18/.test(await txt("#scrInfo")), (await txt("#scrInfo")).slice(0, 80));
  await p.fill("#globalSearch", "cheap"); await p.waitForFunction(() => /Ask the Screener/.test(document.querySelector("#searchResults").textContent), null, { timeout: 15000 });
  expect("search: the dropdown offers the screen first", /^Ask the Screener/.test(await txt("#searchResults div")), await txt("#searchResults div"));
  await p.fill("#globalSearch", ""); await p.reload(); await p.waitForTimeout(500);
  // The day's first downloads: the loader shows real progress, region by region, until the results replace it, and the
  // background scoring shows in the top bar. Mocked here: demo data is never slow enough to see it.
  let fakeDone = 150;
  const fakeJob = (kind) => ({ id: 1, kind, done: fakeDone, total: 400, elapsed: fakeDone / 5, started: 0, regions: { "North America": [Math.min(fakeDone, 150), 150], "Europe": [Math.max(0, fakeDone - 150), 250] } });
  const fakeProgress = (r) => { fakeDone = Math.min(400, fakeDone + 10); r.fulfill({ contentType: "application/json", body: JSON.stringify({ jobs: [fakeJob("screener"), fakeJob("ranks")], pause: 0 }) }); };
  const slowScreen = async (r) => { await new Promise((res) => setTimeout(res, 4000)); r.continue(); };
  await p.route("**/api/progress", fakeProgress); await p.route("**/api/screener", slowScreen);
  await nav("screener"); await p.click("#btnScr");
  await p.waitForSelector("#scrTable .dl-loader", { timeout: 5000 });
  await p.waitForTimeout(900);
  const ld = await p.evaluate(() => ({ count: $("#scrTable .dl-count").textContent, regions: $$("#scrTable .dl-reg").map((x) => `${x.className}: ${x.textContent}`),
    eta: $("#scrTable .dl-eta").textContent, bar: parseFloat($("#scrTable .dl-bar i").style.width) }));
  expect("loader: a long first download shows how far it has got, region by region", /^\d+ of 400 stocks$/.test(ld.count) && ld.regions.length === 2
    && /done: North America ✓/.test(ld.regions[0]) && /busy: Europe \d+\/250/.test(ld.regions[1]) && /s left|Almost/.test(ld.eta) && ld.bar > 37, JSON.stringify(ld));
  await p.waitForSelector("#bgProgress:not(.hidden)", { timeout: 12000 });
  expect("loader: the background scoring shows in the top bar", /Preparing today's data · \d+%/.test(await txt("#bgProgress")) && /Scoring every stock/.test(await p.getAttribute("#bgProgress", "data-tip")), await txt("#bgProgress"));
  await p.waitForFunction(() => /matches out of/.test($("#scrInfo").innerText) && !$("#scrTable .dl-loader"), null, { timeout: SLOW });
  expect("loader: the results replace it", true);
  await p.unroute("**/api/progress", fakeProgress); await p.unroute("**/api/screener", slowScreen);
  await p.waitForSelector("#bgProgress.hidden", { state: "attached", timeout: 12000 });
  expect("loader: the top bar's badge goes when the background scoring ends", true);
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
  // The overall score opens its make-up: the Factor scores card, scrolled to just below the search bar and lit up
  await p.evaluate(() => window.scrollTo(0, 0));
  await p.click("#rScore"); await p.waitForTimeout(900);
  const toCard = await p.evaluate(() => ({ top: Math.round(document.querySelector("#rFactorsCard").getBoundingClientRect().top), bar: Math.round(document.querySelector("header.top").getBoundingClientRect().bottom), flash: document.querySelector("#rFactorsCard").classList.contains("flash") }));
  expect("research: clicking the score goes to the Factor scores", toCard.flash && toCard.top >= toCard.bar && toCard.top - toCard.bar < 40, JSON.stringify(toCard));
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
  // It opens with today's most traded large companies, compared at once
  await p.waitForFunction(() => document.querySelectorAll("#cmpTable tbody tr").length > 0 && !document.querySelector("#btnCompare").disabled, null, { timeout: SLOW });
  const start = await p.evaluate(() => ({ list: document.querySelector("#cmpTickers").value, note: document.querySelector("#cmpStartNote").textContent }));
  expect("compare: opens with today's most traded, compared", start.list === "NVDA,MSFT,AMZN,AAPL,META" && /most traded large companies on Yahoo/.test(start.note)
    && (await p.locator("#cmpTable tbody tr").count()) === start.list.split(",").length, JSON.stringify(start));
  await p.fill("#cmpTickers", "AAPL,SAP.DE,QNBK.QA,BTC-USD"); await p.click("#btnCompare");
  await p.waitForFunction(() => !document.querySelector("#btnCompare").disabled, null, { timeout: SLOW }); await p.waitForTimeout(500);
  expect("compare: table and correlation", (await p.locator("#cmpTable tbody tr").count()) === 4 && (await p.locator("#cmpCorr table tr").count()) === 5);
  expect("compare: chart drawn", await canvasDrawn("#cmpChart"));
  const cmpLayout = await p.evaluate(() => {
    const t = document.querySelector("#cmpTable"), a = t.closest(".card").getBoundingClientRect(), b = document.querySelector("#cmpCorr").closest(".card").getBoundingClientRect();
    return { stacked: b.top >= a.bottom - 1, fullWidth: Math.abs(a.width - b.width) < 1, scroll: t.scrollWidth - t.clientWidth };
  });
  expect("compare: metrics and correlation stack, full width, no sideways scroll", cmpLayout.stacked && cmpLayout.fullWidth && cmpLayout.scroll <= 0, JSON.stringify(cmpLayout));
  const cmpText = await p.evaluate(() => ({ head: document.querySelector("#cmpTable thead").textContent, note: document.querySelector("#cmpNote").textContent,
    opt: document.querySelector("#cmpBaseOpt").textContent, row: document.querySelector("#cmpTable tbody").textContent }));
  expect("compare: in your currency, over the period, with fund cost and scores", /Fund cost/.test(cmpText.head) && /Score/.test(cmpText.head) && /Qual/.test(cmpText.head)
    && /Over 1 year\. In [A-Z]{3}, at each day's exchange rate/.test(cmpText.note) && /^In [A-Z]{3}$/.test(cmpText.opt) && !/undefined|NaN/.test(cmpText.row), JSON.stringify(cmpText).slice(0, 240));
  await p.selectOption("#cmpCcy", "own"); await p.click("#btnCompare");
  await p.waitForFunction(() => /own trading currency/.test(document.querySelector("#cmpNote").textContent), null, { timeout: SLOW });
  expect("compare: each in its own currency when asked", true);
  await p.selectOption("#cmpCcy", "base");
  await shot("compare");

  // ---- Screener
  await nav("screener");
  await p.check('#scrMarkets input[value="Crypto"]');
  await p.fill("#fPe", "25"); await p.fill("#fRoe", "10"); await p.click("#btnScr");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: 400000 }); await p.waitForTimeout(400);
  const scr = await p.evaluate(() => [...document.querySelectorAll("#scrTable tbody tr")].map((r) => r.innerText));
  const scanned = Number((/out of (\d+) scanned/.exec(await txt("#scrInfo")) || [])[1] || 0);
  expect("screener: filters narrow the list", scr.length > 0 && scanned > (MODE === "live" ? 100 : 300) && scr.length < scanned * 0.6, `${scr.length} of ${scanned} | ${await txt("#scrInfo")}`.slice(0, 170));
  await p.click("#scrTable thead th:nth-child(5)"); await p.waitForTimeout(200);
  await p.fill("#fPe", ""); await p.fill("#fRoe", ""); await p.click("#btnScr");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: 400000 }); await p.waitForTimeout(400);
  const allRows = await p.locator("#scrTable tbody tr").count();
  expect("screener: full universe, nothing failed", allRows >= 89 && !/No data:/.test(await txt("#scrInfo")), `${allRows} rows`);
  // A row far down the list opens Research at its top, not where the Screener was scrolled to
  const farRow = p.locator("#scrTable tbody tr").nth(60);
  await farRow.scrollIntoViewIfNeeded();
  const scrolledTo = await p.evaluate(() => scrollY), farTicker = (await farRow.locator("td").first().innerText()).trim().split(/\s/)[0];
  await farRow.click();
  await p.waitForFunction(() => !document.querySelector("#view-research").classList.contains("hidden"), null, { timeout: SLOW }); await p.waitForTimeout(300);
  const landed = await p.evaluate(() => scrollY);
  expect("screener: a row far down opens Research at the top", scrolledTo > 1000 && landed === 0, JSON.stringify({ scrolledTo, landed, farTicker }));
  await nav("screener");
  const width = await p.evaluate(() => { const t = document.querySelector("#scrTable"); return [t.clientWidth, t.scrollWidth]; });
  expect("screener: Score and Stance on screen at 1440px", width[1] <= width[0], width.join("/"));
  // A country narrows the screen to it; the Gulf is offered as a group
  const countries = await p.evaluate(() => [...document.querySelectorAll("#fCountry option")].map((o) => o.value));
  expect("screener: countries offered, with the Gulf as a group", countries.includes("Gulf") && countries.includes("Qatar") && countries.length > 10, countries.slice(0, 8).join(", "));
  await p.selectOption("#fCountry", "Qatar"); await p.click("#btnScr");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: SLOW }); await p.waitForTimeout(300);
  const qa = await p.evaluate(() => [...document.querySelectorAll("#scrTable tbody tr td:nth-child(2)")].map((td) => td.innerText.trim()));
  expect("screener: a country shows only its stocks", qa.length > 0 && qa.every((x) => x === "Qatar") && /Countries: Qatar/.test(await txt("#scrInfo")), `${qa.length} rows | ${qa.slice(0, 3).join(", ")}`);
  await p.selectOption("#fCountry", "");
  await shot("screener");
  await p.fill("#scrQuery", ""); await p.locator("#scrQuery").pressSequentially("cheap profitable dividend stocks in the gulf");
  expect("screener: you can type in the question box", (await p.inputValue("#scrQuery")) === "cheap profitable dividend stocks in the gulf", await p.inputValue("#scrQuery"));
  await p.press("#scrQuery", "Enter");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: SLOW }); await p.waitForTimeout(400);
  expect("screener: plain-language query", /Gulf/.test(await txt("#scrInfo")), await txt("#scrInfo"));
  // Limits beyond the main form (size, RSI, the 52-week range) are read from a sentence into "More filters", which
  // opens to show them, and they stay when the filters are applied again
  await p.fill("#scrQuery", "large caps down 10% from their high, rsi below 60"); await p.press("#scrQuery", "Enter");
  await p.waitForFunction(() => !document.querySelector("#btnScr").disabled && /Min market cap/.test(document.querySelector("#scrInfo").innerText), null, { timeout: SLOW });
  const more = await p.evaluate(() => ({ open: document.querySelector("#scrMore").open, count: document.querySelector("#scrMoreCount").textContent,
    mcap: document.querySelector('[data-more="min_mcap"]').value, off: document.querySelector('[data-more="min_off_high"]').value, rsi: document.querySelector('[data-more="max_rsi"]').value }));
  let applied = null;
  const watchScreen = (r) => { if (r.url().endsWith("/api/screener")) applied = r.postDataJSON(); };
  p.on("request", watchScreen);
  await p.click("#btnScr"); await p.waitForFunction(() => !document.querySelector("#btnScr").disabled, null, { timeout: SLOW }); await p.waitForTimeout(300);
  p.off("request", watchScreen);
  expect("screener: size, the 52-week range and RSI read into More filters, and kept when applied",
    more.open && more.count === "(3 set)" && more.mcap === "10" && more.off === "10" && more.rsi === "60" && applied?.filters?.min_mcap === 1e10 && applied?.filters?.min_off_high === 0.1 && applied?.filters?.max_rsi === 60,
    JSON.stringify({ more, applied: applied?.filters }));
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
  for (const k of ["assetClass", "region", "sector", "currency", "currencyLookThrough", "bucket"]) expect(`portfolio: ${k} allocation adds up`, Math.abs(Object.values(pf.allocation[k]).reduce((a, b) => a + b, 0) - pf.total) < 1e-6);
  expect("portfolio: SPY's currencies opened up, with the trading-currency view beside it", pf.allocation.currencyOpened.includes("SPY") && /SPY counted by the currencies/.test(await txt("#aCurNote")), await txt("#aCurNote"));
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
  // Fund costs: SPY's yearly cost on what's held, and your own figure from the factsheet standing over Yahoo's
  const costKpi = await p.evaluate(() => [...document.querySelectorAll("#pfKpis .kpi")].map((k) => k.innerText.replace(/\s+/g, " ")).find((t) => /^Fund costs/i.test(t)) || "");
  expect("portfolio: fund costs a year, as a share of the portfolio", pf.fundCosts.funds.some((f) => f.ticker === "SPY") && /of portfolio/.test(costKpi) && /Set costs/.test(costKpi), costKpi);
  await p.click("#pfCostEdit"); await p.locator('#modal input[name="SPY"]').fill("0.03"); await p.click("#modal form .btn:not(.ghost)");
  await p.waitForFunction(() => /Fund costs saved/.test(document.querySelector("#toast")?.innerText || ""), null, { timeout: SLOW }); await p.waitForTimeout(1500);
  const mine = (await p.evaluate(async () => (await fetch("/api/portfolio")).json())).fundCosts.funds.find((f) => f.ticker === "SPY");
  expect("portfolio: your fund cost stands over Yahoo's", mine.from === "you" && Math.abs(mine.ratio - 0.0003) < 1e-12, JSON.stringify(mine));
  await p.click("#pfCostEdit"); await p.locator('#modal input[name="SPY"]').fill(""); await p.click("#modal form .btn:not(.ghost)"); await p.waitForTimeout(1500);
  expect("portfolio: emptied, Yahoo's figure is back", (await p.evaluate(async () => (await fetch("/api/settings")).json())).fund_costs.SPY === undefined);
  await shot("portfolio");
  // A sale and a dividend through the form
  await p.selectOption("#hKind", "sell"); await p.fill("#hTicker", "QNBK.QA"); await p.fill("#hShares", "100"); await p.fill("#hCost", "18"); await p.fill("#hDate", "2025-06-02"); await p.click("#btnAddHolding"); await p.waitForTimeout(3000);
  await p.selectOption("#hKind", "dividend"); await p.fill("#hTicker", "QNBK.QA"); await p.fill("#hCost", "120"); await p.fill("#hDate", "2025-06-02"); await p.click("#btnAddHolding"); await p.waitForTimeout(3000);
  await p.selectOption("#hKind", "buy");
  const led = await p.evaluate(async () => (await fetch("/api/portfolio")).json());
  expect("portfolio: sale and dividend recorded", led.positions.find((x) => x.ticker === "QNBK.QA").shares === 400 && led.realised !== 0 && led.dividends > 0, `realised ${Math.round(led.realised)}, dividends ${Math.round(led.dividends)} ${led.base}`);
  expect("portfolio: no annual return while a lot has no date", led.xirr === null && /Needs a date on every purchase and sale/.test(await txt("#pfKpis")), await txt("#pfKpis"));
  expect("portfolio: transaction list", (await p.locator("#txTable tbody tr").count()) === 7);
  // A logo beside the ticker in every row of a list (letters in demo mode)
  const withLogos = (sel) => p.evaluate((q) => { const rows = [...document.querySelectorAll(q)].filter((r) => !r.querySelector(".empty")); return [rows.length, rows.filter((r) => r.querySelector(".logo[data-l]")).length]; }, sel);
  const txLogos = await withLogos("#txTable tbody tr");
  expect("portfolio: every transaction has a logo", txLogos[0] === 7 && txLogos[1] === 7, txLogos.join("/"));
  // Logos: each position has one, or in demo mode (no internet) its first letter on the tile
  const logoTiles = await p.evaluate(() => [...document.querySelectorAll("#pfTable tbody tr")].map((tr) => tr.querySelector(".logo")?.dataset.l || ""));
  expect("portfolio: every position has a logo tile", logoTiles.length >= 4 && logoTiles.every((l) => /^[A-Z0-9]$/.test(l)), logoTiles.join(" "));
  // Tax year: the 2025 sale and dividend, in the base currency, with the CSV for that year
  await p.waitForFunction(() => /2025/.test(document.querySelector("#taxYear")?.value || "") && document.querySelector("#taxSales tbody tr"), null, { timeout: SLOW });
  const taxCard = await p.evaluate(() => ({ text: document.querySelector("#taxCard").innerText.replace(/\s+/g, " "), csv: document.querySelector("#taxCsv").getAttribute("href"),
    sales: document.querySelectorAll("#taxSales tbody tr").length }));
  expect("portfolio: tax year shows the sale, the dividend and where it came from", taxCard.sales === 1 && /QNBK\.QA/.test(taxCard.text) && /Qatar/i.test(taxCard.text)
    && /Net realised/i.test(taxCard.text) && taxCard.csv === "/api/tax.csv?year=2025" && !/undefined|NaN/.test(taxCard.text), taxCard.text.slice(0, 200));
  const csv = await p.evaluate(async () => (await fetch("/api/tax.csv?year=2025")).text());
  expect("portfolio: the tax CSV lists the year's lines", /^Sales,2025/.test(csv) && /2025-06-02,QNBK\.QA/.test(csv), csv.split("\n").slice(0, 3).join(" | "));
  expect("portfolio: look-through adds up", Math.abs(Object.values(led.allocation.sectorLookThrough).reduce((a, b) => a + b, 0) - led.total) < 1e-6, Object.keys(led.allocation.sectorLookThrough).slice(0, 4).join(", "));
  await shot("ledger");
  // Orders: prepared from the targets with new money, one approved, marked done (into the ledger), the rest left
  await p.fill("#ordMoney", "5000"); await p.click("#ordForm .btn");
  await p.waitForFunction(() => document.querySelector("#ordPlan tbody tr") || /Nothing|hold nothing/.test(document.querySelector("#ordPlan").innerText), null, { timeout: SLOW });
  const draft = await p.evaluate(() => ({ rows: document.querySelectorAll("#ordPlan tbody tr").length, text: document.querySelector("#ordPlan").innerText.replace(/\s+/g, " ") }));
  expect("orders: prepared from the targets, each with its reason", draft.rows >= 1 && /target/.test(draft.text) && /After these/.test(draft.text) && !/undefined|NaN/.test(draft.text), draft.text.slice(0, 220));
  expect("orders: new money only, so no sales", !/\bSell\b/.test(await txt("#ordPlan tbody")));
  // not SPY: a later step deletes SPY's only lot
  const pick = await p.evaluate(() => [...document.querySelectorAll("#ordPlan tbody tr [data-stock]")].findIndex((td) => td.dataset.stock !== "SPY"));
  const firstOrder = await p.evaluate((i) => document.querySelectorAll("#ordPlan tbody tr [data-stock]")[i].dataset.stock, pick);
  await p.$$eval("#ordPlan tbody tr .ord-on", (bs, at) => bs.forEach((b, i) => { b.checked = i === at; }), pick);
  await p.locator("#ordPlan tbody tr").nth(pick).locator(".ord-shares").fill("1");
  await p.click("#ordApprove");
  await p.waitForSelector("#ordOpen .ord-done", { timeout: SLOW });
  const open = await p.evaluate(async () => (await fetch("/api/orders")).json());
  expect("orders: only the ticked one is approved, with the shares you changed", open.open.length === 1 && open.open[0].ticker === firstOrder && open.open[0].shares === 1, JSON.stringify(open.open.map((o) => [o.side, o.ticker, o.shares])));
  const ordCsv = await p.evaluate(async () => (await fetch("/api/orders.csv")).text());
  expect("orders: the CSV for the broker", /^Order,Action,Symbol/.test(ordCsv) && ordCsv.includes(`BUY,${firstOrder},1,LIMIT`), ordCsv.split("\n")[1]);
  const txBefore = (await p.evaluate(async () => (await fetch("/api/transactions")).json())).length;
  await p.click("#ordOpen .ord-done"); await p.locator('#modal input[name="fee"]').fill("1"); await p.click("#modal form .btn:not(.ghost)");
  await p.waitForFunction(() => /Recorded in your transactions/.test(document.querySelector("#toast")?.innerText || ""), null, { timeout: SLOW }); await p.waitForTimeout(1500);
  const txs = await p.evaluate(async () => (await fetch("/api/transactions")).json());
  expect("orders: marking done records the purchase, fee included", txs.length === txBefore + 1 && txs.some((t) => t.ticker === firstOrder && t.shares === 1 && /Order #\d+, fee 1/.test(t.note)), JSON.stringify(txs[0]));
  expect("orders: nothing open after it's done, and it's listed as done", !(await p.locator("#ordOpen .ord-done").count()) && /done, in the ledger/.test(await p.evaluate(() => document.querySelector("#ordOpen").textContent)));
  expect("orders: no bad text", !/undefined|NaN|\[object/.test(await txt("#ordCard")));
  await shot("orders");
  await p.click("#pfTable thead th:nth-child(6)"); await p.waitForTimeout(200);
  await p.locator('#pfTable tbody tr:has-text("SPY") [data-lots]').click(); await p.waitForTimeout(300);
  await p.locator("#modal [data-del]").first().click(); await p.waitForFunction(() => document.querySelectorAll("#pfTable tbody tr").length === 4, null, { timeout: SLOW });
  expect("portfolio: delete a lot after sorting", true);
  // With SPY gone nothing is held in ETFs: the draft asks what to buy there, and buys the ticker you type
  await p.fill("#ordMoney", "3000"); await p.click("#ordForm .btn");
  await p.waitForSelector('#ordPlan .ord-pick[data-class="ETFs"]', { timeout: SLOW });
  expect("orders: a class you hold nothing in asks what to buy", !(await p.locator('#ordPlan tbody tr [data-stock="SPY"]').count()) && /you hold nothing there/.test(await txt("#ordPlan")), (await txt("#ordPlan")).slice(0, 200));
  await p.locator('#ordPlan .ord-pick[data-class="ETFs"]').pressSequentially("spy"); await p.keyboard.press("Enter");
  await p.waitForSelector('#ordPlan tbody tr [data-stock="SPY"]', { timeout: SLOW });
  const picked = await p.evaluate(() => ({ text: document.querySelector("#ordPlan").innerText.replace(/\s+/g, " "), kept: document.querySelector('#ordPlan .ord-pick[data-class="ETFs"]').value }));
  expect("orders: the ticker you typed is bought, as your choice", /SPY is your choice/.test(picked.text) && picked.kept === "SPY" && !/undefined|NaN/.test(picked.text), picked.text.slice(0, 240));
  await shot("orders-pick");
  // Fractions are on by default; switching them off redrafts at once, in whole shares
  const spyShares = () => p.evaluate(() => Number(document.querySelector('#ordPlan tbody tr:has([data-stock="SPY"]) .ord-shares')?.value));
  const fracBefore = await spyShares();
  expect("orders: fractional shares on by default", (await p.isChecked("#ordFrac")) && !Number.isInteger(fracBefore), String(fracBefore));
  await p.click("#ordFrac");
  await p.waitForFunction(() => Number.isInteger(Number(document.querySelector('#ordPlan tbody tr:has([data-stock="SPY"]) .ord-shares')?.value)), null, { timeout: SLOW });
  expect("orders: switching fractions off redrafts at once, in whole shares", Math.floor(fracBefore) === (await spyShares()), `${fracBefore} -> ${await spyShares()}`);
  await shot("orders-switches");
  const afterLine = await p.evaluate(() => document.querySelector("#ordPlan .ord-after").textContent);
  expect("orders: classes with no target and nothing held aren't listed", !/0% → 0% \(target 0%\)/.test(afterLine), afterLine);
  await p.click("#ordPlan .ord-discard");
  expect("orders: Discard drops the suggestion, keeping the amount", !(await p.evaluate(() => document.querySelector("#ordPlan").innerHTML.trim())) && (await p.inputValue("#ordMoney")) === "3000"
    && !(await p.evaluate(async () => (await fetch("/api/orders")).json())).open.length);

  // Compare: "My holdings" fills in what's held, largest first
  await nav("compare"); await p.click("#cmpMine");
  await p.waitForFunction(() => !document.querySelector("#btnCompare").disabled && document.querySelectorAll("#cmpTable tbody tr").length > 0 && !/AAPL,SAP\.DE/.test(document.querySelector("#cmpTickers").value), null, { timeout: SLOW });
  const held = await p.evaluate(async () => (await (await fetch("/api/portfolio")).json()).positions.sort((a, b) => b.value - a.value).map((r) => r.ticker).slice(0, 8).join(","));
  expect("compare: My holdings fills in what you hold", (await p.inputValue("#cmpTickers")) === held && (await p.locator("#cmpTable tbody tr").count()) === held.split(",").length, held);

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

  // ---- Portfolio backtest and futures: the same page, its other tab
  await p.click("#btMode button[data-mode=portfolio]");
  await p.waitForSelector("#pmRows .pm-row", { timeout: SLOW });
  expect("portfolio sim: the tab shows a mix", !(await p.evaluate(() => document.querySelector("#btPortfolio").classList.contains("hidden"))) && (await p.locator("#pmRows .pm-row").count()) >= 1);
  // a mix of two that doesn't add up to 100% says so, and isn't run
  await p.evaluate(() => { document.querySelector("#pmRows").innerHTML = ""; });
  await p.click("#pmAdd"); await p.click("#pmAdd");
  const rowsIn = p.locator("#pmRows .pm-row");
  await rowsIn.nth(0).locator(".pm-t").fill("SPY"); await rowsIn.nth(0).locator(".pm-w").pressSequentially("60");
  await rowsIn.nth(1).locator(".pm-t").fill("AAPL"); await rowsIn.nth(1).locator(".pm-w").pressSequentially("30");
  expect("portfolio sim: weights not adding to 100% are flagged", /90\.00%.*must make 100%/.test(await txt("#pmTotal")), await txt("#pmTotal"));
  await rowsIn.nth(1).locator(".pm-w").fill("40"); await rowsIn.nth(1).locator(".pm-w").dispatchEvent("input");
  expect("portfolio sim: 100% accepted", /100\.00%/.test(await txt("#pmTotal")) && !/must/.test(await txt("#pmTotal")), await txt("#pmTotal"));
  await p.selectOption("#pmPeriod", "3y"); await p.fill("#pmMonthly", "100"); await p.click("#pmRunPast");
  await p.waitForFunction(() => !document.querySelector("#pmRunPast").disabled, null, { timeout: SLOW }); await p.waitForTimeout(300);
  const past = await txt("#pmPastOut");
  expect("portfolio sim: the past has results, a chart and each year", /Yearly return/.test(past) && /Worst fall/.test(past) && /Return on the money added/.test(past)
    && (await p.locator("#pmYears tr").count()) >= 3 && !/undefined|NaN/.test(past) && (await canvasDrawn("#pmPastChart")), past.slice(0, 120));
  await p.fill("#mcYears", "5"); await p.fill("#mcMonthly", "100"); await p.fill("#mcGoal", "20000"); await p.click("#mcRun");
  await p.waitForFunction(() => !document.querySelector("#mcRun").disabled, null, { timeout: SLOW }); await p.waitForTimeout(300);
  const fut = await txt("#mcOut");
  expect("portfolio sim: the future shows a range, the goal and its assumptions", /Middle outcome/i.test(fut) && /Bad 1 in 10/i.test(fut) && /Reaches the goal/i.test(fut)
    && /Assumes/.test(fut) && /12-month blocks/.test(fut) && !/undefined|NaN/.test(fut) && (await canvasDrawn("#mcChart")), fut.slice(0, 120));
  await shot("portfolio-sim");
  // Score check: each score's evidence (rank IC, t, verdict) and the changes tested, adopted or not, with why.
  // Demo only: live, a fresh server would download four years of statements and prices for every followed stock,
  // hundreds of requests a run, which is what gets the computer refused by Yahoo. tests/test_smoke.py checks the method.
  if (MODE === "demo") {
  await p.click("#btMode button[data-mode=score]");
  // The first run builds in the background (every stock in the list): the tab says how far it's got meanwhile
  await p.waitForFunction(() => /of \d+ stocks/.test(document.querySelector("#scTable").innerText) || document.querySelector("#scTable table"), null, { timeout: SLOW });
  expect("score check: building in the background says how far it's got", /of \d+ stocks|Rank IC/.test(await txt("#scTable")), await txt("#scTable"));
  await p.waitForFunction(() => document.querySelector("#scTable table") || /No price|stopped/.test(document.querySelector("#scTable").innerText), null, { timeout: SLOW * 4 });
  const sc = await p.evaluate(() => ({ rows: [...document.querySelectorAll("#scTable tr")].slice(1).map((r) => r.innerText.replace(/\s+/g, " ")),
    cands: [...document.querySelectorAll("#scCands tr")].slice(1).map((r) => r.innerText.replace(/\s+/g, " ")), regions: document.querySelectorAll("#scRegions tr").length,
    meta: document.querySelector("#scMeta").innerText, method: document.querySelector("#scMethod").innerText, caveats: document.querySelectorAll("#scCaveats li").length }));
  expect("score check: each score everywhere, on the deciding and the confirming regions", sc.rows.length === 6 && sc.rows.every((r) => (r.match(/works|suggestive|no evidence|works backwards|not enough data/g) || []).length >= 3)
    && /^Overall score/.test(sc.rows[5]) && /stocks · \d{4}-\d\d to \d{4}-\d\d/.test(sc.meta) && /rank IC/.test(sc.method) && sc.caveats === 8 && sc.regions === 7, JSON.stringify(sc).slice(0, 300));
  expect("score check: the three changes tested, each adopted or not with why", sc.cands.length === 3 && sc.cands.every((r) => /adopted/.test(r)) && !/undefined|NaN/.test(sc.cands.join(" ")), sc.cands.slice(0, 2).join(" | "));
  await p.click('.sc-detail:last-of-type summary'); await p.click('#scKind [data-sc="risk"]'); await p.waitForTimeout(300);
  expect("score check: month by month, a score's IC per date", (await canvasDrawn("#scChart")) && await p.evaluate(() => /Risk \(calm\): rank IC/.test(state.charts.scChart.data.datasets[0].label)));
  await shot("score-check");
  }
  await p.click("#btMode button[data-mode=single]");
  expect("portfolio sim: back to a single asset", !(await p.evaluate(() => document.querySelector("#btSingle").classList.contains("hidden"))));

  // ---- Journal & alerts
  await nav("journal");
  await p.fill("#jTicker", "MSFT"); await p.selectOption("#jAction", "buy"); await p.fill("#jPrice", "400"); await p.fill("#jThesis", "walk test"); await p.fill("#jInval", "growth slows"); await p.fill("#jStop", "300"); await p.fill("#jTarget", "900"); await p.fill("#jReview", "2020-01-01");
  await p.click("#btnJournalAdd"); await p.waitForFunction(() => /walk test/.test(document.querySelector("#journalList").innerText), null, { timeout: SLOW }); await p.waitForTimeout(400);
  const jLogos = await withLogos("#journalList .jentry");
  expect("journal: every entry has a logo", jLogos[0] > 0 && jLogos[0] === jLogos[1], jLogos.join("/"));
  expect("journal: entry shows return since entry", /since entry/.test(await txt("#journalList")), (await txt("#journalList")).slice(0, 110));
  expect("journal: scorecard waits for a day", /check back/.test(await txt("#jScore")));
  dialogAnswer = "learned something"; await p.click("[data-out]"); await p.waitForFunction(() => /learned something/.test(document.querySelector("#journalList").innerText), null, { timeout: 20000 });
  expect("journal: record an outcome", true);
  await p.fill("#aTicker", "SPY"); await p.selectOption("#aKind", "price_above"); await p.fill("#aValue", "0.01"); await p.click("#btnAlertAdd"); await p.waitForTimeout(800);
  await p.click("#btnCheckAlerts"); await p.waitForTimeout(3000);
  const aLogos = await withLogos("#alertTable tbody tr");
  expect("alerts: every alert has a logo", aLogos[0] > 0 && aLogos[0] === aLogos[1], aLogos.join("/"));
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

  // ---- Opportunities: four charts from one scoring, each bar opening its stock; the dashboard card leads here
  if (MODE === "live") await p.evaluate(() => localStorage.setItem("oppMarkets", JSON.stringify(["Europe", "Middle East & Africa"])));
  await nav("opportunities");
  await p.waitForFunction(() => /stocks scored/.test(document.querySelector("#oppStatus").innerText), null, { timeout: SLOW });
  await p.waitForTimeout(500);
  const drawn = [];
  for (const id of ["#oppMap", "#oppTop", "#oppFit", "#oppSale"]) drawn.push((await p.evaluate((x) => document.querySelector(x).classList.contains("hidden"), id)) || (await canvasDrawn(id)));
  const opp = await p.evaluate(() => ({ status: document.querySelector("#oppStatus").innerText, fit: document.querySelector("#oppFitHelp").innerText, top: state.charts.oppTop?.data.labels }));
  expect("opportunities: four charts drawn from today's scores", drawn.every(Boolean) && /stocks scored/.test(opp.status) && opp.top?.length === 10 && /what you hold now/.test(opp.fit), `${opp.status} | top: ${opp.top?.slice(0, 3).join(", ")}`);
  // The map zooms with Ctrl + wheel around the pointer (a plain wheel scrolls the page), names every stock in view,
  // moves with a drag (which doesn't open a stock), and resets
  await p.locator("#oppMap").scrollIntoViewIfNeeded();
  const mapRange = () => p.evaluate(() => { const s = state.charts.oppMap.scales; return [s.x.min, s.x.max, s.y.min, s.y.max].map((v) => Math.round(v * 10) / 10); });
  const mapAt = (v) => p.evaluate((v) => { const c = state.charts.oppMap, r = c.canvas.getBoundingClientRect(); return { x: r.left + c.scales.x.getPixelForValue(v), y: r.top + c.scales.y.getPixelForValue(v) }; }, v);
  let pt = await mapAt(70);
  await p.mouse.move(pt.x, pt.y);
  const scrolledFrom = await p.evaluate(() => scrollY);
  await p.mouse.wheel(0, 120); await p.waitForTimeout(200);
  const plainWheel = { range: await mapRange(), scrolled: (await p.evaluate(() => scrollY)) !== scrolledFrom };
  await p.evaluate(() => window.scrollTo(0, scrollY - 120)); await p.waitForTimeout(150);
  pt = await mapAt(70); await p.mouse.move(pt.x, pt.y);
  await p.keyboard.down("Control"); for (let i = 0; i < 4; i++) { await p.mouse.wheel(0, -100); await p.waitForTimeout(60); } await p.keyboard.up("Control");
  const zoomedTo = await mapRange(), stillAt = await mapAt(70);
  expect("opportunities: Ctrl + wheel zooms the map around the pointer; a plain wheel scrolls the page",
    plainWheel.range.join() === "-6,106,-6,106" && plainWheel.scrolled && zoomedTo[1] - zoomedTo[0] < 50 && zoomedTo[0] < 70 && zoomedTo[1] > 70
    && Math.abs(stillAt.x - pt.x) < 8 && Math.abs(stillAt.y - pt.y) < 8 && await p.isVisible("#oppMapReset"), JSON.stringify({ plainWheel, zoomedTo, moved: [Math.round(stillAt.x - pt.x), Math.round(stillAt.y - pt.y)] }));  // the axis labels change width, so the plot shifts a few pixels
  const ticks = await p.evaluate(() => state.charts.oppMap.scales.x.ticks.map((t) => t.value));
  expect("opportunities: zoomed in, the marks are finer", ticks.length >= 3 && ticks[1] - ticks[0] <= 10, ticks.join(","));
  const mapBox = await p.locator("#oppMap").boundingBox();
  await p.mouse.move(mapBox.x + mapBox.width / 2, mapBox.y + mapBox.height / 2); await p.mouse.down();
  await p.mouse.move(mapBox.x + mapBox.width / 2 + 120, mapBox.y + mapBox.height / 2 + 60, { steps: 6 }); await p.mouse.up(); await p.waitForTimeout(300);
  const dragged = await mapRange();
  expect("opportunities: a drag moves the zoomed map and opens nothing", dragged[0] < zoomedTo[0] && dragged[2] > zoomedTo[2] && (await p.evaluate(() => location.hash)) === "#opportunities", JSON.stringify(dragged));
  await p.click("#oppMapReset"); await p.waitForTimeout(200);
  expect("opportunities: Reset zoom shows the whole map again", (await mapRange()).join() === "-6,106,-6,106" && !(await p.isVisible("#oppMapReset")), JSON.stringify(await mapRange()));
  const bar = await p.evaluate(() => { const c = state.charts.oppTop, m = c.getDatasetMeta(0).data[0], r = c.canvas.getBoundingClientRect(); return { x: r.left + m.x - 10, y: r.top + m.y, t: c.data.labels[0] }; });
  await p.mouse.click(bar.x, bar.y);
  await p.waitForFunction((t) => document.querySelector("#rSym")?.textContent === t, bar.t, { timeout: SLOW });
  expect("opportunities: clicking a bar opens that stock", true, bar.t);

  // ---- Ask: the AI icon in the sidebar opens it; with AI off (as in every test run), the page says how to set it up
  await p.click("#modeBadge .st.ai"); await p.waitForTimeout(300);
  expect("ask: the sidebar's AI icon opens Ask ClearYieldAI", (await p.evaluate(() => location.hash)) === "#ask" && !(await p.evaluate(() => document.querySelector("#view-ask").classList.contains("hidden"))));
  // The Yahoo mark opens Yahoo Finance in a new tab: the front page here, the stock's page on Research (not loaded in the test)
  // Records what a click asks the browser to open, without opening Yahoo
  const yahooTab = async () => {
    await p.evaluate(() => { window.__open = window.open; window.__opened = null; window.open = (u, t, f) => { window.__opened = [u, t, f]; return null; }; });
    await p.click("#modeBadge .st.yahoo");
    return p.evaluate(() => { window.open = window.__open; return (window.__opened || []).join(" "); });
  };
  const front = await yahooTab();
  await research("AAPL");
  const quote = await yahooTab();
  expect("sidebar: the Yahoo mark opens Yahoo Finance in a new tab, the stock's page on Research",
    front === "https://finance.yahoo.com/ _blank noopener,noreferrer" && quote === "https://finance.yahoo.com/quote/AAPL/ _blank noopener,noreferrer", `${front} | ${quote}`);
  await nav("ask"); await p.waitForTimeout(300);
  const askInfo = await p.evaluate(() => ({ examples: document.querySelectorAll("#askExamples, .ask-ex").length, intro: document.querySelector("#askIntro p").innerText }));
  // Before the first question, the explanation and the box sit in the middle, the box ready to type in;
  // Shift+Enter starts a new line and the box grows with it
  const empty = await p.evaluate(() => { const b = document.querySelector(".ask-composer").getBoundingClientRect();
    return { mid: b.top > innerHeight * 0.25 && b.bottom < innerHeight * 0.85, focused: document.activeElement?.id === "askInput", h: document.querySelector("#askInput").offsetHeight }; });
  await p.locator("#askInput").pressSequentially("one"); await p.keyboard.press("Shift+Enter"); await p.locator("#askInput").pressSequentially("two");
  const grown = await p.evaluate(() => ({ v: document.querySelector("#askInput").value, h: document.querySelector("#askInput").offsetHeight }));
  expect("ask: before the first question the box sits in the middle, focused; Shift+Enter adds a line and the box grows",
    empty.mid && empty.focused && grown.v === "one\ntwo" && grown.h > empty.h, JSON.stringify({ empty, grown }));
  await p.fill("#askInput", "How is my portfolio doing?"); await p.press("#askInput", "Enter"); await p.waitForTimeout(300);
  expect("ask: a short explanation, no example questions, and it says when AI is off", askInfo.examples === 0 && askInfo.intro.length < 250 && /AI_PROVIDER|isn't set up/.test(await txt("#askStatus")) && !(await p.locator("#askAnswers .ask-answer").count()), await txt("#askStatus"));
  // Figures in an answer are marked: unsupported ones highlighted wherever they appear (however the thousands are
  // separated), worked-out ones underlined with how, and never inside a longer number
  const marked_ = await p.evaluate(() => {
    const box = document.createElement("div");
    box.innerHTML = markFigures("<p>Core is 94.4%, you hold €35 816.51, it could rise 40% (not 1,400 or 40.5).</p><p><code>40</code></p>",
      { unmatched: ["40", "€35,816.51"], derived: [{ figure: "94.4%", how: "100% − 5.6%" }] });
    return { bad: [...box.querySelectorAll("mark.fig-bad")].map((m) => m.textContent), worked: [...box.querySelectorAll("mark.fig-worked")].map((m) => `${m.textContent}|${m.title}`) };
  });
  // A quotation the sources don't have word for word is marked too
  const quoted_ = await p.evaluate(() => { const box = document.createElement("div");
    box.innerHTML = markFigures("<p>Zacks wrote “Novo shares jumped on strong sales” today.</p>", { unmatched: [], derived: [], quotes: ["Novo shares jumped on strong sales"] });
    return [...box.querySelectorAll("mark.fig-bad")].map((m) => `${m.textContent}|${m.title}`); });
  expect("ask: misquotes are marked", quoted_.join() === "Novo shares jumped on strong sales|Not in the sources word for word", JSON.stringify(quoted_));
  // A question runs in the background: the page shows each step and what it's doing as it happens, then the answer
  // with its tickers linked to Research; a follow-up carries the earlier answer, "New conversation" starts afresh.
  // The AI and the job are stood in for (demo mode has no AI).
  {
    const sent = [];
    let polls = 0;
    const step = { tool: "get_stock", args: { ticker: "NVDA" }, ok: true };
    const answer = { id: "a1", answer: "NVDA fell 6.6% on the latest day.", steps: [step], calls: 2, waited: 3, by: "m via Groq", check: { checked: 1, unmatched: [], derived: [], quotes: [] }, revised: null, followed: 0 };
    const startAsk = (r) => { sent.push(r.request().postDataJSON()); r.fulfill({ contentType: "application/json", body: JSON.stringify({ id: `j${sent.length}` }) }); };
    const jobAsk = (r) => {
      polls += 1;
      const events = [{ step, t: 0.4 }, { phase: "thinking", round: 1, t: 0.5 }, ...(polls > 1 ? [{ phase: "waiting", seconds: 3, t: 1 }] : [])];
      const done = polls > 3;
      r.fulfill({ contentType: "application/json", body: JSON.stringify({ status: done ? "done" : "running", events, elapsed: polls, ...(done ? { result: { ...answer, id: `a${sent.length}`, followed: sent.length - 1 } } : {}) }) });
    };
    await p.route("**/api/ask/start", startAsk); await p.route("**/api/ask/job/*", jobAsk);
    await p.evaluate(() => { state.meta.aiInfo = { ...(state.meta.aiInfo || {}), on: true }; });
    await p.fill("#askInput", "Why did Nvidia move today?"); await p.press("#askInput", "Enter");
    await p.waitForFunction(() => /Still working…/.test(document.querySelector(".ask-live .ask-now-text")?.textContent || ""), null, { timeout: 10000 });
    const live = await p.evaluate(() => ({ steps: [...document.querySelectorAll(".ask-live-steps li")].map((li) => li.textContent), now: document.querySelector(".ask-now-text").textContent }));
    expect("ask: while it works, the page shows each step, and a pause plainly, without plan or limit details", live.steps.join() === "Looked up NVDA" && /^Still working…$/.test(live.now.trim()) && !/allowance|plan|limit/i.test(live.now), JSON.stringify(live));
    await p.waitForSelector(".ask-thread .ask-answer", { timeout: 10000 });
    const done1 = await p.evaluate(() => ({ live: !!document.querySelector(".ask-live"), link: document.querySelector(".ask-answer .ask-stock")?.dataset.stock, how: document.querySelector(".ask-answer .ask-steps summary").textContent,
      newBtn: !document.querySelector("#askNew").classList.contains("hidden") }));
    expect("ask: the answer replaces the progress, its tickers link to Research, and says how long it took", !done1.live && done1.link === "NVDA" && /1 look-up, 2 model calls, \d+ s$/.test(done1.how.trim()) && !/waiting|allowance/.test(done1.how) && done1.newBtn, JSON.stringify(done1));
    polls = 0;
    await p.fill("#askInput", "And its dividend?"); await p.press("#askInput", "Enter");
    await p.waitForFunction(() => document.querySelectorAll(".ask-thread.current .ask-answer").length === 2, null, { timeout: 10000 });
    const follow = await p.evaluate(() => ({ pill: document.querySelectorAll(".ask-thread.current .ask-answer")[1].querySelector(".ask-q .pill")?.textContent, threads: document.querySelectorAll(".ask-thread").length }));
    expect("ask: a follow-up carries the earlier answer and joins its conversation", JSON.stringify(sent[1].follow) === '["a1"]' && follow.pill === "Follow-up" && follow.threads === 1, JSON.stringify({ follow, sent }));
    // Like a chat: oldest first, the explanation gone, and the box held at the bottom of the window while scrolling up
    await p.evaluate(() => { document.querySelector(".ask-answer .brief").insertAdjacentHTML("beforeend", "<p>filler</p>".repeat(80)); scrollTo(0, 0); });
    await p.waitForTimeout(200);
    const chat = await p.evaluate(() => { const b = document.querySelector(".ask-composer").getBoundingClientRect(), qs = [...document.querySelectorAll(".ask-answer .ask-q")].map((q) => q.textContent);
      return { order: qs, intro: getComputedStyle(document.querySelector("#askIntro")).display, bottom: Math.round(innerHeight - b.bottom), visible: b.top < innerHeight && b.top > innerHeight - 200 }; });
    expect("ask: the conversation reads oldest first, with the question box kept at the bottom of the window",
      /Nvidia/.test(chat.order[0]) && /dividend/.test(chat.order[1]) && chat.intro === "none" && chat.visible && chat.bottom >= 0 && chat.bottom < 4, JSON.stringify(chat));
    await p.click("#askNew"); polls = 0;
    const cleared = await p.evaluate(() => ({ answers: document.querySelectorAll("#askAnswers .ask-answer").length, empty: document.querySelector("#view-ask").classList.contains("ask-empty") }));
    await p.fill("#askInput", "How are markets?"); await p.press("#askInput", "Enter");
    await p.waitForFunction(() => document.querySelector(".ask-thread.current .ask-answer"), null, { timeout: 10000 });
    const fresh = await p.evaluate(() => document.querySelectorAll(".ask-thread").length);
    expect("ask: New conversation clears the page and starts afresh", JSON.stringify(sent[2].follow) === "[]" && !cleared.answers && cleared.empty && fresh === 1, JSON.stringify({ sent: sent[2], cleared, fresh }));
    // A ticker link opens the stock
    await p.click(".ask-thread.current .ask-answer .ask-stock");
    await p.waitForFunction(() => location.hash === "#research/NVDA", null, { timeout: SLOW });
    expect("ask: a ticker in an answer opens it in Research", true);
    // Hand-offs. From the Screener: a sentence with parts no filter expresses goes to Ask whole; "Ask about these"
    // leaves a question about the matches in Ask's box to edit, without sending it
    await nav("screener");
    await p.fill("#scrQuery", "wide moat stocks"); await p.press("#scrQuery", "Enter");
    await p.waitForSelector("#scrAskAbout", { timeout: SLOW });
    const handoff = await p.evaluate(() => ({ instead: !!document.querySelector("#scrToAsk"), about: !!document.querySelector("#scrAskAbout") }));
    const before = sent.length;
    await p.click("#scrAskAbout"); await p.waitForTimeout(300);
    const about = await p.evaluate(() => ({ view: location.hash, q: document.querySelector("#askInput").value, focused: document.activeElement?.id }));
    expect("screener: Ask about these opens Ask with a question about the matches, ready to edit, not sent",
      handoff.instead && handoff.about && about.view === "#ask" && /^Of the Screener's \d+ matches for "wide moat stocks" \(best first: [A-Z]/.test(about.q) && about.focused === "askInput" && sent.length === before,
      JSON.stringify({ handoff, about }));
    await nav("screener"); polls = 0;
    await p.click("#scrToAsk");
    await p.waitForFunction(() => document.querySelector(".ask-thread .ask-answer"), null, { timeout: 10000 });
    expect("screener: Ask ClearYieldAI instead sends the sentence to Ask", sent.length === before + 1 && sent[before].question === "wide moat stocks" && JSON.stringify(sent[before].follow) === "[]", JSON.stringify(sent[before]));
    // From Ask: an answer that ran a screen opens it in the Screener, where every match shows
    answer.steps = [step, { tool: "screen", args: { question: "cheap european banks" }, ok: true }];
    polls = 0;
    await p.fill("#askInput", "Which European banks are cheap?"); await p.press("#askInput", "Enter");
    await p.waitForSelector(".ask-actions button", { timeout: 10000 });
    await p.click(".ask-actions button");
    await p.waitForFunction(() => location.hash === "#screener" && /matches out of/.test(document.querySelector("#scrInfo").innerText) && !document.querySelector("#btnScr").disabled, null, { timeout: SLOW });
    expect("ask: an answer that ran a screen opens it in the Screener", (await p.inputValue("#scrQuery")) === "cheap european banks" && /Banks only/.test(await txt("#scrInfo")), (await txt("#scrInfo")).slice(0, 120));
    answer.steps = [step];
    await p.unroute("**/api/ask/start", startAsk); await p.unroute("**/api/ask/job/*", jobAsk);
    await nav("ask");
    // A question that stops stays on the page with the reason in plain sight and Try again; an error with no text
    // (a dropped connection) still says what happened. It used to vanish back to the empty page.
    await p.evaluate(() => newAskConversation());
    const failStart = (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "jf" }) });
    let failWith = "";
    const failJob = (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "error", error: failWith, code: 503, events: [], elapsed: 1 }) });
    await p.route("**/api/ask/start", failStart); await p.route("**/api/ask/job/*", failJob);
    await p.fill("#askInput", "Which EU energy stocks use only fossil fuels?"); await p.press("#askInput", "Enter");
    await p.waitForSelector(".ask-failed .ask-error", { timeout: 10000 });
    const failed = await p.evaluate(() => ({ q: document.querySelector(".ask-failed .ask-q").textContent, why: document.querySelector(".ask-failed .ask-error").textContent,
      retry: !!document.querySelector(".ask-failed .ask-retry"), empty: document.querySelector("#view-ask").classList.contains("ask-empty") }));
    expect("ask: a question that stops stays, with a plain reason and Try again", /EU energy/.test(failed.q) && /didn't answer|Try again/.test(failed.why) && failed.retry && !failed.empty, JSON.stringify(failed));
    failWith = "The AI is busy right now. Try again in about 20 seconds.";
    await p.click(".ask-failed .ask-retry");
    await p.waitForFunction(() => /busy right now/.test(document.querySelector(".ask-failed .ask-error")?.textContent || ""), null, { timeout: 10000 });
    expect("ask: Try again asks once more, and shows the new reason", (await p.locator(".ask-failed").count()) === 1);
    await p.unroute("**/api/ask/start", failStart); await p.unroute("**/api/ask/job/*", failJob);
    await p.evaluate(() => newAskConversation());
  }
  // A web search step lists its pages as links that open in a new tab; a link that isn't http(s) is dropped
  const links_ = await p.evaluate(() => { const box = document.createElement("div");
    box.innerHTML = webLinks({ tool: "web_search", ok: true, links: [{ title: "Novo cuts its outlook", url: "https://www.reuters.com/a", site: "reuters.com", date: "2026-11-05" }, { title: "x", url: "javascript:alert(1)" }] }) +
      webLinks({ tool: "web_search", ok: true, links: [], source: "Groq's browser search" });
    return { hrefs: [...box.querySelectorAll("a")].map((a) => `${a.href}|${a.target}`), text: box.innerText }; });
  expect("ask: web search steps list their pages", links_.hrefs.join() === "https://www.reuters.com/a|_blank" && /2026-11-05/.test(links_.text) && /no list of pages/.test(links_.text), JSON.stringify(links_));
  expect("ask: answer figures are marked as unsupported or worked out", JSON.stringify(marked_.bad) === JSON.stringify(["€35 816.51", "40"]) && marked_.worked.join() === "94.4%|Worked out: 100% − 5.6%", JSON.stringify(marked_));

  // ---- Dashboard again, now with data
  await nav("dashboard"); await p.waitForTimeout(1000);
  await p.waitForFunction(() => document.querySelector("#dashOppList .opp-row, #dashOppList div.muted:not(:has(.spinner)):last-child"), null, { timeout: SLOW });
  expect("dashboard: opportunity map card drawn", await canvasDrawn("#dashOppChart"), await txt("#dashOppList"));
  const corner = await p.evaluate(() => { const c = state.charts.dashOppChart, z = state.opp.data.rules.zone, pts = c.data.datasets[0].data;
    return { z, x: c.scales.x.min, y: c.scales.y.min, n: pts.length, all: pts.every((d) => d.x >= z && d.y >= z), inZone: state.opp.data.map.filter((r) => r.f_value >= z && r.f_momentum >= z).length }; });
  expect("dashboard: the small map shows only the shaded corner", corner.all && corner.n === corner.inZone && corner.x === corner.z - 3 && corner.y === corner.z - 3, JSON.stringify(corner));
  // Hovering a dot names the company; the corner's stocks are named on the map itself
  // the dot furthest from any other, since with hundreds of stocks a dot can sit under another one
  const dot = await p.evaluate(() => {
    const c = state.charts.dashOppChart, pts = c.getDatasetMeta(0).data, r = c.canvas.getBoundingClientRect();
    let best = 0, far = -1;
    pts.forEach((m, i) => { const d = Math.min(...pts.map((o, j) => (i === j ? Infinity : Math.hypot(o.x - m.x, o.y - m.y)))); if (d > far) { far = d; best = i; } });
    return { x: r.left + pts[best].x, y: r.top + pts[best].y, t: c.data.datasets[0].data[best].row.ticker };
  });
  await p.mouse.move(dot.x, dot.y); await p.waitForTimeout(300);
  const tip = await p.evaluate(() => { const t = state.charts.dashOppChart.tooltip; return { shown: t.opacity > 0, lines: (t.body || []).flatMap((b) => b.lines) }; });
  expect("dashboard: hovering a dot on the map names the company", tip.shown && tip.lines.some((l) => l.includes(dot.t)), tip.lines.join(" | "));
  await p.click("#dashOppChart"); await p.waitForTimeout(400);
  expect("dashboard: the opportunity card opens Opportunities", (await p.evaluate(() => location.hash)) === "#opportunities");
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
  await p.selectOption("#sBase", startBase === "USD" ? "EUR" : "USD"); await p.locator("[data-w='value']").fill("60"); await saveSettings();
  expect("settings: saved and weights normalised", /Saved/.test(await txt("#settingsMsg")));
  // Regions you follow: unfollowing one takes it out of the Screener's markets and countries; following it again brings it back
  const followedNow = MODE === "live" ? 2 : 5;  // live follows two (set at the start)
  expect("settings: five regions, the followed ones ticked", (await p.locator("#sRegions [data-region]").count()) === 5 && (await p.locator("#sRegions [data-region]:checked").count()) === followedNow);
  await p.uncheck('#sRegions [data-region="Europe"]'); await saveSettings();
  // the page then fetches the markets to offer for the new choice
  await p.waitForFunction(() => ![...document.querySelectorAll("#scrMarkets input")].some((i) => i.value === "Europe"), null, { timeout: SLOW }).catch(() => {});
  const offered = await p.evaluate(() => ({ markets: [...document.querySelectorAll("#scrMarkets input")].map((i) => i.value), germany: !!document.querySelector('#fCountry option[value="Germany"]') }));
  expect("settings: an unfollowed region isn't offered", !offered.markets.includes("Europe") && offered.markets.includes("Middle East & Africa") && !offered.germany, offered.markets.join(", "));
  await p.check('#sRegions [data-region="Europe"]'); await saveSettings();
  await p.waitForFunction(() => [...document.querySelectorAll("#scrMarkets input")].some((i) => i.value === "Europe"), null, { timeout: SLOW }).catch(() => {});
  expect("settings: following it again brings it back", await p.evaluate(() => [...document.querySelectorAll("#scrMarkets input")].some((i) => i.value === "Europe")));
  const after = await p.evaluate(async () => (await fetch("/api/portfolio")).json());
  expect("settings: base currency applies to the portfolio", after.base !== startBase && after.total > 0, `${Math.round(pf.total)} ${pf.base} -> ${Math.round(after.total)} ${after.base}`);
  await research("AAPL");
  expect("settings: custom weights shown on Research", /V \d+ · Q \d+/.test(await txt("#rStance")), await txt("#rStance"));
  await nav("settings"); await p.click("#btnBalanced"); await p.selectOption("#sBase", startBase); await saveSettings();
  // Dashboard tiles: pick two, see two, then put the defaults back
  await nav("settings");
  const defaults = await p.evaluate(() => $$("[data-tile]").filter((i) => i.checked).map((i) => i.dataset.tile));
  await p.evaluate(() => $$("[data-tile]").forEach((i) => { i.checked = ["^GSPC", "KSA"].includes(i.dataset.tile); }));
  await saveSettings();
  await nav("dashboard"); await p.waitForFunction(() => document.querySelectorAll("#macroStrip .mcard").length === 2, null, { timeout: SLOW });
  const tiles = await p.evaluate(() => [...document.querySelectorAll("#macroStrip .mcard")].map((c) => c.dataset.t + " " + c.querySelector(".sm").textContent));
  expect("settings: dashboard shows only the chosen tiles", tiles.length === 2 && tiles.every((t) => !/—/.test(t)), tiles.join(" | "));
  await nav("settings");
  await p.evaluate((d) => $$("[data-tile]").forEach((i) => { i.checked = d.includes(i.dataset.tile); }), defaults);
  await saveSettings();
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
  // Start with Windows applies at once (the check servers point it at a temporary folder, never the real one)
  expect("settings: start with Windows is offered, off", await p.isVisible("#startupOpt") && !(await p.isChecked("#sStartup")));
  await Promise.all([p.waitForResponse((r) => r.url().endsWith("/api/startup"), { timeout: SLOW }), p.click("#startupOpt .switch")]); await p.waitForTimeout(200);
  const stOn = await p.evaluate(async () => (await (await fetch("/api/startup")).json()).enabled);
  expect("settings: start with Windows turns on at once", stOn && /sign in to Windows/.test(await txt("#toast")) && /No unsaved|Saved/.test(await txt("#settingsMsg")), await txt("#toast"));
  await Promise.all([p.waitForResponse((r) => r.url().endsWith("/api/startup"), { timeout: SLOW }), p.click("#startupOpt .switch")]); await p.waitForTimeout(200);
  expect("settings: and off again", !(await p.evaluate(async () => (await (await fetch("/api/startup")).json()).enabled)) && !(await p.isChecked("#sStartup")));
  expect("settings: automatic weekly backups, with the latest", /Backed up automatically every week to \S+\\backups, the last 8 kept · latest \d{4}-\d\d-\d\d/.test(await txt("#autoBackupNote")), await txt("#autoBackupNote"));
  // The save card says when something is unsaved, and Discard puts the saved values back
  const saveMsg = () => txt("#settingsMsg");
  const asiaWas = await p.isChecked('#sRegions [data-region="Asia-Pacific"]');
  expect("settings: nothing unsaved on arrival", /No unsaved changes|Saved/.test(await saveMsg()) && await p.isHidden("#btnDiscard"), await saveMsg());
  await p.click('#sRegions label:has([data-region="Asia-Pacific"])');
  expect("settings: a change shows as unsaved", (await saveMsg()) === "Unsaved changes" && await p.isVisible("#btnDiscard"), await saveMsg());
  await p.click("#btnDiscard");
  expect("settings: discard puts it back", (await p.isChecked('#sRegions [data-region="Asia-Pacific"]')) === asiaWas && /No unsaved changes/.test(await saveMsg()) && await p.isHidden("#btnDiscard"), await saveMsg());
  // Email alerts: the check servers have no mail server, so the card says what to add; on needs an address; the switch saves
  expect("settings: email alerts start off, with no message", !(await txt("#emailStatus")) && !(await p.isChecked("#sEmailOn")) && (await p.isDisabled("#sEmail")), await txt("#emailStatus"));
  await p.check("#sEmailOn"); await p.fill("#sEmail", ""); await p.click("#btnSaveSettings"); await p.waitForTimeout(300);
  expect("settings: email alerts need an address", /email address/.test(await txt("#toast")) && !(await p.evaluate(() => state.meta.settings.email_alerts)), await txt("#toast"));
  await p.locator("#sEmail").pressSequentially("me@example.com"); await saveSettings();
  expect("settings: email alerts turned on and kept", (await p.evaluate(() => [state.meta.settings.email_alerts, state.meta.settings.alert_email].join()))
    === "true,me@example.com" && (await p.isChecked("#sEmailOn")) && (await p.inputValue("#sEmail")) === "me@example.com");
  refusalExpected = /\/api\/email\/test$/;
  await Promise.all([p.waitForResponse((r) => r.url().endsWith("/api/email/test"), { timeout: SLOW }), p.click("#btnTestEmail")]); await p.waitForTimeout(200);
  refusalExpected = null;
  expect("settings: a test email without a mail server says why", /isn't set up/.test(await txt("#emailStatus")), await txt("#emailStatus"));
  await p.uncheck("#sEmailOn"); await saveSettings();
  expect("settings: email alerts turned off, address kept", (await p.evaluate(() => state.meta.settings.email_alerts)) === false && (await p.inputValue("#sEmail")) === "me@example.com");
  expect("favicon", (await p.request.get(new URL("favicon.ico", BASE).href)).ok());
  // The base currency's icon opens the converter: it converts, and swapping turns the rate round
  await p.click("#modeBadge .st.cur");
  await p.waitForFunction(() => /=/.test(document.querySelector("#cvOut")?.innerText || ""), null, { timeout: SLOW });
  await p.fill("#cvAmount", "250"); await p.click("#cvGo"); await p.waitForTimeout(600);
  const conv1 = await p.evaluate(() => ({ text: document.querySelector("#cvOut").innerText, from: document.querySelector("#cvFrom").value, to: document.querySelector("#cvTo").value }));
  await p.click("#cvSwap"); await p.waitForTimeout(600);
  const conv2 = await p.evaluate(() => ({ from: document.querySelector("#cvFrom").value, to: document.querySelector("#cvTo").value, text: document.querySelector("#cvOut").innerText }));
  expect("converter: opens from the currency icon, converts and swaps", /250/.test(conv1.text) && conv1.from !== conv1.to && conv2.from === conv1.to && conv2.to === conv1.from && /250/.test(conv2.text), conv1.text.split("\n")[0]);
  // Every currency in use, and each box searches as you type: "yen" finds the Japanese yen
  expect("converter: every currency is offered", (await p.locator("#cvList option").count()) >= 150, await p.locator("#cvList option").count());
  await p.click("#cvTo"); await p.locator("#cvTo").pressSequentially("yen"); await p.press("#cvTo", "Enter");
  await p.waitForFunction(() => /JPY/.test(document.querySelector("#cvTo").value) && /=/.test(document.querySelector("#cvOut").innerText), null, { timeout: SLOW }).catch(() => {});
  expect("converter: typing a name finds the currency", (await p.inputValue("#cvTo")) === "JPY — Japanese yen" && /=/.test(await txt("#cvOut")), await p.inputValue("#cvTo"));
  await p.click("#cvFrom"); await p.locator("#cvFrom").pressSequentially("zzzz"); await p.press("#cvFrom", "Enter"); await p.waitForTimeout(200);
  expect("converter: an unknown currency asks for one from the list", /Choose the currency to convert from: type part of its name/.test(await txt("#cvOut")), await txt("#cvOut"));
  await p.click("#modal [data-close]");
  const [dl] = await Promise.all([p.waitForEvent("download"), p.click("#btnBackup")]);
  expect("settings: backup downloads", /clearyield-backup/.test(dl.suggestedFilename()), dl.suggestedFilename());
  await p.click("#btnClearCache"); await p.waitForTimeout(600);
  await research("SAP.DE");
  expect("settings: app refetches after clearing the cache", (await txt("#rPrice")).length > 0, await txt("#rPrice"));
  const end = await p.evaluate(async () => (await fetch("/api/backup")).json());
  expect("settings: clearing the cache keeps your data", end.transactions.length >= 3, `${end.transactions.length} transactions`);

  // Chrome flags a form field with neither id nor name ("A form field element should have an id or name attribute")
  const unnamed = await p.evaluate(() => [...document.querySelectorAll("input, select, textarea")].filter((e) => !e.id && !e.name).map((e) => e.outerHTML.slice(0, 80)));
  expect("every form field has an id or a name", unnamed.length === 0, unnamed.slice(0, 3).join(" | "));
  expect("no uncaught JS errors in the whole walk", errs.length === 0, errs.slice(0, 4).join(" | "));
  expect("no failed requests in the whole walk", http.length === 0, http.slice(0, 6).join(" | "));
  console.log(`\n${MODE}: ${pass} passed, ${fail} failed`);
  await browser.close();
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log(`DRIVER FAILED after ${pass} passed, ${fail} failed: ${e.message.slice(0, 500)}`); process.exit(2); });
