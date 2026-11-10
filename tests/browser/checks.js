// Focused browser checks on a demo server. Run through tests/run_all.py.
// Usage: node checks.js <base> <check> <outDir>
const { chromium } = require("playwright-core");
const [BASE, CHECK, OUT] = process.argv.slice(2);
const results = [];
const expect = (name, ok, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"} ${name}${extra ? " :: " + extra : ""}`); };
const post = (page, path, body, method = "POST") => page.evaluate(async ([p, b, m]) => {
  const r = await fetch(p, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined });
  return { status: r.status, body: await r.json().catch(() => null) };
}, [path, body, method]);

const CHECKS = {
  // A holding with no data shows a lasting banner on Portfolio and Dashboard, and rebalancing is withheld
  async incomplete(page) {
    await page.route("**/api/portfolio", async (route) => {
      const resp = await route.fetch(); const j = await resp.json();
      j.incomplete = true; j.missing = ["AAPL"]; j.rebalance = []; j.errors = ["AAPL: No price data"];
      await route.fulfill({ response: resp, json: j });
    });
    await post(page, "/api/holdings", { ticker: "SPY", shares: 5, cost: 400 });
    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForTimeout(2500);
    const pf = await page.evaluate(() => ({ hidden: document.querySelector("#pfIncomplete").classList.contains("hidden"), text: document.querySelector("#pfIncomplete").textContent, reb: document.querySelector("#pfRebal").textContent }));
    expect("portfolio banner visible", !pf.hidden && pf.text.includes("AAPL"), pf.text.slice(0, 90));
    expect("rebalancing withheld", pf.reb.includes("Withheld"), pf.reb.slice(0, 60));
    await page.waitForTimeout(4000);
    expect("banner outlasts the toast", await page.evaluate(() => !document.querySelector("#pfIncomplete").classList.contains("hidden")));
    await page.screenshot({ path: `${OUT}/incomplete_portfolio.png` });
    await page.evaluate(() => { location.hash = "#dashboard"; }); await page.waitForTimeout(2500);
    const d = await page.evaluate(() => ({ hidden: document.querySelector("#dashIncomplete").classList.contains("hidden"), text: document.querySelector("#dashIncomplete").textContent }));
    expect("dashboard banner visible", !d.hidden && d.text.includes("AAPL"));
    await page.unroute("**/api/portfolio");
    await page.reload(); await page.waitForTimeout(2500);
    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForTimeout(2000);
    expect("no banner with complete data", await page.evaluate(() => document.querySelector("#pfIncomplete").classList.contains("hidden")));
  },

  // Row buttons still work after sorting
  async sortbuttons(page) {
    for (const t of ["AAPL", "MSFT", "SPY"]) await post(page, "/api/watchlist", { ticker: t });
    await post(page, "/api/holdings", { ticker: "AAPL", shares: 2, cost: 100 });
    await post(page, "/api/holdings", { ticker: "SPY", shares: 2, cost: 100 });
    for (const [t, v] of [["AAPL", 1], ["MSFT", 2]]) await post(page, "/api/alerts", { ticker: t, kind: "price_above", value: 1e9 + v });

    await page.evaluate(() => { location.hash = "#dashboard"; location.reload(); }); await page.waitForSelector("#watchTable tbody tr, #watchlistTable tbody tr", { timeout: 60000 }).catch(() => {});
    const wt = await page.evaluate(() => { const b = document.querySelector("[data-rm]"); return b ? "#" + b.closest(".tablewrap, [id]").id : null; });
    await page.click(`${wt} thead th:nth-child(2)`); await page.waitForTimeout(300);
    const before = (await post(page, "/api/watchlist", null, "GET")).body.length;
    const rm = await page.getAttribute("[data-rm]", "data-rm");
    await page.click(`[data-rm="${rm}"]`); await page.waitForTimeout(1500);
    expect("watchlist Remove after sort", (await post(page, "/api/watchlist", null, "GET")).body.length === before - 1, `${before} -> ${before - 1}`);

    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForSelector("#pfTable tbody tr");
    await page.click("#pfTable thead th:nth-child(3)"); await page.waitForTimeout(300);
    await page.click("#pfTable [data-lots]"); await page.waitForTimeout(400);
    expect("portfolio Lots after sort", await page.evaluate(() => !document.querySelector("#modal").classList.contains("hidden")));
    await page.evaluate(() => document.querySelector("#modal").classList.add("hidden"));

    await page.evaluate(() => { location.hash = "#journal"; }); await page.waitForSelector("#alertTable tbody tr");
    await page.click("#alertTable thead th:nth-child(3)"); await page.waitForTimeout(300);
    await page.click("#alertTable [data-adel]"); await page.waitForTimeout(1000);
    expect("alert Delete after sort", (await post(page, "/api/alerts", null, "GET")).body.length === 1);
    await page.click("#alertTable thead th:nth-child(1)"); await page.waitForTimeout(200);
    await page.click("#alertTable thead th:nth-child(1)"); await page.waitForTimeout(200);
    await page.click("#alertTable [data-adel]"); await page.waitForTimeout(1000);
    expect("alert Delete after sorting twice", (await post(page, "/api/alerts", null, "GET")).body.length === 0);
  },

  // Download a backup, wipe the data, restore through the real file picker, and confirm the data is back
  async backup(page) {
    await post(page, "/api/holdings", { ticker: "SPY", shares: 5, cost: 400, bought: "2025-01-02", note: "core" });
    await post(page, "/api/journal", { ticker: "AAPL", action: "buy", price: 150, thesis: "services growth" });
    await page.evaluate(() => { location.hash = "#settings"; }); await page.waitForTimeout(400);
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#btnBackup")]);
    const path = `${OUT}/backup_test.json`; await dl.saveAs(path);
    expect("backup file name", /^stockpilot-backup-\d{4}-\d{2}-\d{2}\.json$/.test(dl.suggestedFilename()), dl.suggestedFilename());
    const hid = (await post(page, "/api/backup", null, "GET")).body.transactions[0].id;
    await post(page, `/api/holdings/${hid}`, null, "DELETE");
    const dialogs = []; page.on("dialog", (d) => { dialogs.push(d.message()); d.accept(); });
    await page.setInputFiles("#restoreFile", path); await page.waitForTimeout(2500);
    expect("asks first and says what is in the file", dialogs[0]?.includes("1 transactions") && dialogs[0]?.includes("1 journal"), dialogs[0]);
    const after = (await post(page, "/api/backup", null, "GET")).body;
    expect("holding is back", after.transactions.length === 1 && after.transactions[0].note === "core");
    expect("journal is intact", after.journal[0].thesis === "services growth");
    await page.setInputFiles("#restoreFile", { name: "x.json", mimeType: "application/json", buffer: Buffer.from("not json") }); await page.waitForTimeout(500);
    expect("rejects a broken file", (await page.textContent("#toast")).includes("isn't a StockPilot backup"));
    expect("data untouched by broken file", (await post(page, "/api/backup", null, "GET")).body.transactions.length === 1);
  },

  // A fired alert shows in the digest with a working Dismiss button
  async alertdigest(page) {
    await post(page, "/api/alerts", { ticker: "AAPL", kind: "price_above", value: 0.01 });
    await post(page, "/api/alerts/check", {});
    await page.evaluate(() => { location.hash = "#dashboard"; location.reload(); });
    await page.waitForSelector("[data-dismiss]", { timeout: 60000 });
    expect("fired alert is in the digest", /Alert: AAPL/.test(await page.textContent("#digestList")));
    await page.click("[data-dismiss]"); await page.waitForTimeout(600);
    expect("dismissing keeps you on the dashboard", (await page.evaluate(() => location.hash)) !== "#research/AAPL");
    expect("dismiss removes it", !/Alert: AAPL/.test(await page.textContent("#digestList")));
    await page.click("#refreshDigest"); await page.waitForTimeout(1500);
    expect("still gone after a refresh", !/Alert: AAPL/.test(await page.textContent("#digestList")));
  },

  // A slow reply for the previous stock must not overwrite the current one
  async race(page) {
    await page.route("**/api/stock/AAPL?*", async (route) => { await new Promise((r) => setTimeout(r, 2500)); await route.continue(); });
    await page.route("**/api/stock/AAPL/news", async (route) => { await new Promise((r) => setTimeout(r, 2500)); await route.fulfill({ json: [{ title: "OLD STOCK HEADLINE", sentiment: 0 }] }); });
    await page.evaluate(() => { location.hash = "#research/AAPL"; }); await page.waitForTimeout(200);
    await page.evaluate(() => { location.hash = "#research/NVDA"; });
    await page.waitForTimeout(5000);
    const r = await page.evaluate(() => ({ sym: document.querySelector("#rSym").textContent, state: state.ticker, stock: state.stock?.ticker, news: document.querySelector("#newsList").innerText }));
    expect("header shows the stock you opened last", r.sym === "NVDA", r.sym);
    expect("page data belongs to that stock", r.stock === "NVDA" && r.state === "NVDA", `${r.state}/${r.stock}`);
    const live = await page.evaluate(async () => (await (await fetch("/api/stock/NVDA")).json()).stats.price.toFixed(4));
    await page.click("#btnHold"); await page.waitForTimeout(250);
    const cost = await page.inputValue("#modalCard input[name='cost']");
    expect("+ Holding pre-fills that stock's price", cost === live, `${cost} vs ${live}`);
    await page.evaluate(() => document.querySelector("#modal").classList.add("hidden"));
    expect("old headlines don't overwrite the news", !/OLD STOCK/.test(r.news));
  },

  // Fired alerts show in the tab title and, once allowed, as browser notifications
  async notify(page) {
    await page.addInitScript(() => {
      window.__shown = [];
      window.Notification = function (title, o) { window.__shown.push(o.body); };
      window.Notification.permission = "granted";
      window.Notification.requestPermission = async () => "granted";
    });
    await page.reload(); await page.waitForTimeout(800);
    const before = await post(page, "/api/alerts", { ticker: "AAPL", kind: "price_above", value: 0.01 });
    await page.evaluate(() => checkFiredAlerts()); await page.waitForTimeout(200);
    expect("nothing fired yet", (await page.title()) === "StockPilot", await page.title());
    await post(page, "/api/alerts/check", {});
    await page.evaluate(() => checkFiredAlerts()); await page.waitForTimeout(200);
    expect("count in the tab title", (await page.title()) === "(1) StockPilot", await page.title());
    expect("browser notification shown once", (await page.evaluate(() => window.__shown)).length === 1, JSON.stringify(await page.evaluate(() => window.__shown)));
    await page.evaluate(() => checkFiredAlerts()); await page.waitForTimeout(200);
    expect("not repeated on the next check", (await page.evaluate(() => window.__shown)).length === 1);
    await post(page, `/api/alerts/${before.body.id}/dismiss`, {});
    await page.evaluate(() => checkFiredAlerts()); await page.waitForTimeout(200);
    expect("title clears when dismissed", (await page.title()) === "StockPilot", await page.title());
    await page.evaluate(() => { location.hash = "#journal"; }); await page.waitForTimeout(300);
    expect("the page says it needs the app open", /while this page is open/.test(await page.textContent("#view-journal")));
  },

  // Small page fixes
  async pagefixes(page) {
    // Alert values start from something that suits the condition
    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.click("#btnAlert"); await page.waitForTimeout(200);
    const price = await page.inputValue("#modalCard input[name='value']");
    await page.selectOption("#modalCard select[name='kind']", "rsi_above");
    expect("RSI alert defaults to 70", (await page.inputValue("#modalCard input[name='value']")) === "70", `price default was ${price}`);
    await page.selectOption("#modalCard select[name='kind']", "day_move");
    expect("daily move alert defaults to 5", (await page.inputValue("#modalCard input[name='value']")) === "5");
    await page.selectOption("#modalCard select[name='kind']", "price_below");
    expect("price alert defaults to the price", (await page.inputValue("#modalCard input[name='value']")) === price);
    await page.evaluate(() => document.querySelector("#modal").classList.add("hidden"));

    // Failed actions say so
    await page.route("**/api/journal", (route) => route.request().method() === "POST" ? route.fulfill({ status: 500, json: { detail: "Could not save" } }) : route.continue());
    await page.evaluate(() => { location.hash = "#journal"; }); await page.waitForTimeout(400);
    await page.fill("#jTicker", "AAPL"); await page.click("#btnJournalAdd"); await page.waitForTimeout(500);
    expect("a failed save shows a message", (await page.textContent("#toast")).includes("Could not save"), await page.textContent("#toast"));
    await page.unroute("**/api/journal");

    // Edit outcome starts from what's there
    await post(page, "/api/journal", { ticker: "AAPL", action: "buy", price: 100 });
    const jid = (await post(page, "/api/journal", null, "GET")).body[0].id;
    await post(page, `/api/journal/${jid}`, { outcome: "first note" }, "PATCH");
    await page.reload(); await page.waitForSelector("[data-out]");
    let shown = null, kind = null;
    const once = (d) => { shown = d.defaultValue(); kind = d.type(); d.dismiss(); };
    page.once("dialog", once);
    await page.click("[data-out]"); await page.waitForTimeout(300);
    expect("Edit outcome shows the existing text", kind === "prompt" && shown === "first note", shown);

    // Deleting a lot asks first
    await post(page, "/api/holdings", { ticker: "SPY", shares: 1, cost: 100 });
    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForSelector("#pfTable [data-lots]", { timeout: 60000 });
    await page.click("#pfTable [data-lots]"); await page.waitForTimeout(250);
    let asked = false;
    page.once("dialog", (d) => { asked = true; d.dismiss(); });
    await page.click("#modal [data-del]"); await page.waitForTimeout(500);
    expect("lot delete asks first", asked);
    expect("saying no keeps the lot", (await post(page, "/api/portfolio", null, "GET")).body.positions.length === 1);
    await page.evaluate(() => document.querySelector("#modal").classList.add("hidden"));

    // Removing the last holding clears the chart
    page.once("dialog", (d) => d.accept());
    await page.click("#pfTable [data-lots]"); await page.waitForTimeout(250);
    await page.click("#modal [data-del]"); await page.waitForTimeout(1500);
    expect("chart clears with the last holding", await page.evaluate(() => !state.charts.pfPerf || state.charts.pfPerf.data.labels.length === 0));

    // The sidebar follows the base currency
    await page.evaluate(() => { location.hash = "#settings"; }); await page.waitForTimeout(300);
    await page.selectOption("#sBase", "EUR");
    // wait for the save itself: the Research page opened above may still be ranking AAPL's sector in the background
    await Promise.all([page.waitForResponse((r) => r.url().endsWith("/api/settings") && r.request().method() === "POST", { timeout: 60000 }), page.click("#btnSaveSettings")]);
    await page.waitForTimeout(300);
    const cur = await page.evaluate(() => { const c = document.querySelector("#modeBadge [data-currency]"); return `${c.dataset.currency} ${c.textContent}`; });
    expect("sidebar shows the new base", cur === "EUR €", cur);
    // A blank rate doesn't quietly become 0%, and all-zero weights are refused
    const rate = await page.inputValue("#sRf");
    await page.fill("#sRf", ""); await page.click("#btnSaveSettings"); await page.waitForTimeout(400);
    expect("blank risk-free rate refused", (await post(page, "/api/settings", null, "GET")).body.risk_free_rate === Number(rate) / 100, await page.textContent("#toast"));
    await page.fill("#sRf", rate);
    await page.$$eval("[data-w]", (rs) => rs.forEach((r) => { r.value = 0; }));
    await page.click("#btnSaveSettings"); await page.waitForTimeout(400);
    expect("all-zero weights refused", /weight/i.test(await page.textContent("#toast")), await page.textContent("#toast"));

    // Unticking every market asks for one rather than scanning everything
    await page.evaluate(() => { location.hash = "#screener"; }); await page.waitForTimeout(300);
    await page.$$eval("#scrMarkets input", (i) => i.forEach((x) => { x.checked = false; }));
    await page.click("#btnScr"); await page.waitForTimeout(400);
    expect("no markets ticked", /market/i.test(await page.textContent("#toast")), await page.textContent("#toast"));

    // An odd address doesn't break the page
    await page.goto(BASE + "#a%20b"); await page.waitForTimeout(500);
    expect("odd address falls back to the dashboard", await page.evaluate(() => !document.querySelector("#view-dashboard").classList.contains("hidden")));
  },

  // Record a buy, a sale and a dividend on the Portfolio page, and see the results
  async ledger(page) {
    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForTimeout(500);
    await page.waitForFunction(() => document.querySelector("#pfKpis").innerText.trim(), null, { timeout: 60000 });
    expect("an empty portfolio says so instead of showing zeros", /No transactions yet/.test(await page.innerText("#pfKpis")), await page.innerText("#pfKpis"));
    const record = async (kind, fields) => {
      await page.selectOption("#hKind", kind);
      for (const [k, v] of Object.entries(fields)) await page.fill(k, v);
      // wait for the save itself, then for the table to be drawn again: a fixed wait was sometimes too short under load
      await Promise.all([page.waitForResponse((r) => r.request().method() === "POST" && /\/api\/(holdings|transactions)/.test(r.url()), { timeout: 60000 }), page.click("#btnAddHolding")]);
      await page.waitForTimeout(1500);
    };
    await record("buy", { "#hTicker": "AAPL", "#hShares": "10", "#hCost": "100", "#hDate": "2024-06-03" });
    expect("buy is recorded", (await page.locator("#pfTable tbody tr").count()) === 1);
    expect("form clears", (await page.inputValue("#hTicker")) === "" && (await page.inputValue("#hDate")) === "");

    await record("sell", { "#hTicker": "AAPL", "#hShares": "11", "#hCost": "150", "#hDate": "2025-01-02" });
    expect("selling too many is refused with a reason", /more than were held/.test(await page.textContent("#toast")), await page.textContent("#toast"));
    expect("the form keeps what you typed after a refusal", (await page.inputValue("#hShares")) === "11");
    await record("sell", { "#hTicker": "AAPL", "#hShares": "4", "#hCost": "150", "#hDate": "2025-01-02" });
    await page.selectOption("#hKind", "dividend");
    expect("a dividend has no shares field", !(await page.isVisible("#hShares")));
    expect("its amount is labelled", /Total received/.test(await page.textContent("#hCostLabel")), await page.textContent("#hCostLabel"));
    await page.fill("#hTicker", "AAPL"); await page.fill("#hCost", "10"); await page.fill("#hDate", "2025-02-03");
    await page.click("#btnAddHolding"); await page.waitForTimeout(1500);
    await page.selectOption("#hKind", "buy");
    expect("shares field returns for a buy", await page.isVisible("#hShares"));

    const kpis = (await page.innerText("#pfKpis")).replace(/\s+/g, " ");
    expect("realised P&L shown", /REALISED P&L \$200/i.test(kpis), kpis);
    expect("dividends shown", /DIVIDENDS RECEIVED \$10/i.test(kpis), kpis);
    expect("annual return shown", /ANNUAL RETURN/i.test(kpis));
    expect("position is what's left", /6\.00/.test(await page.innerText("#pfTable tbody tr")));
    const rows = await page.locator("#txTable tbody tr").count();
    expect("all three are listed, not the refused one", rows === 3, rows);
    expect("history is not blank", !/undefined|NaN|null/.test(await page.innerText("#txTable")));
    await page.screenshot({ path: `${OUT}/ledger.png`, fullPage: true });

    // Sorting then deleting: refusal for the buy, then the sale, then the buy
    const dialogs = (d) => d.accept(); page.on("dialog", dialogs);
    await page.click("#txTable thead th:nth-child(1)"); await page.waitForTimeout(150);
    const del = async (kind) => { await page.locator(`#txTable tbody tr:has-text("${kind}") [data-tdel]`).click(); await page.waitForTimeout(1200); };
    await del("buy");
    expect("deleting a buy a sale needs is refused", /sale depends/.test(await page.textContent("#toast")), await page.textContent("#toast"));
    await del("sell"); await del("dividend"); await del("buy");
    expect("all gone", (await page.locator("#txTable tbody tr").count()) === 0);
    expect("position gone", (await page.locator("#pfTable tbody tr").count()) === 0);
    page.off("dialog", dialogs);

    // Delete all in the Lots pop-up: the whole holding, sales and dividends included, after saying what goes
    await post(page, "/api/transactions/import", { text: "AAPL,10,100,2024-06-03\nAAPL,5,110,2024-09-02\nAAPL,4,150,2025-01-02,sell\nAAPL,,10,2025-02-03,dividend\nMSFT,2,300,2024-06-03" });
    await page.evaluate(() => { location.hash = "#dashboard"; }); await page.waitForTimeout(300);
    await page.evaluate(() => { location.hash = "#portfolio"; });
    await page.waitForFunction(() => document.querySelectorAll("#pfTable [data-lots]").length === 2, null, { timeout: 60000 });
    await page.locator('#pfTable [data-lots="AAPL"]').click(); await page.waitForTimeout(300);
    let asked = "";
    const answer = (d) => { asked = d.message(); d.accept(); }; page.on("dialog", answer);
    await page.click("#modal [data-delall]");
    await page.waitForFunction(() => document.querySelectorAll("#pfTable [data-lots]").length === 1, null, { timeout: 30000 });
    page.off("dialog", answer);
    const left = (await post(page, "/api/transactions", null, "GET")).body;
    expect("delete all asks first, naming what goes", /Delete all 4 transactions of AAPL \(2 purchases, 1 sale, 1 dividend\)/.test(asked), asked);
    expect("delete all removes the whole holding and nothing else", left.length === 1 && left[0].ticker === "MSFT", JSON.stringify(left.map((x) => x.ticker)));
  },

  // The chart period buttons change the chart and nothing else
  async periods(page) {
    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.waitForTimeout(800);
    await page.fill("#dGrowth", "7.5"); await page.fill("#ptAmount", "1234");
    await page.click("#btnBrief"); await page.waitForFunction(() => !document.querySelector("#btnBrief").disabled, null, { timeout: 60000 });
    const brief = await page.innerText("#briefOut");
    const bars = () => page.evaluate(() => state.stock.candles.time.length);
    const year = await bars();
    await page.click('#periodSeg button[data-p="3mo"]'); await page.waitForFunction((n) => state.stock.candles.time.length !== n, year, { timeout: 30000 }); await page.waitForTimeout(300);
    expect("chart shows the new period", (await bars()) < year, `${year} -> ${await bars()}`);
    expect("button marked", await page.evaluate(() => document.querySelector('#periodSeg button[data-p="3mo"]').classList.contains("on")));
    expect("brief is kept", (await page.innerText("#briefOut")) === brief);
    expect("DCF input is kept", (await page.inputValue("#dGrowth")) === "7.5");
    expect("pre-trade input is kept", (await page.inputValue("#ptAmount")) === "1234");
    expect("chart still drawn", (await page.locator("#priceChart canvas").count()) > 0);
  },

  // The browser's Back and Forward move between screens
  async backbutton(page) {
    await page.click('[data-view="portfolio"]'); await page.waitForTimeout(200);
    await page.click('[data-view="journal"]'); await page.waitForTimeout(200);
    const visible = () => page.evaluate(() => [...document.querySelectorAll(".view")].find((v) => !v.classList.contains("hidden")).id);
    await page.goBack(); await page.waitForTimeout(300);
    expect("Back returns to Portfolio", (await visible()) === "view-portfolio", await visible());
    await page.goForward(); await page.waitForTimeout(300);
    expect("Forward returns to Journal", (await visible()) === "view-journal", await visible());
    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL", null, { timeout: 60000 });
    await page.click('[data-view="screener"]'); await page.waitForTimeout(200);
    await page.goBack(); await page.waitForTimeout(600);
    expect("Back returns to the stock", (await visible()) === "view-research" && (await page.textContent("#rSym")) === "AAPL");
    await page.goBack(); await page.waitForTimeout(300);
    expect("and back again to Journal, without a loop", (await visible()) === "view-journal", await visible());
    // A symbol that needs encoding doesn't trap you
    await page.evaluate(() => { location.hash = "#research/%5EVIX"; }); await page.waitForTimeout(1500);
    const before = await page.evaluate(() => history.length);
    await page.waitForTimeout(300);
    expect("no extra history entries", (await page.evaluate(() => history.length)) === before);
    await page.goBack(); await page.waitForTimeout(400);
    expect("Back leaves that page", (await visible()) === "view-journal", await visible());
  },

  // The form shows the currency and price, and questions a figure in the wrong unit
  async pence(page) {
    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForTimeout(400);
    await page.fill("#hTicker", "azn.l"); await page.press("#hTicker", "Tab");
    await page.waitForFunction(() => /GBp/.test(document.querySelector("#hHint").textContent), null, { timeout: 30000 });
    const hint = await page.textContent("#hHint");
    expect("shows the currency and that it's pence", /GBp/.test(hint) && /pence/.test(hint), hint);
    const price = await page.evaluate(async () => (await (await fetch("/api/quote/AZN.L")).json()).price);
    await page.fill("#hShares", "10"); await page.fill("#hDate", "2025-01-02");
    await page.fill("#hCost", String(price / 100));
    let asked = null;
    page.once("dialog", (d) => { asked = d.message(); d.dismiss(); });
    await page.click("#btnAddHolding"); await page.waitForTimeout(600);
    expect("a price 100x off is questioned", /far from/.test(asked || ""), asked);
    expect("saying no saves nothing", (await post(page, "/api/transactions", null, "GET")).body.length === 0);
    await page.fill("#hCost", String(price * 0.9));
    await page.click("#btnAddHolding"); await page.waitForTimeout(1500);
    expect("a normal price saves without asking", (await post(page, "/api/transactions", null, "GET")).body.length === 1);
    expect("hint clears with the form", (await page.textContent("#hHint")) === "");
    await page.fill("#hTicker", "NOSUCH.ZZ"); await page.press("#hTicker", "Tab"); await page.waitForTimeout(500);
    expect("no crash on a ticker without data", true);
  },

  // Fund pages only show rows that mean something
  async fundrows(page) {
    await page.evaluate(() => { location.hash = "#research/SPY"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "SPY" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.waitForTimeout(500);
    const rows = await page.evaluate(() => [...document.querySelectorAll("#statGrid > div")].map((d) => [d.children[0].textContent, d.children[1].textContent]));
    expect("no empty rows", !rows.some(([, v]) => v === "—"), JSON.stringify(rows.filter(([, v]) => v === "—")));
    expect("still has returns", rows.some(([k]) => k === "1Y"));
    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.waitForTimeout(300);
    expect("stocks keep all their rows", (await page.locator("#statGrid > div").count()) > 20);
  },

  // Pasting a list, and what the new Research sections show
  async features(page) {
    await page.evaluate(() => { location.hash = "#portfolio"; }); await page.waitForTimeout(500);
    await page.locator("#pasteBox summary").click();
    await page.fill("#hPaste", "SPY,1,100,2024-06-03\nSPY,abc,100,2024-06-04"); await page.click("#btnPaste"); await page.waitForTimeout(800);
    expect("a bad line is named and nothing is added", /Line 2/.test(await page.textContent("#toast")) && (await post(page, "/api/transactions", null, "GET")).body.length === 0, await page.textContent("#toast"));
    expect("the text stays so you can fix it", /abc/.test(await page.inputValue("#hPaste")));
    await page.fill("#hPaste", "ticker,shares,price,date,type\nSPY,5,400,2024-06-03\nAAPL,10,150,2024-06-03\nAAPL,2,200,2025-01-02,sell"); await page.click("#btnPaste"); await page.waitForTimeout(2500);
    expect("pasted transactions are listed", (await page.locator("#txTable tbody tr").count()) === 3, await page.textContent("#toast"));
    expect("box clears", (await page.inputValue("#hPaste")) === "");
    expect("sector chart opens up funds", await page.evaluate(() => state.charts.aSector.data.labels.some((l) => l !== "ETFs")), await page.evaluate(() => state.charts.aSector.data.labels.join(", ")));

    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.waitForSelector("#rEarnings table", { timeout: 20000 });
    expect("stock shows its earnings record", /Beat|Missed/.test(await page.innerText("#rEarnings")), (await page.innerText("#rEarnings")).slice(0, 90));
    expect("stock has no fund card", !(await page.isVisible("#rFundCard")));
    await page.screenshot({ path: `${OUT}/features_stock.png`, fullPage: true });

    await page.evaluate(() => { location.hash = "#research/SPY"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "SPY" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.waitForSelector("#rFund .statgrid", { timeout: 20000 });
    const fund = await page.innerText("#rFund");
    expect("fund shows its costs, sectors and top holdings", /Yearly cost/i.test(fund) && /Top holdings/i.test(fund) && /Sectors/i.test(fund), fund.slice(0, 120));
    expect("fund shows no earnings", (await page.innerText("#rEarnings")) === "");
    await page.screenshot({ path: `${OUT}/features_fund.png`, fullPage: true });

    // The digest starts the score history, which then shows on Research
    await page.evaluate(() => { location.hash = "#dashboard"; }); await page.waitForTimeout(2500);
    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL" && document.querySelector("#researchBody").style.opacity === "1", null, { timeout: 60000 });
    await page.waitForTimeout(400);
    expect("score history is explained from day one", /Score history/.test(await page.innerText("#rHistory")), await page.innerText("#rHistory"));
  },

  // Text from Yahoo can't run as a script in the brief
  async xss(page) {
    await page.evaluate(() => { location.hash = "#research/AAPL"; });
    await page.waitForFunction(() => document.querySelector("#rSym")?.textContent === "AAPL", null, { timeout: 60000 });
    await page.waitForTimeout(1000);
    const evil = ['Evil <img src=x onerror="window.__pwn=1">', "<script>window.__pwn=2</script>", "[click](javascript:window.__pwn=3)"];
    await page.route("**/api/stock/AAPL/brief", (route) => route.fulfill({ json: { text: `## Summary\n\n**Bold** ${evil.join(" ")}\n\n- point one` } }));
    await page.click("#btnBrief"); await page.waitForTimeout(1500);
    const r = await page.evaluate(() => { const o = document.querySelector("#briefOut"); return { pwn: window.__pwn, html: o.innerHTML, h: !!o.querySelector("h2"), b: !!o.querySelector("strong"), li: !!o.querySelector("li"), onerr: !!o.querySelector("[onerror]"), js: !!o.querySelector('a[href^="javascript"]') }; });
    expect("no script ran", r.pwn === undefined, String(r.pwn));
    expect("no onerror attribute or script link survives", !r.onerr && !r.js && !/<script/i.test(r.html));
    expect("normal markdown still renders", r.h && r.b && r.li);
    await page.click("#btnBrief"); await page.waitForTimeout(500);
  },
};

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE, { waitUntil: "load" }); await page.waitForTimeout(800);
  await CHECKS[CHECK](page);
  expect("no uncaught JS errors", errors.length === 0, errors.join(" | ").slice(0, 200));
  await browser.close();
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error("DRIVER FAILED", e.message.slice(0, 400)); process.exit(2); });
