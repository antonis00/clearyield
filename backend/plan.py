"""Your plan: what you invest each month and the goal it's for. Answers "what do I do this month" (the month's money,
what's already gone in, and orders drafted for the rest by the Rebalancing card's rules) and "am I on track" (possible
futures of today's holdings with the monthly money added, from simulate.monte_carlo, against the goal in today's
money). Nothing here is saved or placed: the orders are a draft, prepared again in the Orders card to approve."""
import datetime as dt
import math

from . import data, db, ledger, orders, portfolio, simulate

PATHS = 2000  # futures drawn for the goal: the chance moves under a point between this and 5,000
CAVEAT = ("Possible futures of what you hold today, drawn from its own history with the yearly return set to the assumption, "
          "in today's money. A range, not a forecast; before taxes.")


def _month_flows(rows, base, today):
    """Money put in this calendar month in the base currency: purchases less sales, each at today's rate."""
    start = today.replace(day=1).isoformat()
    net, trades = 0.0, 0
    for r in rows:
        if r.get("kind") not in ("buy", "sell") or not r.get("date") or r["date"] < start or r["date"] > today.isoformat():
            continue
        cur = data.info(r["ticker"]).get("currency") or base
        major, divisor = data.MINOR_UNITS.get(cur, (cur, 1.0))
        amount = r["shares"] * r["price"] / divisor * data.fx_rate(major, base)
        net += amount if r["kind"] == "buy" else -amount
        trades += 1
    return net, trades


def needed_monthly(start, goal, years, yearly):
    """The monthly amount that reaches `goal` from `start` in `years` at a steady `yearly` return (after inflation),
    added at the start of each month: the arithmetic behind "about this much a month"."""
    n = max(1, round(years * 12))
    rm = (1 + yearly) ** (1 / 12) - 1
    grown = start * (1 + rm) ** n
    if grown >= goal:
        return 0.0
    factor = n if abs(rm) < 1e-12 else ((1 + rm) ** n - 1) / rm * (1 + rm)
    return (goal - grown) / factor


def _mix(summary):
    """Today's holdings as a mix the simulation takes: the largest MAX_ASSETS, scaled to 100%."""
    mix = simulate.current_mix(summary)[:simulate.MAX_ASSETS]
    total = sum(m["weight"] for m in mix)
    if not mix or not total:
        return []
    mix = [{"ticker": m["ticker"], "weight": round(m["weight"] * 100 / total, 2)} for m in mix]
    mix[0]["weight"] = round(mix[0]["weight"] + 100 - sum(m["weight"] for m in mix), 2)
    return mix


def summary(settings, today=None, with_orders=True):
    today = today or dt.date.today()
    p = settings.get("plan") or {}
    monthly, goal, by = float(p.get("monthly") or 0), float(p.get("goal") or 0), p.get("by") or ""
    expected, inflation = float(p.get("expected", 0.06)), float(p.get("inflation", 0.02))
    s = portfolio.summary(settings)
    s.pop("_series", None)
    base = s["base"]
    out = {"base": base, "monthly": monthly, "goal": goal, "by": by, "expected": expected, "inflation": inflation,
           "total": s.get("total") or 0.0, "month": today.strftime("%B %Y"), "set": bool(monthly or goal)}
    net, trades = _month_flows(db.q("SELECT * FROM transactions"), base, today)
    left = max(0.0, monthly - net)
    out["thisMonth"] = {"invested": net, "trades": trades, "left": left, "orders": [], "notes": [], "empty": []}
    if with_orders and left > 0 and s.get("positions"):
        try:
            d = orders.plan(settings, new_money=left, fractional=True)
            out["thisMonth"].update(orders=[{k: o.get(k) for k in ("ticker", "name", "assetClass", "shares", "value", "currency")} for o in d["orders"]],
                                    notes=d["notes"], empty=d.get("empty") or [])
        except orders.OrderError as e:
            out["thisMonth"]["notes"] = [str(e)]
    if goal > 0 and by:
        out["goalCheck"] = goal_check(s, base, monthly, goal, by, expected, inflation, today)
    return out


def goal_check(s, base, monthly, goal, by, expected, inflation, today):
    try:
        end = dt.date.fromisoformat(by)
    except ValueError:
        return {"error": "The goal's date isn't a date."}
    years = (end - today).days / 365.25
    if years <= 0:
        return {"error": "The goal's date has passed: set a later one."}
    total = s.get("total") or 0.0
    real = (1 + expected) / (1 + inflation) - 1
    out = {"years": years, "needed": needed_monthly(total, goal, years, real), "realYearly": real, "caveat": CAVEAT}
    mix = _mix(s)
    if not mix or total <= 0:
        out["note"] = "Record what you hold for the chance of reaching it; until then only the monthly amount is worked out."
        return out
    if years < 1:
        out["note"] = "Under a year away: the simulation needs a year or more, so only the monthly amount is worked out."
        return out
    try:
        mc = simulate.monte_carlo(mix, base, min(years, 50), total, monthly, "yearly", 10.0, "max", PATHS, expected, inflation, goal)
    except (ValueError, data.DataError) as e:
        out["note"] = f"No simulation: {e}"
        return out
    f = mc["final"]
    deflate = (1 + inflation) ** years
    out.update(chance=f["reachGoal"], median=f["medianReal"] if f["medianReal"] is not None else f["median"],
               p10=f["p10"] / deflate, p90=f["p90"] / deflate, invested=total + monthly * round(years * 12),
               mix=mix, historyFrom=mc["assumption"]["historyFrom"], standIns=mc["standIns"])
    return out


def check(v):
    """The plan setting, checked (db.check_settings)."""
    if not isinstance(v, dict) or any(k not in ("monthly", "goal", "by", "expected", "inflation") for k in v):
        raise ValueError("The plan has a monthly amount, a goal, its date, a yearly return and inflation.")
    out = {}
    for k, lo, hi in (("monthly", 0, 1e9), ("goal", 0, 1e12), ("expected", -0.2, 0.2), ("inflation", 0, 0.15)):
        if k in v:
            x = v[k]
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not lo <= x <= hi:
                raise ValueError(f"The plan's {k} must be a number from {lo:g} to {hi:g}.")
            out[k] = float(x)
    if "by" in v:
        if v["by"] in ("", None):
            out["by"] = ""
        else:
            try:
                out["by"] = dt.date.fromisoformat(str(v["by"])).isoformat()
            except ValueError:
                raise ValueError("The goal's date must be a date such as 2040-01-01.")
    return out


def digest_line(settings, today=None):
    """For the digest: the month's money not yet invested, or None."""
    try:
        p = summary(settings, today, with_orders=False)
    except (ledger.LedgerError, data.DataError):
        return None
    m = p["thisMonth"]
    if p["monthly"] > 0 and m["left"] > 0.005 * p["monthly"]:
        return p, m
    return None
