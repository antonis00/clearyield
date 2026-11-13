"""Each tax residence's rules for a private investor, applied to the tax engine's figures (taxcalc.py).

Every country is a `Country`: its currency and tax year, the matching method per kind of asset, the questions whose
answers change the tax (`options`, such as a marginal income tax rate or whether the broker is at home), and a
`compute()` that turns one tax year into lines (what's taxed, at what rate, under which rule), the forms and boxes the
figures go in, deadlines, losses carried into the next year and notes. Figures were checked for tax year 2026 on
`VERIFIED`, from the sources listed with each country (official pages first); anything the sources didn't settle is
said so on the page (`unsure`), never silently assumed. This is arithmetic on your records under stated rules, not
tax advice: the page and the report say so.
"""
import datetime as dt

VERIFIED = "2026-11-13"

# The tax a source country usually keeps from a dividend paid to someone living abroad, as a share of the gross,
# under the usual treaty (with the form it needs). Used only when you didn't enter what was withheld, and said so.
SOURCE_WITHHOLDING = {
    "United States": 0.15, "Canada": 0.15, "United Kingdom": 0.0, "Germany": 0.26375, "Netherlands": 0.15, "Switzerland": 0.35,
    "France": 0.128, "Ireland": 0.25, "Spain": 0.19, "Italy": 0.26, "Belgium": 0.30, "Portugal": 0.25, "Austria": 0.275,
    "Denmark": 0.27, "Sweden": 0.30, "Finland": 0.35, "Norway": 0.25, "Japan": 0.15315, "Australia": 0.30, "Hong Kong": 0.0,
    "Singapore": 0.0, "Greece": 0.05, "Cyprus": 0.0,
}
TREATY_CAP = 0.15  # most treaties cap portfolio dividends at 15% of the gross; credit above that is reclaimed at source, not at home

FUND_BOND_WORDS = ("bond", "treasury", "treasuries", "gilt", "aggregate", "govt", "government", "corporate", "credit", "fixed income",
                   "tips", "inflation-linked", "inflation linked", "money market", "short duration", "high yield")
FUND_MIXED_WORDS = ("lifestrategy", "allocation", "balanced", "mixed", "multi-asset", "multi asset", "portfolio")
FUND_OTHER_WORDS = ("gold", "silver", "commodit", "physical", "bitcoin", "ether", "crypto")
FUND_KINDS = ("equity", "mixed", "bond", "other")
DOMICILES = ("EU", "US", "other")


def fund_profile(ticker, inf, overrides=None):
    """(kind, domicile, assumed): a fund's kind (equity, mixed, bond, other) from its name, and its domicile from where
    it's listed (a European listing is nearly always an Irish or Luxembourg UCITS fund; a US listing a US fund).
    Your own answer in the Tax year card (`fund_tax`) stands over both; `assumed` says whether one was guessed."""
    o = (overrides or {}).get(ticker) or {}
    name = (inf.get("name") or "").lower()
    kind = ("other" if any(w in name for w in FUND_OTHER_WORDS) else "bond" if any(w in name for w in FUND_BOND_WORDS)
            else "mixed" if any(w in name for w in FUND_MIXED_WORDS) else "equity")
    suffix = ticker.rsplit(".", 1)[1] if "." in ticker else ""
    dom = "EU" if suffix in ("L", "DE", "AS", "PA", "MI", "SW", "BR", "MC", "LS", "VI", "IR", "F", "XC", "HE", "CO", "ST", "OL") else "US" if not suffix else "other"
    return o.get("kind") or kind, o.get("domicile") or dom, not (o.get("kind") and o.get("domicile"))


class Result:
    """One tax year's outcome, built line by line so every figure carries the rule it came from."""

    def __init__(self, ctx):
        self.ctx, self.lines, self.forms, self.notes, self.unsure, self.deadlines = ctx, [], [], [], [], []
        self.allowances, self.carry, self.extra = [], {}, {}

    def line(self, label, base, rate=None, tax=None, rule="", form=None, kind="tax"):
        """A taxed amount: `base` at `rate` gives `tax` (worked out when not given). `kind` "info" lines are shown
        but not added up (a tax your broker already took, a value to declare)."""
        if tax is None and rate is not None:
            tax = max(base, 0) * rate
        self.lines.append({"label": label, "base": base, "rate": rate, "tax": tax, "rule": rule, "form": form, "kind": kind})
        return tax or 0.0

    def form(self, form, box, label, amount=None, note=""):
        self.forms.append({"form": form, "box": box, "label": label, "amount": amount, "note": note})

    def allowance(self, name, amount, used):
        self.allowances.append({"name": name, "amount": amount, "used": min(max(used, 0), amount), "left": max(amount - max(used, 0), 0)})

    def out(self):
        total = sum(x["tax"] or 0 for x in self.lines if x["kind"] == "tax")
        return {"lines": self.lines, "tax": total, "forms": self.forms, "notes": self.notes, "unsure": self.unsure,
                "deadlines": self.deadlines, "allowances": self.allowances, "carry": self.carry, **self.extra}


# ---------------------------------------------------------------- helpers shared by the countries

def by_cat(ctx, *cats):
    return [p for p in ctx["pieces"] if p["category"] in cats]


def net(pieces):
    g = sum(p["gain"] for p in pieces if p["gain"] > 0)
    loss = sum(p["gain"] for p in pieces if p["gain"] < 0)
    return g, loss, g + loss


def gross_and_withheld(d):
    """(gross, withheld, estimated) for a dividend in the tax currency: what you entered, or, when you didn't enter what
    was withheld, the source country's usual rate worked back from what arrived (a European-listed fund: none)."""
    if d["withheldKnown"]:
        return d["gross"], d["withheld"], False
    if d.get("fund") and d.get("domicile") == "EU":
        return d["net"], 0.0, True
    w = {**SOURCE_WITHHOLDING, **(d.get("sourceRates") or {})}.get(d.get("country") or "", 0.0)
    gross = d["net"] / (1 - w) if w < 1 else d["net"]
    return gross, gross - d["net"], True


def credit(withheld, gross, home_rate, cap=TREATY_CAP):
    """The usual foreign tax credit: what was withheld, no more than the treaty rate (the rest is reclaimed from the
    source country) and no more than the home tax on the same dividend."""
    return min(withheld, cap * gross, home_rate * gross)


def bands(amount, table):
    """Tax on `amount` under progressive `table` [(up to, rate)], the last with None for no limit."""
    tax, low = 0.0, 0.0
    for top, rate in table:
        hi = amount if top is None else min(amount, top)
        if hi > low:
            tax += (hi - low) * rate
        if top is None or amount <= top:
            break
        low = top
    return tax


def carry_losses(pool, gain, years=None, year=None):
    """Use carried losses [{year, amount>0}] against `gain`, oldest first, dropping those older than `years`.
    Returns (gain left, used, pool left)."""
    pool = [dict(x) for x in pool if years is None or year is None or year - x["year"] <= years]
    used = 0.0
    for x in pool:
        if gain <= 0:
            break
        take = min(x["amount"], gain)
        x["amount"] -= take
        gain -= take
        used += take
    return gain, used, [x for x in pool if x["amount"] > 1e-9]


def opt(ctx, key):
    return ctx["options"].get(key)


def dividend_rows(ctx, cats=None):
    out = []
    for d in ctx["dividends"]:
        if cats and d["category"] not in cats:
            continue
        g, w, est = gross_and_withheld(d)
        out.append({**d, "g": g, "w": w, "estimated": est})
    return out


def note_estimates(res, divs):
    est = sorted({d["ticker"] for d in divs if d["estimated"]})
    if est:
        res.notes.append(f"Tax withheld abroad wasn't entered for {', '.join(est)}: the source country's usual treaty rate was assumed. "
                         "Enter what your statement shows (the dividend's Tax withheld field) for exact figures.")


def foreign_broker(ctx):
    return opt(ctx, "broker") == "foreign"


BROKER_OPTION = {"key": "broker", "label": "Your broker", "type": "select", "default": "foreign",
                 "choices": [["foreign", "Abroad (you declare and pay yourself)"], ["home", "In the country (it withholds for you)"]]}


def marginal_option(default, label="Your marginal income tax rate (%)"):
    return {"key": "marginal", "label": label, "type": "number", "default": default, "min": 0, "max": 60,
            "help": "The rate on your last euro of income, for the parts taxed as income."}


# ---------------------------------------------------------------- France

FR_PFU, FR_IR, FR_PS = 0.314, 0.128, 0.186
FR_CRYPTO_FLOOR = 305


def _fr_crypto(ctx):
    """Crypto under article 150 VH bis: each sale's gain is the price less the portfolio's total cost in the proportion
    the price bears to the whole crypto portfolio's value just before the sale."""
    from . import taxcalc
    evs, out = ctx["events"], []
    crypto = {t: e for t, e in evs.items() if e["category"] == "crypto"}
    if not crypto:
        return out
    seq = sorted((t["date"] or "", 0 if t["kind"] == "buy" else 1, tk, t) for tk, e in crypto.items() for t in e["trades"])
    total_cost = 0.0
    for date, _, tk, t in seq:
        if t["kind"] == "buy":
            total_cost += t["value"]
            continue
        held = []
        for k, e in crypto.items():
            n = sum(x["shares"] if x["kind"] == "buy" else -x["shares"] for x in e["trades"] if (x["date"], x["kind"] == "sell", x["id"] or 0) < (date, True, t["id"] or 0))
            if n > 1e-12:
                if k == tk:
                    held.append(n * t["value"] / t["shares"])
                else:
                    v = taxcalc.value_on({k: e}, date, ctx["currency"])
                    held.append(v[0]["value"] if v and v[0]["value"] is not None else 0.0)
        whole = sum(held)
        share = t["value"] / whole if whole > 0 else 1.0
        cost = total_cost * min(share, 1.0)
        total_cost -= cost
        if date[:4] == str(ctx["year"]):
            out.append({"ticker": tk, "date": date, "proceeds": t["value"], "cost": cost, "gain": t["value"] - cost, "portfolio": whole})
    return out


def france(ctx):
    r = Result(ctx)
    progressive = opt(ctx, "progressive")
    m = (opt(ctx, "marginal") or 30) / 100
    securities = by_cat(ctx, "shares", "funds")
    g, loss, n = net(securities)
    gain, used, pool = carry_losses(ctx["carry"].get("fr_losses", []), max(n, 0), 10, ctx["year"])
    if n < 0:
        pool.append({"year": ctx["year"], "amount": -n})
    rate_ir = m if progressive else FR_IR
    r.line("Gains on shares and funds, after losses", gain, rate_ir, rule=(f"Income tax at your {m:.0%} marginal rate (option 2OP)" if progressive
           else "Income tax part of the flat tax (PFU): 12.8%"), form="2042-C 3VG")
    r.line("Social charges on those gains", gain, FR_PS, rule="CSG 10.6% + CRDS 0.5% + solidarity levy 7.5% = 18.6% (from 2026)")
    if used:
        r.notes.append(f"{used:,.0f} of losses from earlier years (kept 10 years) was used.")
    r.carry["fr_losses"] = pool
    if n >= 0:
        r.form("2042-C", "3VG", "Net gain on securities", gain)
    else:
        r.form("2042-C", "3VH", "Net loss on securities", -n, "box number to check: the sources didn't confirm it")
    r.form("2074", "", "Detail of each sale (not needed when your broker's IFU gives the figures)")
    # Crypto: the global portfolio formula, its own pot, no carrying forward
    cr = _fr_crypto(ctx)
    proceeds = sum(x["proceeds"] for x in cr)
    cnet = sum(x["gain"] for x in cr)
    if cr:
        if proceeds <= FR_CRYPTO_FLOOR:
            r.line("Crypto gains", cnet, 0, 0, rule=f"Exempt: sales totalled {proceeds:,.0f}, at most €{FR_CRYPTO_FLOOR} in the year")
        else:
            r.line("Crypto gains (global portfolio formula)", max(cnet, 0), FR_PFU, rule="Article 150 VH bis: 31.4% flat; losses only against the same year's crypto gains")
        r.form("2042-C", "3AN" if cnet >= 0 else "3BN", "Crypto net gain" if cnet >= 0 else "Crypto net loss", abs(cnet))
        r.form("2086", "", "Detail of each crypto sale")
        r.extra["cryptoSales"] = cr
    # Dividends: gross, the foreign tax credited against the income tax part only
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    cr_total = sum(credit(d["w"], d["g"], m if progressive else FR_IR) for d in divs if d.get("country") != "France")
    taxed = gross * 0.6 if progressive else gross
    r.line("Dividends: income tax", taxed, rate_ir, rule="40% allowance, then your marginal rate (2OP)" if progressive else "12.8% on the gross dividend")
    if cr_total:
        r.line("Foreign tax credited", -cr_total, None, -cr_total, rule="What was withheld abroad, up to the treaty rate (15% for the US) and the French income tax on it")
    r.line("Dividends: social charges", gross, FR_PS, rule="18.6% on the gross dividend, not reduced by foreign tax")
    note_estimates(r, divs)
    if gross:
        r.form("2042", "2DC", "Dividends (gross)", gross)
        r.form("2042", "8VL", "Tax credit for foreign withholding", cr_total)
        r.form("2047", "", "Foreign income, by country, before it goes on 2042")
    if foreign_broker(ctx):
        r.form("3916 / 3916-bis", "", "Each account abroad (broker, crypto platform), every year")
        if gross:
            r.notes.append("With a broker abroad you pay the 12.8% advance and the social charges on dividends yourself, with form 2778-DIV, "
                           "by the 15th of the month after payment (unless your income is under the exemption threshold).")
            r.unsure.append("The income threshold for not paying the 12.8% advance wasn't confirmed for 2026.")
    r.line("Financial transaction tax (TTF)", sum(t["value"] for t in ctx["buys"] if t.get("ttf")), 0.004, kind="info",
           rule="0.4% on purchases of French companies worth over €1bn; your broker charges it")
    if progressive:
        r.notes.append("Option 2OP is yearly and covers all your flat-tax income: gains, dividends and interest together.")
    r.deadlines += ["Return for 2026 income: May to early June 2027, the date set by your département.",
                    "Tax due on the notice sent in summer 2027."]
    r.unsure.append("The exchange rate rule (each side at its own date's rate) and box 3VH weren't confirmed in an official text.")
    return r.out()


# ---------------------------------------------------------------- Belgium

BE_CGT, BE_EXEMPT, BE_CARRY_UP, BE_CARRY_MAX = 0.10, 10000, 1000, 15000
BE_DIV, BE_DIV_EXEMPT = 0.30, 833
BE_TOB = {"shares": (0.0035, 1600), "fund_EU": (0.0012, 1300), "fund_US": (0.0035, 1600), "fund_other": (0.0035, 1600)}


def belgium(ctx):
    r = Result(ctx)
    pieces = [p for p in ctx["pieces"] if p["date"] >= "2026-01-01"]
    # Assets bought before 2026: their 31 Dec 2025 value is the cost, or the price paid if higher (until 2030)
    stepped = 0.0
    for p in pieces:
        if (p.get("bought") or "") < "2026-01-01":
            v = ctx["value_at"](p["ticker"], "2025-12-31")
            if v is not None and v * p["shares"] > p["cost"]:
                stepped += v * p["shares"] - p["cost"]
                p["gain"] = p["proceeds"] - v * p["shares"]
                p["rule"] += "; cost raised to the 31 Dec 2025 value"
    bond_part = 0.0
    for p in pieces:  # Reynders: the bond part of a gain on a fund holding over 10% in bonds is taxed as interest instead
        if p["category"] == "funds" and p.get("fundKind") in ("bond", "mixed") and p["gain"] > 0:
            share = 1.0 if p["fundKind"] == "bond" else (opt(ctx, "mixed_bonds") or 40) / 100
            bond_part += p["gain"] * share
    g, loss, n = net(pieces)
    n -= bond_part
    room = BE_EXEMPT + min(sum(ctx["carry"].get("be_unused", [])), BE_CARRY_MAX - BE_EXEMPT)
    taxed = max(n - room, 0)
    r.allowance("Yearly exemption (with unused exemption carried in)", room, max(n, 0))
    r.line("Gains on financial assets (shares, funds, crypto), after the year's losses", taxed, BE_CGT,
           rule=f"10% above €{room:,.0f} exempt; losses only against the same year's gains, not carried")
    unused = ctx["carry"].get("be_unused", [])[-4:] + ([min(BE_CARRY_UP, max(room - max(n, 0), 0))] if n < BE_EXEMPT and ctx["year"] >= 2026 else [])
    r.carry["be_unused"] = unused
    if stepped:
        r.notes.append(f"Holdings bought before 2026 count at their value on 31 Dec 2025 where that's above what you paid ({stepped:,.0f} added to cost).")
    if bond_part:
        r.line("Reynders tax: the bond part of fund gains", bond_part, 0.30, rule="30% on the debt-income part (the fund's TIS per unit if your broker gives it; here the gain times the bond share)",
               form="1444/2444")
        r.unsure.append("The Reynders tax uses the fund's own TIS per unit when published; the bond share here is from the fund's kind.")
    divs = dividend_rows(ctx)
    share_divs = sum(d["g"] - d["w"] for d in divs if d["category"] == "shares")
    fund_divs = sum(d["g"] - d["w"] for d in divs if d["category"] != "shares")
    exempt = min(share_divs, BE_DIV_EXEMPT)
    r.allowance("Dividend exemption (ordinary shares)", BE_DIV_EXEMPT, share_divs)
    r.line("Dividends after foreign withholding", share_divs - exempt + fund_divs, BE_DIV,
           rule="30% on what's left after the source country's tax (no credit for it); the first €833 of share dividends exempt")
    note_estimates(r, divs)
    if not foreign_broker(ctx) and exempt:
        r.form("Tax return", "1437/2437", "Withholding on exempt dividends, to claim back (at most €249.90)", exempt * BE_DIV)
    tob = 0.0
    for t in ctx["trades"]:
        key = "shares" if t["category"] != "funds" else f"fund_{t.get('domicile') or 'EU'}"
        if t["category"] == "crypto":
            continue
        rate, cap = BE_TOB[key]
        tob += min(t["value"] * rate, cap)
    r.line("Stock exchange tax (TOB) on the year's trades", tob, None, tob, kind="tax" if foreign_broker(ctx) else "info",
           rule="0.35% on shares and non-EEA funds (max €1,600), 0.12% on EEA funds not registered in Belgium (max €1,300), per trade",
           form="TOB return" if foreign_broker(ctx) else None)
    if foreign_broker(ctx):
        r.notes.append("With a broker abroad you file and pay the TOB yourself, by the end of the second month after each trade.")
        r.notes.append("Declare the capital gains, dividends and any Reynders amount on your return; report the foreign account to the Central Point of Contact.")
    end_value = ctx["year_end_value"]
    if end_value and end_value > 1_000_000:
        r.line("Securities account tax", end_value, 0.003, kind="info", rule="0.30% (from 2 June 2026) on accounts averaging over €1m")
    r.deadlines += ["Return for income year 2026 (assessment year 2027): around 30 June 2027 online (check the year's date).",
                    "TOB with a broker abroad: by the end of the second month after the trade."]
    r.unsure += ["The new capital gains tax's matching rule wasn't stated in the sources: first in, first out is used.",
                 "Return codes for the new capital gains tax weren't published when this was checked.",
                 "Distributing funds' TOB can be 0.35% for some funds: check each fund."]
    return r.out()


# ---------------------------------------------------------------- Spain

ES_BANDS = [(6000, 0.19), (50000, 0.21), (200000, 0.23), (300000, 0.27), (None, 0.30)]
ES_WASH_DAYS = 61  # two months before or after, for listed securities


def spain(ctx):
    r = Result(ctx)
    deferred = 0.0
    for p in ctx["pieces"]:  # the two-month rule: a loss with the same security bought again within two months waits
        if p["gain"] < 0 and p["category"] != "crypto" and ctx["bought_near"](p["ticker"], p["date"], ES_WASH_DAYS, p):
            deferred += -p["gain"]
            p["deferred"] = -p["gain"]
            p["gain"] = 0.0
            p["rule"] += "; loss deferred (bought again within two months)"
    g, loss, gains = net(ctx["pieces"])
    divs = dividend_rows(ctx)
    income = sum(d["g"] for d in divs)
    pool = ctx["carry"].get("es_gain_losses", [])
    ipool = ctx["carry"].get("es_income_losses", [])
    gains, used, pool = carry_losses(pool, gains, 4, ctx["year"]) if gains > 0 else (gains, 0.0, [x for x in pool if ctx["year"] - x["year"] <= 4])
    # A net loss on gains offsets up to 25% of dividends and interest, and the other way round
    if gains < 0:
        off = min(-gains, 0.25 * income)
        income -= off
        gains += off
        if gains < 0:
            pool = pool + [{"year": ctx["year"], "amount": -gains}]
            gains = 0.0
    r.carry["es_gain_losses"] = pool
    r.carry["es_income_losses"] = ipool
    base = max(gains, 0) + max(income, 0)
    tax = bands(base, ES_BANDS)
    r.line("Savings income: gains and dividends together", base, None, tax,
           rule="Savings bands: 19% to €6,000, 21% to €50,000, 23% to €200,000, 27% to €300,000, 30% above", form="Modelo 100")
    avg = tax / base if base else 0
    cr = sum(credit(d["w"], d["g"], avg) for d in divs if d.get("country") != "Spain")
    if cr:
        r.line("Deduction for tax paid abroad", -cr, None, -cr, rule="What was withheld, up to the treaty rate and the Spanish tax on that income (art. 80 LIRPF)")
    if not foreign_broker(ctx):
        withheld = sum(d["g"] - d["w"] for d in divs) * 0.19
        r.line("Withheld at home by your broker (19% on dividends)", withheld, None, -withheld, rule="Already paid; it comes off what's due")
    note_estimates(r, divs)
    if deferred:
        r.notes.append(f"{deferred:,.0f} of losses waits because the same securities were bought within two months; it counts when those are sold.")
    r.carry["es_deferred"] = ctx["carry"].get("es_deferred", 0) + deferred
    if used:
        r.notes.append(f"{used:,.0f} of losses from the past four years was used.")
    r.form("Modelo 100", "0029", "Dividends: gross, withholding", sum(d["g"] for d in divs))
    r.form("Modelo 100", "0328", "Sales of listed shares and ETFs (box to check: guides differ)", gains)
    ev = ctx["year_end_value"] or 0
    if foreign_broker(ctx):
        abroad = ctx["year_end_by_cat"]
        if abroad.get("shares", 0) + abroad.get("funds", 0) > 50000:
            r.form("Modelo 720", "", "Securities held abroad over €50,000 at 31 Dec: file by 31 March")
        if abroad.get("crypto", 0) > 50000:
            r.form("Modelo 721", "", "Crypto held abroad over €50,000 at 31 Dec: file by 31 March")
    if ev > 700000:
        r.line("Wealth tax: value at 31 Dec (exemption €700,000; regions vary)", ev, kind="info", rule="State scale 0.2% to 3.5% above the exemption; Modelo 714")
    if ev > 3_000_000:
        r.line("Solidarity tax on large fortunes: value at 31 Dec", ev, kind="info", rule="1.7% to 3.5% above €3m; Modelo 718")
    r.line("Financial transaction tax", sum(t["value"] for t in ctx["buys"] if t.get("ftt_es")), 0.002, kind="info",
           rule="0.2% on purchases of Spanish companies worth over €1bn; your broker charges it")
    r.deadlines += ["Renta 2026: April to 30 June 2027.", "Modelo 720/721: 1 January to 31 March 2027."]
    r.unsure += ["Box 0328 for sales: guides differ.", "The foreign tax deduction's formula wasn't checked in an official text."]
    return r.out()


# ---------------------------------------------------------------- Italy

IT_RATE = 0.26
IT_CRYPTO = {2025: 0.26, 2026: 0.33}


def italy(ctx):
    r = Result(ctx)
    y = ctx["year"]
    m = (opt(ctx, "marginal") or 35) / 100
    shares = by_cat(ctx, "shares")
    funds = by_cat(ctx, "funds")
    crypto = by_cat(ctx, "crypto")
    eu_funds = [p for p in funds if p.get("domicile") == "EU"]
    other_funds = [p for p in funds if p.get("domicile") != "EU"]
    # Redditi diversi: share gains and losses, crypto, and losses on funds (but not fund gains) in one pot, 4 years
    fund_gain = sum(p["gain"] for p in eu_funds if p["gain"] > 0)
    fund_loss = sum(p["gain"] for p in eu_funds if p["gain"] < 0)
    diversi = sum(p["gain"] for p in shares) + fund_loss
    crypto_net = sum(p["gain"] for p in crypto)
    pool = ctx["carry"].get("it_losses", [])
    d_gain, used, pool = carry_losses(pool, max(diversi, 0), 4, y)
    if diversi < 0:
        pool = pool + [{"year": y, "amount": -diversi}]
    c_gain, c_used, pool = carry_losses(pool, max(crypto_net, 0), 4, y)
    if crypto_net < 0:
        pool = pool + [{"year": y, "amount": -crypto_net}]
    r.carry["it_losses"] = pool
    r.line("Share gains after losses (redditi diversi)", d_gain, IT_RATE, rule="26% substitute tax; losses kept 4 years", form="Quadro RT")
    r.line("Gains on EU funds and ETFs (redditi di capitale)", fund_gain, IT_RATE, rule="26%; losses can't reduce them", form="Quadro RM")
    if other_funds:
        og = sum(p["gain"] for p in other_funds)
        r.line("Gains on non-EU funds (e.g. US ETFs)", max(og, 0), m, rule=f"Taxed as income at your {m:.0%} marginal rate (IRPEF)")
    if crypto:
        rate = IT_CRYPTO.get(y, 0.33 if y > 2026 else 0.26)
        r.line("Crypto gains", c_gain, rate, rule=f"{rate:.0%} in {y}; the €2,000 threshold was abolished from 2025", form="Quadro RT")
    if used or c_used:
        r.notes.append(f"{used + c_used:,.0f} of losses from the past four years was used.")
    divs = dividend_rows(ctx)
    if foreign_broker(ctx):
        gross = sum(d["g"] for d in divs)
        r.line("Dividends (declared: 26% on the gross)", gross, IT_RATE, rule="The Agenzia allows no credit for foreign withholding on dividends taxed at 26%", form="Quadro RM")
        r.unsure.append("A 2022 Cassazione ruling allows a foreign tax credit where the treaty doesn't exclude it; the Agenzia disagrees.")
    else:
        r.line("Dividends (your broker withholds 26% on what arrives)", sum(d["g"] - d["w"] for d in divs), IT_RATE, kind="info", rule="Withheld at source in the regime amministrato")
    note_estimates(r, divs)
    if foreign_broker(ctx):
        held_value = ctx["year_end_by_cat"]
        fin = held_value.get("shares", 0) + held_value.get("funds", 0)
        r.line("IVAFE: foreign financial assets at 31 Dec", fin, 0.002, rule="0.2% a year (in proportion to the days held)", form="Quadro RW / W, F24 code 4043")
        if held_value.get("crypto"):
            r.line("IVCA: crypto at 31 Dec", held_value["crypto"], 0.002, rule="0.2% a year")
        r.form("Quadro RW (Redditi PF) / Quadro W (730)", "", "Every foreign asset and crypto, with its value")
    r.line("Tobin tax", sum(t["value"] for t in ctx["buys"] if t.get("tobin")), 0.002, kind="info" if not foreign_broker(ctx) else "tax",
           rule="0.2% on purchases of Italian shares worth over €500m (2026)", form="F24 code 4058" if foreign_broker(ctx) else None)
    r.deadlines += ["Balance and first instalment: 30 June 2027 (or 30 July with 0.4% more).", "Modello 730: by 30 September 2027; Redditi PF: by 31 October 2027."]
    r.unsure += ["Weighted average cost is used; for holdings declared yourself some sources say LIFO.",
                 "Whether Redditi PF also moved crypto to the new Quadro W isn't settled."]
    return r.out()


# ---------------------------------------------------------------- Portugal

PT_RATE = 0.28
PT_AGGREGATE_FROM = 86634


def portugal(ctx):
    r = Result(ctx)
    secs = by_cat(ctx, "shares", "funds")
    crypto = by_cat(ctx, "crypto")
    short = sum(p["gain"] for p in secs if p["heldDays"] is not None and p["heldDays"] < 365)
    g, loss, n = net(secs)
    income = opt(ctx, "income") or 0
    aggregate = opt(ctx, "aggregate")
    if short > 0 and income + short >= PT_AGGREGATE_FROM:
        m = (opt(ctx, "marginal") or 48) / 100
        r.line("Gains on securities held under a year (must be added to income)", short, m, rule=f"Taxable income with them is over €{PT_AGGREGATE_FROM:,}: your marginal rate")
        n -= short
    pool = ctx["carry"].get("pt_losses", [])
    if aggregate:
        n, used, pool = carry_losses(pool, max(n, 0), 5, ctx["year"]) if n > 0 else (n, 0.0, pool)
        if n < 0:
            pool = pool + [{"year": ctx["year"], "amount": -n}]
    r.carry["pt_losses"] = pool if aggregate else []
    r.line("Gains on shares and funds", max(n, 0), PT_RATE, rule="28% special rate (or your income scale if you opt to aggregate); losses carried 5 years only when aggregated",
           form="Anexo J quadro 9.2A, code G10" if foreign_broker(ctx) else "Anexo G quadro 9, code G01")
    short_c = [p for p in crypto if p["heldDays"] is None or p["heldDays"] < 365]
    long_c = [p for p in crypto if p["heldDays"] is not None and p["heldDays"] >= 365]
    if crypto:
        r.line("Crypto held under 365 days", max(sum(p["gain"] for p in short_c), 0), PT_RATE, form="Anexo G quadro 18")
        if long_c:
            r.line("Crypto held 365 days or more", sum(p["gain"] for p in long_c), 0, 0, rule="Exempt, but still reported", form="Anexo G1 quadro 7")
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    r.line("Dividends", gross, PT_RATE, rule="28% on the gross", form="Anexo J quadro 8" if foreign_broker(ctx) else "Withheld by your bank")
    cr = sum(credit(d["w"], d["g"], PT_RATE) for d in divs if d.get("country") != "Portugal")
    if cr and foreign_broker(ctx):
        r.line("Credit for tax paid abroad", -cr, None, -cr, rule="The lower of the foreign tax (treaty rate) and the Portuguese tax on it")
    note_estimates(r, divs)
    r.deadlines += ["IRS for 2026: 1 April to 30 June 2027."]
    r.unsure += ["PwC lists a 10%/20%/30% exclusion of gains on securities held 2-5, 5-8 and 8+ years; its conditions weren't confirmed, so it isn't applied.",
                 f"The €{PT_AGGREGATE_FROM:,} threshold for 2026 is from secondary sources."]
    return r.out()


# ---------------------------------------------------------------- the list

COUNTRIES = {
    "FR": {"name": "France", "currency": "EUR", "year": "calendar", "methods": {"shares": "average", "funds": "average", "crypto": "average"},
           "compute": france, "options": [BROKER_OPTION, {"key": "progressive", "label": "Opt for the progressive scale (2OP)", "type": "bool", "default": False},
                                           marginal_option(30)],
           "sources": ["https://www.impots.gouv.fr/particulier/questions/comment-declarer-les-plus-ou-moins-values-sur-cessions-dactifs-numeriques",
                       "https://taxsummaries.pwc.com/france/individual/income-determination",
                       "https://www.legifiscal.fr/reperes-fiscaux/flat-tax-pfu-champ-application-taux-applicables-2026.html",
                       "https://bofip.impots.gouv.fr/export/pdf/6667"]},
    "BE": {"name": "Belgium", "currency": "EUR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": belgium, "options": [BROKER_OPTION, {"key": "mixed_bonds", "label": "Bond share of mixed funds (%)", "type": "number", "default": 40, "min": 0, "max": 100}],
           "sources": ["https://kpmg.com/be/en/insights/my-tax-compass/corporate-tax-insights/belgian-capital-gains-tax-approved-by-parliament.html",
                       "https://fin.belgium.be/nl/particulieren/belastingvoordelen/vrijstellingen-dividenden",
                       "https://taxsummaries.pwc.com/belgium/individual/income-determination",
                       "https://www.test-aankoop.be/invest/beleggen/fiscaliteit-and-rechten/dossiers/beurstaks-tob/beurstaks-etf-tob-belgie"]},
    "ES": {"name": "Spain", "currency": "EUR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": spain, "options": [BROKER_OPTION],
           "sources": ["https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025/c12-integracion-compensacion-rentas/reglas-integracion-compensacion-rentas/integracion-compensacion-rentas-base-imponible-ahorro.html",
                       "https://taxsummaries.pwc.com/spain/individual/income-determination",
                       "https://www.cnmv.es/DocPortal/Publicaciones/Guias/Guia_Fiscalidad_Acciones.pdf"]},
    "IT": {"name": "Italy", "currency": "EUR", "year": "calendar", "methods": {"shares": "average", "funds": "average", "crypto": "average"},
           "compute": italy, "options": [BROKER_OPTION, marginal_option(35)],
           "sources": ["https://taxsummaries.pwc.com/italy/individual/income-determination",
                       "https://www.fiscal-focus.it/quotidiano/il-quotidiano/articoli-fisco/cripto-attivita-2026-aliquota-al-33-ma-scatta-una-deroga-per-i-token-di-moneta-elettronica-in-euro,3,180507",
                       "https://www.money.it/ivafe-2026-imposta-sulle-attivita-finanziarie-estere-quanto-si-paga-guida",
                       "https://quifinanza.it/fisco-tasse/tobin-tax-2026/961162/"]},
    "PT": {"name": "Portugal", "currency": "EUR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": portugal, "options": [BROKER_OPTION, {"key": "income", "label": "Your other taxable income (€)", "type": "number", "default": 0, "min": 0},
                                             marginal_option(48), {"key": "aggregate", "label": "Opt to aggregate (englobamento)", "type": "bool", "default": False}],
           "sources": ["https://taxsummaries.pwc.com/portugal/individual/income-determination",
                       "https://info.portaldasfinancas.gov.pt/pt/apoio_contribuinte/Folhetos_informativos/Documents/Criptoativos.pdf",
                       "https://eco.sapo.pt/descodificador/como-declarar-e-poupar-em-irs-os-seus-investimentos-financeiros/01-o-que-muda-este-ano-para-os-investidores-em-bolsa/"]},
}
# ---------------------------------------------------------------- Germany

DE_RATE, DE_SOLI = 0.25, 0.055
DE_TFS = {"equity": 0.30, "mixed": 0.15, "bond": 0.0, "other": 0.0}  # Teilfreistellung for private investors (§ 20 InvStG)
DE_BASISZINS = {2023: 0.0255, 2024: 0.0229, 2025: 0.0253, 2026: 0.0320}  # the Vorabpauschale's base rate for each calendar year (BMF)
DE_ALLOWANCE = {False: 1000, True: 2000}
DE_CRYPTO_FREIGRENZE = 1000


def de_rate(church):
    """The flat tax with the solidarity surcharge and, if paid, church tax (§ 32d(1): (e - 4q) / (4 + k))."""
    k = church or 0.0
    base = DE_RATE / (1 + DE_RATE * k)
    return base * (1 + DE_SOLI + k)


def vorabpauschale(ctx, year):
    """[{ticker, units, perUnit, amount}] for the Vorabpauschale of calendar `year` (taxed as received on the first working day
    of the next one): the 1 January price x the base rate x 0.7, capped at the year's rise plus distributions, less the
    distributions, for each unit held at the year's end; units bought during the year lose 1/12 for each full month before."""
    rate = DE_BASISZINS.get(year)
    out = []
    if rate is None:
        return out
    for tk, e in ctx["events"].items():
        if e["category"] != "funds":
            continue
        start, end = ctx["price_of"](tk, f"{year - 1}-12-31"), ctx["price_of"](tk, f"{year}-12-31")
        if start is None or end is None:
            continue
        units = 0.0
        months = 0.0
        for t in e["trades"]:
            d = t["date"] or ""
            if d > f"{year}-12-31":
                continue
            sign = 1 if t["kind"] == "buy" else -1
            units += sign * t["shares"]
            if sign > 0 and d >= f"{year}-01-01":
                months += t["shares"] * (int(d[5:7]) - 1) / 12  # full months before the month of purchase
        if units <= 1e-9:
            continue
        dist = sum(d["net"] + d["withheld"] for d in e["dividends"] if d["date"][:4] == str(year)) / units
        basis = start * rate * 0.7
        per = max(0.0, min(basis, max(end - start + dist, 0)) - dist)
        amount = per * (units - min(months, units))
        out.append({"ticker": tk, "units": units, "perUnit": per, "amount": amount, "year": year, "kind": e.get("fundKind")})
    return out


def germany(ctx):
    r = Result(ctx)
    y = ctx["year"]
    rate = de_rate((opt(ctx, "church") or 0) / 100)
    allowance = DE_ALLOWANCE[bool(opt(ctx, "joint"))]
    tfs = lambda p: DE_TFS.get(p.get("fundKind") or "equity", 0.0)  # noqa: E731
    shares = by_cat(ctx, "shares")
    funds = by_cat(ctx, "funds")
    # Vorabpauschale already taxed reduces the gain on sale (§ 19 InvStG)
    vp_paid = ctx["carry"].get("de_vp", {})
    for p in funds:
        per = sum(v for yr, v in vp_paid.get(p["ticker"], {}).items() if p.get("bought") and p["bought"] <= f"{yr}-12-31")
        if per:
            p["gain"] -= per * p["shares"]
            p["rule"] += "; less Vorabpauschale already taxed"
    share_gain = sum(p["gain"] for p in shares)
    fund_gain = sum(p["gain"] * (1 - tfs(p)) for p in funds)
    vp = vorabpauschale(ctx, y - 1)
    vp_taxable = sum(v["amount"] * (1 - DE_TFS.get(v["kind"] or "equity", 0)) for v in vp)
    new_paid = {k: dict(v) for k, v in vp_paid.items()}
    for v in vp:
        new_paid.setdefault(v["ticker"], {})[str(y - 1)] = v["perUnit"]
    r.carry["de_vp"] = new_paid
    divs = dividend_rows(ctx)
    div_taxable = sum(d["g"] * (1 - (DE_TFS.get(d.get("fundKind") or "equity", 0) if d["category"] == "funds" else 0)) for d in divs)
    # Two loss pots: shares only against shares, everything else in the general pot; both carried without limit
    share_pot, gen_pot = ctx["carry"].get("de_share_pot", 0.0), ctx["carry"].get("de_gen_pot", 0.0)
    if share_gain >= 0:
        use = min(share_pot, share_gain)
        share_gain -= use
        share_pot -= use
    else:
        share_pot += -share_gain
        share_gain = 0.0
    general = fund_gain + vp_taxable + div_taxable
    if general >= 0:
        use = min(gen_pot, general)
        general -= use
        gen_pot -= use
    else:
        gen_pot += -general
        general = 0.0
    r.carry.update({"de_share_pot": share_pot, "de_gen_pot": gen_pot})
    income = share_gain + general
    used = min(allowance, income)
    r.allowance("Sparer-Pauschbetrag", allowance, income)
    taxed = income - used
    r.line("Capital income after losses and the allowance", taxed, rate,
           rule=f"Abgeltungsteuer 25% + solidarity surcharge 5.5%{f' + church tax {opt(ctx, 'church'):g}%' if opt(ctx, 'church') else ''} = {rate:.3%}")
    r.extra["parts"] = {"shareGains": share_gain, "fundGains": fund_gain, "vorabpauschale": vp_taxable, "dividends": div_taxable}
    if vp:
        r.extra["vorabpauschale"] = vp
        r.notes.append(f"Vorabpauschale for {y - 1} ({DE_BASISZINS[y - 1]:.2%} base rate), treated as received on 2 January {y}: "
                       + ", ".join(f"{v['ticker']} {v['amount']:,.2f}" for v in vp) + " before the partial exemption.")
    if any(p["category"] == "funds" for p in ctx["pieces"]) or vp or any(d["category"] == "funds" for d in divs):
        r.notes.append("Funds' gains, distributions and Vorabpauschale are 30% exempt for equity funds (over 50% shares), 15% for mixed funds (Teilfreistellung).")
    cr = sum(credit(d["w"], d["g"], DE_RATE) for d in divs if d.get("country") != "Germany")
    cap = taxed * DE_RATE
    cr = min(cr, cap)
    if cr:
        r.line("Foreign tax credited", -cr, None, -cr, rule="Up to the treaty rate (15% for the US) and the German tax on each dividend (§ 32d(5))", form="Anlage KAP Z.41")
    note_estimates(r, divs)
    # Crypto: a private sale (§ 23 EStG): tax-free after a year; within a year at your income tax rate, unless the year's total stays under €1,000
    crypto = by_cat(ctx, "crypto")
    if crypto:
        short = [p for p in crypto if not p["long"]]
        sg = sum(p["gain"] for p in short)
        pot = ctx["carry"].get("de_s23_pot", 0.0)
        if sg > 0:
            use = min(pot, sg)
            sg -= use
            pot -= use
        else:
            pot += -sg
            sg = 0.0
        r.carry["de_s23_pot"] = pot
        m = (opt(ctx, "marginal") or 42) / 100
        free = 0 < sg < DE_CRYPTO_FREIGRENZE
        r.line("Crypto sold within a year (§ 23 EStG)", 0 if free else sg, m * (1 + DE_SOLI), rule=(f"Under the €{DE_CRYPTO_FREIGRENZE:,} Freigrenze: tax-free" if free
               else f"Your income tax rate ({m:.0%}) plus solidarity surcharge; all of it once the year's total reaches €{DE_CRYPTO_FREIGRENZE:,}"), form="Anlage SO")
        held = [p for p in crypto if p["long"]]
        if held:
            r.line("Crypto held over a year", sum(p["gain"] for p in held), 0, 0, rule="Tax-free after more than a year")
    if foreign_broker(ctx):
        r.form("Anlage KAP", "Z.19", "Foreign capital income without German withholding (dividends, share gains)", sum(d["g"] for d in divs if d["category"] == "shares") + sum(p["gain"] for p in shares if p["gain"] > 0))
        r.form("Anlage KAP", "Z.20", "Of which share gains", sum(p["gain"] for p in shares if p["gain"] > 0))
        r.form("Anlage KAP", "Z.23", "Share losses", -sum(p["gain"] for p in shares if p["gain"] < 0))
        r.form("Anlage KAP", "Z.41", "Foreign tax not yet credited", cr)
        r.form("Anlage KAP-INV", "Z.4-8", "Fund distributions, by fund type", sum(d["g"] for d in divs if d["category"] == "funds"))
        r.form("Anlage KAP-INV", "Z.9-13", "Vorabpauschale, by fund type", sum(v["amount"] for v in vp))
        r.form("Anlage KAP-INV", "Z.14-28", "Fund sales, by fund type", sum(p["gain"] for p in funds))
        r.notes.append("With a broker abroad nothing is withheld: declare everything, including the Vorabpauschale worked out here.")
    else:
        r.notes.append("A German broker withholds the tax, keeps the loss pots and uses your Freistellungsauftrag; you declare only to correct it (e.g. foreign tax, the Günstigerprüfung).")
    r.form("Anlage SO", "", "Crypto sales within a year") if crypto else None
    r.deadlines += [f"Return for {y}: 31 July {y + 1} if you file yourself (with an adviser, later)."]
    r.unsure += ["Anlage KAP line numbers are from the 2025 forms; the 2026 forms weren't out when this was checked.",
                 "The 2023 base rate (2.55%) is from memory; 2024 to 2026 were checked."]
    return r.out()


# ---------------------------------------------------------------- Austria

AT_RATE = 0.275


def austria(ctx):
    r = Result(ctx)
    y = ctx["year"]
    secs = by_cat(ctx, "shares", "funds")
    crypto = [p for p in by_cat(ctx, "crypto") if not p.get("bought") or p["bought"] > "2021-02-28"]
    old = [p for p in by_cat(ctx, "crypto") if p.get("bought") and p["bought"] <= "2021-02-28"]
    # Non-reporting funds: deemed income each year from the year's prices (§ 186(2) Z3 InvFG)
    deemed = []
    for tk, e in ctx["events"].items():
        if e["category"] != "funds" or e.get("domicile") != "US":
            continue
        units = sum(t["shares"] if t["kind"] == "buy" else -t["shares"] for t in e["trades"] if (t["date"] or "") <= f"{y}-12-31")
        start, end = ctx["price_of"](tk, f"{y - 1}-12-31"), ctx["price_of"](tk, f"{y}-12-31")
        if units > 1e-9 and end is not None:
            per = max(0.9 * (end - start) if start is not None else 0, 0.1 * end)
            deemed.append({"ticker": tk, "units": units, "perUnit": per, "amount": per * units})
    total = sum(p["gain"] for p in secs) + sum(p["gain"] for p in crypto) + sum(d["amount"] for d in deemed)
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    income = total + gross
    r.line("Capital income at the special rate, after the year's losses", max(income, 0), AT_RATE,
           rule="27.5%; losses only against the same year's capital income, never carried forward; no allowance")
    if deemed:
        r.extra["deemedIncome"] = deemed
        r.notes.append("US-listed funds are taken as non-reporting funds: deemed income is the larger of 90% of the year's rise and 10% of the year-end price, per unit. "
                       "Check each on OeKB; a reporting fund's figures come from my.oekb.at instead.")
    if any(e["category"] == "funds" and e.get("domicile") == "EU" for e in ctx["events"].values()):
        r.notes.append("EU funds are taken as reporting funds (Meldefonds): their deemed distributions (ausschüttungsgleiche Erträge) are published per unit on my.oekb.at and "
                       "aren't included here. Add them as income; they raise the fund's cost for the sale.")
        r.unsure.append("Reporting funds' deemed distributions need the OeKB figures, which this app can't fetch.")
    cr = sum(credit(d["w"], d["g"], AT_RATE) for d in divs if d.get("country") != "Austria")
    if cr:
        r.line("Foreign tax credited", -cr, None, -cr, rule="Up to 15% of the gross dividend; above that, reclaim it from the source country", form="E1kv KZ 998")
    note_estimates(r, divs)
    if old:
        r.line("Crypto bought before March 2021", sum(p["gain"] for p in old), 0, 0, rule="Old rules: tax-free after a year (taxed at your income rate within one)")
    r.form("E1kv", "KZ 863", "Foreign dividends", sum(d["g"] for d in divs if d["category"] == "shares"))
    r.form("E1kv", "KZ 994", "Foreign gains", sum(p["gain"] for p in secs if p["gain"] > 0))
    r.form("E1kv", "KZ 892", "Foreign losses", -sum(p["gain"] for p in secs if p["gain"] < 0))
    r.form("E1kv", "KZ 898", "Fund distributions", sum(d["g"] for d in divs if d["category"] == "funds"))
    r.form("E1kv", "KZ 937", "Funds' deemed distributions", sum(d["amount"] for d in deemed))
    r.form("E1kv", "KZ 998", "Creditable foreign tax", cr)
    if not foreign_broker(ctx):
        r.notes.append("An Austrian bank withholds the KESt as final tax, offsets losses within the year and credits foreign tax: nothing to declare for it.")
    r.deadlines += [f"Return for {y}: 30 April {y + 1} on paper, 30 June {y + 1} through FinanzOnline."]
    r.unsure += ["E1kv codes are from a broker's guide, not the BMF; crypto codes weren't confirmed."]
    return r.out()


# ---------------------------------------------------------------- Switzerland

def switzerland(ctx):
    r = Result(ctx)
    y = ctx["year"]
    gains = sum(p["gain"] for p in ctx["pieces"])
    r.line("Gains on shares, funds and crypto", gains, 0, 0, rule="Tax-free for private assets (losses not deductible), unless you count as a professional trader")
    # The safe harbour: held at least a year, turnover at most five times the portfolio, gains under half your income...
    quick = [p for p in ctx["pieces"] if p["heldDays"] is not None and p["heldDays"] < 365]
    start = ctx["year_start_value"] or 0
    turnover = sum(t["value"] for t in ctx["trades"])
    flags = []
    if quick:
        flags.append(f"{len(quick)} sale piece{'s' if len(quick) > 1 else ''} held under a year")
    if start and turnover > 5 * start:
        flags.append(f"turnover {turnover:,.0f} is over five times the portfolio at the start of the year ({start:,.0f})")
    income = opt(ctx, "income") or 0
    if income and gains > 0.5 * income:
        flags.append("gains over half your taxable income")
    if flags:
        r.notes.append("Outside the safe harbour for private investors (" + "; ".join(flags) + "): the canton may class you as a professional trader and tax the gains as income.")
    m = (opt(ctx, "marginal") or 30) / 100
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    r.line("Dividends and fund distributions (income tax)", gross, m, rule=f"Federal, cantonal and communal income tax: about {m:.0%} at your marginal rate")
    cr = sum(credit(d["w"], d["g"], m) for d in divs if d.get("country") not in ("Switzerland",) and not (d.get("fund") and d.get("domicile") == "EU"))
    if cr:
        r.line("Foreign tax credited (form DA-1)", -cr, None, -cr, rule="Up to the treaty rate (15% for the US)", form="DA-1")
    ch = sum(d["w"] for d in divs if d.get("country") == "Switzerland")
    if ch:
        r.line("Swiss withholding tax to reclaim", ch, None, -ch, rule="The 35% Verrechnungssteuer comes back when you declare the dividend")
    note_estimates(r, divs)
    if any(e["category"] == "funds" and not e["dividends"] for e in ctx["events"].values()):
        r.notes.append("Accumulating funds' reinvested income is taxable every year too: the per-unit figure is in the ESTV Kursliste (ictax.admin.ch), which this app can't fetch.")
        r.unsure.append("Accumulating funds' yearly taxable income needs the ESTV Kursliste.")
    end = ctx["year_end_value"]
    if end:
        r.line(f"Wealth to declare at 31 Dec {y}", end, kind="info", rule="Cantonal and communal wealth tax on net worth; the canton's scale applies (use the ESTV year-end prices)")
    r.form("Securities schedule (Wertschriftenverzeichnis)", "", "Each holding's value at 31 Dec and its income", end)
    r.form("DA-1", "", "Foreign tax to credit", cr)
    r.deadlines += [f"Return for {y}: usually 31 March {y + 1} (cantons set their own dates and extensions)."]
    r.unsure += ["Wealth tax scales are cantonal: only the value is given.", "The CHF 100 threshold below which foreign tax isn't credited wasn't confirmed."]
    return r.out()


# ---------------------------------------------------------------- Netherlands

NL_BOX3 = {2025: {"other": 0.0588, "bank": 0.0137, "debt": 0.027, "allowance": 57684, "rate": 0.36, "debtFree": 3800},
           2026: {"other": 0.0600, "bank": 0.0128, "debt": 0.027, "allowance": 59357, "rate": 0.36, "debtFree": 3800}}


def netherlands(ctx):
    r = Result(ctx)
    y = ctx["year"]
    p = NL_BOX3.get(y)
    if not p:
        r.notes.append(f"Box 3 figures for {y} aren't in the app yet (2027's were still before parliament when checked).")
        return r.out()
    other = ctx["year_start_value"] or 0
    bank = opt(ctx, "bank") or 0
    debts = opt(ctx, "debts") or 0
    partners = bool(opt(ctx, "partner"))
    allowance = p["allowance"] * (2 if partners else 1)
    d_over = max(0, debts - p["debtFree"] * (2 if partners else 1))
    deemed = bank * p["bank"] + other * p["other"] - d_over * p["debt"]
    base = bank + other - d_over
    share = max(0.0, (base - allowance) / base) if base > 0 else 0.0
    tax = max(deemed * share, 0) * p["rate"]
    r.line(f"Box 3: deemed return on your investments at 1 Jan {y}", max(deemed * share, 0), p["rate"], tax,
           rule=f"Other assets {p['other']:.2%}, savings {p['bank']:.2%}, debts {p['debt']:.2%}; {share:.0%} of the base is above the €{allowance:,} allowance; 36%",
           form="Aangifte inkomstenbelasting: box 3")
    r.allowance("Box 3 allowance", allowance, base)
    # The counterproof: the actual return, if lower, is taxed instead (no allowance)
    end = ctx["year_end_value"]
    if end is not None and y < dt.date.today().year:
        flows = sum(t["value"] if t["kind"] == "buy" else -t["value"] for t in ctx["trades"])
        divs = sum(d["net"] + d["withheld"] for d in ctx["dividends"])
        actual = end - other - flows + divs
        r.extra["actualReturn"] = actual
        if max(actual, 0) * p["rate"] < tax:
            r.notes.append(f"Your actual return on the investments ({actual:,.0f}) is below the deemed one: you can claim to be taxed on it instead "
                           f"(about {max(actual, 0) * p['rate']:,.0f}), with no allowance. Savings and debts count in it too.")
    elif y >= dt.date.today().year:
        r.notes.append("The actual return can be compared once the year is over.")
    divs = dividend_rows(ctx)
    nl = sum(d["w"] for d in divs if d.get("country") == "Netherlands")
    foreign = sum(credit(d["w"], d["g"], 1.0) for d in divs if d.get("country") != "Netherlands")
    if nl:
        r.line("Dutch dividend tax withheld (credited)", -nl, None, -nl, rule="15% dividendbelasting comes off your income tax")
    if foreign:
        r.line("Foreign tax credited against box 3", -min(foreign, tax), None, -min(foreign, tax), rule="Up to the treaty rate, and no more than the box 3 tax on it")
    note_estimates(r, divs)
    r.notes.append("Gains and dividends aren't taxed separately: box 3 taxes a deemed return on what you hold on 1 January.")
    r.deadlines += [f"Return for {y}: 1 May {y + 1}."]
    r.unsure += [f"The {y} savings and debt rates are provisional until early {y + 1}." if y == 2026 else "",
                 "2027's box 3 figures were still before parliament when checked."]
    r.unsure = [u for u in r.unsure if u]
    return r.out()


# ---------------------------------------------------------------- Greece

GR_DIV, GR_GAIN = 0.05, 0.15


def greece(ctx):
    r = Result(ctx)
    y = ctx["year"]
    shares = by_cat(ctx, "shares")
    funds = by_cat(ctx, "funds")
    big = bool(opt(ctx, "stake"))
    sg = sum(p["gain"] for p in shares)
    if big:
        pool = ctx["carry"].get("gr_losses", [])
        g, used, pool = carry_losses(pool, max(sg, 0), 5, y)
        if sg < 0:
            pool = pool + [{"year": y, "amount": -sg}]
        r.carry["gr_losses"] = pool
        r.line("Share gains (a stake of 0.5% or more)", g, GR_GAIN, rule="15% (art. 42 Law 4172/2013); losses carried 5 years", form="E1 829/830")
    else:
        r.line("Gains on listed shares", sg, 0, 0, rule="Exempt: listed on an exchange (Athens or abroad) and under 0.5% of the company (POL 1032/2015)", form="E1 659/660")
    eu = [p for p in funds if p.get("domicile") == "EU"]
    other = [p for p in funds if p.get("domicile") != "EU"]
    if eu:
        r.line("Gains on EU (UCITS) funds and ETFs", sum(p["gain"] for p in eu), 0, 0, rule="Exempt (art. 103 Law 4099/2012)")
    if other:
        r.line("Gains on non-EU funds (e.g. US ETFs)", max(sum(p["gain"] for p in other), 0), GR_GAIN, rule="15% under art. 42")
        r.unsure.append("US-listed funds at 15% rests on readings of POL 1032/2015.")
    crypto = by_cat(ctx, "crypto")
    if crypto:
        cg = sum(p["gain"] for p in crypto)
        if y >= 2027:
            r.line("Crypto gains", cg if cg > 500 else 0, 0.10, rule="10% from 2027, all of it once the year's net gains are over €500")
        else:
            r.line("Crypto gains", cg, None, None, kind="info", rule=f"No law taxes crypto gains in {y}; the 10% tax starts on 1 January 2027")
            r.unsure.append(f"Crypto in {y}: no specific law; the tax authority refused to treat bitcoin gains as exempt income in one dispute.")
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    home = gross * GR_DIV
    cr = sum(min(d["w"], d["g"] * GR_DIV) for d in divs if d.get("country") != "Greece")
    r.line("Dividends", gross, GR_DIV, rule="5% on the gross; final tax")
    if cr:
        r.line("Foreign tax credited", -cr, None, -cr, rule="Up to the Greek tax on each dividend (the rest isn't refunded)", form="E1 683/684")
    note_estimates(r, divs)
    if gross:
        r.form("E1", "295/296", "Foreign dividends before tax", sum(d["g"] for d in divs if d.get("country") != "Greece"))
        r.form("E1", "683/684", "Tax withheld abroad", cr)
    if not big and sg:
        r.form("E1", "659/660", "Exempt gains on listed shares", sg, "code from published decisions, not AADE's instructions")
    r.notes.append("The US withholds 30% on dividends to residents of Greece (the treaty has no lower rate), and Greece credits only its own 5%.")
    r.deadlines += [f"E1 for {y}: by 15 July {y + 1}; pay by 31 July or in 8 monthly instalments (4% off if filed by 15 May and paid in full)."]
    r.unsure += ["E1 codes come from published decisions and forums, not AADE's own instructions; codes can change each year.",
                 "Fund distributions may count as dividends (5%) or interest (15%); 5% is used."]
    return r.out()


# ---------------------------------------------------------------- Cyprus

def cyprus(ctx):
    r = Result(ctx)
    y = ctx["year"]
    secs = by_cat(ctx, "shares", "funds")
    r.line("Gains on shares and funds", sum(p["gain"] for p in secs), 0, 0, rule="Exempt: titles (fund units until 2030)")
    crypto = by_cat(ctx, "crypto")
    if crypto:
        cg = sum(p["gain"] for p in crypto)
        r.line("Crypto gains", max(cg, 0), 0.08 if y >= 2026 else 0, rule="8% from 2026 (section 20E); losses only against the same year's crypto gains")
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    domiciled = opt(ctx, "domiciled") is not False
    sdc = 0.05 if domiciled else 0.0
    t = r.line("Special defence contribution on dividends", gross, sdc, rule="5% from 2026 for residents domiciled in Cyprus (17% before); none if not domiciled")
    cr = min(sum(d["w"] for d in divs), t)
    if cr:
        r.line("Foreign tax credited", -cr, None, -cr, rule="Against the defence contribution")
    r.line("GESY (health contribution) on dividends", gross, 0.0265, rule="2.65%, domiciled or not (income cap €180,000)")
    note_estimates(r, divs)
    r.form("IR1", "", "Income tax return (every resident aged 25+ files from 2026)")
    r.deadlines += [f"Return for {y}: usually 31 July {y + 1} (the date is often extended)."]
    r.unsure += ["How the 17% rule for Cyprus profits up to 2025 applies to foreign dividends wasn't found.",
                 "When and on which form the defence contribution on foreign dividends is paid wasn't confirmed."]
    return r.out()


# ---------------------------------------------------------------- Ireland

IE_EXEMPT = 1270
IE_FUND = {2025: 0.41, 2026: 0.38, 2027: 0.35}
IE_CUT = "2026-10-07"  # CGT 33% before, 31% from


def ie_cgt_rate(date):
    return 0.31 if date >= IE_CUT else 0.33


def ireland(ctx):
    r = Result(ctx)
    y = ctx["year"]
    equivalent_us = opt(ctx, "us_funds") != "non"
    fund_rate = IE_FUND.get(y, 0.38 if y < 2027 else 0.35)
    cgt = [p for p in ctx["pieces"] if p["category"] != "funds" or (p.get("domicile") == "US" and not equivalent_us) or p.get("domicile") == "other"]
    fund = [p for p in ctx["pieces"] if p not in cgt]
    # CGT: the two 2026 rates; the exemption and losses go first against the gains at the higher rate
    hi = sum(p["gain"] for p in cgt if p["gain"] > 0 and ie_cgt_rate(p["date"]) == 0.33)
    lo = sum(p["gain"] for p in cgt if p["gain"] > 0 and ie_cgt_rate(p["date"]) == 0.31)
    loss = -sum(p["gain"] for p in cgt if p["gain"] < 0) + ctx["carry"].get("ie_losses", 0.0)
    left = [hi, lo]

    def against(amount):  # the 33% gains first, then the 31% ones; returns what's left of the amount
        for i in (0, 1):
            a = min(left[i], amount)
            left[i] -= a
            amount -= a
        return amount
    loss = against(loss)
    against(IE_EXEMPT)
    hi, lo = left
    r.carry["ie_losses"] = loss
    r.allowance("Annual CGT exemption", IE_EXEMPT, sum(p["gain"] for p in cgt if p["gain"] > 0) - (-sum(p["gain"] for p in cgt if p["gain"] < 0)))
    if hi:
        r.line("Gains on shares and crypto to 6 Oct 2026", hi, 0.33, rule="CGT 33%", form="Form 11: CGT")
    if lo or y >= 2026:
        r.line("Gains on shares and crypto from 7 Oct 2026" if y == 2026 else "Gains on shares and crypto", lo if y == 2026 else lo + hi, 0.31, rule="CGT 31%", form="Form 11: CGT")
    if loss:
        r.notes.append(f"{loss:,.0f} of capital losses carries forward, without a time limit.")
    four = [p for p in cgt if p.get("rule", "").startswith("Bought within 4 weeks")]
    if four:
        r.notes.append("Shares sold within four weeks of buying them are matched with those purchases first (last in, first out).")
    rebought = [p for p in cgt if p["gain"] < 0 and ctx["bought_near"](p["ticker"], p["date"], 28, p, after_only=True)]
    if rebought:
        r.notes.append("A loss on shares bought again within four weeks can only be used against the gain on those shares: "
                       + ", ".join(sorted({p["ticker"] for p in rebought})) + ".")
    # Funds: exit tax on gains and distributions, no exemption, losses not relieved; deemed disposal every 8 years
    fg = sum(max(p["gain"], 0) for p in fund)
    r.line("Gains on EU and equivalent funds (exit tax)", fg, fund_rate, rule=f"{fund_rate:.0%} in {y}; no exemption; fund losses get no relief", form="Form 11: Panel E")
    deemed = ctx["deemed_disposals"](8)
    due = [d for d in deemed if d["date"][:4] == str(y)]
    if due:
        r.line("Deemed disposals (8 years after purchase)", sum(max(d["gain"], 0) for d in due), fund_rate,
               rule="Taxed as if sold; the tax is credited when you really sell", form="Form 11: Panel E")
    r.extra["deemedDisposals"] = [d for d in deemed if d["date"] >= f"{y}-01-01"][:12]
    divs = dividend_rows(ctx)
    fdiv = sum(d["g"] for d in divs if d["category"] == "funds" and (d.get("domicile") == "EU" or equivalent_us))
    sdiv = [d for d in divs if not (d["category"] == "funds" and (d.get("domicile") == "EU" or equivalent_us))]
    if fdiv:
        r.line("Fund distributions", fdiv, fund_rate, rule=f"{fund_rate:.0%}; no USC or PRSI", form="Form 11: Panel E")
    m = (opt(ctx, "marginal") or 40) / 100
    usc = (opt(ctx, "usc") or 3) / 100
    sg = sum(d["g"] for d in sdiv)
    if sg:
        r.line("Dividends (income tax)", sg, m, rule=f"Your marginal rate ({m:.0%})")
        r.line("USC on dividends", sg, usc, rule=f"At your top USC rate ({usc:.0%})")
        unearned = sg + (opt(ctx, "other_unearned") or 0)
        if unearned > 5000:
            prsi = sum(d["g"] * (0.042 if d["date"] < "2026-10-01" else 0.0435) for d in sdiv)
            r.line("PRSI on dividends", sg, None, prsi, rule="4.2% to 30 Sep 2026, 4.35% from 1 Oct (unearned income over €5,000)")
        cr = sum(credit(d["w"], d["g"], m) for d in sdiv if d.get("country") != "Ireland")
        if cr:
            r.line("Foreign tax credited", -cr, None, -cr, rule="Under the treaty (US 15% with a W-8BEN)")
    note_estimates(r, divs)
    r.form("Form 11", "", "You must file a Form 11 if you hold an offshore fund") if any(e["category"] == "funds" for e in ctx["events"].values()) else None
    r.deadlines += [f"CGT on gains from January to November {y}: by 15 December {y}.", f"CGT on December {y} gains: by 31 January {y + 1}.",
                    f"Form 11, fund tax and dividends: 31 October {y + 1} (mid-November on ROS)."]
    r.unsure += ["Which 2026 gains (33% or 31%) the exemption and losses reduce first isn't stated; the 33% ones are used first.",
                 "Whether a US ETF is an 'equivalent' fund is Revenue's case-by-case test: the choice is in the options.",
                 "The 35% fund rate for 2027 is in a Bill, not yet law."]
    return r.out()


# ---------------------------------------------------------------- United Kingdom

UK_AEA = 3000
UK_PA, UK_BASIC_BAND, UK_ADDITIONAL = 12570, 37700, 125140
UK_DIV_ALLOWANCE = 500
UK_DIV = {2025: (0.0875, 0.3375, 0.3935), 2026: (0.1075, 0.3575, 0.3935)}


def uk(ctx):
    r = Result(ctx)
    y = ctx["year"]
    income = opt(ctx, "income") or 0  # taxable non-savings income, before the personal allowance
    reporting = [p for p in ctx["pieces"] if not (p["category"] == "funds" and p.get("domicile") != "EU" and not opt(ctx, "us_reporting"))]
    nonrep = [p for p in ctx["pieces"] if p not in reporting]
    shares = [p for p in reporting if p["category"] != "crypto"]
    crypto = [p for p in reporting if p["category"] == "crypto"]
    gains = sum(p["gain"] for p in reporting if p["gain"] > 0)
    losses = -sum(p["gain"] for p in reporting if p["gain"] < 0)
    net_g = gains - losses
    brought = ctx["carry"].get("uk_losses", 0.0)
    if net_g < 0:
        brought += -net_g
        net_g = 0.0
    use = min(brought, max(net_g - UK_AEA, 0))  # losses brought forward only bring gains down to the exempt amount
    brought -= use
    net_g -= use
    r.carry["uk_losses"] = brought
    taxable = max(net_g - UK_AEA, 0)
    r.allowance("Annual exempt amount", UK_AEA, net_g)
    divs = dividend_rows(ctx)
    dg = sum(d["g"] for d in divs)
    band_used = max(income - UK_PA, 0)
    div_taxed = max(dg - UK_DIV_ALLOWANCE, 0)
    rates = UK_DIV.get(y, UK_DIV[2026] if y > 2026 else UK_DIV[2025])
    # Dividends sit on top of other income, then gains on top of both
    basic_left = max(UK_BASIC_BAND - band_used, 0)
    in_basic = min(div_taxed, max(basic_left - min(UK_DIV_ALLOWANCE, dg), 0))
    over = div_taxed - in_basic
    add_from = max(UK_ADDITIONAL - UK_PA - band_used - in_basic, 0)
    in_add = max(over - add_from, 0)
    in_high = over - in_add
    r.allowance("Dividend allowance", UK_DIV_ALLOWANCE, dg)
    t = r.line("Dividends in the basic rate band", in_basic, rates[0], rule=f"{rates[0]:.2%} ({y}/{str(y + 1)[2:]})")
    t += r.line("Dividends in the higher rate band", in_high, rates[1], rule=f"{rates[1]:.2%}")
    if in_add:
        t += r.line("Dividends in the additional rate band", in_add, rates[2], rule=f"{rates[2]:.2%}")
    cr = sum(credit(d["w"], d["g"], t / dg if dg else 0) for d in divs if d.get("country") != "United Kingdom")
    if cr:
        r.line("Foreign tax credit relief", -cr, None, -cr, rule="Each dividend: the lower of the foreign tax (treaty rate) and the UK tax on it", form="SA106")
    note_estimates(r, divs)
    left = max(basic_left - min(UK_DIV_ALLOWANCE, dg) - in_basic, 0)
    low = min(taxable, left)
    r.line("Gains in the unused basic rate band", low, 0.18, rule="CGT 18%", form="SA108")
    r.line("Gains above it", taxable - low, 0.24, rule="CGT 24% (since 30 October 2024)", form="SA108")
    if nonrep:
        m = (opt(ctx, "marginal") or 40) / 100
        r.line("Gains on non-reporting funds (offshore income gains)", max(sum(p["gain"] for p in nonrep), 0), m,
               rule=f"Taxed as income at your marginal rate ({m:.0%}); no exempt amount, no capital losses", form="SA106 box 56")
        r.notes.append("US-listed funds are taken as non-reporting funds; check HMRC's list of reporting funds and change it in the options if one is.")
    if any(e["category"] == "funds" and e.get("domicile") == "EU" and not e["dividends"] for e in ctx["events"].values()):
        r.notes.append("Accumulating reporting funds: their excess reportable income is taxable each year (as dividends, or interest for bond funds) "
                       "on the fund's distribution date, from the fund's report. It isn't included here; it reduces the gain when you sell.")
        r.unsure.append("Excess reportable income needs each fund's yearly report, which this app can't fetch.")
    sh_pro = sum(p["proceeds"] for p in shares)
    r.form("SA108", "23", "Listed shares: number of disposals", len({p["saleId"] for p in shares}))
    r.form("SA108", "24", "Listed shares: proceeds", sh_pro)
    r.form("SA108", "25", "Listed shares: allowable costs", sum(p["cost"] for p in shares))
    r.form("SA108", "26", "Listed shares: gains before losses", sum(p["gain"] for p in shares if p["gain"] > 0))
    r.form("SA108", "27", "Listed shares: losses", -sum(p["gain"] for p in shares if p["gain"] < 0))
    if crypto:
        r.form("SA108", "13.1-13.5", "Cryptoassets: disposals, proceeds, costs, gains, losses", sum(p["gain"] for p in crypto))
    r.form("SA108", "45", "Losses brought forward and used", use)
    r.form("SA108", "47", "Losses available to carry forward", brought)
    r.form("SA106", "F2/F3", "Foreign dividends, with the foreign tax", sum(d["g"] for d in divs if d.get("country") != "United Kingdom"))
    total_proceeds = sum(p["proceeds"] for p in reporting)
    if total_proceeds > 50000 or sum(p["gain"] for p in reporting if p["gain"] > 0) > UK_AEA:
        r.notes.append("You need the capital gains pages (SA108): disposals over £50,000 or gains over the £3,000 exempt amount.")
    r.deadlines += [f"Self Assessment for {y}/{str(y + 1)[2:]}: online by 31 January {y + 2}, with the tax (CGT is never in payments on account)."]
    r.unsure += ["SA108/SA106 box numbers are the 2025/26 forms'; re-check when HMRC publishes 2026/27.",
                 "Using the exempt amount and losses against the 24% gains first is HMRC practice, not checked on a page."]
    return r.out()


# ---------------------------------------------------------------- United States

US_LTCG = {2025: {"single": (48350, 533400), "mfj": (96700, 600050), "mfs": (48350, 300000), "hoh": (64750, 566700)},
           2026: {"single": (49450, 545500), "mfj": (98900, 613700), "mfs": (49450, 306850), "hoh": (66200, 579600)}}
US_NIIT = {"single": 200000, "hoh": 200000, "mfj": 250000, "mfs": 125000}
US_LOSS_LIMIT = {"mfs": 1500}
US_STD = {2026: {"single": 16100, "mfj": 32200, "mfs": 16100, "hoh": 24150}}


def united_states(ctx):
    r = Result(ctx)
    y = ctx["year"]
    status = opt(ctx, "status") or "single"
    ordinary = opt(ctx, "income") or 0  # taxable ordinary income, after deductions
    m = (opt(ctx, "marginal") or 22) / 100
    pfic = [p for p in ctx["pieces"] if p["category"] == "funds" and p.get("domicile") != "US"]
    rest = [p for p in ctx["pieces"] if p not in pfic]
    st = sum(p["gain"] for p in rest if not p["long"])
    lt = sum(p["gain"] for p in rest if p["long"])
    c_st, c_lt = ctx["carry"].get("us_st", 0.0), ctx["carry"].get("us_lt", 0.0)
    st -= c_st
    lt -= c_lt
    # Net short against long; a net loss offsets up to $3,000 of ordinary income, the rest carries with its character
    limit = US_LOSS_LIMIT.get(status, 3000)
    out_st = out_lt = 0.0
    if st < 0 and lt > 0:
        lt, st = lt + st, 0.0
        if lt < 0:
            st, lt = lt, 0.0
    elif lt < 0 and st > 0:
        st, lt = st + lt, 0.0
        if st < 0:
            lt, st = st, 0.0
    deduct = 0.0
    if st + lt < 0:
        loss = -(st + lt)
        deduct = min(loss, limit)
        rem = deduct
        use_st = min(-min(st, 0), rem)
        rem -= use_st
        out_st = -min(st, 0) - use_st
        out_lt = -min(lt, 0) - rem
        st = lt = 0.0
    r.carry.update({"us_st": out_st, "us_lt": out_lt})
    divs = dividend_rows(ctx)
    qualified = sum(d["g"] for d in divs if not (d["category"] == "funds" and d.get("domicile") != "US"))
    nonq = sum(d["g"] for d in divs if d["category"] == "funds" and d.get("domicile") != "US")
    r.line("Short-term gains (held a year or less)", max(st, 0), m, rule=f"Ordinary rates: your marginal {m:.0%}", form="Form 8949 Part I, Schedule D line 7")
    zero_to, fifteen_to = US_LTCG.get(y, US_LTCG[2026])[status]
    stack = max(ordinary + max(st, 0), 0)
    pref = max(lt, 0) + qualified
    in0 = max(min(pref, zero_to - stack), 0)
    in15 = max(min(pref - in0, fifteen_to - max(stack, zero_to)), 0)
    in20 = pref - in0 - in15
    r.line("Long-term gains and qualified dividends at 0%", in0, 0, 0, rule=f"Up to ${zero_to:,} of taxable income ({status})")
    r.line("... at 15%", in15, 0.15, rule=f"Up to ${fifteen_to:,}")
    if in20:
        r.line("... at 20%", in20, 0.20)
    if deduct:
        r.line("Capital loss deducted from ordinary income", -deduct, m, -deduct * m, rule=f"Up to ${limit:,}; the rest carries forward with its character", form="Schedule D line 21")
    if pfic or nonq:
        pg = sum(max(p["gain"], 0) for p in pfic)
        r.line("Non-US funds (PFICs): gains and distributions", pg + nonq, m, rule="Excess distribution regime: ordinary rates, with an interest charge for earlier years not worked out here", form="Form 8621")
        r.notes.append("Funds domiciled outside the US (Irish or Luxembourg UCITS ETFs) are PFICs: Form 8621 each year, no long-term rate. US advisers generally say to avoid them.")
    magi = ordinary + max(st, 0) + max(lt, 0) + qualified + nonq
    niit_from = US_NIIT[status]
    if magi > niit_from:
        nii = max(st, 0) + max(lt, 0) + qualified + nonq
        r.line("Net investment income tax", min(nii, magi - niit_from), 0.038, rule=f"3.8% above ${niit_from:,} of modified AGI (not indexed)", form="Form 8960")
    ftc = sum(d["w"] for d in divs if d.get("country") != "United States")
    if ftc:
        simple = 600 if status == "mfj" else 300
        r.line("Foreign tax credit", -ftc, None, -ftc, rule=(f"Up to ${simple} without Form 1116" if ftc <= simple else "Form 1116 (passive category)"), form="Form 1116" if ftc > simple else "Schedule 3")
    note_estimates(r, divs)
    washed = [p for p in ctx["pieces"] if p.get("washDisallowed")]
    if washed:
        r.notes.append(f"Wash sales: {sum(p['washDisallowed'] for p in washed):,.0f} of losses disallowed and added to the replacement shares' cost (code W on Form 8949).")
    r.form("Form 8949", "Part I (A-C; G-I for crypto)", "Short-term sales", sum(p["gain"] for p in rest if not p["long"]))
    r.form("Form 8949", "Part II (D-F; J-L for crypto)", "Long-term sales", sum(p["gain"] for p in rest if p["long"]))
    r.form("Schedule D", "6 / 14", "Short- and long-term loss carryover in", c_st + c_lt)
    if qualified + nonq > 1500:
        r.form("Schedule B", "5", "Ordinary dividends", qualified + nonq)
    if foreign_broker(ctx):
        r.notes.append("A broker abroad is a foreign financial account: FBAR (FinCEN 114) if your foreign accounts together topped $10,000 at any time; "
                       "Form 8938 from $50,000 at year end ($100,000 married filing jointly) living in the US.")
    r.deadlines += [f"Return for {y}: 15 April {y + 1}.", f"Estimated tax for {y}: 15 April, 15 June, 15 September {y}, 15 January {y + 1}."]
    r.unsure += ["Qualified dividends need 61 days held around the ex-date; every US-listed company's dividend is taken as qualified.",
                 "Crypto is taken as outside the wash-sale rule (it covers stock or securities).", "State income tax isn't included."]
    return r.out()


COUNTRIES.update({
    "DE": {"name": "Germany", "currency": "EUR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": germany, "options": [BROKER_OPTION, {"key": "joint", "label": "Assessed jointly (€2,000 allowance)", "type": "bool", "default": False},
                                            {"key": "church", "label": "Church tax (%)", "type": "select", "default": 0, "choices": [[0, "None"], [8, "8% (Bavaria, Baden-Württemberg)"], [9, "9%"]]},
                                            marginal_option(42, "Your income tax rate for crypto sold within a year (%)")],
           "sources": ["https://www.gesetze-im-internet.de/estg/__20.html", "https://www.gesetze-im-internet.de/estg/__32d.html", "https://www.gesetze-im-internet.de/estg/__23.html",
                       "https://www.gesetze-im-internet.de/invstg_2018/__18.html", "https://www.gesetze-im-internet.de/invstg_2018/__20.html",
                       "https://www.otto-schmidt.de/news/steuerrecht/basiszins-zur-berechnung-der-vorabpauschale-gem-18-absatz-4-invstg-basiszins-zum-2-1-2026-2026-01-15.html"]},
    "AT": {"name": "Austria", "currency": "EUR", "year": "calendar", "methods": {"shares": "average", "funds": "average", "crypto": "average"},
           "compute": austria, "options": [BROKER_OPTION],
           "sources": ["https://www.bmf.gv.at/themen/steuern/sparen-veranlagen/information-zu-einkuenften-aus-kapitalvermoegen.html",
                       "https://www.bmf.gv.at/themen/steuern/sparen-veranlagen/steuerliche-behandlung-von-kryptowaehrungen.html",
                       "https://www.jusline.at/gesetz/estg/paragraf/27a", "https://www.jusline.at/gesetz/invfg_2011/paragraf/186"]},
    "CH": {"name": "Switzerland", "currency": "CHF", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": switzerland, "options": [marginal_option(30, "Your marginal income tax rate, federal + cantonal (%)"),
                                                {"key": "income", "label": "Your taxable income (CHF)", "type": "number", "default": 0, "min": 0}],
           "sources": ["https://taxsummaries.pwc.com/switzerland/individual/income-determination",
                       "https://steuerpraxis.tg.ch/steuerpraxis/2026-09/stp-20-nr-2-gewerbsmassiger-handel-mit-wertschriften-gold-devisen-oder-kryptowahrungen",
                       "https://www.estv.admin.ch/de/kurslisten-ictax"]},
    "NL": {"name": "Netherlands", "currency": "EUR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": netherlands, "options": [{"key": "partner", "label": "With a fiscal partner (double allowance)", "type": "bool", "default": False},
                                                {"key": "bank", "label": "Savings at 1 January (€)", "type": "number", "default": 0, "min": 0},
                                                {"key": "debts", "label": "Box 3 debts at 1 January (€)", "type": "number", "default": 0, "min": 0}],
           "sources": ["https://www.belastingdienst.nl/wps/wcm/connect/nl/box-3/content/berekening-box-3-inkomen-2026",
                       "https://www.belastingdienst.nl/wps/wcm/connect/nl/box-3/content/wat-is-mijn-werkelijk-rendement",
                       "https://www.sra.nl/nieuws/259001/2026/01/box-3-percentages-vrijstellingen-en-bedragen-2026"]},
    "GR": {"name": "Greece", "currency": "EUR", "year": "calendar", "methods": {"shares": "average", "funds": "average", "crypto": "fifo"},
           "compute": greece, "sourceRates": {"United States": 0.30},
           "options": [BROKER_OPTION, {"key": "stake", "label": "You hold 0.5% or more of a company", "type": "bool", "default": False}],
           "sources": ["https://www.taxheaven.gr/law/4172/2013/arthro/42", "https://www.taxheaven.gr/circulars/20353",
                       "https://taxsummaries.pwc.com/greece/individual/income-determination", "https://taxsummaries.pwc.com/greece/individual/foreign-tax-relief-and-tax-treaties",
                       "https://decrypt.co/382756/greece-passes-crypto-tax-law-staking-defi"]},
    "CY": {"name": "Cyprus", "currency": "EUR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
           "compute": cyprus, "options": [{"key": "domiciled", "label": "Domiciled in Cyprus", "type": "bool", "default": True}],
           "sources": ["https://taxsummaries.pwc.com/cyprus/individual/income-determination", "https://www.tridenttrust.com/knowledge/news/summary-of-cyprus-s-2026-tax-reforms",
                       "https://www.agplaw.com/?p=31864"]},
    "IE": {"name": "Ireland", "currency": "EUR", "year": "calendar", "methods": {"shares": "ie", "funds": "fifo", "crypto": "ie"},
           "compute": ireland, "options": [BROKER_OPTION, marginal_option(40), {"key": "usc", "label": "Your top USC rate (%)", "type": "number", "default": 3, "min": 0, "max": 8},
                                            {"key": "other_unearned", "label": "Other unearned income (€, for PRSI)", "type": "number", "default": 0, "min": 0},
                                            {"key": "us_funds", "label": "US-listed ETFs", "type": "select", "default": "equivalent",
                                             "choices": [["equivalent", "Equivalent funds (fund rate, deemed disposal)"], ["non", "Non-equivalent (income tax and CGT)"]]}],
           "sources": ["https://www.revenue.ie/en/gains-gifts-and-inheritance/transfering-an-asset/how-to-calculate-cgt.aspx",
                       "https://www.revenue.ie/en/gains-gifts-and-inheritance/transfering-an-asset/selling-or-disposing-of-shares.aspx",
                       "https://www.revenue.ie/en/tax-professionals/ebrief/2026/no-0162026.aspx",
                       "https://www.revenue.ie/en/tax-professionals/tdm/income-tax-capital-gains-tax-corporation-tax/part-27/27-04-01.pdf"]},
    "UK": {"name": "United Kingdom", "currency": "GBP", "year": "uk", "methods": {"shares": "uk", "funds": "uk", "crypto": "uk"},
           "compute": uk, "options": [{"key": "income", "label": "Your other taxable income for the year (£, before the personal allowance)", "type": "number", "default": 50000, "min": 0},
                                       marginal_option(40, "Your marginal income tax rate (%)"),
                                       {"key": "us_reporting", "label": "Treat US-listed funds as reporting funds", "type": "bool", "default": False}],
           "sources": ["https://www.gov.uk/capital-gains-tax/rates", "https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560",
                       "https://www.gov.uk/tax-on-dividends", "https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds",
                       "https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg78310"]},
    "US": {"name": "United States", "currency": "USD", "year": "calendar", "methods": {"shares": "us", "funds": "us", "crypto": "us"},
           "compute": united_states, "options": [{"key": "status", "label": "Filing status", "type": "select", "default": "single",
                                                  "choices": [["single", "Single"], ["mfj", "Married filing jointly"], ["mfs", "Married filing separately"], ["hoh", "Head of household"]]},
                                                 {"key": "income", "label": "Taxable ordinary income ($, after deductions)", "type": "number", "default": 80000, "min": 0},
                                                 marginal_option(22, "Your marginal income tax rate (%)"), BROKER_OPTION],
           "sources": ["https://www.irs.gov/taxtopics/tc409", "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf", "https://www.irs.gov/publications/p550",
                       "https://www.irs.gov/instructions/i8621", "https://www.irs.gov/instructions/i8949", "https://www.irs.gov/taxtopics/tc559"]},
})
ORDER = ["AT", "BE", "CY", "FR", "DE", "GR", "IE", "IT", "NL", "PT", "ES", "CH", "UK", "US"]


def check_residence(v):
    if v in ("", "other") or v in COUNTRIES:
        return v
    raise ValueError("Unknown tax residence.")


def check_options(v):
    """{country code: {option key: value}}, each value checked against its option's type and limits."""
    if not isinstance(v, dict):
        raise ValueError("Tax options must be an object.")
    out = {}
    for code, opts in v.items():
        if code not in COUNTRIES or not isinstance(opts, dict):
            continue
        spec = {o["key"]: o for o in COUNTRIES[code]["options"]}
        clean = {}
        for k, x in opts.items():
            o = spec.get(k)
            if not o:
                continue
            if o["type"] == "bool":
                if not isinstance(x, bool):
                    raise ValueError(f"{o['label']} must be on or off.")
            elif o["type"] == "number":
                if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x < o.get("min", -1e18) or x > o.get("max", 1e18):
                    raise ValueError(f"{o['label']}: enter a number{' from ' + str(o.get('min')) if 'min' in o else ''}{' to ' + str(o['max']) if 'max' in o else ''}.")
            elif o["type"] == "select" and x not in [c[0] for c in o["choices"]]:
                raise ValueError(f"{o['label']}: not one of the choices.")
            clean[k] = x
        out[code] = clean
    return out


def check_fund_tax(v):
    if not isinstance(v, dict):
        raise ValueError("Fund tax details must be an object.")
    out = {}
    for t, d in v.items():
        if not isinstance(t, str) or not isinstance(d, dict) or len(t) > 20:
            raise ValueError("Fund tax details: one entry per ticker.")
        e = {}
        if d.get("kind") in FUND_KINDS:
            e["kind"] = d["kind"]
        if d.get("domicile") in DOMICILES:
            e["domicile"] = d["domicile"]
        if e:
            out[t.upper()] = e
    return out


def options_for(code, saved):
    """The country's options with your saved answers over the defaults."""
    spec = COUNTRIES[code]["options"]
    mine = (saved or {}).get(code, {})
    return {o["key"]: mine.get(o["key"], o["default"]) for o in spec}


def public(code):
    """What the page needs to show a country (no functions)."""
    c = COUNTRIES[code]
    return {"code": code, "name": c["name"], "currency": c["currency"], "year": c["year"], "methods": c["methods"], "options": c["options"], "sources": c["sources"]}



# ---------------------------------------------------------------- Qatar

US_ESTATE_THRESHOLD = 60000  # US estate tax on a non-US person's US-situs assets above this (18-40%)


def qatar(ctx):
    """No tax on an individual's dividends or gains on securities held outside a business; what still costs money is
    abroad: US withholding at 30% (no US treaty) and US estate tax on US-situs holdings over $60,000."""
    from . import data, taxcalc
    r = Result(ctx)
    y = ctx["year"]
    r.line("Gains on shares, funds and crypto", sum(p["gain"] for p in ctx["pieces"]), 0, 0,
           rule="Exempt for an individual when the securities aren't assets of a business (Law No. 24 of 2018)")
    divs = dividend_rows(ctx)
    gross = sum(d["g"] for d in divs)
    r.line("Dividends", gross, 0, 0, rule="Not taxed in Qatar")
    lost = sum(d["w"] for d in divs)
    if lost:
        r.line("Tax kept abroad on your dividends", lost, None, None, kind="info",
               rule="A cost, not reclaimable: the US keeps 30% (no US-Qatar treaty); Irish funds pay out without withholding")
    note_estimates(r, divs)
    # US estate tax exposure: US-listed shares and US-domiciled funds (not Irish or Luxembourg UCITS, not crypto)
    end = min(ctx["end"], dt.date.today().isoformat())
    us_value = 0.0
    names = []
    for tk, e in ctx["events"].items():
        us_situs = (e["category"] == "shares" and e.get("country") == "United States") or (e["category"] == "funds" and e.get("domicile") == "US")
        if not us_situs:
            continue
        n = taxcalc.shares_on(e, end)
        unit = ctx["value_at"](tk, end)
        if n > 1e-9 and unit is not None:
            us_value += n * unit
            names.append(tk)
    try:
        usd = us_value * data.fx_rate(ctx["currency"], "USD")
    except data.DataError:
        usd = None
    if usd is not None:
        r.line(f"US assets for US estate tax ({', '.join(names) or 'none'})", us_value, None, None, kind="info",
               rule=f"About ${usd:,.0f} of the ${US_ESTATE_THRESHOLD:,} a non-US person can leave free of US estate tax (18% to 40% above it)")
        r.extra["usEstate"] = {"usd": usd, "threshold": US_ESTATE_THRESHOLD, "tickers": names}
        if usd > 0.8 * US_ESTATE_THRESHOLD:
            r.notes.append(f"Your US-listed holdings (about ${usd:,.0f}) are near or over the ${US_ESTATE_THRESHOLD:,} US estate tax threshold for non-US persons. "
                           "The same exposure through an Irish-domiciled fund (such as VWCE) isn't US property.")
    r.notes += ["No personal tax return is needed for investment income in Qatar.",
                "US dividends: the US withholds 30% from residents of Qatar (no income tax treaty); an Irish fund holding US shares pays 15% inside the fund instead.",
                "A US citizen or green card holder is taxed by the US wherever they live; some home countries keep taxing for a time after you leave. Then choose that country instead."]
    r.unsure += ["The dividend exemption is described as for profits already taxed in Qatar or exempt there; foreign dividends to individuals aren't taxed in practice, but PwC's wording is narrower.",
                 "No US-Qatar income tax treaty was found; check IRS Publication 515 or your broker's withholding."]
    r.deadlines.append(f"Nothing to file in Qatar for {y} investment income.")
    return r.out()


COUNTRIES["QA"] = {"name": "Qatar", "currency": "QAR", "year": "calendar", "methods": {"shares": "fifo", "funds": "fifo", "crypto": "fifo"},
                   "compute": qatar, "sourceRates": {"United States": 0.30}, "options": [],
                   "sources": ["https://taxsummaries.pwc.com/qatar/individual/income-determination",
                               "https://taxsummaries.pwc.com/Qatar/Individual/Residence",
                               "https://www.taxesforexpats.com/articles/non-us-citizens/nonresident-alien-estate-tax-exemption-us-situs-assets.html"]}
ORDER.insert(ORDER.index("ES"), "QA")
