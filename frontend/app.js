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
  catch { return `${num(v, d)} ${cur}`; }
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const badge = (st) => `<span class="badge b-${(st || "Insufficient").split(" ")[0]}">${esc(st || "n/a")}</span>`;
const scoreColor = (s) => (!isNum(s) ? C("--muted") : s >= 70 ? C("--up") : s >= 58 ? C("--accent2") : s >= 45 ? C("--neutral") : s >= 35 ? C("--accent") : C("--down"));
const bar = (s) => `<div class="bar"><i style="width:${isNum(s) ? s : 0}%;background:${scoreColor(s)}"></i></div>`;
function toast(msg, ms = 3500) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }
const missingNote = (p) => `<b>Incomplete:</b> no current data for ${esc(p.missing.join(", "))}, so these totals leave ${p.missing.length > 1 ? "them" : "it"} out and weights and rebalancing are withheld. Check the symbol, or try again later.`;
const loading = (el, msg = "Loading…") => { el.innerHTML = `<div class="muted"><span class="spinner"></span>${msg}</div>`; };
const FACTOR_INFO = {
  value: "Value: is the price cheap for what the company earns and owns? From P/E, forward P/E, price/book, EV/EBITDA and free-cash-flow yield. A higher score means cheaper. Stocks only.",
  quality: "Quality: how profitable and financially sound the business is. From return on equity, operating and net margins, debt/equity and the current ratio. A higher score means a stronger business. Stocks only.",
  growth: "Growth: how fast the business is growing. From revenue and earnings growth over the past year, and forecast against trailing earnings per share. A higher score means faster growth. Stocks only.",
  momentum: "Momentum: how the price has been trending. From the 12-month return leaving out the last month, the price against its 200-day average, and the 3-month return. A higher score means a stronger trend. All assets.",
  risk: "Risk: how bumpy the ride has been. From 1-year volatility and the largest fall from a peak in the past year, plus beta for stocks. A higher score means lower risk. All assets; crypto is judged on a looser scale.",
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
const symCell = (v, r) => `<span class="sym">${esc(v)}</span><span class="sub">${esc(r.name || "")}</span>`;

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
  $$(".view").forEach((v) => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  if (view === "research" && state.redrawResearch && state.stock) redrawResearch();
  if (push) {
    const target = view === "research" && state.ticker ? `#research/${state.ticker}` : `#${view}`;
    if (decodeURIComponent(location.hash) !== target) history.pushState(null, "", target); // so Back and Forward move between screens
  }
  ({ dashboard: loadDashboard, portfolio: loadPortfolio, journal: loadJournal, settings: loadSettings }[view] || (() => {}))();
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

/* ---------------------------------------------------------------- search */
(() => {
  const inp = $("#globalSearch"), dd = $("#searchResults");
  let hits = [], sel = 0, timer;
  const render = () => {
    dd.innerHTML = hits.map((h, i) => `<div data-i="${i}" class="${i === sel ? "sel" : ""}"><b>${esc(h.ticker)}</b><span>${esc(h.name)}</span><span class="muted sm" style="margin-left:auto">${esc(h.market)}</span></div>`).join("");
    dd.classList.toggle("hidden", !hits.length);
    $$("div", dd).forEach((d) => d.onmousedown = (e) => {
      const h = hits[+d.dataset.i];
      if (e.button === 1 || e.ctrlKey || e.metaKey) { e.preventDefault(); if (h) openInNewTab(h.ticker); return; }
      if (e.button === 0) pick(h);
    });
  };
  const pick = (h) => { if (!h) return; inp.value = ""; hits = []; render(); openStock(h.ticker); };
  inp.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = inp.value.trim();
      const found = text ? await api(`/api/search?q=${encodeURIComponent(text)}`) : [];
      if (text !== inp.value.trim()) return; // Yahoo can take a moment, and you may have typed more by now
      hits = found; sel = 0; render();
    }, 250);
  };
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    else if (e.key === "Enter") pick(hits[sel] || { ticker: inp.value.trim().toUpperCase() });
    else if (e.key === "Escape") { hits = []; render(); }
  };
  inp.onblur = () => setTimeout(() => { hits = []; render(); }, 150);
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
async function loadDashboard() {
  const strip = $("#macroStrip");
  if (!strip.children.length) loading(strip, "Loading markets…");
  api("/api/macro").then((m) => {
    if (!m.length) { strip.innerHTML = `<div class="muted">No market tiles chosen. <a data-goto="settings">Pick some in Settings →</a></div>`; return; }
    strip.innerHTML = m.map((x) => `<div class="mcard" data-t="${esc(x.ticker)}" data-stock="${esc(x.ticker)}"><div class="t">${esc(x.name)}</div><div class="v">${num(x.price, x.price > 1000 ? 0 : 2)}</div><div class="sm">${cpct(x.day, 2)} <span class="muted">1D</span> · ${cpct(x.ret1m)} <span class="muted">1M</span></div>${spark(x.spark, (x.ret1m || 0) >= 0)}</div>`).join("");
  }).catch((e) => strip.innerHTML = `<div class="muted">${esc(e.message)}</div>`);

  api("/api/portfolio").then((p) => {
    $("#dashIncomplete").classList.toggle("hidden", !p.incomplete);
    if (p.incomplete) $("#dashIncomplete").innerHTML = missingNote(p);
    $("#dashPortfolio").innerHTML = p.positions.length ? [
      ["Value", money(p.total, p.base, 0), ""], ["Today", money(p.dayPnl, p.base, 0), cls(p.dayPnl)],
      // Everything gained: what's still held, what was sold, and the dividends, with the parts underneath
      ["Total gain", money(p.totalGain, p.base, 0), cls(p.totalGain), `<span class="${cls(p.pnl)}">${money(p.pnl, p.base, 0)}</span> open · <span class="${cls(p.realised)}">${money(p.realised, p.base, 0)}</span> sold · ${money(p.dividends, p.base, 0)} dividends`],
      ["Est. income / yr", money(p.annualIncome, p.base, 0), ""],
    ].map(([l, v, c, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("")
      : `<div class="muted">No holdings yet. <a data-goto="portfolio">Add your first position →</a></div>`;
  }).catch(() => {});

  loadDigest();
  const wt = $("#watchTable");
  if (!wt.innerHTML) loading(wt);
  try {
    const w = await api("/api/watchlist");
    table(wt, [
      { k: "ticker", label: "Ticker", fmt: symCell }, { k: "price", label: "Price", num: 1, fmt: (v, r) => money(v, r.currency) },
      { k: "day", label: "1D", num: 1, fmt: (v) => cpct(v, 2) }, { k: "ret1m", label: "1M", num: 1, fmt: (v) => cpct(v) },
      { k: "ret1y", label: "1Y", num: 1, fmt: (v) => cpct(v) }, { k: "rsi", label: "RSI", num: 1, fmt: (v) => num(v, 0) },
      { k: "composite", label: "Score", num: 1, fmt: (v) => `${num(v, 0)}` }, { k: "stance", label: "Stance", fmt: badge },
      { k: "ticker", label: "", fmt: (v) => `<button class="btn danger" data-rm="${esc(v)}">Remove</button>` },
    ], w, { stock: (r) => r.ticker, empty: "Your watchlist is empty. Research a ticker and click “+ Watchlist”." })
    .afterDraw(() => $$("[data-rm]", wt).forEach((b) => b.onclick = async () => { await api(`/api/watchlist/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); loadDashboard(); }));
  } catch (e) { wt.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
}
async function loadDigest() {
  const ul = $("#digestList"); loading(ul, "Scanning your holdings and watchlist…");
  try {
    const d = await api("/api/digest");
    ul.innerHTML = d.items.length ? d.items.map((i) => `<li class="t${i.tone}" data-t="${esc(i.ticker || "")}"${i.ticker ? ` data-stock="${esc(i.ticker)}"` : ""}>${esc(i.text)}${i.alertId ? ` <button class="btn ghost sm" data-dismiss="${i.alertId}">Dismiss</button>` : ""}</li>`).join("") : `<li>Quiet day — nothing notable on your names.</li>`;
    $$("[data-dismiss]", ul).forEach((b) => b.onclick = async (e) => { e.stopPropagation(); await api(`/api/alerts/${b.dataset.dismiss}/dismiss`, { method: "POST" }); b.closest("li").remove(); checkFiredAlerts(); });
  } catch (e) { ul.innerHTML = `<li>${esc(e.message)}</li>`; }
}
$("#refreshDigest").onclick = loadDigest;

/* ---------------------------------------------------------------- research */
// The range is how far back the chart goes; bars is how much time each candle covers
const chartUrl = () => `/api/stock/${encodeURIComponent(state.ticker)}?period=${state.period}&bars=${state.bars}`;
async function openStock(t, period) {
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
    $("#briefOut").innerHTML = "Combines scores, price action, fundamentals and headlines into a bull/bear summary.";
    $("#briefOut").classList.add("muted");
    $("#rAbout").textContent = s.info.longBusinessSummary || "No description available.";
    $("#ptOut").innerHTML = ""; ["#ptAmount", "#ptStop", "#ptRisk"].forEach((id) => $(id).value = "");
    $("#ptEntry").value = s.stats.price.toFixed(2);
    $("#ptAmountLabel").textContent = `Amount (${state.meta.settings.base_currency})`;
    $("#ptRiskLabel").textContent = `Most I'd lose (${state.meta.settings.base_currency})`;
    const g = s.info.revenueGrowth; $("#dGrowth").value = isNum(g) ? Math.min(Math.max(g * 100, 0), 15).toFixed(1) : 5;
    runDcf(); loadNews(); loadEarnings(); loadFund(s); renderHistory(s);
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
  $("#rPrice").textContent = money(st.price, i.currency);
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
function lwChart(el, height) {
  return LightweightCharts.createChart(el, {
    height, layout: { background: { color: "transparent" }, attributionLogo: false, textColor: C("--muted"), fontFamily: "IBM Plex Mono" },
    grid: { vertLines: { color: C("--grid") }, horzLines: { color: C("--grid") } }, rightPriceScale: { borderColor: C("--line") },
    timeScale: { borderColor: C("--line") }, crosshair: { mode: 0 }, autoSize: true,
  });
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
  $("#factorList").innerHTML = names.map((n) => `<div class="factor"><div class="fh"><span>${n} ▸ ${factorInfo(n)}</span><span style="color:${scoreColor(f[n])}">${isNum(f[n]) ? num(f[n], 0) : "—"}</span></div>${isNum(f[n]) ? bar(f[n]) : `<div class="gap">${esc(factorGap(s.info, n))}</div>`}
    <div class="parts">${(s.scores.breakdown[n] || []).map((p) => `<div><span>${esc(p.label)}<small>0 at ${fmtBound(p.bad, p.fmt === "loss" ? "num" : p.fmt)}, 100 at ${fmtBound(p.good, p.fmt === "loss" ? "num" : p.fmt)}</small></span><span>${fmtPart(p)}</span><span style="color:${scoreColor(p.score)}">${isNum(p.score) ? num(p.score, 0) : "n/a"}</span></div>`).join("") || '<div><span>No data for this factor</span></div>'}</div></div>`).join("");
  $$(".factor .fh").forEach((h) => h.onclick = (e) => { if (!e.target.closest(".info")) h.parentElement.classList.toggle("open"); });
  $("#signalList").innerHTML = s.scores.signals.map((x) => `<li class="t${x.tone}">${esc(x.text)}</li>`).join("") || "<li>No notable signals.</li>";
}
function renderStats(s) {
  const i = s.info, st = s.stats, cur = i.currency;
  const rows = [
    ["Market cap", big(i.marketCap)], ["52w high", money(st.high52, cur)], ["52w low", money(st.low52, cur)], ["RSI (14)", num(st.rsi, 0)],
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
function renderFin(s) {
  const f = s.financials || [];
  $("#finEmpty").classList.toggle("hidden", !!f.length); $("#finChart").classList.toggle("hidden", !f.length);
  if (!f.length) return;
  mkChart("finChart", {
    type: "bar",
    data: { labels: f.map((x) => x.year), datasets: [
      { label: "Revenue", data: f.map((x) => x.revenue), backgroundColor: tint("--accent2", "aa") },
      { label: "Net income", data: f.map((x) => x.netIncome), backgroundColor: tint("--accent", "aa") },
      { label: "Free cash flow", data: f.map((x) => x.fcf), backgroundColor: tint("--violet", "aa") }] },
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
    out.innerHTML = `<div class="dcf-big"><div>${money(d.fairValue, cur)}<small>Fair value / share</small></div><div>${money(d.price, cur)}<small>Price</small></div><div class="${cls(d.marginOfSafety)}">${pct(d.marginOfSafety)}<small>Upside to fair value</small></div></div>
      <div class="muted sm">Starting FCF ${big(d.startFcf)} ${esc(d.statementCurrency || "")} · Terminal value is ${pct(d.terminalShare, 0, false)} of the total, so the result is very sensitive to the discount and terminal rates. Net cash ${big(d.netCash)} ${esc(d.statementCurrency || "")}.${d.statementCurrency && !cur.startsWith(d.statementCurrency.slice(0, 2)) ? ` Fair value is converted from ${esc(d.statementCurrency)} at today's rate.` : ""}</div>
      <p class="sm"><b>What the price assumes:</b> ${isNum(d.impliedGrowth) ? `free cash flow growing ${pct(d.impliedGrowth, 1, false)} a year for ${d.years} years, at this discount and terminal rate.` : "no growth rate from -50% to +100% a year reaches today's price with these rates."}</p>
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
  try { const r = await api(`/api/stock/${encodeURIComponent(ticker)}/brief`); if (ticker !== state.ticker) return; out.classList.remove("muted"); out.innerHTML = DOMPurify.sanitize(marked.parse(r.text)) + briefFooter(r); }
  catch (e) { out.textContent = e.message; } finally { b.disabled = false; }
};
// Who wrote the brief, and whether every figure in it could be found in the data it was given
function briefFooter(r) {
  if (!r.by) return "";
  const c = r.check, by = `Written by ${esc(r.by)}.`;
  if (!c || !c.checked) return `<p class="muted sm">${by}</p>`;
  const own = c.suggested?.length ? ` The triggers use ${c.suggested.length === 1 ? "a level" : `${c.suggested.length} levels`} of the model's own, not from the data: ${c.suggested.map(esc).join(", ")}.` : "";
  const facts = c.checked - (c.suggested?.length || 0);
  if (!c.unmatched.length) return `<p class="brief-check ok">✓ All ${facts} figures match the data.${own} ${by}</p>`;
  return `<p class="brief-check warn">${c.unmatched.length} of ${facts} figures aren't in the data, so the model may have made them up or worked them out: <b>${c.unmatched.map(esc).join(", ")}</b>. Check them before relying on them.${own} ${by}</p>`;
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
  max_vol: ["Max volatility", (v) => pct(v, 0, false)], sector: ["Sector", (v) => esc(v)], sort: ["Sorted by", (v) => esc(v)], markets: ["Markets", (v) => esc(v.join(", "))] };
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
           min_score: v("#fScore"), max_vol: v("#fVol", 100), sort: $("#fSort").value };
}
async function runScreen(body) {
  const el = $("#scrTable"); loading(el, "Scanning the universe… the first run downloads data for ~100 tickers.");
  $$("#btnScr,#btnScrQuery").forEach((b) => b.disabled = true);
  try {
    const d = await api("/api/screener", { method: "POST", body });
    const f = d.filters;
    if (body.query) fillFilterForm(f);
    $("#scrInfo").innerHTML = `${d.rows.length} matches out of ${d.scanned} scanned. ${filterChips(f)}${f.ignored?.length ? `<br><b>Not understood, so not applied:</b> ${esc(f.ignored.join(", "))}` : ""}${d.failed.length ? ` · No data: ${esc(d.failed.join(", "))}` : ""}${f.sort === "composite" ? "<br>Ranked within each asset class: ETFs and crypto are scored on trend and risk only, so their composite isn't comparable with a stock's." : ""}`;
    table(el, [
      { k: "ticker", label: "Ticker", fmt: symCell }, { k: "region", label: "Region" }, { k: "sector", label: "Sector", fmt: (v, r) => `<span class="clip" title="${esc(v || r.assetClass)}">${esc(v || r.assetClass)}</span>` },
      { k: "price", label: "Price", num: 1, fmt: (v, r) => money(v, r.currency) }, { k: "ret1y", label: "1Y", num: 1, fmt: (v) => cpct(v) },
      { k: "pe", label: "P/E", num: 1, fmt: (v) => num(v, 1) }, { k: "divYield", label: "Yield", num: 1, fmt: (v) => pct(v, 2, false) },
      { k: "revGrowth", label: "Rev g.", num: 1, fmt: (v) => cpct(v) }, { k: "roe", label: "ROE", num: 1, fmt: (v) => pct(v, 0, false) },
      { k: "f_value", label: `Val ${factorInfo("value")}`, num: 1, fmt: (v) => num(v, 0) }, { k: "f_quality", label: `Qual ${factorInfo("quality")}`, num: 1, fmt: (v) => num(v, 0) },
      { k: "f_growth", label: `Grow ${factorInfo("growth")}`, num: 1, fmt: (v) => num(v, 0) }, { k: "f_momentum", label: `Mom ${factorInfo("momentum")}`, num: 1, fmt: (v) => num(v, 0) },
      { k: "f_risk", label: `Risk ${factorInfo("risk")}`, num: 1, fmt: (v) => num(v, 0) }, { k: "composite", label: "Score", num: 1, fmt: (v) => `<b>${num(v, 0)}</b>` },
      { k: "stance", label: "Stance", fmt: (v, r) => `${badge(v)}${r.scoredOn !== "All factors" ? `<span class="sub">${esc(r.scoredOn)}</span>` : ""}` },
    ], d.rows, { stock: (r) => r.ticker, empty: "No matches — loosen the filters." });
  } catch (e) { el.innerHTML = `<div class="muted">${esc(e.message)}</div>`; } finally { $$("#btnScr,#btnScrQuery").forEach((b) => b.disabled = false); }
}
const selectedMarkets = () => $$("#scrMarkets input:checked").map((i) => i.value);
$("#btnScr").onclick = () => (selectedMarkets().length ? runScreen({ markets: selectedMarkets(), filters: screenerFilters() }) : toast("Tick at least one market."));
$("#btnScrQuery").onclick = () => $("#scrQuery").value.trim() && runScreen({ markets: selectedMarkets(), query: $("#scrQuery").value });
$("#scrQuery").onkeydown = (e) => { if (e.key === "Enter") $("#btnScrQuery").click(); };

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
    ["Today", `<span class="${cls(p.dayPnl)}">${money(p.dayPnl, b, 0)}</span>`], ["Realised P&L", `<span class="${cls(p.realised)}">${money(p.realised, b, 0)}</span>`], ["Dividends received", money(p.dividends, b, 0)],
    ["Annual return", isNum(p.xirr) ? cpct(p.xirr) : "—", isNum(p.xirr) ? "Your money's return, with timing" : "Needs dated transactions over a month or more"],
    ["Est. dividend income / yr", money(p.annualIncome, b, 0)], ["Positions", p.positions.length],
  ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("");
  table(t, [
    { k: "ticker", label: "Ticker", fmt: symCell }, { k: "shares", label: "Shares", num: 1, fmt: (v) => num(v, v < 1 ? 4 : 2) },
    { k: "avgCost", label: "Avg cost", num: 1, fmt: (v, r) => money(v, r.currency) }, { k: "price", label: "Price", num: 1, fmt: (v, r) => money(v, r.currency) },
    { k: "dayPct", label: "1D", num: 1, fmt: (v) => cpct(v, 2) }, { k: "value", label: `Value (${b})`, num: 1, fmt: (v) => money(v, b, 0) },
    { k: "pnl", label: "P&L", num: 1, fmt: (v) => `<span class="${cls(v)}">${money(v, b, 0)}</span>` }, { k: "pnlPct", label: "P&L %", num: 1, fmt: (v) => cpct(v) },
    { k: "weight", label: "Weight", num: 1, fmt: (v) => pct(v, 1, false) },
    { k: "bucket", label: "Bucket", fmt: (v, r) => `<button class="btn ghost bucket ${v}" data-bucket="${esc(r.ticker)}" title="${v === r.bucketDefault ? "The default for this holding." : "Chosen by you."} Click to make it ${v === "core" ? "satellite" : "core"}.">${v === "core" ? "Core" : "Satellite"}</button>` },
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
    modal(`<h3>${esc(pos.ticker)} lots</h3><table><tr><th>Date</th><th class="num">Shares</th><th class="num">Cost</th><th>Note</th><th></th></tr>${pos.lots.map((l) => `<tr><td>${esc(l.bought || "—")}${l.split && l.split !== 1 ? `<div class="muted sm">Restated for splits since: ×${num(l.split, l.split < 1 ? 4 : 0)} shares</div>` : ""}</td><td class="num">${num(l.shares, 4)}</td><td class="num">${money(l.cost, pos.currency)}</td><td>${esc(l.note)}</td><td><button class="btn danger" data-del="${l.id}">Delete</button></td></tr>`).join("")}</table><div class="form-row"><button class="btn ghost" data-close>Close</button></div>`);
    $$("[data-del]", $("#modal")).forEach((d) => d.onclick = async () => { if (!confirm("Delete this lot?")) return; await api(`/api/holdings/${d.dataset.del}`, { method: "DELETE" }); $("#modal").classList.add("hidden"); loadPortfolio(); });
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
  pie("aAsset", p.allocation.assetClass); pie("aRegion", p.allocation.region); pie("aSector", p.allocation.sectorLookThrough); pie("aCur", p.allocation.currency);
  table($("#pfRebal"), [
    { k: "assetClass", label: "Asset class" }, { k: "target", label: "Target", num: 1, fmt: (v) => pct(v, 1, false) }, { k: "actual", label: "Actual", num: 1, fmt: (v) => pct(v, 1, false) },
    { k: "drift", label: "Drift", num: 1, fmt: (v) => cpct(v) }, { k: "action", label: "Suggestion", fmt: (v) => `<b class="${v === "Add" ? "up" : v === "Trim" ? "down" : "muted"}">${v}</b>` },
    { k: "trade", label: `Amount (${b})`, num: 1, fmt: (v, row) => row.action === "Hold" ? "—" : money(Math.abs(v), b, 0) },
  ], p.rebalance, { empty: p.incomplete ? "Withheld until every holding has data." : "Add holdings to see rebalancing suggestions." });
}
function renderCoreSatellite(p) {
  const cs = p.coreSatellite, b = p.base;
  $("#csCard").classList.toggle("hidden", !cs);
  if (!cs) return;
  const at = (v) => Math.min(Math.max(v * 100, 0), 100);
  const target = `${Math.round(cs.target * 100)}/${Math.round((1 - cs.target) * 100)}`;
  const advice = cs.action === undefined ? "No suggestion until every holding has data."
    : cs.action === "Hold" ? `Within 2 points of your ${target} target.`
    : `Move about ${money(cs.amount, b, 0)} ${cs.action === "To core" ? "from satellite to core" : "from core to satellite"} to reach ${target}, or put new money into ${cs.action === "To core" ? "core" : "satellite"} until it gets there.`;
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
  $("#weightSliders").innerHTML = Object.entries(s.weights).map(([k, v]) => `<div class="wrow"><label>${k} ${factorInfo(k)}</label><input type="range" min="0" max="100" value="${Math.round(v * 100)}" data-w="${k}"><span>${Math.round(v * 100)}%</span></div>`).join("");
  $$("[data-w]").forEach((r) => r.oninput = () => r.nextElementSibling.textContent = r.value + "%");
  $("#sBase").value = s.base_currency; $("#sRf").value = (s.risk_free_rate * 100).toFixed(2); $("#sBench").value = s.benchmark;
  $("#targetInputs").innerHTML = Object.entries(s.targets).map(([k, v]) => `<label>${k}<input type="number" data-tgt="${k}" value="${v}"></label>`).join("");
  $("#sCore").value = s.core_target;
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
  return `<span class="st txt ${a?.on ? "on" : "off"}" role="img" aria-label="${esc(tip)}" data-tip="${esc(tip)}" data-icon="AI">AI</span>`;
}
function renderModeBadge() {
  const m = state.meta, cur = m.settings.base_currency;
  $("#modeBadge").innerHTML = `<div class="status">
    ${statusIcon(!m.demo, "yahoo.svg", "Yahoo Finance", m.demo ? "Demo data: made-up prices, Yahoo Finance is off." : "Prices: live from Yahoo Finance.")}
    ${aiStatus(m.aiInfo)}
    <span class="st cur" role="img" data-currency="${esc(cur)}" aria-label="Base currency: ${esc(cur)}" data-tip="Base currency: ${esc(cur)}. Every total is shown in it; change it in Settings.">${esc(currencySymbol(cur))}</span>
  </div>`;
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
    core_target: Number($("#sCore").value) };
  state.meta.settings = await api("/api/settings", { method: "POST", body });
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
// Dark, light, or whatever the computer uses. The page's colours follow at once; charts are redrawn with the new ones.
const savedTheme = () => { try { return localStorage.getItem("theme") || "dark"; } catch { return "dark"; } };
const lightQuery = matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const choice = savedTheme();
  const t = choice === "system" ? (lightQuery.matches ? "light" : "dark") : choice;
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

/* ---------------------------------------------------------------- boot */
(async function boot() {
  state.meta = await api("/api/meta");
  const m = state.meta;
  renderModeBadge();
  checkFiredAlerts(); setInterval(checkFiredAlerts, 60000);
  $("#scrMarkets").innerHTML = m.markets.map((x) => `<label><input type="checkbox" value="${x}" ${x !== "Crypto" ? "checked" : ""}> ${x}</label>`).join("");
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
