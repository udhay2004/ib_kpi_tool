"""Credit facility catalogue (fund-based and non-fund-based) and limit-assessment methods.

Assessment methods follow common Indian bank practice:
- Working capital: Nayak committee turnover method (limits up to Rs 5 Cr for MSEs) and
  Tandon committee 2nd method of lending (MPBF).
- Term loans: DSCR on the proposed repayment schedule + promoter margin on project cost.
- Property-backed: loan-to-value against the security offered.
- LC / BG: need-based on purchases, lead time and contract pipeline.
Every result is a guide for the analyst, not a sanction.
"""

# Field spec: (key, label, kind, default, help) - kind: money / pct / int / days / text / choice:<a|b>
F = {
    "amount": ("amount", "Amount requested (₹)", "money", 0.0, None),
    "tenor": ("tenor_months", "Tenor (months)", "int", 60, None),
    "rate": ("rate_pct", "Interest / commission rate (% p.a.)", "pct", 10.5, None),
    "purpose": ("purpose", "Purpose / end use", "text", "", None),
    "turnover": ("projected_turnover", "Projected annual turnover (₹)", "money", 0.0,
                 "Leave 0 to use latest revenue + growth from the financials"),
    "project_cost": ("project_cost", "Total project / asset cost (₹)", "money", 0.0, None),
    "moratorium": ("moratorium_months", "Moratorium (months)", "int", 0, "Interest-only period before principal starts"),
    "cash_margin": ("cash_margin_pct", "Cash margin (%)", "pct", 15.0, "Fixed deposit / cash held against the facility"),
    "annual_purchases": ("annual_purchases_lc", "Annual purchases through LC (₹)", "money", 0.0, None),
    "lead_days": ("lead_time_days", "Lead time: order to shipment (days)", "days", 30, None),
    "usance_days": ("usance_days", "Usance / credit period (days)", "days", 0, "0 for sight LC"),
    "credit_sales": ("annual_credit_sales", "Annual credit sales eligible for discounting (₹)", "money", 0.0, None),
    "collection_days": ("collection_days", "Average collection period (days)", "days", 60, None),
    "export_turnover": ("export_turnover", "Annual export turnover (₹)", "money", 0.0, None),
    "cycle_days": ("cycle_days", "Pre-shipment cycle (days)", "days", 90, None),
    "contract_value": ("contracts_requiring_bg", "Contracts / tenders needing guarantees in 12 months (₹)", "money", 0.0, None),
    "bg_pct": ("bg_pct_of_contract", "Guarantee as % of contract value", "pct", 10.0, None),
    "monthly_rent": ("monthly_rent", "Monthly lease rental (₹)", "money", 0.0, None),
    "lease_left": ("lease_remaining_months", "Lease period remaining (months)", "int", 108, None),
    "beneficiary": ("beneficiary", "Beneficiary", "text", "", None),
    "lc_kind": ("lc_kind", "LC type", "choice:Inland sight|Inland usance|Import sight|Import usance", "Inland sight", None),
    "bond_size": ("bond_size", "Bond issue size (₹)", "money", 0.0, None),
}

COMMON = ["amount", "rate", "purpose"]

# category -> group -> product -> spec
CATALOGUE = {
    "Fund based": {
        "Working capital": {
            "Cash Credit (CC)": {"fields": COMMON + ["turnover"], "method": "wc", "repay": "revolving",
                                 "security": "Hypothecation of stock and book debts", "tenor": "12 months, renewable"},
            "Overdraft (OD)": {"fields": COMMON + ["turnover"], "method": "wc", "repay": "revolving",
                               "security": "Current assets / property / FD", "tenor": "12 months, renewable"},
            "Working Capital Demand Loan (WCDL)": {"fields": COMMON + ["tenor", "turnover"], "method": "wc", "repay": "demand",
                                                   "security": "Pari passu with CC", "tenor": "7 days – 12 months"},
            "Bill / invoice discounting": {"fields": COMMON + ["credit_sales", "collection_days"], "method": "bills",
                                           "repay": "self_liquidating", "security": "Bills / receivables",
                                           "tenor": "Up to 180 days per bill"},
            "Supply chain / channel finance": {"fields": COMMON + ["credit_sales", "collection_days"], "method": "bills",
                                               "repay": "self_liquidating", "security": "Receivables, anchor tie-up",
                                               "tenor": "30–120 days per invoice"},
            "Packing credit / PCFC (pre-shipment)": {"fields": COMMON + ["export_turnover", "cycle_days"], "method": "export",
                                                    "repay": "self_liquidating", "security": "Export orders / LCs, stock",
                                                    "tenor": "Up to 360 days"},
            "Post-shipment credit (EBD / FBP)": {"fields": COMMON + ["export_turnover", "collection_days"], "method": "bills",
                                                 "repay": "self_liquidating", "security": "Export bills",
                                                 "tenor": "Up to usance + transit period"},
        },
        "Term loans": {
            "Term loan – capex / expansion": {"fields": COMMON + ["tenor", "moratorium", "project_cost"], "method": "term",
                                              "repay": "amortising", "security": "Assets financed + collateral",
                                              "tenor": "3–10 years"},
            "Machinery / equipment loan": {"fields": COMMON + ["tenor", "moratorium", "project_cost"], "method": "term",
                                           "repay": "amortising", "security": "Hypothecation of machinery",
                                           "tenor": "3–7 years"},
            "Vehicle / commercial vehicle loan": {"fields": COMMON + ["tenor", "project_cost"], "method": "term",
                                                  "repay": "amortising", "security": "Hypothecation of vehicle",
                                                  "tenor": "3–5 years"},
            "Project finance": {"fields": COMMON + ["tenor", "moratorium", "project_cost"], "method": "term",
                                "repay": "amortising", "security": "Project assets, escrow of cash flows",
                                "tenor": "7–15 years"},
            "Construction finance": {"fields": COMMON + ["tenor", "moratorium", "project_cost"], "method": "term",
                                     "repay": "amortising", "security": "Project land + receivables escrow",
                                     "tenor": "3–5 years"},
            "Loan against property (LAP)": {"fields": COMMON + ["tenor"], "method": "ltv", "repay": "amortising",
                                            "security": "Mortgage of property", "tenor": "Up to 15 years"},
            "Lease rental discounting (LRD)": {"fields": COMMON + ["tenor", "monthly_rent", "lease_left"], "method": "lrd",
                                               "repay": "amortising", "security": "Property + escrow of rentals",
                                               "tenor": "Up to remaining lease"},
            "Unsecured business loan": {"fields": COMMON + ["tenor"], "method": "term", "repay": "amortising",
                                        "security": "Personal guarantees", "tenor": "1–5 years"},
            "Bridge loan": {"fields": COMMON + ["tenor"], "method": "term", "repay": "bullet",
                            "security": "Assignment of expected inflow", "tenor": "Up to 12–24 months"},
            "Acquisition finance": {"fields": COMMON + ["tenor", "moratorium", "project_cost"], "method": "term",
                                    "repay": "amortising", "security": "Pledge of acquired shares + assets",
                                    "tenor": "5–7 years"},
            "Loan against FD / securities": {"fields": COMMON + ["tenor"], "method": "ltv", "repay": "bullet",
                                             "security": "Lien on FD / pledge of securities", "tenor": "Up to FD maturity"},
        },
    },
    "Non-fund based": {
        "Bank guarantees": {
            "Financial guarantee": {"fields": COMMON + ["tenor", "beneficiary", "contract_value", "bg_pct", "cash_margin"],
                                    "method": "bg", "repay": "contingent", "security": "Cash margin + counter-indemnity",
                                    "tenor": "As per underlying obligation"},
            "Performance guarantee": {"fields": COMMON + ["tenor", "beneficiary", "contract_value", "bg_pct", "cash_margin"],
                                      "method": "bg", "repay": "contingent", "security": "Cash margin + counter-indemnity",
                                      "tenor": "Contract period + claim period"},
            "Bid bond / EMD guarantee": {"fields": COMMON + ["tenor", "beneficiary", "contract_value", "bg_pct", "cash_margin"],
                                         "method": "bg", "repay": "contingent", "security": "Cash margin",
                                         "tenor": "Tender validity"},
            "Advance payment guarantee": {"fields": COMMON + ["tenor", "beneficiary", "contract_value", "bg_pct", "cash_margin"],
                                          "method": "bg", "repay": "contingent", "security": "Cash margin",
                                          "tenor": "Until advance is adjusted"},
            "Retention money guarantee": {"fields": COMMON + ["tenor", "beneficiary", "contract_value", "bg_pct", "cash_margin"],
                                          "method": "bg", "repay": "contingent", "security": "Cash margin",
                                          "tenor": "Defect liability period"},
            "Customs / statutory guarantee": {"fields": COMMON + ["tenor", "beneficiary", "cash_margin"], "method": "bg_simple",
                                              "repay": "contingent", "security": "Cash margin", "tenor": "As required"},
            "Deferred payment guarantee (DPG)": {"fields": COMMON + ["tenor", "beneficiary", "project_cost", "cash_margin"],
                                                 "method": "term", "repay": "amortising", "security": "Asset purchased",
                                                 "tenor": "Instalment period"},
            "Standby letter of credit (SBLC)": {"fields": COMMON + ["tenor", "beneficiary", "cash_margin"], "method": "bg_simple",
                                                "repay": "contingent", "security": "Cash margin + collateral",
                                                "tenor": "Up to 12 months typically"},
        },
        "Letters of credit": {
            "Letter of credit": {"fields": COMMON + ["lc_kind", "annual_purchases", "lead_days", "usance_days", "cash_margin"],
                                 "method": "lc", "repay": "contingent", "security": "Goods (documents of title) + margin",
                                 "tenor": "Sight or usance up to 180 days"},
            "Co-acceptance of bills": {"fields": COMMON + ["annual_purchases", "usance_days", "cash_margin"], "method": "lc",
                                       "repay": "contingent", "security": "Goods + margin", "tenor": "Bill usance"},
        },
        "Other non-fund": {
            "Partial credit enhancement (PCE)": {"fields": COMMON + ["tenor", "bond_size"], "method": "pce",
                                                 "repay": "contingent", "security": "As agreed",
                                                 "tenor": "Bond tenor"},
            "Derivative / forward contract limit (CEL)": {"fields": COMMON + ["tenor"], "method": "none",
                                                          "repay": "contingent", "security": "Margin as per exposure",
                                                          "tenor": "Contract tenor"},
        },
    },
}


def product(name: str) -> dict:
    for cat, groups in CATALOGUE.items():
        for grp, prods in groups.items():
            if name in prods:
                return {**prods[name], "category": cat, "group": grp, "name": name}
    raise KeyError(name)


def all_products():
    return [(cat, grp, p) for cat, g in CATALOGUE.items() for grp, ps in g.items() for p in ps]


def is_fund(fac: dict) -> bool:
    return product(fac["product"])["category"] == "Fund based"


def exposure(fac: dict) -> float:
    """Exposure net of cash margin (what security has to cover)."""
    amt = fac.get("amount") or 0
    return amt * (1 - (fac.get("cash_margin_pct") or 0) / 100) if not is_fund(fac) else amt


# ---------------------------------------------------------------- assessment

def assess(fac: dict, fin: dict, securities_value: float = 0.0) -> dict:
    """fin: latest-year figures {revenue, growth, total_current_assets, total_current_liabilities,
    short_term_borrowings}. Returns {eligible, method, workings, ok}."""
    p = product(fac["product"])
    m, amt = p["method"], fac.get("amount") or 0
    out = {"method": "", "eligible": None, "workings": []}
    w = out["workings"]

    if m == "wc":
        turnover = fac.get("projected_turnover") or (
            (fin.get("revenue") or 0) * (1 + max(fin.get("growth") or 0, 0)) if fin.get("revenue") else 0)
        if turnover:
            nayak = 0.20 * turnover
            w.append(f"Turnover method (Nayak): 20% of projected turnover ₹{turnover:,.0f} = ₹{nayak:,.0f}; "
                     f"promoter margin 5% = ₹{0.05 * turnover:,.0f}")
            out.update(method="Turnover method", eligible=nayak)
        ca, cl, stb = fin.get("total_current_assets"), fin.get("total_current_liabilities"), fin.get("short_term_borrowings") or 0
        if ca and cl:
            ocl = cl - stb
            mpbf = 0.75 * ca - ocl
            w.append(f"MPBF (Tandon II): 75% × current assets ₹{ca:,.0f} − other current liabilities ₹{ocl:,.0f} = ₹{mpbf:,.0f}")
            if out["eligible"] is None or amt > 5e7:  # above Rs 5 Cr banks use MPBF / cash budget
                out.update(method="MPBF (2nd method)", eligible=max(mpbf, 0))
        if out["eligible"] is None:
            w.append("Needs projected turnover or the balance sheet (current assets / liabilities)")
    elif m == "bills":
        sales, days = fac.get("annual_credit_sales") or 0, fac.get("collection_days") or 0
        if sales:
            e = sales / 365 * days * 0.80
            w.append(f"Credit sales ₹{sales:,.0f} ÷ 365 × {days} days × 80% (20% margin) = ₹{e:,.0f}")
            out.update(method="Receivables cycle", eligible=e)
    elif m == "export":
        exp, days = fac.get("export_turnover") or 0, fac.get("cycle_days") or 0
        if exp:
            e = exp / 365 * days * 0.90
            w.append(f"Export turnover ₹{exp:,.0f} ÷ 365 × {days} days × 90% (10% margin) = ₹{e:,.0f}")
            out.update(method="Pre-shipment cycle", eligible=e)
    elif m == "lc":
        purch = fac.get("annual_purchases_lc") or 0
        days = (fac.get("lead_time_days") or 0) + (fac.get("usance_days") or 0)
        if purch:
            e = purch / 365 * max(days, 30)
            w.append(f"LC purchases ₹{purch:,.0f} ÷ 365 × (lead time + usance = {days} days, min 30) = ₹{e:,.0f}")
            out.update(method="Purchase cycle", eligible=e)
    elif m == "bg":
        c, pct = fac.get("contracts_requiring_bg") or 0, fac.get("bg_pct_of_contract") or 0
        if c:
            e = c * pct / 100
            w.append(f"Contracts ₹{c:,.0f} × {pct:.0f}% guarantee = ₹{e:,.0f}")
            out.update(method="Contract pipeline", eligible=e)
    elif m == "term":
        cost = fac.get("project_cost") or 0
        if cost:
            margin = (cost - amt) / cost
            e = cost * 0.75
            w.append(f"Project cost ₹{cost:,.0f}; promoter margin {margin:.0%} (norm ≥ 25%) → max loan ₹{e:,.0f}")
            out.update(method="Project cost less 25% margin", eligible=e)
        w.append("Debt-service capacity is tested on the repayment schedule (step 5)")
    elif m == "ltv":
        if securities_value:
            e = securities_value * 0.60
            w.append(f"60% LTV on security value ₹{securities_value:,.0f} = ₹{e:,.0f}")
            out.update(method="Loan-to-value", eligible=e)
        else:
            w.append("Add the property / FD in step 2 to test loan-to-value")
    elif m == "lrd":
        rent, n, r = fac.get("monthly_rent") or 0, fac.get("lease_remaining_months") or 0, (fac.get("rate_pct") or 0) / 1200
        if rent and n:
            net = rent * 0.85  # after property tax / maintenance
            pv = net * ((1 - (1 + r) ** -n) / r if r else n)
            e = 0.90 * pv
            w.append(f"90% of present value of net rentals (₹{net:,.0f}/month × {n} months at {fac.get('rate_pct')}%) = ₹{e:,.0f}")
            out.update(method="Discounted rentals", eligible=e)
    elif m == "pce":
        b = fac.get("bond_size") or 0
        if b:
            out.update(method="≤ 50% of bond size (RBI NFB Directions 2025)", eligible=0.5 * b)
            w.append(f"PCE capped at 50% of bond size ₹{b:,.0f}")
    elif m == "bg_simple":
        w.append("Assessed on the specific requirement (no formula)")

    e = out["eligible"]
    out["ok"] = None if e is None else amt <= e * 1.0001
    return out
