"""Fictional extracted data so the dashboard can be explored without an API key."""

import random

L = 100_000  # one lakh


def _fy(fy, rev, aud):
    return {
        "fiscal_year": fy, "audited": aud,
        "revenue_from_operations": rev, "other_income": 12 * L,
        "cost_of_materials_and_purchases": rev * 0.62, "change_in_inventory": -8 * L,
        "employee_cost": rev * 0.08, "other_expenses": rev * 0.14, "ebitda_reported": None,
        "depreciation": 55 * L, "finance_cost": 68 * L,
        "profit_before_tax": rev * 0.16 - 55 * L - 68 * L + 12 * L + 8 * L,
        "tax_expense": None, "profit_after_tax": None,
        "intangible_assets": 15 * L, "unsecured_loans_from_promoters": 120 * L,
        "share_capital": 200 * L, "reserves_and_surplus": None, "net_worth": None,
        "long_term_borrowings": 420 * L, "short_term_borrowings": 310 * L,
        "current_maturities_of_long_term_debt": 95 * L, "trade_payables": rev * 0.11,
        "total_current_liabilities": rev * 0.24, "fixed_assets_net": 900 * L,
        "inventory": rev * 0.12, "trade_receivables": rev * 0.21, "cash_and_bank": 40 * L,
        "total_current_assets": rev * 0.36, "total_assets": None,
        "cash_from_operations": rev * 0.09, "capex": 110 * L, "source_pages": [4, 5, 6],
    }


def demo_docs() -> list:
    random.seed(7)
    fy24, fy25 = _fy("FY2023-24", 3650 * L, True), _fy("FY2024-25", 4280 * L, True)
    for y, res in ((fy24, 610 * L), (fy25, 820 * L)):
        y["tax_expense"] = y["profit_before_tax"] * 0.252
        y["profit_after_tax"] = y["profit_before_tax"] - y["tax_expense"]
        y["reserves_and_surplus"] = res
        y["net_worth"] = y["share_capital"] + res
        y["total_assets"] = y["fixed_assets_net"] + y["total_current_assets"] + 60 * L

    months = [f"{2024 + (m > 12)}-{(m - 1) % 12 + 1:02d}" for m in range(4, 16)]
    bal, bank_months, gst_periods = 35 * L, [], []
    for i, m in enumerate(months):
        cr = 4280 * L / 12 * random.uniform(0.85, 1.12)
        dr = cr * random.uniform(0.95, 1.04)
        close = bal + cr - dr
        bank_months.append({
            "month": m, "opening_balance": bal, "closing_balance": close,
            "total_credits": cr, "total_debits": dr, "credit_count": random.randint(140, 210),
            "debit_count": random.randint(260, 340), "min_balance": min(bal, close) - 12 * L,
            "max_balance": max(bal, close) + 25 * L, "cash_deposits": cr * 0.04,
            "cash_withdrawals": 3 * L, "inward_cheque_returns": 1 if i in (2, 7) else 0,
            "outward_cheque_or_emi_bounces": 1 if i == 9 else 0,
            "emi_or_loan_repayments": 9.5 * L,
            "balances_on_5_10_15_20_25": [bal + (close - bal) * k / 5 - random.uniform(5, 30) * L for k in range(5)],
            "transfers_in_from_own_or_related_accounts": cr * 0.03, "transfers_out_to_own_or_related_accounts": dr * 0.06,
            "loan_disbursal_credits": 0, "source_pages": [i + 1],
        })
        bal = close
        taxable = cr * 1.02 / 1.18
        gst_periods.append({
            "period": m, "return_type": "GSTR-3B", "taxable_turnover": taxable,
            "exempt_nil_turnover": 0, "export_turnover": 0, "igst": taxable * 0.10,
            "cgst": taxable * 0.04, "sgst": taxable * 0.04, "cess": 0,
            "tax_paid_cash": taxable * 0.05, "itc_claimed": taxable * 0.13, "itc_available_gstr2b": taxable * 0.12,
            "filing_date": f"{m}-{18 if i != 5 else 28}", "due_date": f"{m}-20", "source_pages": [1],
        })
    # GST dates are for the following month
    for p in gst_periods:
        y, mo = int(p["period"][:4]), int(p["period"][5:])
        nxt = f"{y + (mo == 12)}-{mo % 12 + 1:02d}"
        p["filing_date"] = nxt + p["filing_date"][7:]
        p["due_date"] = nxt + "-20"

    def itr(name, ay, sal, other):
        return {"assessee_name": name, "assessee_type": "Individual", "pan_last4": "123F",
                "assessment_year": ay, "itr_form": "ITR-2", "filing_date": None,
                "income_salary": sal, "income_house_property": 2.4 * L, "income_business_profession": None,
                "income_capital_gains": None, "income_other_sources": other,
                "gross_total_income": sal + 2.4 * L + other, "deductions_chapter_via": 1.5 * L,
                "total_income": sal + 0.9 * L + other, "tax_payable": (sal + other) * 0.27,
                "taxes_paid_tds_advance": (sal + other) * 0.27, "refund_or_demand": 0, "source_pages": [1, 2]}

    return [
        {"doc_id": "D01", "filename": "Audited_FS_FY24_FY25.pdf", "doc_type": "financials", "status": "demo",
         "extracted": {"entity_name": "Demo Polymers Pvt Ltd", "unit_stated_in_document": "INR lakhs",
                       "years": [fy24, fy25], "auditor_qualifications": [], "contingent_liabilities": 85 * L,
                       "related_party_notes": ["Rent paid to director: 18 lakhs"], "notes": []}},
        {"doc_id": "D02", "filename": "HDFC_CC_Statement_FY25.pdf", "doc_type": "bank_statement", "status": "demo",
         "extracted": {"bank_name": "HDFC Bank", "account_holder": "Demo Polymers Pvt Ltd", "account_type": "Cash credit",
                       "account_number_last4": "4471", "sanctioned_od_cc_limit": 300 * L,
                       "statement_from": "2024-04-01", "statement_to": "2025-03-31", "months": bank_months,
                       "top_credit_counterparties": [
                           {"name": n, "amount": a * L, "count": c} for n, a, c in
                           (("Apex Packaging Ltd", 980, 41), ("Nova Retail Pvt Ltd", 610, 33), ("Kiran Plastics", 420, 28),
                            ("Sunrise FMCG", 390, 22), ("Metro Distributors", 300, 19))],
                       "top_debit_counterparties": [],
                       "notable_transactions": [], "notes": []}},
        {"doc_id": "D03", "filename": "GSTR3B_FY25.pdf", "doc_type": "gst", "status": "demo",
         "extracted": {"gstin": "27AABCD1234E1Z5", "legal_name": "Demo Polymers Pvt Ltd", "state": "Maharashtra",
                       "periods": gst_periods, "notes": [],
                       "top_customers": [{"name_or_gstin": "Apex Packaging Ltd", "taxable_value": 820 * L},
                                         {"name_or_gstin": "Nova Retail Pvt Ltd", "taxable_value": 515 * L}]}},
        {"doc_id": "D04", "filename": "ITR_Director_A_AY24.pdf", "doc_type": "itr", "status": "demo",
         "extracted": itr("Director A", "AY2024-25", 36 * L, 4 * L)},
        {"doc_id": "D05", "filename": "ITR_Director_A_AY25.pdf", "doc_type": "itr", "status": "demo",
         "extracted": itr("Director A", "AY2025-26", 42 * L, 5 * L)},
        {"doc_id": "D06", "filename": "ITR_Director_B_AY25.pdf", "doc_type": "itr", "status": "demo",
         "extracted": itr("Director B", "AY2025-26", 24 * L, 1 * L)},
    ]


def demo_case() -> dict:
    """Borrower, facilities requested and security offered for the demo company."""
    from datetime import date

    import security as sec

    def s(kind, value, **kw):
        return {"id": kind[:6].lower().replace(" ", "") + str(int(value) % 997), "kind": kind, "description": "",
                "owner": "Demo Polymers Pvt Ltd", "owner_is_third_party": False, "value": value, "valuation_date": None,
                "valuer": "", "existing_charge": "", "margin_pct": float(sec.SECURITY_TYPES[kind][2]), **kw}

    return {
        "borrower": {"name": "Demo Polymers Pvt Ltd", "constitution": "Private limited company",
                     "industry": "Plastic packaging manufacturer", "msme_class": "Small", "years_in_business": 14,
                     "existing_exposure": 825 * L},
        "facilities": [
            {"id": "cc01", "product": "Cash Credit (CC)", "amount": 600 * L, "rate_pct": 9.75,
             "purpose": "Working capital for raw material and receivables", "projected_turnover": 0.0},
            {"id": "tl01", "product": "Machinery / equipment loan", "amount": 400 * L, "rate_pct": 10.25,
             "purpose": "Second extrusion line", "tenor_months": 72, "moratorium_months": 6, "project_cost": 540 * L},
            {"id": "bg01", "product": "Performance guarantee", "amount": 150 * L, "rate_pct": 1.5,
             "purpose": "Supply contract with a state utility", "tenor_months": 24, "beneficiary": "State Power Utility",
             "contracts_requiring_bg": 1800 * L, "bg_pct_of_contract": 10.0, "cash_margin_pct": 15.0},
            {"id": "lc01", "product": "Letter of credit", "amount": 100 * L, "rate_pct": 1.0,
             "purpose": "Polymer resin purchases", "lc_kind": "Inland usance", "annual_purchases_lc": 900 * L,
             "lead_time_days": 15, "usance_days": 60, "cash_margin_pct": 10.0},
        ],
        "securities": [
            s("Industrial property / factory", 950 * L, description="Plot 14, MIDC Ambernath, 2.1 acres with shed",
              valuation_date=date(2022, 3, 15), valuer="ABC Valuers LLP"),
            s("Stock (inventory)", 510 * L, description="Raw material, WIP and finished goods"),
            s("Book debts / receivables (< 90 days)", 760 * L, description="Domestic receivables under 90 days"),
            s("Plant & machinery", 540 * L, description="New extrusion line (to be financed)"),
            s("Fixed deposit", 32.5 * L, description="Cash margin FD for BG / LC"),
            s("Personal guarantee", 0.0, owner="Director A", description="Managing director"),
        ],
        "repay": {"tl01": {"method": "Step-up instalments", "freq": "Monthly", "step_up_pct": 10.0,
                           "structured": "10,15,20,25,30"}},
        "dscr": {"growth_pct": 8.0, "tax_pct": 25.0, "wc_utilisation_pct": 80.0},
    }


def demo_kyc() -> list:
    """KYC documents on the demo case (names only). Address proof and Director B's ID proof are not attached yet."""
    docs = [("certificate_of_incorporation", ""), ("moa", ""), ("aoa", ""), ("entity_pan", ""), ("gst_certificate", ""),
            ("udyam", ""), ("board_resolution", ""), ("shareholding", ""), ("pan", "Director A"),
            ("aadhaar", "Director A"), ("photo", "Director A"), ("pan", "Director B")]
    return [{"doc_id": f"K{i:02d}", "filename": f"{kind}{'_' + person.replace(' ', '_') if person else ''}.pdf",
             "doc_type": kind, "person": person} for i, (kind, person) in enumerate(docs, 1)]


def demo_security_docs() -> list:
    return [{"doc_id": "S01", "filename": "Valuation_MIDC_Ambernath_2022.pdf", "doc_type": "security", "status": "demo",
             "extracted": {"document_type": "Valuation report", "suggested_security_kind": "Industrial property / factory",
                           "description": "Plot 14, MIDC Ambernath, 2.1 acres with shed", "owner": "Demo Polymers Pvt Ltd",
                           "market_value": 950 * L, "realisable_or_distress_value": 760 * L, "valuation_date": "2022-03-15",
                           "valuer": "ABC Valuers LLP", "existing_charge_or_encumbrance": None, "area": "2.1 acres",
                           "source_pages": [1, 4], "issues": ["Valuation older than 3 years"]}}]
