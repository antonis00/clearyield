/* StockPilot frontend — vanilla JS, Lightweight Charts (price) + Chart.js (everything else). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const state = { stockRequest: 0, meta: null, ticker: null, period: "1y", stock: null, lw: [], charts: {}, lastScreen: null };

const CSS = getComputedStyle(document.documentElement);
const C = (n) => CSS.getPropertyValue(n).trim();
const PALETTE = ["#f2b84b", "#4fb3bf", "#a78bfa", "#3fb68b", "#ef5b5b", "#f472b6", "#60a5fa", "#fb923c", "#94a3b8", "#22d3ee"];
Chart.defaults.color = C("--muted");
Chart.defaults.borderColor = "#243042";
Chart.defaults.font.family = "IBM Plex Sans, system-ui, -apple-system, Segoe UI, sans-serif";

/* ---------------------------------------------------------------- helpers */
async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers = { "Content-Type": "application/json" }; }
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `Request failed (${r.status})`);
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
function mkChart(id, cfg) { state.charts[id]?.destroy(); const ctx = document.getElementById(id); state.charts[id] = new Chart(ctx, cfg); return state.charts[id]; }

/* Generic sortable table. cols: {k,label,fmt(v,row),num,sort} */
function table(el, cols, rows, { onClick, empty = "Nothing here yet." } = {}) {
  if (!rows.length) { el.innerHTML = `<div class="muted">${empty}</div>`; return { afterDraw() {} }; }
  let sortK = null, dir = -1, after = () => {};
  const draw = () => {
    const data = [...rows];
    if (sortK) data.sort((a, b) => { const x = a[sortK], y = b[sortK]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    el.innerHTML = `<table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}" data-k="${c.k}">${c.label}${sortK === c.k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${data.map((r, i) => `<tr class="${onClick ? "click" : ""}" data-i="${rows.indexOf(r)}">${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.fmt ? c.fmt(r[c.k], r) : esc(r[c.k])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    $$("th", el).forEach((th) => th.onclick = () => { const k = th.dataset.k; if (sortK === k) dir = -dir; else { sortK = k; dir = -1; } draw(); });
    if (onClick) $$("tbody tr", el).forEach((tr) => tr.onclick = (e) => { if (e.target.closest("button")) return; onClick(rows[+tr.dataset.i]); });
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
  if (push) history.replaceState(null, "", view === "research" && state.ticker ? `#research/${state.ticker}` : `#${view}`);
  ({ dashboard: loadDashboard, portfolio: loadPortfolio, journal: loadJournal, settings: loadSettings }[view] || (() => {}))();
}
$$("#nav a").forEach((a) => a.onclick = () => show(a.dataset.view));
document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g) show(g.dataset.goto); });

/* ---------------------------------------------------------------- search */
(() => {
  const inp = $("#globalSearch"), dd = $("#searchResults");
  let hits = [], sel = 0, timer;
  const render = () => {
    dd.innerHTML = hits.map((h, i) => `<div data-i="${i}" class="${i === sel ? "sel" : ""}"><b>${esc(h.ticker)}</b><span>${esc(h.name)}</span><span class="muted sm" style="margin-left:auto">${esc(h.market)}</span></div>`).join("");
    dd.classList.toggle("hidden", !hits.length);
    $$("div", dd).forEach((d) => d.onmousedown = () => pick(hits[+d.dataset.i]));
  };
  const pick = (h) => { if (!h) return; inp.value = ""; hits = []; render(); openStock(h.ticker); };
  inp.oninput = () => { clearTimeout(timer); timer = setTimeout(async () => { hits = inp.value.trim() ? await api(`/api/search?q=${encodeURIComponent(inp.value)}`) : []; sel = 0; render(); }, 180); };
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, hits.length - 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    else if (e.key === "Enter") pick(hits[sel] || { ticker: inp.value.trim().toUpperCase() });
    else if (e.key === "Escape") { hits = []; render(); }
  };
  inp.onblur = () => setTimeout(() => { hits = []; render(); }, 150);
})();

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
    strip.innerHTML = m.map((x) => `<div class="mcard" data-t="${esc(x.ticker)}"><div class="t">${esc(x.name)}</div><div class="v">${num(x.price, x.price > 1000 ? 0 : 2)}</div><div class="sm">${cpct(x.day, 2)} <span class="muted">1D</span> · ${cpct(x.ret1m)} <span class="muted">1M</span></div>${spark(x.spark, (x.ret1m || 0) >= 0)}</div>`).join("");
    $$(".mcard", strip).forEach((c) => c.onclick = () => openStock(c.dataset.t));
  }).catch((e) => strip.innerHTML = `<div class="muted">${esc(e.message)}</div>`);

  api("/api/portfolio").then((p) => {
    $("#dashIncomplete").classList.toggle("hidden", !p.incomplete);
    if (p.incomplete) $("#dashIncomplete").innerHTML = missingNote(p);
    $("#dashPortfolio").innerHTML = p.positions.length ? [
      ["Value", money(p.total, p.base, 0), ""], ["Today", money(p.dayPnl, p.base, 0), cls(p.dayPnl)],
      ["Total P&L", money(p.pnl, p.base, 0), `${cls(p.pnl)}`, pct(p.pnlPct)], ["Est. income / yr", money(p.annualIncome, p.base, 0), ""],
    ].map(([l, v, c, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div>${s ? `<div class="s ${c}">${s}</div>` : ""}</div>`).join("")
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
    ], w, { onClick: (r) => openStock(r.ticker), empty: "Your watchlist is empty. Research a ticker and click “+ Watchlist”." })
    .afterDraw(() => $$("[data-rm]", wt).forEach((b) => b.onclick = async () => { await api(`/api/watchlist/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); loadDashboard(); }));
  } catch (e) { wt.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
}
async function loadDigest() {
  const ul = $("#digestList"); loading(ul, "Scanning your holdings and watchlist…");
  try {
    const d = await api("/api/digest");
    ul.innerHTML = d.items.length ? d.items.map((i) => `<li class="t${i.tone}" data-t="${esc(i.ticker || "")}">${esc(i.text)}${i.alertId ? ` <button class="btn ghost sm" data-dismiss="${i.alertId}">Dismiss</button>` : ""}</li>`).join("") : `<li>Quiet day — nothing notable on your names.</li>`;
    $$("li[data-t]", ul).forEach((li) => li.dataset.t && (li.onclick = () => openStock(li.dataset.t)));
    $$("[data-dismiss]", ul).forEach((b) => b.onclick = async (e) => { e.stopPropagation(); await api(`/api/alerts/${b.dataset.dismiss}/dismiss`, { method: "POST" }); b.closest("li").remove(); });
  } catch (e) { ul.innerHTML = `<li>${esc(e.message)}</li>`; }
}
$("#refreshDigest").onclick = loadDigest;

/* ---------------------------------------------------------------- research */
async function openStock(t, period) {
  state.ticker = t.toUpperCase(); state.period = period || state.period;
  show("research");
  $("#researchEmpty").classList.add("hidden");
  const body = $("#researchBody");
  body.classList.remove("hidden"); body.style.opacity = 0.5;
  const request = ++state.stockRequest; // a slower reply for an earlier stock must not overwrite this one
  try {
    const s = await api(`/api/stock/${encodeURIComponent(state.ticker)}?period=${state.period}`);
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
    runDcf(); loadNews();
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
  $("#rChange").innerHTML = `<span class="${cls(st.change)}">${st.change > 0 ? "+" : ""}${num(st.change)} (${pct(st.changePct, 2)})</span> <span class="muted sm">1D</span>`;
  const sc = s.scores.composite;
  $("#rScore").textContent = num(sc, 0);
  $("#rScore").style.background = `conic-gradient(${scoreColor(sc)} ${(sc || 0) * 3.6}deg, #243042 0)`;
  $("#rScore").style.boxShadow = `inset 0 0 0 7px ${C("--panel")}`;
  const w = state.meta.settings.weights;
  const basis = i.assetClass !== "Stocks" ? "trend & risk only"
    : Object.values(w).every((v) => Math.abs(v - 0.2) < 0.005) ? "balanced"
    : Object.entries(w).map(([k, v]) => `${k[0].toUpperCase()} ${Math.round(v * 100)}`).join(" · ");
  $("#rStance").innerHTML = `${badge(s.scores.stance)}<div class="muted sm">Composite (${esc(basis)})</div>`;
  $("#btnWatch").textContent = s.watched ? "✓ Watching" : "+ Watchlist";
}
function lwChart(el, height) {
  return LightweightCharts.createChart(el, {
    height, layout: { background: { color: "transparent" }, textColor: C("--muted"), fontFamily: "IBM Plex Mono" },
    grid: { vertLines: { color: "#1b2433" }, horzLines: { color: "#1b2433" } }, rightPriceScale: { borderColor: "#243042" },
    timeScale: { borderColor: "#243042" }, crosshair: { mode: 0 }, autoSize: true,
  });
}
const series = (c, k) => c.time.map((t, i) => (isNum(c[k][i]) ? { time: t, value: c[k][i] } : null)).filter(Boolean);
function renderPrice(s) {
  state.lw.forEach((c) => c.remove()); state.lw = [];
  const c = s.candles;
  const main = lwChart($("#priceChart"), 400), rsi = lwChart($("#rsiChart"), 110), macd = lwChart($("#macdChart"), 110);
  state.lw = [main, rsi, macd];
  main.addCandlestickSeries({ upColor: C("--up"), downColor: C("--down"), borderVisible: false, wickUpColor: C("--up"), wickDownColor: C("--down") })
    .setData(c.time.map((t, i) => ({ time: t, open: c.open[i], high: c.high[i], low: c.low[i], close: c.close[i] })));
  if ($("#tVol").checked) {
    const v = main.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" });
    main.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    v.setData(c.time.map((t, i) => ({ time: t, value: c.volume[i], color: c.close[i] >= c.open[i] ? "#3fb68b44" : "#ef5b5b44" })));
  }
  if ($("#tSma").checked) {
    main.addLineSeries({ color: C("--accent"), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, title: "SMA50" }).setData(series(c, "sma50"));
    main.addLineSeries({ color: C("--accent2"), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, title: "SMA200" }).setData(series(c, "sma200"));
  }
  if ($("#tBb").checked) {
    for (const k of ["bbUpper", "bbLower"]) main.addLineSeries({ color: "#a78bfa99", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }).setData(series(c, k));
  }
  const r = rsi.addLineSeries({ color: "#a78bfa", lineWidth: 1.5, priceLineVisible: false });
  r.setData(series(c, "rsi"));
  r.createPriceLine({ price: 70, color: "#ef5b5b88", lineStyle: 2, axisLabelVisible: false });
  r.createPriceLine({ price: 30, color: "#3fb68b88", lineStyle: 2, axisLabelVisible: false });
  macd.addHistogramSeries({ priceLineVisible: false }).setData(c.time.map((t, i) => (isNum(c.macdHist[i]) ? { time: t, value: c.macdHist[i], color: c.macdHist[i] >= 0 ? "#3fb68b88" : "#ef5b5b88" } : null)).filter(Boolean));
  macd.addLineSeries({ color: C("--accent"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(series(c, "macd"));
  macd.addLineSeries({ color: C("--accent2"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(series(c, "macdSignal"));
  main.timeScale().fitContent();
  // keep the three panes in sync
  let syncing = false;
  state.lw.forEach((src) => src.timeScale().subscribeVisibleLogicalRangeChange((rng) => {
    if (syncing || !rng) return; syncing = true;
    state.lw.forEach((dst) => dst !== src && dst.timeScale().setVisibleLogicalRange(rng)); syncing = false;
  }));
}
$$("#periodSeg button").forEach((b) => b.onclick = () => { $$("#periodSeg button").forEach((x) => x.classList.toggle("on", x === b)); state.period = b.dataset.p; openStock(state.ticker); });
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
    data: { labels: names.map((n) => n[0].toUpperCase() + n.slice(1)), datasets: [{ data: names.map((n) => f[n]), spanGaps: true, backgroundColor: "#f2b84b33", borderColor: C("--accent"), pointBackgroundColor: C("--accent") }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 }, grid: { color: "#243042" }, angleLines: { color: "#243042" }, pointLabels: { color: C("--text"), font: { size: 12 } } } } },
  });
  $("#factorList").innerHTML = names.map((n) => `<div class="factor"><div class="fh"><span>${n} ▸</span><span style="color:${scoreColor(f[n])}">${isNum(f[n]) ? num(f[n], 0) : "n/a"}</span></div>${bar(f[n])}
    <div class="parts">${(s.scores.breakdown[n] || []).map((p) => `<div><span>${esc(p.label)}<small>0 at ${fmtBound(p.bad, p.fmt === "loss" ? "num" : p.fmt)}, 100 at ${fmtBound(p.good, p.fmt === "loss" ? "num" : p.fmt)}</small></span><span>${fmtPart(p)}</span><span style="color:${scoreColor(p.score)}">${isNum(p.score) ? num(p.score, 0) : "n/a"}</span></div>`).join("") || '<div><span>No data for this factor</span></div>'}</div></div>`).join("");
  $$(".factor .fh").forEach((h) => h.onclick = () => h.parentElement.classList.toggle("open"));
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
  $("#statGrid").innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");
}
function renderFin(s) {
  const f = s.financials || [];
  $("#finEmpty").classList.toggle("hidden", !!f.length); $("#finChart").classList.toggle("hidden", !f.length);
  if (!f.length) return;
  mkChart("finChart", {
    type: "bar",
    data: { labels: f.map((x) => x.year), datasets: [
      { label: "Revenue", data: f.map((x) => x.revenue), backgroundColor: "#4fb3bfaa" },
      { label: "Net income", data: f.map((x) => x.netIncome), backgroundColor: "#f2b84baa" },
      { label: "Free cash flow", data: f.map((x) => x.fcf), backgroundColor: "#a78bfaaa" }] },
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
async function loadNews() {
  const ticker = state.ticker;
  const ul = $("#newsList"); loading(ul);
  const n = await api(`/api/stock/${encodeURIComponent(ticker)}/news`).catch(() => []);
  if (ticker !== state.ticker) return;
  ul.innerHTML = n.length ? n.map((x) => `<li><span class="${x.sentiment > 0 ? "up" : x.sentiment < 0 ? "down" : "muted"}">${x.sentiment > 0 ? "▲" : x.sentiment < 0 ? "▼" : "•"}</span> ${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}<div class="meta">${esc(x.publisher || "")} · ${x.time ? new Date(x.time).toLocaleString() : ""}</div></li>`).join("") : "<li class='muted'>No recent headlines.</li>";
}
$("#btnBrief").onclick = async () => {
  const ticker = state.ticker;
  const b = $("#btnBrief"), out = $("#briefOut"); b.disabled = true; loading(out, "Writing brief…");
  try { const r = await api(`/api/stock/${encodeURIComponent(ticker)}/brief`); if (ticker !== state.ticker) return; out.classList.remove("muted"); out.innerHTML = DOMPurify.sanitize(marked.parse(r.text)); }
  catch (e) { out.textContent = e.message; } finally { b.disabled = false; }
};
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
$("#btnAlert").onclick = () => modal(`<h3>Alert on ${esc(state.ticker)}</h3><form><div class="form-row">
  <label>Condition<select name="kind">${Object.entries(state.meta.alertKinds).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
  <label>Value<input name="value" type="number" step="any" value="${state.stock.stats.price.toFixed(2)}" required></label></div>
  <div class="form-row"><button class="btn">Create</button><button type="button" class="btn ghost" data-close>Cancel</button></div></form>`,
  async (f) => { await api("/api/alerts", { method: "POST", body: { ticker: state.ticker, kind: f.get("kind"), value: f.get("value") } }); toast("Alert created"); });
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
    ], d.meta, { onClick: (r) => openStock(r.ticker) });
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
      { k: "f_value", label: "Val", num: 1, fmt: (v) => num(v, 0) }, { k: "f_quality", label: "Qual", num: 1, fmt: (v) => num(v, 0) },
      { k: "f_growth", label: "Grow", num: 1, fmt: (v) => num(v, 0) }, { k: "f_momentum", label: "Mom", num: 1, fmt: (v) => num(v, 0) },
      { k: "f_risk", label: "Risk", num: 1, fmt: (v) => num(v, 0) }, { k: "composite", label: "Score", num: 1, fmt: (v) => `<b>${num(v, 0)}</b>` },
      { k: "stance", label: "Stance", fmt: (v, r) => `${badge(v)}${r.scoredOn !== "All factors" ? `<span class="sub">${esc(r.scoredOn)}</span>` : ""}` },
    ], d.rows, { onClick: (r) => openStock(r.ticker), empty: "No matches — loosen the filters." });
  } catch (e) { el.innerHTML = `<div class="muted">${esc(e.message)}</div>`; } finally { $$("#btnScr,#btnScrQuery").forEach((b) => b.disabled = false); }
}
const selectedMarkets = () => $$("#scrMarkets input:checked").map((i) => i.value);
$("#btnScr").onclick = () => runScreen({ markets: selectedMarkets(), filters: screenerFilters() });
$("#btnScrQuery").onclick = () => $("#scrQuery").value.trim() && runScreen({ markets: selectedMarkets(), query: $("#scrQuery").value });
$("#scrQuery").onkeydown = (e) => e.key === "Enter" && $("#btnScrQuery").click();

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
    ["Today", `<span class="${cls(p.dayPnl)}">${money(p.dayPnl, b, 0)}</span>`], ["Est. dividend income / yr", money(p.annualIncome, b, 0)], ["Positions", p.positions.length],
  ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("");
  table(t, [
    { k: "ticker", label: "Ticker", fmt: symCell }, { k: "shares", label: "Shares", num: 1, fmt: (v) => num(v, v < 1 ? 4 : 2) },
    { k: "avgCost", label: "Avg cost", num: 1, fmt: (v, r) => money(v, r.currency) }, { k: "price", label: "Price", num: 1, fmt: (v, r) => money(v, r.currency) },
    { k: "dayPct", label: "1D", num: 1, fmt: (v) => cpct(v, 2) }, { k: "value", label: `Value (${b})`, num: 1, fmt: (v) => money(v, b, 0) },
    { k: "pnl", label: "P&L", num: 1, fmt: (v) => `<span class="${cls(v)}">${money(v, b, 0)}</span>` }, { k: "pnlPct", label: "P&L %", num: 1, fmt: (v) => cpct(v) },
    { k: "weight", label: "Weight", num: 1, fmt: (v) => pct(v, 1, false) },
    { k: "ticker", label: "", fmt: (v) => `<button class="btn danger" data-lots="${esc(v)}">Lots</button>` },
  ], p.positions, { onClick: (r) => openStock(r.ticker), empty: "No holdings yet — add one above." })
  .afterDraw(() => $$("[data-lots]", t).forEach((btn) => btn.onclick = () => {
    const pos = p.positions.find((x) => x.ticker === btn.dataset.lots);
    modal(`<h3>${esc(pos.ticker)} lots</h3><table><tr><th>Date</th><th class="num">Shares</th><th class="num">Cost</th><th>Note</th><th></th></tr>${pos.lots.map((l) => `<tr><td>${esc(l.bought || "—")}</td><td class="num">${num(l.shares, 4)}</td><td class="num">${money(l.cost, pos.currency)}</td><td>${esc(l.note)}</td><td><button class="btn danger" data-del="${l.id}">Delete</button></td></tr>`).join("")}</table><div class="form-row"><button class="btn ghost" data-close>Close</button></div>`);
    $$("[data-del]", $("#modal")).forEach((d) => d.onclick = async () => { await api(`/api/holdings/${d.dataset.del}`, { method: "DELETE" }); $("#modal").classList.add("hidden"); loadPortfolio(); });
  }));
  if (p.errors?.length) toast(p.errors.join(" · "));
  $("#pfNote").textContent = p.note;
  if (p.performance) {
    const ds = [{ label: "Portfolio", data: p.performance.portfolio, borderColor: C("--accent"), borderWidth: 2, pointRadius: 0 }];
    if (p.performance.benchmark) ds.push({ label: p.performance.benchmarkName, data: p.performance.benchmark, borderColor: C("--muted"), borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0 });
    mkChart("pfPerf", { type: "line", data: { labels: p.performance.dates, datasets: ds }, options: { interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 8 } } } } });
  }
  const r = p.risk;
  $("#pfRisk").innerHTML = [["1Y return", cpct(r.return1y)], ["Benchmark 1Y", cpct(r.benchmarkReturn)], ["Volatility", pct(r.volatility, 1, false)],
    ["Max drawdown", cpct(r.maxDrawdown)], ["Sharpe", num(r.sharpe)], ["Sortino", num(r.sortino)], ["Beta", num(r.beta)]]
    .map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("");
  $("#pfWarn").innerHTML = p.warnings.map((w) => `<li class="t-1">${esc(w)}</li>`).join("") || (p.positions.length ? `<li class="t1">No concentration warnings.</li>` : "");
  const pie = (id, obj) => mkChart(id, { type: "doughnut", data: { labels: Object.keys(obj), datasets: [{ data: Object.values(obj), backgroundColor: PALETTE, borderColor: C("--panel"), borderWidth: 2 }] },
    options: { cutout: "62%", plugins: { legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: (c) => `${c.label}: ${pct(c.raw / p.total, 1, false)}` } } } } });
  pie("aAsset", p.allocation.assetClass); pie("aRegion", p.allocation.region); pie("aSector", p.allocation.sector); pie("aCur", p.allocation.currency);
  table($("#pfRebal"), [
    { k: "assetClass", label: "Asset class" }, { k: "target", label: "Target", num: 1, fmt: (v) => pct(v, 1, false) }, { k: "actual", label: "Actual", num: 1, fmt: (v) => pct(v, 1, false) },
    { k: "drift", label: "Drift", num: 1, fmt: (v) => cpct(v) }, { k: "action", label: "Suggestion", fmt: (v) => `<b class="${v === "Add" ? "up" : v === "Trim" ? "down" : "muted"}">${v}</b>` },
    { k: "trade", label: `Amount (${b})`, num: 1, fmt: (v, row) => row.action === "Hold" ? "—" : money(Math.abs(v), b, 0) },
  ], p.rebalance, { empty: p.incomplete ? "Withheld until every holding has data." : "Add holdings to see rebalancing suggestions." });
}
$("#btnAddHolding").onclick = async () => {
  try {
    await api("/api/holdings", { method: "POST", body: { ticker: $("#hTicker").value, shares: $("#hShares").value, cost: $("#hCost").value, bought: $("#hDate").value, note: $("#hNote").value } });
    ["#hTicker", "#hShares", "#hCost", "#hDate", "#hNote"].forEach((id) => $(id).value = ""); toast("Holding added"); loadPortfolio();
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
    ${entryResult(results[e.id])}<span style="margin-left:auto"></span><button class="btn ghost sm" data-out="${e.id}">${e.outcome ? "Edit outcome" : "Record outcome"}</button><button class="btn danger" data-jdel="${e.id}">Delete</button></div>
    ${e.thesis ? `<p><b>Thesis:</b> ${esc(e.thesis)}</p>` : ""}${e.invalidation ? `<p class="muted"><b>Wrong if:</b> ${esc(e.invalidation)}</p>` : ""}${e.outcome ? `<p class="up"><b>Outcome:</b> ${esc(e.outcome)}</p>` : ""}</div>`).join("")
    : `<div class="muted">No entries yet. A decision journal is the best way to learn from your own track record.</div>`;
  $$("[data-jdel]").forEach((b) => b.onclick = async () => { if (confirm("Delete this entry?")) { await api(`/api/journal/${b.dataset.jdel}`, { method: "DELETE" }); loadJournal(); } });
  $$("[data-out]").forEach((b) => b.onclick = async () => { const o = prompt("What happened, and what did you learn?"); if (o !== null) { await api(`/api/journal/${b.dataset.out}`, { method: "PATCH", body: { outcome: o } }); loadJournal(); } });
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
$("#btnCheckAlerts").onclick = async () => { const f = await api("/api/alerts/check", { method: "POST" }); toast(f.length ? f.map((x) => x.message).join(" · ") : "No alerts triggered"); loadJournal(); };

/* ---------------------------------------------------------------- settings */
function loadSettings() {
  const s = state.meta.settings;
  $("#weightSliders").innerHTML = Object.entries(s.weights).map(([k, v]) => `<div class="wrow"><label>${k}</label><input type="range" min="0" max="100" value="${Math.round(v * 100)}" data-w="${k}"><span>${Math.round(v * 100)}%</span></div>`).join("");
  $$("[data-w]").forEach((r) => r.oninput = () => r.nextElementSibling.textContent = r.value + "%");
  $("#sBase").value = s.base_currency; $("#sRf").value = (s.risk_free_rate * 100).toFixed(2); $("#sBench").value = s.benchmark;
  $("#targetInputs").innerHTML = Object.entries(s.targets).map(([k, v]) => `<label>${k}<input type="number" data-tgt="${k}" value="${v}"></label>`).join("");
}
$("#btnBalanced").onclick = () => $$("[data-w]").forEach((r) => { r.value = 20; r.nextElementSibling.textContent = "20%"; });
$("#btnSaveSettings").onclick = async () => {
  const w = Object.fromEntries($$("[data-w]").map((r) => [r.dataset.w, Number(r.value)]));
  const tot = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  const body = { weights: Object.fromEntries(Object.entries(w).map(([k, v]) => [k, v / tot])), base_currency: $("#sBase").value,
    risk_free_rate: Number($("#sRf").value) / 100, benchmark: $("#sBench").value.trim().toUpperCase() || "SPY",
    targets: Object.fromEntries($$("[data-tgt]").map((i) => [i.dataset.tgt, Number(i.value)])) };
  state.meta.settings = await api("/api/settings", { method: "POST", body });
  loadSettings(); $("#settingsMsg").textContent = "Saved. Weights were normalised to 100%."; toast("Settings saved");
};
$("#btnClearCache").onclick = async () => { await api("/api/cache/clear", { method: "POST" }); toast("Cache cleared — next loads fetch fresh data"); };

$("#btnRestore").onclick = () => $("#restoreFile").click();
$("#restoreFile").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  let backup;
  try { backup = JSON.parse(await file.text()); } catch { return toast("That file isn't a StockPilot backup."); }
  const n = (k) => (Array.isArray(backup[k]) ? backup[k].length : 0);
  if (!confirm(`Replace everything in the app with this backup? It has ${n("holdings")} lots, ${n("journal")} journal entries, ${n("alerts")} alerts and ${n("watchlist")} watchlist items.`)) return;
  try { await api("/api/backup/restore", { method: "POST", body: backup }); toast("Backup restored"); setTimeout(() => location.reload(), 600); }
  catch (err) { toast(err.message); }
};

/* ---------------------------------------------------------------- boot */
(async function boot() {
  state.meta = await api("/api/meta");
  const m = state.meta;
  $("#modeBadge").innerHTML = `${m.demo ? '<span class="pill warn">DEMO DATA</span>' : '<span class="pill on">LIVE · Yahoo</span>'}
    ${m.ai ? '<span class="pill on">AI on</span>' : '<span class="pill">AI off</span>'}<div style="margin-top:6px">Base: ${esc(m.settings.base_currency)}</div>`;
  $("#scrMarkets").innerHTML = m.markets.map((x) => `<label><input type="checkbox" value="${x}" ${x !== "Crypto" ? "checked" : ""}> ${x}</label>`).join("");
  $("#bStrat").innerHTML = Object.entries(m.strategies).map(([k, v]) => `<option value="${k}" ${k === "sma_cross" ? "selected" : ""}>${v.label}</option>`).join("");
  $("#bStrat").onchange = renderParams; renderParams();
  $("#aKind").innerHTML = Object.entries(m.alertKinds).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  const route = () => {
    const [view, arg] = location.hash.slice(1).split("/");
    if (view === "research" && arg) openStock(decodeURIComponent(arg));
    else show(view && $(`#view-${view}`) ? view : "dashboard", false);
  };
  window.addEventListener("hashchange", route);
  route();
})();
