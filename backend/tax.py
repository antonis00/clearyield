"""A tax year's figures from the ledger: what was realised and what was received, in the base currency.

Each sale is matched to the oldest shares still held (first in, first out, as the ledger does), and both sides are
converted at the rate on their own dates, so a currency's move counts in the gain as it would for a tax return made out
in the base currency. Dividends are what you entered, which is what the broker paid: tax withheld at source is already
out of them. This is the arithmetic, not tax advice: countries differ (average cost instead of first in first out,
allowances, a separate rate for long holdings), and the page says so."""
import csv
import datetime as dt
import io

from . import data, ledger
from .portfolio import _lot_fx, _sale_fx
from .universe import country_of

LONG_DAYS = 365  # held over a year, which some countries tax differently
# The standard rate each country withholds from dividends paid to someone living abroad, before any treaty relief.
# Only rates that are well established; anywhere else the page says to check with the broker.
WITHHOLDING = {
    "United States": "30%, or 15% with a W-8BEN form under most tax treaties",
    "Canada": "25%, or 15% under most tax treaties",
    "United Kingdom": "none on ordinary dividends",
    "Germany": "26.375% (25% plus the solidarity surcharge); part can be reclaimed under most treaties",
    "Netherlands": "15%",
    "Switzerland": "35%; part can be reclaimed under most treaties",
}
FUND_NOTE = ("A fund listed in Europe pays out under the rules of the country it's domiciled in, not where it's listed: "
             "Irish and Luxembourg funds, the usual ones, generally withhold nothing. Accumulating funds pay nothing out.")
CAVEATS = [
    "Arithmetic, not tax advice: countries tax differently (some use the average cost instead of first in, first out, "
    "some have allowances or a lower rate for long holdings).",
    "Both sides of each sale are converted at the rate on their own date, so a currency's move is part of the gain.",
    "Dividends are what you entered, which is what your broker paid: tax withheld at source is already out of them.",
]


def held_over_a_year(bought, sold):
    """Whether a lot sold on `sold` was held more than a year: the day after the anniversary of `bought` or later (an
    anniversary sale isn't "more than a year"; 29 February counts from 1 March)."""
    b = dt.date.fromisoformat(bought)
    try:
        anniversary = b.replace(year=b.year + 1)
    except ValueError:  # 29 February
        anniversary = dt.date(b.year + 1, 3, 1)
    return dt.date.fromisoformat(sold) > anniversary


LEFT_OUT = {}  # holdings the last summary had to leave out (no exchange rate at all), named on the page


def _rows(base):
    """Every sale piece and dividend in the ledger, in the base currency at each date's rate. A holding with no
    exchange rate at all is left out and named (`left`), never dropped silently."""
    book = ledger.replay(ledger.transactions())
    sales, dividends, approx, left = [], [], set(), set()
    for t, p in book.items():
        inf = data.info(t)
        cur = inf.get("currency")
        try:
            fx = data.fx_rate(cur, base)
        except data.DataError:
            if p["sales"] or p["dividends"]:
                left.add(t)
            continue
        for s in p["sales"]:
            sold_at, a = _sale_fx(cur, base, s["date"], fx)
            bought_at, b = _lot_fx(s, cur, base, fx)
            proceeds, cost = s["shares"] * s["price"] * sold_at, s["shares"] * s["cost"] * bought_at
            held = (dt.date.fromisoformat(s["date"]) - dt.date.fromisoformat(s["bought"])).days if s["bought"] else None
            sales.append({"ticker": t, "name": inf.get("name"), "date": s["date"], "bought": s["bought"] or None, "shares": s["shares"],
                          "price": s["price"], "cost": s["cost"], "currency": cur, "rateSold": sold_at, "rateBought": bought_at,
                          "proceeds": proceeds, "costBase": cost, "gain": proceeds - cost, "heldDays": held,
                          "long": held is not None and held_over_a_year(s["bought"], s["date"]), "exact": a and b})
            if not (a and b):
                approx.add(t)
        pay = data.main_unit(cur)
        for d in p["dividends"]:
            try:
                today_rate = fx if pay == cur else data.fx_rate(pay, base)
            except data.DataError:
                left.add(t)
                continue
            at, a = _sale_fx(pay, base, d["date"], today_rate)
            fund = inf.get("assetClass") == "ETFs"
            country = country_of(t)
            dividends.append({"ticker": t, "name": inf.get("name"), "date": d["date"], "amount": d["amount"], "currency": pay, "rate": at,
                              "inBase": d["amount"] * at, "country": country, "fund": fund and "." in t, "exact": a})
            if not a:
                approx.add(t)
    LEFT_OUT[base] = sorted(left)
    return sales, dividends, approx


def years(base):
    """The calendar years with a sale or a dividend, newest first."""
    sales, dividends, _ = _rows(base)
    return sorted({r["date"][:4] for r in sales + dividends}, reverse=True)


def summary(settings, year=None):
    base = settings.get("base_currency", "USD")
    sales, dividends, approx = _rows(base)
    all_years = sorted({r["date"][:4] for r in sales + dividends}, reverse=True)
    year = str(year) if year else (all_years[0] if all_years else str(dt.date.today().year))
    sales = sorted((r for r in sales if r["date"].startswith(year)), key=lambda r: (r["date"], r["ticker"]))
    dividends = sorted((r for r in dividends if r["date"].startswith(year)), key=lambda r: (r["date"], r["ticker"]))
    gains = sum(r["gain"] for r in sales if r["gain"] > 0)
    losses = sum(r["gain"] for r in sales if r["gain"] < 0)
    by_country = {}
    for d in dividends:
        key = "Funds listed in Europe" if d["fund"] else (d["country"] or "Unknown")
        c = by_country.setdefault(key, {"country": key, "amount": 0.0, "tickers": [],
                                        "withholding": FUND_NOTE if d["fund"] else WITHHOLDING.get(d["country"])})
        c["amount"] += d["inBase"]
        if d["ticker"] not in c["tickers"]:
            c["tickers"].append(d["ticker"])
    return {"base": base, "year": year, "years": all_years, "sales": sales, "dividends": dividends,
            "gains": gains, "losses": losses, "net": gains + losses, "proceeds": sum(r["proceeds"] for r in sales),
            "longNet": sum(r["gain"] for r in sales if r["long"]), "shortNet": sum(r["gain"] for r in sales if not r["long"]),
            "dividendTotal": sum(d["inBase"] for d in dividends),
            "dividendsByCountry": sorted(by_country.values(), key=lambda c: -c["amount"]),
            "approximate": sorted(t for t in approx if any(r["ticker"] == t for r in sales + dividends)),
            "leftOut": LEFT_OUT.get(base, []),
            "caveats": CAVEATS}


def to_csv(s):
    """The year's lines as a spreadsheet: sales, then dividends, each with the rates used."""
    out = io.StringIO()
    w = csv.writer(out)
    b = s["base"]
    w.writerow(["Sales", s["year"], f"amounts in {b} unless named", "first in, first out"])
    w.writerow(["Date sold", "Ticker", "Name", "Shares", "Date bought", "Days held", "Currency", "Cost per share", "Price per share",
                f"Rate bought ({b} per unit)", f"Rate sold ({b} per unit)", f"Cost ({b})", f"Proceeds ({b})", f"Gain ({b})", "Rate exact"])
    from .orders import _cell  # names come from Yahoo and tickers from pasted files: never run as a formula
    for r in s["sales"]:
        w.writerow([r["date"], _cell(r["ticker"]), _cell(r["name"]), f"{r['shares']:.6g}", r["bought"] or "unknown", "" if r["heldDays"] is None else r["heldDays"],
                    r["currency"], f"{r['cost']:.6g}", f"{r['price']:.6g}", f"{r['rateBought']:.6g}", f"{r['rateSold']:.6g}",
                    f"{r['costBase']:.2f}", f"{r['proceeds']:.2f}", f"{r['gain']:.2f}", "yes" if r["exact"] else "no: today's rate"])
    w.writerow(["Total", "", "", "", "", "", "", "", "", "", "", "", f"{s['proceeds']:.2f}", f"{s['net']:.2f}"])
    w.writerow([])
    w.writerow(["Dividends", s["year"], "as received from the broker"])
    w.writerow(["Date", "Ticker", "Name", "Country listed", "Amount", "Currency", f"Rate ({b} per unit)", f"Amount ({b})", "Rate exact"])
    for d in s["dividends"]:
        w.writerow([d["date"], _cell(d["ticker"]), _cell(d["name"]), _cell(d["country"] or ""), f"{d['amount']:.2f}", d["currency"], f"{d['rate']:.6g}",
                    f"{d['inBase']:.2f}", "yes" if d["exact"] else "no: today's rate"])
    w.writerow(["Total", "", "", "", "", "", "", f"{s['dividendTotal']:.2f}"])
    return out.getvalue()


# ---------------------------------------------------------------- your tax residence's rules (taxlaw.py, taxcalc.py)

import copy  # noqa: E402

from . import taxcalc, taxlaw  # noqa: E402

RESIDENCE_CAVEATS = [
    "Arithmetic on your records under the rules listed, not tax advice. Rules change every year and depend on your whole situation: check with your tax office or an adviser before filing.",
    "Each amount is converted to the tax currency at the rate on its own date (Yahoo's daily rate; tax offices may name another source, such as the ECB's).",
    "Fees are in the prices you recorded. Dividends are what arrived; tax withheld abroad is what you entered, or the source country's usual rate when you didn't.",
]
HARVEST_WINDOW = {"US": ("wash-sale rule", 30), "UK": ("30-day rule", 30), "IE": ("four-week rule", 28), "ES": ("two-month rule", 61)}
# Holding periods after which a gain is taxed less: (days, what happens), by residence and kind of asset
CROSSINGS = {"US": {"shares": (365, "becomes long-term (lower rates)"), "funds": (365, "becomes long-term (lower rates)"), "crypto": (365, "becomes long-term (lower rates)")},
             "DE": {"crypto": (365, "becomes tax-free")}, "PT": {"crypto": (365, "becomes tax-free"), "shares": (365, "no longer counts as a short holding"),
                                                                 "funds": (365, "no longer counts as a short holding")}}


def year_bounds(code, y):
    if taxlaw.COUNTRIES[code]["year"] == "uk":
        return f"{y}-04-06", f"{y + 1}-04-05", f"{y}/{str(y + 1)[2:]}"
    return f"{y}-01-01", f"{y}-12-31", str(y)


def year_of(code, date):
    y = int(date[:4])
    if taxlaw.COUNTRIES[code]["year"] == "uk" and date[5:] < "04-06":
        return y - 1
    return y


def _flags(tk, e):
    """Transaction taxes that depend on the company: France's, Spain's and Italy's on their large listed companies."""
    cap = e["info"].get("marketCap") or 0
    try:
        cap_eur = cap * data.fx_rate(e["currency"], "EUR") if cap else 0
    except data.DataError:
        cap_eur = 0
    stock = e["category"] == "shares"
    return {"ttf": stock and tk.endswith(".PA") and cap_eur > 1e9, "ftt_es": stock and tk.endswith(".MC") and cap_eur > 1e9,
            "tobin": stock and tk.endswith(".MI") and cap_eur > 5e8}


def _unit(evs, tk, date, cur):
    """One unit of `tk` in the tax currency on `date`: the quoted close then, at that day's rate (None without a price)."""
    v = taxcalc.value_on({tk: {**evs[tk], "trades": [{"kind": "buy", "date": "", "shares": 1, "value": 0}]}}, date, cur)
    return v[0]["value"] if v and v[0]["value"] is not None else None


def residence_summary(settings, year=None, today=None):
    """Your tax year under your residence's rules: every line with its rule, forms and boxes, deadlines, losses carried
    from earlier years (each year worked out in turn from your first trade), and planning notes for the year under way."""
    code = settings.get("tax_residence")
    c = taxlaw.COUNTRIES[code]
    cur = c["currency"]
    today = today or dt.date.today().isoformat()
    evs, left = taxcalc.events(cur)
    overrides = settings.get("fund_tax") or {}
    pieces, dividends, trades = [], [], []
    for tk, e in evs.items():
        e["country"] = country_of(tk)
        if e["category"] == "funds":
            e["fundKind"], e["domicile"], e["assumed"] = taxlaw.fund_profile(tk, e["info"], overrides)
        method = c["methods"][e["category"]]
        ps, _ = taxcalc.match(e, method)
        for p in ps:
            pieces.append({**p, "ticker": tk, "name": e["name"], "category": e["category"], "fundKind": e.get("fundKind"), "domicile": e.get("domicile"), "method": method})
        for d in e["dividends"]:
            dividends.append({**d, "ticker": tk, "name": e["name"], "category": e["category"], "country": e["country"], "fund": e["category"] == "funds",
                              "fundKind": e.get("fundKind"), "domicile": e.get("domicile"), "sourceRates": c.get("sourceRates")})
        for t in e["trades"]:
            if t["date"]:
                trades.append({**t, "ticker": tk, "category": e["category"], "domicile": e.get("domicile"), **_flags(tk, e)})
    dated = [p["date"] for p in pieces] + [d["date"] for d in dividends] + [t["date"] for t in trades]
    this_year = year_of(code, today)
    years = sorted({year_of(code, d) for d in dated} | {this_year}, reverse=True)
    target = int(year) if year else this_year
    prices = {}

    def value_at(tk, date):
        key = (tk, date)
        if key not in prices:
            prices[key] = _unit(evs, tk, min(date, today), cur)
        return prices[key]

    def totals(date):
        vals = taxcalc.value_on(evs, min(date, today), cur)
        by = {}
        for v in vals:
            by[v["category"]] = by.get(v["category"], 0) + (v["value"] or 0)
        return sum(by.values()), by

    def bought_near(tk, date, days, piece, after_only=False):
        d0 = dt.date.fromisoformat(date)
        for t in evs[tk]["trades"]:
            if t["kind"] != "buy" or not t["date"] or t["date"] == piece.get("bought"):
                continue
            gap = (dt.date.fromisoformat(t["date"]) - d0).days
            if (0 < gap <= days) if after_only else (abs(gap) <= days):
                return True
        return False

    def deemed_disposals(every):
        """Each fund lot's deemed sale every `every` years after purchase (Ireland's eight), with what it would be
        worth then: the price that day once it's passed, today's price as an estimate before."""
        out = []
        for tk, e in evs.items():
            if e["category"] != "funds" or e.get("domicile") == "other":
                continue
            for b in [t for t in e["trades"] if t["kind"] == "buy" and t["date"]]:
                d = dt.date.fromisoformat(b["date"])
                for k in range(1, 4):
                    try:
                        when = d.replace(year=d.year + every * k).isoformat()
                    except ValueError:
                        when = dt.date(d.year + every * k, 3, 1).isoformat()
                    _, lots = taxcalc.fifo([t for t in e["trades"] if (t["date"] or "") <= when])
                    lot = next((x for x in lots if x["bought"] == b["date"]), None)
                    if not lot or lot["shares"] <= 1e-9:
                        break
                    unit = value_at(tk, when)
                    out.append({"ticker": tk, "date": when, "bought": b["date"], "shares": lot["shares"], "cost": lot["cost"],
                                "value": unit * lot["shares"] if unit is not None else None,
                                "gain": (unit * lot["shares"] - lot["cost"]) if unit is not None else 0.0, "estimate": when > today})
        return sorted(out, key=lambda x: x["date"])

    carry, result, year_pieces, year_divs = {}, None, [], []
    for y in range(min(years), target + 1):
        start, end, label = year_bounds(code, y)
        ctx = {"year": y, "label": label, "start": start, "end": end, "currency": cur, "events": evs, "carry": carry,
               "options": taxlaw.options_for(code, settings.get("tax_options")),
               "pieces": [copy.deepcopy(p) for p in pieces if start <= p["date"] <= end],
               "dividends": [d for d in dividends if start <= d["date"] <= end],
               "trades": [t for t in trades if start <= t["date"] <= end],
               "value_at": value_at, "price_of": value_at, "bought_near": bought_near, "deemed_disposals": deemed_disposals}
        ctx["buys"] = [t for t in ctx["trades"] if t["kind"] == "buy"]
        prev = (dt.date.fromisoformat(start) - dt.timedelta(days=1)).isoformat()
        if y == target or code in ("NL", "BE", "ES", "IT", "CH"):
            ctx["year_end_value"], ctx["year_end_by_cat"] = totals(end)
            ctx["year_start_value"] = totals(prev)[0]
        else:  # earlier years only pass their carried losses on, which don't depend on values
            ctx["year_end_value"], ctx["year_end_by_cat"], ctx["year_start_value"] = None, {}, None
        result = c["compute"](ctx)
        carry = result["carry"]
        year_pieces, year_divs = ctx["pieces"], ctx["dividends"]
    start, end, label = year_bounds(code, target)
    planning = _planning(code, evs, cur, result, today) if start <= today <= end else []
    approx = sorted({p["ticker"] for p in year_pieces if not p["exact"] or not p.get("exactCost", True)} | {d["ticker"] for d in year_divs if not d["exact"]})
    divs = []
    for d in year_divs:
        g, w, est = taxlaw.gross_and_withheld(d)
        divs.append({**d, "gross": g, "withheldEst": w, "estimated": est})
    return {"residence": taxlaw.public(code), "year": target, "label": label, "start": start, "end": end, "years": years, "currency": cur,
            "result": result, "sales": year_pieces, "dividends": divs,
            "funds": [{"ticker": tk, "name": e["name"], "kind": e["fundKind"], "domicile": e["domicile"], "assumed": e["assumed"]} for tk, e in evs.items() if e["category"] == "funds"],
            "methodNames": taxcalc.METHOD_NAMES, "planning": planning, "leftOut": left, "approximate": approx,
            "verified": taxlaw.VERIFIED, "caveats": RESIDENCE_CAVEATS}


def _planning(code, evs, cur, result, today):
    """For the year under way: the allowance left against unrealised gains, unrealised losses that could offset the
    year's taxed gains (with the country's rule on buying back), and holdings about to cross a holding period that
    lowers their tax."""
    notes, gains, losses = [], [], []
    for tk, e in evs.items():
        _, lots = taxcalc.match(e, "fifo")
        if not lots:
            continue
        unit = _unit(evs, tk, today, cur)
        if unit is None:
            continue
        for lot in lots:
            g = unit * lot["shares"] - lot["cost"]
            # Ireland's funds pay exit tax, with no exemption and no use for losses: not part of the CGT planning
            if code == "IE" and e["category"] == "funds":
                continue
            (gains if g >= 0 else losses).append({"ticker": tk, "gain": g})
            cross = CROSSINGS.get(code, {}).get(e["category"])
            if cross and lot["bought"] and g > 0:
                days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(lot["bought"])).days
                if 0 < cross[0] - days <= 60:
                    notes.append({"kind": "holding", "ticker": tk, "text": f"{tk} bought {lot['bought']} {cross[1]} in {cross[0] - days} days, "
                                                                          f"on an unrealised gain of {g:,.0f} {cur}: selling before then costs more."})
    ug, ul = sum(x["gain"] for x in gains), sum(x["gain"] for x in losses)
    for a in result.get("allowances", []):
        if a["left"] > 0 and ug > 0 and "ividend" not in a["name"] and "Box 3" not in a["name"]:
            notes.append({"kind": "allowance", "text": f"{a['name']}: {a['left']:,.0f} {cur} unused this year, with {ug:,.0f} {cur} of unrealised gains. "
                                                       "Realising gains up to it costs no tax (check the rules on buying back the same day)."})
    taxed = sum(x["tax"] for x in result.get("lines", []) if x["kind"] == "tax" and (x["tax"] or 0) > 0)
    if losses and taxed > 0:
        rule = HARVEST_WINDOW.get(code)
        notes.append({"kind": "losses", "text": f"Unrealised losses of {-ul:,.0f} {cur} ({', '.join(sorted({x['ticker'] for x in losses}))}) could offset this year's taxed gains if sold"
                      + (f"; mind the {rule[0]}: buying the same back within {rule[1]} days changes how the loss counts." if rule else ".")})
    return notes


def residence_csv(s):
    """The year under your residence's rules as a spreadsheet: the tax lines, the forms, each sale and each dividend."""
    out = io.StringIO()
    w = csv.writer(out)
    from .orders import _cell
    c, cur = s["residence"], s["currency"]
    w.writerow([f"Tax year {s['label']}", c["name"], f"amounts in {cur}", f"rules checked {s['verified']}"])
    w.writerow([])
    w.writerow(["Line", "Amount", "Rate", "Tax", "Rule", "Form"])
    for x in s["result"]["lines"]:
        w.writerow([x["label"], f"{x['base']:.2f}" if x["base"] is not None else "", f"{x['rate']:.4%}" if x["rate"] is not None else "",
                    f"{x['tax']:.2f}" if x["tax"] is not None else "", x["rule"] + (" (shown, not added up)" if x["kind"] == "info" else ""), x["form"] or ""])
    w.writerow(["Estimated tax", "", "", f"{s['result']['tax']:.2f}"])
    w.writerow([])
    w.writerow(["Form", "Box", "What", "Amount", "Note"])
    for f in s["result"]["forms"]:
        w.writerow([f["form"], f["box"], f["label"], "" if f["amount"] is None else f"{f['amount']:.2f}", f["note"]])
    w.writerow([])
    w.writerow(["Sold", "Ticker", "Name", "Kind", "Shares", "Bought", "Days held", f"Cost ({cur})", f"Proceeds ({cur})", f"Gain ({cur})", "Matched by", "Rate exact"])
    for p in s["sales"]:
        w.writerow([p["date"], _cell(p["ticker"]), _cell(p["name"]), p["category"], f"{p['shares']:.6g}", p["bought"] or "pool", "" if p["heldDays"] is None else p["heldDays"],
                    f"{p['cost']:.2f}", f"{p['proceeds']:.2f}", f"{p['gain']:.2f}", p["rule"], "yes" if p["exact"] and p.get("exactCost", True) else "no"])
    w.writerow([])
    w.writerow(["Paid", "Ticker", "Name", "Country", f"Received ({cur})", f"Withheld ({cur})", f"Gross ({cur})", "Withheld entered"])
    for d in s["dividends"]:
        w.writerow([d["date"], _cell(d["ticker"]), _cell(d["name"]), _cell(d["country"] or ""), f"{d['net']:.2f}", f"{d['withheldEst']:.2f}", f"{d['gross']:.2f}",
                    "yes" if d["withheldKnown"] else "no: usual rate assumed"])
    w.writerow([])
    for line in s["result"]["notes"] + s["result"]["unsure"] + s["caveats"]:
        w.writerow([line])
    return out.getvalue()
