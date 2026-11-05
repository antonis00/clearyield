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
    const hid = (await post(page, "/api/backup", null, "GET")).body.holdings[0].id;
    await post(page, `/api/holdings/${hid}`, null, "DELETE");
    const dialogs = []; page.on("dialog", (d) => { dialogs.push(d.message()); d.accept(); });
    await page.setInputFiles("#restoreFile", path); await page.waitForTimeout(2500);
    expect("asks first and says what is in the file", dialogs[0]?.includes("1 lots") && dialogs[0]?.includes("1 journal"), dialogs[0]);
    const after = (await post(page, "/api/backup", null, "GET")).body;
    expect("holding is back", after.holdings.length === 1 && after.holdings[0].note === "core");
    expect("journal is intact", after.journal[0].thesis === "services growth");
    await page.setInputFiles("#restoreFile", { name: "x.json", mimeType: "application/json", buffer: Buffer.from("not json") }); await page.waitForTimeout(500);
    expect("rejects a broken file", (await page.textContent("#toast")).includes("isn't a StockPilot backup"));
    expect("data untouched by broken file", (await post(page, "/api/backup", null, "GET")).body.holdings.length === 1);
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
