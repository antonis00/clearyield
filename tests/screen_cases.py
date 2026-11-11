"""Everyday Screener requests and the filters each should give, to measure how well requests are read.

`RULES` must be read by the keyword rules alone (checked offline in test_smoke.py). `AI` needs the model: the rules
must leave at least one word unread for those, so the model is asked, and tests/check_screen_ai.py runs them against
the real model in .env and counts the misreads.

Each expected dict lists every limit the reading should set: a limit missing, different or extra is a misread. "sort"
and "ignored" are only compared when listed. Industries are compared as sets, by the part of Yahoo's name given
("Insurance" stands for every insurance industry). Where two readings are both right, `EITHER` gives the other.
"""

RULES = [
    ("cheap european banks", {"markets": ["Europe"], "sector": "Financial Services", "industry": "Banks", "max_pe": 18}),
    ("CHEAP stocks", {"max_pe": 18}),
    ("dividend stocks in the gulf", {"countries": ["Qatar", "Saudi Arabia", "UAE", "Kuwait", "Bahrain", "Oman"], "min_div_yield": 0.025}),
    ("yield above 4 percent", {"min_div_yield": 0.04}),
    ("at least 4% dividend yield", {"min_div_yield": 0.04}),
    ("dividend payers yielding 5%+", {"min_div_yield": 0.05}),
    ("top 10 dividend stocks", {"min_div_yield": 0.025}),
    ("pe under 15", {"max_pe": 15.0}),
    ("cheaper than 12x earnings", {"max_pe": 12.0}),
    ("semiconductor stocks under 25 times earnings", {"max_pe": 25.0, "industry": ["Semiconductor"]}),
    ("P/E of 20 or less", {"max_pe": 20.0}),
    ("roe above 15% and debt to equity under 1", {"min_roe": 0.15, "max_de": 1.0}),
    ("low debt stocks with debt/equity below 0.3", {"max_de": 0.3}),
    ("revenue growth above 20%", {"min_rev_growth": 0.2}),
    ("growing 15% a year", {"min_rev_growth": 0.15}),
    ("volatility under 20%", {"max_vol": 0.2}),
    ("score above 70", {"min_score": 70.0}),
    ("quality score above 70", {"min_quality": 70.0}),
    ("safe dividend stocks, not tech", {"exclude_sector": "Technology", "min_div_yield": 0.025, "max_vol": 0.25}),
    ("us tech stocks with strong momentum", {"countries": ["United States"], "sector": "Technology", "min_momentum": 60}),
    ("show us cheap stocks", {"max_pe": 18}),
    ("large cap japanese tech", {"min_mcap": 10e9, "countries": ["Japan"], "sector": "Technology"}),
    ("mid cap stocks", {"min_mcap": 2e9, "max_mcap": 10e9}),
    ("small caps in india growing fast", {"max_mcap": 2e9, "countries": ["India"], "min_rev_growth": 0.1}),
    ("market cap above $50bn", {"min_mcap": 50e9}),
    ("oversold european stocks", {"max_rsi": 30, "markets": ["Europe"]}),
    ("overbought tech", {"min_rsi": 70, "sector": "Technology"}),
    ("stocks down 30% from their high", {"min_off_high": 0.3}),
    ("beaten down quality stocks", {"min_off_high": 0.25, "min_roe": 0.12}),
    ("companies near their 52 week low", {"max_above_low": 0.1}),
    ("mega cap tech near highs", {"min_mcap": 200e9, "max_off_high": 0.05, "sector": "Technology"}),
    ("up 20% over the past year", {"min_ret1y": 0.2}),
    ("fell 30% this year", {"max_ytd": -0.3}),
    ("stocks that beat the market", {"beat_market": True}),
    ("trading below book value", {"max_pb": 1.0}),
    ("largest european banks", {"markets": ["Europe"], "sector": "Financial Services", "industry": "Banks", "sort": "size"}),
    ("insurers in europe", {"markets": ["Europe"], "industry": ["Insurance"]}),
    ("big pharma paying 3% or more", {"min_mcap": 10e9, "min_div_yield": 0.03, "industry": ["Drug Manufacturers"]}),
    ("luxury brands", {"industry": ["Luxury Goods"]}),
    ("health insurers", {"industry": ["Healthcare Plans"]}),
    ("oil majors", {"industry": ["Oil & Gas Integrated"]}),
    ("gold miners near 52 week lows", {"industry": ["Gold"], "max_above_low": 0.1}),
    ("insurers and banks", {"industry": ["Insurance", "Banks"]}),
    ("european stocks excluding banks", {"markets": ["Europe"], "exclude_industry": "Banks"}),
    ("dividend stocks excluding tobacco", {"min_div_yield": 0.025, "exclude_industry": ["Tobacco"]}),
    ("excluding banks and insurers", {"exclude_industry": ["Banks", "Insurance"]}),
    ("not tech or energy", {"exclude_sector": ["Technology", "Energy"]}),
    ("yield under 2%", {"ignored": ["yield under 2%"]}),
    ("stocks that doubled over the last twelve months", {"min_ret1y": 1.0}),
    ("halved this year", {"max_ytd": -0.5}),
    ("quiet, steady stocks", {"max_vol": 0.25}),
    ("weapons makers", {"industry": ["Aerospace & Defense"]}),
]

AI = [
    ("posh fashion houses", {"industry": ["Luxury Goods"]}),
    ("companies with a wide moat", {}),
    ("dividend aristocrats in america", {"countries": ["United States"], "min_div_yield": 0.025}),
    ("cash-rich japanese firms", {"countries": ["Japan"], "max_de": 0.5}),
    ("stocks that got crushed but are still profitable", {"min_off_high": 0.25, "min_roe": 0.12}),
    ("firms trading on the cheap in the land of the rising sun", {"countries": ["Japan"], "max_pe": 18}),
    ("drug developers", {"industry": ["Biotechnology", "Drug Manufacturers"]}),
    ("fertiliser producers in the middle east", {"markets": ["Middle East & Africa"], "industry": ["Agricultural Inputs"]}),
    ("electricity companies yielding over 4%", {"min_div_yield": 0.04, "industry": ["Utilities - Regulated Electric"]}),
    ("names on sale that analysts like", {}),
    ("businesses worth more than a hundred billion dollars", {"min_mcap": 100e9}),
    ("dependable compounders", {"min_roe": 0.12}),
    ("miners of the yellow metal", {"industry": ["Gold"]}),
]
# Other readings that are right too, by request
EITHER = {
    "drug developers": [{"industry": ["Drug Manufacturers"]}],
    "electricity companies yielding over 4%": [{"min_div_yield": 0.04, "sector": "Utilities"}, {"min_div_yield": 0.04, "industry": ["Utilities"]}],
    "dependable compounders": [{"min_roe": 0.12, "max_vol": 0.25}, {"min_quality": 60.0}, {}],
    "names on sale that analysts like": [{"max_pe": 18}, {"min_off_high": 0.25}],
    "cash-rich japanese firms": [{"countries": ["Japan"]}],  # cash-rich isn't low debt; saying so is right too
}


def _same_industry(want, got):
    """Industries by the part of Yahoo's name given: ["Insurance"] matches all four insurance industries, "Banks"
    (the rules' text) matches the bank industries."""
    from backend.ai import industries_named
    if isinstance(want, str) and isinstance(got, str):
        return want.lower() == got.lower()
    expand = lambda v: {n for part in ([v] if isinstance(v, str) else v) for n in industries_named(part)}  # noqa: E731
    return expand(want) == expand(got)


def misread(expected, got, request=None):
    """What differs between the expected reading and the one given, as short notes; empty when they agree, or when it
    agrees with another right reading of the request (EITHER)."""
    notes = _differences(expected, got)
    if notes and any(not _differences(other, got) for other in EITHER.get(request, [])):
        return []
    return notes


def _differences(expected, got):
    notes = []
    for k, want in expected.items():
        have = got.get(k)
        if k in ("industry", "exclude_industry"):
            same = have is not None and _same_industry(want, have)
        elif isinstance(want, float) and isinstance(have, (int, float)):
            same = abs(want - have) <= abs(want) * 1e-6 + 1e-9
        elif isinstance(want, list) and isinstance(have, list):
            same = sorted(want) == sorted(have)
        else:
            same = want == have
        if not same:
            notes.append(f"{k}: wanted {want!r}, got {have!r}")
    for k, have in got.items():
        if k not in expected and k not in ("sort", "ignored"):
            # a region added beside the countries it holds changes nothing
            if k == "markets" and got.get("countries") and expected.get("countries"):
                from backend.universe import REGION_OF_COUNTRY
                if set(have) <= {REGION_OF_COUNTRY.get(c) for c in got["countries"]}:
                    continue
            notes.append(f"{k}: not wanted, got {have!r}")
    return notes
