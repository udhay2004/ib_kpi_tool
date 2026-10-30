"""Run: python -m pytest test_case.py"""

import json

import pytest

import kyc
import loans
import repayment as rp
import security as sec
from demo_data import demo_case, demo_kyc

L = 1e5


@pytest.mark.parametrize("method", list(rp.METHODS))
@pytest.mark.parametrize("freq", list(rp.FREQUENCIES))
def test_every_schedule_repays_exactly(method, freq):
    s = rp.schedule(100 * L, 10.5, 60, method, freq, moratorium_months=6, structured_pcts=[10, 20, 30, 40])
    assert s["principal"].sum() == pytest.approx(100 * L)
    assert s["closing"].iloc[-1] == pytest.approx(0, abs=1)
    assert (s.loc[s["month"] <= 6, "principal"] == 0).all()  # moratorium


def test_emi_is_constant():
    s = rp.schedule(100 * L, 12, 12, "EMI – equated instalments")
    assert s["instalment"].round(2).nunique() == 1
    assert s["instalment"].iloc[0] == pytest.approx(888_487.9, rel=1e-4)  # standard EMI formula


def test_dscr_falls_when_debt_added():
    base = rp.dscr_projection([], 600 * L, 68 * L, 95 * L, 0)
    more = rp.dscr_projection([rp.schedule(400 * L, 10, 60, "EMI – equated instalments")], 600 * L, 68 * L, 95 * L, 0)
    assert more["dscr"].min() < base["dscr"].min()


def test_catalogue_covers_fund_and_non_fund():
    cats = {c for c, _, _ in loans.all_products()}
    assert cats == {"Fund based", "Non-fund based"}
    assert len(loans.all_products()) >= 25
    for _, _, p in loans.all_products():
        spec = loans.product(p)
        assert all(f in loans.F for f in spec["fields"])


def test_wc_assessment_uses_mpbf_above_5cr():
    fin = {"revenue": 4280 * L, "growth": 0.1, "total_current_assets": 1540 * L, "total_current_liabilities": 1030 * L,
           "short_term_borrowings": 310 * L}
    a = loans.assess({"product": "Cash Credit (CC)", "amount": 600 * L}, fin)
    assert a["method"].startswith("MPBF") and a["eligible"] == pytest.approx(0.75 * 1540 * L - 720 * L)
    small = loans.assess({"product": "Cash Credit (CC)", "amount": 100 * L}, fin)
    assert small["method"] == "Turnover method" and small["ok"]


def test_nfb_exposure_net_of_margin():
    assert loans.exposure({"product": "Performance guarantee", "amount": 100 * L, "cash_margin_pct": 20}) == 80 * L
    assert loans.exposure({"product": "Cash Credit (CC)", "amount": 100 * L}) == 100 * L


def test_security_cover_ignores_guarantees():
    secs = [{"kind": "Fixed deposit", "value": 100 * L, "margin_pct": 10},
            {"kind": "Personal guarantee", "value": 500 * L, "margin_pct": 100}]
    assert sec.cover(secs, 90 * L)["cover"] == pytest.approx(1.0)


def test_kyc_guess_from_filename():
    assert kyc.guess_kind("Company_PAN.pdf", person=False) == "entity_pan"
    assert kyc.guess_kind("AOA signed.pdf", person=False) == "aoa"
    assert kyc.guess_kind("DirectorA aadhaar masked.jpg", person=True) == "aadhaar"
    assert kyc.guess_kind("company profile.pdf", person=False) == "other"  # 'pan' inside 'company' is not PAN


def test_kyc_checklist_on_demo():
    have = {d["doc_type"] for d in demo_kyc()}
    missing = [k for k in kyc.REQUIRED[kyc.family("Private limited company")] if k not in have]
    assert missing == ["address_proof"]


def test_credit_file_zip_contains_kyc(monkeypatch):
    import io
    import zipfile
    from steps import summary
    docs = [{"doc_id": "K01", "filename": "aoa.pdf", "doc_type": "aoa", "person": "", "data": b"%PDF"},
            {"doc_id": "K02", "filename": "pan.jpg", "doc_type": "pan", "person": "Director A", "data": b"img"},
            {"doc_id": "K03", "filename": "moa.pdf", "doc_type": "moa", "person": "", "data": None}]
    monkeypatch.setattr(summary.kyc_step, "kyc_docs", lambda: docs)
    names = zipfile.ZipFile(io.BytesIO(summary.credit_file_zip(b"xls", "Acme"))).namelist()
    assert names == ["Acme_credit_note.xlsx", "KYC/K01 - Articles of association - aoa.pdf",
                     "KYC/K02 - PAN card - Director A - pan.jpg"]


def test_case_json_roundtrip():
    from datetime import date
    case = demo_case()
    raw = json.dumps(case, default=lambda o: o.isoformat() if isinstance(o, date) else str(o))
    back = json.loads(raw)
    assert back["facilities"][1]["amount"] == case["facilities"][1]["amount"]
    assert back["securities"][0]["valuation_date"] == "2022-03-15"


def _demo_capacity(case, rate=None):
    import capacity
    import kpis
    from demo_data import demo_docs
    res = kpis.analyse(demo_docs())

    def build(r):
        return [rp.schedule(f["amount"], (rate if rate is not None else f["rate_pct"]) + r, f["tenor_months"],
                            "EMI – equated instalments", "Monthly", f.get("moratorium_months") or 0)
                for f in case["facilities"] if loans.product(f["product"])["repay"] == "amortising"]
    return capacity.assess(res, case["facilities"], build, case["dscr"])


def test_capacity_on_demo_is_comfortable_and_explained():
    cap = _demo_capacity(demo_case())
    assert cap["verdict"]["label"] == "Comfortable"
    titles = [n["title"] for n in cap["notes"]]
    assert titles[:2] == ["How they will repay", "Capacity to service the debt"]
    s = cap["stress"].set_index("Scenario")["Minimum DSCR"]
    assert s["Revenue −20%"] < s["Revenue −10%"] < s["Base case"]  # stress lowers DSCR
    assert s["Interest +2%"] < s["Base case"]
    assert 0 < cap["break_even_100"] and cap["break_even_125"] < cap["break_even_100"]


def test_capacity_flags_an_oversized_loan():
    case = demo_case()
    for f in case["facilities"]:
        if loans.product(f["product"])["repay"] == "amortising":
            f["amount"], f["tenor_months"] = 40 * 1e7, 36  # Rs 40 Cr over 3 years
    cap = _demo_capacity(case)
    assert cap["verdict"]["status"] == "critical" and cap["min_dscr"] < 1
