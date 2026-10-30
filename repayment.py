"""Repayment schedules for term facilities and DSCR on the proposed structure."""

import math

import pandas as pd

METHODS = {
    "EMI – equated instalments": "Same instalment every period; interest falls, principal rises",
    "Equated principal + interest": "Same principal every period plus interest on the balance (falling outgo)",
    "Step-up instalments": "Principal rises by a fixed % each year – suits growing cash flows",
    "Ballooning (back-ended)": "Principal rises steadily towards the end – suits projects that ramp up",
    "Bullet": "Interest every period, all principal at maturity",
    "Structured – % per year": "You set the % of principal repaid in each year",
}
FREQUENCIES = {"Monthly": 1, "Quarterly": 3, "Half-yearly": 6}
NON_AMORTISING = {
    "revolving": "Revolving – renewed every 12 months; interest serviced monthly; reduces with drawing power",
    "demand": "Demand loan – repaid at end of each tranche (≤ 12 months) from operating cash flows",
    "self_liquidating": "Self-liquidating – each bill / advance is repaid from the related sale or export proceeds",
    "contingent": "Contingent – no repayment unless invoked; devolved amounts are recovered at once (or converted to a term loan)",
}


def schedule(amount: float, rate_pct: float, tenor_months: int, method: str, freq: str = "Monthly",
             moratorium_months: int = 0, step_up_pct: float = 10.0, structured_pcts: list | None = None) -> pd.DataFrame:
    f = FREQUENCIES[freq]
    i = rate_pct / 100 * f / 12
    n_total = math.ceil(tenor_months / f)
    n_mor = min(math.ceil(moratorium_months / f), n_total - 1)
    n = n_total - n_mor

    # principal weights for the n repayment periods
    if method.startswith("Step-up"):
        w = [(1 + step_up_pct / 100) ** (((k + n_mor) * f) // 12) for k in range(n)]
    elif method.startswith("Ballooning"):
        w = [k + 1 for k in range(n)]
    elif method.startswith("Bullet"):
        w = [0] * (n - 1) + [1]
    elif method.startswith("Structured"):
        pcts = structured_pcts or []
        w = []
        for k in range(n):
            year = ((k + n_mor) * f) // 12
            per_year = max(1, 12 // f)
            w.append((pcts[year] if year < len(pcts) else 0) / per_year)
    else:
        w = [1] * n
    total_w = sum(w) or 1
    principal_plan = [amount * x / total_w for x in w]

    rows, bal = [], amount
    emi = amount * i / (1 - (1 + i) ** -n) if method.startswith("EMI") and i else (amount / n if method.startswith("EMI") else None)
    for k in range(n_total):
        interest = bal * i
        if k < n_mor:
            principal = 0.0
        elif emi is not None:
            principal = emi - interest if k < n_total - 1 else bal
        else:
            principal = principal_plan[k - n_mor] if k < n_total - 1 else bal
        principal = min(max(principal, 0), bal)
        rows.append({"period": k + 1, "month": (k + 1) * f, "opening": bal, "interest": interest,
                     "principal": principal, "instalment": interest + principal, "closing": bal - principal})
        bal -= principal
    df = pd.DataFrame(rows)
    df["year"] = (df["month"] - 1) // 12 + 1
    return df


def yearly(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby("year")[["interest", "principal", "instalment"]].sum()


def dscr_projection(schedules: list, cash_accrual: float, existing_interest: float, existing_principal: float,
                    new_wc_interest: float, growth_pct: float = 0.0, tax_pct: float = 25.0) -> pd.DataFrame:
    """Year-by-year DSCR = (cash accrual + interest added back) / (interest + principal).

    cash_accrual: latest PAT + depreciation. Existing debt service is assumed to continue at the
    latest year's level (conservative). New interest reduces PAT after tax; new term-loan interest
    is added back in the numerator as in the standard DSCR formula."""
    years = max([int(s["year"].max()) for s in schedules] + [1])
    y = [yearly(s).reindex(range(1, years + 1), fill_value=0) for s in schedules]
    tl_int = sum((t["interest"] for t in y), pd.Series(0.0, index=range(1, years + 1)))
    tl_prin = sum((t["principal"] for t in y), pd.Series(0.0, index=range(1, years + 1)))
    keep = 1 - tax_pct / 100
    rows = []
    for t in range(1, years + 1):
        ca = cash_accrual * (1 + growth_pct / 100) ** t - keep * (tl_int[t] + new_wc_interest)
        available = ca + existing_interest + tl_int[t]
        service = existing_interest + existing_principal + tl_int[t] + tl_prin[t]
        rows.append({"year": t, "cash_accrual": ca, "available_for_debt_service": available,
                     "new_interest": tl_int[t], "new_principal": tl_prin[t],
                     "existing_debt_service": existing_interest + existing_principal,
                     "total_debt_service": service, "dscr": available / service if service else None})
    return pd.DataFrame(rows)
