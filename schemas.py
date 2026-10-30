"""JSON schemas Claude must fill when extracting each document type.

Every number is nullable: the model returns null ("NOT FOUND") instead of
guessing. All amounts are absolute INR (lakhs/crores converted).
"""

NUM = {"type": ["number", "null"]}
INT = {"type": ["integer", "null"]}
STR = {"type": ["string", "null"]}
PAGES = {"type": "array", "items": {"type": "integer"}}

DOC_TYPES = {
    "financials": "Audited / provisional financial statements (P&L, balance sheet, cash flow)",
    "bank_statement": "Bank account statement",
    "gst": "GST returns (GSTR-1, GSTR-3B, GSTR-9) or GST filing summary",
    "itr": "Income tax return / computation of income of a director, promoter or the company",
    "other": "Anything else (sanction letter, MCA filing, CMA data, etc.)",
}


def _obj(props: dict) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


CLASSIFY_SCHEMA = _obj({
    "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
    "entity_name": STR,
    "period_covered": STR,
    "reason": {"type": "string"},
})

FIN_YEAR = _obj({
    "fiscal_year": {"type": "string", "description": "Format FY2024-25"},
    "audited": {"type": ["boolean", "null"]},
    "revenue_from_operations": NUM,
    "other_income": NUM,
    "cost_of_materials_and_purchases": NUM,
    "change_in_inventory": NUM,
    "employee_cost": NUM,
    "other_expenses": NUM,
    "ebitda_reported": NUM,
    "depreciation": NUM,
    "finance_cost": NUM,
    "profit_before_tax": NUM,
    "tax_expense": NUM,
    "profit_after_tax": NUM,
    "intangible_assets": NUM,
    "unsecured_loans_from_promoters": NUM,
    "share_capital": NUM,
    "reserves_and_surplus": NUM,
    "net_worth": NUM,
    "long_term_borrowings": NUM,
    "short_term_borrowings": NUM,
    "current_maturities_of_long_term_debt": NUM,
    "trade_payables": NUM,
    "total_current_liabilities": NUM,
    "fixed_assets_net": NUM,
    "inventory": NUM,
    "trade_receivables": NUM,
    "cash_and_bank": NUM,
    "total_current_assets": NUM,
    "total_assets": NUM,
    "cash_from_operations": NUM,
    "capex": NUM,
    "source_pages": PAGES,
})

FINANCIALS_SCHEMA = _obj({
    "entity_name": STR,
    "unit_stated_in_document": STR,
    "years": {"type": "array", "items": FIN_YEAR},
    "auditor_qualifications": {"type": "array", "items": {"type": "string"}},
    "contingent_liabilities": NUM,
    "related_party_notes": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "array", "items": {"type": "string"}},
})

BANK_MONTH = _obj({
    "month": {"type": "string", "description": "Format YYYY-MM"},
    "opening_balance": NUM,
    "closing_balance": NUM,
    "total_credits": NUM,
    "total_debits": NUM,
    "credit_count": INT,
    "debit_count": INT,
    "min_balance": NUM,
    "max_balance": NUM,
    "cash_deposits": NUM,
    "cash_withdrawals": NUM,
    "inward_cheque_returns": INT,
    "outward_cheque_or_emi_bounces": INT,
    "emi_or_loan_repayments": NUM,
    "balances_on_5_10_15_20_25": {"type": "array", "items": {"type": "number"},
                                  "description": "End-of-day balance on the 5th, 10th, 15th, 20th and 25th"},
    "transfers_in_from_own_or_related_accounts": NUM,
    "transfers_out_to_own_or_related_accounts": NUM,
    "loan_disbursal_credits": NUM,
    "source_pages": PAGES,
})

BANK_SCHEMA = _obj({
    "bank_name": STR,
    "account_holder": STR,
    "account_type": STR,
    "account_number_last4": STR,
    "sanctioned_od_cc_limit": NUM,
    "statement_from": STR,
    "statement_to": STR,
    "months": {"type": "array", "items": BANK_MONTH},
    "top_credit_counterparties": {"type": "array", "items": _obj({
        "name": {"type": "string"},
        "amount": NUM,
        "count": INT,
    })},
    "top_debit_counterparties": {"type": "array", "items": _obj({
        "name": {"type": "string"},
        "amount": NUM,
        "count": INT,
    })},
    "notable_transactions": {"type": "array", "items": _obj({
        "date": STR,
        "description": {"type": "string"},
        "amount": NUM,
        "why_notable": {"type": "string"},
        "page": INT,
    })},
    "notes": {"type": "array", "items": {"type": "string"}},
})

GST_PERIOD = _obj({
    "period": {"type": "string", "description": "Format YYYY-MM (or YYYY-YY for annual GSTR-9)"},
    "return_type": {"type": "string", "description": "GSTR-1, GSTR-3B, GSTR-9, ..."},
    "taxable_turnover": NUM,
    "exempt_nil_turnover": NUM,
    "export_turnover": NUM,
    "igst": NUM,
    "cgst": NUM,
    "sgst": NUM,
    "cess": NUM,
    "tax_paid_cash": NUM,
    "itc_claimed": NUM,
    "itc_available_gstr2b": NUM,
    "filing_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
    "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
    "source_pages": PAGES,
})

GST_SCHEMA = _obj({
    "gstin": STR,
    "legal_name": STR,
    "state": STR,
    "periods": {"type": "array", "items": GST_PERIOD},
    "top_customers": {"type": "array", "items": _obj({
        "name_or_gstin": {"type": "string"},
        "taxable_value": NUM,
    })},
    "notes": {"type": "array", "items": {"type": "string"}},
})

ITR_SCHEMA = _obj({
    "assessee_name": STR,
    "assessee_type": {"type": ["string", "null"], "description": "Individual, HUF, Company, Firm, LLP"},
    "pan_last4": STR,
    "assessment_year": {"type": ["string", "null"], "description": "Format AY2025-26"},
    "itr_form": STR,
    "filing_date": STR,
    "income_salary": NUM,
    "income_house_property": NUM,
    "income_business_profession": NUM,
    "income_capital_gains": NUM,
    "income_other_sources": NUM,
    "gross_total_income": NUM,
    "deductions_chapter_via": NUM,
    "total_income": NUM,
    "tax_payable": NUM,
    "taxes_paid_tds_advance": NUM,
    "refund_or_demand": NUM,
    "employer_or_business_names": {"type": "array", "items": {"type": "string"}},
    "source_pages": PAGES,
    "notes": {"type": "array", "items": {"type": "string"}},
})

OTHER_SCHEMA = _obj({
    "title": STR,
    "entity_name": STR,
    "summary": {"type": "string"},
    "key_figures": {"type": "array", "items": _obj({
        "label": {"type": "string"},
        "value": NUM,
        "period": STR,
        "page": INT,
    })},
    "risks_or_observations": {"type": "array", "items": {"type": "string"}},
})

DETECTED = {"type": "string", "enum": list(DOC_TYPES),
            "description": "What this document actually is, regardless of how it was filed"}
for _schema in (FINANCIALS_SCHEMA, BANK_SCHEMA, GST_SCHEMA, ITR_SCHEMA, OTHER_SCHEMA):
    _schema["properties"] = {"detected_type": DETECTED, **_schema["properties"]}
    _schema["required"] = list(_schema["properties"])

EXTRACT_SCHEMAS = {
    "financials": FINANCIALS_SCHEMA,
    "bank_statement": BANK_SCHEMA,
    "gst": GST_SCHEMA,
    "itr": ITR_SCHEMA,
    "other": OTHER_SCHEMA,
}
