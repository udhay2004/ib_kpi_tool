"""Deterministic KPI engine and cross-document checks.

Inputs are the extracted dicts from extractor.py (after human review/edits).
Every KPI records its formula so it can be traced back; missing inputs yield
None ("NOT FOUND") rather than a guess. Flags follow the pack's Risk Flag
Register idea: evidence-linked, severity-scored, never a decision.

Benchmarks are common Indian bank / NBFC appraisal norms (PSB credit policies,
Tandon/Nayak working-capital norms, bank-statement-analysis practice). They
vary by lender - edit THRESHOLDS to match the policy you work to.
"""

from dataclasses import dataclass, asdict
from datetime import date

import pandas as pd

THRESHOLDS = {
    "current_ratio_benchmark": 1.33,   # Tandon 2nd method; ordinarily not below 1.17
    "current_ratio_floor": 1.17,
    "dscr_min": 1.25,                  # any single year
    "dscr_avg_min": 1.50,              # average over the years
    "interest_coverage_min": 2.0,
    "debt_equity_max": 3.0,            # term-loan D/E norm (2:1 for many lenders)
    "tol_tnw_max": 4.0,                # PSB norm; ordinarily not above 5
    "debt_ebitda_max": 3.5,
    "receivable_days_max": 120,
    "gst_vs_books_tolerance": 0.10,
    "gstr1_vs_3b_tolerance": 0.05,
    "itc_3b_vs_2b_tolerance": 0.20,    # notices are reported from ~20% excess ITC
    "bank_credit_to_revenue_min": 0.70,
    "bank_credit_to_revenue_max": 1.50,
    "cash_deposit_share_refer": 0.20,
    "cash_deposit_share_high": 0.40,
    "related_transfer_share_max": 0.30,
    "bounces_refer": 3,                # 0-2 acceptable, 3-5 refer
    "bounces_high": 6,                 # 6+ in 12 months is serious
    "top5_customer_share_max": 0.60,
    "balance_to_inflow_min": 0.10,     # ABB below 10% of monthly inflows = thin cushion
}


@dataclass
class Flag:
    severity: str          # High / Medium / Low
    area: str
    finding: str
    evidence: str
    next_step: str


def _ok(v):
    return v is not None and not pd.isna(v)


def _inr(v) -> str:
    """Indian-style short amount: ₹34.84 Cr / ₹12.50 L / ₹9,500."""
    if not _ok(v):
        return "—"
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def _div(a, b):
    if not _ok(a) or not _ok(b) or b == 0:
        return None
    return a / b


def _sum(*vals):
    vals = [v for v in vals if _ok(v)]
    return sum(vals) if vals else None


def _v(row, key):
    val = row.get(key)
    return float(val) if _ok(val) else None


def _pages(item):
    pages = item.get("source_pages") or []
    return ", ".join(str(p) for p in pages) if isinstance(pages, list) else str(pages)


def _fy_of(period: str):
    """'2024-05' -> 'FY2024-25'."""
    try:
        y, m = int(period[:4]), int(period[5:7])
    except (ValueError, TypeError):
        return None
    start = y if m >= 4 else y - 1
    return f"FY{start}-{str(start + 1)[-2:]}"


# ---------------------------------------------------------------- normalise

def build_tables(docs: list) -> dict:
    """docs: list of dicts {doc_id, filename, doc_type, extracted}. Returns DataFrames + conflicts."""
    fin_rows, bank_rows, gst_rows, itr_rows, other_rows = [], [], [], [], []
    counterparties, customers, conflicts = [], [], []
    for d in docs:
        x = d.get("extracted") or {}
        src = d["doc_id"]
        if d["doc_type"] == "financials":
            for y in x.get("years", []):
                fin_rows.append({**y, "doc_id": src, "source_pages": _pages(y)})
        elif d["doc_type"] == "bank_statement":
            acct = f"{x.get('bank_name') or 'Bank'} ..{x.get('account_number_last4') or '?'}"
            for m in x.get("months", []):
                samples = m.get("balances_on_5_10_15_20_25") or []
                row = {k: v for k, v in m.items() if k != "balances_on_5_10_15_20_25"}
                row["abb_month"] = sum(samples) / len(samples) if samples else None
                bank_rows.append({**row, "account": acct, "od_cc_limit": x.get("sanctioned_od_cc_limit"),
                                  "doc_id": src, "source_pages": _pages(m)})
            for side in ("credit", "debit"):
                for c in x.get(f"top_{side}_counterparties", []) or []:
                    counterparties.append({**c, "side": side, "account": acct, "doc_id": src})
        elif d["doc_type"] == "gst":
            for p in x.get("periods", []):
                gst_rows.append({**p, "gstin": x.get("gstin"), "doc_id": src, "source_pages": _pages(p)})
            for c in x.get("top_customers", []) or []:
                customers.append({**c, "doc_id": src})
        elif d["doc_type"] == "itr":
            itr_rows.append({k: v for k, v in x.items()
                             if k not in ("notes", "employer_or_business_names", "detected_type")}
                            | {"doc_id": src, "source_pages": _pages(x)})
        else:
            for f in x.get("key_figures", []):
                other_rows.append({**f, "doc_id": src})

    fin = pd.DataFrame(fin_rows)
    if not fin.empty:
        # Same FY in two files: prefer audited, record a conflict if key numbers differ.
        fin["_aud"] = fin["audited"].fillna(False).astype(bool)
        fin = fin.sort_values(["fiscal_year", "_aud"], ascending=[True, False])
        for fy, grp in fin.groupby("fiscal_year"):
            if len(grp) > 1:
                a, b = grp.iloc[0], grp.iloc[1]
                for col in ("revenue_from_operations", "profit_after_tax", "net_worth"):
                    if _ok(a[col]) and _ok(b[col]) and abs(a[col] - b[col]) > 0.01 * max(abs(a[col]), 1):
                        conflicts.append(f"{fy} {col}: {a['doc_id']}={_inr(a[col])} vs {b['doc_id']}={_inr(b[col])}")
        fin = fin.drop_duplicates("fiscal_year").drop(columns="_aud").set_index("fiscal_year").sort_index()

    bank = pd.DataFrame(bank_rows)
    if not bank.empty:  # overlapping statements of the same account: keep one row per month
        bank = bank.drop_duplicates(["account", "month"]).sort_values(["account", "month"])
    gst = pd.DataFrame(gst_rows)
    if not gst.empty:
        gst = gst.drop_duplicates(["period", "return_type"]).sort_values("period")
    itr = pd.DataFrame(itr_rows)
    if not itr.empty:
        itr = itr.drop_duplicates(["assessee_name", "assessment_year"])

    return {"financials": fin, "bank": bank, "gst": gst, "itr": itr, "other": pd.DataFrame(other_rows),
            "counterparties": pd.DataFrame(counterparties), "customers": pd.DataFrame(customers),
            "conflicts": conflicts}


# ---------------------------------------------------------------- KPIs

FIN_KPIS = [
    # key, label, unit, formula text
    ("revenue", "Revenue from operations", "INR", "Reported"),
    ("revenue_growth", "Revenue growth", "%", "Revenue / prior-year revenue - 1"),
    ("ebitda", "EBITDA", "INR", "PBT + finance cost + depreciation - other income"),
    ("ebitda_margin", "EBITDA margin", "%", "EBITDA / revenue"),
    ("pat", "Profit after tax", "INR", "Reported"),
    ("pat_margin", "PAT margin", "%", "PAT / revenue"),
    ("tnw", "Tangible net worth", "INR", "Net worth - intangible assets"),
    ("total_debt", "Total debt", "INR", "LT borrowings + ST borrowings + current maturities"),
    ("current_ratio", "Current ratio", "x", "Current assets / current liabilities"),
    ("quick_ratio", "Quick ratio", "x", "(Current assets - inventory) / current liabilities"),
    ("debt_equity", "Debt / equity", "x", "Total debt / TNW"),
    ("tol_tnw", "TOL / TNW", "x", "(Total assets - TNW) / TNW"),
    ("adj_tol_tnw", "TOL / adjusted TNW", "x", "Promoter unsecured loans treated as quasi-equity"),
    ("debt_ebitda", "Debt / EBITDA", "x", "Total debt / EBITDA"),
    ("interest_coverage", "Interest coverage", "x", "EBITDA / finance cost"),
    ("dscr", "DSCR", "x", "(PAT + depreciation + finance cost) / (finance cost + current maturities of LTD)"),
    ("roe", "Return on equity", "%", "PAT / TNW"),
    ("roce", "ROCE", "%", "(PBT + finance cost) / (TNW + total debt)"),
    ("asset_turnover", "Asset turnover", "x", "Revenue / total assets"),
    ("receivable_days", "Receivable days", "days", "Trade receivables / revenue x 365"),
    ("inventory_days", "Inventory days", "days", "Inventory / cost of materials x 365"),
    ("payable_days", "Payable days", "days", "Trade payables / cost of materials x 365"),
    ("cash_conversion_cycle", "Cash conversion cycle", "days", "Receivable + inventory - payable days"),
    ("cfo_to_ebitda", "CFO / EBITDA", "%", "Cash from operations / EBITDA"),
    ("free_cash_flow", "Free cash flow", "INR", "Cash from operations - capex"),
]


def financial_kpis(fin: pd.DataFrame) -> pd.DataFrame:
    """Rows = KPI, columns = fiscal years."""
    out, prev_rev = {}, None
    for fy, r in fin.iterrows():
        r = r.to_dict()
        rev = _v(r, "revenue_from_operations")
        oth = _v(r, "other_income") or 0
        dep, fc, pbt, pat = (_v(r, k) for k in ("depreciation", "finance_cost", "profit_before_tax", "profit_after_tax"))
        ebitda = (pbt + (fc or 0) + (dep or 0) - oth) if pbt is not None else _v(r, "ebitda_reported")
        nw = _v(r, "net_worth") or _sum(_v(r, "share_capital"), _v(r, "reserves_and_surplus"))
        tnw = nw - (_v(r, "intangible_assets") or 0) if nw is not None else None
        quasi = _v(r, "unsecured_loans_from_promoters") or 0
        cm = _v(r, "current_maturities_of_long_term_debt")
        debt = _sum(_v(r, "long_term_borrowings"), _v(r, "short_term_borrowings"), cm)
        ca, cl, inv = _v(r, "total_current_assets"), _v(r, "total_current_liabilities"), _v(r, "inventory")
        ta, cogs = _v(r, "total_assets"), _v(r, "cost_of_materials_and_purchases")
        recv, pay = _v(r, "trade_receivables"), _v(r, "trade_payables")
        cfo, capex = _v(r, "cash_from_operations"), _v(r, "capex")
        tol = ta - nw if ta is not None and nw is not None else None

        def days(a, b):
            q = _div(a, b)
            return q * 365 if q is not None else None

        rd, idays, pdays = days(recv, rev), days(inv, cogs), days(pay, cogs)
        growth = _div(rev, prev_rev)
        out[fy] = {
            "revenue": rev,
            "revenue_growth": growth - 1 if growth is not None else None,
            "ebitda": ebitda,
            "ebitda_margin": _div(ebitda, rev),
            "pat": pat,
            "pat_margin": _div(pat, rev),
            "tnw": tnw,
            "total_debt": debt,
            "current_ratio": _div(ca, cl),
            "quick_ratio": _div(ca - inv, cl) if ca is not None and inv is not None else None,
            "debt_equity": _div(debt, tnw),
            "tol_tnw": _div(tol, tnw),
            "adj_tol_tnw": _div(tol - quasi, tnw + quasi) if quasi and tol is not None and tnw is not None else None,
            "debt_ebitda": _div(debt, ebitda),
            "interest_coverage": _div(ebitda, fc),
            "dscr": _div(_sum(pat, dep, fc), _sum(fc, cm)),
            "roe": _div(pat, tnw),
            "roce": _div(_sum(pbt, fc), _sum(tnw, debt)),
            "asset_turnover": _div(rev, ta),
            "receivable_days": rd,
            "inventory_days": idays,
            "payable_days": pdays,
            "cash_conversion_cycle": (rd + idays - pdays) if None not in (rd, idays, pdays) else None,
            "cfo_to_ebitda": _div(cfo, ebitda),
            "free_cash_flow": cfo - capex if cfo is not None and capex is not None else None,
        }
        prev_rev = rev
    meta = pd.DataFrame(FIN_KPIS, columns=["key", "KPI", "unit", "formula"]).set_index("key")
    return meta.join(pd.DataFrame(out))


def bank_kpis(bank: pd.DataFrame, counterparties: pd.DataFrame) -> pd.DataFrame:
    """One row per account."""
    rows = []
    for acct, g in bank.groupby("account"):
        n = len(g)
        credits = g["total_credits"].sum(min_count=1)
        own_in = g["transfers_in_from_own_or_related_accounts"].sum(min_count=1) if "transfers_in_from_own_or_related_accounts" in g else None
        own_out = g["transfers_out_to_own_or_related_accounts"].sum(min_count=1) if "transfers_out_to_own_or_related_accounts" in g else None
        loans_in = g["loan_disbursal_credits"].sum(min_count=1) if "loan_disbursal_credits" in g else None
        business = credits - (own_in if _ok(own_in) else 0) - (loans_in if _ok(loans_in) else 0) if _ok(credits) else None
        debits = g["total_debits"].sum(min_count=1)
        limit = g["od_cc_limit"].dropna().iloc[0] if g["od_cc_limit"].notna().any() else None
        abb = g["abb_month"].mean() if "abb_month" in g and g["abb_month"].notna().any() \
            else g[["opening_balance", "closing_balance"]].mean(axis=1).mean()
        emi = g["emi_or_loan_repayments"].mean()
        top5 = None
        if not counterparties.empty:
            cp = counterparties[(counterparties["account"] == acct) & (counterparties["side"] == "credit")]
            if not cp.empty and _ok(credits) and credits:
                top5 = cp.nlargest(5, "amount")["amount"].sum() / credits
        rows.append({
            "account": acct,
            "months_covered": n,
            "period": f"{g['month'].min()} to {g['month'].max()}",
            "total_credits": credits,
            "business_credits": business,
            "avg_monthly_credits": credits / n if n else None,
            "avg_monthly_debits": debits / n if n else None,
            "annualised_business_credits": business / n * 12 if n and _ok(business) else None,
            "abb": abb,
            "lowest_balance": g["min_balance"].min(),
            "abb_to_emi": _div(abb, emi),
            "abb_to_monthly_inflow": _div(abb, credits / n if n else None),
            "cash_deposit_share": _div(g["cash_deposits"].sum(min_count=1), credits),
            "related_transfer_share": _div(own_out, debits),
            "top5_counterparty_share": top5,
            "inward_returns": int(g["inward_cheque_returns"].fillna(0).sum()),
            "outward_bounces": int(g["outward_cheque_or_emi_bounces"].fillna(0).sum()),
            "monthly_emi_outflow": emi,
            "od_cc_limit": limit,
            # CC/OD balances are negative when drawn
            "limit_utilisation": _div((-g["closing_balance"]).clip(lower=0).mean(), limit) if limit else None,
            "credit_volatility_cv": _div(g["total_credits"].std(), g["total_credits"].mean()),
        })
    return pd.DataFrame(rows)


def _gst_measures(grp: pd.DataFrame) -> dict:
    """Turnover from GSTR-3B (falls back to GSTR-1), compliance and ITC checks for a set of returns."""
    rt = grp["return_type"].fillna("").str.upper()
    b3 = grp[rt.str.contains("3B")]
    r1 = grp[rt.str.contains("GSTR-1|GSTR1", regex=True)]
    base = b3 if not b3.empty else r1 if not r1.empty else grp
    turnover = base[["taxable_turnover", "exempt_nil_turnover", "export_turnover"]].sum(axis=1, min_count=1).sum(min_count=1)
    late = 0
    for _, r in grp.iterrows():
        try:
            if r["filing_date"] and r["due_date"] and date.fromisoformat(r["filing_date"]) > date.fromisoformat(r["due_date"]):
                late += 1
        except (ValueError, TypeError):
            pass
    t1, t3 = r1["taxable_turnover"].sum(min_count=1), b3["taxable_turnover"].sum(min_count=1)
    itc = b3["itc_claimed"].sum(min_count=1)
    itc2b = b3["itc_available_gstr2b"].sum(min_count=1) if "itc_available_gstr2b" in b3 else None
    months = base["period"].nunique()
    return {
        "months_filed": months,
        "gst_turnover": turnover,
        "avg_monthly_turnover": turnover / months if months else None,
        "tax_paid_cash": base["tax_paid_cash"].sum(min_count=1),
        "itc_claimed": itc,
        "itc_available_2b": itc2b,
        "itc_excess_over_2b": _div(itc - itc2b, itc2b) if _ok(itc) and _ok(itc2b) else None,
        "gstr1_vs_3b_gap": _div(t1 - t3, t3) if _ok(t1) and _ok(t3) else None,
        "late_filings": late,
    }


def gst_kpis(gst: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """GST measures per FY."""
    g = gst.copy()
    g["fy"] = g["period"].map(_fy_of)
    g = g[g["fy"].notna()]
    out = pd.DataFrame([{"fiscal_year": fy, **_gst_measures(grp)} for fy, grp in g.groupby("fy")])
    if not customers.empty and not out.empty:
        total = out["gst_turnover"].sum()
        out["top5_customer_share"] = _div(customers.nlargest(5, "taxable_value")["taxable_value"].sum(), total)
    return out


def gst_last_12_months(gst: pd.DataFrame) -> tuple[pd.DataFrame, dict | None]:
    """Monthly returns in the 12 calendar months ending at the latest month read, and their measures.
    Annual returns (GSTR-9, period YYYY-YY) are left out. Measures are None when there are no monthly returns."""
    is_month = gst["period"].astype(str).str.fullmatch(r"\d{4}-(0[1-9]|1[0-2])")
    monthly = gst[is_month & ~gst["return_type"].fillna("").astype(str).str.contains("9")]
    if monthly.empty:
        return monthly, None
    last = monthly["period"].max()
    first = (pd.Period(last, "M") - 11).strftime("%Y-%m")
    recent = monthly[monthly["period"] >= first]
    return recent, {"period": f"{recent['period'].min()} to {last}", **_gst_measures(recent)}


# ---------------------------------------------------------------- checks / flags

def risk_flags(t: dict, fk: pd.DataFrame, bk: pd.DataFrame, gk: pd.DataFrame, docs: list) -> list[Flag]:
    th, flags = THRESHOLDS, []
    fin = t["financials"]

    def add(*a):
        flags.append(Flag(*a))

    for c in t["conflicts"]:
        add("High", "Source conflict", f"Same year reported differently: {c}",
            "Financial statements", "Confirm the controlling (audited) version")

    years = [c for c in fk.columns if str(c).startswith("FY")] if not fk.empty else []
    if years:
        fy = years[-1]
        ev = f"{fy} financials ({fin.at[fy, 'doc_id']} p.{fin.at[fy, 'source_pages']})"

        def val(key):
            v = fk.at[key, fy]
            return v if _ok(v) else None

        cr = val("current_ratio")
        if cr is not None and cr < th["current_ratio_floor"]:
            add("High", "Liquidity", f"Current ratio {cr:.2f}x in {fy} - below the 1.17x floor", ev,
                "Review working-capital funding and creditor stretch")
        elif cr is not None and cr < th["current_ratio_benchmark"]:
            add("Medium", "Liquidity", f"Current ratio {cr:.2f}x in {fy} - below the 1.33x benchmark", ev,
                "Check margin money for working capital")
        checks = [
            ("dscr", lambda v: v < th["dscr_min"], "High", "Debt service", "DSCR {v:.2f}x in {fy} - below 1.25x",
             "Stress-test the repayment schedule"),
            ("interest_coverage", lambda v: v < th["interest_coverage_min"], "High", "Debt service",
             "Interest coverage {v:.2f}x in {fy} - below 2.0x", "Check cost of debt and EBITDA quality"),
            ("debt_equity", lambda v: v > th["debt_equity_max"], "Medium", "Leverage",
             "Debt/TNW {v:.2f}x in {fy} - above 3.0x", "Assess further leverage capacity"),
            ("tol_tnw", lambda v: v > th["tol_tnw_max"], "Medium", "Leverage",
             "TOL/TNW {v:.2f}x in {fy} - above 4.0x", "Check promoter unsecured loans (quasi-equity)"),
            ("debt_ebitda", lambda v: v > th["debt_ebitda_max"], "Medium", "Leverage",
             "Debt/EBITDA {v:.2f}x in {fy} - above 3.5x", "Review debt capacity"),
            ("receivable_days", lambda v: v > th["receivable_days_max"], "Medium", "Working capital",
             "Receivable days of {v:.0f} in {fy} - collections are slow", "Get debtor ageing; check related-party debtors"),
            ("pat", lambda v: v < 0, "High", "Profitability", "Net loss in {fy}", "Understand cause and path to profit"),
            ("tnw", lambda v: v < 0, "High", "Leverage", "Negative tangible net worth in {fy}", "Check capital infusion plan"),
            ("revenue_growth", lambda v: v < -0.10, "Medium", "Profitability", "Revenue changed {v:+.0%} in {fy}",
             "Ask management for the reason"),
            ("cfo_to_ebitda", lambda v: v < 0.5, "Medium", "Earnings quality",
             "Only {v:.0%} of EBITDA turned into operating cash in {fy}", "Review working-capital build-up"),
        ]
        for key, bad, sev, area, text, step in checks:
            v = val(key)
            if v is not None and bad(v):
                add(sev, area, text.format(v=v, fy=fy), ev, step)

        dscrs = [fk.at["dscr", y] for y in years if _ok(fk.at["dscr", y])]
        if len(dscrs) > 1 and sum(dscrs) / len(dscrs) < th["dscr_avg_min"]:
            add("Medium", "Debt service", f"Average DSCR {sum(dscrs) / len(dscrs):.2f}x - below the 1.50x norm",
                "All years", "Check repayment tenor / moratorium")

        rep = fin.at[fy, "ebitda_reported"] if "ebitda_reported" in fin else None
        calc = val("ebitda")
        if _ok(rep) and calc and abs(rep - calc) > 0.05 * abs(calc):
            add("Medium", "Earnings quality", f"Reported EBITDA {_inr(rep)} vs computed {_inr(calc)} in {fy}",
                ev, "Get management's EBITDA bridge / add-backs")

    # GST
    for _, r in gk.iterrows() if not gk.empty else []:
        fy = r["fiscal_year"]
        if fy in years and r["months_filed"] >= 12:
            rev = fk.at["revenue", fy]
            gap = _div(r["gst_turnover"] - rev, rev)
            if gap is not None and abs(gap) > th["gst_vs_books_tolerance"]:
                add("High", "GST vs books", f"{fy}: GST turnover {_inr(r['gst_turnover'])} vs book revenue {_inr(rev)} ({gap:+.0%})",
                    "GST returns vs financials", "Reconcile (exempt sales, stock transfers, unbilled revenue)")
        if r["late_filings"]:
            add("Low", "GST compliance", f"{fy}: {r['late_filings']} return(s) filed late", "GST returns",
                "Check interest / late fee paid")
        if _ok(r["gstr1_vs_3b_gap"]) and abs(r["gstr1_vs_3b_gap"]) > th["gstr1_vs_3b_tolerance"]:
            add("Medium", "GST compliance", f"{fy}: GSTR-1 vs GSTR-3B turnover gap {r['gstr1_vs_3b_gap']:+.0%}",
                "GST returns", "Ask for GSTR-1 vs 3B reconciliation (Rule 88C notice risk)")
        if _ok(r.get("itc_excess_over_2b")) and r["itc_excess_over_2b"] > th["itc_3b_vs_2b_tolerance"]:
            add("Medium", "GST compliance", f"{fy}: ITC claimed is {r['itc_excess_over_2b']:+.0%} above GSTR-2B",
                "GST returns", "Excess ITC may be reversed (Rule 88D) - check supplier filings")
        if _ok(r.get("top5_customer_share")) and r["top5_customer_share"] > th["top5_customer_share_max"]:
            add("Medium", "Concentration", f"Top 5 customers are {r['top5_customer_share']:.0%} of GST turnover",
                "GSTR-1", "Check customer contracts and credit quality")

    # Banking
    for _, r in bk.iterrows() if not bk.empty else []:
        ev = f"Bank statement {r['account']}"
        b = r["outward_bounces"]
        if b >= th["bounces_high"]:
            add("High", "Banking conduct", f"{b} outward cheque/EMI bounces in {r['period']}", ev, "Decline-level conduct; check CIBIL/CRILC")
        elif b >= th["bounces_refer"]:
            add("Medium", "Banking conduct", f"{b} outward cheque/EMI bounces in {r['period']}", ev, "Refer - get explanation for each")
        elif b > 0:
            add("Low", "Banking conduct", f"{b} outward cheque/EMI bounce(s) in {r['period']}", ev, "Get explanation")
        if r["inward_returns"] > 2:
            add("Low", "Banking conduct", f"{r['inward_returns']} inward cheque returns", ev, "Check quality of customers")
        cash = r["cash_deposit_share"]
        if _ok(cash) and cash > th["cash_deposit_share_high"]:
            add("High", "Banking conduct", f"Cash deposits are {cash:.0%} of credits", ev, "Understand cash sales; AML check")
        elif _ok(cash) and cash > th["cash_deposit_share_refer"]:
            add("Medium", "Banking conduct", f"Cash deposits are {cash:.0%} of credits", ev, "Understand cash sales")
        rel = r["related_transfer_share"]
        if _ok(rel) and rel > th["related_transfer_share_max"]:
            add("Medium", "Fund diversion", f"{rel:.0%} of debits go to own / related accounts", ev,
                "Trace fund flows; check for round-tripping")
        top5 = r["top5_counterparty_share"]
        if _ok(top5) and top5 > th["top5_customer_share_max"]:
            add("Medium", "Concentration", f"Top 5 payers are {top5:.0%} of bank credits", ev, "Check customer dependence")
        thin = r["abb_to_monthly_inflow"]
        if _ok(thin) and 0 <= thin < th["balance_to_inflow_min"]:
            add("Low", "Liquidity", f"Average balance is only {thin:.0%} of monthly inflows", ev, "Thin cash cushion; check sweep / other accounts")
        if r["months_covered"] < 12:
            add("Low", "Coverage", f"Only {r['months_covered']} months of statement", ev, "Request 12 months")
    if not bk.empty and years:
        fy = years[-1]
        ann = bk["annualised_business_credits"].sum(min_count=1)
        ratio = _div(ann, fk.at["revenue", fy])
        if ratio is not None and not (th["bank_credit_to_revenue_min"] <= ratio <= th["bank_credit_to_revenue_max"]):
            add("Medium", "Bank vs books", f"Annualised business credits {_inr(ann)} are {ratio:.0%} of {fy} revenue",
                "Bank statements vs financials", "Check other accounts and non-business credits")

    # Director ITRs (optional)
    itr = t["itr"]
    if not itr.empty:
        for name, g in itr.groupby("assessee_name"):
            g = g.sort_values("assessment_year")
            if len(g) >= 2:
                a, b = g["total_income"].iloc[-2], g["total_income"].iloc[-1]
                if _ok(a) and _ok(b) and a and (b - a) / a < -0.25:
                    add("Low", "Promoter income", f"{name}: total income fell {(b - a) / a:.0%} YoY", "ITRs",
                        "Understand the promoter's other commitments")

    for d in docs:
        if d.get("status") not in ("extracted", "demo"):
            add("Medium", "Coverage", f"{d['filename']} not analysed ({d.get('status')})", d["doc_id"],
                d.get("error") or "Re-upload or unlock")
        for w in d.get("warnings") or []:
            add("Low", "Coverage", f"{d['filename']}: {w}", d["doc_id"], "Check it was filed correctly")
        for q in (d.get("extracted") or {}).get("auditor_qualifications", []) or []:
            add("High", "Audit", f"Auditor qualification: {q}", d["doc_id"], "Discuss with the auditor")
    for kind, label in (("financials", "Financial statements"), ("bank_statement", "Bank statements"),
                        ("gst", "GST returns")):
        if not any(d["doc_type"] == kind and d.get("extracted") for d in docs):
            add("Low", "Coverage", f"No {label} provided", "-", f"Request {label}")

    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(flags, key=lambda f: order[f.severity])


AREAS = {  # flag area -> scorecard pillar
    "Profitability": "Financial strength", "Leverage": "Financial strength", "Debt service": "Financial strength",
    "Liquidity": "Liquidity & working capital", "Working capital": "Liquidity & working capital",
    "Earnings quality": "Earnings quality", "Source conflict": "Earnings quality", "Audit": "Earnings quality",
    "Bank vs books": "Earnings quality", "GST vs books": "Earnings quality",
    "Banking conduct": "Banking conduct", "Fund diversion": "Banking conduct", "Concentration": "Banking conduct",
    "GST compliance": "Tax compliance", "Promoter income": "Tax compliance", "Coverage": "Data coverage",
}


def scorecard(flags: pd.DataFrame) -> list[dict]:
    """Worst flag per pillar - an attention guide, not a credit decision."""
    pillars = list(dict.fromkeys(AREAS.values()))
    out = []
    for p in pillars:
        f = flags[flags["area"].map(AREAS) == p] if not flags.empty else flags
        sev = next((s for s in ("High", "Medium", "Low") if not f.empty and (f["severity"] == s).any()), None)
        status = {"High": "critical", "Medium": "warning", "Low": "info", None: "good"}[sev]
        out.append({"pillar": p, "status": status, "count": len(f)})
    return out


def analyse(docs: list, tables: dict | None = None) -> dict:
    """tables: optional analyst-edited output of build_tables()."""
    t = tables or build_tables(docs)
    fk = financial_kpis(t["financials"]) if not t["financials"].empty else pd.DataFrame()
    bk = bank_kpis(t["bank"], t["counterparties"]) if not t["bank"].empty else pd.DataFrame()
    gk = gst_kpis(t["gst"], t["customers"]) if not t["gst"].empty else pd.DataFrame()
    flags = pd.DataFrame([asdict(f) for f in risk_flags(t, fk, bk, gk, docs)],
                         columns=["severity", "area", "finding", "evidence", "next_step"])
    return {"tables": t, "fin_kpis": fk, "bank_kpis": bk, "gst_kpis": gk, "flags": flags,
            "scorecard": scorecard(flags)}
