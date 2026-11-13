"""The tax year as a printable page: what you'd hand an adviser or keep with your return. Every figure is from
tax.residence_summary() and carries its rule; the page prints cleanly (A4, black on white) and the browser's
"Save as PDF" makes the file. Nothing outside the app is loaded."""
import datetime as dt
from html import escape

CSS = """
@import url('/static/vendor/fonts/fonts.css');
:root { color-scheme: light; }
body { font: 13px/1.45 'IBM Plex Sans', system-ui, sans-serif; color: #111; background: #fff; margin: 32px auto; max-width: 960px; padding: 0 20px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 26px 0 8px; border-bottom: 1px solid #bbb; padding-bottom: 4px; }
.sub { color: #555; margin: 0 0 16px; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 10px; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid #ddd; vertical-align: top; }
th { font-weight: 600; background: #f3f3f3; } td.n, th.n { text-align: right; font-family: 'IBM Plex Mono', monospace; white-space: nowrap; }
tr.total td { font-weight: 700; border-top: 2px solid #111; } tr.info td { color: #555; font-style: italic; }
.muted { color: #555; } .warn { background: #fff6dd; border: 1px solid #e6c766; padding: 8px 10px; border-radius: 4px; margin: 10px 0; }
ul { margin: 4px 0 10px 18px; padding: 0; } li { margin: 2px 0; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }
.kpi { border: 1px solid #ccc; border-radius: 6px; padding: 8px 10px; } .kpi .l { color: #555; font-size: 11px; } .kpi .v { font: 600 17px 'IBM Plex Mono', monospace; }
.print { position: fixed; top: 12px; right: 16px; } .print button { font: inherit; padding: 6px 12px; cursor: pointer; }
a { color: #0645ad; word-break: break-all; }
@media print { .print { display: none; } body { margin: 0; max-width: none; } h2 { break-after: avoid; } tr { break-inside: avoid; } @page { size: A4; margin: 16mm; } }
"""


def _m(v, cur, dp=2):
    if v is None:
        return ""
    return f"{'-' if v < 0 else ''}{abs(v):,.{dp}f} {cur}"


def _p(v):
    return "" if v is None else f"{v:.2%}".replace(".00%", "%")


def html(r, settings):
    e, cur, res = escape, r["currency"], r["result"]
    c = r["residence"]
    gains = sum(p["gain"] for p in r["sales"])
    divs = sum(d["gross"] for d in r["dividends"])
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
           f"<title>Tax year {e(r['label'])}, {e(c['name'])}</title><style>{CSS}</style></head><body>",
           "<div class='print'><button onclick='window.print()'>Print or save as PDF</button></div>",
           f"<h1>Tax year {e(r['label'])}: {e(c['name'])}</h1>",
           f"<p class='sub'>{e(r['start'])} to {e(r['end'])} · amounts in {e(cur)} · made {dt.date.today():%d %B %Y} · rules checked {e(r['verified'])}</p>",
           "<div class='warn'>Arithmetic on your recorded trades under the rules below, not tax advice. Check it against your broker's statements "
           "and with your tax office or an adviser before you file.</div>",
           "<div class='kpis'>",
           f"<div class='kpi'><div class='l'>Estimated tax</div><div class='v'>{_m(res['tax'], cur, 0)}</div></div>",
           f"<div class='kpi'><div class='l'>Net gains on sales</div><div class='v'>{_m(gains, cur, 0)}</div></div>",
           f"<div class='kpi'><div class='l'>Dividends (gross)</div><div class='v'>{_m(divs, cur, 0)}</div></div>",
           f"<div class='kpi'><div class='l'>Sales</div><div class='v'>{len({p['saleId'] for p in r['sales']})}</div></div></div>"]
    out.append("<h2>The calculation</h2><table><tr><th>Line</th><th class='n'>Amount</th><th class='n'>Rate</th><th class='n'>Tax</th><th>Rule</th><th>Form</th></tr>")
    for x in res["lines"]:
        out.append(f"<tr class='{'info' if x['kind'] == 'info' else ''}'><td>{e(x['label'])}</td><td class='n'>{_m(x['base'], cur)}</td><td class='n'>{_p(x['rate'])}</td>"
                   f"<td class='n'>{_m(x['tax'], cur) if x['kind'] == 'tax' else ''}</td><td>{e(x['rule'])}{' (shown, not added up)' if x['kind'] == 'info' else ''}</td><td>{e(x['form'] or '')}</td></tr>")
    out.append(f"<tr class='total'><td>Estimated tax</td><td></td><td></td><td class='n'>{_m(res['tax'], cur)}</td><td colspan='2'></td></tr></table>")
    if res["allowances"]:
        out.append("<h2>Allowances</h2><table><tr><th>Allowance</th><th class='n'>Amount</th><th class='n'>Used</th><th class='n'>Left</th></tr>")
        out += [f"<tr><td>{e(a['name'])}</td><td class='n'>{_m(a['amount'], cur, 0)}</td><td class='n'>{_m(a['used'], cur, 0)}</td><td class='n'>{_m(a['left'], cur, 0)}</td></tr>" for a in res["allowances"]]
        out.append("</table>")
    if res["forms"]:
        out.append("<h2>Where the figures go</h2><table><tr><th>Form</th><th>Box</th><th>What</th><th class='n'>Amount</th><th>Note</th></tr>")
        out += [f"<tr><td>{e(f['form'])}</td><td>{e(f['box'])}</td><td>{e(f['label'])}</td><td class='n'>{_m(f['amount'], cur)}</td><td class='muted'>{e(f['note'])}</td></tr>" for f in res["forms"]]
        out.append("</table>")
    methods = ", ".join(sorted(set(r["methodNames"].get(p["method"], p["method"]) for p in r["sales"]))) or "none"
    out.append(f"<h2>Sales ({e(methods)})</h2>")
    if r["sales"]:
        out.append("<table><tr><th>Sold</th><th>Ticker</th><th class='n'>Shares</th><th>Bought</th><th class='n'>Cost</th><th class='n'>Proceeds</th><th class='n'>Gain</th><th>Matched</th></tr>")
        for p in r["sales"]:
            out.append(f"<tr><td>{e(p['date'])}</td><td>{e(p['ticker'])}<div class='muted'>{e(p['name'] or '')}</div></td><td class='n'>{p['shares']:,.6g}</td>"
                       f"<td>{e(p['bought'] or 'pool')}</td><td class='n'>{_m(p['cost'], cur)}</td><td class='n'>{_m(p['proceeds'], cur)}</td><td class='n'>{_m(p['gain'], cur)}</td>"
                       f"<td class='muted'>{e(p['rule'])}{'' if p['exact'] and p.get('exactCost', True) else '; a rate was missing (today’s used)'}</td></tr>")
        out.append(f"<tr class='total'><td colspan='6'>Net</td><td class='n'>{_m(gains, cur)}</td><td></td></tr></table>")
    else:
        out.append("<p class='muted'>No sales in this tax year.</p>")
    out.append("<h2>Dividends</h2>")
    if r["dividends"]:
        out.append("<table><tr><th>Paid</th><th>Ticker</th><th>From</th><th class='n'>Received</th><th class='n'>Withheld abroad</th><th class='n'>Gross</th></tr>")
        for d in r["dividends"]:
            out.append(f"<tr><td>{e(d['date'])}</td><td>{e(d['ticker'])}</td><td>{e(d['country'] or '')}</td><td class='n'>{_m(d['net'], cur)}</td>"
                       f"<td class='n'>{_m(d['withheldEst'], cur)}{'' if d['withheldKnown'] else ' *'}</td><td class='n'>{_m(d['gross'], cur)}</td></tr>")
        out.append(f"<tr class='total'><td colspan='5'>Total gross</td><td class='n'>{_m(divs, cur)}</td></tr></table>")
        if any(not d["withheldKnown"] for d in r["dividends"]):
            out.append("<p class='muted'>* Not entered: the source country's usual treaty rate was assumed.</p>")
    else:
        out.append("<p class='muted'>No dividends in this tax year.</p>")
    extra = []
    for key, title in (("vorabpauschale", "Vorabpauschale"), ("deemedIncome", "Deemed income of non-reporting funds"), ("deemedDisposals", "Deemed disposals (8 years)"),
                       ("cryptoSales", "Crypto sales (global portfolio formula)")):
        rows = res.get(key) or []
        if rows:
            cols = [k for k in rows[0] if k not in ("kind", "estimate", "year")]
            extra.append(f"<h2>{e(title)}</h2><table><tr>{''.join(f'<th>{e(k)}</th>' for k in cols)}</tr>"
                         + "".join("<tr>" + "".join(f"<td class='n'>{v:,.2f}</td>" if isinstance(v, float) else f"<td>{e(str(v))}</td>" for v in (row[k] for k in cols)) + "</tr>" for row in rows)
                         + "</table>")
    out += extra
    if r["funds"]:
        out.append("<h2>Funds, as treated here</h2><table><tr><th>Fund</th><th>Kind</th><th>Domicile</th><th></th></tr>")
        out += [f"<tr><td>{e(f['ticker'])} <span class='muted'>{e(f['name'] or '')}</span></td><td>{e(f['kind'])}</td><td>{e(f['domicile'])}</td>"
                f"<td class='muted'>{'guessed from its name and listing' if f['assumed'] else 'as you set it'}</td></tr>" for f in r["funds"]]
        out.append("</table>")
    for title, items in (("Deadlines", res["deadlines"]), ("Notes", res["notes"] + [p["text"] for p in r["planning"]]),
                         ("Not settled by the sources", res["unsure"]), ("Caveats", r["caveats"])):
        if items:
            out.append(f"<h2>{e(title)}</h2><ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>")
    if r["leftOut"]:
        out.append(f"<div class='warn'>Left out for want of an exchange rate: {e(', '.join(r['leftOut']))}.</div>")
    opts = [o for o in c["options"]]
    if opts:
        answers = (settings.get("tax_options") or {}).get(c["code"], {})
        out.append("<h2>Your answers used</h2><ul>" + "".join(
            f"<li>{e(o['label'])}: {e(str(next((ch[1] for ch in o.get('choices', []) if ch[0] == answers.get(o['key'], o['default'])), answers.get(o['key'], o['default']))))}</li>"
            for o in opts) + "</ul>")
    out.append("<h2>Sources</h2><ul>" + "".join(f"<li><a href='{e(u)}' rel='noopener noreferrer'>{e(u)}</a></li>" for u in c["sources"]) + "</ul>")
    out.append("</body></html>")
    return "".join(out)
