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


def _rows(base):
    """Every sale piece and dividend in the ledger, in the base currency at each date's rate."""
    book = ledger.replay(ledger.transactions())
    sales, dividends, approx = [], [], set()
    for t, p in book.items():
        inf = data.info(t)
        cur = inf.get("currency")
        try:
            fx = data.fx_rate(cur, base)
        except data.DataError:
            approx.add(t)
            continue
        for s in p["sales"]:
            sold_at, a = _sale_fx(cur, base, s["date"], fx)
            bought_at, b = _lot_fx(s, cur, base, fx)
            proceeds, cost = s["shares"] * s["price"] * sold_at, s["shares"] * s["cost"] * bought_at
            held = (dt.date.fromisoformat(s["date"]) - dt.date.fromisoformat(s["bought"])).days if s["bought"] else None
            sales.append({"ticker": t, "name": inf.get("name"), "date": s["date"], "bought": s["bought"] or None, "shares": s["shares"],
                          "price": s["price"], "cost": s["cost"], "currency": cur, "rateSold": sold_at, "rateBought": bought_at,
                          "proceeds": proceeds, "costBase": cost, "gain": proceeds - cost, "heldDays": held,
                          "long": held is not None and held > LONG_DAYS, "exact": a and b})
            if not (a and b):
                approx.add(t)
        pay = data.main_unit(cur)
        for d in p["dividends"]:
            at, a = _sale_fx(pay, base, d["date"], fx if pay == cur else data.fx_rate(pay, base))
            fund = inf.get("assetClass") == "ETFs"
            country = country_of(t)
            dividends.append({"ticker": t, "name": inf.get("name"), "date": d["date"], "amount": d["amount"], "currency": pay, "rate": at,
                              "inBase": d["amount"] * at, "country": country, "fund": fund and "." in t, "exact": a})
            if not a:
                approx.add(t)
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
            "caveats": CAVEATS}


def to_csv(s):
    """The year's lines as a spreadsheet: sales, then dividends, each with the rates used."""
    out = io.StringIO()
    w = csv.writer(out)
    b = s["base"]
    w.writerow(["Sales", s["year"], f"amounts in {b} unless named", "first in, first out"])
    w.writerow(["Date sold", "Ticker", "Name", "Shares", "Date bought", "Days held", "Currency", "Cost per share", "Price per share",
                f"Rate bought ({b} per unit)", f"Rate sold ({b} per unit)", f"Cost ({b})", f"Proceeds ({b})", f"Gain ({b})", "Rate exact"])
    for r in s["sales"]:
        w.writerow([r["date"], r["ticker"], r["name"], f"{r['shares']:.6g}", r["bought"] or "unknown", "" if r["heldDays"] is None else r["heldDays"],
                    r["currency"], f"{r['cost']:.6g}", f"{r['price']:.6g}", f"{r['rateBought']:.6g}", f"{r['rateSold']:.6g}",
                    f"{r['costBase']:.2f}", f"{r['proceeds']:.2f}", f"{r['gain']:.2f}", "yes" if r["exact"] else "no: today's rate"])
    w.writerow(["Total", "", "", "", "", "", "", "", "", "", "", "", f"{s['proceeds']:.2f}", f"{s['net']:.2f}"])
    w.writerow([])
    w.writerow(["Dividends", s["year"], "as received from the broker"])
    w.writerow(["Date", "Ticker", "Name", "Country listed", "Amount", "Currency", f"Rate ({b} per unit)", f"Amount ({b})", "Rate exact"])
    for d in s["dividends"]:
        w.writerow([d["date"], d["ticker"], d["name"], d["country"] or "", f"{d['amount']:.2f}", d["currency"], f"{d['rate']:.6g}",
                    f"{d['inBase']:.2f}", "yes" if d["exact"] else "no: today's rate"])
    w.writerow(["Total", "", "", "", "", "", "", f"{s['dividendTotal']:.2f}"])
    return out.getvalue()
