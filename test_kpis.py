"""Run: python -m pytest test_kpis.py"""

import pandas as pd
import pytest

import kpis
from demo_data import demo_docs


def fin_doc(**overrides):
    year = {k: None for k in kpis.build_tables(demo_docs())["financials"].columns if k not in ("doc_id",)}
    year.update(fiscal_year="FY2024-25", audited=True, revenue_from_operations=1000.0, other_income=0.0,
                depreciation=50.0, finance_cost=40.0, profit_before_tax=110.0, profit_after_tax=80.0,
                net_worth=400.0, long_term_borrowings=300.0, short_term_borrowings=100.0,
                current_maturities_of_long_term_debt=60.0, total_current_assets=500.0,
                total_current_liabilities=250.0, inventory=100.0, total_assets=1200.0, source_pages=[1])
    year.update(overrides)
    return {"doc_id": "D01", "filename": "fs.pdf", "doc_type": "financials", "status": "extracted",
            "extracted": {"years": [year]}}


def kpi(doc, key):
    return kpis.analyse([doc])["fin_kpis"].at[key, "FY2024-25"]


def test_ratios():
    d = fin_doc()
    assert kpi(d, "ebitda") == 200                       # 110 + 40 + 50
    assert kpi(d, "current_ratio") == 2
    assert kpi(d, "quick_ratio") == pytest.approx(1.6)
    assert kpi(d, "debt_equity") == pytest.approx(460 / 400)
    assert kpi(d, "tol_tnw") == pytest.approx(800 / 400)
    assert kpi(d, "dscr") == pytest.approx(170 / 100)    # (80+50+40)/(40+60)
    assert kpi(d, "interest_coverage") == 5


def test_missing_inputs_are_not_guessed():
    d = fin_doc(total_current_liabilities=None, finance_cost=None)
    assert pd.isna(kpi(d, "current_ratio"))
    assert pd.isna(kpi(d, "interest_coverage"))


def test_flags():
    d = fin_doc(profit_after_tax=-10.0, total_current_liabilities=500.0)
    findings = " ".join(kpis.analyse([d])["flags"]["finding"])
    assert "Net loss" in findings
    assert "Current ratio 1.00x" in findings
    assert "No Bank statements provided" in findings


def test_demo_end_to_end():
    res = kpis.analyse(demo_docs())
    assert not res["bank_kpis"].empty and not res["gst_kpis"].empty
    assert (res["flags"]["area"] == "GST vs books").any()
    assert res["bank_kpis"].at[0, "top5_counterparty_share"] > 0.6
    assert {s["pillar"] for s in res["scorecard"]} >= {"Banking conduct", "Financial strength"}


def test_bounce_bands():
    docs = demo_docs()
    for i, m in enumerate(docs[1]["extracted"]["months"]):
        m["outward_cheque_or_emi_bounces"] = 1 if i < 6 else 0
    flags = kpis.analyse(docs)["flags"]
    assert ((flags["area"] == "Banking conduct") & (flags["severity"] == "High")).any()


def test_itrs_are_optional():
    docs = [d for d in demo_docs() if d["doc_type"] != "itr"]
    findings = " ".join(kpis.analyse(docs)["flags"]["finding"])
    assert "ITR" not in findings


def test_fy_mapping():
    assert kpis._fy_of("2025-03") == "FY2024-25"
    assert kpis._fy_of("2025-04") == "FY2025-26"
