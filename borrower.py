"""Step 1 - the borrower's repayment potential, worked out before any product is chosen.

Pure functions, no UI: credit-score bands, income, net worth, past repayment record, existing obligations, FOIR,
the surplus left for a new EMI and the indicative loan it supports. Every input may be missing - a new borrower has
no bureau history, a proprietor has no shareholding - so each function says what it could not work out instead of
failing, and nothing missing is ever guessed.

Thresholds come from kpis.thresholds() (bureau_strong / good / fair, foir_strong / foir_max / foir_weak, dpd_watch /
dpd_adverse) and can be changed per case in the sidebar.
"""

import math

INCOME_SOURCES = ["Business income", "Salary", "Rental", "Interest / dividend", "Agricultural", "Other"]
FREQUENCIES = {"Monthly": 12, "Annual": 1}
STABILITY = ["Stable", "Variable", "One-off"]
ASSET_TYPES = ["Property", "Financial investments", "Vehicles", "Business equity", "Other"]
LIABILITY_TYPES = ["Loans", "Credit cards", "Payables / dues", "Other"]
BASIS = ["Self-declared", "Documented"]
HISTORY_STATUS = ["Closed", "Regular", "Delayed", "Restructured", "Settled", "Written-off"]
OBLIGATION_KINDS = ["Fund based", "Non-fund based"]

VERDICTS = {
    "Strong": ("good", "Comfortable capacity for new debt: low obligations against income, a strong bureau record."),
    "Adequate": ("good", "Can take on new debt within normal limits."),
    "Stretched": ("warning", "Little headroom: high obligations or a patchy record. Size and structure the request carefully."),
    "Weak": ("critical", "Existing obligations, bureau record or past defaults leave little room for new debt."),
    "Not assessed": ("info", "Not enough information yet - see what is missing."),
}


def _n(v):
    """A number, or None for blanks / text / NaN (never a guess)."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rows(rows) -> list:
    return [r for r in (rows or []) if isinstance(r, dict) and any(v not in (None, "") for v in r.values())]


# ---------------------------------------------------------------- credit score

def bureau_band(score, th: dict) -> tuple[str, str]:
    """(band, status) for a CIBIL-style score 300-900. Out-of-range or blank -> ('No score', 'info')."""
    s = _n(score)
    if s is None or not 300 <= s <= 900:
        return "No score", "info"
    if s >= th["bureau_strong"]:
        return "Strong", "good"
    if s >= th["bureau_good"]:
        return "Good", "good"
    if s >= th["bureau_fair"]:
        return "Fair", "warning"
    return "Weak", "critical"


def scores_summary(rows, no_score: bool, th: dict) -> dict:
    """Bands each score; the weakest band among the borrower and its people drives the assessment."""
    order = ["Weak", "Fair", "Good", "Strong"]
    out, scored = [], []
    for r in _rows(rows):
        band, st = bureau_band(r.get("score"), th)
        out.append({**r, "band": band, "status": st})
        if band != "No score":
            scored.append(band)
    worst = min(scored, key=order.index) if scored else ("New to credit" if no_score else None)
    status = {"Strong": "good", "Good": "good", "Fair": "warning", "Weak": "critical"}.get(worst, "info")
    return {"rows": out, "worst": worst, "status": status, "count": len(scored)}


# ---------------------------------------------------------------- income

def annual(amount, frequency) -> float | None:
    a = _n(amount)
    return None if a is None else a * FREQUENCIES.get(frequency or "Annual", 1)


def income_summary(rows) -> dict:
    """Total annual income, the stable part, and the eligible part used for FOIR (stable + variable; one-off
    receipts are left out because they will not recur)."""
    total = stable = eligible = 0.0
    out, n = [], 0
    for r in _rows(rows):
        a = annual(r.get("amount"), r.get("frequency"))
        out.append({**r, "annual": a})
        if a is None:
            continue
        n += 1
        total += a
        if r.get("stability") == "Stable":
            stable += a
        if r.get("stability") != "One-off":
            eligible += a
    return {"rows": out, "count": n, "total_annual": total if n else None, "stable_annual": stable if n else None,
            "eligible_annual": eligible if n else None, "eligible_monthly": eligible / 12 if n else None,
            "proof_share": (sum(1 for r in out if r.get("proof") == "Y" and r["annual"]) / n) if n else None}


# ---------------------------------------------------------------- net worth

def net_worth(assets, liabilities) -> dict:
    a_rows = [{**r, "value": _n(r.get("value"))} for r in _rows(assets)]
    l_rows = [{**r, "value": _n(r.get("value"))} for r in _rows(liabilities)]
    a = sum(r["value"] or 0 for r in a_rows)
    l_ = sum(r["value"] or 0 for r in l_rows)
    documented = sum(r["value"] or 0 for r in a_rows if r.get("basis") == "Documented")
    have = any(r["value"] is not None for r in a_rows + l_rows)
    return {"assets": a_rows, "liabilities": l_rows, "total_assets": a if have else None,
            "total_liabilities": l_ if have else None, "net_worth": a - l_ if have else None,
            "documented_share": documented / a if have and a else None}


# ---------------------------------------------------------------- past repayment record

def track_record(rows, th: dict) -> dict:
    """Rating from past and closed loans / cards: Clean, Satisfactory, Weak, Adverse - or No history."""
    rows = _rows(rows)
    if not rows:
        return {"rating": "No history", "status": "info", "reasons": ["No past loans or cards recorded"], "rows": []}
    reasons, rating = [], "Clean"
    rank = {"Clean": 0, "Satisfactory": 1, "Weak": 2, "Adverse": 3}

    def worse(r, why):
        nonlocal rating
        reasons.append(why)
        if rank[r] > rank[rating]:
            rating = r
    for r in rows:
        name = r.get("lender") or "a lender"
        dpd = _n(r.get("max_dpd")) or 0
        st = r.get("status")
        if st in ("Written-off", "Settled") or r.get("default") == "Y":
            worse("Adverse", f"{st if st in ('Written-off', 'Settled') else 'Default'} with {name}")
        elif st == "Restructured" or dpd >= th["dpd_adverse"]:
            worse("Weak", f"{'Restructured' if st == 'Restructured' else f'{dpd:.0f} days past due'} with {name}")
        elif st == "Delayed" or dpd >= th["dpd_watch"]:
            worse("Satisfactory", f"Delays (up to {dpd:.0f} days past due) with {name}")
    status = {"Clean": "good", "Satisfactory": "good", "Weak": "warning", "Adverse": "critical"}[rating]
    if not reasons:
        reasons.append(f"{len(rows)} loan(s) / card(s) repaid regularly")
    return {"rating": rating, "status": status, "reasons": reasons, "rows": rows}


# ---------------------------------------------------------------- existing obligations

def obligations_summary(rows) -> dict:
    """Live obligations. Rows marked to be closed with the new loan are left out of the monthly obligations."""
    rows = [{**r, "outstanding": _n(r.get("outstanding")), "emi": _n(r.get("emi"))} for r in _rows(rows)]
    fund = [r for r in rows if r.get("kind") != "Non-fund based"]
    return {"rows": rows, "count": len(rows),
            "total_outstanding": sum(r["outstanding"] or 0 for r in fund),
            "nonfund_outstanding": sum(r["outstanding"] or 0 for r in rows if r.get("kind") == "Non-fund based"),
            "monthly": sum(r["emi"] or 0 for r in rows if r.get("close_with_new") != "Y"),
            "closing": [r for r in rows if r.get("close_with_new") == "Y"],
            "secured_outstanding": sum(r["outstanding"] or 0 for r in fund if r.get("secured") == "Secured")}


# ---------------------------------------------------------------- FOIR and headroom

def foir(monthly_obligations, monthly_income) -> float | None:
    """Fixed obligations to income ratio. None without income."""
    inc, ob = _n(monthly_income), _n(monthly_obligations) or 0
    return None if not inc or inc <= 0 else ob / inc


def surplus(monthly_income, monthly_obligations, foir_cap) -> float | None:
    """Monthly room for a new EMI: income x FOIR cap, less what is already committed (never below zero)."""
    inc = _n(monthly_income)
    if not inc or inc <= 0:
        return None
    return max(0.0, inc * foir_cap - (_n(monthly_obligations) or 0))


def max_loan(emi, rate_pct, tenure_months) -> float | None:
    """Principal an EMI repays over the tenure at the rate (present value of an annuity)."""
    e, n = _n(emi), int(_n(tenure_months) or 0)
    if e is None or n <= 0:
        return None
    i = (_n(rate_pct) or 0) / 1200
    return e * n if i == 0 else e * (1 - (1 + i) ** -n) / i


# ---------------------------------------------------------------- overall

def assess(profile: dict, th: dict) -> dict:
    """The repayment-potential panel for step 1. profile: case['borrower_profile']."""
    p = profile or {}
    sc = scores_summary(p.get("scores"), bool(p.get("no_score")), th)
    inc = income_summary(p.get("income"))
    nw = net_worth(p.get("assets"), p.get("liabilities"))
    tr = track_record(p.get("history"), th)
    ob = obligations_summary(p.get("obligations"))
    monthly = inc["eligible_monthly"]
    f = foir(ob["monthly"], monthly)
    room = surplus(monthly, ob["monthly"], th["foir_max"])
    rate, tenure = _n(p.get("rate_pct")) or 0, int(_n(p.get("tenure_months")) or 0)
    loan = max_loan(room, rate, tenure) if room is not None else None

    missing = []
    if not inc["count"]:
        missing.append("income sources")
    if not sc["count"] and not p.get("no_score"):
        missing.append("credit scores (or tick 'no score / new to credit')")
    if not tr["rows"]:
        missing.append("past repayment record")
    if nw["net_worth"] is None:
        missing.append("net worth")

    observations, flags = [], []

    def flag(sev, text, step="Step 1"):
        flags.append({"severity": sev, "area": "Repayment potential", "finding": text, "evidence": step,
                      "next_step": "Review the borrower profile in step 1"})

    if f is not None:
        observations.append(f"Existing obligations take {f:.0%} of eligible monthly income (FOIR cap {th['foir_max']:.0%}).")
        if f > th["foir_weak"]:
            flag("High", f"FOIR already {f:.0%} before any new loan")
        elif f > th["foir_max"]:
            flag("Medium", f"FOIR already {f:.0%} - above the {th['foir_max']:.0%} cap")
    if room is not None:
        observations.append(f"Room for a new EMI of about ₹{room:,.0f} a month"
                            + (f", which supports about ₹{loan:,.0f} over {tenure} months at {rate:g}%." if loan else "."))
    if sc["worst"]:
        observations.append(f"Weakest bureau band: {sc['worst']}.")
        if sc["worst"] == "Weak":
            flag("High", f"A bureau score is below {th['bureau_fair']:.0f}")
        elif sc["worst"] == "Fair":
            flag("Medium", f"A bureau score is between {th['bureau_fair']:.0f} and {th['bureau_good'] - 1:.0f}")
        elif sc["worst"] == "New to credit":
            flag("Low", "New to credit - no bureau score; lenders may ask for more security or a guarantor")
    observations.append(f"Repayment track record: {tr['rating']} ({'; '.join(tr['reasons'])}).")
    if tr["rating"] == "Adverse":
        flag("High", "Past default, settlement or write-off: " + "; ".join(tr["reasons"]))
    elif tr["rating"] == "Weak":
        flag("Medium", "Restructuring or serious delays in the past: " + "; ".join(tr["reasons"]))
    if nw["net_worth"] is not None:
        observations.append(f"Net worth ₹{nw['net_worth']:,.0f}"
                            + (f", {nw['documented_share']:.0%} of assets documented." if nw["documented_share"] is not None else "."))
        if nw["net_worth"] < 0:
            flag("High", "Negative net worth (liabilities exceed assets)")
    if inc["count"] and inc["stable_annual"] is not None and inc["total_annual"] and inc["stable_annual"] < 0.5 * inc["total_annual"]:
        flag("Low", "Less than half of the income is marked stable")
    if missing:
        observations.append("Not yet provided: " + ", ".join(missing) + ".")

    # verdict on what is available
    if monthly is None:
        verdict = "Not assessed"
    elif (f is not None and f > th["foir_weak"]) or sc["worst"] == "Weak" or tr["rating"] == "Adverse" \
            or (nw["net_worth"] is not None and nw["net_worth"] < 0):
        verdict = "Weak"
    elif (f is not None and f > th["foir_max"]) or sc["worst"] == "Fair" or tr["rating"] == "Weak" or not room:
        verdict = "Stretched"
    elif f is not None and f <= th["foir_strong"] and sc["worst"] == "Strong" and tr["rating"] == "Clean":
        verdict = "Strong"
    else:
        verdict = "Adequate"
    status, meaning = VERDICTS[verdict]
    return {"scores": sc, "income": inc, "net_worth": nw, "track": tr, "obligations": ob,
            "monthly_income": monthly, "foir": f, "surplus": room, "max_emi": room, "max_loan": loan,
            "rate_pct": rate, "tenure_months": tenure,
            "verdict": {"label": verdict, "status": status, "meaning": meaning},
            "observations": observations, "flags": flags, "missing": missing}


def with_proposal(pot: dict, new_monthly_emi: float, th: dict) -> dict:
    """FOIR and headroom once the proposed loans' instalments are added (used in step 6)."""
    monthly = pot.get("monthly_income")
    existing = pot["obligations"]["monthly"]
    f_after = foir(existing + (new_monthly_emi or 0), monthly)
    within = None if pot.get("max_emi") is None else (new_monthly_emi or 0) <= pot["max_emi"] + 1
    return {"new_emi": new_monthly_emi, "foir_after": f_after, "within_headroom": within,
            "status": None if f_after is None else "good" if f_after <= th["foir_max"] else
            "warning" if f_after <= th["foir_weak"] else "critical"}
