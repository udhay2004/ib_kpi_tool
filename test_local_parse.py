"""Run: python -m pytest test_local_parse.py  — reading documents without any API."""

import io
import random
from datetime import date, timedelta

import fitz
import pandas as pd
import pytest

import local_parse as lp
from extractor import SourceDoc

NARR = [("NEFT CR-HDFC0000123-ACME TRADERS PVT LTD-INV 2231", "cr", 250000),
        ("UPI/412345678901/RAMESH KUMAR/OKAXIS/payment", "cr", 18000),
        ("BY CASH DEPOSIT - AMBERNATH BRANCH", "cr", 40000),
        ("RTGS-SBIN0001234-BHARAT PLASTICS LTD-PO 88", "dr", 300000),
        ("ACH D- BAJAJ FINANCE LTD-EMI 4471", "dr", 85000),
        ("ATM WDL/AMBERNATH/123", "dr", 20000),
        ("NEFT CR-ICIC0000999-SUNRISE FOODS LTD-INV 91", "cr", 175000)]


def make_txns(months=3, seed=1):
    random.seed(seed)
    bal, out, d = 500000.0, [], date(2024, 4, 1)
    end = date(2024, 4, 1) + timedelta(days=30 * months)
    while d < end:
        for narr, side, amt in random.sample(NARR, 3):
            amt = float(amt + random.randint(0, 99) * 100)
            bal += amt if side == "cr" else -amt
            out.append({"date": d, "narration": narr, "debit": amt if side == "dr" else 0.0,
                        "credit": amt if side == "cr" else 0.0, "balance": bal})
        d += timedelta(days=4)
    # one cheque bounce (charge + GST on charge = 1 event)
    out.append({"date": d - timedelta(days=1), "narration": "CHQ RETURN CHGS INSUFFICIENT FUNDS 000123", "debit": 590.0,
                "credit": 0.0, "balance": bal - 590})
    out.append({"date": d - timedelta(days=1), "narration": "GST ON CHQ RETURN CHGS", "debit": 106.2, "credit": 0.0,
                "balance": bal - 696.2})
    return out


def statement_df(txns):
    return pd.DataFrame([{"Date": t["date"].strftime("%d/%m/%y"), "Narration": t["narration"], "Chq./Ref.No.": "0000",
                          "Withdrawal Amt.": f"{t['debit']:,.2f}" if t["debit"] else "",
                          "Deposit Amt.": f"{t['credit']:,.2f}" if t["credit"] else "",
                          "Closing Balance": f"{t['balance']:,.2f}"} for t in txns])


def csv_bytes(txns):
    head = "HDFC BANK LTD\nAccount Name : DEMO POLYMERS PVT LTD\nAccount No : 50200012345678\nIFSC : HDFC0000123\n\n"
    return (head + statement_df(txns).to_csv(index=False)).encode()


def xlsx_bytes(txns):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["Statement of account"], ["Account No : XXXXXXXX5678"], []]).to_excel(xw, header=False, index=False)
        statement_df(txns).to_excel(xw, startrow=4, index=False)
    return buf.getvalue()


def pdf_statement(txns, ruled=True):
    doc = fitz.open()
    cols = [36, 90, 330, 400, 470, 545]  # Date | Narration | Withdrawal | Deposit | Balance
    rows_per_page = 30
    for start in range(0, len(txns), rows_per_page):
        page = doc.new_page(width=595, height=842)
        y = 60
        if start == 0:
            page.insert_text((36, 40), "AXIS BANK  Statement of Account  A/c No: 912020045671234  IFSC: UTIB0000456", fontsize=8)
            opening = txns[0]["balance"] - txns[0]["credit"] + txns[0]["debit"]
            page.insert_text((36, 52), f"Opening Balance {opening:,.2f}", fontsize=8)
        head = ["Date", "Narration", "Withdrawal", "Deposit", "Balance"]
        chunk = txns[start:start + rows_per_page]
        lines = [head] + [[t["date"].strftime("%d-%m-%Y"), t["narration"][:42], f"{t['debit']:,.2f}" if t["debit"] else "",
                           f"{t['credit']:,.2f}" if t["credit"] else "", f"{t['balance']:,.2f}"] for t in chunk]
        for row in lines:
            for i, cell in enumerate(row):
                page.insert_text((cols[i] + 2, y + 10), cell, fontsize=7)
            if ruled:
                page.draw_line((cols[0], y), (cols[-1], y))
            y += 14
        if ruled:
            page.draw_line((cols[0], y), (cols[-1], y))
            for x in cols:
                page.draw_line((x, 60), (x, y))
    return doc.tobytes()


def check_months(x, txns):
    months = {m["month"]: m for m in x["months"]}
    df = pd.DataFrame(txns)
    df["m"] = df["date"].map(lambda d: f"{d.year}-{d.month:02d}")
    for m, g in df.groupby("m"):
        assert months[m]["total_credits"] == pytest.approx(g["credit"].sum())
        assert months[m]["total_debits"] == pytest.approx(g["debit"].sum())
        assert months[m]["closing_balance"] == pytest.approx(g.iloc[-1]["balance"])
    assert sum(m["outward_cheque_or_emi_bounces"] for m in x["months"]) == 1
    assert sum(m["cash_deposits"] for m in x["months"]) > 0
    assert sum(m["emi_or_loan_repayments"] for m in x["months"]) > 0


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf_ruled", "pdf_plain"])
def test_bank_statement_formats(fmt):
    txns = make_txns()
    data, ext = {"csv": (csv_bytes(txns), ".csv"), "xlsx": (xlsx_bytes(txns), ".xlsx"),
                 "pdf_ruled": (pdf_statement(txns), ".pdf"), "pdf_plain": (pdf_statement(txns, ruled=False), ".pdf")}[fmt]
    x = lp.parse_bank(data, ext, f"statement{ext}")
    assert x is not None, fmt
    check_months(x, txns)
    names = [c["name"] for c in x["top_credit_counterparties"]]
    assert "Acme Traders Pvt Ltd" in names
    if fmt == "csv":
        assert x["bank_name"] == "HDFC Bank" and x["account_number_last4"] == "5678"
    if fmt.startswith("pdf"):
        assert x["bank_name"] == "Axis Bank"


def test_counterparty_names():
    assert lp.counterparty("NEFT CR-HDFC0000123-ACME TRADERS PVT LTD-INV 2231") == "Acme Traders Pvt Ltd"
    assert lp.counterparty("UPI/412345678901/RAMESH KUMAR/OKAXIS/payment") == "Ramesh Kumar"


def test_numbers_and_dates():
    assert lp.to_num("1,23,456.50") == 123456.5
    assert lp.to_num("(2,500)") == -2500
    assert lp.to_num("12,000.00 Dr") == -12000
    assert lp.to_date("05/04/24") == date(2024, 4, 5)
    assert lp.to_date("05-Apr-2024") == date(2024, 4, 5)


def schedule3_pdf():
    lines = [
        "DEMO POLYMERS PRIVATE LIMITED",
        "Balance Sheet as at 31st March, 2025 (All amounts in ₹ Lakhs)",
        "Particulars Note As at 31 March 2025 As at 31 March 2024",
        "EQUITY AND LIABILITIES",
        "Share capital 2 200.00 200.00",
        "Reserves and surplus 3 1,850.40 1,415.10",
        "Non-current liabilities",
        "Long-term borrowings 4 420.00 510.00",
        "Current liabilities",
        "Short-term borrowings 5 310.00 280.00",
        "Trade payables 6 540.25 470.80",
        "Total current liabilities 1,030.00 940.00",
        "Total 3,500.40 3,065.10",
        "ASSETS",
        "Non-current assets",
        "Property, plant and equipment 7 1,600.00 1,450.00",
        "Current assets",
        "Inventories 8 610.00 540.00",
        "Trade receivables 9 720.00 655.00",
        "Cash and cash equivalents 10 85.00 60.00",
        "Total current assets 1,540.00 1,390.00",
        "Total assets 3,500.40 3,065.10",
        "Statement of Profit and Loss for the year ended 31st March, 2025",
        "Revenue from operations 11 4,280.00 3,650.00",
        "Other income 12 22.00 18.00",
        "Cost of materials consumed 13 2,560.00 2,210.00",
        "Purchases of stock-in-trade 14 120.00 90.00",
        "Employee benefits expense 15 310.00 270.00",
        "Finance costs 16 68.00 72.00",
        "Depreciation and amortisation expense 7 55.00 50.00",
        "Other expenses 17 420.00 380.00",
        "Profit before tax 581.00 489.00",
        "Total tax expense 146.00 123.00",
        "Profit for the year 435.00 366.00",
        "Independent Auditor's Report",
    ]
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i, t in enumerate(lines):
        page.insert_text((36, 40 + i * 20), t.replace("₹", "Rs."), fontsize=8)
    return doc.tobytes()


def test_schedule3_financials():
    x = lp.parse_financials(schedule3_pdf(), ".pdf")
    assert x is not None
    cur, prev = x["years"]
    assert cur["fiscal_year"] == "FY2024-25" and prev["fiscal_year"] == "FY2023-24"
    L = 1e5
    assert cur["revenue_from_operations"] == pytest.approx(4280 * L)
    assert prev["revenue_from_operations"] == pytest.approx(3650 * L)
    assert cur["profit_after_tax"] == pytest.approx(435 * L)
    assert cur["long_term_borrowings"] == pytest.approx(420 * L)
    assert cur["cost_of_materials_and_purchases"] == pytest.approx(2680 * L)  # materials + purchases
    assert cur["net_worth"] == pytest.approx(2050.4 * L)
    assert cur["depreciation"] == pytest.approx(55 * L) and cur["audited"]


def gstr3b_pdf(month="April", year="2024-25", taxable=3_45_67_890.00):
    text = (f"Form GSTR-3B\n[See rule 61(5)]\nYear {year}\nPeriod {month}\n1. GSTIN 27AABCD1234E1Z5\n"
            "2(a). Legal name of the registered person DEMO POLYMERS PRIVATE LIMITED\n"
            "3.1 Details of Outward supplies and inward supplies liable to reverse charge\n"
            f"(a) Outward taxable supplies (other than zero rated, nil rated and exempted) {taxable:,.2f} 0.00 "
            f"{taxable * 0.09:,.2f} {taxable * 0.09:,.2f} 0.00\n"
            "(b) Outward taxable supplies (zero rated) 12,000.00 0.00 0.00 0.00 0.00\n"
            "(c) Other outward supplies (Nil rated, exempted) 0.00 0.00 0.00 0.00 0.00\n"
            "C. Net ITC Available (A) - (B) 1,20,000.00 25,00,000.00 25,00,000.00 0.00\n"
            "Date of ARN 18/05/2024\n")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(fitz.Rect(36, 36, 560, 800), text, fontsize=7)
    return doc.tobytes()


def test_gstr3b():
    pdf = fitz.open()
    for m in ("April", "May"):
        pdf.insert_pdf(fitz.open(stream=gstr3b_pdf(m), filetype="pdf"))
    x = lp.parse_gst(pdf.tobytes(), ".pdf")
    assert x["gstin"] == "27AABCD1234E1Z5"
    assert [p["period"] for p in x["periods"]] == ["2024-04", "2024-05"]
    p = x["periods"][0]
    assert p["taxable_turnover"] == pytest.approx(34567890.0)
    assert p["itc_claimed"] == pytest.approx(5120000.0)
    assert p["filing_date"] == "2024-05-18" and p["due_date"] == "2024-05-20"


def test_extract_entry_point_detects_type():
    doc = SourceDoc(doc_id="D01", filename="axis.pdf", data=pdf_statement(make_txns()))
    doc.doc_type = "financials"  # filed in the wrong section
    lp.extract(doc)
    assert doc.status == "extracted" and doc.doc_type == "bank_statement" and doc.warnings


def test_scanned_pdf_fails_cleanly():
    d = fitz.open()
    d.new_page()
    doc = SourceDoc(doc_id="D02", filename="scan.pdf", data=d.tobytes())
    lp.extract(doc)
    assert doc.status == "failed" and "API key" in doc.error
