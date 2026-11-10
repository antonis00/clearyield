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
        # More of each sector, so every one has enough companies to compare a stock with
        "HON": "Honeywell", "GE": "GE Aerospace", "RTX": "RTX", "UNP": "Union Pacific", "UPS": "UPS", "DE": "Deere",
        "LMT": "Lockheed Martin", "BA": "Boeing", "ETN": "Eaton", "BAC": "Bank of America", "WFC": "Wells Fargo",
        "MS": "Morgan Stanley", "C": "Citigroup", "BLK": "BlackRock", "SCHW": "Charles Schwab", "AXP": "American Express",
        "SPGI": "S&P Global", "ABT": "Abbott", "TMO": "Thermo Fisher", "DHR": "Danaher", "AMGN": "Amgen",
        "ISRG": "Intuitive Surgical", "BMY": "Bristol-Myers Squibb", "GILD": "Gilead", "CVS": "CVS Health", "MDT": "Medtronic",
        "PM": "Philip Morris", "MO": "Altria", "MDLZ": "Mondelez", "CL": "Colgate-Palmolive", "LOW": "Lowe's",
        "SBUX": "Starbucks", "BKNG": "Booking", "TJX": "TJX", "GM": "General Motors", "F": "Ford", "COP": "ConocoPhillips",
        "SLB": "SLB", "EOG": "EOG Resources", "NEE": "NextEra Energy", "DUK": "Duke Energy", "SO": "Southern Company",
        "D": "Dominion Energy", "PLD": "Prologis", "AMT": "American Tower", "EQIX": "Equinix", "O": "Realty Income",
        "SPG": "Simon Property", "LIN": "Linde", "SHW": "Sherwin-Williams", "APD": "Air Products", "FCX": "Freeport-McMoRan",
        "NEM": "Newmont", "T": "AT&T", "VZ": "Verizon", "CMCSA": "Comcast", "TMUS": "T-Mobile US", "IBM": "IBM",
        "NOW": "ServiceNow", "INTU": "Intuit", "AMAT": "Applied Materials", "MU": "Micron", "PANW": "Palo Alto Networks",
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
        # Industrials, healthcare, consumer, energy, materials, utilities, telecoms, tech and property
        "SU.PA": "Schneider Electric", "RR.L": "Rolls-Royce", "SAF.PA": "Safran", "ABBN.SW": "ABB", "VOLV-B.ST": "Volvo",
        "ATCO-A.ST": "Atlas Copco", "DSV.CO": "DSV", "RHM.DE": "Rheinmetall", "DG.PA": "Vinci", "NOVN.SW": "Novartis",
        "SAN.PA": "Sanofi", "GSK.L": "GSK", "BAYN.DE": "Bayer", "LONN.SW": "Lonza", "EL.PA": "EssilorLuxottica",
        "ITX.MC": "Inditex", "ABI.BR": "AB InBev", "DGE.L": "Diageo", "RI.PA": "Pernod Ricard", "ADS.DE": "Adidas",
        "VOW3.DE": "Volkswagen", "MBG.DE": "Mercedes-Benz", "STLAM.MI": "Stellantis", "RACE.MI": "Ferrari",
        "CFR.SW": "Richemont", "BATS.L": "British American Tobacco", "TSCO.L": "Tesco", "KER.PA": "Kering", "BP.L": "BP",
        "ENI.MI": "Eni", "EQNR.OL": "Equinor", "REP.MC": "Repsol", "BAS.DE": "BASF", "RIO.L": "Rio Tinto",
        "GLEN.L": "Glencore", "AAL.L": "Anglo American", "SIKA.SW": "Sika", "AI.PA": "Air Liquide", "ENGI.PA": "Engie",
        "NG.L": "National Grid", "EOAN.DE": "E.ON", "RWE.DE": "RWE", "SSE.L": "SSE", "DTE.DE": "Deutsche Telekom",
        "ORA.PA": "Orange", "VOD.L": "Vodafone", "TEF.MC": "Telefonica", "IFX.DE": "Infineon", "CAP.PA": "Capgemini",
        "ADYEN.AS": "Adyen", "PRX.AS": "Prosus", "NOKIA.HE": "Nokia", "ERIC-B.ST": "Ericsson", "DSY.PA": "Dassault Systemes",
        "VNA.DE": "Vonovia", "SGRO.L": "Segro", "LAND.L": "Land Securities", "HEIA.AS": "Heineken", "DHL.DE": "DHL Group",
        "MAERSK-B.CO": "Maersk",
    },
    "Gulf": {
        "QNBK.QA": "QNB", "IQCD.QA": "Industries Qatar", "QIBK.QA": "Qatar Islamic Bank",
        "ORDS.QA": "Ooredoo", "MARK.QA": "Masraf Al Rayan", "QGTS.QA": "Nakilat",
        "2222.SR": "Saudi Aramco", "1120.SR": "Al Rajhi Bank", "2010.SR": "SABIC", "7010.SR": "STC",
        "1180.SR": "Saudi National Bank", "EMAAR.AE": "Emaar Properties", "DIB.AE": "Dubai Islamic Bank",
        # Saudi, Qatari, Dubai and Kuwaiti companies across sectors. Abu Dhabi (ADX) isn't on Yahoo at all.
        "2082.SR": "ACWA Power", "1150.SR": "Alinma Bank", "1211.SR": "Ma'aden", "2280.SR": "Almarai",
        "5110.SR": "Saudi Electricity", "1010.SR": "Riyad Bank", "1060.SR": "Saudi Awwal Bank", "4002.SR": "Mouwasat Medical",
        "4190.SR": "Jarir Marketing", "CBQK.QA": "Commercial Bank of Qatar", "QEWS.QA": "Qatar Electricity & Water",
        "DUBK.QA": "Dukhan Bank", "QAMC.QA": "Qatar Aluminium", "EMIRATESNBD.AE": "Emirates NBD",
        "DEWA.AE": "Dubai Electricity & Water", "SALIK.AE": "Salik", "NBK.KW": "National Bank of Kuwait",
        "KFH.KW": "Kuwait Finance House", "ZAIN.KW": "Zain",
    },
    "Asia-Pacific": {
        # Japan, Hong Kong (incl. Chinese companies listed there), India, Korea, Taiwan, Australia, Singapore, Indonesia.
        # Mainland A-shares (.SS/.SZ) are left out: Yahoo's data for them is patchy, and the big ones list in Hong Kong too.
        "7203.T": "Toyota", "6758.T": "Sony", "8306.T": "Mitsubishi UFJ", "6861.T": "Keyence", "9984.T": "SoftBank Group",
        "6098.T": "Recruit", "9432.T": "NTT", "8035.T": "Tokyo Electron", "4063.T": "Shin-Etsu Chemical",
        "9983.T": "Fast Retailing", "7974.T": "Nintendo", "8058.T": "Mitsubishi Corp", "6501.T": "Hitachi", "4502.T": "Takeda",
        "4568.T": "Daiichi Sankyo", "7267.T": "Honda", "6367.T": "Daikin", "8316.T": "Sumitomo Mitsui",
        "8766.T": "Tokio Marine", "2914.T": "Japan Tobacco", "9433.T": "KDDI", "8802.T": "Mitsubishi Estate",
        "1605.T": "Inpex", "5401.T": "Nippon Steel", "9502.T": "Chubu Electric", "0700.HK": "Tencent", "9988.HK": "Alibaba",
        "1299.HK": "AIA", "0939.HK": "China Construction Bank", "1398.HK": "ICBC", "3690.HK": "Meituan", "1810.HK": "Xiaomi",
        "2318.HK": "Ping An", "0941.HK": "China Mobile", "0883.HK": "CNOOC", "0388.HK": "HKEX", "1211.HK": "BYD",
        "0016.HK": "Sun Hung Kai Properties", "0002.HK": "CLP Holdings", "9618.HK": "JD.com", "2020.HK": "Anta Sports",
        "1088.HK": "China Shenhua", "RELIANCE.NS": "Reliance Industries", "TCS.NS": "Tata Consultancy",
        "HDFCBANK.NS": "HDFC Bank", "INFY.NS": "Infosys", "ICICIBANK.NS": "ICICI Bank", "HINDUNILVR.NS": "Hindustan Unilever",
        "ITC.NS": "ITC", "BHARTIARTL.NS": "Bharti Airtel", "LT.NS": "Larsen & Toubro", "SBIN.NS": "State Bank of India",
        "BAJFINANCE.NS": "Bajaj Finance", "MARUTI.NS": "Maruti Suzuki", "SUNPHARMA.NS": "Sun Pharma",
        "ASIANPAINT.NS": "Asian Paints", "NTPC.NS": "NTPC", "ONGC.NS": "ONGC", "ULTRACEMCO.NS": "UltraTech Cement",
        "TITAN.NS": "Titan", "DLF.NS": "DLF", "005930.KS": "Samsung Electronics", "000660.KS": "SK Hynix",
        "005380.KS": "Hyundai Motor", "035420.KS": "Naver", "051910.KS": "LG Chem", "105560.KS": "KB Financial",
        "068270.KS": "Celltrion", "005490.KS": "POSCO Holdings", "2330.TW": "TSMC", "2317.TW": "Hon Hai (Foxconn)",
        "2454.TW": "MediaTek", "2308.TW": "Delta Electronics", "2881.TW": "Fubon Financial", "2412.TW": "Chunghwa Telecom",
        "2382.TW": "Quanta Computer", "BHP.AX": "BHP", "CBA.AX": "Commonwealth Bank", "CSL.AX": "CSL",
        "NAB.AX": "National Australia Bank", "WBC.AX": "Westpac", "WES.AX": "Wesfarmers", "MQG.AX": "Macquarie",
        "WDS.AX": "Woodside Energy", "FMG.AX": "Fortescue", "TLS.AX": "Telstra", "GMG.AX": "Goodman Group",
        "WOW.AX": "Woolworths", "D05.SI": "DBS", "O39.SI": "OCBC", "U11.SI": "UOB", "Z74.SI": "Singtel",
        "C38U.SI": "CapitaLand Integrated", "BBCA.JK": "Bank Central Asia", "BBRI.JK": "Bank Rakyat Indonesia",
        "TLKM.JK": "Telkom Indonesia", "ASII.JK": "Astra International",
    },
    "Canada": {
        "RY.TO": "Royal Bank of Canada", "TD.TO": "Toronto-Dominion Bank", "SHOP.TO": "Shopify", "ENB.TO": "Enbridge",
        "CNR.TO": "Canadian National Railway", "CP.TO": "Canadian Pacific Kansas City", "BN.TO": "Brookfield",
        "BMO.TO": "Bank of Montreal", "BNS.TO": "Scotiabank", "CNQ.TO": "Canadian Natural Resources", "SU.TO": "Suncor",
        "TRI.TO": "Thomson Reuters", "ATD.TO": "Alimentation Couche-Tard", "CSU.TO": "Constellation Software",
        "MFC.TO": "Manulife", "NTR.TO": "Nutrien", "ABX.TO": "Barrick", "FTS.TO": "Fortis", "BCE.TO": "BCE",
        "WCN.TO": "Waste Connections",
    },
    "Latin America": {
        # Brazil, Mexico and Chile on their own exchanges; Argentina, Peru, Colombia and the region's big tech through
        # their New York listings, since Yahoo's data from those exchanges is thin
        "PETR4.SA": "Petrobras", "VALE3.SA": "Vale", "ITUB4.SA": "Itau Unibanco", "BBDC4.SA": "Bradesco",
        "BBAS3.SA": "Banco do Brasil", "ABEV3.SA": "Ambev", "WEGE3.SA": "WEG", "B3SA3.SA": "B3", "SUZB3.SA": "Suzano",
        "RENT3.SA": "Localiza", "RADL3.SA": "Raia Drogasil", "VIVT3.SA": "Telefonica Brasil", "EQTL3.SA": "Equatorial",
        "PRIO3.SA": "PRIO", "SBSP3.SA": "Sabesp", "WALMEX.MX": "Walmart de Mexico", "AMXB.MX": "America Movil",
        "GFNORTEO.MX": "Banorte", "FEMSAUBD.MX": "FEMSA", "CEMEXCPO.MX": "Cemex", "GMEXICOB.MX": "Grupo Mexico",
        "GAPB.MX": "Grupo Aeroportuario del Pacifico", "SQM-B.SN": "SQM", "CHILE.SN": "Banco de Chile",
        "FALABELLA.SN": "Falabella", "COPEC.SN": "Empresas Copec", "MELI": "MercadoLibre", "NU": "Nu Holdings",
        "BAP": "Credicorp", "CIB": "Bancolombia", "YPF": "YPF", "GGAL": "Grupo Financiero Galicia",
    },
    "Africa": {
        # Johannesburg, quoted in cents (ZAc), which the app converts like pence
        "NPN.JO": "Naspers", "FSR.JO": "FirstRand", "SBK.JO": "Standard Bank", "MTN.JO": "MTN Group", "SOL.JO": "Sasol",
        "GFI.JO": "Gold Fields", "SHP.JO": "Shoprite", "CPI.JO": "Capitec", "VOD.JO": "Vodacom", "BID.JO": "Bid Corporation",
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
    # Latin American companies listed in New York: their business is in the region, not the US
    "MELI": "Latin America", "NU": "Latin America", "BAP": "Latin America", "CIB": "Latin America", "YPF": "Latin America", "GGAL": "Latin America",
}

REGION_BY_SUFFIX = {
    ".QA": "Gulf", ".SR": "Gulf", ".AE": "Gulf", ".AD": "Gulf", ".KW": "Gulf", ".BH": "Gulf",
    ".L": "Europe", ".PA": "Europe", ".DE": "Europe", ".AS": "Europe", ".SW": "Europe", ".CO": "Europe",
    ".MC": "Europe", ".MI": "Europe", ".BR": "Europe", ".ST": "Europe", ".OL": "Europe", ".HE": "Europe",
    ".AT": "Europe", ".LS": "Europe", ".VI": "Europe", ".IR": "Europe", ".F": "Europe", ".SG": "Europe", ".WA": "Europe",
    ".MU": "Europe", ".DU": "Europe", ".BE": "Europe", ".HM": "Europe", ".HA": "Europe",
    # Regions carry the markets' names, so a stock's region is the market you'd tick to find it
    ".T": "Asia-Pacific", ".HK": "Asia-Pacific", ".AX": "Asia-Pacific", ".NS": "Asia-Pacific", ".BO": "Asia-Pacific",
    ".SS": "Asia-Pacific", ".SZ": "Asia-Pacific", ".KS": "Asia-Pacific", ".KQ": "Asia-Pacific", ".TW": "Asia-Pacific",
    ".SI": "Asia-Pacific", ".NZ": "Asia-Pacific", ".JK": "Asia-Pacific", ".TO": "Canada", ".V": "Canada",
    ".SA": "Latin America", ".MX": "Latin America", ".SN": "Latin America", ".JO": "Africa",
    ".NYB": "US",
}

CURRENCY_BY_SUFFIX = {
    ".QA": "QAR", ".SR": "SAR", ".AE": "AED", ".AD": "AED", ".KW": "KWF", ".BH": "BHD", ".L": "GBp",
    ".PA": "EUR", ".DE": "EUR", ".AS": "EUR", ".MC": "EUR", ".MI": "EUR", ".BR": "EUR", ".HE": "EUR",
    ".AT": "EUR", ".LS": "EUR", ".VI": "EUR", ".IR": "EUR", ".SW": "CHF", ".CO": "DKK", ".ST": "SEK",
    ".OL": "NOK", ".F": "EUR", ".SG": "EUR", ".MU": "EUR", ".DU": "EUR", ".BE": "EUR", ".HM": "EUR", ".HA": "EUR", ".WA": "PLN", ".T": "JPY", ".HK": "HKD", ".AX": "AUD", ".NS": "INR", ".BO": "INR", ".SS": "CNY",
    ".SZ": "CNY", ".KS": "KRW", ".KQ": "KRW", ".TW": "TWD", ".SI": "SGD", ".NZ": "NZD", ".TO": "CAD", ".V": "CAD", ".SA": "BRL", ".MX": "MXN",
    ".SN": "CLP", ".JO": "ZAc", ".JK": "IDR",
    ".NYB": "USD",
}


# An older fund tracking almost the same index, whose history stands in before a fund's own begins, so a simulation
# isn't built from a few good years alone (VWCE.DE's starts in 2019, VT's in 2008, through the financial crisis).
# Each pair was checked in euros over the years both exist: yearly returns within 0.15 points (VWCE.DE 12.58% vs
# VT 12.71%, IWDA.AS 12.93% vs URTH 13.08%, CSPX.L 15.72% vs SPY 15.87%). Add a pair only after the same check.
STAND_INS = {"VWCE.DE": "VT", "IWDA.AS": "URTH", "EUNL.DE": "URTH", "CSPX.L": "SPY", "VUAA.DE": "SPY"}

# Broad, market-weighted index funds: the usual core of a core-and-satellite portfolio. Everything else (single
# stocks, sector, country, factor and thematic funds, crypto) starts as a satellite. Any holding can be switched.
CORE_FUNDS = {
    "VWCE.DE", "VWCE.MI", "VWCE.AS", "VWRL.L", "VWRA.L", "VWRL.AS", "IWDA.AS", "SWDA.L", "EUNL.DE", "ACWI", "URTH", "VT",
    "SPY", "VOO", "IVV", "CSPX.L", "VUSA.L", "SXR8.DE", "VTI", "VXUS", "VEA", "EFA", "VWO", "IEMG", "BND", "AGG",
}

# The currencies a broad fund's holdings are priced in, which is what moves its value for someone in another currency:
# VWCE trades in euros but is mostly US companies. Yahoo doesn't publish this, so these are the indexes' country
# weights, rounded (approximate, from the index factsheets; update when they drift). A fund that isn't here counts in
# the currency it trades in.
_ALL_WORLD = {"USD": 0.63, "EUR": 0.08, "JPY": 0.055, "GBP": 0.035, "CNY/HKD": 0.03, "CAD": 0.028, "CHF": 0.022,
              "TWD": 0.02, "INR": 0.02, "AUD": 0.017, "Other": 0.063}  # FTSE All-World, MSCI ACWI
_DEVELOPED = {"USD": 0.72, "EUR": 0.085, "JPY": 0.055, "GBP": 0.037, "CAD": 0.03, "CHF": 0.025, "AUD": 0.018, "Other": 0.03}  # MSCI World
_US_ONLY = {"USD": 1.0}
FUND_CURRENCIES = {
    **{t: _ALL_WORLD for t in ("VWCE.DE", "VWCE.MI", "VWCE.AS", "VWRL.L", "VWRA.L", "VWRL.AS", "ACWI", "VT")},
    **{t: _DEVELOPED for t in ("IWDA.AS", "SWDA.L", "EUNL.DE", "URTH")},
    **{t: _US_ONLY for t in ("SPY", "VOO", "IVV", "CSPX.L", "VUSA.L", "SXR8.DE", "VTI", "BND", "AGG")},
}


# The markets of single stocks, as the Screener, Opportunities and Ask offer them
STOCK_MARKETS = [m for m in UNIVERSE if m not in ("ETFs", "Crypto")]


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
