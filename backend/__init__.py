import os

# The app was called StockPilot: settings still given under the old names (STOCKPILOT_DB, STOCKPILOT_DEMO, ...) count
# as the new ones, unless a new one is set too.
for _k, _v in list(os.environ.items()):
    if _k.startswith("STOCKPILOT_"):
        os.environ.setdefault("CLEARYIELD_" + _k[len("STOCKPILOT_"):], _v)
