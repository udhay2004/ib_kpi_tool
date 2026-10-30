"""Run: python -m pytest test_credit_report.py"""

import io
import logging
import zipfile

import fitz
import pytest

logging.getLogger("streamlit").setLevel(logging.ERROR)

import credit_report as cr  # noqa: E402
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
    for n, title in enumerate(["Proposal at a glance", "Borrower profile", "Proposed facilities", "Assessment of limits",
                               "Financial analysis", "Banking conduct", "GST returns", "Income tax returns", "Security",
                               "Repayment capacity", "Risks", "Compliance checks", "Suggested terms", "KYC documents",
                               "Other documents", "Basis of preparation"], 1):
        assert any(t.startswith(f"{n}. {title}") for t in toc), title
    assert "Annexures" in toc
    text = _text(doc)
    # every annexure page is stamped; 2 + 1 + 3 annexure pages after the report
    assert text.count("Annexure A1 —") == 2 and text.count("Annexure B1 —") == 3 and "Annexure A10 —" in text
    assert "Balance sheet 3" in text and "Certificate 2" in text
    assert "Demo Polymers Pvt Ltd" in text and "Comfortable" in text and "Test Bank" in text
    assert "CRILC" in text and "CERSAI" in text  # exposure >= 5 Cr, hypothecated / mortgaged assets
    assert "Registered office address proof" in text  # missing KYC listed
    assert "included in the credit file ZIP" in text  # xlsx listed, not embedded
    for page in doc[:4]:  # tables stay inside the right margin
        assert max(b[2] for b in page.get_text("blocks")) <= cr.BODY.x1 + 3


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
