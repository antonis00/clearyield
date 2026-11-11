"""Default ticker universe (Yahoo Finance symbols) used by the screener and search.

Edit freely - any Yahoo Finance symbol works in Research even if it is not listed here.
"""

# Single stocks by region, grouped by country within each (the country a stock is listed in; the New York listings
# of Latin American companies count as their own country). Funds and crypto are kinds of asset, not places.
UNIVERSE = {
    "North America": {
        # United States
        "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "GOOGL": "Alphabet", "AMZN": "Amazon",
        "META": "Meta Platforms", "AVGO": "Broadcom", "TSLA": "Tesla", "BRK-B": "Berkshire Hathaway", "JPM": "JPMorgan Chase",
        "V": "Visa", "MA": "Mastercard", "UNH": "UnitedHealth", "LLY": "Eli Lilly", "JNJ": "Johnson & Johnson",
        "PG": "Procter & Gamble", "KO": "Coca-Cola", "PEP": "PepsiCo", "XOM": "Exxon Mobil", "CVX": "Chevron",
        "HD": "Home Depot", "COST": "Costco", "WMT": "Walmart", "ABBV": "AbbVie", "MRK": "Merck", "ADBE": "Adobe",
        "CRM": "Salesforce", "AMD": "AMD", "ORCL": "Oracle", "NFLX": "Netflix", "INTC": "Intel", "CSCO": "Cisco",
        "QCOM": "Qualcomm", "TXN": "Texas Instruments", "MCD": "McDonald's", "NKE": "Nike", "DIS": "Disney",
        "CAT": "Caterpillar", "GS": "Goldman Sachs", "PFE": "Pfizer", "HON": "Honeywell", "GE": "GE Aerospace", "RTX": "RTX",
        "UNP": "Union Pacific", "UPS": "UPS", "DE": "Deere", "LMT": "Lockheed Martin", "BA": "Boeing", "ETN": "Eaton",
        "BAC": "Bank of America", "WFC": "Wells Fargo", "MS": "Morgan Stanley", "C": "Citigroup", "BLK": "BlackRock",
        "SCHW": "Charles Schwab", "AXP": "American Express", "SPGI": "S&P Global", "ABT": "Abbott", "TMO": "Thermo Fisher",
        "DHR": "Danaher", "AMGN": "Amgen", "ISRG": "Intuitive Surgical", "BMY": "Bristol-Myers Squibb", "GILD": "Gilead",
        "CVS": "CVS Health", "MDT": "Medtronic", "PM": "Philip Morris", "MO": "Altria", "MDLZ": "Mondelez",
        "CL": "Colgate-Palmolive", "LOW": "Lowe's", "SBUX": "Starbucks", "BKNG": "Booking", "TJX": "TJX",
        "GM": "General Motors", "F": "Ford", "COP": "ConocoPhillips", "SLB": "SLB", "EOG": "EOG Resources",
        "NEE": "NextEra Energy", "DUK": "Duke Energy", "SO": "Southern Company", "D": "Dominion Energy", "PLD": "Prologis",
        "AMT": "American Tower", "EQIX": "Equinix", "O": "Realty Income", "SPG": "Simon Property", "LIN": "Linde",
        "SHW": "Sherwin-Williams", "APD": "Air Products", "FCX": "Freeport-McMoRan", "NEM": "Newmont", "T": "AT&T",
        "VZ": "Verizon", "CMCSA": "Comcast", "TMUS": "T-Mobile US", "IBM": "IBM", "NOW": "ServiceNow", "INTU": "Intuit",
        "AMAT": "Applied Materials", "MU": "Micron", "PANW": "Palo Alto Networks",
        # Canada
        "RY.TO": "Royal Bank of Canada", "TD.TO": "Toronto-Dominion Bank", "SHOP.TO": "Shopify", "ENB.TO": "Enbridge",
        "CNR.TO": "Canadian National Railway", "CP.TO": "Canadian Pacific Kansas City", "BN.TO": "Brookfield",
        "BMO.TO": "Bank of Montreal", "BNS.TO": "Scotiabank", "CNQ.TO": "Canadian Natural Resources", "SU.TO": "Suncor",
        "TRI.TO": "Thomson Reuters", "ATD.TO": "Alimentation Couche-Tard", "CSU.TO": "Constellation Software",
        "MFC.TO": "Manulife", "NTR.TO": "Nutrien", "ABX.TO": "Barrick", "FTS.TO": "Fortis", "BCE.TO": "BCE",
        "WCN.TO": "Waste Connections",
    },
    "Europe": {
        # United Kingdom
        "AZN.L": "AstraZeneca", "SHEL.L": "Shell", "ULVR.L": "Unilever", "HSBA.L": "HSBC", "BARC.L": "Barclays",
        "LLOY.L": "Lloyds", "RR.L": "Rolls-Royce", "GSK.L": "GSK", "DGE.L": "Diageo", "BATS.L": "British American Tobacco",
        "TSCO.L": "Tesco", "BP.L": "BP", "RIO.L": "Rio Tinto", "GLEN.L": "Glencore", "AAL.L": "Anglo American",
        "NG.L": "National Grid", "SSE.L": "SSE", "VOD.L": "Vodafone", "SGRO.L": "Segro", "LAND.L": "Land Securities",
        # France
        "MC.PA": "LVMH", "OR.PA": "L'Oreal", "TTE.PA": "TotalEnergies", "AIR.PA": "Airbus", "RMS.PA": "Hermes",
        "BNP.PA": "BNP Paribas", "CS.PA": "AXA", "SU.PA": "Schneider Electric", "SAF.PA": "Safran", "DG.PA": "Vinci",
        "SAN.PA": "Sanofi", "EL.PA": "EssilorLuxottica", "RI.PA": "Pernod Ricard", "KER.PA": "Kering", "AI.PA": "Air Liquide",
        "ENGI.PA": "Engie", "ORA.PA": "Orange", "CAP.PA": "Capgemini", "DSY.PA": "Dassault Systemes",
        # Germany
        "SAP.DE": "SAP", "SIE.DE": "Siemens", "ALV.DE": "Allianz", "BMW.DE": "BMW", "DBK.DE": "Deutsche Bank",
        "MUV2.DE": "Munich Re", "RHM.DE": "Rheinmetall", "BAYN.DE": "Bayer", "ADS.DE": "Adidas", "VOW3.DE": "Volkswagen",
        "MBG.DE": "Mercedes-Benz", "BAS.DE": "BASF", "EOAN.DE": "E.ON", "RWE.DE": "RWE", "DTE.DE": "Deutsche Telekom",
        "IFX.DE": "Infineon", "VNA.DE": "Vonovia", "DHL.DE": "DHL Group",
        # Switzerland
        "NESN.SW": "Nestle", "RO.SW": "Roche", "UBSG.SW": "UBS", "ZURN.SW": "Zurich Insurance", "ABBN.SW": "ABB",
        "NOVN.SW": "Novartis", "LONN.SW": "Lonza", "CFR.SW": "Richemont", "SIKA.SW": "Sika",
        # Spain
        "SAN.MC": "Banco Santander", "IBE.MC": "Iberdrola", "BBVA.MC": "BBVA", "ITX.MC": "Inditex", "REP.MC": "Repsol",
        "TEF.MC": "Telefonica",
        # Italy
        "ENEL.MI": "Enel", "UCG.MI": "UniCredit", "ISP.MI": "Intesa Sanpaolo", "STLAM.MI": "Stellantis", "RACE.MI": "Ferrari",
        "ENI.MI": "Eni",
        # Netherlands
        "ASML.AS": "ASML", "INGA.AS": "ING", "ADYEN.AS": "Adyen", "PRX.AS": "Prosus", "HEIA.AS": "Heineken",
        # Denmark
        "NOVO-B.CO": "Novo Nordisk", "DSV.CO": "DSV", "MAERSK-B.CO": "Maersk",
        # Sweden
        "VOLV-B.ST": "Volvo", "ATCO-A.ST": "Atlas Copco", "ERIC-B.ST": "Ericsson",
        # Finland
        "NDA-FI.HE": "Nordea", "NOKIA.HE": "Nokia",
        # Belgium
        "ABI.BR": "AB InBev",
        # Norway
        "EQNR.OL": "Equinor",
    },
    "Latin America": {
        # Brazil
        "PETR4.SA": "Petrobras", "VALE3.SA": "Vale", "ITUB4.SA": "Itau Unibanco", "BBDC4.SA": "Bradesco",
        "BBAS3.SA": "Banco do Brasil", "ABEV3.SA": "Ambev", "WEGE3.SA": "WEG", "B3SA3.SA": "B3", "SUZB3.SA": "Suzano",
        "RENT3.SA": "Localiza", "RADL3.SA": "Raia Drogasil", "VIVT3.SA": "Telefonica Brasil", "EQTL3.SA": "Equatorial",
        "PRIO3.SA": "PRIO", "SBSP3.SA": "Sabesp", "NU": "Nu Holdings",
        # Mexico
        "WALMEX.MX": "Walmart de Mexico", "AMXB.MX": "America Movil", "GFNORTEO.MX": "Banorte", "FEMSAUBD.MX": "FEMSA",
        "CEMEXCPO.MX": "Cemex", "GMEXICOB.MX": "Grupo Mexico", "GAPB.MX": "Grupo Aeroportuario del Pacifico",
        # Chile
        "SQM-B.SN": "SQM", "CHILE.SN": "Banco de Chile", "FALABELLA.SN": "Falabella", "COPEC.SN": "Empresas Copec",
        # Argentina
        "YPF": "YPF", "GGAL": "Grupo Financiero Galicia",
        # Uruguay, and below Peru, Colombia, Argentina: New York listings (Yahoo's data from those exchanges is thin)
        "MELI": "MercadoLibre",
        # Peru
        "BAP": "Credicorp",
        # Colombia
        "CIB": "Bancolombia",
    },
    "Middle East & Africa": {
        # Saudi Arabia
        "2222.SR": "Saudi Aramco", "1120.SR": "Al Rajhi Bank", "2010.SR": "SABIC", "7010.SR": "STC",
        "1180.SR": "Saudi National Bank", "2082.SR": "ACWA Power", "1150.SR": "Alinma Bank", "1211.SR": "Ma'aden",
        "2280.SR": "Almarai", "5110.SR": "Saudi Electricity", "1010.SR": "Riyad Bank", "1060.SR": "Saudi Awwal Bank",
        "4002.SR": "Mouwasat Medical", "4190.SR": "Jarir Marketing",
        # Qatar
        "QNBK.QA": "QNB", "IQCD.QA": "Industries Qatar", "QIBK.QA": "Qatar Islamic Bank", "ORDS.QA": "Ooredoo",
        "MARK.QA": "Masraf Al Rayan", "QGTS.QA": "Nakilat", "CBQK.QA": "Commercial Bank of Qatar",
        "QEWS.QA": "Qatar Electricity & Water", "DUBK.QA": "Dukhan Bank", "QAMC.QA": "Qatar Aluminium",
        # South Africa (Johannesburg, quoted in cents (ZAc), which the app converts like pence)
        "NPN.JO": "Naspers", "FSR.JO": "FirstRand", "SBK.JO": "Standard Bank", "MTN.JO": "MTN Group", "SOL.JO": "Sasol",
        "GFI.JO": "Gold Fields", "SHP.JO": "Shoprite", "CPI.JO": "Capitec", "VOD.JO": "Vodacom", "BID.JO": "Bid Corporation",
        # Israel (Tel Aviv, quoted in agorot (ILA); Monday to Friday since 2026)
        "TEVA.TA": "Teva Pharmaceutical", "NICE.TA": "NICE", "LUMI.TA": "Bank Leumi", "POLI.TA": "Bank Hapoalim",
        "DSCT.TA": "Israel Discount Bank", "MZTF.TA": "Mizrahi Tefahot", "ICL.TA": "ICL Group", "ESLT.TA": "Elbit Systems",
        "AZRG.TA": "Azrieli Group", "BEZQ.TA": "Bezeq",
        # Turkey
        "THYAO.IS": "Turkish Airlines", "AKBNK.IS": "Akbank", "GARAN.IS": "Garanti BBVA", "KCHOL.IS": "Koc Holding",
        "BIMAS.IS": "BIM", "ASELS.IS": "Aselsan", "TUPRS.IS": "Tupras", "SISE.IS": "Sisecam", "EREGL.IS": "Erdemir",
        "YKBNK.IS": "Yapi Kredi",
        # UAE (Abu Dhabi's exchange, ADX, isn't on Yahoo at all)
        "EMAAR.AE": "Emaar Properties", "DIB.AE": "Dubai Islamic Bank", "EMIRATESNBD.AE": "Emirates NBD",
        "DEWA.AE": "Dubai Electricity & Water", "SALIK.AE": "Salik",
        # Kuwait
        "NBK.KW": "National Bank of Kuwait", "KFH.KW": "Kuwait Finance House", "ZAIN.KW": "Zain",
    },
    "Asia-Pacific": {
        # Japan
        "7203.T": "Toyota", "6758.T": "Sony", "8306.T": "Mitsubishi UFJ", "6861.T": "Keyence", "9984.T": "SoftBank Group",
        "6098.T": "Recruit", "9432.T": "NTT", "8035.T": "Tokyo Electron", "4063.T": "Shin-Etsu Chemical",
        "9983.T": "Fast Retailing", "7974.T": "Nintendo", "8058.T": "Mitsubishi Corp", "6501.T": "Hitachi", "4502.T": "Takeda",
        "4568.T": "Daiichi Sankyo", "7267.T": "Honda", "6367.T": "Daikin", "8316.T": "Sumitomo Mitsui",
        "8766.T": "Tokio Marine", "2914.T": "Japan Tobacco", "9433.T": "KDDI", "8802.T": "Mitsubishi Estate",
        "1605.T": "Inpex", "5401.T": "Nippon Steel", "9502.T": "Chubu Electric",
        # India
        "RELIANCE.NS": "Reliance Industries", "TCS.NS": "Tata Consultancy", "HDFCBANK.NS": "HDFC Bank", "INFY.NS": "Infosys",
        "ICICIBANK.NS": "ICICI Bank", "HINDUNILVR.NS": "Hindustan Unilever", "ITC.NS": "ITC", "BHARTIARTL.NS": "Bharti Airtel",
        "LT.NS": "Larsen & Toubro", "SBIN.NS": "State Bank of India", "BAJFINANCE.NS": "Bajaj Finance",
        "MARUTI.NS": "Maruti Suzuki", "SUNPHARMA.NS": "Sun Pharma", "ASIANPAINT.NS": "Asian Paints", "NTPC.NS": "NTPC",
        "ONGC.NS": "ONGC", "ULTRACEMCO.NS": "UltraTech Cement", "TITAN.NS": "Titan", "DLF.NS": "DLF",
        # Hong Kong (incl. the big Chinese companies listed there; mainland A-shares (.SS/.SZ) are left out: Yahoo's data for them is patchy)
        "0700.HK": "Tencent", "9988.HK": "Alibaba", "1299.HK": "AIA", "0939.HK": "China Construction Bank", "1398.HK": "ICBC",
        "3690.HK": "Meituan", "1810.HK": "Xiaomi", "2318.HK": "Ping An", "0941.HK": "China Mobile", "0883.HK": "CNOOC",
        "0388.HK": "HKEX", "1211.HK": "BYD", "0016.HK": "Sun Hung Kai Properties", "0002.HK": "CLP Holdings",
        "9618.HK": "JD.com", "2020.HK": "Anta Sports", "1088.HK": "China Shenhua",
        # Australia
        "BHP.AX": "BHP", "CBA.AX": "Commonwealth Bank", "CSL.AX": "CSL", "NAB.AX": "National Australia Bank",
        "WBC.AX": "Westpac", "WES.AX": "Wesfarmers", "MQG.AX": "Macquarie", "WDS.AX": "Woodside Energy", "FMG.AX": "Fortescue",
        "TLS.AX": "Telstra", "GMG.AX": "Goodman Group", "WOW.AX": "Woolworths",
        # South Korea
        "005930.KS": "Samsung Electronics", "000660.KS": "SK Hynix", "005380.KS": "Hyundai Motor", "035420.KS": "Naver",
        "051910.KS": "LG Chem", "105560.KS": "KB Financial", "068270.KS": "Celltrion", "005490.KS": "POSCO Holdings",
        # Taiwan
        "2330.TW": "TSMC", "2317.TW": "Hon Hai (Foxconn)", "2454.TW": "MediaTek", "2308.TW": "Delta Electronics",
        "2881.TW": "Fubon Financial", "2412.TW": "Chunghwa Telecom", "2382.TW": "Quanta Computer",
        # Singapore
        "D05.SI": "DBS", "O39.SI": "OCBC", "U11.SI": "UOB", "Z74.SI": "Singtel", "C38U.SI": "CapitaLand Integrated",
        # Indonesia
        "BBCA.JK": "Bank Central Asia", "BBRI.JK": "Bank Rakyat Indonesia", "TLKM.JK": "Telkom Indonesia",
        "ASII.JK": "Astra International",
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

# Yahoo's industry names for the stocks in the list, with their sectors (read from Yahoo, November 2026), so the
# Screener can filter any of them ("insurers", "luxury", "chipmakers"). A stock added under a new industry needs it here.
INDUSTRIES = {
    **dict.fromkeys(['Agricultural Inputs', 'Aluminum', 'Building Materials', 'Chemicals', 'Copper', 'Gold', 'Other Industrial Metals & Mining', 'Paper & Paper Products', 'Specialty Chemicals', 'Steel'], 'Basic Materials'),
    **dict.fromkeys(['Electronic Gaming & Multimedia', 'Entertainment', 'Internet Content & Information', 'Telecom Services'], 'Communication Services'),
    **dict.fromkeys(['Apparel Retail', 'Auto Manufacturers', 'Department Stores', 'Footwear & Accessories', 'Home Improvement Retail', 'Internet Retail', 'Leisure', 'Luxury Goods', 'Restaurants', 'Specialty Retail', 'Travel Services'], 'Consumer Cyclical'),
    **dict.fromkeys(['Beverages - Brewers', 'Beverages - Non-Alcoholic', 'Beverages - Wineries & Distilleries', 'Confectioners', 'Discount Stores', 'Food Distribution', 'Grocery Stores', 'Household & Personal Products', 'Packaged Foods', 'Tobacco'], 'Consumer Defensive'),
    **dict.fromkeys(['Oil & Gas E&P', 'Oil & Gas Equipment & Services', 'Oil & Gas Integrated', 'Oil & Gas Midstream', 'Oil & Gas Refining & Marketing', 'Thermal Coal'], 'Energy'),
    **dict.fromkeys(['Asset Management', 'Banks - Diversified', 'Banks - Regional', 'Capital Markets', 'Credit Services', 'Financial Data & Stock Exchanges', 'Insurance - Diversified', 'Insurance - Life', 'Insurance - Property & Casualty', 'Insurance - Reinsurance'], 'Financial Services'),
    **dict.fromkeys(['Biotechnology', 'Diagnostics & Research', 'Drug Manufacturers - General', 'Drug Manufacturers - Specialty & Generic', 'Healthcare Plans', 'Medical Care Facilities', 'Medical Devices', 'Medical Instruments & Supplies', 'Pharmaceutical Retailers'], 'Healthcare'),
    **dict.fromkeys(['Aerospace & Defense', 'Airlines', 'Airports & Air Services', 'Building Products & Equipment', 'Conglomerates', 'Electrical Equipment & Parts', 'Engineering & Construction', 'Farm & Heavy Construction Machinery', 'Infrastructure Operations', 'Integrated Freight & Logistics', 'Marine Shipping', 'Railroads', 'Rental & Leasing Services', 'Specialty Business Services', 'Specialty Industrial Machinery', 'Waste Management'], 'Industrials'),
    **dict.fromkeys(['REIT - Diversified', 'REIT - Industrial', 'REIT - Retail', 'REIT - Specialty', 'Real Estate - Development', 'Real Estate - Diversified', 'Real Estate Services'], 'Real Estate'),
    **dict.fromkeys(['Communication Equipment', 'Computer Hardware', 'Consumer Electronics', 'Electronic Components', 'Information Technology Services', 'Scientific & Technical Instruments', 'Semiconductor Equipment & Materials', 'Semiconductors', 'Software - Application', 'Software - Infrastructure'], 'Technology'),
    **dict.fromkeys(['Utilities - Diversified', 'Utilities - Regulated Electric', 'Utilities - Regulated Water'], 'Utilities'),
}

# The five regions, and each country's. A stock's country is where it's listed (from its symbol's suffix, or by name
# for the New York listings of foreign companies); its region follows from the country.
REGIONS = ["North America", "Europe", "Latin America", "Middle East & Africa", "Asia-Pacific"]
COUNTRY_BY_SUFFIX = {
    ".QA": "Qatar", ".SR": "Saudi Arabia", ".AE": "UAE", ".AD": "UAE", ".KW": "Kuwait", ".BH": "Bahrain",
    ".OM": "Oman", ".TA": "Israel", ".IS": "Turkey", ".JO": "South Africa", ".L": "United Kingdom", ".PA": "France",
    ".DE": "Germany", ".F": "Germany", ".SG": "Germany", ".MU": "Germany", ".DU": "Germany", ".BE": "Germany",
    ".HM": "Germany", ".HA": "Germany", ".AS": "Netherlands", ".SW": "Switzerland", ".CO": "Denmark", ".MC": "Spain",
    ".MI": "Italy", ".BR": "Belgium", ".ST": "Sweden", ".OL": "Norway", ".HE": "Finland", ".AT": "Greece",
    ".LS": "Portugal", ".VI": "Austria", ".IR": "Ireland", ".WA": "Poland", ".T": "Japan", ".HK": "Hong Kong",
    ".NS": "India", ".BO": "India", ".SS": "China", ".SZ": "China", ".KS": "South Korea", ".KQ": "South Korea",
    ".TW": "Taiwan", ".AX": "Australia", ".NZ": "New Zealand", ".SI": "Singapore", ".JK": "Indonesia", ".TO": "Canada",
    ".V": "Canada", ".SA": "Brazil", ".MX": "Mexico", ".SN": "Chile",
}
# Foreign companies listed only in New York in the list: their business is in their own country
COUNTRY_BY_TICKER = {
    "MELI": "Uruguay", "NU": "Brazil", "BAP": "Peru", "CIB": "Colombia", "YPF": "Argentina", "GGAL": "Argentina",
}
REGION_OF_COUNTRY = {
    "United States": "North America", "Canada": "North America", "Brazil": "Latin America", "Mexico": "Latin America",
    "Chile": "Latin America", "Argentina": "Latin America", "Peru": "Latin America", "Colombia": "Latin America",
    "Uruguay": "Latin America", "Qatar": "Middle East & Africa", "Saudi Arabia": "Middle East & Africa", "UAE": "Middle East & Africa",
    "Kuwait": "Middle East & Africa", "Bahrain": "Middle East & Africa", "Oman": "Middle East & Africa", "Israel": "Middle East & Africa",
    "Turkey": "Middle East & Africa", "South Africa": "Middle East & Africa", "Japan": "Asia-Pacific", "Hong Kong": "Asia-Pacific",
    "India": "Asia-Pacific", "China": "Asia-Pacific", "South Korea": "Asia-Pacific", "Taiwan": "Asia-Pacific",
    "Australia": "Asia-Pacific", "New Zealand": "Asia-Pacific", "Singapore": "Asia-Pacific", "Indonesia": "Asia-Pacific",
}
# Countries named together: "Gulf banks" means these
COUNTRY_GROUPS = {"Gulf": ["Qatar", "Saudi Arabia", "UAE", "Kuwait", "Bahrain", "Oman"]}
# The markets the app had before the five regions, read as the countries they meant, so a saved link, an old
# setting or a model that still says "US" or "Gulf" gets what it asked for rather than nothing
LEGACY_MARKETS = {"US": ["United States"], "Canada": ["Canada"], "Gulf": COUNTRY_GROUPS["Gulf"], "Africa": ["South Africa"]}

# Funds whose region is what they hold, not where they're listed: VWCE trades in Frankfurt but owns the world
REGION_BY_TICKER = {
    "VWCE.DE": "Global", "SPY": "North America", "QQQ": "North America", "VWO": "Emerging", "VXUS": "International", "EFA": "International",
    "KSA": "Middle East & Africa", "QAT": "Middle East & Africa",
}

CURRENCY_BY_SUFFIX = {
    ".QA": "QAR", ".SR": "SAR", ".AE": "AED", ".AD": "AED", ".KW": "KWF", ".BH": "BHD", ".L": "GBp",
    ".PA": "EUR", ".DE": "EUR", ".AS": "EUR", ".MC": "EUR", ".MI": "EUR", ".BR": "EUR", ".HE": "EUR",
    ".AT": "EUR", ".LS": "EUR", ".VI": "EUR", ".IR": "EUR", ".SW": "CHF", ".CO": "DKK", ".ST": "SEK",
    ".OL": "NOK", ".F": "EUR", ".SG": "EUR", ".MU": "EUR", ".DU": "EUR", ".BE": "EUR", ".HM": "EUR", ".HA": "EUR", ".WA": "PLN", ".T": "JPY", ".HK": "HKD", ".AX": "AUD", ".NS": "INR", ".BO": "INR", ".SS": "CNY",
    ".SZ": "CNY", ".KS": "KRW", ".KQ": "KRW", ".TW": "TWD", ".SI": "SGD", ".NZ": "NZD", ".TO": "CAD", ".V": "CAD", ".SA": "BRL", ".MX": "MXN",
    ".SN": "CLP", ".JO": "ZAc", ".JK": "IDR", ".TA": "ILA", ".IS": "TRY", ".OM": "OMR",
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


# The regions of single stocks, as the Screener, Opportunities, Ask and Settings offer them
STOCK_MARKETS = [m for m in UNIVERSE if m not in ("ETFs", "Crypto")]
assert STOCK_MARKETS == REGIONS, "the list's regions and REGIONS must match"


def default_bucket(ticker):
    return "core" if ticker in CORE_FUNDS else "satellite"


def name_of(ticker):
    for group in UNIVERSE.values():
        if ticker in group:
            return group[ticker]
    return MACRO.get(ticker, ticker)


def country_of(ticker):
    """The country a stock is listed in, None for crypto and for an exchange the app doesn't know."""
    if ticker in COUNTRY_BY_TICKER:
        return COUNTRY_BY_TICKER[ticker]
    if ticker.endswith("-USD"):
        return None
    for suf in sorted(COUNTRY_BY_SUFFIX, key=len, reverse=True):
        if ticker.endswith(suf):
            return COUNTRY_BY_SUFFIX[suf]
    return None if "." in ticker else "United States"


def region_of(ticker, quote_type=None):
    if ticker in REGION_BY_TICKER:
        return REGION_BY_TICKER[ticker]
    if ticker.endswith("-USD") or quote_type == "CRYPTOCURRENCY":
        return "Crypto"
    country = country_of(ticker)
    return REGION_OF_COUNTRY.get(country, "Europe") if country else "Other"


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


def countries_in(region):
    """The countries of the list's stocks in a region, most stocks first."""
    from collections import Counter
    return [c for c, _ in Counter(country_of(t) for t in UNIVERSE.get(region, {})).most_common()]
