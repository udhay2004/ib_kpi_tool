"""Run: python -m pytest test_flow.py

The seven-step flow: step 1 repayment potential maths, optional security, the product taxonomy (equity / debt,
fund / non-fund, secured / unsecured), non-fund limits, optional KYC, old case files and empty reports."""

import json

import fitz
import pytest

import borrower as bw
import kpis
import loans
from engine import Workspace, clean_case

L = 1e5
TH = kpis.thresholds()


# ---------------------------------------------------------------- step 1 maths

def test_foir_surplus_and_max_loan():
    assert bw.foir(30_000, 100_000) == pytest.approx(0.30)
    assert bw.foir(30_000, 0) is None and bw.foir(30_000, None) is None
    assert bw.surplus(100_000, 30_000, 0.55) == pytest.approx(25_000)
    assert bw.surplus(100_000, 70_000, 0.55) == 0  # never below zero
    emi, n, r = 25_000, 60, 12.0
    i = r / 1200
    assert bw.max_loan(emi, r, n) == pytest.approx(emi * (1 - (1 + i) ** -n) / i)
    assert bw.max_loan(emi, 0, n) == pytest.approx(emi * n)
    assert bw.max_loan(None, r, n) is None and bw.max_loan(emi, r, 0) is None


@pytest.mark.parametrize("score,band", [(900, "Strong"), (750, "Strong"), (749, "Good"), (700, "Good"), (699, "Fair"),
                                        (650, "Fair"), (649, "Weak"), (300, "Weak"), (None, "No score"), (950, "No score"),
                                        ("abc", "No score")])
def test_bureau_bands(score, band):
    assert bw.bureau_band(score, TH)[0] == band


def test_income_net_worth_and_track_record():
    inc = bw.income_summary([
        {"source": "Salary", "amount": 1 * L, "frequency": "Monthly", "stability": "Stable", "proof": "Y"},
        {"source": "Other", "amount": 6 * L, "frequency": "Annual", "stability": "One-off", "proof": "N"},
        {"source": "Rental", "amount": None, "frequency": "Monthly", "stability": "Stable"}])  # blank stays out
    assert inc["total_annual"] == pytest.approx(18 * L) and inc["stable_annual"] == pytest.approx(12 * L)
    assert inc["eligible_monthly"] == pytest.approx(1 * L) and inc["count"] == 2
    nw = bw.net_worth([{"type": "Property", "value": 100 * L, "basis": "Documented"}, {"type": "Vehicles", "value": 20 * L}],
                      [{"type": "Loans", "value": 30 * L}])
    assert nw["net_worth"] == pytest.approx(90 * L) and nw["documented_share"] == pytest.approx(100 / 120)
    assert bw.net_worth([], [])["net_worth"] is None
    assert bw.track_record([], TH)["rating"] == "No history"
    assert bw.track_record([{"lender": "A", "status": "Closed", "max_dpd": 0}], TH)["rating"] == "Clean"
    assert bw.track_record([{"lender": "A", "status": "Regular", "max_dpd": 45}], TH)["rating"] == "Satisfactory"
    assert bw.track_record([{"lender": "A", "status": "Restructured"}], TH)["rating"] == "Weak"
    assert bw.track_record([{"lender": "A", "status": "Regular", "max_dpd": 95}], TH)["rating"] == "Weak"
    assert bw.track_record([{"lender": "A", "status": "Written-off"}, {"lender": "B", "status": "Closed"}], TH)["rating"] == "Adverse"


def test_potential_verdicts_and_missing():
    empty = bw.assess({}, TH)
    assert empty["verdict"]["label"] == "Not assessed" and "income sources" in empty["missing"]
    strong = bw.assess({"income": [{"amount": 2 * L, "frequency": "Monthly", "stability": "Stable"}],
                        "scores": [{"score": 790}], "history": [{"lender": "A", "status": "Closed"}],
                        "obligations": [{"emi": 50_000}, {"emi": 30_000, "close_with_new": "Y"}],
                        "rate_pct": 12, "tenure_months": 60}, TH)
    assert strong["foir"] == pytest.approx(0.25) and strong["verdict"]["label"] == "Strong"
    assert strong["max_emi"] == pytest.approx(2 * L * 0.55 - 50_000)
    weak = bw.assess({"income": [{"amount": 1 * L, "frequency": "Monthly", "stability": "Stable"}],
                      "obligations": [{"emi": 75_000}], "no_score": True}, TH)
    assert weak["verdict"]["label"] == "Weak" and any(f["severity"] == "High" for f in weak["flags"])


# ---------------------------------------------------------------- taxonomy and non-fund limits

def test_taxonomy_and_defaults():
    tax = loans.taxonomy()
    assert set(tax) == {"Equity", "Debt"} and set(tax["Debt"]) == {"Fund based", "Non-fund based"}
    assert "Unsecured business loan" in tax["Debt"]["Fund based"]["Unsecured"]
    assert "Loan against property (LAP)" in tax["Debt"]["Fund based"]["Secured"]
    assert "Purchase invoice discounting (PID)" in tax["Debt"]["Fund based"]["Secured"]
    assert loans.classification({"product": "Cash Credit (CC)"})["label"] == "Debt · Fund based · Secured"
    assert loans.classification({"product": "Cash Credit (CC)", "secured": False})["secured"] is False  # override


def test_nonfund_only_case_with_utilisation():
    ws = Workspace()
    ws.set_case({"borrower": {"name": "Guarantee Co", "constitution": "Partnership firm"}, "security_opted": "yes",
                 "facilities": [{"id": "bg", "product": "Performance guarantee", "amount": 100 * L, "cash_margin_pct": 20,
                                 "limit_utilised": 60 * L},
                                {"id": "lc", "product": "Letter of credit", "amount": 50 * L, "cash_margin_pct": 10, "limit_utilised": 10 * L}]})
    t = ws.totals()
    assert t["fund"] == 0 and t["nonfund"] == 150 * L
    assert t["nfb_fund_equivalent"] == pytest.approx(80 * L * 0.50 + 45 * L * 0.20)  # default CCFs 50% and 20%
    st = ws.state()
    assert st["product"]["nfb_totals"]["utilisation"] == "47%" and st["product"]["nfb"][0]["utilisation"] == "60%"
    ws.report_pdf(ws.build()); ws.cam_pdf(ws.build()); ws.excel(ws.build())


def test_equity_only_case_is_not_exposure():
    ws = Workspace()
    ws.set_case({"borrower": {"name": "Startup Pvt Ltd", "constitution": "Private limited company"}, "security_opted": "no",
                 "equity": [{"id": "e1", "instrument": "Equity shares", "amount": 500 * L, "pre_money": 4500 * L}]})
    t = ws.totals()
    assert t["equity"] == 500 * L and t["total"] == 0 and t["credit_equivalent"] == 0
    s = ws.build()
    assert s["equity"]["rows"][0]["dilution_pct"] == pytest.approx(10.0)
    assert not any("No facilities" in f for f in s["flags"]["finding"])
    st = ws.state()
    assert st["repay"] is None and st["summary"]["steps"][5]["state"] == "Skipped (optional)"
    text = "".join(p.get_text() for p in fitz.open("pdf", ws.report_pdf(s)))
    assert "Equity requested" in text and "Equity shares" in text


# ---------------------------------------------------------------- optional security and KYC

def test_unsecured_case_skips_security_cleanly():
    ws = Workspace()
    ws.set_case({"borrower": {"name": "Trader", "constitution": "Sole proprietorship"}, "security_opted": "no",
                 "facilities": [{"id": "u", "product": "Unsecured business loan", "amount": 25 * L, "tenor_months": 36, "rate_pct": 15}]})
    st = ws.state()
    assert st["security"]["tiles"][3]["value"] == "n/a - unsecured"
    assert st["summary"]["steps"][1]["state"] == "Skipped (optional)" and st["summary"]["steps"][1]["note"] == "Not applicable (unsecured)"
    findings = list(ws.build()["flags"]["finding"])
    assert not any("No security offered" in f for f in findings)
    assert any("Unsecured request" in f for f in findings)
    text = "".join(p.get_text() for p in fitz.open("pdf", ws.report_pdf(ws.build())))
    assert "unsecured" in text.lower()


def test_security_mismatch_warnings():
    ws = Workspace()
    ws.set_case({"borrower": {"name": "X", "constitution": "Private limited company"}, "security_opted": "no",
                 "facilities": [{"id": "l", "product": "Loan against property (LAP)", "amount": 50 * L, "tenor_months": 120}]})
    assert any("Secured product(s) chosen but no security" in f for f in ws.build()["flags"]["finding"])
    ws.set_case({"borrower": {"name": "X", "constitution": "Private limited company"}, "security_opted": "yes",
                 "facilities": [{"id": "u", "product": "Unsecured business loan", "amount": 20 * L, "tenor_months": 36}],
                 "securities": [{"kind": "Fixed deposit", "value": 30 * L}]})
    assert any("Only unsecured products are chosen, yet security is offered" in f for f in ws.build()["flags"]["finding"])


def test_case_with_no_kyc_documents_is_informational():
    ws = Workspace()
    ws.set_case({"borrower": {"name": "Firm", "constitution": "Partnership firm"}})
    rows = ws.kyc_rows()
    assert rows and not any(r["resolved"] for r in rows)
    kyc_flags = [f for f in ws.build()["flags"].to_dict("records") if f["area"] == "KYC documents"]
    assert kyc_flags and all(f["severity"] == "Low" and "information only" in f["finding"] for f in kyc_flags)
    ws.case["kyc_status"] = {r["kind"]: "na" for r in rows}
    assert ws.kyc_missing() == [] and ws.build()["steps"][3]["state"] == "Complete"
    ws.report_pdf(ws.build())


# ---------------------------------------------------------------- old case files and empty reports

def test_old_format_case_file_opens():
    old = {"version": 1, "case": {
        "borrower": {"name": "Old Co Pvt Ltd", "constitution": "Private limited company", "msme_class": "Small"},
        "facilities": [{"id": "cc1", "product": "Cash Credit (CC)", "amount": 100 * L, "rate_pct": 10},
                       {"id": "bg1", "product": "Financial guarantee", "amount": 40 * L, "cash_margin_pct": 10}],
        "securities": [{"id": "s1", "kind": "Stock (inventory)", "value": 150 * L, "margin_pct": 25}],
        "client": {"obligations": [{"Lender": "SBI", "Product": "Term loan", "EMI ₹": 1 * L, "Outstanding ₹": 30 * L,
                                    "Security": "Secured", "Close with new loan": "N"}]},
        "dscr": {"growth_pct": 5, "tax_pct": 25, "wc_utilisation_pct": 80}}}
    ws = Workspace()
    ws.load_case(json.dumps(old).encode())
    c = ws.case
    assert c["security_opted"] == "yes" and c["equity"] == [] and c["kyc_status"] == {}
    assert c["facilities"][0]["secured"] is True and c["facilities"][1]["ccf_pct"] == 100.0
    assert c["borrower_profile"]["obligations"][0]["lender"] == "SBI"  # moved from the old client details
    assert ws.potential()["obligations"]["monthly"] == pytest.approx(1 * L)
    assert json.loads(ws.save_case())["version"] == 2


def test_reports_with_every_optional_section_empty():
    ws = Workspace()
    ws.set_case(clean_case({"borrower": {"name": "Only A Name", "constitution": "Trust / society"}}))
    s = ws.build()
    doc = fitz.open("pdf", ws.report_pdf(s))
    text = "".join(p.get_text() for p in doc)
    assert "3. Borrower profile & repayment potential" in [t for _, t, _ in doc.get_toc()]
    assert "not provided" in text.lower()
    assert fitz.open("pdf", ws.cam_pdf(s)).page_count >= 1
    assert ws.excel(s)[:2] == b"PK"
    st = ws.state()
    assert st["borrower"]["verdict"]["label"] == "Not assessed" and st["summary"]["steps"][0]["state"] == "Partial"
