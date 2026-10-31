"""Credit appraisal memorandum (CAM) as one PDF for the bank: the full write-up, with the client's KYC, financial
and security documents merged in as annexures.

Section order follows the usual Indian bank CAM / credit proposal note: proposal at a glance, borrower and
promoters, facilities, assessment of limits, financial / banking / GST analysis, security, repayment capacity,
risks, compliance checks for the bank, suggested terms, document index, declaration. There is no single RBI
format - every bank has its own - so the bank's own note format and sanction terms prevail.

Pure module (no Streamlit): build_pdf() takes the case summary from steps.summary.build() plus the documents.
"""

import io
import re
from datetime import datetime
from html import escape

import fitz
import pandas as pd

import kpis
import kyc
import loans
import security as sec

A4 = fitz.paper_rect("a4")
BODY = A4 + (42, 50, -42, -46)
LAKH = 1e5
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "tif", "tiff", "bmp", "gif"}

# No background colours: MuPDF's Story redraws backgrounds of elements that continue across a page at the wrong
# place on later pages, so emphasis is by borders and coloured text only.
CSS = """
* {font-family: sans-serif;}
body {font-size: 9pt; color: #1d2433; line-height: 1.35;}
h1 {font-size: 14pt; color: #1f3864; margin: 16pt 0 5pt 0; padding-bottom: 2pt; border-bottom: 1.2pt solid #1f3864;}
h2 {font-size: 10.5pt; color: #1f3864; margin: 9pt 0 3pt 0;}
p {margin: 2pt 0 4pt 0;}
table {border-spacing: 0; margin: 2pt 0 6pt 0;}
th {color: #1f3864; font-size: 7.6pt; font-weight: bold; text-align: left; padding: 2.5pt 3pt;
    border-top: 1pt solid #1f3864; border-bottom: 1pt solid #1f3864;}
td {font-size: 7.8pt; padding: 2.2pt 3pt; border-bottom: 0.5pt solid #c9cfd9; vertical-align: top;}
td.n, th.n {text-align: right;}
td.sp {border: none; padding: 0 3pt; font-size: 1pt; line-height: 0;}
tr.High td {color: #9b1c1c;}
.good {color: #1a7f37; font-weight: bold;} .warning {color: #9a6700; font-weight: bold;}
.critical {color: #c62828; font-weight: bold;}
.box {border: 0.8pt solid #1f3864; border-left: 3pt solid #1f3864; padding: 5pt 7pt; margin: 4pt 0 6pt 0;}
.muted {color: #667085; font-size: 7.8pt;}
ul {margin: 2pt 0 4pt 0; padding-left: 13pt;} li {margin-bottom: 2pt;}
.cover-title {font-size: 22pt; font-weight: bold; color: #1f3864; margin: 120pt 0 4pt 0;}
.cover-sub {font-size: 12pt; color: #475467; margin-bottom: 26pt;}
.cover-name {font-size: 17pt; font-weight: bold; margin-bottom: 18pt;}
.toc td {font-size: 9.5pt; padding: 3.5pt 2pt;}
"""

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}
STATUS_WORD = {"good": "Comfortable", "warning": "Watch", "critical": "Concern"}

FIN_LINES = [  # extracted line, label - in Schedule III order
    ("revenue_from_operations", "Revenue from operations"), ("other_income", "Other income"),
    ("cost_of_materials_and_purchases", "Cost of materials & purchases"), ("employee_cost", "Employee cost"),
    ("other_expenses", "Other expenses"), ("depreciation", "Depreciation"), ("finance_cost", "Finance cost"),
    ("profit_before_tax", "Profit before tax"), ("tax_expense", "Tax"), ("profit_after_tax", "Profit after tax"),
    ("share_capital", "Share capital"), ("reserves_and_surplus", "Reserves & surplus"), ("net_worth", "Net worth"),
    ("unsecured_loans_from_promoters", "Unsecured loans from promoters"),
    ("long_term_borrowings", "Long-term borrowings"), ("short_term_borrowings", "Short-term borrowings"),
    ("current_maturities_of_long_term_debt", "Current maturities of LTD"), ("trade_payables", "Trade payables"),
    ("total_current_liabilities", "Total current liabilities"), ("fixed_assets_net", "Fixed assets (net)"),
    ("intangible_assets", "Intangible assets"), ("inventory", "Inventory"), ("trade_receivables", "Trade receivables"),
    ("cash_and_bank", "Cash & bank"), ("total_current_assets", "Total current assets"), ("total_assets", "Total assets"),
    ("cash_from_operations", "Cash from operations"), ("capex", "Capex"),
]

BANK_KPI_LABELS = [  # key, label, unit
    ("period", "Period", "text"), ("months_covered", "Months covered", "count"),
    ("total_credits", "Total credits", "INR"), ("business_credits", "Business credits (excl. own / loan)", "INR"),
    ("avg_monthly_credits", "Average monthly credits", "INR"), ("avg_monthly_debits", "Average monthly debits", "INR"),
    ("abb", "Average bank balance", "INR"), ("lowest_balance", "Lowest balance", "INR"),
    ("od_cc_limit", "OD / CC limit", "INR"), ("limit_utilisation", "Limit utilisation", "%"),
    ("monthly_emi_outflow", "Monthly EMI outflow", "INR"), ("abb_to_emi", "ABB / EMI", "x"),
    ("cash_deposit_share", "Cash deposits share of credits", "%"),
    ("related_transfer_share", "Own / related transfers share", "%"),
    ("top5_counterparty_share", "Top-5 payers share", "%"), ("inward_returns", "Inward cheque returns", "count"),
    ("outward_bounces", "Outward cheque / EMI bounces", "count"),
    ("credit_volatility_cv", "Monthly credit volatility (CV)", "x"),
]

GST_KPI_LABELS = [
    ("months_filed", "Months filed", "count"), ("gst_turnover", "GST turnover", "INR"),
    ("avg_monthly_turnover", "Average monthly turnover", "INR"), ("tax_paid_cash", "Tax paid in cash", "INR"),
    ("itc_claimed", "ITC claimed", "INR"), ("itc_available_2b", "ITC available (GSTR-2B)", "INR"),
    ("itc_excess_over_2b", "ITC claimed over 2B", "%"), ("gstr1_vs_3b_gap", "GSTR-1 vs 3B gap", "%"),
    ("late_filings", "Late filings", "count"), ("top5_customer_share", "Top-5 customers share", "%"),
]


# ---------------------------------------------------------------- formatting

def _na(v) -> bool:
    try:
        return v is None or pd.isna(v)
    except (TypeError, ValueError):
        return False


def inr(v) -> str:
    if _na(v):
        return "—"
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def lakh(v) -> str:
    return "—" if _na(v) else f"{v / LAKH:,.2f}"


def val(v, unit: str) -> str:
    if _na(v):
        return "—"
    if unit in ("INR", "lakh"):
        return lakh(v)
    if unit == "%":
        return f"{v:.1%}"
    if unit == "x":
        return f"{v:.2f}x"
    if unit in ("days", "count"):
        return f"{v:,.0f}"
    return str(v)


def e(v) -> str:
    """Escaped text; long names may break after _ or / so they don't stretch a column."""
    return "—" if _na(v) or v == "" else re.sub(r"([_/])", "\\1\u200b", escape(str(v)))


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>|&[a-z]+;", "x", html)


def _spacer(fractions: list) -> str:
    """MuPDF sizes table columns to their content and ignores CSS widths, so an invisible row of non-breaking
    spaces fixes each column's width (a 1pt non-breaking space is ~0.28pt wide; 6pt is the cell's side padding)."""
    return "<tr>" + "".join(f'<td class="sp">{"&nbsp;" * max(int((BODY.width * f - 6.5) / 0.2835), 1)}</td>'
                            for f in fractions) + "</tr>"


def table(head: list, rows: list, num_cols=(), row_class=None, widths=None, head_row=True) -> str:
    """rows: lists of already-escaped cell HTML. num_cols: indexes right-aligned. row_class: one class per row.
    widths: column fractions; by default from the length of the content."""
    if not rows:
        return '<p class="muted">Nothing to show.</p>'
    if widths is None:
        cols = list(zip(*([head] + rows))) if head_row else list(zip(*rows))
        w = [min(max([len(_plain(str(c))) for c in col] + [5]), 45) for col in cols]
        widths = [x / sum(w) for x in w]
    widths = [float(x.rstrip("%")) / 100 if isinstance(x, str) else x for x in widths]
    th ="<tr>" + "".join(f'<th{" class=n" if i in num_cols else ""}>{h}</th>' for i, h in enumerate(head)) + "</tr>"         if head_row else ""
    body = ""
    for r, row in enumerate(rows):
        cls = f' class="{row_class[r]}"' if row_class and row_class[r] else ""
        body += f"<tr{cls}>" + "".join(f'<td{" class=n" if i in num_cols else ""}>{c}</td>'
                                       for i, c in enumerate(row)) + "</tr>"
    return f"<table>{th}{body}{_spacer(widths)}</table>"


def kv_table(pairs: list) -> str:
    return table([], [[f'<b>{escape(k)}</b>', v] for k, v in pairs], widths=[0.34, 0.66], head_row=False,
                 row_class=["kv"] * len(pairs))


def status(st_: str | None, text: str | None = None) -> str:
    if not st_:
        return e(text)
    return f'<span class="{st_}">{escape(text or STATUS_WORD.get(st_, st_))}</span>'


# ---------------------------------------------------------------- sections

def _cover(s, info, total_pages_hint=""):
    b, t = s["case"]["borrower"], s["totals"]
    rows = [("Borrower", e(b["name"] or "Unnamed borrower")), ("Constitution", e(b["constitution"])),
            ("Total proposed", e(inr(t["total"])) + f' &nbsp;<span class="muted">(fund {e(inr(t["fund"]))} · '
                                                     f'non-fund {e(inr(t["nonfund"]))})</span>'),
            ("Submitted to", e(", ".join(x for x in (info.get("bank"), info.get("branch")) if x) or "—")),
            ("Prepared by", e(info.get("prepared_by"))), ("Reference", e(info.get("reference"))),
            ("Date", e(info.get("date") or datetime.now().strftime("%d %b %Y")))]
    return (f'<div class="cover-title">Credit Appraisal Memorandum</div>'
            f'<div class="cover-sub">Credit proposal note with supporting documents</div>'
            f'<div class="cover-name">{e(b["name"] or "Unnamed borrower")}</div>{kv_table(rows)}'
            f'<p class="muted" style="margin-top:30pt">Prepared from documents supplied by the borrower. Figures were read '
            f'from those documents and should be checked against the originals in the annexures. KYC documents are '
            f'attached as received and have not been verified by the preparer; verification, credit bureau and '
            f'regulatory checks are for the bank.</p>')


def _toc(entries):
    return '<h1>Contents</h1><div class="toc">' + table([], [[escape(t), str(p)] for t, p in entries], num_cols=(1,),
                                                          widths=[0.9, 0.1], head_row=False) + "</div>"


def _summary(s, info):
    case, b, t, cov, dt, cap = s["case"], s["case"]["borrower"], s["totals"], s["cover"], s["dscr"], s["capacity"]
    fl = s["flags"]
    n = {k: int((fl["severity"] == k).sum()) if not fl.empty else 0 for k in ("High", "Medium", "Low")}
    th = kpis.THRESHOLDS
    fk = s["res"]["fin_kpis"] if s["res"] is not None else pd.DataFrame()
    fy = s["fin"].get("fy") if s["fin"] else None

    def kv(key):
        if not fy or fk.empty or key not in fk.index or fy not in fk.columns:
            return None
        v = fk.at[key, fy]
        return None if _na(v) else v
    mn = None if dt is None or dt.empty else float(dt["dscr"].min())
    metrics = [
        ("Total proposed", e(inr(t["total"])), "", None),
        ("Net exposure (after cash margins)", e(inr(t["net_exposure"])), "", None),
        ("Existing exposure", e(inr(b.get("existing_exposure"))), "", None),
        (f"Revenue {fy or ''}", e(inr(kv("revenue"))), "", None),
        ("EBITDA margin", e(val(kv("ebitda_margin"), "%")), "", None),
        ("Current ratio", e(val(kv("current_ratio"), "x")), f"≥ {th['current_ratio_benchmark']:.2f}x",
         None if kv("current_ratio") is None else "good" if kv("current_ratio") >= th["current_ratio_benchmark"] else "critical"),
        ("TOL / TNW", e(val(kv("tol_tnw"), "x")), f"≤ {th['tol_tnw_max']:.1f}x",
         None if kv("tol_tnw") is None else "good" if kv("tol_tnw") <= th["tol_tnw_max"] else "critical"),
        ("Minimum DSCR on proposal", "—" if mn is None else f"{mn:.2f}x", f"≥ {th['dscr_min']:.2f}x",
         None if mn is None else "good" if mn >= th["dscr_min"] else "critical"),
        ("Security cover (realisable)", "—" if cov["cover"] is None else f"{cov['cover']:.2f}x", "≥ 1.25x",
         None if cov["cover"] is None else "good" if cov["cover"] >= 1.25 else "critical"),
        ("Repayment capacity", e(cap["verdict"]["label"]) if cap else "Not assessed", "",
         cap["verdict"]["status"] if cap else None),
    ]
    rows = [[escape(a), f'<b>{v}</b>', escape(bm), status(st_) if st_ else ""] for a, v, bm, st_ in metrics]
    out = "<h1>1. Proposal at a glance</h1>"
    out += table(["Measure", "Value", "Benchmark", "Assessment"], rows, widths=["40%", "22%", "18%", "20%"])
    if not s["facilities"].empty:
        out += "<h2>Facilities requested</h2>"
        out += table(["Facility", "Type", "Amount (₹ lakh)", "Tenor (m)", "Rate %", "Purpose"],
                     [[e(r["Facility"]), e(r["Category"]), lakh(r["Amount (₹)"]),
                       e(None if _na(r["Tenor (months)"]) else int(r["Tenor (months)"])), e(r["Rate %"]), e(r["Purpose"])]
                      for _, r in s["facilities"].iterrows()], num_cols=(2, 3, 4))
    out += "<h2>Overall view</h2>"
    if cap:
        out += (f'<div class="box">{status(cap["verdict"]["status"], "Repayment capacity: " + cap["verdict"]["label"])}'
                f' — {e(cap["verdict"]["meaning"])}</div>')
    out += (f"<p>Findings across the file: <span class=critical>{n['High']} high</span>, "
            f"<span class=warning>{n['Medium']} medium</span>, {n['Low']} low (section 11).</p>")
    highs = fl[fl["severity"] == "High"] if not fl.empty else fl
    if not highs.empty:
        out += "<ul>" + "".join(f"<li>{e(r['area'])}: {e(r['finding'])}</li>" for _, r in highs.iterrows()) + "</ul>"
    if info.get("recommendation"):
        out += "<h2>Preparer's recommendation</h2><p>" + e(info["recommendation"]).replace("\n", "<br>") + "</p>"
    return out


def _borrower(s, people, gst_meta):
    b = s["case"]["borrower"]
    pairs = [("Name", e(b["name"])), ("Constitution", e(b["constitution"])), ("Industry / activity", e(b["industry"])),
             ("MSME classification (Udyam)", e(b["msme_class"])), ("Years in business", e(b["years_in_business"])),
             ("Existing bank exposure", e(inr(b.get("existing_exposure"))))]
    for k, v in gst_meta.items():
        pairs.append((k, e(v)))
    out = "<h1>2. Borrower profile</h1>" + kv_table(pairs)
    out += "<h2>Promoters, directors, partners and guarantors</h2>"
    out += table(["Name", "Role / documents on file"], [[e(p), e(r)] for p, r in people.items()]) if people else \
        '<p class="muted">No individuals named on the KYC documents or guarantees.</p>'
    return out


def _proposal(s):
    t, fac = s["totals"], s["facilities"]
    out = "<h1>3. Proposed facilities and terms</h1>"
    rows = []
    for i, f in enumerate(s["case"]["facilities"]):
        p = loans.product(f["product"])
        r = fac.iloc[i] if i < len(fac) else None
        terms = []
        for key, label in (("tenor_months", "Tenor {} months"), ("moratorium_months", "Moratorium {} months"),
                           ("cash_margin_pct", "Cash margin {}%"), ("beneficiary", "Beneficiary: {}"),
                           ("lc_kind", "LC type: {}"), ("usance_days", "Usance {} days"),
                           ("project_cost", None)):
            v = f.get(key)
            if v in (None, "", 0, 0.0):
                continue
            terms.append(f"Project cost {inr(v)}" if key == "project_cost" else label.format(
                int(v) if isinstance(v, float) and v.is_integer() else v))
        rows.append([e(f["product"]), e(p["category"]), lakh(f.get("amount")), e(f.get("rate_pct")),
                     e(r["Repayment"] if r is not None else ""), e("; ".join(terms)), e(f.get("purpose"))])
    out += table(["Facility", "Type", "Amount (₹ lakh)", "Rate %", "Repayment", "Terms", "Purpose"], rows,
                 num_cols=(2, 3))
    out += kv_table([("Fund based", e(inr(t["fund"]))), ("Non-fund based", e(inr(t["nonfund"]))),
                     ("Total proposed", f"<b>{e(inr(t['total']))}</b>"),
                     ("Net exposure (after cash margins)", e(inr(t["net_exposure"]))),
                     ("Total with existing exposure", e(inr(t["total"] + (s["case"]["borrower"].get("existing_exposure") or 0))))])
    return out


def _assessment(s):
    fac = s["facilities"]
    out = "<h1>4. Assessment of limits</h1>"
    if fac.empty:
        return out + '<p class="muted">No facilities.</p>'
    rows, cls = [], []
    for _, r in fac.iterrows():
        ok = r["Within eligibility"]
        rows.append([e(r["Facility"]), e(r["Assessment"]), lakh(r["Amount (₹)"]), lakh(r["Eligible (₹)"]),
                     status(None if ok is None else "good" if ok == "Yes" else "critical",
                            "—" if ok is None else "Within" if ok == "Yes" else "Exceeds")])
        cls.append("High" if ok == "No" else None)
    out += table(["Facility", "Method", "Requested (₹ lakh)", "Assessed (₹ lakh)", "Result"], rows, num_cols=(2, 3),
                 row_class=cls)
    out += ('<p class="muted">Working capital: turnover method (Nayak Committee, 20% of projected turnover) for limits up '
            'to ₹5 Cr, MPBF second method of lending above that. Term loans: project cost less promoter margin. '
            'Non-fund limits: contract pipeline / purchase cycle.</p>')
    return out


def _financials(s):
    res = s["res"]
    out = "<h1>5. Financial analysis</h1>"
    if res is None:
        return out + '<p class="critical">No financial statements were read.</p>'
    fin = res["tables"]["financials"]
    if not fin.empty:
        years = list(fin.index)
        out += "<h2>Summary financials (₹ lakh)</h2>"
        rows = [["Audited"] + [e("Yes" if fin.at[y, "audited"] else "No") if "audited" in fin.columns else "—"
                               for y in years]]
        for key, label in FIN_LINES:
            if key in fin.columns and fin[key].notna().any():
                rows.append([escape(label)] + [lakh(fin.at[y, key]) for y in years])
        out += table(["Line item"] + [escape(str(y)) for y in years], rows, num_cols=tuple(range(1, len(years) + 1)))
    fk = res["fin_kpis"]
    if not fk.empty:
        years = [c for c in fk.columns if c not in ("KPI", "unit", "formula")]
        out += "<h2>Key ratios</h2>"
        out += table(["Ratio"] + [escape(y) for y in years] + ["Basis"],
                     [[e(r["KPI"])] + [e(val(r[y], r["unit"])) for y in years] + [f'<span class="muted">{e(r["formula"])}</span>']
                      for _, r in fk.iterrows()], num_cols=tuple(range(1, len(years) + 1)))
        out += '<p class="muted">Amounts in the ratio table are ₹ lakh.</p>'
    return out


def _banking(s):
    res = s["res"]
    out = "<h1>6. Banking conduct</h1>"
    if res is None or res["bank_kpis"].empty:
        return out + '<p class="muted">No bank statements were read.</p>'
    bk = res["bank_kpis"]
    accts = [str(a) for a in bk["account"]]
    out += table(["Measure (₹ lakh where amount)"] + [e(a) for a in accts],
                 [[escape(label)] + [e(val(r.get(k), unit)) for _, r in bk.iterrows()]
                  for k, label, unit in BANK_KPI_LABELS if k in bk.columns], num_cols=tuple(range(1, len(accts) + 1)))
    bm = res["tables"]["bank"]
    if not bm.empty:
        out += "<h2>Month-wise summary (₹ lakh)</h2>"
        cols = [("account", "Account", "text"), ("month", "Month", "text"), ("total_credits", "Credits", "INR"),
                ("total_debits", "Debits", "INR"), ("closing_balance", "Closing bal.", "INR"),
                ("min_balance", "Min bal.", "INR"), ("cash_deposits", "Cash dep.", "INR"),
                ("emi_or_loan_repayments", "EMI", "INR"), ("inward_cheque_returns", "Inward ret.", "count"),
                ("outward_cheque_or_emi_bounces", "Bounces", "count")]
        cols = [c for c in cols if c[0] in bm.columns]
        out += table([c[1] for c in cols], [[e(val(r[k], u)) for k, _, u in cols] for _, r in bm.iterrows()],
                     num_cols=tuple(i for i, c in enumerate(cols) if c[2] != "text"))
    cp = res["tables"].get("counterparties")
    if cp is not None and not cp.empty:
        cr = cp[cp["side"] == "credit"] if "side" in cp.columns else cp
        if not cr.empty:
            out += "<h2>Largest payers into the account</h2>"
            out += table(["Counterparty", "Amount (₹ lakh)", "Transactions"],
                         [[e(r["name"]), lakh(r["amount"]), e(r.get("count"))] for _, r in cr.head(10).iterrows()],
                         num_cols=(1, 2))
    return out


def _gst(s):
    res = s["res"]
    out = "<h1>7. GST returns</h1>"
    if res is None or res["gst_kpis"].empty:
        return out + '<p class="muted">No GST returns were read.</p>'
    gk = res["gst_kpis"]
    out += table(["Measure (₹ lakh where amount)"] + [e(y) for y in gk["fiscal_year"]],
                 [[escape(label)] + [e(val(r.get(k), unit)) for _, r in gk.iterrows()]
                  for k, label, unit in GST_KPI_LABELS if k in gk.columns], num_cols=tuple(range(1, len(gk) + 1)))
    g = res["tables"]["gst"]
    if not g.empty:
        out += "<h2>Return-wise summary (₹ lakh)</h2>"
        cols = [("period", "Period", "text"), ("return_type", "Return", "text"), ("taxable_turnover", "Taxable value", "INR"),
                ("export_turnover", "Exports", "INR"), ("tax_paid_cash", "Tax in cash", "INR"),
                ("itc_claimed", "ITC claimed", "INR"), ("filing_date", "Filed", "text"), ("due_date", "Due", "text")]
        cols = [c for c in cols if c[0] in g.columns]
        out += table([c[1] for c in cols], [[e(val(r[k], u)) for k, _, u in cols] for _, r in g.iterrows()],
                     num_cols=tuple(i for i, c in enumerate(cols) if c[2] != "text"))
    cu = res["tables"].get("customers")
    if cu is not None and not cu.empty:
        out += "<h2>Largest customers (GST)</h2>"
        out += table(["Customer / GSTIN", "Taxable value (₹ lakh)"],
                     [[e(r["name_or_gstin"]), lakh(r["taxable_value"])] for _, r in cu.head(10).iterrows()], num_cols=(1,))
    return out


def _itr(s):
    res = s["res"]
    itr = res["tables"]["itr"] if res is not None else pd.DataFrame()
    out = "<h1>8. Income tax returns of promoters / guarantors</h1>"
    if itr.empty:
        return out + '<p class="muted">No ITRs were read.</p>'
    cols = [("assessee_name", "Assessee", "text"), ("assessment_year", "AY", "text"), ("itr_form", "Form", "text"),
            ("gross_total_income", "Gross income", "INR"), ("total_income", "Taxable income", "INR"),
            ("tax_payable", "Tax", "INR"), ("filing_date", "Filed", "text")]
    cols = [c for c in cols if c[0] in itr.columns]
    return out + table([c[1] + (" (₹ lakh)" if c[2] == "INR" else "") for c in cols],
                       [[e(val(r[k], u)) for k, _, u in cols] for _, r in itr.iterrows()],
                       num_cols=tuple(i for i, c in enumerate(cols) if c[2] != "text"))


def _security(s):
    secs, cov = s["case"]["securities"], s["cover"]
    out = "<h1>9. Security</h1>"
    if not secs:
        return out + '<p class="critical">No security offered.</p>'
    rows = []
    for x in secs:
        cls_, charge, _, _ = sec.SECURITY_TYPES[x["kind"]]
        vd = x.get("valuation_date")
        rows.append([e(x["kind"]), f"{e(cls_)}<br>{e(charge)}",
                     e(x.get("description")) + f'<br><span class="muted">Owner: {e(x.get("owner"))}'
                     + (" (third party)" if x.get("owner_is_third_party") else "") + "</span>",
                     "—" if cls_ == "Guarantee" else lakh(x.get("value")), e(x.get("margin_pct")),
                     "—" if cls_ == "Guarantee" else lakh(sec.realisable(x)),
                     e(vd.strftime("%d %b %Y") if hasattr(vd, "strftime") else vd) +
                     (f"<br>{e(x.get('valuer'))}" if x.get("valuer") else "")])
    out += table(["Security", "Class / charge", "Description", "Value (₹ lakh)", "Margin %", "Realisable (₹ lakh)",
                  "Valuation"], rows, num_cols=(3, 4, 5), widths=[0.17, 0.16, 0.25, 0.1, 0.08, 0.11, 0.13])
    out += kv_table([("Market value of tangible security", e(inr(cov["market_value"]))),
                     ("Realisable value after margins", e(inr(cov["realisable_value"]))),
                     ("Security cover on net exposure", "—" if cov["cover"] is None else f"<b>{cov['cover']:.2f}x</b>"),
                     ("Collateral cover", "—" if cov.get("collateral_cover") is None else f"{cov['collateral_cover']:.2f}x"),
                     ("Guarantees", e(", ".join(f"{g['kind']} — {g.get('owner') or ''}" for g in cov.get("guarantees", []))))])
    out += "<h2>Documents for creating the charge</h2>"
    out += table(["Security", "Documents to collect"],
                 [[e(k), e(sec.SECURITY_TYPES[k][3])] for k in dict.fromkeys(x["kind"] for x in secs)])
    return out


def _repayment(s, schedules):
    cap, dt = s["capacity"], s["dscr"]
    out = "<h1>10. Repayment capacity</h1>"
    if cap is None:
        return out + '<p class="muted">Not assessed — no facilities.</p>'
    out += (f'<div class="box">{status(cap["verdict"]["status"], cap["verdict"]["label"])} — '
            f'{e(cap["verdict"]["meaning"])}</div>')
    out += "<h2>Observations</h2><ul>" + "".join(
        f"<li><b>{e(n['title'])}.</b> {e(n['text'])}</li>" for n in cap["notes"]) + "</ul>"
    m = cap["metrics"]
    out += "<h2>Measures</h2>" + table(
        ["Measure", "Value", "Benchmark / note", "Assessment"],
        [[e(r["Measure"]), e(r["Value"]), e(r["Benchmark / note"]), status(r.get("Status")) if r.get("Status") else ""]
         for _, r in m.iterrows()])
    stv = cap["stress"]
    out += "<h2>Stress tests</h2>" + table(
        ["Scenario", "Minimum DSCR", "Average DSCR", "Assessment"],
        [[e(r["Scenario"]), e(val(r["Minimum DSCR"], "x")), e(val(r["Average DSCR"], "x")),
          status(r.get("Status")) if r.get("Status") else ""] for _, r in stv.iterrows()], num_cols=(1, 2))
    if dt is not None and not dt.empty:
        out += "<h2>Projected debt service coverage (₹ lakh)</h2>" + table(
            ["Year", "Cash accrual", "Available for debt service", "New interest", "New principal", "Existing debt service",
             "Total debt service", "DSCR"],
            [[e(int(r["year"])), lakh(r["cash_accrual"]), lakh(r["available_for_debt_service"]), lakh(r["new_interest"]),
              lakh(r["new_principal"]), lakh(r["existing_debt_service"]), lakh(r["total_debt_service"]),
              f'<b>{r["dscr"]:.2f}x</b>'] for _, r in dt.iterrows()],
            num_cols=tuple(range(8)), row_class=["High" if r["dscr"] < kpis.THRESHOLDS["dscr_min"] else None
                                                for _, r in dt.iterrows()])
    names = {f["id"]: f["product"] for f in s["case"]["facilities"]}
    for fid, sc in (schedules or {}).items():
        if sc is None or sc.empty:
            continue
        y = sc.groupby("year").agg(opening=("opening", "first"), interest=("interest", "sum"),
                                   principal=("principal", "sum"), closing=("closing", "last")).reset_index()
        out += f"<h2>Repayment schedule — {e(names.get(fid, fid))} (₹ lakh, by year)</h2>" + table(
            ["Year", "Opening", "Interest", "Principal", "Closing"],
            [[e(int(r["year"])), lakh(r["opening"]), lakh(r["interest"]), lakh(r["principal"]), lakh(r["closing"])]
             for _, r in y.iterrows()], num_cols=(0, 1, 2, 3, 4))
    return out


def _risks(s):
    fl = s["flags"]
    out = "<h1>11. Risks, observations and mitigants</h1>"
    if fl.empty:
        return out + "<p>No findings.</p>"
    fl = fl.sort_values("severity", key=lambda c: c.map(SEV_ORDER))
    return out + table(["Severity", "Area", "Finding", "Source", "Mitigant / next step"],
                       [[status({"High": "critical", "Medium": "warning"}.get(r["severity"]), r["severity"]), e(r["area"]),
                         e(r["finding"]), e(r["evidence"]), e(r["next_step"])]
                        for _, r in fl.iterrows()],
                       widths=["9%", "14%", "38%", "14%", "25%"])


def compliance_checks(s) -> list:
    """(check, why) the bank still has to do. Not done by this tool."""
    case, t = s["case"], s["totals"]
    b = case["borrower"]
    agg = t["total"] + (b.get("existing_exposure") or 0)
    is_company = kyc.family(b["constitution"]) in ("company", "llp")
    kinds = {x["kind"] for x in case["securities"]}
    products = " ".join(f["product"].lower() for f in case["facilities"])
    checks = [
        ("Credit bureau reports (CIBIL / Experian / Equifax / CRIF) — borrower, promoters and guarantors",
         "Repayment history and existing obligations; reports from two bureaus are commonly required above ₹1 Cr"),
        ("RBI list of wilful defaulters and suit-filed accounts", "Borrower, directors / partners and guarantors"),
        ("KYC verification (CKYC search, PAN and Aadhaar validation, officially valid documents)",
         "KYC is attached as received; verification is the bank's"),
        ("GSTIN status and return filing on the GST portal", "Active registration, filing regularity"),
    ]
    if agg >= 5e7:
        checks.append(("CRILC (Central Repository of Information on Large Credits)",
                       f"Aggregate exposure {inr(agg)} is ₹5 Cr or more"))
    if is_company:
        checks.append(("MCA / ROC search — charges registered, director DIN status, filings",
                       "Existing charges on the assets offered; disqualified directors"))
    if b.get("msme_class") not in (None, "", "Not registered"):
        checks.append(("Udyam registration verification", f"MSME class stated: {b['msme_class']}"))
    if kinds & sec.PROPERTY:
        checks.append(("Legal scrutiny of title, search report and fresh valuation by the bank's empanelled valuer",
                       "Mortgaged property"))
    if kinds & (sec.PROPERTY | {"Plant & machinery", "Vehicles", "Stock (inventory)",
                                "Book debts / receivables (< 90 days)"}):
        checks.append(("CERSAI search before creating the charge, and registration after", "Prior charges on the assets"))
    if "export" in products or "pcfc" in products or "packing" in products:
        checks.append(("ECGC Specific Approval List (SAL)", "Export facilities"))
    if "CGTMSE cover" in kinds:
        checks.append(("CGTMSE eligibility and guarantee fee", "Cover proposed"))
    checks.append(("Pre-sanction site visit / unit inspection", "Confirms the business, stock and assets exist"))
    return checks


def suggested_terms(s) -> tuple[list, list]:
    case = s["case"]
    pre, post = [], []
    for x in case["securities"]:
        cls_, charge, _, docs = sec.SECURITY_TYPES[x["kind"]]
        what = f"{x['kind']}" + (f" ({x['description']})" if x.get("description") else "")
        if cls_ == "Guarantee":
            pre.append(f"{x['kind']} of {x.get('owner') or 'the guarantor'} — {docs}")
        else:
            pre.append(f"{charge} over {what} — {docs}")
    for f in case["facilities"]:
        if f.get("cash_margin_pct"):
            pre.append(f"{f['product']}: cash margin of {f['cash_margin_pct']:g}% by way of FD under lien")
        if f.get("project_cost") and f.get("amount") and f["project_cost"] > f["amount"]:
            pre.append(f"{f['product']}: promoter contribution of {inr(f['project_cost'] - f['amount'])} to be brought in "
                       f"before disbursement")
    pre.append("Board resolution / authority letter to borrow and execute documents; loan documents duly executed")
    kinds = {x["kind"] for x in case["securities"]}
    if any(loans.product(f["product"])["repay"] not in ("amortising", "bullet") and loans.is_fund(f)
           for f in case["facilities"]):
        post.append("Monthly stock and book-debt statements; drawing power as per the bank's margins")
        post.append("Periodic stock audit / inspection as per the bank's policy")
    if kinds - {"Personal guarantee", "Corporate guarantee", "CGTMSE cover", "Fixed deposit", "Cash margin"}:
        post.append("Assets charged to be fully insured with the bank clause")
    if kinds & sec.PROPERTY:
        post.append(f"Revaluation of property every {sec.REVALUE_YEARS} years")
    post += ["Audited financial statements within six months of year end; provisional figures half-yearly",
             "Sales proceeds to be routed through the accounts with the bank in proportion to its share",
             "No further borrowing or change in ownership / management without the bank's consent",
             "Annual review / renewal of working capital and non-fund limits"]
    return pre, post


def _checks_and_terms(s):
    out = "<h1>12. Compliance checks for the bank</h1>"
    out += '<p class="muted">Not carried out by the preparer. Left for the bank to record.</p>'
    out += table(["Check", "Why", "Status / remarks (bank)"],
                 [[e(c), e(w), "&nbsp;"] for c, w in compliance_checks(s)], widths=["44%", "36%", "20%"])
    pre, post = suggested_terms(s)
    out += "<h1>13. Suggested terms and conditions</h1>"
    out += '<p class="muted">Suggested from the proposal and security. The bank\'s sanction letter prevails.</p>'
    out += "<h2>Before disbursement</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in pre) + "</ul>"
    out += "<h2>After disbursement / covenants</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in post) + "</ul>"
    return out


def _documents(s, kyc_list, kyc_missing, annex):
    out = "<h1>14. KYC documents</h1>"
    out += '<p class="muted">Attached as received from the borrower. Not verified by the preparer.</p>'
    ref = {a["key"]: a for a in annex}
    rows = [[e(d["doc_id"]), e(kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"])), e(d.get("person")),
             e(d["filename"].rsplit("/", 1)[-1]), e(ref[d["doc_id"]]["where"]) if d["doc_id"] in ref else "Not attached"]
            for d in kyc_list]
    out += table(["ID", "Document", "Person", "File", "In this report"], rows)
    if kyc_missing:
        out += "<h2>Required documents not yet received</h2><ul>" + "".join(
            f"<li>{e(m)}</li>" for m in kyc_missing) + "</ul>"
    others = [a for a in annex if a["group"] != "KYC documents"]
    out += "<h1>15. Other documents relied on</h1>"
    out += table(["ID", "Group", "File", "In this report"],
                 [[e(a["key"]), e(a["group"]), e(a["filename"]), e(a["where"])] for a in others])
    return out


def _declaration(info):
    return ("<h1>16. Basis of preparation and declaration</h1>"
            "<ul><li>This note is prepared from documents supplied by the borrower, listed in sections 14 and 15 and "
            "attached as annexures.</li>"
            "<li>Figures were read from those documents (automatically, then reviewed); where they differ, the original "
            "documents prevail.</li>"
            "<li>KYC documents are attached as received. Their verification, credit bureau and regulatory checks "
            "(section 12), legal scrutiny and valuation are for the bank.</li>"
            "<li>Projections assume the growth, tax and utilisation stated in section 10; actual results may differ.</li>"
            "<li>The terms in section 13 are suggestions; the bank's sanction terms prevail.</li></ul>"
            '<p style="margin-top:24pt"></p>' +
            table([], [["<b>Prepared by</b>", e(info.get("prepared_by")), "<b>Signature</b>", "<br><br>"],
                       ["<b>Date</b>", e(info.get("date") or datetime.now().strftime("%d %b %Y")), "<b>Place</b>", ""]],
                  widths=[0.16, 0.34, 0.16, 0.34], head_row=False, row_class=["kv", "kv"]))


# ---------------------------------------------------------------- assembly

def people_on_file(s, kyc_list) -> dict:
    people = {}
    for d in kyc_list:
        if d.get("person"):
            people.setdefault(d["person"], set()).add(kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]).split(" (")[0])
    for x in s["case"]["securities"]:
        if sec.SECURITY_TYPES[x["kind"]][0] == "Guarantee" and x.get("owner"):
            people.setdefault(x["owner"], set()).add(x["kind"])
    res = s["res"]
    if res is not None and not res["tables"]["itr"].empty:
        for n in res["tables"]["itr"]["assessee_name"].dropna().unique():
            people.setdefault(n, set()).add("ITR")
    return {p: ", ".join(sorted(v)) for p, v in people.items()}


def _render(chunks: list) -> tuple[bytes, list]:
    """chunks: list of HTML strings, each starting on a new page. Returns the PDF and [(level, title, page)]."""
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    heads, page = [], 0
    for html in chunks:
        story = fitz.Story(f"<body>{html}</body>", user_css=CSS)
        more = 1
        while more:
            dev = writer.begin_page(A4)
            more, _ = story.place(BODY)
            story.element_positions(lambda pos: heads.append((pos.heading, pos.text.strip(), pos.page + 1))
                                    if pos.heading in (1, 2) and pos.open_close & 1 and pos.text else None,
                                    {"page": page})
            story.draw(dev)
            writer.end_page()
            page += 1
    writer.close()
    return buf.getvalue(), heads


def _stamp(page, text: str, top: bool):
    r = page.rect
    box = fitz.Rect(30, 6, r.width - 30, 22) if top else fitz.Rect(42, r.height - 32, r.width - 42, r.height - 14)
    if top:
        page.draw_rect(fitz.Rect(0, 0, r.width, 24), color=None, fill=(1, 1, 1), fill_opacity=0.85, overlay=True)
    page.insert_htmlbox(box, f'<div style="font-family:sans-serif;font-size:7pt;color:#475467">{text}</div>')


def _as_pdf(data: bytes, filename: str):
    """(document, None) for a PDF or image, else (None, why it can't be embedded)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == "pdf" or data[:5] == b"%PDF-":
            src = fitz.open(stream=data, filetype="pdf")
            if src.needs_pass:
                src.close()
                return None, "password-protected - unlock it in the app first"
            return src, None
        if ext in IMAGE_EXTS:
            doc = fitz.open()
            page = doc.new_page(width=A4.width, height=A4.height)
            page.insert_image(A4 + (30, 34, -30, -30), stream=data, keep_proportion=True)
            return doc, None
    except Exception as ex:  # damaged file: list it, don't fail the whole report
        return None, f"could not be embedded ({type(ex).__name__})"
    return None, (f".{ext} file - included in the credit file ZIP" if ext else "unknown file type")


def build_pdf(s: dict, info: dict, kyc_list: list, kyc_missing: list, other_docs: dict,
              schedules: dict | None = None, gst_meta: dict | None = None) -> bytes:
    """s: steps.summary.build(). info: bank, branch, prepared_by, reference, date, recommendation.
    kyc_list: kyc_step.kyc_docs(). other_docs: {group: [(doc_id, filename, bytes)]}.
    schedules: {facility id: repayment schedule} for the yearly tables."""
    b = s["case"]["borrower"]
    # Annexure plan first, so the document index can say where each file is.
    groups = [("KYC documents", [(d["doc_id"], d["filename"].rsplit("/", 1)[-1], d["data"],
                                  " · ".join(x for x in (kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]).split(" (")[0],
                                                         d.get("person")) if x)) for d in kyc_list])]
    groups += [(g, [(i, f.rsplit("/", 1)[-1], data, "") for i, f, data in files]) for g, files in other_docs.items()]
    annex = []
    for gi, (group, files) in enumerate(groups):
        letter = "ABCDEFGH"[gi]
        for n, (key, fname, data, label) in enumerate(files, 1):
            doc, why = _as_pdf(data, fname) if data else (None, None)
            where = (f"Annexure {letter}{n}" if doc else f"Not embedded: {why}") if data else                 "Listed only (file not uploaded)"
            annex.append({"key": key, "group": group, "filename": fname, "doc": doc, "label": label,
                          "code": f"{letter}{n}", "where": where})

    body = [_summary(s, info), _borrower(s, people_on_file(s, kyc_list), gst_meta or {}), _proposal(s), _assessment(s),
            _financials(s), _banking(s), _gst(s), _itr(s), _security(s), _repayment(s, schedules), _risks(s),
            _checks_and_terms(s), _documents(s, kyc_list, kyc_missing, annex), _declaration(info)]
    body_html = "".join(body)

    # Pass 1 for page numbers, pass 2 with the contents page filled in.
    _, heads = _render([_cover(s, info), _toc([("x", 0)] * 20), body_html])
    heads = [(lvl, t.replace("\u200b", ""), p) for lvl, t, p in heads]
    entries = [(t, p) for lvl, t, p in heads if lvl == 1 and t != "Contents"]
    pdf, heads = _render([_cover(s, info), _toc(entries + [("Annexures", "")]), body_html])

    out = fitz.open("pdf", pdf)
    n_report = out.page_count
    toc = [[1, "Cover", 1]] + [[lvl, t.replace("\u200b", ""), p] for lvl, t, p in heads]
    annex_start = None
    for a in annex:
        if a["doc"] is None:
            continue
        start = out.page_count
        out.insert_pdf(a["doc"])
        a["doc"].close()
        if annex_start is None:
            annex_start = start + 1
            toc.append([1, "Annexures", annex_start])
        title = f"Annexure {a['code']} — {a['group'][:-1] if a['group'].endswith('s') else a['group']}: " \
                f"{a['label'] or a['filename']}"
        toc.append([2, f"{a['code']} {a['label'] or a['filename']}", start + 1])
        for pno in range(start, out.page_count):
            _stamp(out[pno], escape(f"{title} · {a['filename']} · page {pno - start + 1} of {out.page_count - start}"),
                   top=True)
    name = escape(b["name"] or "Borrower")
    total = out.page_count
    for pno in range(n_report):
        if pno == 0:
            continue
        _stamp(out[pno], f'<table style="width:100%"><tr><td>{name} — Credit Appraisal Memorandum</td>'
                         f'<td style="text-align:right">Page {pno + 1} of {total}</td></tr></table>', top=False)
    out.set_toc(toc)
    out.set_metadata({"title": f"Credit Appraisal Memorandum — {b['name'] or 'Borrower'}",
                      "author": info.get("prepared_by") or "", "subject": "Credit proposal",
                      "creator": "Credit Appraisal Workbench"})
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data
