"""The first-run checklist on the dashboard: the few things that make every other page right for you, each ticked off
from what's already saved (never a separate "done" flag that could disagree with the data), until all the needed ones
are done or you hide it."""
from . import ai, db


def _saved(key):
    """Whether you've saved this setting yourself (a default never counts: the base currency is USD until you say)."""
    return bool(db.q("SELECT 1 FROM settings WHERE key=?", (key,)))


def steps(settings):
    """Each step with whether it's done and where it's done. `optional` ones never hold the checklist open."""
    plan = settings.get("plan") or {}
    trades = db.q("SELECT COUNT(*) AS n FROM transactions")[0]["n"]
    watched = db.q("SELECT COUNT(*) AS n FROM watchlist")[0]["n"]
    cfg = ai.config()
    return [
        {"key": "settings", "done": _saved("base_currency"), "goto": "settings", "target": "#sBase",
         "title": "Choose your base currency, regions and targets",
         "why": "Every value, gain and tax figure is in your base currency; the regions decide which stocks are screened; "
                "the targets drive rebalancing and orders."},
        {"key": "tax", "done": bool(settings.get("tax_residence")), "goto": "settings", "target": "#sTaxRes",
         "title": "Choose your tax residence",
         "why": "The Tax year card then applies your country's rules: how gains are matched, rates, allowances and the forms."},
        {"key": "trade", "done": trades > 0, "goto": "portfolio", "target": "#recordCard",
         "title": "Record your first trade",
         "why": "Positions, returns, risk, dividends and tax all come from the trades you record."},
        {"key": "plan", "done": bool(plan.get("monthly") or plan.get("goal")), "goto": "portfolio", "target": "#planCard",
         "title": "Set a monthly amount or a goal",
         "why": "Your plan says what's left to invest this month and how likely the goal is."},
        {"key": "watchlist", "done": watched > 0, "goto": "research", "target": None, "optional": True,
         "title": "Add a stock to your watchlist",
         "why": "The digest then follows it too."},
        {"key": "ai", "done": cfg["provider"] != "none" and not cfg.get("problem"), "goto": "ask", "target": None, "optional": True,
         "title": "Set up an AI model",
         "why": "For research briefs, Ask ClearYieldAI and screener sentences: set it in .env (see .env.example), then restart."},
    ]


def summary(settings):
    s = steps(settings)
    needed = [x for x in s if not x.get("optional")]
    left = sum(not x["done"] for x in needed)
    dismissed = bool(settings.get("setup_dismissed"))
    return {"steps": s, "left": left, "needed": len(needed), "dismissed": dismissed, "show": left > 0 and not dismissed}
