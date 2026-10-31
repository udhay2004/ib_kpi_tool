"""Run: python -m pytest test_credit_report.py"""

import io
import logging
import zipfile

import fitz
import pandas as pd
import pytest

logging.getLogger("streamlit").setLevel(logging.ERROR)

import credit_report as cr  # noqa: E402
import kpis  # noqa: E402
from common import init_state, reset_all, ss  # noqa: E402
from demo_data import demo_case, demo_docs, demo_kyc, demo_security_docs  # noqa: E402


def _pdf(pages=1, text="page", password=None) -> bytes:
    d = fitz.open()
    for i in range(pages):
        d.new_page().insert_text((72, 100), f"{text} {i + 1}", fontsize=18)
    if password:
        return d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=password, user_pw=password)
    return d.tobytes()


def _text(doc) -> str:
    """Page text with whitespace collapsed and ligatures expanded (the PDF uses 'fi' ligatures)."""
    return " ".join("".join(p.get_text(flags=0) for p in doc).replace(" ", " ").split())


def _png() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 200), 0)
    pix.clear_with(180)
    return pix.tobytes("png")


@pytest.fixture
def demo():
    init_state()
    reset_all()
    ss.case = demo_case()
    ss.demo, ss.static_kyc, ss.static_sec = demo_docs(), demo_kyc(), demo_security_docs()
    from steps import summary
    yield summary
    reset_all()


def _build(summary, kyc_list, other):
    from steps import kyc_step
    from steps.repayment_step import schedules
    s = summary.build()
    return s, cr.build_pdf(s, {"bank": "Test Bank", "prepared_by": "Analyst", "recommendation": "Recommended."},
                           kyc_list, kyc_step.missing(), other, schedules(), {"GSTIN": "27AABCD1234E1Z5"})


def test_report_has_every_section_and_annexures(demo):
    from steps import kyc_step
    kyc_list = kyc_step.kyc_docs()
    kyc_list[0].update(data=_pdf(2, "Certificate"))
    kyc_list[9].update(data=_png(), filename="aadhaar_A.png")
    s, pdf = _build(demo, kyc_list, {"Financial documents": [("D01", "Audited_FS.pdf", _pdf(3, "Balance sheet")),
                                                             ("D02", "bank.xlsx", b"PK\x03\x04")]})
    doc = fitz.open("pdf", pdf)
    toc = [t for _, t, _ in doc.get_toc()]
    for n, title in enumerate(["Company KYC", "Director KYC", "Financials", "GST", "Proposed plan", "Security", "Repayment",
                               "Banking conduct", "Income tax returns", "Overall assessment", "Risks",
                               "Compliance checks", "Suggested terms", "Other documents", "Basis of preparation"], 1):
        assert any(t.startswith(f"{n}. {title}") for t in toc), title
    assert "Annexures" in toc
    text = _text(doc)
    # every attached page is stamped: KYC inside its section, the rest as annexures
    assert text.count("Company KYC CK1 —") == 2 and text.count("Annexure A1 —") == 3 and "Director KYC DK2 —" in text
    pages = {t: p for _, t, p in doc.get_toc()}
    contents = doc[0].get_text("words")  # the page number sits at the right of its title on the cover
    assert any(w[4] == str(pages["3. Financials — last 2 years"]) and w[0] > cr.BODY.x1 - 40 for w in contents)
    assert pages["1. Company KYC"] < pages["CK1 Certificate of incorporation"] < pages["2. Director KYC"] \
        < pages["DK2 Aadhaar · Director A"] < pages["3. Financials — last 2 years"]
    assert "Balance sheet 3" in text and "Certificate 2" in text
    assert "Demo Polymers Pvt Ltd" in text and "Comfortable" in text and "Test Bank" in text
    assert "CRILC" in text and "CERSAI" in text  # exposure >= 5 Cr, hypothecated / mortgaged assets
    assert "Registered office address proof" in text  # missing KYC listed
    assert "included in the credit file ZIP" in text  # xlsx listed, not embedded
    for page in doc[:4]:  # tables stay inside the right margin
        assert max(b[2] for b in page.get_text("blocks")) <= cr.BODY.x1 + 3


def test_financials_keep_two_years_and_gst_twelve_months(demo):
    s = demo.build()
    fin = s["res"]["tables"]["financials"]
    old = fin.iloc[[0]].copy()
    old.index = ["FY2010-11"]
    s["res"]["tables"]["financials"] = pd.concat([old, fin])
    gst = s["res"]["tables"]["gst"]
    extra = gst.iloc[[0]].assign(period="2010-01")
    annual = gst.iloc[[0]].assign(period="2010-11", return_type="GSTR-9")
    s["res"]["tables"]["gst"] = pd.concat([extra, annual, gst])
    text = _text(fitz.open("pdf", cr.build_pdf(s, {}, [], [], {})))
    assert "FY2010-11" not in text and "2010-01" not in text and "GSTR-9" not in text
    recent, m = kpis.gst_last_12_months(s["res"]["tables"]["gst"])
    assert recent["period"].nunique() <= 12 and m["months_filed"] == recent["period"].nunique()


def test_locked_pdf_is_listed_not_embedded(demo):
    from steps import kyc_step
    kyc_list = kyc_step.kyc_docs()
    kyc_list[0].update(data=_pdf(1, "secret", password="ABCDE1234F"))
    _, pdf = _build(demo, kyc_list, {})
    text = _text(fitz.open("pdf", pdf))
    assert "password-protected" in text and "secret 1" not in text


def test_empty_case_still_builds():
    init_state()
    reset_all()
    from steps import summary
    s = summary.build()
    doc = fitz.open("pdf", cr.build_pdf(s, {}, [], [], {}))
    assert doc.page_count >= 3 and "No financial statements were read" in _text(doc)
    reset_all()


def test_zip_carries_the_pdf(monkeypatch):
    from steps import summary
    monkeypatch.setattr(summary.kyc_step, "kyc_docs", lambda: [])
    names = zipfile.ZipFile(io.BytesIO(summary.credit_file_zip(b"xls", "Acme", pdf=b"%PDF-1.7"))).namelist()
    assert names[:2] == ["Acme_credit_report.pdf", "Acme_credit_note.xlsx"]


def test_blank_cells_as_nan_do_not_break_the_report(demo):
    # pandas 3 stores blank text cells as NaN (a float), pandas 2 as None
    from steps import kyc_step
    s = demo.build()
    nan = float("nan")
    s["capacity"]["metrics"]["Status"] = s["capacity"]["metrics"]["Status"].astype(object).where(lambda c: c.notna(), nan)
    s["capacity"]["metrics"].loc[1, "Status"] = nan
    s["facilities"] = s["facilities"].astype({"Within eligibility": object})
    s["facilities"].loc[0, "Within eligibility"] = nan
    text = _text(fitz.open("pdf", cr.build_pdf(s, {}, kyc_step.kyc_docs(), [], {})))
    assert "nan" not in text.lower().split() and "Exceeds" not in text
    assert cr.status(nan) == "" and cr.status(None) == ""
