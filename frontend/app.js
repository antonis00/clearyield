/* StockPilot frontend — vanilla JS, Lightweight Charts (price) + Chart.js (everything else). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { stockRequest: 0, meta: null, ticker: null, period: "1y", bars: "day", stock: null, lw: [], charts: {}, lastScreen: null };

const CSS = getComputedStyle(document.documentElement);
const C = (n) => CSS.getPropertyValue(n).trim();
const PALETTE = ["#f2b84b", "#4fb3bf", "#a78bfa", "#3fb68b", "#ef5b5b", "#f472b6", "#60a5fa", "#fb923c", "#94a3b8", "#22d3ee"];
const tint = (token, alpha) => C(token) + alpha; // tokens are #rrggbb, so this adds transparency
Chart.defaults.color = C("--muted");
Chart.defaults.borderColor = C("--line");
Chart.defaults.font.family = "IBM Plex Sans, system-ui, -apple-system, Segoe UI, sans-serif";

/* ---------------------------------------------------------------- helpers */
async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers = { "Content-Type": "application/json" }; }
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : r.status === 422 ? "Please check the values you entered." : `Request failed (${r.status})`);
  return j;
}
const isNum = (v) => v !== null && v !== undefined && !Number.isNaN(v);
const num = (v, d = 2) => (isNum(v) ? Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
const pct = (v, d = 1, sign = true) => (isNum(v) ? `${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%` : "—");
const cls = (v) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "") : "");
const cpct = (v, d = 1) => `<span class="${cls(v)}">${pct(v, d)}</span>`;
function big(v) {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  for (const [n, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (a >= n) return (v / n).toFixed(2) + s;
  return num(v, 0);
}
function money(v, cur = "USD", d = 2) {
  if (!isNum(v)) return "—";
  if (!cur) return num(v, d);
  if (cur === "GBp") return `${num(v, d)} GBp`;
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, minimumFractionDigits: d, maximumFractionDigits: d }).format(v); }
  catch { return `${num(v, d)} ${esc(cur)}`; }
}
// "Today" only when every holding's latest price is from today; otherwise the day (or days) the change is for,
// since at a weekend stocks show Friday's move and crypto the weekend's
const shortDay = (iso) => new Date(`${iso}T12:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
function dayChange(p) {
  if (!p.dayTo) return ["Today", ""];
  const today = new Date().toLocaleDateString("en-CA");
  if (p.dayFrom === today && p.dayTo === today) return ["Today", ""];
  return ["Latest day", p.dayFrom === p.dayTo ? shortDay(p.dayTo) : `${shortDay(p.dayFrom)} – ${shortDay(p.dayTo)}, each holding's latest`];
}
// A price as it should read: a yield like the 10-year Treasury's in percent, an index in points, anything else in its currency
const priceOf = (v, r) => r?.priceUnit === "%" ? (isNum(v) ? `${num(v, 2)}%` : "—") : r?.priceUnit === "points" ? num(v, 2) : money(v, r?.currency);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const badge = (st) => `<span class="badge b-${esc((st || "Insufficient").split(" ")[0])}">${esc(st || "n/a")}</span>`;
const scoreColor = (s) => (!isNum(s) ? C("--muted") : s >= 70 ? C("--up") : s >= 58 ? C("--accent2") : s >= 45 ? C("--neutral") : s >= 35 ? C("--accent") : C("--down"));
const bar = (s) => `<div class="bar"><i style="width:${isNum(s) ? s : 0}%;background:${scoreColor(s)}"></i></div>`;
function toast(msg, ms = 3500) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }
const missingNote = (p) => `<b>Incomplete:</b> no current data for ${esc(p.missing.join(", "))}, so these totals leave ${p.missing.length > 1 ? "them" : "it"} out and weights and rebalancing are withheld. Check the symbol, or try again later.`;
const loading = (el, msg = "Loading…") => { el.innerHTML = `<div class="muted"><span class="spinner"></span>${msg}</div>`; };
const FACTOR_INFO = {
  value: "Value: is the price cheap for what the company earns and owns? From P/E, forward P/E, price/book, EV/EBITDA and free-cash-flow yield. A higher score means cheaper. Stocks only.",
  quality: "Quality: how profitable and financially sound the business is. From return on equity, operating and net margins, debt/equity and the current ratio. A higher score means a stronger business. Stocks only.",
  growth: "Growth: how fast the business is growing. From revenue and net income growth a year over the last three annual statements (the latest quarter against a year before, where there aren't three years or a loss at either end), and forecast against trailing earnings per share. A higher score means faster growth. Stocks only.",
  momentum: "Momentum: how the price has been trending. From the 12-month return leaving out the last month, the price against its 200-day average, and the 3-month return. A higher score means a stronger trend. All assets.",
  risk: "Risk: how bumpy the ride has been. From 1-year volatility and the largest fall from a peak in the past year, plus beta for stocks. A higher score means lower risk. One scale for every asset, crypto included.",
};
// A small "i" that explains something on hover or keyboard focus; the text is shown by the floating tooltip below
const infoIcon = (text) => `<span class="info" tabindex="0" role="img" aria-label="${esc(text)}" data-tip="${esc(text)}">i</span>`;
const factorInfo = (name) => infoIcon(FACTOR_INFO[name]);
// Why a factor has no score, in words, instead of a bare "n/a"
const ACCOUNTS = ["value", "quality", "growth"];
const KIND = { ETFs: "funds", Crypto: "crypto", Other: "indexes, currencies and futures" };
function factorGap(info, name) {
  if (ACCOUNTS.includes(name) && info.assetClass !== "Stocks") return `Doesn't apply to ${KIND[info.assetClass] || "this kind of asset"}: there are no company accounts to measure.`;
  if (ACCOUNTS.includes(name)) return "Yahoo has none of the figures this uses for this company.";
  return "Needs more price history than Yahoo has for this symbol.";
}
// What the overall score is built from, or why there isn't one
function compositeBasis(info, scores) {
  if (info.assetClass === "Other") return ["not given to market indicators", "Indexes, currencies and futures get no overall score, because they aren't something you hold. Their momentum and risk are still shown below for context."];
  if (info.assetClass !== "Stocks") return ["trend & risk only", `${KIND[info.assetClass] === "crypto" ? "Crypto has" : "Funds have"} no company accounts, so value, quality and growth don't apply. The score is the momentum and risk factors only, so compare it with other ${KIND[info.assetClass]}, not with a stock's.`];
  const missing = ACCOUNTS.concat(["momentum", "risk"]).filter((k) => !isNum(scores.factors[k]));
  return [null, missing.length ? `Built from the factors that have data. Left out: ${missing.join(", ")}.` : null];
}
function mkChart(id, cfg) { state.charts[id]?.destroy(); const ctx = document.getElementById(id); state.charts[id] = new Chart(ctx, cfg); return state.charts[id]; }

/* Generic sortable table. cols: {k,label,fmt(v,row),num,sort} */
function table(el, cols, rows, { stock, empty = "Nothing here yet." } = {}) {
  if (!rows.length) { el.innerHTML = `<div class="muted">${empty}</div>`; return { afterDraw() {} }; }
  let sortK = null, dir = -1, after = () => {};
  const draw = () => {
    const data = [...rows];
    if (sortK) data.sort((a, b) => { const x = a[sortK], y = b[sortK]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    el.innerHTML = `<table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}" data-k="${c.k}">${c.label}${sortK === c.k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${data.map((r, i) => `<tr class="${stock ? "click" : ""}" data-i="${rows.indexOf(r)}"${stock ? ` data-stock="${esc(stock(r))}"` : ""}>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.fmt ? c.fmt(r[c.k], r) : esc(r[c.k])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    $$("th", el).forEach((th) => th.onclick = (e) => { if (e.target.closest(".info")) return; const k = th.dataset.k; if (sortK === k) dir = -dir; else { sortK = k; dir = -1; } draw(); });
    after();
  };
  draw();
  // Sorting rebuilds the rows, so anything bound to them has to be bound again each time
  return { afterDraw(fn) { after = fn; fn(); } };
}
// A price Yahoo couldn't refresh is marked wherever it shows, so an old number isn't read as today's
const oldMark = (r) => r?.stale ? ` <span class="pill warn sm" title="Yahoo isn't responding, so this is the last price it gave${r.asOf ? `, from ${esc(r.asOf)}` : ""}.">old price</span>` : "";
const symCell = (v, r) => `<span class="sym">${esc(v)}</span>${oldMark(r)}<span class="sub">${esc(r.name || "")}</span>`;

function modal(html, onSubmit) {
  const m = $("#modal"); $("#modalCard").innerHTML = html; m.classList.remove("hidden");
  const close = () => m.classList.add("hidden");
  m.onclick = (e) => { if (e.target === m) close(); };
  $$("[data-close]", m).forEach((b) => b.onclick = close);
  const f = $("form", m);
  if (f) f.onsubmit = async (e) => { e.preventDefault(); try { await onSubmit(new FormData(f)); close(); } catch (err) { toast(err.message); } };
}

/* ---------------------------------------------------------------- routing */
function show(view, push = true) {
  const other = $(`#view-${view}`).classList.contains("hidden");  // another screen: it opens at its top, not where the last one was scrolled to
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  if (other) window.scrollTo(0, 0);
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  if (view === "research" && state.redrawResearch && state.stock) redrawResearch();
  if (push) {
    const target = view === "research" && state.ticker ? `#research/${state.ticker}` : `#${view}`;
    if (decodeURIComponent(location.hash) !== target) history.pushState(null, "", target); // so Back and Forward move between screens
  }
  ({ dashboard: loadDashboard, portfolio: loadPortfolio, journal: loadJournal, settings: loadSettings, opportunities: () => loadOpportunities(false), ask: renderAskSetup, backtest: loadBacktestView }[view] || (() => {}))();
}
$$("#nav a").forEach((a) => a.onclick = () => show(a.dataset.view));

// Anything marked data-stock opens that stock: a plain click here, a middle click or Ctrl/Cmd+click in a new tab.
// Buttons and links inside a row keep their own job.
const openInNewTab = (t) => window.open(`#research/${encodeURIComponent(t)}`, "_blank", "noopener");
const stockTarget = (e) => (e.target.closest("button, a, input, select, label") ? null : e.target.closest("[data-stock]"));
document.addEventListener("click", (e) => {
  const el = e.button === 0 && stockTarget(e);
  if (!el) return;
  if (e.ctrlKey || e.metaKey) openInNewTab(el.dataset.stock); else openStock(el.dataset.stock);
});
document.addEventListener("auxclick", (e) => {
  const el = e.button === 1 && stockTarget(e);
  if (el) { e.preventDefault(); openInNewTab(el.dataset.stock); }
});
// Windows starts scroll mode on a middle press; on a stock it should only open the tab
document.addEventListener("mousedown", (e) => { if (e.button === 1 && stockTarget(e)) e.preventDefault(); });
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-goto]");
  if (!g || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return; // let the browser open those in a new tab
  e.preventDefault();
  show(g.dataset.goto);
});

/* ---------------------------------------------------------------- the day's first downloads */
// The first Screener, Opportunities or sector ranking of the day downloads every stock in the regions you follow,
// which can take a minute or more. The loader shows real progress from /api/progress: how many stocks of how many,
// region by region, about how long is left, and when Yahoo has asked for a pause. A quick load (the data already kept)
// shows only the small spinner: the loader appears once a download is actually under way.
const DL_TITLE = { screener: "Scanning the stocks", opportunities: "Scoring the stocks", ranks: "Ranking the stocks within their sectors" };
const DL_NOTE = "The first load of the day downloads every stock in the regions you follow. You can adjust your regions in the Settings page.";
function dlEta(j, pause) {
  if (pause > 0) return `Yahoo asked for a short pause. Carrying on in ${Math.ceil(pause)} s…`;
  if (j.done >= j.total) return "Finishing…";
  if (j.done < 10 || j.elapsed < 3) return "Working out how long it takes…";
  const left = (j.total - j.done) / (j.done / j.elapsed);
  return left < 5 ? "Almost done…" : `About ${left < 60 ? `${Math.max(5, Math.round(left / 5) * 5)} s` : `${Math.round(left / 60)} min`} left`;
}
const dlRegions = (j) => Object.entries(j.regions).map(([r, [d, t]]) =>
  `<span class="dl-reg${d >= t ? " done" : d ? " busy" : ""}" style="--p:${t ? (d / t).toFixed(3) : 0}">${esc(r)} <b>${d >= t ? "✓" : `${d}/${t}`}</b></span>`).join("");
function dlRender(box, j, pause, kind) {
  let l = box.querySelector(".dl-loader");
  if (!l) {
    box.innerHTML = `<div class="dl-loader" role="status" aria-live="polite">
      <div class="dl-head"><span class="dl-mark" aria-hidden="true">◆</span><b>${esc(DL_TITLE[kind] || "Getting today's data")}</b><span class="dl-count"></span></div>
      <div class="dl-bar"><i></i></div><div class="dl-regions"></div>
      <div class="dl-foot"><span class="dl-eta"></span><span class="muted">${esc(DL_NOTE)}</span></div></div>`;
    l = box.querySelector(".dl-loader");
  }
  l.classList.toggle("paused", pause > 0);
  $(".dl-count", l).textContent = `${j.done.toLocaleString()} of ${j.total.toLocaleString()} stocks`;
  $(".dl-bar i", l).style.width = `${(100 * j.done / Math.max(j.total, 1)).toFixed(1)}%`;  // the same element, so it glides
  $(".dl-regions", l).innerHTML = dlRegions(j);
  $(".dl-eta", l).textContent = dlEta(j, pause);
}
// Shows `quick` with a spinner in `el` and, while that's still there and a scoring of `kind` is under way, the loader.
// It stops on its own once the caller puts the results (or an error) in `el`.
function dlLoader(el, kind, quick = "Loading…") {
  if (!el.querySelector(".dl-loader")) el.innerHTML = `<div class="muted dl-wait"><span class="spinner"></span>${quick}</div>`;
  const token = el._dl = {};
  let last = null;
  const waiting = () => el._dl === token && el.isConnected && el.querySelector(".dl-wait, .dl-loader");
  const tick = async () => {
    if (!waiting()) return;
    let p = null;
    try { p = await api("/api/progress"); } catch { /* the next check tries again */ }
    if (!waiting()) return;  // the results came while we asked
    const job = p?.jobs.filter((j) => j.kind === kind).at(-1);
    if (job) { last = job; dlRender(el, job, p.pause, kind); }
    // The download is done but the page is still working the results out (the portfolio fit, the ranks): shown complete
    else if (p && last && el.querySelector(".dl-loader")) {
      const full = Object.fromEntries(Object.entries(last.regions).map(([r, [, t]]) => [r, [t, t]]));
      dlRender(el, { ...last, done: last.total, regions: full }, 0, kind);
    }
    setTimeout(tick, 800);
  };
  setTimeout(tick, 350);
}
// The background scoring (soon after the app starts, then every six hours) shows as a small badge in the top bar while
// it runs, so a page that seems slow says why. Checked every few seconds; every second or so while it runs.
async function watchBackground() {
  let p = null;
  try { p = await api("/api/progress"); } catch { /* tried again shortly */ }
  const j = p?.jobs.find((x) => x.kind === "ranks"), b = $("#bgProgress");
  b.classList.toggle("hidden", !j);
  if (j) {
    const share = j.done / Math.max(j.total, 1);
    if (!b.querySelector("i")) b.innerHTML = `<span class="dl-mark" aria-hidden="true">◆</span><span class="bg-text"></span><i></i>`;
    $(".bg-text", b).textContent = `Preparing today's data · ${Math.round(share * 100)}%`;
    $("i", b).style.width = `${(share * 100).toFixed(1)}%`;
    b.dataset.tip = `Scoring every stock in the regions you follow in the background (${j.done} of ${j.total}), so sector ranks, the Screener and Opportunities are quick. ${dlEta(j, p.pause)}`;
  }
  setTimeout(watchBackground, j ? 1500 : 8000);
}

// Research: the overall score opens its make-up, the Factor scores card, which lights up briefly to show where it is
function toFactors() {
  const card = $("#rFactorsCard");
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");  // restart the glow on a second click
}
$("#rScoreBox").onclick = (e) => { if (!e.target.closest(".info")) toFactors(); };
$("#rScoreBox").onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toFactors(); } };

/* ---------------------------------------------------------------- search */
(() => {
  const inp = $("#globalSearch"), dd = $("#searchResults");
  let hits = [], hitsFor = "", sel = 0, timer;
  const render = () => {
    dd.innerHTML = hits.map((h, i) => `<div data-i="${i}" class="${i === sel ? "sel" : ""}">${h.screen ? `<b>${esc(h.name)}</b><span>“${esc(h.ticker)}”</span>` : `<b>${esc(h.ticker)}</b><span>${esc(h.name)}</span>`}<span class="muted sm" style="margin-left:auto">${esc(h.market)}</span></div>`).join("");
    dd.classList.toggle("hidden", !hits.length);
    $$("div", dd).forEach((d) => d.onmousedown = (e) => {
      const h = hits[+d.dataset.i];
      if (e.button === 1 || e.ctrlKey || e.metaKey) { e.preventDefault(); if (h && !h.screen) openInNewTab(h.ticker); return; }
      if (e.button === 0) pick(h);
    });
  };
  const screen = (text) => { show("screener"); $("#scrQuery").value = text; $("#btnScrQuery").click(); };
  const pick = (h) => { if (!h) return; inp.value = ""; hits = []; hitsFor = ""; render(); h.screen ? screen(h.ticker) : openStock(h.ticker); };
  // Enter takes the chosen result; a sentence that matches no stock is a screener question, run there instead of
  // asking Yahoo for a symbol
  const enter = (text) => {
    if (hits[sel] || !/\s/.test(text)) { pick(hits[sel] || { ticker: text.toUpperCase() }); return; }
    inp.value = ""; hits = []; hitsFor = ""; render(); screen(text);
  };
  inp.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = inp.value.trim();
      const found = text ? await api(`/api/search?q=${encodeURIComponent(text)}`) : [];
      if (text !== inp.value.trim()) return; // Yahoo can take a moment, and you may have typed more by now
      hits = found; hitsFor = text; sel = 0; render();
    }, 250);
  };
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    else if (e.key === "Enter") {
      const text = inp.value.trim();
      if (!text) return;
      // Results shown for shorter text (Enter pressed before the search caught up) are fetched afresh, so "CHEAP" typed
      // fast isn't taken for the symbol the first letters matched
      if (hitsFor !== text) { clearTimeout(timer); api(`/api/search?q=${encodeURIComponent(text)}`).then((found) => {
        if (text !== inp.value.trim()) return;
        hits = found; hitsFor = text; sel = 0; enter(text);
      }); return; }
      enter(text);
    }
    else if (e.key === "Escape") { hits = []; hitsFor = ""; render(); }
  };
  inp.onblur = () => setTimeout(() => { hits = []; hitsFor = ""; render(); }, 150);
})();

// Anything that fails without its own handling still tells you
window.addEventListener("unhandledrejection", (e) => { e.preventDefault(); toast(e.reason?.message || "Something went wrong."); });

/* ---------------------------------------------------------------- alerts in the tab title and notifications */
let firedSeen = null;
async function checkFiredAlerts() {
  const all = await api("/api/alerts").catch(() => null);
  if (!all) return;
  const fired = all.filter((a) => a.triggered && !a.dismissed);
  document.title = fired.length ? `(${fired.length}) StockPilot` : "StockPilot";
  if (firedSeen && window.Notification?.permission === "granted") {
    fired.filter((a) => !firedSeen.has(a.id)).forEach((a) => new Notification("StockPilot alert", { body: a.message }));
  }
  firedSeen = new Set(all.filter((a) => a.triggered).map((a) => a.id));
}

/* ---------------------------------------------------------------- dashboard */
function spark(vals, up) {
  if (!(vals?.length > 1)) return "";
  const mn = Math.min(...vals), mx = Math.max(...vals), w = 120, h = 26;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - ((v - mn) / (mx - mn || 1)) * (h - 2) - 1}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="${up ? C("--up") : C("--down")}" stroke-width="1.5" points="${pts}"/></svg>`;
}
// Below the snapshot's figures: the core share against your target, which opens the Portfolio page
function snapshotExtras(p) {
  const out = [], cs = p.coreSatellite;
  if (cs && isNum(cs.core)) {
    const core = Math.max(0, Math.min(1, cs.core)), target = Math.max(0, Math.min(1, cs.target));
    out.push(`<div class="snap-block" data-goto="portfolio" title="Core: broad index funds. Satellite: everything else. The mark is your target, set in Settings.">
      <div class="snap-h"><span>Core ${pct(core, 0, false)} · Satellite ${pct(1 - core, 0, false)}</span><span class="muted">target ${pct(target, 0, false)} core</span></div>
      <div class="cs-bar"><i class="core" style="width:${core * 100}%"></i><i class="sat" style="width:${(1 - core) * 100}%"></i><b style="left:${target * 100}%"></b></div></div>`);
  }
  return out.join("");
}
async function loadDashboard() {
  const strip = $("#macroStrip");
  if (!strip.children.length) loading(strip, "Loading markets…");
  api("/api/macro").then((m) => {
    if (!m.length) { strip.innerHTML = `<div class="muted">No market tiles chosen. <a data-goto="settings">Pick some in Settings →</a></div>`; return; }
    strip.innerHTML = m.map((x) => `<div class="mcard" data-t="${esc(x.ticker)}" data-stock="${esc(x.ticker)}"><div class="t">${esc(x.name)}${oldMark(x)}</div><div class="v">${num(x.price, x.price > 1000 ? 0 : 2)}</div><div class="sm">${cpct(x.day, 2)} <span class="muted">1D</span> · ${cpct(x.ret1m)} <span class="muted">1M</span></div>${spark(x.spark, (x.ret1m || 0) >= 0)}</div>`).join("");
  }).catch((e) => strip.innerHTML = `<div class="muted">${esc(e.message)}</div>`);

  api("/api/portfolio").then((p) => {
    $("#dashIncomplete").classList.toggle("hidden", !p.incomplete);
    if (p.incomplete) $("#dashIncomplete").innerHTML = missingNote(p);
    // Everything gained: what's still held, what was sold, and the dividends. Only the parts that aren't zero are
    // listed, so a portfolio that has never sold or been paid reads "open" alone instead of two "€0"s.
    const parts = [[p.pnl, "open", "from open positions"], [p.realised, "sold", "from sales"], [p.dividends, "dividends", "from dividends"]].filter(([v]) => Math.abs(v) >= 0.5);
    // One part alone would repeat the figure above it, so it just says where the gain is
    const gainParts = parts.length === 1 ? `<span class="muted">${parts[0][2]}</span>`
      : parts.map(([v, w]) => `<span class="${cls(v)}">${money(v, p.base, 0)}</span> ${w}`).join(" · ");
    // No income isn't missing data: say why, since a fund that reinvests (accumulates) pays nothing out
    const noIncome = p.annualIncome ? "" : p.positions.some((r) => r.assetClass === "ETFs") ? "Reinvested by your funds" : "None paid in the past year";
    $("#dashPortfolio").innerHTML = p.positions.length ? [
      ["Value", money(p.total, p.base, 0), ""], [dayChange(p)[0], money(p.dayPnl, p.base, 0), cls(p.dayPnl), esc(dayChange(p)[1])],
      ["Total gain", money(p.totalGain, p.base, 0), cls(p.totalGain), gainParts],
      ["Est. income / yr", money(p.annualIncome, p.base, 0), "", noIncome && `<span class="muted">${noIncome}</span>`],
    ].map(([l, v, c, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("")
      : `<div class="muted">No holdings yet. <a data-goto="portfolio">Add your first position →</a></div>`;
    $("#dashExtras").innerHTML = p.positions.length ? snapshotExtras(p) : "";
  }).catch(() => {});

  loadDigest();
  setTimeout(loadDashOpp, 0);  // after the rest of the dashboard: the first scoring of the day takes a while
  const wt = $("#watchTable");
  if (!wt.innerHTML) loading(wt);
  try {
    const w = await api("/api/watchlist");
    table(wt, [
      { k: "ticker", label: "Ticker", fmt: symCell }, { k: "price", label: "Price", num: 1, fmt: priceOf },
      { k: "day", label: "1D", num: 1, fmt: (v) => cpct(v, 2) }, { k: "ret1m", label: "1M", num: 1, fmt: (v) => cpct(v) },
      { k: "ret1y", label: "1Y", num: 1, fmt: (v) => cpct(v) }, { k: "rsi", label: "RSI", num: 1, fmt: (v) => num(v, 0) },
      { k: "composite", label: "Score", num: 1, fmt: (v) => `${num(v, 0)}` }, { k: "stance", label: "Stance", fmt: badge },
      { k: "ticker", label: "", fmt: (v) => `<button class="btn danger" data-rm="${esc(v)}">Remove</button>` },
    ], w, { stock: (r) => r.ticker, empty: "Your watchlist is empty. Research a ticker and click “+ Watchlist”." })
    .afterDraw(() => $$("[data-rm]", wt).forEach((b) => b.onclick = async () => { await api(`/api/watchlist/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); loadDashboard(); }));
  } catch (e) { wt.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
}
const DIGEST_RULES = "Each line is a rule with its figures. In order: your alerts and journal reminders; your portfolio's latest-day change, its core share against your target and any warnings; holdings and watchlist names that moved 3% or more in a day, have an RSI over 70 or under 30, report earnings or go ex-dividend soon, score Weak or change stance; dashboard tiles that moved more than usual for their kind (indexes 2% in a day or 8% in a month, bitcoin 5% or 15%, currencies 1% or 3%, the VIX 15% or 30%, yields 0.15 or 0.40 percentage points); and headlines from the past 3 days that name a stock or coin you hold or watch (funds left out: their feed is general news). When none of yours has any, the day's market headlines take their place, marked as such.";
$("#digestInfo").innerHTML = infoIcon(DIGEST_RULES);
// Two tabs: Highlights (alerts, journal, portfolio, holdings and watchlist, market moves) and News (headlines naming your
// stocks). The choice is remembered; switching only redraws, it doesn't ask the server again.
const digestTab = () => { try { return localStorage.getItem("digestTab") === "news" ? "news" : "highlights"; } catch { return "highlights"; } };
function renderDigest() {
  const ul = $("#digestList"), d = state.digest, tab = digestTab();
  if (!d) return;
  const news = d.items.filter((i) => i.kind === "news"), mine = d.items.filter((i) => i.kind !== "news");
  $$("#digestTabs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.tab === tab);
    b.textContent = `${b.dataset.tab === "news" ? "News" : "Highlights"} (${(b.dataset.tab === "news" ? news : mine).length})`;
  });
  const shown = tab === "news" ? news : mine;
  // Market headlines stand in when none of your stocks or coins has any: said at the top, so it's clear why
  const marketNote = tab === "news" && shown.length && shown.every((i) => i.market)
    ? '<li class="muted sm">None of the stocks or coins you hold or watch has headlines from the past 3 days, so these are headlines about the market as a whole.</li>' : "";
  // A headline links to its source; the link keeps its own job inside a row that opens the stock
  const text = (i) => i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.text)}</a>` : esc(i.text);
  ul.innerHTML = shown.length ? marketNote + shown.map((i) => `<li class="t${i.tone}${i.kind ? ` k-${esc(i.kind)}` : ""}" data-t="${esc(i.ticker || "")}"${i.ticker ? ` data-stock="${esc(i.ticker)}"` : i.kind === "portfolio" ? ` data-goto="portfolio"` : ""}>${text(i)}${i.alertId ? ` <button class="btn ghost sm" data-dismiss="${i.alertId}">Dismiss</button>` : ""}</li>`).join("")
    : `<li>${tab === "news" ? "No headlines from the past 3 days name a stock you hold or watch." : "Quiet day — nothing notable on your names."}</li>`;
  $$("[data-dismiss]", ul).forEach((b) => b.onclick = async (e) => {
    e.stopPropagation(); await api(`/api/alerts/${b.dataset.dismiss}/dismiss`, { method: "POST" });
    d.items = d.items.filter((i) => String(i.alertId) !== b.dataset.dismiss); renderDigest(); checkFiredAlerts();
  });
}
// The first load shows a spinner. A refresh keeps the current list in place, faded, and swaps in the new one in a
// single step, so nothing jumps or flickers; if it fails, the old list stays and a message says so.
async function loadDigest() {
  const ul = $("#digestList"), btn = $("#refreshDigest"), had = !!state.digest;
  if (had) { ul.classList.add("refreshing"); btn.disabled = true; btn.textContent = "Refreshing…"; }
  else loading(ul, "Scanning your portfolio, watchlist and markets…");
  try { state.digest = await api("/api/digest"); renderDigest(); }
  catch (e) { if (had) toast(`The digest couldn't be refreshed: ${e.message}`); else ul.innerHTML = `<li>${esc(e.message)}</li>`; }
  finally { ul.classList.remove("refreshing"); btn.disabled = false; btn.textContent = "Refresh"; }
}
$$("#digestTabs button").forEach((b) => b.onclick = () => {
  try { localStorage.setItem("digestTab", b.dataset.tab); } catch { /* not remembered, but still shown */ }
  if (state.digest) renderDigest(); else $$("#digestTabs button").forEach((x) => x.classList.toggle("on", x === b));
});
$$("#digestTabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === digestTab()));
$("#refreshDigest").onclick = loadDigest;

/* ---------------------------------------------------------------- research */
// The range is how far back the chart goes; bars is how much time each candle covers
const chartUrl = () => `/api/stock/${encodeURIComponent(state.ticker)}?period=${state.period}&bars=${state.bars}`;
async function openStock(t, period) {
  if (state.ticker !== t.toUpperCase()) window.scrollTo(0, 0);  // another stock starts at the top too, also from Research itself
  state.ticker = t.toUpperCase(); state.period = period || state.period;
  show("research");
  $("#researchEmpty").classList.add("hidden");
  const body = $("#researchBody");
  body.classList.remove("hidden"); body.style.opacity = 0.5;
  const request = ++state.stockRequest; // a slower reply for an earlier stock must not overwrite this one
  try {
    const s = await api(chartUrl());
    if (request !== state.stockRequest) return;
    state.stock = s; body.style.opacity = 1;
    renderHeader(s); renderPrice(s); renderFactors(s); renderStats(s); renderFin(s);
    loadPeers(state.ticker, request);
    $("#briefOut").innerHTML = "Combines scores, price action, fundamentals and headlines into a bull/bear summary.";
    $("#briefOut").classList.add("muted");
    $("#rAbout").textContent = s.info.longBusinessSummary || "No description available.";
    $("#ptOut").innerHTML = ""; ["#ptAmount", "#ptStop", "#ptRisk"].forEach((id) => $(id).value = "");
    $("#ptEntry").value = s.stats.price.toFixed(2);
    $("#ptAmountLabel").textContent = `Amount (${state.meta.settings.base_currency})`;
    $("#ptRiskLabel").textContent = `Most I'd lose (${state.meta.settings.base_currency})`;
    // The default growth: revenue a year over three years where there are the statements, else the latest quarter
    const g = isNum(s.info.revenueGrowth3y) ? s.info.revenueGrowth3y : s.info.revenueGrowth; $("#dGrowth").value = isNum(g) ? Math.min(Math.max(g * 100, 0), 15).toFixed(1) : 5;
    if (hasDcf(s)) runDcf();
    loadNews(); loadEarnings(); loadFund(s); loadOwnership(s); renderHistory(s);
  } catch (e) {
    if (request !== state.stockRequest) return;
    body.style.opacity = 1; body.classList.add("hidden"); $("#researchEmpty").classList.remove("hidden");
    $("#researchEmpty").textContent = e.message;
  }
}
function renderHeader(s) {
  const i = s.info, st = s.stats;
  $("#rSym").textContent = s.ticker; $("#rName").textContent = i.name || "";
  const when = (iso, days) => (iso ? `${iso} (${days === 0 ? "today" : `in ${days}d`})` : null);
  $("#rTags").innerHTML = [i.assetClass, i.region, i.sector, i.currency].filter(Boolean).map((x) => `<span class="pill">${esc(x)}</span>`).join("")
    + (s.events.earnings ? `<span class="pill warn">Earnings ${esc(when(s.events.earnings, s.events.earningsDays))}</span>` : "")
    + (s.events.exDividend ? `<span class="pill warn">Ex-dividend ${esc(when(s.events.exDividend, s.events.exDividendDays))}</span>` : "");
  $("#rPrice").textContent = priceOf(st.price, i);
  $("#rFresh").innerHTML = st.stale ? `<span class="pill warn">Old data from ${esc(st.asOf)}: Yahoo isn't responding</span>` : `<span class="muted sm">Prices to ${esc(st.asOf)}</span>`;
  $("#rChange").innerHTML = `<span class="${cls(st.change)}">${st.change > 0 ? "+" : ""}${num(st.change)} (${pct(st.changePct, 2)})</span> <span class="muted sm">1D</span>`;
  const sc = s.scores.composite;
  $("#rScore").textContent = num(sc, 0);
  $("#rScore").style.background = `conic-gradient(${scoreColor(sc)} ${(sc || 0) * 3.6}deg, ${C("--line")} 0)`;
  $("#rScore").style.boxShadow = `inset 0 0 0 7px ${C("--panel")}`;
  const w = state.meta.settings.weights;
  const [kind, why] = compositeBasis(i, s.scores);
  const basis = kind || (Object.values(w).every((v) => Math.abs(v - 0.2) < 0.005) ? "balanced"
    : Object.entries(w).map(([k, v]) => `${k[0].toUpperCase()} ${Math.round(v * 100)}`).join(" · "));
  $("#rStance").innerHTML = `${badge(s.scores.stance)}<div class="muted sm">Composite (${esc(basis)})${why ? ` ${infoIcon(why)}` : ""}</div>`;
  $("#btnWatch").textContent = s.watched ? "✓ Watching" : "+ Watchlist";
}
// The mouse wheel scrolls the page, as everywhere else; Ctrl (or Cmd) + wheel zooms the chart around the pointer.
// Dragging still pans and a touchpad pinch still zooms.
function lwChart(el, height) {
  const chart = LightweightCharts.createChart(el, {
    height, layout: { background: { color: "transparent" }, attributionLogo: false, textColor: C("--muted"), fontFamily: "IBM Plex Mono" },
    grid: { vertLines: { color: C("--grid") }, horzLines: { color: C("--grid") } }, rightPriceScale: { borderColor: C("--line") },
    timeScale: { borderColor: C("--line") }, crosshair: { mode: 0 }, autoSize: true,
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
  });
  el._lw = chart;
  if (!el.dataset.ctrlZoom) {  // the element outlives its charts, so the listener is added once
    el.dataset.ctrlZoom = "1";
    el.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey) || !el._lw) return;  // a plain wheel scrolls the page
      e.preventDefault();  // and Ctrl + wheel zooms this chart, not the whole page
      const ts = el._lw.timeScale(), r = ts.getVisibleLogicalRange();
      if (!r) return;
      const at = ts.coordinateToLogical(e.clientX - el.getBoundingClientRect().left) ?? (r.from + r.to) / 2;
      const f = e.deltaY > 0 ? 1.15 : 1 / 1.15;
      if (f < 1 && r.to - r.from < 10) return;  // close enough: a few bars across
      ts.setVisibleLogicalRange({ from: at - (at - r.from) * f, to: at + (r.to - at) * f });
    }, { passive: false });
  }
  return chart;
}
const series = (c, k) => c.time.map((t, i) => (isNum(c[k][i]) ? { time: t, value: c[k][i] } : null)).filter(Boolean);
function renderPrice(s) {
  state.lw.forEach((c) => c.remove()); state.lw = [];
  const c = s.candles, intraday = c.intraday;
  $("#indPanes").classList.toggle("hidden", !!intraday);
  $("#barSeg").classList.toggle("hidden", !!intraday);
  ["#tSma", "#tBb"].forEach((id) => { $(id).disabled = !!intraday; });
  $$("#barSeg button").forEach((x) => x.classList.toggle("on", x.dataset.b === c.bars));
  $("#chartNote").textContent = c.note ? c.note
    : intraday ? `${intraday.replace("m", "-minute")} candles, in the market's local time. Moving averages, RSI and MACD use daily prices, so they appear from 1M.`
    : c.bars === "week" ? "One candle per week. The lines are still daily (SMA 50 is 50 days), taken at each week's last close." : "";
  const main = lwChart($("#priceChart"), 400);
  state.lw = [main];
  if (intraday) main.applyOptions({ timeScale: { timeVisible: true, secondsVisible: false } });
  drawPrice(main, c);
  if ($("#tVol").checked) {
    const v = main.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    main.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    v.setData(c.time.map((t, i) => ({ time: t, value: c.volume[i], color: c.close[i] >= c.open[i] ? tint("--up", "44") : tint("--down", "44") })));
  }
  if (!intraday && $("#tSma").checked) {
    main.addLineSeries({ color: C("--accent"), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, title: "SMA50" }).setData(series(c, "sma50"));
    main.addLineSeries({ color: C("--accent2"), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, title: "SMA200" }).setData(series(c, "sma200"));
  }
  if (!intraday && $("#tBb").checked) {
    for (const k of ["bbUpper", "bbLower"]) main.addLineSeries({ color: tint("--violet", "99"), lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }).setData(series(c, k));
  }
  if (!intraday) drawIndicators(c);
  main.timeScale().fitContent();
  // keep the panes in sync
  let syncing = false;
  state.lw.forEach((src) => src.timeScale().subscribeVisibleLogicalRangeChange((rng) => {
    if (syncing || !rng) return; syncing = true;
    state.lw.forEach((dst) => dst !== src && dst.timeScale().setVisibleLogicalRange(rng)); syncing = false;
  }));
}
// Candles show each period's open, high, low and close; area and line show only the close,
// coloured by whether the price rose or fell over the range on screen
function drawPrice(chart, c) {
  const ohlc = c.time.map((t, i) => ({ time: t, open: c.open[i], high: c.high[i], low: c.low[i], close: c.close[i] }));
  const closes = c.time.map((t, i) => ({ time: t, value: c.close[i] }));
  const tone = c.close.at(-1) >= c.close[0] ? C("--up") : C("--down");
  if (state.kind === "line") chart.addLineSeries({ color: tone, lineWidth: 2 }).setData(closes);
  else if (state.kind === "area") chart.addAreaSeries({ lineColor: tone, topColor: `${tone}55`, bottomColor: `${tone}05`, lineWidth: 2 }).setData(closes);
  else chart.addCandlestickSeries({ upColor: C("--up"), downColor: C("--down"), borderVisible: false, wickUpColor: C("--up"), wickDownColor: C("--down") }).setData(ohlc);
}
function drawIndicators(c) {
  const rsi = lwChart($("#rsiChart"), 110), macd = lwChart($("#macdChart"), 110);
  state.lw.push(rsi, macd);
  const r = rsi.addLineSeries({ color: C("--violet"), lineWidth: 1.5, priceLineVisible: false });
  r.setData(series(c, "rsi"));
  r.createPriceLine({ price: 70, color: tint("--down", "88"), lineStyle: 2, axisLabelVisible: false });
  r.createPriceLine({ price: 30, color: tint("--up", "88"), lineStyle: 2, axisLabelVisible: false });
  macd.addHistogramSeries({ priceLineVisible: false }).setData(c.time.map((t, i) => (isNum(c.macdHist[i]) ? { time: t, value: c.macdHist[i], color: c.macdHist[i] >= 0 ? tint("--up", "88") : tint("--down", "88") } : null)).filter(Boolean));
  macd.addLineSeries({ color: C("--accent"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(series(c, "macd"));
  macd.addLineSeries({ color: C("--accent2"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(series(c, "macdSignal"));
}
// A new period only changes the chart, so your brief, DCF inputs and pre-trade check stay as they are
async function reloadChart() {
  const request = ++state.stockRequest;
  const s = await api(chartUrl());
  if (request === state.stockRequest) { state.stock = s; renderPrice(s); }
}
// Long ranges start with weekly candles, because a thousand daily ones are too crowded to read
$$("#periodSeg button").forEach((b) => b.onclick = () => {
  $$("#periodSeg button").forEach((x) => x.classList.toggle("on", x === b));
  state.period = b.dataset.p;
  state.bars = ["2y", "5y"].includes(state.period) ? "week" : "day";
  reloadChart();
});
$$("#barSeg button").forEach((b) => b.onclick = () => { state.bars = b.dataset.b; reloadChart(); });
// The chart type only changes the drawing, so it needs no new data, and it's remembered for next time
const markKind = () => $$("#kindSeg button").forEach((x) => x.classList.toggle("on", x.dataset.k === state.kind));
try { state.kind = localStorage.getItem("chartKind"); } catch { state.kind = null; }
if (!["candle", "area", "line"].includes(state.kind)) state.kind = "candle"; // also covers a type that no longer exists
markKind();
$$("#kindSeg button").forEach((b) => b.onclick = () => {
  state.kind = b.dataset.k; markKind();
  try { localStorage.setItem("chartKind", state.kind); } catch { /* private window: just not remembered */ }
  if (state.stock) renderPrice(state.stock);
});
["#tSma", "#tBb", "#tVol"].forEach((id) => $(id).onchange = () => state.stock && renderPrice(state.stock));

function fmtBound(v, fmt) {
  if (fmt === "pct") return `${+(v * 100).toFixed(1)}%`;
  return fmt === "x" ? `${v}x` : `${v}`;
}
function fmtPart(p) {
  if (p.fmt === "loss") return "loss";
  if (p.fmt === "pct") return pct(p.value, 1, false);
  if (p.fmt === "x") return isNum(p.value) ? `${num(p.value, 2)}x` : "—";
  return num(p.value, 2);
}
function renderFactors(s) {
  const f = s.scores.factors, names = ["value", "quality", "growth", "momentum", "risk"];
  $("#rCoverage").textContent = `Data coverage ${Math.round(s.scores.coverage * 100)}% (${s.scores.metricsScored} of ${s.scores.metricsTotal} metrics)`;
  mkChart("radar", {
    type: "radar",
    data: { labels: names.map((n) => n[0].toUpperCase() + n.slice(1)), datasets: [{ data: names.map((n) => f[n]), spanGaps: true, backgroundColor: tint("--accent", "33"), borderColor: C("--accent"), pointBackgroundColor: C("--accent") }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 }, grid: { color: C("--line") }, angleLines: { color: C("--line") }, pointLabels: { color: C("--text"), font: { size: 12 } } } } },
  });
  $("#factorList").innerHTML = names.map((n) => `<div class="factor"><div class="fh"><span>${n} ▸ ${factorInfo(n)}</span><span style="color:${scoreColor(f[n])}">${isNum(f[n]) ? num(f[n], 0) : "—"}</span></div>${isNum(f[n]) ? bar(f[n]) : `<div class="gap">${esc(factorGap(s.info, n))}</div>`}<div class="peer" data-peer="${n}"></div>
    <div class="parts">${(s.scores.breakdown[n] || []).map((p) => `<div><span>${esc(p.label)}<small>0 at ${fmtBound(p.bad, p.fmt === "loss" ? "num" : p.fmt)}, 100 at ${fmtBound(p.good, p.fmt === "loss" ? "num" : p.fmt)}</small></span><span>${fmtPart(p)}</span><span style="color:${scoreColor(p.score)}"${p.note ? ` title="${esc(p.note)}"` : ""}>${isNum(p.score) ? num(p.score, 0) : p.note ? "not used" : "n/a"}</span></div>${p.note && p === (s.scores.breakdown[n] || []).find((x) => x.note) ? `<div class="part-note">${esc(p.note)}</div>` : ""}`).join("") || '<div><span>No data for this factor</span></div>'}</div></div>`).join("");
  $$(".factor .fh").forEach((h) => h.onclick = (e) => { if (!e.target.closest(".info")) h.parentElement.classList.toggle("open"); });
  $("#signalList").innerHTML = s.scores.signals.map((x) => `<li class="t${x.tone}">${esc(x.text)}</li>`).join("") || "<li>No notable signals.</li>";
}
// Where each score stands among the other stocks of its sector, from every market in the list. It loads after the
// page, since the first ranking of the hour scores the whole list.
const PEER_INFO = "Ranks compare a score with the other stocks in the same sector, from every market in StockPilot's list. 50% is the "
  + "sector's middle. The score itself is on one fixed scale for every stock, so a bank and a software company meet the same limits; "
  + "the rank says how it compares with its own kind. A sector with fewer than 8 others isn't ranked.";
async function loadPeers(t, request, tries = 0) {
  const stock = state.stock?.info?.assetClass === "Stocks";
  if (!tries || !$("#rPeers .dl-loader")) $("#rPeers").innerHTML = stock ? '<span class="muted">Comparing with its sector…</span>' : "";
  $$(".factor .peer").forEach((el) => { el.textContent = ""; });
  if (!stock) return;
  let r;
  try { r = await api(`/api/stock/${encodeURIComponent(t)}/peers`); } catch { r = null; }
  if (request !== state.stockRequest) return;
  if (!r) { $("#rPeers").innerHTML = ""; return; }
  if (r.pending) {  // the first ranking of the followed regions is being built in the background: look again shortly
    dlLoader($("#rPeers"), "ranks", "Sector ranks are being prepared: the first ranking downloads every stock in the regions you follow…");
    if (tries < 180) setTimeout(() => { if (request === state.stockRequest) loadPeers(t, request, tries + 1); }, 5000);  // up to 15 minutes, as long as the build may wait on Yahoo
    return;
  }
  const where = Object.entries(r.regions).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k)} ${v}`).join(", ");
  const line = (k) => {
    const x = r.ranks[k];
    if (x.beats == null) return x.of ? `only ${x.of} other ${esc(r.sector)} stock${x.of === 1 ? " has" : "s have"} this score: too few to rank` : "";
    return `better than <b>${pct(x.beats, 0, false)}</b> of ${x.of} ${esc(r.sector)} stocks · their middle: ${num(x.median, 0)}`;
  };
  const c = r.ranks.composite;
  $("#rPeers").innerHTML = (c.beats == null
    ? `<span class="muted">${r.others} other ${esc(r.sector)} stock${r.others === 1 ? "" : "s"} in the list: too few to rank against</span>`
    : `In its sector: the overall score is better than <b>${pct(c.beats, 0, false)}</b> of ${c.of} other ${esc(r.sector)} stocks <span class="muted">(${where})</span>`) + ` ${infoIcon(PEER_INFO)}`;
  $$(".factor .peer").forEach((el) => { el.innerHTML = line(el.dataset.peer); });
}
function renderStats(s) {
  const i = s.info, st = s.stats, cur = i.currency;
  const rows = [
    ["Market cap", big(i.marketCap)], ["52w high", priceOf(st.high52, i)], ["52w low", priceOf(st.low52, i)], ["RSI (14)", num(st.rsi, 0)],
    ["1M", cpct(st.ret1m)], ["3M", cpct(st.ret3m)], ["YTD", cpct(st.ytd)], ["1Y", cpct(st.ret1y)], ["5Y CAGR", cpct(st.ret5yCagr)],
    ["Volatility 1Y", pct(st.vol1y, 1, false)], ["Max DD 1Y", cpct(st.maxDD1y)], ["Sharpe 1Y", num(st.sharpe1y)],
    ["Beta (2Y vs bench)", num(st.betaCalc)], ["P/E", num(i.trailingPE, 1)], ["Fwd P/E", num(i.forwardPE, 1)], ["P/B", num(i.priceToBook, 1)],
    ["EV/EBITDA", num(i.enterpriseToEbitda, 1)], ["PEG", num(i.pegRatio)], ["ROE", pct(i.returnOnEquity, 1, false)],
    ["Op. margin", pct(i.operatingMargins, 1, false)], ["Net margin", pct(i.profitMargins, 1, false)], ["Debt/Equity", isNum(i.debtToEquity) ? num(i.debtToEquity) + "x" : "—"],
    ["Rev. growth", cpct(i.revenueGrowth)], ["EPS growth", cpct(i.earningsGrowth)], ["Div. yield", pct(i.dividendYield, 2, false)],
    ["Payout ratio", pct(i.payoutRatio, 0, false)], ["Analyst target", money(i.targetMeanPrice, cur)], ["Consensus", esc((i.recommendationKey || "—").replace("_", " "))],
    ...(i.assetClass === "Stocks" ? [["Next earnings", esc(s.events.earnings || "not announced")], ["Ex-dividend", esc(s.events.exDividend || "not announced")]] : []),
  ];
  const shown = i.assetClass === "Stocks" ? rows : rows.filter(([, v]) => String(v).replace(/<[^>]+>/g, "") !== "—"); // funds have no fundamentals
  $("#statGrid").innerHTML = shown.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");
}
// Company statements and a cash-flow valuation only mean something for a company: for a fund, crypto, an index or a
// currency both cards are hidden and their neighbours take the full row
const isCompany = (s) => s.info.assetClass === "Stocks";
// A bank's free cash flow follows its deposits and loans, so a cash-flow valuation and FCF bars would mislead
const isBank = (s) => /bank/i.test(s.info.industry || "");
const hasDcf = (s) => isCompany(s) && !isBank(s);
function renderFin(s) {
  $("#finCard").classList.toggle("hidden", !isCompany(s)); $("#dcfCard").classList.toggle("hidden", !hasDcf(s));
  const f = s.financials || [];
  $("#finEmpty").classList.toggle("hidden", !!f.length); $("#finChart").classList.toggle("hidden", !f.length);
  if (!f.length) return;
  mkChart("finChart", {
    type: "bar",
    data: { labels: f.map((x) => x.year), datasets: [
      { label: "Revenue", data: f.map((x) => x.revenue), backgroundColor: tint("--accent2", "aa") },
      { label: "Net income", data: f.map((x) => x.netIncome), backgroundColor: tint("--accent", "aa") },
      ...(isBank(s) ? [] : [{ label: "Free cash flow", data: f.map((x) => x.fcf), backgroundColor: tint("--violet", "aa") }])] },
    options: { plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${big(c.raw)}` } } }, scales: { y: { beginAtZero: true, ticks: { callback: (v) => big(v) } } } },
  });
}
async function runDcf() {
  const ticker = state.ticker;
  const out = $("#dcfOut"); const q = new URLSearchParams({ growth: $("#dGrowth").value / 100, years: $("#dYears").value, discount: $("#dDisc").value / 100, terminal: $("#dTerm").value / 100 });
  try {
    const d = await api(`/api/dcf/${encodeURIComponent(ticker)}?${q}`);
    if (ticker !== state.ticker) return;
    if (d.error) { out.innerHTML = `<div class="muted">${esc(d.error)}</div>`; return; }
    const cur = state.stock.info.currency;
    // What the price assumes comes first: it needs no growth guess. The fair value follows as the central figure and
    // the range across the grid, since a single number looks more precise than a model this sensitive can be.
    const vals = d.grid.fairValue.flat().filter(isNum), lo = Math.min(...vals), hi = Math.max(...vals);
    out.innerHTML = `<p class="sm dcf-lead"><b>What the price assumes:</b> ${isNum(d.impliedGrowth) ? `free cash flow growing <b>${pct(d.impliedGrowth, 1, false)} a year</b> for ${d.years} years, at this discount and terminal rate. Compare it with the growth you think is realistic.` : "no growth rate from -50% to +100% a year reaches today's price with these rates."}</p>
      <div class="dcf-big"><div>${money(d.fairValue, cur)}<small>Fair value at ${pct(d.growth, 1, false)} growth</small></div><div>${vals.length ? `${money(lo, cur, 0)}–${money(hi, cur, 0)}` : "—"}<small>Range across the grid</small></div><div>${money(d.price, cur)}<small>Price</small></div><div class="${cls(d.marginOfSafety)}">${pct(d.marginOfSafety)}<small>Upside at the central figure</small></div></div>
      <div class="muted sm">Starting FCF ${big(d.startFcf)} ${esc(d.statementCurrency || "")} · Terminal value is ${pct(d.terminalShare, 0, false)} of the total, so the result is very sensitive to the discount and terminal rates. Net cash ${big(d.netCash)} ${esc(d.statementCurrency || "")}.${d.statementCurrency && !cur.startsWith(d.statementCurrency.slice(0, 2)) ? ` Fair value is converted from ${esc(d.statementCurrency)} at today's rate.` : ""}</div>
      <div class="tablewrap"><table class="grid"><tr><th>Growth ↓ · Discount →</th>${d.grid.discount.map((x) => `<th class="num">${pct(x, 1, false)}</th>`).join("")}</tr>${d.grid.fairValue.map((row, r) => `<tr><th>${pct(d.grid.growth[r], 1, false)}</th>${row.map((v) => `<td class="num ${isNum(v) ? cls(v - d.price) : ""}">${money(v, cur, 0)}</td>`).join("")}</tr>`).join("")}</table></div>
      <p class="muted sm">Fair value at each pair of rates. Green is above today's price, red below.</p>`;
  } catch (e) { out.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
}
$("#btnDcf").onclick = runDcf;
async function loadEarnings() {
  const ticker = state.ticker, el = $("#rEarnings"); el.innerHTML = "";
  const e = await api(`/api/stock/${encodeURIComponent(ticker)}/earnings`).catch(() => []);
  if (ticker !== state.ticker || !e.length) return;
  el.innerHTML = `<h4>Earnings against expectations</h4><table><tr><th>Quarter to</th><th class="num">Reported</th><th class="num">Expected</th><th>Result</th></tr>${e.map((q) => `<tr><td>${esc(q.quarter)}</td><td class="num">${num(q.actual)}</td><td class="num">${num(q.estimate)}</td><td class="${cls(q.surprise)}">${isNum(q.surprise) ? `${q.surprise >= 0 ? "Beat" : "Missed"} by ${pct(Math.abs(q.surprise), 1, false)}` : "—"}</td></tr>`).join("")}</table>`;
}
// Insiders and institutions. A trade counts as buying or selling only when it was at a price on the market: Yahoo's
// own "purchases" include share awards and option exercises, which are pay, not a decision to buy.
const OWN_INFO = "Only trades at a price on the market count as buying or selling. Share awards, option exercises, gifts and conversions are "
  + "part of pay or planning, so they're listed but not counted (Yahoo's own summary counts them as purchases). Insiders sell far more often "
  + "than they buy, often for routine reasons; buying with their own money is the rarer and stronger sign. Yahoo has these records mainly "
  + "for US and Canadian companies.";
const OWN_KIND = { buy: ["Bought", "up"], sell: ["Sold", "down"], other: ["Award, exercise or gift", "muted"] };
async function loadOwnership(s) {
  const ticker = state.ticker, card = $("#rOwnCard"), el = $("#rOwn");
  card.classList.toggle("hidden", s.info.assetClass !== "Stocks");
  el.innerHTML = "";
  if (s.info.assetClass !== "Stocks") return;
  el.innerHTML = '<span class="muted">Loading…</span>';
  const o = await api(`/api/stock/${encodeURIComponent(ticker)}/ownership`).catch(() => null);
  if (ticker !== state.ticker) return;
  if (!o) { el.innerHTML = '<div class="muted">Yahoo has no ownership data for this company. It has it mainly for US and Canadian companies.</div>'; return; }
  const cur = o.currency, sm = o.summary;
  const kpis = [["Held by insiders", pct(o.insiderPct, 2, false)], ["Held by institutions", pct(o.institutionPct, 1, false)],
    ["Institutions", isNum(o.institutionCount) ? num(o.institutionCount, 0) : "—"],
    [`Insider buys, ${sm.months} months`, `${sm.buys}${sm.buys ? ` · ${money(sm.buyValue, cur, 0)}` : ""}`],
    [`Insider sales, ${sm.months} months`, `${sm.sells}${sm.sells ? ` · ${money(sm.sellValue, cur, 0)}` : ""}`]];
  const trades = o.insiders.slice(0, 12);
  const holders = (rows, title) => rows.length ? `<h4>${title}</h4><table><tr><th>Holder</th><th class="num">Owns</th><th class="num">Change</th><th>Reported</th></tr>${rows.slice(0, 8).map((h) =>
    `<tr><td><span class="clip-wide" title="${esc(h.holder)}">${esc(h.holder)}</span></td><td class="num">${pct(h.pct, 2, false)}</td><td class="num">${cpct(h.change)}</td><td class="muted">${esc(h.reported || "")}</td></tr>`).join("")}</table>` : "";
  el.innerHTML = `<p class="own-head">${esc(sm.headline)} ${infoIcon(OWN_INFO)}</p>
    <div class="kpis">${kpis.map(([l, v]) => `<div class="kpi"><div class="l">${l}</div><div class="v sm-v">${v}</div></div>`).join("")}</div>
    <div class="grid2 own-grid">
      <div>${trades.length ? `<h4>Insider trades, past year</h4><table><tr><th>Date</th><th>Who</th><th>Trade</th><th class="num">Shares</th><th class="num">Value</th></tr>${trades.map((t) => {
        const [label, tone] = OWN_KIND[t.kind] || OWN_KIND.other;
        return `<tr><td>${esc(t.date)}</td><td><span class="clip-wide" title="${esc(`${t.insider}, ${t.position}`)}">${esc(t.insider)}</span><span class="sub">${esc(t.position)}</span></td>
          <td class="${tone}" title="${esc(t.text || "")}">${label}</td><td class="num">${num(t.shares, 0)}</td><td class="num">${isNum(t.value) && t.kind !== "other" ? money(t.value, cur, 0) : "—"}</td></tr>`; }).join("")}</table>`
        : '<div class="muted">No insider trades reported in the past year.</div>'}</div>
      <div>${holders(o.institutions, "Largest institutions") || '<div class="muted">Yahoo lists no institutions for this company.</div>'}${holders(o.funds, "Largest funds")}</div>
    </div>`;
}
async function loadFund(s) {
  const ticker = state.ticker, card = $("#rFundCard");
  card.classList.toggle("hidden", s.info.assetClass !== "ETFs");
  $("#rFund").innerHTML = "";
  if (s.info.assetClass !== "ETFs") return;
  const f = await api(`/api/stock/${encodeURIComponent(ticker)}/fund`).catch(() => null);
  if (ticker !== state.ticker || !f) return;
  const sectors = Object.entries(f.sectors).sort((a, b) => b[1] - a[1]);
  $("#rFund").innerHTML = `<div class="statgrid"><div><span>Yearly cost</span><span>${pct(f.expenseRatio, 2, false)}</span></div></div>
    ${sectors.length ? `<h4>Sectors</h4><div class="statgrid">${sectors.map(([k, v]) => `<div><span>${esc(k)}</span><span>${pct(v, 1, false)}</span></div>`).join("")}</div>` : `<p class="muted sm">Yahoo has no sector data for this fund.</p>`}
    ${f.holdings.length ? `<h4>Top holdings</h4><div class="statgrid">${f.holdings.map((h) => `<div><span>${esc(h.ticker)}</span><span>${pct(h.weight, 1, false)}</span></div>`).join("")}</div>` : ""}`;
}
function renderHistory(s) {
  const h = s.scoreHistory || [], vals = h.map((x) => x.composite);
  $("#rHistory").innerHTML = !h.length ? (s.watched ? "" : "Score history builds up daily for what you hold or watch.")
    : `Score history since ${esc(h[0].date)}: ${vals.length > 1 ? spark(vals, vals[vals.length - 1] >= vals[0]) : "one day so far."}`;
}
async function loadNews() {
  const ticker = state.ticker;
  const ul = $("#newsList"); loading(ul);
  const n = await api(`/api/stock/${encodeURIComponent(ticker)}/news`).catch(() => []);
  if (ticker !== state.ticker) return;
  ul.innerHTML = n.length ? n.map((x) => `<li>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}<div class="meta">${esc(x.publisher || "")} · ${x.time ? new Date(x.time).toLocaleString() : ""}</div></li>`).join("") : "<li class='muted'>No recent headlines.</li>";
}
$("#btnBrief").onclick = async () => {
  const ticker = state.ticker;
  const b = $("#btnBrief"), out = $("#briefOut"); b.disabled = true; loading(out, "Writing brief…");
  try { const r = await api(`/api/stock/${encodeURIComponent(ticker)}/brief`, { method: "POST" }); if (ticker !== state.ticker) return; out.classList.remove("muted"); out.innerHTML = markFigures(DOMPurify.sanitize(marked.parse(r.text)), r.check) + briefFooter(r); }
  catch (e) { out.textContent = e.message; } finally { b.disabled = false; }
};
// Who wrote the brief, and whether every figure in it could be found in the data it was given (or worked out from
// figures that were)
function briefFooter(r) {
  if (!r.by) return "";
  const c = r.check, by = `Written by ${esc(r.by)}.`;
  if (!c || !c.checked) return `<p class="muted sm">${by}</p>`;
  const own = c.suggested?.length ? ` The triggers use ${c.suggested.length === 1 ? "a level" : `${c.suggested.length} levels`} of the model's own, not from the data: ${c.suggested.map(esc).join(", ")}.` : "";
  const facts = c.checked - (c.suggested?.length || 0), worked = c.derived?.length || 0;
  const how = worked ? ` ${worked === 1 ? "One is" : `${worked} are`} worked out from the others (underlined; hover to see how).` : "";
  const fixed = r.revised?.fixed ? ` The model corrected ${r.revised.fixed === 1 ? "one figure or quote" : `${r.revised.fixed} figures or quotes`} that weren't in the data.` : "";
  const quotes = c.quotes?.length ? ` ${c.quotes.length === 1 ? "A quotation isn't" : `${c.quotes.length} quotations aren't`} in the sources word for word (marked).` : "";
  if (!c.unmatched.length && !quotes) return `<p class="brief-check ok">✓ All ${facts} figures match the data.${how}${fixed}${own} ${by}</p>`;
  if (!c.unmatched.length) return `<p class="brief-check warn">All ${facts} figures match the data.${quotes}${how}${fixed}${own} ${by}</p>`;
  return `<p class="brief-check warn">${c.unmatched.length} of ${facts} figures aren't in the data and can't be worked out from it (marked): <b>${c.unmatched.map(esc).join(", ")}</b>. Check them before relying on them.${quotes}${how}${fixed}${own} ${by}</p>`;
}
// Marks figures inside rendered text: unsupported ones highlighted, worked-out ones underlined with how. A figure is
// matched however the model wrote its separators (a narrow space or a comma between thousands, a typographic minus).
function markFigures(html, c) {
  if (!c || (!c.unmatched?.length && !c.derived?.length && !c.quotes?.length)) return html;
  const pattern = (f) => f.split("").map((ch) => /[,   ]/.test(ch) ? "[,\\u202f\\u00a0\\u2009]" : /[-−‑–]/.test(ch) ? "[-\\u2212\\u2011\\u2013]" : /\s/.test(ch) ? "\\s*" : ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("");
  const marks = [...(c.unmatched || []).map((f) => ({ f, cls: "fig-bad", title: "Not in the data, and can't be worked out from it" })),
                 ...(c.quotes || []).map((f) => ({ f, cls: "fig-bad", title: "Not in the sources word for word" })),
                 ...(c.derived || []).map((d) => ({ f: d.figure, cls: "fig-worked", title: `Worked out: ${d.how}` }))]
    .filter((m) => m.f).sort((a, b) => b.f.length - a.f.length);
  if (!marks.length) return html;
  // Not inside a longer number: "40" mustn't light up in "1,400" or "40.5"
  const re = new RegExp(marks.map((m) => `(?<![\\d.,])(${pattern(m.f)})(?![\\d]|[.,]\\d)`).join("|"), "g");
  const box = document.createElement("div"); box.innerHTML = html;
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT), nodes = [];
  while (walker.nextNode()) if (!walker.currentNode.parentElement.closest("code, pre, mark")) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const s = node.nodeValue; re.lastIndex = 0; if (!re.test(s)) continue;
    const frag = document.createDocumentFragment(); let at = 0; re.lastIndex = 0;
    for (const m of s.matchAll(re)) {
      const m_ = marks[m.slice(1).findIndex((g) => g !== undefined)];
      frag.append(s.slice(at, m.index));
      const el = document.createElement("mark"); el.className = m_.cls; el.title = m_.title; el.textContent = m[0]; frag.append(el);
      at = m.index + m[0].length;
    }
    frag.append(s.slice(at)); node.replaceWith(frag);
  }
  return box.innerHTML;
}
$("#btnWatch").onclick = async () => {
  if (state.stock.watched) { await api(`/api/watchlist/${encodeURIComponent(state.ticker)}`, { method: "DELETE" }); state.stock.watched = false; }
  else { await api("/api/watchlist", { method: "POST", body: { ticker: state.ticker } }); state.stock.watched = true; }
  $("#btnWatch").textContent = state.stock.watched ? "✓ Watching" : "+ Watchlist";
  toast(state.stock.watched ? `${state.ticker} added to watchlist` : `${state.ticker} removed`);
};
$("#btnHold").onclick = () => modal(`<h3>Add ${esc(state.ticker)} holding</h3><form><div class="form-row">
  <label>Shares<input name="shares" type="number" step="any" required></label>
  <label>Cost / share (${esc(state.stock.info.currency)})<input name="cost" type="number" step="any" value="${state.stock.stats.price.toFixed(4)}" required></label>
  <label>Date<input name="bought" type="date"></label></div><div class="form-row"><button class="btn">Add</button><button type="button" class="btn ghost" data-close>Cancel</button></div></form>`,
  async (f) => { await api("/api/holdings", { method: "POST", body: { ticker: state.ticker, shares: f.get("shares"), cost: f.get("cost"), bought: f.get("bought") } }); toast("Holding added"); });
const ALERT_DEFAULTS = { rsi_above: 70, rsi_below: 30, day_move: 5, score_below: 45 };
$("#btnAlert").onclick = () => { modal(`<h3>Alert on ${esc(state.ticker)}</h3><form><div class="form-row">
  <label>Condition<select name="kind">${Object.entries(state.meta.alertKinds).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
  <label>Value<input name="value" type="number" step="any" value="${state.stock.stats.price.toFixed(2)}" required></label></div>
  <div class="form-row"><button class="btn">Create</button><button type="button" class="btn ghost" data-close>Cancel</button></div></form>`,
  async (f) => { await api("/api/alerts", { method: "POST", body: { ticker: state.ticker, kind: f.get("kind"), value: f.get("value") } }); toast("Alert created"); });
  const kind = $("#modalCard select[name='kind']");
  kind.onchange = () => { $("#modalCard input[name='value']").value = ALERT_DEFAULTS[kind.value] ?? state.stock.stats.price.toFixed(2); };
};
$("#btnJournal").onclick = () => { show("journal"); $("#jTicker").value = state.ticker; $("#jPrice").value = state.stock.stats.price.toFixed(2); $("#jThesis").focus(); };

$("#btnPretrade").onclick = async () => {
  const b = $("#btnPretrade"), out = $("#ptOut"); b.disabled = true; loading(out, "Checking against your portfolio…");
  try {
    const r = await api("/api/pretrade", { method: "POST", body: { ticker: state.ticker, amount: $("#ptAmount").value, entry: $("#ptEntry").value, stop: $("#ptStop").value, risk: $("#ptRisk").value } });
    const change = (k) => (r.before ? `${pct(r.before[k], 1, false)} → <b>${pct(r.after[k], 1, false)}</b>` : "withheld");
    const rows = [
      ["Position", `${num(r.shares, r.shares < 1 ? 4 : 2)} shares at ${money(r.price, r.currency)}`], ["Value", money(r.value, r.base, 0)],
      ...(isNum(r.maxLoss) ? [["Loss at the stop", money(r.maxLoss, r.base, 0)]] : []),
      ...(isNum(r.sizedShares) ? [["Size from your risk", `${num(r.sizedShares, r.sizedShares < 1 ? 4 : 2)} shares`]] : []),
      ["Weight of this asset", change("position")], [`Sector: ${esc(r.sector || "Unknown")}`, change("sector")],
      [`Region: ${esc(r.region || "Unknown")}`, change("region")], [`Currency: ${esc(r.currency)}`, change("currencyWeight")],
      ["Portfolio beta", r.before ? `${num(r.before.beta)} → <b>${num(r.after.beta)}</b>` : "withheld"],
    ];
    out.innerHTML = `${r.incomplete ? `<div class="alertbar"><b>Incomplete:</b> no current data for ${esc(r.missing.join(", "))}, so weights and beta are withheld.</div>` : ""}
      <div class="statgrid">${rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("")}</div>
      ${r.newWarnings.length ? `<h4>New warnings from this purchase</h4><ul class="signals">${r.newWarnings.map((w) => `<li class="t-1">${esc(w)}</li>`).join("")}</ul>` : ""}
      ${r.correlations.length ? `<h4>Moves with your holdings (weekly returns)</h4><div class="statgrid">${r.correlations.map((c) => `<div><span>${esc(c.ticker)}</span><span>${num(c.correlation, 2)}</span></div>`).join("")}</div>` : ""}
      <div class="form-row"><button class="btn ghost sm" id="btnPtJournal">Record in journal</button></div>`;
    $("#btnPtJournal").onclick = () => {
      const stop = $("#ptStop").value, entry = $("#ptEntry").value;
      show("journal"); $("#jTicker").value = state.ticker; $("#jPrice").value = entry; $("#jStop").value = stop; $("#jThesis").focus();
    };
  } catch (e) { out.innerHTML = `<div class="muted">${esc(e.message)}</div>`; } finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- compare */
$("#btnCompare").onclick = async () => {
  const b = $("#btnCompare"); b.disabled = true;
  try {
    const d = await api(`/api/compare?tickers=${encodeURIComponent($("#cmpTickers").value)}&period=${$("#cmpPeriod").value}`);
    const ts = Object.keys(d.series);
    if (!ts.length) { toast("No data for those tickers"); return; }
    mkChart("cmpChart", {
      type: "line", data: { labels: d.dates, datasets: ts.map((t, i) => ({ label: t, data: d.series[t], borderColor: PALETTE[i % PALETTE.length], borderWidth: 1.6, pointRadius: 0, tension: 0.1, spanGaps: true })) },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 10 } } } },
    });
    table($("#cmpTable"), [
      { k: "ticker", label: "Ticker", fmt: symCell }, { k: "periodReturn", label: "Period", num: 1, fmt: (v) => cpct(v) },
      { k: "ret5yCagr", label: "5Y CAGR", num: 1, fmt: (v) => cpct(v) }, { k: "vol1y", label: "Vol", num: 1, fmt: (v) => pct(v, 1, false) },
      { k: "maxDD1y", label: "Max DD 1Y", num: 1, fmt: (v) => cpct(v) }, { k: "sharpe1y", label: "Sharpe", num: 1, fmt: (v) => num(v) },
      { k: "pe", label: "P/E", num: 1, fmt: (v) => num(v, 1) }, { k: "divYield", label: "Yield", num: 1, fmt: (v) => pct(v, 2, false) },
    ], d.meta, { stock: (r) => r.ticker });
    const cm = d.correlation;
    const col = (v) => v >= 0 ? `rgba(239,91,91,${v * 0.7})` : `rgba(79,179,191,${-v * 0.7})`;
    $("#cmpCorr").innerHTML = `<table><tr><th></th>${cm.tickers.map((t) => `<th class="num">${esc(t)}</th>`).join("")}</tr>${cm.matrix.map((row, i) => `<tr><th>${esc(cm.tickers[i])}</th>${row.map((v) => `<td class="num" style="background:${col(v)}">${num(v, 2)}</td>`).join("")}</tr>`).join("")}</table>
      <p class="muted sm">High correlation (red) means the assets tend to move together and add little diversification. It uses weekly returns on shared dates, because markets open at different hours and on different days. Short periods have few weeks, so read those with care; "—" means too few.</p>
      ${d.short.length ? `<p class="muted sm">Started later than the rest, so shown from its first day: ${esc(d.short.join(", "))}.</p>` : ""}
      ${d.failed.length ? `<p class="muted sm">No data for: ${esc(d.failed.join(", "))}.</p>` : ""}`;
  } catch (e) { toast(e.message); } finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- screener */
const FILTER_LABELS = { max_pe: ["Max P/E", (v) => v], min_div_yield: ["Min yield", (v) => pct(v, 1, false)], min_rev_growth: ["Min revenue growth", (v) => pct(v, 0, false)],
  max_de: ["Max debt/equity", (v) => v], min_roe: ["Min ROE", (v) => pct(v, 0, false)], min_score: ["Min score", (v) => v], min_momentum: ["Min momentum", (v) => v],
  max_vol: ["Max volatility", (v) => pct(v, 0, false)], sector: ["Sector", (v) => esc(v)], sort: ["Sorted by", (v) => esc(v)], markets: ["Markets", (v) => esc(v.join(", "))],
  countries: ["Countries", (v) => { const g = Object.entries(state.meta.countryGroups || {}).find(([, cs]) => cs.length === v.length && cs.every((c) => v.includes(c)));
    return g ? `${esc(g[0])} (${esc(v.join(", "))})` : esc(v.join(", ")); }] };
const filterChips = (f) => Object.entries(FILTER_LABELS).filter(([k]) => isNum(f[k]) && f[k] !== "").map(([k, [label, show]]) => `<span class="pill">${label}: ${show(f[k])}</span>`).join("");
function fillFilterForm(f) {
  const set = (id, v, scale = 1) => { $(id).value = isNum(v) ? +(v * scale).toFixed(4) : ""; };
  set("#fPe", f.max_pe); set("#fDiv", f.min_div_yield, 100); set("#fGrowth", f.min_rev_growth, 100); set("#fDe", f.max_de);
  set("#fRoe", f.min_roe, 100); set("#fScore", f.min_score); set("#fVol", f.max_vol, 100);
  if (f.sort && $(`#fSort option[value="${f.sort}"]`)) $("#fSort").value = f.sort;
}
function screenerFilters() {
  const v = (id, scale = 1) => ($(id).value === "" ? undefined : Number($(id).value) / scale);
  return { max_pe: v("#fPe"), min_div_yield: v("#fDiv", 100), min_rev_growth: v("#fGrowth", 100), max_de: v("#fDe"), min_roe: v("#fRoe", 100),
           min_score: v("#fScore"), max_vol: v("#fVol", 100), sort: $("#fSort").value, ...($("#fCountry").value ? { countries: [$("#fCountry").value] } : {}) };
}
async function runScreen(body) {
  const el = $("#scrTable"); dlLoader(el, "screener", "Scanning the stocks…");
  $$("#btnScr,#btnScrQuery").forEach((b) => b.disabled = true);
  try {
    const d = await api("/api/screener", { method: "POST", body });
    const f = d.filters;
    if (body.query) fillFilterForm(f);
    const kinds = [f.industry ? `${f.industry} only` : f.sector ? `${f.sector} only` : "", f.exclude_sector ? `Not ${f.exclude_sector}` : "", f.exclude_industry ? `Not ${f.exclude_industry}` : ""].filter(Boolean).map((x) => `<span class="pill">${esc(x)}</span>`).join("");
    $("#scrInfo").innerHTML = `${d.rows.length} matches out of ${d.scanned} scanned. ${kinds}${filterChips(f)}${(d.notes || []).map((n) => `<br>${esc(n)}`).join("")}${f.ignored?.length ? `<br><b>Not understood, so not applied:</b> ${esc(f.ignored.join(", "))}` : ""}${d.failed.length ? ` · No data: ${esc(d.failed.join(", "))}` : ""}${f.sort === "composite" ? "<br>Ranked within each asset class: ETFs and crypto are scored on trend and risk only, so their composite isn't comparable with a stock's." : ""}`;
    table(el, [
      { k: "ticker", label: "Ticker", fmt: symCell }, { k: "country", label: "Country", fmt: (v, r) => `<span class="clip" title="${esc(v || r.region)}">${esc(v || r.region)}</span>` }, { k: "sector", label: "Sector", fmt: (v, r) => `<span class="clip" title="${esc(v || r.assetClass)}">${esc(v || r.assetClass)}</span>` },
      { k: "price", label: "Price", num: 1, fmt: priceOf }, { k: "ret1y", label: "1Y", num: 1, fmt: (v) => cpct(v) },
      { k: "pe", label: "P/E", num: 1, fmt: (v) => num(v, 1) }, { k: "divYield", label: "Yield", num: 1, fmt: (v) => pct(v, 2, false) },
      { k: "revGrowth", label: "Rev g.", num: 1, fmt: (v) => cpct(v) }, { k: "roe", label: "ROE", num: 1, fmt: (v) => pct(v, 0, false) },
      { k: "f_value", label: `Val ${factorInfo("value")}`, num: 1, fmt: (v) => num(v, 0) }, { k: "f_quality", label: `Qual ${factorInfo("quality")}`, num: 1, fmt: (v) => num(v, 0) },
      { k: "f_growth", label: `Grow ${factorInfo("growth")}`, num: 1, fmt: (v) => num(v, 0) }, { k: "f_momentum", label: `Mom ${factorInfo("momentum")}`, num: 1, fmt: (v) => num(v, 0) },
      { k: "f_risk", label: `Risk ${factorInfo("risk")}`, num: 1, fmt: (v) => num(v, 0) },
      { k: "composite", label: `Score ${infoIcon("Below the score: the share of the other stocks in the same sector, from every market in the list, whose overall score this one beats. 50% is the sector's middle; a sector with fewer than 8 others isn't ranked.")}`,
        num: 1, fmt: (v, r) => `<b>${num(v, 0)}</b>${isNum(r.sectorBeats) ? `<span class="sub" title="Better than ${pct(r.sectorBeats, 0, false)} of ${r.sectorOf} other ${esc(r.sector)} stocks">beats ${pct(r.sectorBeats, 0, false)}</span>` : ""}` },
      { k: "stance", label: "Stance", fmt: (v, r) => `${badge(v)}${r.scoredOn !== "All factors" ? `<span class="sub">${esc(r.scoredOn)}</span>` : ""}` },
    ], d.rows, { stock: (r) => r.ticker, empty: "No matches — loosen the filters." });
  } catch (e) { el.innerHTML = `<div class="muted">${esc(e.message)}</div>`; } finally { $$("#btnScr,#btnScrQuery").forEach((b) => b.disabled = false); }
}
const selectedMarkets = () => $$("#scrMarkets input:checked").map((i) => i.value);
$("#btnScr").onclick = () => (selectedMarkets().length ? runScreen({ markets: selectedMarkets(), filters: screenerFilters() }) : toast("Tick at least one market."));
$("#btnScrQuery").onclick = () => $("#scrQuery").value.trim() && runScreen({ markets: selectedMarkets(), query: $("#scrQuery").value });
$("#scrQuery").onkeydown = (e) => { if (e.key === "Enter") $("#btnScrQuery").click(); };

/* ---------------------------------------------------------------- ask */
// A question goes to the AI, which uses the app's read-only tools in a loop and answers. Each answer shows how it was
// worked out (every tool it used) and the check of its figures. Answers stay on the page for this visit only.
const ASK_STEP = {
  get_portfolio: () => "Read your portfolio", get_stock: (a) => `Looked up ${a.ticker}`,
  screen: (a) => `Ran the screener${a.question ? `: "${a.question}"` : a.filters ? ` with ${Object.keys(a.filters).join(", ")}` : ""}`,
  get_opportunities: (a) => `Opened Opportunities${a.markets ? ` (${a.markets.join(", ")})` : ""}`, compare: (a) => `Compared ${(a.tickers || []).join(", ")}`,
  pretrade: (a) => `Pre-trade check: ${a.amount} of ${a.ticker}`, search: (a) => `Searched for "${a.text}"`, get_news: (a) => `Read headlines about ${a.ticker}`,
  get_analysts: (a) => `Read analysts' views on ${a.ticker}`, get_ownership: (a) => `Looked at who owns ${a.ticker}`, get_earnings: (a) => `Checked ${a.ticker}'s earnings`, get_dividends: (a) => `Read ${a.ticker}'s dividend history`,
  get_price_history: (a) => `Looked at ${a.ticker} from ${a.start} to ${a.end || "today"}`, get_fund: (a) => `Looked inside ${a.ticker}`, get_market: () => "Checked the markets",
  web_search: (a) => `Searched the web: "${a.query}"`,
};
// The pages a web search found, to open and check (Groq's search lists none, and says so)
function webLinks(s) {
  if (s.tool !== "web_search" || !s.ok) return "";
  const safe = (s.links || []).filter((l) => /^https?:\/\//.test(l.url || ""));
  if (!safe.length) return ` <span class="muted">(${esc(s.source || "web")}: no list of pages)</span>`;
  return `<ul class="web-links">${safe.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title || l.site)}</a> <span class="muted">${esc(l.site || "")}${l.date ? ` · ${esc(l.date.slice(0, 10))}` : ""}</span></li>`).join("")}</ul>`;
}
function renderAskSetup() {
  const a = state.meta?.aiInfo;
  if (!a?.on) $("#askStatus").textContent = a?.problem ? `AI isn't set up: ${a.problem}` : "Ask needs an AI provider. Set AI_PROVIDER in .env (see .env.example), then restart the app.";
}
async function runAsk() {
  const q = $("#askInput").value.trim(), btn = $("#askGo"), status = $("#askStatus");
  if (!q) return toast("Type a question first.");
  if (!state.meta?.aiInfo?.on) return renderAskSetup();
  btn.disabled = true; status.innerHTML = `<span class="spinner"></span>Investigating… the AI looks things up first, which can take up to a minute.`;
  try {
    const r = await api("/api/ask", { method: "POST", body: { question: q } });
    const steps = r.steps.map((s) => `<li class="${s.ok ? "" : "warn"}">${esc((ASK_STEP[s.tool] || (() => s.tool))(s.args || {}))}${s.ok ? "" : ` <span class="muted">(${esc(s.error || "failed")})</span>`}${webLinks(s)}</li>`).join("");
    const card = document.createElement("div"); card.className = "card ask-answer";
    card.innerHTML = `<div class="ask-q">${esc(q)}</div><div class="brief">${markFigures(DOMPurify.sanitize(marked.parse(r.answer)), r.check)}</div>
      <details class="ask-steps"><summary>How it was worked out: ${r.steps.length} look-up${r.steps.length === 1 ? "" : "s"}, ${r.calls} model call${r.calls === 1 ? "" : "s"}</summary><ol>${steps || "<li>Answered without looking anything up.</li>"}</ol></details>
      ${briefFooter({ ...r, check: { ...r.check, suggested: [] } })}`;
    $("#askAnswers").prepend(card); status.textContent = ""; $("#askInput").value = "";
  } catch (e) { status.textContent = e.message; } finally { btn.disabled = false; }
}
$("#askGo").onclick = runAsk;
$("#askInput").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); runAsk(); } };

/* ---------------------------------------------------------------- opportunities */
// One scoring of the stocks feeds the page and the dashboard card. It's kept for five minutes, so moving between
// them doesn't recalculate; Refresh asks again. The first load of the day downloads every stock, about half a minute.
// Every market of single stocks, from the backend's list; until it has arrived, the three the app began with
const OPP_MARKETS_START = ["North America", "Europe", "Latin America", "Middle East & Africa", "Asia-Pacific"];
const oppAll = () => state.meta?.stockMarkets || OPP_MARKETS_START;
// A choice saved under names no longer offered (the old "US", "Gulf", or a region you stopped following) starts again
// from every region you follow, rather than quietly keeping what's left of it
const oppMarkets = () => { try { const m = JSON.parse(localStorage.getItem("oppMarkets")); return Array.isArray(m) && m.length && m.every((x) => oppAll().includes(x)) ? m : oppAll(); } catch { return oppAll(); } };
async function getOpportunities(force) {
  const key = oppMarkets().join(",");
  if (!force && state.opp?.key === key && Date.now() - state.opp.at < 5 * 60e3) return state.opp.data;
  if (!force && state.oppPending?.key === key) return state.oppPending.promise;  // the page and the card share one request
  const promise = api(`/api/opportunities?markets=${encodeURIComponent(key)}`);
  state.oppPending = { key, promise };
  try { const data = await promise; state.opp = { key, data, at: Date.now() }; return data; } finally { if (state.oppPending?.promise === promise) state.oppPending = null; }
}
const oppName = (r) => `${r.name || r.ticker} (${r.ticker})`;
const oppTip = (r, extra = []) => [oppName(r), `Score ${num(r.composite, 0)} · ${r.stance}`, ...extra, ...(r.reasons || [])];
// The map: value across, momentum up, quality as size, overall score as colour; the "cheap and rising" corner shaded
// The map's full extent: a little room past 0 and 100, so a stock at the edge isn't cut in half
const MAP_LO = -6, MAP_HI = 106, MAP_MIN_SPAN = 6;
function oppMapChart(id, rows, zone, small) {
  // The dashboard's small map shows only the shaded corner (cheap and rising), a little past its edge so a dot on it isn't cut
  if (small) rows = rows.filter((r) => r.f_value >= zone && r.f_momentum >= zone);
  const from = small ? zone - 3 : MAP_LO;
  const shade = { id: "zone", beforeDatasetsDraw(c) {
    const { ctx, chartArea: a, scales: { x, y } } = c;
    const x0 = Math.max(a.left, Math.min(a.right, x.getPixelForValue(zone))), y0 = Math.max(a.top, Math.min(a.bottom, y.getPixelForValue(zone)));  // zoomed, the corner may be partly out of view
    ctx.save(); ctx.fillStyle = tint("--up", "1c"); ctx.fillRect(x0, a.top, a.right - x0, y0 - a.top);
    ctx.restore();
  } };
  // The stocks in the corner are named on the map (zoomed in, every stock in view), best score first, each to the right
  // of its dot or else to the left; a name that would overlap one already placed is left for the hover, so nothing is
  // written over anything
  const names = { id: "names", afterDatasetsDraw(c) {
    const { ctx, chartArea: a, scales: { x, y } } = c, pts = c.getDatasetMeta(0).data, h = small ? 11 : 13, placed = [];
    const zoomedIn = x.max - x.min < 100 || y.max - y.min < 100;
    ctx.save(); ctx.textBaseline = "middle";
    if (!small && x.getPixelForValue(zone) < a.right - 100 && y.getPixelForValue(zone) > a.top + 14) {
      ctx.font = "600 11px IBM Plex Sans"; ctx.fillStyle = C("--up"); ctx.textAlign = "left";
      const tx = Math.max(a.left, x.getPixelForValue(zone)) + 6, ty = a.top + 10, tw = ctx.measureText("Cheap and rising").width;
      ctx.fillText("Cheap and rising", tx, ty); placed.push({ l: tx, r: tx + tw, t: ty - h / 2, b: ty + h / 2 });
    }
    ctx.font = `600 ${small ? 10 : 11}px IBM Plex Sans`; ctx.fillStyle = C("--text");
    const corner = c.data.datasets[0].data.map((d, i) => ({ d, pt: pts[i] })).filter(({ d }) => (zoomedIn ? d.x >= x.min && d.x <= x.max && d.y >= y.min && d.y <= y.max : d.x >= zone && d.y >= zone)).sort((p, q) => q.d.row.composite - p.d.row.composite);
    // The dots themselves are obstacles too, so a name doesn't sit on another stock
    corner.forEach(({ d, pt }) => placed.push({ l: pt.x - d.r, r: pt.x + d.r, t: pt.y - d.r, b: pt.y + d.r, dot: true }));
    corner.forEach(({ d, pt }) => {
      const t = d.row.ticker, label = /^\d/.test(t) ? t : t.replace(/\.[A-Z]+$/, ""), w = ctx.measureText(label).width;
      const own = placed.findIndex((p) => p.dot && p.l === pt.x - d.r && p.t === pt.y - d.r);
      const others = placed.filter((_, i) => i !== own);
      const fits = (b) => b.l >= a.left && b.r <= a.right && b.t >= a.top && b.b <= a.bottom && !others.some((p) => b.l < p.r && b.r > p.l && b.t < p.b && b.b > p.t);
      const right = { l: pt.x + d.r + 3, r: pt.x + d.r + 3 + w, t: pt.y - h / 2, b: pt.y + h / 2 };
      const left = { l: pt.x - d.r - 3 - w, r: pt.x - d.r - 3, t: pt.y - h / 2, b: pt.y + h / 2 };
      const box = fits(right) ? right : fits(left) ? left : null;
      if (!box) return;
      ctx.textAlign = "left"; ctx.fillText(label, box.l, pt.y); placed.push(box);
    });
    ctx.restore();
  } };
  // Axis marks every 20 points, finer as you zoom in
  const marks = (ax) => {
    const span = ax.max - ax.min, step = span > 60 ? 20 : span > 30 ? 10 : span > 12 ? 5 : 2;
    ax.ticks = [];
    for (let v = Math.ceil(Math.max(0, ax.min) / step) * step; v <= Math.min(100, ax.max); v += step) ax.ticks.push({ value: v });
  };
  const chart = mkChart(id, {
    type: "bubble", plugins: [shade, names],
    data: { datasets: [{ data: rows.map((r) => ({ x: r.f_value, y: r.f_momentum, r: (small ? 2.5 : 4) + ((r.f_quality || 0) / 100) * (small ? 5 : 9), row: r })),
      backgroundColor: rows.map((r) => scoreColor(r.composite) + "cc"), borderColor: rows.map((r) => scoreColor(r.composite)), borderWidth: 1, clip: 0 }] },
    options: { maintainAspectRatio: false, animation: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => (small ? oppTip(c.raw.row).slice(0, 3) : oppTip(c.raw.row)) } } },
      // A little room past 0 and 100, so a stock at the edge isn't cut in half
      scales: { x: { min: from, max: MAP_HI, title: { display: true, text: small ? "cheaper →" : "Value score — cheaper →", font: { size: small ? 10 : 12 } },
                     ticks: { display: !small }, afterBuildTicks: marks },
                y: { min: from, max: MAP_HI, title: { display: true, text: small ? "stronger trend ↑" : "Momentum score — stronger trend ↑", font: { size: small ? 10 : 12 } },
                     ticks: { display: !small }, afterBuildTicks: marks } },
      onClick: small ? undefined : (e, els, c) => { if (c.$dragged) { c.$dragged = false; return; } if (els[0]) openStock(c.data.datasets[0].data[els[0].index].row.ticker); },
      onHover: (e, els, c) => { if (!c.$drag) e.native.target.style.cursor = els.length || small ? "pointer" : c.$zoomed?.() ? "grab" : "default"; } },
  });
  if (!small) oppZoom(chart, $("#oppMapReset"));
  return chart;
}
// The big map zooms: Ctrl + wheel in and out around the pointer (a plain wheel scrolls the page, as on Research),
// a drag moves around once zoomed in, and a double-click or "Reset zoom" shows it all again
function oppZoom(chart, reset) {
  // Chart.js builds its options afresh on each update, so they're looked up every time rather than kept
  const cv = chart.canvas, ax = (k) => chart.options.scales[k];
  const zoomed = chart.$zoomed = () => ax("x").min > MAP_LO || ax("x").max < MAP_HI || ax("y").min > MAP_LO || ax("y").max < MAP_HI;
  const place = (k, lo, hi) => {  // keep the window inside the map, at the same size
    const span = Math.min(MAP_HI - MAP_LO, Math.max(MAP_MIN_SPAN, hi - lo));
    lo = Math.min(Math.max(lo, MAP_LO), MAP_HI - span);
    ax(k).min = lo; ax(k).max = lo + span;
  };
  const redraw = () => { chart.update("none"); reset.classList.toggle("hidden", !zoomed()); };
  const all = () => { place("x", MAP_LO, MAP_HI); place("y", MAP_LO, MAP_HI); redraw(); };
  reset.onclick = all;
  cv.ondblclick = all;
  cv.onwheel = (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();  // not the browser's own page zoom
    const f = e.deltaY < 0 ? 0.8 : 1.25, { x, y } = chart.scales;
    const at = { x: x.getValueForPixel(e.offsetX), y: y.getValueForPixel(e.offsetY) };
    for (const [k, sc, v] of [["x", x, at.x], ["y", y, at.y]]) {
      const lo = sc.min, hi = sc.max, span = Math.min(MAP_HI - MAP_LO, Math.max(MAP_MIN_SPAN, (hi - lo) * f));
      const share = (v - lo) / (hi - lo);  // the point under the pointer stays under it
      place(k, v - share * span, v + (1 - share) * span);
    }
    redraw();
  };
  cv.onpointerdown = (e) => {
    chart.$dragged = false;
    if (!zoomed() || e.button !== 0) return;
    const { x, y } = chart.scales;
    chart.$drag = { x: e.clientX, y: e.clientY, sx: [x.min, x.max], sy: [y.min, y.max] };
    cv.setPointerCapture(e.pointerId);
  };
  cv.onpointermove = (e) => {
    const d = chart.$drag;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y, a = chart.chartArea;
    if (!chart.$dragged && Math.hypot(dx, dy) < 4) return;  // a small wobble is still a click
    chart.$dragged = true; cv.style.cursor = "grabbing";
    const vx = dx * (d.sx[1] - d.sx[0]) / (a.right - a.left), vy = dy * (d.sy[1] - d.sy[0]) / (a.bottom - a.top);
    place("x", d.sx[0] - vx, d.sx[1] - vx); place("y", d.sy[0] + vy, d.sy[1] + vy);
    redraw();
  };
  cv.onpointerup = cv.onpointercancel = () => { chart.$drag = null; cv.style.cursor = zoomed() ? "grab" : "default"; };
  reset.classList.add("hidden");
}

// A ranked list as horizontal bars, each opening its stock
function oppBars(id, rows, value, { label, tip, color, max, fmt }) {
  return mkChart(id, {
    type: "bar",
    data: { labels: rows.map(label), datasets: [{ data: rows.map(value), backgroundColor: rows.map(color), borderRadius: 4, row: rows }] },
    options: { indexAxis: "y", maintainAspectRatio: false, animation: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { title: (c) => oppName(rows[c[0].dataIndex]), label: (c) => tip(rows[c.dataIndex]) } } },
      scales: { x: { min: 0, max, ticks: { callback: fmt } }, y: { ticks: { autoSkip: false } } },
      onClick: (e, els) => { if (els[0]) openStock(rows[els[0].index].ticker); },
      onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; } },
  });
}
// An empty list gets a sentence in place of a blank chart
function oppEmpty(wrap, canvasId, message) {
  const w = $(wrap); w.querySelector(".opp-empty")?.remove(); $(`#${canvasId}`).classList.toggle("hidden", !!message);
  if (message) w.insertAdjacentHTML("beforeend", `<div class="opp-empty muted">${esc(message)}</div>`);
}
async function loadOpportunities(force) {
  const status = $("#oppStatus");
  $("#oppMarkets").innerHTML = oppAll().map((m) => `<label><input type="checkbox" value="${m}" ${oppMarkets().includes(m) ? "checked" : ""}> ${m}</label>`).join("");
  $$("#oppMarkets input").forEach((i) => i.onchange = () => {
    const picked = $$("#oppMarkets input:checked").map((x) => x.value);
    if (!picked.length) { i.checked = true; toast("Pick at least one market."); return; }
    try { localStorage.setItem("oppMarkets", JSON.stringify(picked)); } catch { /* kept for this visit only */ }
    loadOpportunities();
  });
  dlLoader(status, "opportunities", "Scoring the stocks…");
  $("#oppRefresh").disabled = true;
  let d;
  try { d = await getOpportunities(force); } catch (e) { status.textContent = e.message; return; } finally { $("#oppRefresh").disabled = false; }
  const R = d.rules, base = d.satellite?.base || state.meta?.settings?.base_currency || "USD";
  status.textContent = `${d.map.length} stocks scored in ${d.markets.join(", ")}${d.failed.length ? `; no data for ${d.failed.join(", ")}` : ""}.`;
  const inZone = d.map.filter((r) => r.f_value >= R.zone && r.f_momentum >= R.zone);
  oppMapChart("oppMap", d.map, R.zone, false);
  $("#oppMapNote").textContent = `${inZone.length} in the shaded corner`;
  $("#oppMapHelp").textContent = `Each dot is a stock. Further right is cheaper (value score), higher has a stronger price trend (momentum score), a bigger dot is better quality, and the colour is its overall score. The shaded corner is ${R.zone} or more on both: cheap and rising. Ctrl + scroll zooms in, and every stock in view gets its name; drag to move around, double-click to see it all.`;
  $("#oppTopNote").textContent = `The ${R.top} highest overall scores, at most ${R.sectorCap} from one sector`;
  oppBars("oppTop", d.top, (r) => r.composite, { label: (r) => r.ticker, tip: (r) => oppTip(r).slice(1), color: (r) => scoreColor(r.composite), max: 100 });
  $("#oppTopHelp").textContent = `The ${R.top} highest overall scores (the five factors, weighted as in Settings), with at most ${R.sectorCap} from one sector so the list isn't one sector's bet. Hover a bar for the strongest reasons (factors at ${R.reasonAt} or more).`;
  const sat = d.satellite;
  $("#oppFitNote").textContent = sat && isNum(sat.core) ? `Satellite is ${pct(1 - sat.core, 0, false)} of your portfolio (your limit: ${pct(1 - sat.target, 0, false)})` : "";
  oppEmpty("#oppFitWrap", "oppFit", !d.holdings.length ? "Add your holdings on the Portfolio page to see which stocks would fit them." : !d.fit.length ? "Not enough shared price history with your portfolio yet: it needs about two months." : "");
  if (d.fit.length) oppBars("oppFit", d.fit, (r) => r.fit, { label: (r) => r.ticker, tip: (r) => [`Fit ${num(r.fit, 0)}: score ${num(r.composite, 0)}, diversifies ${num(r.diversifies, 0)}`, `Moves ${pct(r.corr, 0, false)} in step with your portfolio`, ...(r.reasons || [])], color: () => C("--accent2"), max: 100 });
  $("#oppFitHelp").textContent = d.holdings.length ? `Ranked by the average of a stock's overall score and how little it has moved with what you hold now (100 = independently or against it, 0 = in step), with both in your base currency, over the past ${d.fitWeeks} weeks.${d.fitWeeks < 26 ? " That's a short period, so treat the diversification part as a rough guide." : ""} Stocks you already hold are left out.` : "";
  oppEmpty("#oppSaleWrap", "oppSale", d.onSale.length ? "" : `No stock with a quality score of ${R.saleQuality}+ is ${Math.abs(R.saleDrop * 100)}% or more below its 52-week high with its fall paused right now.`);
  if (d.onSale.length) oppBars("oppSale", d.onSale, (r) => -r.fromHigh * 100, { label: (r) => r.ticker, tip: (r) => [`${pct(r.fromHigh, 0)} from its 52-week high`, `Quality score ${num(r.f_quality, 0)}`, ...(r.reasons || [])], color: () => C("--accent"), fmt: (v) => (v ? `-${v}%` : "0%") });
  $("#oppSaleHelp").textContent = `Quality score ${R.saleQuality} or more, at least ${Math.abs(R.saleDrop * 100)}% below the highest price of the past year, and back above its 50-day average, so the fall has paused (a stock still falling tends to keep falling for a while). Best quality first; the bar is how far it fell. A fall can be a bargain or a warning, so check why it fell.`;
}
$("#oppRefresh").onclick = () => loadOpportunities(true);
// The dashboard's card: the map in small, and the top of the "cheap and rising" corner. Loaded after the rest.
async function loadDashOpp() {
  const list = $("#dashOppList"), wrap = list.parentElement;
  if (!state.opp) { wrap.classList.add("loading"); dlLoader(list, "opportunities", "Scoring the stocks…"); }
  let d;
  try { d = await getOpportunities(false); } catch (e) { wrap.classList.add("loading"); list.innerHTML = `<div class="muted sm">${esc(e.message)}</div>`; return; }  // an error, centred too
  wrap.classList.remove("loading");  // the map comes back beside the list
  oppMapChart("dashOppChart", d.map, d.rules.zone, true);
  const zone = d.map.filter((r) => r.f_value >= d.rules.zone && r.f_momentum >= d.rules.zone).sort((a, b) => b.composite - a.composite).slice(0, 5);
  list.innerHTML = `<div class="muted sm">Cheap and rising, best first</div>` + (zone.length ? zone.map((r) => `<div class="opp-row"><b>${esc(r.ticker)}</b><span class="muted">${esc((r.reasons || [])[0] || "")}</span><b style="color:${scoreColor(r.composite)}">${num(r.composite, 0)}</b></div>`).join("")
    : `<div class="muted sm">No stock is in the corner today.</div>`);
}

/* ---------------------------------------------------------------- portfolio */
async function loadPortfolio() {
  const t = $("#pfTable"); loading(t);
  let p;
  try { p = await api("/api/portfolio"); } catch (e) { t.innerHTML = esc(e.message); return; }
  const b = p.base;
  $("#pfIncomplete").classList.toggle("hidden", !p.incomplete);
  if (p.incomplete) $("#pfIncomplete").innerHTML = missingNote(p);
  $("#pfKpis").innerHTML = [
    ["Total value", money(p.total, b, 0)], ["Invested", money(p.cost, b, 0)], ["Unrealised P&L", `<span class="${cls(p.pnl)}">${money(p.pnl, b, 0)}</span>`, pct(p.pnlPct)],
    [dayChange(p)[0], `<span class="${cls(p.dayPnl)}">${money(p.dayPnl, b, 0)}</span>`, esc(dayChange(p)[1])], ["Realised P&L", `<span class="${cls(p.realised)}">${money(p.realised, b, 0)}</span>`], ["Dividends received", money(p.dividends, b, 0)],
    // Under a year, the return so far (not annualised); from a year, the annual return with timing
    isNum(p.xirr) ? ["Annual return", cpct(p.xirr), "Your money's return a year, with timing"]
      : /^Shown after a year/.test(p.xirrWhy || "") && isNum(p.returnSoFar) ? ["Return so far", cpct(p.returnSoFar), "Not annualised: that starts after a year of history"]
      : ["Annual return", "—", esc(p.xirrWhy || "")],
    ["Est. dividend income / yr", money(p.annualIncome, b, 0), "What your holdings paid per share in the past year"], ["Positions", p.positions.length],
  ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("");
  // Nothing bought or sold yet: no row of zeros that looks like a result
  if (!p.positions.length && !p.closed.length && !p.missing.length) $("#pfKpis").innerHTML = `<div class="muted">No transactions yet. Add a purchase above, or paste your broker's export.</div>`;
  table(t, [
    { k: "ticker", label: "Ticker", fmt: symCell }, { k: "shares", label: "Shares", num: 1, fmt: (v) => num(v, v < 1 ? 4 : 2) },
    { k: "avgCost", label: "Avg cost", num: 1, fmt: (v, r) => money(v, r.currency) }, { k: "price", label: "Price", num: 1, fmt: (v, r) => money(v, r.currency) },
    { k: "dayPct", label: "1D", num: 1, fmt: (v) => cpct(v, 2) }, { k: "value", label: `Value (${esc(b)})`, num: 1, fmt: (v) => money(v, b, 0) },
    { k: "pnl", label: "P&L", num: 1, fmt: (v) => `<span class="${cls(v)}">${money(v, b, 0)}</span>` }, { k: "pnlPct", label: "P&L %", num: 1, fmt: (v) => cpct(v) },
    { k: "weight", label: "Weight", num: 1, fmt: (v) => pct(v, 1, false) },
    { k: "bucket", label: "Bucket", fmt: (v, r) => `<button class="btn ghost bucket ${v === "core" ? "core" : "satellite"}" data-bucket="${esc(r.ticker)}" title="${v === r.bucketDefault ? "The default for this holding." : "Chosen by you."} Click to make it ${v === "core" ? "satellite" : "core"}.">${v === "core" ? "Core" : "Satellite"}</button>` },
    { k: "ticker", label: "", fmt: (v) => `<button class="btn danger" data-lots="${esc(v)}">Lots</button>` },
  ], p.positions, { stock: (r) => r.ticker, empty: "No holdings yet — add one above." })
  .afterDraw(() => {
    $$("[data-bucket]", t).forEach((btn) => btn.onclick = async () => {
      const pos = p.positions.find((x) => x.ticker === btn.dataset.bucket);
      btn.disabled = true;
      try { await api("/api/buckets", { method: "POST", body: { ticker: pos.ticker, bucket: pos.bucket === "core" ? "satellite" : "core" } }); loadPortfolio(); }
      catch (e) { btn.disabled = false; toast(e.message); }
    });
    $$("[data-lots]", t).forEach((btn) => btn.onclick = () => {
    const pos = p.positions.find((x) => x.ticker === btn.dataset.lots);
    modal(`<h3>${esc(pos.ticker)} lots</h3><table><tr><th>Date</th><th class="num">Shares</th><th class="num">Cost</th><th>Note</th><th></th></tr>${pos.lots.map((l) => `<tr><td>${esc(l.bought || "—")}${l.split && l.split !== 1 ? `<div class="muted sm">Restated for splits since: ×${num(l.split, l.split < 1 ? 4 : 0)} shares</div>` : ""}</td><td class="num">${num(l.shares, 4)}</td><td class="num">${money(l.cost, pos.currency)}</td><td>${esc(l.note)}</td><td><button class="btn danger" data-del="${l.id}">Delete</button></td></tr>`).join("")}</table><div class="modal-actions"><button class="btn danger" data-delall title="Every transaction of this holding: purchases, sales and dividends">Delete all</button><button class="btn ghost" data-close>Close</button></div>`);
    $$("[data-del]", $("#modal")).forEach((d) => d.onclick = async () => { if (!confirm("Delete this lot?")) return; await api(`/api/holdings/${d.dataset.del}`, { method: "DELETE" }); $("#modal").classList.add("hidden"); loadPortfolio(); });
    // The whole holding, sales and dividends included, so nothing is left without the purchase it came from
    $("[data-delall]", $("#modal")).onclick = async () => {
      try {
        const mine = (await api("/api/transactions")).filter((x) => x.ticker === pos.ticker);
        const count = (k, word) => { const n = mine.filter((x) => (x.kind || "buy") === k).length; return n ? [`${n} ${word}${n > 1 ? "s" : ""}`] : []; };
        const parts = [...count("buy", "purchase"), ...count("sell", "sale"), ...count("dividend", "dividend")];
        if (!confirm(`Delete all ${mine.length} transactions of ${pos.ticker} (${parts.join(", ")})? This can't be undone.`)) return;
        const r = await api(`/api/transactions/ticker/${encodeURIComponent(pos.ticker)}`, { method: "DELETE" });
        $("#modal").classList.add("hidden"); toast(`Deleted ${r.deleted} transactions of ${pos.ticker}.`); loadPortfolio();
      } catch (e) { toast(e.message); }
    };
    });
  });
  renderCoreSatellite(p);
  if (p.errors?.length) toast(p.errors.join(" · "));
  const tx = await api("/api/transactions");
  const curOf = Object.fromEntries(p.positions.map((x) => [x.ticker, x.currency]));
  table($("#txTable"), [
    { k: "date", label: "Date", fmt: (v) => esc(v || "no date") }, { k: "ticker", label: "Ticker", fmt: (v) => `<span class="sym">${esc(v)}</span>` }, { k: "kind", label: "Type", fmt: (v) => esc(v) },
    { k: "shares", label: "Shares", num: 1, fmt: (v) => (isNum(v) ? num(v, v < 1 ? 4 : 2) : "") },
    { k: "price", label: "Price or amount", num: 1, fmt: (v, r) => (r.kind === "dividend" ? money(r.amount, MAIN_UNIT[curOf[r.ticker]] || (curOf[r.ticker] ?? null)) : money(v, curOf[r.ticker] ?? null)) }, { k: "note", label: "Note", fmt: (v) => esc(v || "") },
    { k: "id", label: "", fmt: (v) => `<button class="btn danger" data-tdel="${v}">Delete</button>` },
  ], tx, { empty: "Nothing recorded yet." })
  .afterDraw(() => $$("[data-tdel]", $("#txTable")).forEach((d) => d.onclick = async () => {
    if (!confirm("Delete this transaction?")) return;
    try { await api(`/api/transactions/${d.dataset.tdel}`, { method: "DELETE" }); loadPortfolio(); } catch (e) { toast(e.message); }
  }));
  $("#pfClosed").innerHTML = p.closed.length ? `<h4>Closed positions</h4><div class="statgrid">${p.closed.map((c) => `<div><span>${esc(c.ticker)}</span><span class="${cls(c.realised + c.dividends)}">${money(c.realised + c.dividends, b, 0)}</span></div>`).join("")}</div>` : "";
  $("#pfNote").textContent = p.note;
  if (!p.performance) { state.charts.pfPerf?.destroy(); delete state.charts.pfPerf; }
  if (p.performance) {
    const ds = [{ label: "Portfolio", data: p.performance.portfolio, borderColor: C("--accent"), borderWidth: 2, pointRadius: 0 }];
    if (p.performance.benchmark) ds.push({ label: p.performance.benchmarkName, data: p.performance.benchmark, borderColor: C("--muted"), borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0 });
    mkChart("pfPerf", { type: "line", data: { labels: p.performance.dates, datasets: ds }, options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 8 } } } } });
  }
  const r = p.risk;
  $("#pfRisk").innerHTML = [["Your return", cpct(r.return1y)], ["Benchmark", cpct(r.benchmarkReturn)], ["Volatility", pct(r.volatility, 1, false)],
    ["Max drawdown", cpct(r.maxDrawdown)], ["Sharpe", num(r.sharpe)], ["Sortino", num(r.sortino)], ["Beta", num(r.beta)]]
    .map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");
  $("#pfWarn").innerHTML = p.warnings.map((w) => `<li class="t-1">${esc(w)}</li>`).join("") || (p.positions.length ? `<li class="t1">No concentration warnings.</li>` : "");
  const pie = (id, obj) => mkChart(id, { type: "doughnut", data: { labels: Object.keys(obj), datasets: [{ data: Object.values(obj), backgroundColor: PALETTE, borderColor: C("--panel"), borderWidth: 2 }] },
    options: { cutout: "62%", plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: (c) => `${c.label}: ${pct(c.raw / p.total, 1, false)}` } } } } });
  pie("aAsset", p.allocation.assetClass); pie("aRegion", p.allocation.region); pie("aSector", p.allocation.sectorLookThrough); pie("aCur", p.allocation.currencyLookThrough);
  // Which funds were opened up, and what the chart would say by trading currency alone
  const opened = p.allocation.currencyOpened || [];
  $("#aCurNote").textContent = opened.length ? `${opened.join(", ")} counted by the currencies of the companies ${opened.length > 1 ? "they hold" : "it holds"} (approximate index weights). By the currency ${opened.length > 1 ? "they trade" : "it trades"} in: ${Object.entries(p.allocation.currency).map(([k, v]) => `${k} ${pct(v / p.total, 0, false)}`).join(", ")}.` : "";
  table($("#pfRebal"), [
    { k: "assetClass", label: "Asset class" }, { k: "target", label: "Target", num: 1, fmt: (v) => pct(v, 1, false) }, { k: "actual", label: "Actual", num: 1, fmt: (v) => pct(v, 1, false) },
    // Plain, not green and red: over target isn't a gain and under it isn't a loss (the suggestion carries the colour)
    { k: "drift", label: "Drift", num: 1, fmt: (v) => pct(v) }, { k: "action", label: "Status", fmt: (v) => `<b class="${v === "Add" ? "up" : "muted"}">${v === "Add" ? "Under target" : v}</b>` },
    { k: "newMoney", label: `New money (${esc(b)})`, num: 1, fmt: (v) => (isNum(v) && v >= 0.5 ? money(v, b, 0) : "—") },
    { k: "trade", label: `Or by selling (${esc(b)})`, num: 1, fmt: (v, row) => row.action === "Hold" ? "—" : `${v > 0 ? "buy" : "sell"} ${money(Math.abs(v), b, 0)}` },
  ], p.rebalance, { empty: p.incomplete ? "Withheld until every holding has data." : "Add holdings to see rebalancing suggestions." });
  // New money first: rebalancing by what you add costs nothing; selling has fees and may be taxed
  const nm = p.rebalanceNewMoney;
  $("#pfRebalNote").textContent = !p.rebalance.length ? "" : nm === null ? "Something you hold has a 0% target, so only selling would reach the targets. Selling has costs and may be taxed."
    : nm < 0.5 ? "On target: nothing to do." : `Without selling anything: about ${money(nm, b, 0)} of new money, split as in the New money column, reaches every target. Selling instead has costs and may be taxed.`;
}
function renderCoreSatellite(p) {
  const cs = p.coreSatellite, b = p.base;
  $("#csCard").classList.toggle("hidden", !cs);
  if (!cs) return;
  const at = (v) => Math.min(Math.max(v * 100, 0), 100);
  const target = `${Math.round(cs.target * 100)}/${Math.round((1 - cs.target) * 100)}`;
  const advice = cs.action === undefined ? "No suggestion until every holding has data."
    : cs.action === "Hold" ? `Within 2 points of your ${target} target.`
    : cs.action === "Above core target" ? `More in core than your ${target} target: the lower-risk side, so nothing needs selling. If you want satellite holdings, about ${money(cs.newMoney, b, 0)} of new money there would reach ${target}.`
    : `Satellite is above its share: the riskier side. About ${money(cs.newMoney, b, 0)} of new money into core would bring it back to ${target} without selling, or moving about ${money(cs.amount, b, 0)} from satellite to core (selling has costs and may be taxed).`;
  const r = cs.riskShare, share = (v) => pct(Math.abs(v ?? 0) < 0.005 ? 0 : v, 0, false); // no "-0%"
  const risk = !r ? "Risk shares need two or more holdings with a few weeks of prices."
    : `Satellite is ${pct(cs.satellite, 0, false)} of the money and ${share(r.satellite)} of the portfolio's day-to-day swings; core ${pct(cs.core, 0, false)} and ${share(r.core)}. (Past year, today's holdings.)`;
  const pf = cs.performance;
  const judge = !pf ? "Hold both core and satellite for a while to compare how they did."
    : `${pf.pastYearOnly ? `Over the past year (your purchases have no dates), from ${esc(pf.start)}` : `Since ${esc(pf.start)}, while you held both`}: core ${cpct(pf.core)}, satellite ${cpct(pf.satellite)}. `
      + `Your satellites ${pf.edge >= 0 ? "are ahead of" : "trail"} the core by ${num(Math.abs(pf.edge) * 100, 1)} points`
      + (pf.days < 60 ? `, but ${pf.days} trading days is too short to judge.` : ".");
  $("#csSplit").innerHTML = `
    <div class="split"><i style="width:${at(cs.core)}%"></i><i style="width:${100 - at(cs.core)}%"></i><b style="left:calc(${at(cs.target)}% - 1px)" data-label="target ${target}"></b></div>
    <div class="split-legend"><span class="cs-core">Core ${pct(cs.core, 1, false)}</span><span class="cs-sat">Satellite ${pct(cs.satellite, 1, false)}</span></div>
    <div class="cs-lines"><p>${advice}</p><p>${risk}</p><p>${judge}</p></div>`;
  $("#csPerfNote").textContent = pf ? "Each bucket's time-weighted return from the first day both were held, at 100. Lots without a date count from your first dated trade; closed positions count in the bucket they're in now." : "";
  if (!pf) { state.charts.csPerf?.destroy(); delete state.charts.csPerf; $("#csPerf").classList.add("hidden"); return; }
  $("#csPerf").classList.remove("hidden");
  const line = (label, data, color) => ({ label, data, borderColor: color, borderWidth: 2, pointRadius: 0, spanGaps: false });
  mkChart("csPerf", { type: "line", data: { labels: pf.dates, datasets: [line("Core", pf.coreLine, C("--accent2")), line("Satellite", pf.satelliteLine, C("--accent"))] },
    options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 6 } } } } });
}
// Stocks quoted in a fraction of a currency: prices are in that fraction, dividends in the main currency
const MAIN_UNIT = { GBp: "GBP", GBX: "GBP", ZAc: "ZAR", ILA: "ILS", KWF: "KWD" };
let formQuote = null;
$("#hTicker").onchange = async () => {
  const hint = $("#hHint"), t = $("#hTicker").value.trim();
  formQuote = null; hint.textContent = "";
  if (!t) return;
  const q = await api(`/api/quote/${encodeURIComponent(t)}`).catch(() => null);
  if (!q || $("#hTicker").value.trim() !== t) return;
  formQuote = q;
  const unit = { GBp: " (pence, 1/100 of a pound)", GBX: " (pence, 1/100 of a pound)", ZAc: " (cents, 1/100 of a rand)", ILA: " (agorot, 1/100 of a shekel)", KWF: " (fils, 1/1000 of a dinar)" }[q.currency] || "";
  const pay = MAIN_UNIT[q.currency];
  hint.textContent = `${q.ticker} trades in ${q.currency}${unit}. Latest price ${num(q.price)}.` + (pay ? ` Enter prices in ${q.currency} and dividends in ${pay}, as your broker pays them.` : "");
  $("#hKind").onchange(); // the dividend label names the currency too
};
$("#btnPaste").onclick = async () => {
  try {
    const r = await api("/api/transactions/import", { method: "POST", body: { text: $("#hPaste").value } });
    $("#hPaste").value = ""; toast(`Added ${r.added} transaction${r.added === 1 ? "" : "s"}`); loadPortfolio();
  } catch (e) { toast(e.message); }
};
$("#hKind").onchange = () => {
  const dividend = $("#hKind").value === "dividend";
  $("#hSharesWrap").classList.toggle("hidden", dividend);
  const pay = formQuote && MAIN_UNIT[formQuote.currency];
  $("#hCostLabel").textContent = dividend ? `Total received${pay ? ` (${pay})` : ""}` : "Price / share";
};
$("#btnAddHolding").onclick = async () => {
  const kind = $("#hKind").value, value = $("#hCost").value;
  const figures = kind === "dividend" ? { amount: value } : { shares: $("#hShares").value, price: value };
  // A price far from the market is often the wrong unit, such as pounds for a stock quoted in pence
  if (kind !== "dividend" && formQuote && value && (Number(value) > formQuote.price * 5 || Number(value) < formQuote.price / 5)
      && !confirm(`${value} is far from the latest price of ${num(formQuote.price)} ${formQuote.currency}. Save it anyway?`)) return;
  try {
    await api("/api/transactions", { method: "POST", body: { ticker: $("#hTicker").value, kind, date: $("#hDate").value, note: $("#hNote").value, ...figures } });
    ["#hTicker", "#hShares", "#hCost", "#hDate", "#hNote"].forEach((id) => $(id).value = ""); $("#hHint").textContent = ""; formQuote = null; toast("Transaction recorded"); loadPortfolio();
  } catch (e) { toast(e.message); }
};

/* ---------------------------------------------------------------- backtest */
function renderParams() {
  const s = state.meta.strategies[$("#bStrat").value];
  $("#bParams").innerHTML = Object.entries(s.params).map(([k, v]) => `<label>${k.replace("_", " ")}<input type="number" data-param="${k}" value="${v}"></label>`).join("");
}
$("#btnBt").onclick = async () => {
  const b = $("#btnBt"); b.disabled = true;
  const params = Object.fromEntries($$("[data-param]").map((i) => [i.dataset.param, Number(i.value)]));
  try {
    const d = await api("/api/backtest", { method: "POST", body: { ticker: $("#bTicker").value, strategy: $("#bStrat").value, params, period: $("#bPeriod").value, cost_bps: $("#bCost").value } });
    mkChart("btChart", { type: "line", data: { labels: d.dates, datasets: [
      { label: d.label, data: d.equity, borderColor: C("--accent"), borderWidth: 2, pointRadius: 0 },
      { label: "Buy & hold", data: d.buyHold, borderColor: C("--muted"), borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0 }] },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 10 } } } } });
    const m = [["Total return", "totalReturn", (v) => cpct(v)], ["CAGR", "cagr", (v) => cpct(v)], ["Volatility", "volatility", (v) => pct(v, 1, false)],
      ["Sharpe", "sharpe", (v) => num(v)], ["Max drawdown", "maxDrawdown", (v) => cpct(v)], ["Trades", "trades", (v) => num(v, 0)], ["Time in market", "exposure", (v) => pct(v, 0, false)]];
    $("#btTable").innerHTML = `<table><tr><th>Metric</th><th class="num">${esc(d.label)}</th><th class="num">Buy &amp; hold</th></tr>${m.map(([l, k, f]) => `<tr><td>${l}</td><td class="num">${f(d.strategy[k])}</td><td class="num">${k === "exposure" ? "100%" : f(d.benchmark[k])}</td></tr>`).join("")}</table>`;
    $("#btCaveats").innerHTML = d.caveats.map((c) => `<li class="t-1">${esc(c)}</li>`).join("");
  } catch (e) { toast(e.message); } finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- portfolio backtest and futures */
// One mix feeds both: how it did (a day-by-day backtest) and what it might do (paths drawn from its own months).
const PM = { loaded: false, base: "USD" };
const BT_HINT = { single: "One asset, one trading rule.", portfolio: "A mix of assets: how it did, and a range of what it might do." };
function setBtMode(mode, load = true) {
  $$("#btMode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("#btSingle").classList.toggle("hidden", mode !== "single");
  $("#btPortfolio").classList.toggle("hidden", mode !== "portfolio");
  $("#btModeHint").textContent = BT_HINT[mode];
  try { localStorage.setItem("btMode", mode); } catch { /* not remembered, but still shown */ }
  if (mode === "portfolio" && load) pmLoad().catch((e) => toast(e.message));
}
$$("#btMode button").forEach((b) => b.onclick = () => setBtMode(b.dataset.mode));
function btMode() { try { return localStorage.getItem("btMode") === "portfolio" ? "portfolio" : "single"; } catch { return "single"; } }
setBtMode(btMode(), false);
function loadBacktestView() { if (btMode() === "portfolio") pmLoad().catch((e) => toast(e.message)); }

function pmRow(ticker = "", weight = "") {
  const row = document.createElement("div");
  row.className = "form-row inline pm-row";
  row.innerHTML = `<label>Asset<input class="pm-t" value="${esc(ticker)}" placeholder="e.g. VWCE.DE" autocomplete="off"></label>
    <label>Weight (%)<input class="pm-w" type="number" min="0" max="100" step="0.1" value="${esc(String(weight))}"></label>
    <button class="btn ghost sm pm-del" title="Remove this asset" aria-label="Remove">✕</button>`;
  row.querySelector(".pm-del").onclick = () => { row.remove(); pmTotal(); };
  row.querySelectorAll("input").forEach((i) => { i.oninput = () => pmTotal(); });
  $("#pmRows").append(row);
}
function pmMix() {
  return $$("#pmRows .pm-row").map((r) => ({ ticker: r.querySelector(".pm-t").value.trim().toUpperCase(), weight: Number(r.querySelector(".pm-w").value || 0) }))
    .filter((m) => m.ticker);
}
function pmTotal() {
  const t = pmMix().reduce((s, m) => s + m.weight, 0), good = Math.abs(t - 100) <= 0.05;
  $("#pmTotal").innerHTML = `Weights add up to <b class="${good ? "up" : "down"}">${t.toFixed(2)}%</b>${good ? "" : " (they must make 100%)"}`;
  return good;
}
async function pmLoad(fromHoldings = false) {
  if (PM.loaded && !fromHoldings) return;
  const d = await api("/api/simulate/mix");
  PM.base = d.base;
  if (!PM.loaded) {
    $("#pmRebal").innerHTML = Object.entries(d.rebalance).map(([k, v]) => `<option value="${k}" ${k === "yearly" ? "selected" : ""}>${esc(v)}</option>`).join("");
    [["#pmStartLabel", "Start amount"], ["#pmMonthlyLabel", "Add each month"], ["#mcStartLabel", "Start amount"], ["#mcMonthlyLabel", "Add each month"], ["#mcGoalLabel", "Goal, in today's money"]]
      .forEach(([id, text]) => { $(id).firstChild.textContent = `${text} (${d.base})`; });
  }
  PM.loaded = true;
  $("#pmRows").innerHTML = "";
  const mix = d.mix.length ? d.mix : [{ ticker: "VWCE.DE", weight: 80 }, { ticker: "SPY", weight: 20 }];
  mix.forEach((m) => pmRow(m.ticker, m.weight));
  pmTotal();
  if (fromHoldings && !d.mix.length) toast("You hold nothing yet, so an example mix is shown.");
}
$("#pmMine").onclick = () => pmLoad(true).catch((e) => toast(e.message));
$("#pmAdd").onclick = () => { pmRow(); pmTotal(); };
function pmBody() {
  if (!pmTotal()) throw new Error("The weights must add up to 100%.");
  return { mix: pmMix(), rebalance: $("#pmRebal").value, cost_bps: Number($("#pmCost").value || 0), stand_ins: $("#pmStand").checked };
}
const pmMoney = (v) => money(v, PM.base, 0);

$("#pmRunPast").onclick = async () => {
  const b = $("#pmRunPast"); b.disabled = true;
  try {
    const d = await api("/api/simulate/backtest", { method: "POST", body: { ...pmBody(), period: $("#pmPeriod").value, start_value: Number($("#pmStart").value || 0), monthly: Number($("#pmMonthly").value || 0) } });
    const p = d.portfolio, bm = d.benchmark, adding = Number($("#pmMonthly").value || 0) > 0;
    $("#pmPastOut").classList.remove("hidden");
    $("#pmKpis").innerHTML = [
      ["Yearly return", cpct(p.cagr), `${d.start} to ${d.end}`],
      ["Worst fall", cpct(p.maxDrawdown), `from a high to a later low`],
      ["Longest below a high", `${Math.round(p.longestUnderwaterDays / 30.4)} months`, p.underwaterNow ? "still below it now" : "before a new high"],
      ["Final value", pmMoney(p.finalValue), `${pmMoney(p.invested)} put in`],
    ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s muted">${esc(s)}</div></div>`).join("");
    mkChart("pmPastChart", { type: "line", data: { labels: d.dates, datasets: [
      { label: "The mix (growth of 100)", data: d.growth, borderColor: C("--accent"), borderWidth: 2, pointRadius: 0 },
      ...(bm ? [{ label: `${bm.ticker} held throughout`, data: bm.growth, borderColor: C("--muted"), borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0 }] : [])] },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 10 } } } } });
    const rows = [["Yearly return", "cagr", (v) => cpct(v)], ["Total return", "totalReturn", (v) => cpct(v)], ["Volatility", "volatility", (v) => pct(v, 1, false)],
      ["Worst fall", "maxDrawdown", (v) => cpct(v)], ["Sharpe", "sharpe", (v) => num(v)], ["Best full year", "bestYear", (v) => v ? `${cpct(v.return)} <span class="muted">${v.year}</span>` : "—"],
      ["Worst full year", "worstYear", (v) => v ? `${cpct(v.return)} <span class="muted">${v.year}</span>` : "—"],
      ...(adding ? [["Return on the money added", "moneyWeightedReturn", (v) => cpct(v)]] : [])];
    $("#pmPastTable").innerHTML = `<table><tr><th>Measure</th><th class="num">The mix</th>${bm ? `<th class="num">${esc(bm.ticker)}</th>` : ""}</tr>
      ${rows.map(([l, k, f]) => `<tr><td>${l}</td><td class="num">${f(p[k])}</td>${bm ? `<td class="num">${f(bm[k])}</td>` : ""}</tr>`).join("")}
      <tr><td>Rebalanced</td><td class="num">${Object.keys(d.weights).length > 1 ? `${esc(d.rebalance)} (${p.rebalances} times)` : "not needed: one asset"}</td>${bm ? `<td class="num">never</td>` : ""}</tr></table>`;
    const bmYear = Object.fromEntries((bm?.yearly || []).map((y) => [y.year, y.return]));
    $("#pmYears").innerHTML = `<table><tr><th>Year</th><th class="num">The mix</th>${bm ? `<th class="num">${esc(bm.ticker)}</th>` : ""}</tr>
      ${p.yearly.map((y) => `<tr><td>${y.year}${y.partial ? ' <span class="muted sm">(part)</span>' : ""}</td><td class="num">${cpct(y.return)}</td>${bm ? `<td class="num">${cpct(bmYear[y.year])}</td>` : ""}</tr>`).join("")}</table>`;
    $("#pmPastCaveats").innerHTML = d.caveats.map((c) => `<li class="t-1">${esc(c)}</li>`).join("");
  } catch (e) { toast(e.message); } finally { b.disabled = false; }
};

$("#mcRun").onclick = async () => {
  const b = $("#mcRun"); b.disabled = true;
  const opt = (id, scale = 1) => ($(id).value === "" ? null : Number($(id).value) * scale);
  try {
    const d = await api("/api/simulate/future", { method: "POST", body: { ...pmBody(), period: "max", years: Number($("#mcYears").value || 10),
      start_value: Number($("#mcStart").value || 0), monthly: Number($("#mcMonthly").value || 0), expected: opt("#mcExpected", 0.01),
      inflation: (opt("#mcInfl", 0.01) || 0), goal: opt("#mcGoal") } });
    const a = d.assumption, f = d.final, real = !!d.real, show = real ? d.real : d.nominal, word = real ? "in today's money" : "";
    $("#mcOut").classList.remove("hidden");
    $("#mcKpis").innerHTML = [
      ["Middle outcome", pmMoney(real ? f.medianReal : f.median), `after ${d.years[d.years.length - 1]} years ${word}`.trim()],
      ["Bad 1 in 10", pmMoney(show["10"].at(-1)), "or worse"],
      ["Good 1 in 10", pmMoney(show["90"].at(-1)), "or better"],
      ["Put in", pmMoney(d.invested.at(-1)), `ends below it in ${pct(f.belowInvested, 1, false)} of futures`],
      ...(d.goal ? [["Reaches the goal", pct(f.reachGoal, 0, false), `of futures reach ${pmMoney(d.goal)}`]] : []),
      ["A fall of 30%+ on the way", pct(d.falls.worseThan30, 0, false), `of futures; the middle one's worst fall ${pct(d.falls.median, 0)}`],
    ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s muted">${esc(s)}</div></div>`).join("");
    const band = (k) => show[k], none = { borderWidth: 0, pointRadius: 0 };
    mkChart("mcChart", { type: "line", data: { labels: d.years.map((y) => `${y}y`), datasets: [
      { label: "10th percentile", data: band("10"), ...none, fill: false },
      { label: "10th to 90th", data: band("90"), ...none, fill: 0, backgroundColor: tint("--accent", "1f") },
      { label: "25th percentile", data: band("25"), ...none, fill: false },
      { label: "25th to 75th", data: band("75"), ...none, fill: 2, backgroundColor: tint("--accent", "3d") },
      { label: "Middle (50th)", data: band("50"), borderColor: C("--accent"), borderWidth: 2, pointRadius: 0 },
      { label: "Put in", data: d.invested.map((v, i) => real ? v / (1 + a.inflation) ** d.years[i] : v), borderColor: C("--muted"), borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0 }] },
      options: { interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { filter: (item) => !/percentile$/.test(item.text) } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${pmMoney(c.parsed.y)}` } } },
        scales: { y: { ticks: { callback: (v) => pmMoney(v) } } } } });
    $("#mcLegend").textContent = `Shaded: the middle half of futures (25th to 75th percentile) and 8 in 10 (10th to 90th). Assumes ${pct(a.usedYearly, 1)} a year`
      + `${a.expected == null ? ` as in its history` : ` (its history: ${pct(a.historicalYearly, 1)})`}, with history's swings (volatility ${pct(a.historicalVolatility, 1, false)}, worst month ${pct(a.worstMonth, 1)}).`
      + ` ${a.paths.toLocaleString()} futures${real ? `, ${pct(a.inflation, 1, false)} inflation taken out` : ""}.`;
    $("#mcCaveats").innerHTML = d.caveats.map((c) => `<li class="t-1">${esc(c)}</li>`).join("");
  } catch (e) { toast(e.message); } finally { b.disabled = false; }
};

/* ---------------------------------------------------------------- journal & alerts */
function renderScorecard(card) {
  const s = card.summary;
  if (!s.counted) { $("#jScore").innerHTML = `<div class="muted">Record a buy or add decision and check back after a day or more to see how it compares with the benchmark.</div>`; return; }
  const kpis = [["Decisions judged", s.counted], [`Beat ${esc(s.benchmarks.join(", "))}`, pct(s.hitRate, 0, false)], ["Average return", cpct(s.avgReturn)], ["Average vs benchmark", cpct(s.avgExcess)]];
  $("#jScore").innerHTML = `<div class="kpis">${kpis.map(([l, v]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("")}</div>
    ${s.counted < 20 ? `<p class="muted sm">Only ${s.counted} decision${s.counted === 1 ? "" : "s"} so far. That's too few to conclude anything yet.</p>` : ""}
    <div class="tablewrap" id="jBands"></div>`;
  table($("#jBands"), [
    { k: "stance", label: "Stance when recorded", fmt: badge }, { k: "n", label: "Decisions", num: 1 }, { k: "hitRate", label: "Beat benchmark", num: 1, fmt: (v) => pct(v, 0, false) },
    { k: "avgReturn", label: "Avg return", num: 1, fmt: (v) => cpct(v) }, { k: "avgExcess", label: "Avg vs benchmark", num: 1, fmt: (v) => cpct(v) },
  ], s.byStance);
}
function entryResult(r) {
  if (!r) return "";
  if (r.error) return `<span class="pill warn" title="${esc(r.error)}">no data</span>`;
  const vs = isNum(r.excess) ? ` · <span class="${cls(r.excess)}">${pct(r.excess)} vs ${esc(r.benchmark)}</span>` : "";
  const flag = r.status === "Stop hit" || r.status === "Target reached" ? `<span class="pill warn">${esc(r.status)}</span>` : "";
  return `<span class="pill">since entry <span class="${cls(r.returnSince)}">${pct(r.returnSince)}</span>${vs}</span>${flag}${r.stance ? `<span class="muted sm">${esc(r.stance)} when recorded (${num(r.score, 0)})</span>` : ""}`;
}
async function loadJournal() {
  const [j, a, card] = await Promise.all([api("/api/journal"), api("/api/alerts"), api("/api/journal/scorecard").catch(() => null)]);
  const results = Object.fromEntries((card?.entries || []).map((r) => [r.id, r]));
  if (card) renderScorecard(card); else $("#jScore").innerHTML = `<div class="muted">The scorecard couldn't be loaded.</div>`;
  $("#journalList").innerHTML = j.length ? j.map((e) => `<div class="jentry"><div class="jh"><span class="sym" style="font-family:var(--mono);font-weight:600">${esc(e.ticker)}</span>
    <span class="pill ${e.action === "buy" || e.action === "add" ? "on" : "warn"}">${esc(e.action)}</span><span class="muted sm">${new Date(e.ts * 1000).toLocaleDateString()} ${e.price ? "@ " + num(e.price) : ""}</span>
    ${e.stop_price ? `<span class="pill">stop ${num(e.stop_price)}</span>` : ""}${e.target_price ? `<span class="pill">target ${num(e.target_price)}</span>` : ""}${e.review_date ? `<span class="pill">review ${esc(e.review_date)}</span>` : ""}
    ${entryResult(results[e.id])}<span style="margin-left:auto"></span><button class="btn ghost sm" data-out="${e.id}" data-text="${esc(e.outcome || "")}">${e.outcome ? "Edit outcome" : "Record outcome"}</button><button class="btn danger" data-jdel="${e.id}">Delete</button></div>
    ${e.thesis ? `<p><b>Thesis:</b> ${esc(e.thesis)}</p>` : ""}${e.invalidation ? `<p class="muted"><b>Wrong if:</b> ${esc(e.invalidation)}</p>` : ""}${e.outcome ? `<p class="up"><b>Outcome:</b> ${esc(e.outcome)}</p>` : ""}</div>`).join("")
    : `<div class="muted">No entries yet. A decision journal is the best way to learn from your own track record.</div>`;
  $$("[data-jdel]").forEach((b) => b.onclick = async () => { if (confirm("Delete this entry?")) { await api(`/api/journal/${b.dataset.jdel}`, { method: "DELETE" }); loadJournal(); } });
  $$("[data-out]").forEach((b) => b.onclick = async () => { const o = prompt("What happened, and what did you learn?", b.dataset.text); if (o !== null) { await api(`/api/journal/${b.dataset.out}`, { method: "PATCH", body: { outcome: o } }); loadJournal(); } });
  table($("#alertTable"), [
    { k: "ticker", label: "Ticker", fmt: (v) => `<span class="sym">${esc(v)}</span>` }, { k: "kind", label: "Condition", fmt: (v) => esc(state.meta.alertKinds[v] || v) },
    { k: "value", label: "Value", num: 1, fmt: (v) => num(v) }, { k: "active", label: "Status", fmt: (v, r) => v ? `<span class="pill on">active</span>` : `<span class="pill warn" title="${esc(r.message)}">triggered</span>` },
    { k: "message", label: "Last trigger", fmt: (v) => `<span class="muted sm">${esc(v || "")}</span>` }, { k: "id", label: "", fmt: (v) => `<button class="btn danger" data-adel="${v}">Delete</button>` },
  ], a, { empty: "No alerts yet." })
  .afterDraw(() => $$("[data-adel]").forEach((b) => b.onclick = async () => { await api(`/api/alerts/${b.dataset.adel}`, { method: "DELETE" }); loadJournal(); }));
}
$("#btnJournalAdd").onclick = async () => {
  if (!$("#jTicker").value.trim()) return toast("Ticker is required");
  await api("/api/journal", { method: "POST", body: { ticker: $("#jTicker").value, action: $("#jAction").value, price: $("#jPrice").value, thesis: $("#jThesis").value,
    invalidation: $("#jInval").value, stop_price: $("#jStop").value, target_price: $("#jTarget").value, review_date: $("#jReview").value } });
  ["#jTicker", "#jPrice", "#jThesis", "#jInval", "#jStop", "#jTarget", "#jReview"].forEach((id) => $(id).value = ""); toast("Journal entry saved"); loadJournal();
};
$("#btnAlertAdd").onclick = async () => {
  try { await api("/api/alerts", { method: "POST", body: { ticker: $("#aTicker").value, kind: $("#aKind").value, value: $("#aValue").value } }); toast("Alert created"); loadJournal(); }
  catch (e) { toast(e.message); }
};
$("#btnCheckAlerts").onclick = async () => { const f = await api("/api/alerts/check", { method: "POST" }); toast(f.length ? f.map((x) => x.message).join(" · ") : "No alerts triggered"); loadJournal(); checkFiredAlerts(); };
$("#btnNotify").onclick = async () => {
  if (!window.Notification) return toast("This browser doesn't support notifications.");
  const answer = await Notification.requestPermission();
  toast(answer === "granted" ? "Notifications are on." : "Notifications are off. You can change this in the browser's site settings.");
};

/* ---------------------------------------------------------------- settings */
function loadSettings() {
  const s = state.meta.settings;
  $("#weightSliders").innerHTML = Object.entries(s.weights).map(([k, v]) => `<div class="wrow"><label>${esc(k)} ${factorInfo(k)}</label><input type="range" min="0" max="100" value="${Math.round(v * 100)}" data-w="${esc(k)}"><span>${Math.round(v * 100)}%</span></div>`).join("");
  $$("[data-w]").forEach((r) => r.oninput = () => r.nextElementSibling.textContent = r.value + "%");
  $("#sBase").value = s.base_currency; $("#sRf").value = (s.risk_free_rate * 100).toFixed(2); $("#sBench").value = s.benchmark;
  $("#targetInputs").innerHTML = Object.entries(s.targets).map(([k, v]) => `<label>${esc(k)}<input type="number" data-tgt="${esc(k)}" value="${esc(v)}"></label>`).join("");
  $("#sCore").value = s.core_target;
  $("#sRegions").innerHTML = state.meta.regions.map((r) => `<label><input type="checkbox" data-region="${esc(r)}" ${(s.regions || state.meta.regions).includes(r) ? "checked" : ""}> ${esc(r)}</label>`).join("");
  renderTileChoices(new Set(s.dashboard_tiles));
}
// Ticks you haven't saved yet are kept when a custom tile is added or removed
function renderTileChoices(shown) {
  $("#tileChoices").innerHTML = state.meta.tiles.map((t) => `<label title="${esc(t.ticker)}"><input type="checkbox" data-tile="${esc(t.ticker)}" ${shown.has(t.ticker) ? "checked" : ""}> ${esc(t.name)}${t.custom ? ` <button class="rm" data-rmtile="${esc(t.ticker)}" title="Remove this custom tile">×</button>` : ""}</label>`).join("");
  $$("[data-rmtile]").forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); // inside a label: don't tick the box
    const ticked = new Set($$("[data-tile]").filter((i) => i.checked).map((i) => i.dataset.tile));
    try { tilesChanged(await api(`/api/tiles/${encodeURIComponent(b.dataset.rmtile)}`, { method: "DELETE" }), ticked); toast("Tile removed"); }
    catch (err) { toast(err.message); }
  });
}
function tilesChanged(r, ticked) {
  state.meta.tiles = r.tiles;
  state.meta.settings.custom_tiles = r.settings.custom_tiles; state.meta.settings.dashboard_tiles = r.settings.dashboard_tiles;
  const known = new Set(r.tiles.map((t) => t.ticker));
  renderTileChoices(new Set([...ticked].filter((t) => known.has(t)))); // a removed tile drops out; a new one arrives ticked
}
$("#btnAddTile").onclick = async () => {
  const ticker = $("#ctTicker").value.trim();
  if (!ticker) return toast("Enter a Yahoo symbol.");
  const btn = $("#btnAddTile"), ticked = new Set($$("[data-tile]").filter((i) => i.checked).map((i) => i.dataset.tile));
  btn.disabled = true; $("#ctMsg").textContent = `Checking ${ticker.toUpperCase()} on Yahoo…`;
  try {
    const r = await api("/api/tiles", { method: "POST", body: { ticker, name: $("#ctName").value } });
    const added = r.tiles.find((t) => t.custom && !state.meta.tiles.some((x) => x.ticker === t.ticker));
    tilesChanged(r, new Set([...ticked, ...(added ? [added.ticker] : [])]));
    $("#ctTicker").value = ""; $("#ctName").value = "";
    $("#ctMsg").textContent = added ? `Added "${added.name}". It's on the dashboard now.` : "";
  } catch (err) { $("#ctMsg").textContent = err.message; }
  finally { btn.disabled = false; }
};
["#ctTicker", "#ctName"].forEach((id) => $(id).onkeydown = (e) => { if (e.key === "Enter") $("#btnAddTile").click(); });
$("#btnBalanced").onclick = () => $$("[data-w]").forEach((r) => { r.value = 20; r.nextElementSibling.textContent = "20%"; });
// The sidebar's status icons: lit green when a service is in use, greyed when it isn't. The logos are files in
// frontend/icons, so the real ones can replace the stand-ins without touching the code.
const statusIcon = (on, img, alt, tip) => `<span class="st ${on ? "on" : "off"}" role="img" aria-label="${esc(tip)}" data-tip="${esc(tip)}" data-icon="${esc(alt)}"><i style="--icon: url('/static/icons/${img}')"></i></span>`;
const currencySymbol = (code) => {
  try { return new Intl.NumberFormat("en", { style: "currency", currency: code }).formatToParts(0).find((x) => x.type === "currency").value; }
  catch { return code; }
};
function aiStatus(a) {
  const tip = a?.on ? `AI: ${a.model} via ${a.server}${a.thinking === "off" ? ", thinking off" : a.thinking === "on" ? ", thinking on" : ["low", "medium", "high"].includes(a.thinking) ? `, thinking ${a.thinking}` : ""}. Writes research briefs and reads screener questions.`
    : a?.problem ? `AI isn't set up: ${a.problem}` : "AI is off. Set AI_PROVIDER in .env (see .env.example) for AI-written briefs.";
  // A click opens Ask StockPilotAI, where the model answers questions with the app's tools
  return `<span class="st txt ai ${a?.on ? "on" : "off"}" role="button" tabindex="0" data-goto="ask" aria-label="${esc(tip)} Opens Ask StockPilotAI." data-tip="${esc(tip)} Click to ask it a question." data-icon="AI">AI</span>`;
}
// The quote icon shows one of these at random on each hover, never the same twice in a row.
const QUOTES = [
  "“The greatest wealth is to live content with little.” Plato",
  "“The love of money is the metropolis of all evils.” Diogenes",
  "“Nothing is enough for the man to whom enough is too little.” Epicurus",
  "“Misers have not got wealth; wealth has got them.” Bion of Borysthenes",
  "“It is not the man who has too little, but the man who craves more, that is poor.” Seneca",
  "“Wealth is the slave of a wise man, the master of a fool.” Seneca",
  "“Not to be greedy is wealth; not to be a compulsive buyer is income.” Cicero",
  "“Endless money is the sinews of war.” Cicero",
  "“Money amassed either serves us or rules us.” Horace",
  "“The love of money grows as the money itself grows.” Juvenal",
  "“Money alone rules all things.” Publilius Syrus",
  "“Time is money.” Benjamin Franklin",
  "“That man is the richest whose pleasures are the cheapest.” Thoreau",
  "“Price is what you pay. Value is what you get.” Warren Buffett",
];
const QUOTE = QUOTES[0];
const nextQuote = (el) => {
  const others = QUOTES.filter((q) => q !== el.dataset.tip);
  const q = others[Math.floor(Math.random() * others.length)];
  el.dataset.tip = q; el.setAttribute("aria-label", q);
};
function renderModeBadge() {
  const m = state.meta, cur = m.settings.base_currency;
  $("#modeBadge").innerHTML = `<div class="status">
    ${statusIcon(!m.demo, "column.svg", "Yahoo Finance", `${m.demo ? "Demo data: made-up prices, Yahoo Finance is off." : "Prices: live from Yahoo Finance."} Click to open Yahoo Finance (the stock's page when you're on one).`).replace('class="st ', 'class="st yahoo ').replace('role="img"', 'role="link" tabindex="0"')}
    <span class="st cur" role="button" tabindex="0" data-currency="${esc(cur)}" aria-label="Base currency: ${esc(cur)}. Opens the currency converter." data-tip="Base currency: ${esc(cur)}. Every total is shown in it; change it in Settings. Click to convert between currencies.">${esc(currencySymbol(cur))}</span>
    ${aiStatus(m.aiInfo)}
    <span class="st quote" tabindex="0" aria-label="${esc(QUOTE)}" data-tip="${esc(QUOTE)}"><i style="--icon: url('/static/icons/owl.svg')"></i></span>
  </div>`;
  const icon = $("#modeBadge .st.cur");
  icon.onclick = openConverter;
  icon.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openConverter(); } };
  $("#modeBadge .st.ai").onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show("ask"); } };
  // The Yahoo mark opens Yahoo Finance in a new tab: the open stock's page when on Research, else the front page
  const yahoo = $("#modeBadge .st.yahoo");
  const openYahoo = () => {
    const onStock = !$("#view-research").classList.contains("hidden") && state.ticker;
    window.open(onStock ? `https://finance.yahoo.com/quote/${encodeURIComponent(state.ticker)}/` : "https://finance.yahoo.com/", "_blank", "noopener,noreferrer");
  };
  yahoo.onclick = openYahoo;
  yahoo.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openYahoo(); } };
}
// The converter: today's rate between two currencies, from the same rates the portfolio uses. Every currency in use
// can be picked; each box searches as you type, by code, name or country ("yen", "JPY", "Japanese"). It starts from
// your base currency to the dollar, and remembers the last pair.
const currencies = () => state.meta?.currencies || { EUR: "Euro", USD: "US dollar" };
const currencyLabel = (c) => `${c} — ${currencies()[c] || c}`;
function currencyOf(text) {
  const all = currencies(), t = String(text || "").trim(), code = t.slice(0, 3).toUpperCase();
  if (all[code] && (t.length === 3 || t.slice(3, 6) === " — ")) return code;
  const hits = Object.entries(all).filter(([c, n]) => `${c} ${n}`.toLowerCase().includes(t.toLowerCase()));
  return t && hits.length === 1 ? hits[0][0] : null;  // one match for words typed, e.g. "yen"
}
function openConverter() {
  const base = state.meta?.settings?.base_currency || "EUR";
  let pair; try { pair = JSON.parse(localStorage.getItem("fxPair")); } catch { pair = null; }
  const [from0, to0] = Array.isArray(pair) && pair.every((c) => currencies()[c]) ? pair : [base, base === "USD" ? "EUR" : "USD"];
  modal(`<h3>Currency converter</h3>
    <datalist id="cvList">${Object.keys(currencies()).map((c) => `<option value="${esc(currencyLabel(c))}"></option>`).join("")}</datalist>
    <div class="form-row conv">
      <label>Amount<input type="number" id="cvAmount" value="1000" min="0" step="any"></label>
      <label>From<input id="cvFrom" class="cv-cur" list="cvList" value="${esc(currencyLabel(from0))}" autocomplete="off" spellcheck="false" placeholder="Type to search"></label>
      <button type="button" class="btn ghost sm" id="cvSwap" title="Swap">⇄</button>
      <label>To<input id="cvTo" class="cv-cur" list="cvList" value="${esc(currencyLabel(to0))}" autocomplete="off" spellcheck="false" placeholder="Type to search"></label>
      <button type="button" class="btn" id="cvGo">Convert</button>
    </div>
    <div id="cvOut" class="conv-out"></div>
    <div class="modal-actions"><span class="muted sm">Today's rate from Yahoo, as the portfolio uses. Your bank or broker will add its own margin.</span><button class="btn ghost" data-close>Close</button></div>`);
  let asked = 0;
  const run = async () => {
    const from = currencyOf($("#cvFrom").value), to = currencyOf($("#cvTo").value), amount = Number($("#cvAmount").value), out = $("#cvOut"), mine = ++asked;
    if (!from || !to) { out.innerHTML = `<span class="muted">Choose the currency to convert ${!from ? "from" : "to"}: type part of its name or code, then pick it in the list.</span>`; return; }
    $("#cvFrom").value = currencyLabel(from); $("#cvTo").value = currencyLabel(to);
    try { localStorage.setItem("fxPair", JSON.stringify([from, to])); } catch { /* kept for this visit */ }
    if (!(amount >= 0)) { out.innerHTML = `<span class="muted">Enter an amount.</span>`; return; }
    try {
      const r = await api(`/api/fx?src=${from}&dst=${to}`);
      if (mine !== asked) return;  // a newer choice is already on its way
      out.innerHTML = `<div class="conv-big">${money(amount, from)} = <b>${money(amount * r.rate, to)}</b></div>
        <div class="muted sm">1 ${from} = ${num(r.rate, r.rate < 0.1 ? 6 : 4)} ${to} · 1 ${to} = ${num(1 / r.rate, 1 / r.rate < 0.1 ? 6 : 4)} ${from}${r.stale ? " · Yahoo isn't answering, so this is the last rate it gave" : ""}</div>`;
    } catch (e) { if (mine === asked) out.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
  };
  // Converts on the button or Enter; swapping converts straight away
  $("#cvGo").onclick = run;
  $$("#cvAmount, .cv-cur").forEach((i) => { i.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } }; });
  // Clicking a currency box clears it, so the whole list shows and typing searches it; leaving it empty puts it back
  $$(".cv-cur").forEach((i) => {
    i.onfocus = () => { i.dataset.was = i.value; i.value = ""; };
    i.onblur = () => { if (!i.value.trim()) i.value = i.dataset.was || ""; };
    i.onchange = () => { if (currencyOf(i.value)) run(); };
  });
  $("#cvSwap").onclick = () => { const f = $("#cvFrom").value; $("#cvFrom").value = $("#cvTo").value; $("#cvTo").value = f; run(); };
  run(); $("#cvAmount").select();
}
$("#btnSaveSettings").onclick = async () => {
  if ($("#sRf").value.trim() === "") return toast("Enter a risk-free rate.");
  const core = Number($("#sCore").value);
  if ($("#sCore").value.trim() === "" || !(core >= 0 && core <= 100)) return toast("The core share is a percentage from 0 to 100.");
  if (!$$("[data-w]").some((r) => Number(r.value) > 0)) return toast("Give at least one factor some weight.");
  const w = Object.fromEntries($$("[data-w]").map((r) => [r.dataset.w, Number(r.value)]));
  const tot = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  const body = { weights: Object.fromEntries(Object.entries(w).map(([k, v]) => [k, v / tot])), base_currency: $("#sBase").value,
    risk_free_rate: Number($("#sRf").value) / 100, benchmark: $("#sBench").value.trim().toUpperCase() || "SPY",
    targets: Object.fromEntries($$("[data-tgt]").map((i) => [i.dataset.tgt, Number(i.value)])),
    dashboard_tiles: $$("[data-tile]").filter((i) => i.checked).map((i) => i.dataset.tile),
    core_target: Number($("#sCore").value), regions: $$("[data-region]").filter((i) => i.checked).map((i) => i.dataset.region) };
  if (!body.regions.length) return toast("Follow at least one region.");
  const regionsBefore = (state.meta.settings.regions || []).join();
  state.meta.settings = await api("/api/settings", { method: "POST", body });
  if (state.meta.settings.regions.join() !== regionsBefore) {  // other regions: other markets and countries to offer
    state.meta = await api("/api/meta"); renderMarketChoices(); state.opp = null;
  }
  renderModeBadge(); loadSettings(); $("#settingsMsg").textContent = "Saved. Weights were normalised to 100%."; toast("Settings saved");
};
$("#btnClearCache").onclick = async () => { await api("/api/cache/clear", { method: "POST" }); toast("Everything will refresh on its next use"); };

$("#btnRestore").onclick = () => $("#restoreFile").click();
$("#restoreFile").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  let backup;
  try { backup = JSON.parse(await file.text()); } catch { return toast("That file isn't a StockPilot backup."); }
  const n = (k) => (Array.isArray(backup[k]) ? backup[k].length : 0);
  if (!confirm(`Replace everything in the app with this backup? It has ${n("transactions") + n("holdings")} transactions, ${n("journal")} journal entries, ${n("alerts")} alerts and ${n("watchlist")} watchlist items.`)) return;
  try { await api("/api/backup/restore", { method: "POST", body: backup }); toast("Backup restored"); setTimeout(() => location.reload(), 600); }
  catch (err) { toast(err.message); }
};

/* ---------------------------------------------------------------- info tooltips */
(() => {
  const tip = document.createElement("div");
  tip.id = "tooltip"; tip.setAttribute("role", "tooltip"); tip.className = "hidden";
  document.body.appendChild(tip);
  let current = null;
  const show = (el) => {
    if (el !== current && el.classList.contains("quote")) nextQuote(el);
    current = el; tip.textContent = el.dataset.tip; tip.classList.remove("hidden");
    const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
    const below = r.bottom + 8 + h < innerHeight;
    tip.style.left = `${left}px`; tip.style.top = `${below ? r.bottom + 8 : r.top - h - 8}px`;
  };
  const hide = () => { current = null; tip.classList.add("hidden"); };
  document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-tip]"); if (el) show(el); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tip]") && !e.relatedTarget?.closest?.("[data-tip]")) hide(); });
  document.addEventListener("focusin", (e) => { if (e.target.matches?.("[data-tip]")) show(e.target); });
  document.addEventListener("focusout", (e) => { if (e.target.matches?.("[data-tip]")) hide(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  // While scrolling, the tip follows its icon, and goes once the icon is off screen or gone
  addEventListener("scroll", () => {
    if (!current) return;
    const r = current.getBoundingClientRect();
    if (!current.isConnected || r.bottom < 0 || r.top > innerHeight) hide(); else show(current);
  }, true);
})();

/* ---------------------------------------------------------------- theme */
// Dark, navy, light, or whatever the computer uses (navy when it's dark). The page's colours follow at once; charts
// are redrawn with the new ones.
const THEMES = ["dark", "navy", "light", "system"];
const savedTheme = () => { try { const t = localStorage.getItem("theme"); return THEMES.includes(t) ? t : "navy"; } catch { return "navy"; } };
const lightQuery = matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const choice = savedTheme();
  const t = choice === "system" ? (lightQuery.matches ? "light" : "navy") : choice;
  $$("#themeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.theme === choice));
  if (document.documentElement.dataset.theme === t) return;
  document.documentElement.dataset.theme = t;
  Chart.defaults.color = C("--muted"); Chart.defaults.borderColor = C("--line");
  for (const ch of Object.values(state.charts)) {
    for (const sc of Object.values(ch.options.scales || {})) {
      if (sc.grid) sc.grid.color = C("--line");
      if (sc.angleLines) sc.angleLines.color = C("--line");
    }
    if (ch.config.type === "doughnut") ch.data.datasets.forEach((d) => { d.borderColor = C("--panel"); });
    ch.update("none");
  }
  // A chart drawn while its page is hidden has no width, so Research is redrawn when it's next shown
  if (state.stock) { if ($("#view-research").classList.contains("hidden")) state.redrawResearch = true; else redrawResearch(); }
}
function redrawResearch() {
  state.redrawResearch = false;
  renderHeader(state.stock); renderPrice(state.stock); renderFactors(state.stock); renderFin(state.stock);
}
$$("#themeSeg button").forEach((b) => b.onclick = () => {
  try { localStorage.setItem("theme", b.dataset.theme); } catch { /* not remembered, but still applied */ }
  applyTheme();
});
lightQuery.addEventListener("change", applyTheme);
applyTheme();

// The Screener's markets and countries come from the regions you follow, so they're built again when that changes
function renderMarketChoices() {
  const m = state.meta;
  $("#scrMarkets").innerHTML = m.markets.map((x) => `<label><input type="checkbox" value="${esc(x)}" ${x !== "Crypto" ? "checked" : ""}> ${esc(x)}</label>`).join("");
  const groups = Object.entries(m.countryGroups || {}).filter(([, cs]) => cs.some((c) => Object.values(m.countries).flat().includes(c)));
  $("#fCountry").innerHTML = '<option value="">Any</option>' + groups.map(([g]) => `<option value="${esc(g)}">${esc(g)} (all)</option>`).join("")
    + Object.entries(m.countries).map(([r, cs]) => `<optgroup label="${esc(r)}">${cs.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}</optgroup>`).join("");
}

/* ---------------------------------------------------------------- boot */
(async function boot() {
  state.meta = await api("/api/meta");
  const m = state.meta;
  renderModeBadge();
  checkFiredAlerts(); setInterval(checkFiredAlerts, 60000);
  watchBackground();
  renderMarketChoices();
  $("#bStrat").innerHTML = Object.entries(m.strategies).map(([k, v]) => `<option value="${k}" ${k === "sma_cross" ? "selected" : ""}>${v.label}</option>`).join("");
  $("#bStrat").onchange = renderParams; renderParams();
  $("#aKind").innerHTML = Object.entries(m.alertKinds).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  const route = () => {
    const [view, arg] = location.hash.slice(1).split("/");
    if (view === "research" && arg) openStock(decodeURIComponent(arg));
    else show(view && document.getElementById(`view-${view}`) ? view : "dashboard", false);
  };
  window.addEventListener("hashchange", route);
  route();
})();
