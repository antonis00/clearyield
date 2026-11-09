"""Default ticker universe (Yahoo Finance symbols) used by the screener and search.

Edit freely - any Yahoo Finance symbol works in Research even if it is not listed here.
"""

UNIVERSE = {
    "US": {
        "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet", "AMZN": "Amazon",
        "META": "Meta Platforms", "AVGO": "Broadcom", "TSLA": "Tesla", "BRK-B": "Berkshire Hathaway",
        "JPM": "JPMorgan Chase", "V": "Visa", "MA": "Mastercard", "UNH": "UnitedHealth", "LLY": "Eli Lilly",
        "JNJ": "Johnson & Johnson", "PG": "Procter & Gamble", "KO": "Coca-Cola", "PEP": "PepsiCo",
        "XOM": "Exxon Mobil", "CVX": "Chevron", "HD": "Home Depot", "COST": "Costco", "WMT": "Walmart",
        "ABBV": "AbbVie", "MRK": "Merck", "ADBE": "Adobe", "CRM": "Salesforce", "AMD": "AMD",
        "ORCL": "Oracle", "NFLX": "Netflix", "INTC": "Intel", "CSCO": "Cisco", "QCOM": "Qualcomm",
        "TXN": "Texas Instruments", "MCD": "McDonald's", "NKE": "Nike", "DIS": "Disney", "CAT": "Caterpillar",
        "GS": "Goldman Sachs", "PFE": "Pfizer",
    },
    "Europe": {
        "ASML.AS": "ASML", "SAP.DE": "SAP", "MC.PA": "LVMH", "NESN.SW": "Nestle", "NOVO-B.CO": "Novo Nordisk",
        "RO.SW": "Roche", "SIE.DE": "Siemens", "AZN.L": "AstraZeneca", "SHEL.L": "Shell", "OR.PA": "L'Oreal",
        "TTE.PA": "TotalEnergies", "ULVR.L": "Unilever", "ALV.DE": "Allianz", "SAN.MC": "Banco Santander",
        "AIR.PA": "Airbus", "HSBA.L": "HSBC", "BMW.DE": "BMW", "ENEL.MI": "Enel", "RMS.PA": "Hermes",
        "IBE.MC": "Iberdrola",
        # Banks and insurers, so a screen for European financials has more than a handful to choose from
        "BNP.PA": "BNP Paribas", "INGA.AS": "ING", "UCG.MI": "UniCredit", "ISP.MI": "Intesa Sanpaolo", "BBVA.MC": "BBVA",
        "DBK.DE": "Deutsche Bank", "BARC.L": "Barclays", "LLOY.L": "Lloyds", "UBSG.SW": "UBS", "NDA-FI.HE": "Nordea",
        "CS.PA": "AXA", "ZURN.SW": "Zurich Insurance", "MUV2.DE": "Munich Re",
    },
    "Gulf": {
        "QNBK.QA": "QNB", "IQCD.QA": "Industries Qatar", "QIBK.QA": "Qatar Islamic Bank",
        "ORDS.QA": "Ooredoo", "MARK.QA": "Masraf Al Rayan", "QGTS.QA": "Nakilat",
        "2222.SR": "Saudi Aramco", "1120.SR": "Al Rajhi Bank", "2010.SR": "SABIC", "7010.SR": "STC",
        "1180.SR": "Saudi National Bank", "EMAAR.AE": "Emaar Properties", "DIB.AE": "Dubai Islamic Bank",
    },
    "ETFs": {
        "SPY": "S&P 500 ETF", "QQQ": "Nasdaq-100 ETF", "VTI": "Total US Market ETF", "VXUS": "Intl ex-US ETF",
        "VWO": "Emerging Markets ETF", "SCHD": "US Dividend ETF", "VYM": "High Dividend Yield ETF",
        "GLD": "Gold ETF", "TLT": "20+Y Treasury ETF", "BND": "Total Bond ETF", "VNQ": "US REIT ETF",
        "IWM": "Russell 2000 ETF", "EFA": "Developed ex-US ETF", "XLK": "Tech Sector ETF",
        "XLE": "Energy Sector ETF", "XLV": "Health Care Sector ETF", "VWCE.DE": "Vanguard FTSE All-World (Acc)",
    },
    "Crypto": {
        "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana", "BNB-USD": "BNB",
        "XRP-USD": "XRP", "ADA-USD": "Cardano",
    },
}

# Every tile the dashboard can show, in display order. You pick which ones in Settings.
# The Gulf indexes (^TASI.SR, ^GNRI.QA) are on Yahoo but return a single day of history, so the Saudi and
# Qatari markets are followed through the iShares country ETFs, which trade in New York and have full history.
MACRO = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^RUT": "Russell 2000", "^STOXX50E": "Euro Stoxx 50", "^GDAXI": "DAX",
    "^FTSE": "FTSE 100", "^N225": "Nikkei 225", "^HSI": "Hang Seng", "KSA": "Saudi Arabia (KSA ETF)",
    "QAT": "Qatar (QAT ETF)", "^VIX": "VIX", "^TNX": "US 10Y yield", "GC=F": "Gold", "SI=F": "Silver",
    "CL=F": "WTI Oil", "BZ=F": "Brent Oil", "NG=F": "Natural Gas", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY", "DX-Y.NYB": "US Dollar Index",
    "VWCE.DE": "FTSE All-World (VWCE)", "ACWI": "MSCI ACWI (USD)",
}
DEFAULT_TILES = ["^GSPC", "^IXIC", "^STOXX50E", "KSA", "QAT", "^VIX", "^TNX", "GC=F", "CL=F", "BTC-USD", "EURUSD=X", "DX-Y.NYB"]

# Funds whose region is what they hold, not where they're listed: VWCE trades in Frankfurt but owns the world
REGION_BY_TICKER = {
    "VWCE.DE": "Global", "SPY": "US", "QQQ": "US", "VWO": "Emerging", "VXUS": "International", "EFA": "International",
    "KSA": "Gulf", "QAT": "Gulf",
}

REGION_BY_SUFFIX = {
    ".QA": "Gulf", ".SR": "Gulf", ".AE": "Gulf", ".AD": "Gulf", ".KW": "Gulf", ".BH": "Gulf",
    ".L": "Europe", ".PA": "Europe", ".DE": "Europe", ".AS": "Europe", ".SW": "Europe", ".CO": "Europe",
    ".MC": "Europe", ".MI": "Europe", ".BR": "Europe", ".ST": "Europe", ".OL": "Europe", ".HE": "Europe",
    ".AT": "Europe", ".LS": "Europe", ".VI": "Europe", ".IR": "Europe", ".F": "Europe", ".SG": "Europe", ".WA": "Europe",
    ".MU": "Europe", ".DU": "Europe", ".BE": "Europe", ".HM": "Europe", ".HA": "Europe",
    ".T": "Asia", ".HK": "Asia", ".AX": "Asia", ".NS": "Asia", ".BO": "Asia", ".SS": "Asia", ".SZ": "Asia", ".KS": "Asia",
    ".KQ": "Asia", ".TW": "Asia", ".SI": "Asia", ".NZ": "Asia", ".TO": "Americas", ".V": "Americas", ".SA": "Americas", ".MX": "Americas",
    ".NYB": "US",
}

CURRENCY_BY_SUFFIX = {
    ".QA": "QAR", ".SR": "SAR", ".AE": "AED", ".AD": "AED", ".KW": "KWF", ".BH": "BHD", ".L": "GBp",
    ".PA": "EUR", ".DE": "EUR", ".AS": "EUR", ".MC": "EUR", ".MI": "EUR", ".BR": "EUR", ".HE": "EUR",
    ".AT": "EUR", ".LS": "EUR", ".VI": "EUR", ".IR": "EUR", ".SW": "CHF", ".CO": "DKK", ".ST": "SEK",
    ".OL": "NOK", ".F": "EUR", ".SG": "EUR", ".MU": "EUR", ".DU": "EUR", ".BE": "EUR", ".HM": "EUR", ".HA": "EUR", ".WA": "PLN", ".T": "JPY", ".HK": "HKD", ".AX": "AUD", ".NS": "INR", ".BO": "INR", ".SS": "CNY",
    ".SZ": "CNY", ".KS": "KRW", ".KQ": "KRW", ".TW": "TWD", ".SI": "SGD", ".NZ": "NZD", ".TO": "CAD", ".V": "CAD", ".SA": "BRL", ".MX": "MXN",
    ".NYB": "USD",
}


# Broad, market-weighted index funds: the usual core of a core-and-satellite portfolio. Everything else (single
# stocks, sector, country, factor and thematic funds, crypto) starts as a satellite. Any holding can be switched.
CORE_FUNDS = {
    "VWCE.DE", "VWCE.MI", "VWCE.AS", "VWRL.L", "VWRA.L", "VWRL.AS", "IWDA.AS", "SWDA.L", "EUNL.DE", "ACWI", "URTH", "VT",
    "SPY", "VOO", "IVV", "CSPX.L", "VUSA.L", "SXR8.DE", "VTI", "VXUS", "VEA", "EFA", "VWO", "IEMG", "BND", "AGG",
}


def default_bucket(ticker):
    return "core" if ticker in CORE_FUNDS else "satellite"


def name_of(ticker):
    for group in UNIVERSE.values():
        if ticker in group:
            return group[ticker]
    return MACRO.get(ticker, ticker)


def region_of(ticker, quote_type=None):
    if ticker in REGION_BY_TICKER:
        return REGION_BY_TICKER[ticker]
    if ticker.endswith("-USD") or quote_type == "CRYPTOCURRENCY":
        return "Crypto"
    for suf, reg in REGION_BY_SUFFIX.items():
        if ticker.endswith(suf):
            return reg
    return "Other" if "." in ticker else "US"


def currency_guess(ticker):
    for suf, cur in CURRENCY_BY_SUFFIX.items():
        if ticker.endswith(suf):
            return cur
    return None if "." in ticker else "USD"  # an exchange we don't know is not safe to assume is in dollars


def tickers_for(markets):
    out = []
    for m in markets or UNIVERSE.keys():
        out.extend(UNIVERSE.get(m, {}).keys())
    return list(dict.fromkeys(out))


def search(text, limit=12):
    text = (text or "").strip().lower()
    if not text:
        return []
    hits = []
    for market, group in UNIVERSE.items():
        for t, n in group.items():
            if text in t.lower() or text in n.lower():
                hits.append({"ticker": t, "name": n, "market": market})
    hits.sort(key=lambda h: (not h["ticker"].lower().startswith(text), len(h["ticker"])))
    return hits[:limit]
