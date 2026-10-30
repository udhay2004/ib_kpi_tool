"""Repayment capacity: how the borrower will actually repay, worked out from the financials,
bank statements, GST and the proposed structure.

Plain formulas, no model calls. Produces numbers, a stress test and written observations for the credit note.
"""

import pandas as pd

import repayment as rp
from kpis import THRESHOLDS, _inr

STATUS_RANK = {"good": 0, "warning": 1, "critical": 2}
VERDICTS = [  # (label, status, meaning)
    ("Comfortable", "good", "Repays from operating cash with room to absorb a downturn."),
    ("Adequate", "good", "Repays from operating cash at plan; limited cushion if earnings fall."),
    ("Stretched", "warning", "Cash only just covers debt service; a modest fall in earnings would cause shortfalls."),
    ("Insufficient", "critical", "Operating cash does not cover debt service on the proposed structure."),
]


def _kpi(fk: pd.DataFrame, key: str, fy: str):
    if fk is None or fk.empty or key not in fk.index or fy not in fk.columns:
        return None
    v = fk.at[key, fy]
    return float(v) if pd.notna(v) else None


def _fin(fin: pd.DataFrame, key: str, fy: str):
    if fin is None or fin.empty or key not in fin.columns or fy not in fin.index:
        return None
    v = fin.at[fy, key]
    return float(v) if pd.notna(v) else None


def _bank_total(bk: pd.DataFrame, col: str):
    if bk is None or bk.empty or col not in bk or not bk[col].notna().any():
        return None
    return float(bk[col].sum())


def min_dscr(schedules, cash_accrual, existing_interest, existing_principal, wc_interest, growth_pct, tax_pct):
    t = rp.dscr_projection(schedules, cash_accrual, existing_interest, existing_principal, wc_interest, growth_pct,
                           tax_pct)
    d = t["dscr"].dropna()
    return (float(d.min()), float(d.mean()), t) if not d.empty else (None, None, t)


def break_even_fall(target, schedules, cash_accrual, ei, ep, wc, growth, tax) -> float | None:
    """Largest % fall in cash accrual (all years) that keeps the minimum DSCR at or above target."""
    base = min_dscr(schedules, cash_accrual, ei, ep, wc, growth, tax)[0]
    if base is None or cash_accrual <= 0:
        return None
    if base < target:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        v = min_dscr(schedules, cash_accrual * (1 - mid), ei, ep, wc, growth, tax)[0]
        lo, hi = (mid, hi) if v is not None and v >= target else (lo, mid)
    return lo


def assess(res: dict | None, facilities: list, build_schedules, dscr_cfg: dict, cover: dict | None = None,
           wc_interest_fn=None) -> dict | None:
    """res: output of kpis.analyse (or None). build_schedules(rate_add_pct) -> list of new term-loan schedules.
    wc_interest_fn(rate_add_pct) -> annual interest on new CC/OD at the assumed utilisation.
    Returns None without financials."""
    if not res or res["fin_kpis"].empty:
        return None
    fk, fin, bk = res["fin_kpis"], res["tables"]["financials"], res["bank_kpis"]
    years = [c for c in fk.columns if str(c).startswith("FY")]
    fy = years[-1]
    prev = years[-2] if len(years) > 1 else None
    pat, dep = _fin(fin, "profit_after_tax", fy), _fin(fin, "depreciation", fy)
    if pat is None:
        return None
    accrual = pat + (dep or 0)
    accrual_prev = (_fin(fin, "profit_after_tax", prev) or 0) + (_fin(fin, "depreciation", prev) or 0) if prev else None
    ei, ep = _fin(fin, "finance_cost", fy) or 0, _fin(fin, "current_maturities_of_long_term_debt", fy) or 0
    growth, tax = dscr_cfg["growth_pct"], dscr_cfg["tax_pct"]
    wc = (wc_interest_fn or (lambda r: 0.0))
    sch = build_schedules(0)
    revenue, ebitda = _kpi(fk, "revenue", fy), _kpi(fk, "ebitda", fy)
    th = THRESHOLDS

    # ---------------------------------------------------------------- base and stress
    cogs = _fin(fin, "cost_of_materials_and_purchases", fy)
    contribution = 1 - cogs / revenue if cogs and revenue else min(1.0, 2 * (ebitda / revenue)) if ebitda and revenue else 0.3
    keep = 1 - tax / 100

    def scenario(rev_fall=0.0, rate_add=0.0, growth_=None):
        ca = accrual - (revenue or 0) * rev_fall * contribution * keep
        return min_dscr(build_schedules(rate_add), ca, ei * (1 + rate_add / 10), ep, wc(rate_add),
                        growth if growth_ is None else growth_, tax)

    base_min, base_avg, base_t = scenario()
    if base_min is None:  # no term debt at all: only existing debt service
        base_min, base_avg, base_t = min_dscr([], accrual, ei, ep, wc(0), growth, tax)
    stress_rows = []
    for name, kw in [("Base case", {}), ("No growth", {"growth_": 0.0}), ("Revenue −10%", {"rev_fall": 0.10}),
                     ("Revenue −20%", {"rev_fall": 0.20}), ("Interest +2%", {"rate_add": 2.0}),
                     ("Revenue −10% and interest +2%", {"rev_fall": 0.10, "rate_add": 2.0})]:
        mn, avg, _ = scenario(**kw)
        stress_rows.append({"Scenario": name, "Minimum DSCR": mn, "Average DSCR": avg,
                            "Status": None if mn is None else "good" if mn >= th["dscr_min"] else
                            "warning" if mn >= 1.0 else "critical"})
    stress = pd.DataFrame(stress_rows)
    be_125 = break_even_fall(th["dscr_min"], sch, accrual, ei, ep, wc(0), growth, tax)
    be_100 = break_even_fall(1.0, sch, accrual, ei, ep, wc(0), growth, tax)

    # ---------------------------------------------------------------- debt and banking
    import loans
    new_term = sum(f.get("amount") or 0 for f in facilities if loans.product(f["product"])["repay"] in ("amortising", "bullet"))
    new_wc = sum(f.get("amount") or 0 for f in facilities if loans.product(f["product"])["repay"] in ("revolving", "demand"))
    debt_now = _kpi(fk, "total_debt", fy) or 0
    debt_post = debt_now + new_term + new_wc
    lev_post = debt_post / ebitda if ebitda else None
    payback = (debt_now + new_term) / accrual if accrual > 0 else None
    y1 = base_t.iloc[0] if not base_t.empty else None
    service_y1 = float(y1["total_debt_service"]) if y1 is not None else ei + ep
    peak = float(base_t["total_debt_service"].max()) if not base_t.empty else service_y1
    tight = base_t.loc[base_t["dscr"].idxmin()] if not base_t.empty and base_t["dscr"].notna().any() else None
    headroom = float(tight["available_for_debt_service"] - tight["total_debt_service"]) if tight is not None else None
    biz_credits = _bank_total(bk, "annualised_business_credits")
    months = _bank_total(bk, "months_covered")
    emi_now = _bank_total(bk, "monthly_emi_outflow")
    abb = _bank_total(bk, "abb")
    bounces = int(_bank_total(bk, "outward_bounces") or 0) if bk is not None and not bk.empty else None
    new_monthly = (float(y1["new_interest"] + y1["new_principal"]) / 12) if y1 is not None else 0
    burden = (12 * ((emi_now or 0) + new_monthly) / biz_credits) if biz_credits else None
    abb_cover = abb / ((emi_now or 0) + new_monthly) if abb and ((emi_now or 0) + new_monthly) else None
    credits_vs_rev = biz_credits / revenue if biz_credits and revenue else None
    ccc, cr = _kpi(fk, "cash_conversion_cycle", fy), _kpi(fk, "current_ratio", fy)
    cfo_e = _kpi(fk, "cfo_to_ebitda", fy)

    def st_(v, good, warn, higher=True):
        if v is None:
            return None
        if higher:
            return "good" if v >= good else "warning" if v >= warn else "critical"
        return "good" if v <= good else "warning" if v <= warn else "critical"

    metrics = [
        ("Cash accrual (PAT + depreciation)", _inr(accrual), f"{fy}" + (f"; {prev}: {_inr(accrual_prev)}" if prev else ""),
         None if accrual_prev is None else "good" if accrual >= accrual_prev else "warning"),
        ("Debt service, year 1 (existing + new)", _inr(service_y1), f"Peak year {_inr(peak)}", None),
        ("Minimum DSCR on proposal", f"{base_min:.2f}x" if base_min else "—", f"≥ {th['dscr_min']:.2f}x",
         st_(base_min, th["dscr_min"], 1.0)),
        ("Average DSCR on proposal", f"{base_avg:.2f}x" if base_avg else "—", f"≥ {th['dscr_avg_min']:.2f}x",
         st_(base_avg, th["dscr_avg_min"], th["dscr_min"])),
        ("Cash left after debt service, tightest year", _inr(headroom), "", st_(headroom, 0.000001, 0)),
        ("Earnings can fall before DSCR < 1.25x", "—" if be_125 is None else f"{be_125:.0%}", "≥ 20% comfortable",
         st_(be_125, 0.20, 0.05)),
        ("Earnings can fall before DSCR < 1.0x", "—" if be_100 is None else f"{be_100:.0%}", "", st_(be_100, 0.30, 0.10)),
        ("Debt / EBITDA after proposal", "—" if lev_post is None else f"{lev_post:.1f}x", "≤ 3.5x",
         st_(lev_post, 3.5, 5.0, higher=False)),
        ("Years of cash accrual to repay term debt", "—" if payback is None else f"{payback:.1f} yrs", "≤ 5 yrs",
         st_(payback, 5, 7, higher=False)),
        ("Bank credits vs revenue", "—" if credits_vs_rev is None else f"{credits_vs_rev:.0%}", "≥ 80% routed",
         st_(credits_vs_rev, 0.8, 0.6)),
        ("EMIs as % of business credits", "—" if burden is None else f"{burden:.0%}", "≤ 30%",
         st_(burden, 0.30, 0.45, higher=False)),
        ("Average bank balance ÷ monthly EMIs", "—" if abb_cover is None else f"{abb_cover:.1f}x", "≥ 1.5x",
         st_(abb_cover, 1.5, 1.0)),
        ("Cash conversion cycle", "—" if ccc is None else f"{ccc:.0f} days", "WC limits revolve with this cycle", None),
    ]
    metrics_df = pd.DataFrame(metrics, columns=["Measure", "Value", "Benchmark / note", "Status"])

    # ---------------------------------------------------------------- verdict
    stress10 = stress.loc[stress["Scenario"] == "Revenue −10%", "Minimum DSCR"].iloc[0]
    if base_min is None or base_min < 1.0:
        verdict = VERDICTS[3]
    elif base_min < th["dscr_min"]:
        verdict = VERDICTS[2]
    elif base_min >= th["dscr_avg_min"] and stress10 is not None and stress10 >= th["dscr_min"] \
            and (lev_post is None or lev_post <= 4):
        verdict = VERDICTS[0]
    else:
        verdict = VERDICTS[1]

    # ---------------------------------------------------------------- written observations
    notes = []

    def note(title, status, text):
        notes.append({"title": title, "status": status, "text": text})

    trend = ""
    if accrual_prev:
        ch = accrual / accrual_prev - 1
        trend = f", {'up' if ch >= 0 else 'down'} {abs(ch):.0%} on {prev}"
    src = [f"Term debt is repaid from cash accruals of {_inr(accrual)} a year in {fy} (PAT {_inr(pat)} + "
           f"depreciation {_inr(dep)}){trend}."] if new_term or ep else []
    if new_wc:
        src.append(f"The working-capital limits ({_inr(new_wc)}) are not repaid in instalments: they revolve as "
                   f"stock is sold and debtors pay"
                   + (f", on a cash conversion cycle of about {ccc:.0f} days" if ccc is not None else "")
                   + (f", with a current ratio of {cr:.2f}x" if cr is not None else "") + ".")
    if cfo_e is not None:
        src.append(f"Cash from operations was {cfo_e:.0%} of EBITDA, "
                   + ("so profits are turning into cash." if cfo_e >= 0.6 else
                      "so a large part of profit is tied up in working capital rather than available as cash."))
    note("How they will repay", "good" if accrual > 0 else "critical",
         " ".join(src) or "No term repayment in the proposal.")

    if base_min is not None:
        txt = (f"With the new facilities on top of existing debt, the minimum DSCR is {base_min:.2f}x and the average "
               f"{base_avg:.2f}x (benchmarks {th['dscr_min']:.2f}x and {th['dscr_avg_min']:.2f}x). ")
        if tight is not None:
            txt += (f"The tightest year is year {int(tight['year'])}, when debt service is "
                    f"{_inr(tight['total_debt_service'])} and {_inr(headroom)} is left over. ")
        if payback is not None:
            txt += f"Existing and new term debt equals {payback:.1f} years of current cash accrual."
        note("Capacity to service the debt", st_(base_min, th["dscr_min"], 1.0), txt)

    if be_125 is not None:
        worst = stress.dropna(subset=["Minimum DSCR"]).sort_values("Minimum DSCR").iloc[0]
        txt = (f"Cash accrual can fall {be_125:.0%} before the DSCR drops below {th['dscr_min']:.2f}x, and "
               f"{be_100:.0%} before it cannot cover debt service at all. The hardest scenario tested "
               f"({worst['Scenario'].lower()}) gives a minimum DSCR of {worst['Minimum DSCR']:.2f}x.")
        note("How much stress it can take", st_(be_125, 0.20, 0.05), txt)

    ev = []
    if credits_vs_rev is not None:
        ev.append(f"Business credits in the bank statements are {_inr(biz_credits)} a year, {credits_vs_rev:.0%} of "
                  "book revenue" + (" — sales are routed through the accounts seen." if credits_vs_rev >= 0.8 else
                                    " — part of the turnover is banked elsewhere or not reflected; ask for all accounts."))
    if burden is not None:
        ev.append(f"Existing EMIs plus the new instalments would take {burden:.0%} of monthly business credits.")
    if abb_cover is not None:
        ev.append(f"The average balance covers monthly EMIs {abb_cover:.1f} times.")
    if bounces is not None:
        ev.append("No outward cheque / EMI bounces." if bounces == 0 else
                  f"{bounces} outward bounce(s) in {int(months or 0)} months, within the usual tolerance."
                  if bounces < THRESHOLDS["bounces_refer"]
                  else f"{bounces} outward bounces in {int(months or 0)} months — repayment discipline is a concern.")
    gst = res["flags"][res["flags"]["area"] == "GST vs books"] if not res["flags"].empty else pd.DataFrame()
    if not gst.empty:
        ev.append("GST turnover does not match the books: " + gst.iloc[0]["finding"] + ".")
    if ev:
        worst_ev = max([s for s in (st_(credits_vs_rev, 0.8, 0.6), st_(burden, 0.30, 0.45, higher=False),
                                    "critical" if bounces and bounces >= THRESHOLDS["bounces_high"] else
                                    "warning" if bounces and bounces >= THRESHOLDS["bounces_refer"] else "good",
                                    "warning" if not gst.empty else "good") if s], key=STATUS_RANK.get)
        note("What the bank statements and GST show", worst_ev, " ".join(ev))

    if cover and cover.get("cover") is not None:
        g = cover.get("guarantees") or []
        txt = (f"If operating cash falls short, the fall-back is security with a realisable value of "
               f"{_inr(cover['realisable_value'])}, covering exposure {cover['cover']:.2f} times")
        txt += (", plus " + ", ".join(sorted({x['kind'].lower() for x in g})) + ".") if g else "."
        note("Fall-back if cash falls short", "good" if cover["cover"] >= 1.25 else "warning", txt)

    watch = []
    if lev_post is not None and lev_post > 3.5:
        watch.append(f"debt rises to {lev_post:.1f}x EBITDA after the proposal")
    if accrual_prev and accrual < accrual_prev:
        watch.append("cash accrual fell in the latest year")
    if growth > 10:
        watch.append(f"the projection assumes {growth:.0f}% a year growth in cash accrual — check it against the order book")
    moratorium_end = [f for f in facilities if (f.get("moratorium_months") or 0) > 0]
    if moratorium_end:
        watch.append("repayment starts only after the moratorium, so year-1 cash flow looks better than steady state")
    if watch:
        note("Points to watch", "warning", "; ".join(w[0].upper() + w[1:] for w in watch) + ".")

    return {"fy": fy, "verdict": {"label": verdict[0], "status": verdict[1], "meaning": verdict[2]},
            "metrics": metrics_df, "stress": stress, "notes": notes, "dscr": base_t,
            "break_even_125": be_125, "break_even_100": be_100, "min_dscr": base_min}


def report_rows(cap: dict) -> pd.DataFrame:
    """Flat table for the credit note."""
    rows = [{"Section": "Conclusion", "Item": cap["verdict"]["label"], "Detail": cap["verdict"]["meaning"]}]
    rows += [{"Section": "Observation", "Item": n["title"], "Detail": n["text"]} for n in cap["notes"]]
    rows += [{"Section": "Measure", "Item": r["Measure"], "Detail": f"{r['Value']}  ({r['Benchmark / note']})"}
             for _, r in cap["metrics"].iterrows()]
    rows += [{"Section": "Stress test", "Item": r["Scenario"],
              "Detail": "—" if r["Minimum DSCR"] is None or pd.isna(r["Minimum DSCR"])
              else f"min DSCR {r['Minimum DSCR']:.2f}x, avg {r['Average DSCR']:.2f}x"} for _, r in cap["stress"].iterrows()]
    return pd.DataFrame(rows)
