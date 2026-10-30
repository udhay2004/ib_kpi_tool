"""Step 6 - credit summary across all steps, the credit report (PDF) for the bank, credit file ZIP, save / open a case."""

import io
import json
import re
import zipfile
from datetime import date, datetime
from html import escape

import pandas as pd
import streamlit as st

import capacity
import credit_report
import kpis
import kyc
import loans
import security as sec
import ui
from common import as_dicts, facility_totals, inr, ss
from intake import coverage
from report import to_excel
from steps import kyc_step
from steps.financials import analysis, fin_docs, latest
from steps.repayment_step import assessments, capacity_result, dscr_table, repay_cfg, schedules

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def build() -> dict:
    case = ss.case
    b = case["borrower"]
    res = analysis()
    fin = latest(res)
    t = facility_totals(case["facilities"])
    ass = assessments(fin)
    cov = sec.cover(case["securities"], t["net_exposure"])
    dt = dscr_table(fin) if fin else None
    cap = capacity_result(res) if case["facilities"] else None
    flags = []

    def add(sev, area, finding, step, next_step):
        flags.append({"severity": sev, "area": area, "finding": finding, "evidence": step, "next_step": next_step})

    if not case["facilities"]:
        add("High", "Proposal", "No facilities entered", "Step 1", "Add the facilities requested")
    fac_rows = []
    for f in case["facilities"]:
        p, a = loans.product(f["product"]), ass[f["id"]]
        method = repay_cfg(f)["method"] if p["repay"] in ("amortising", "bullet") else p["repay"].replace("_", "-").capitalize()
        fac_rows.append({"Facility": f["product"], "Category": p["category"], "Amount (₹)": f.get("amount"),
                         "Tenor (months)": f.get("tenor_months"), "Rate %": f.get("rate_pct"),
                         "Cash margin %": f.get("cash_margin_pct"), "Repayment": method,
                         "Assessment": a["method"], "Eligible (₹)": a["eligible"],
                         "Within eligibility": None if a["ok"] is None else ("Yes" if a["ok"] else "No"),
                         "Purpose": f.get("purpose")})
        if a["ok"] is False:
            add("High", "Eligibility", f"{f['product']}: requested {inr(f.get('amount'))} exceeds assessed {inr(a['eligible'])}",
                "Step 5", "Reduce the limit or justify the excess")
    for s, finding, n in sec.security_flags(case["securities"], t["net_exposure"], b["msme_class"], t["fund"]):
        add(s, "Security", finding, "Step 2", n)
    for label in kyc_step.missing():
        add("Low", "KYC documents", f"Not attached: {label}", "Step 3", "Collect from the client for the credit file")
    if res is not None:
        for _, r in res["flags"].iterrows():
            add(r["severity"], r["area"], r["finding"], r["evidence"], r["next_step"])
    else:
        add("High", "Financials", "No financial documents read", "Step 4", "Upload and read the financials")
    if cap is not None and cap["verdict"]["status"] != "good":
        add("High" if cap["verdict"]["status"] == "critical" else "Medium", "Repayment capacity",
            f"Repayment capacity: {cap['verdict']['label']} — {cap['verdict']['meaning']}", "Step 5",
            "Restructure (tenor, moratorium, amount) or get additional security / guarantees")
    if dt is not None and not dt.empty:
        mn, avg = dt["dscr"].min(), dt["dscr"].mean()
        if mn < kpis.THRESHOLDS["dscr_min"]:
            add("High", "Repayment", f"Minimum DSCR on the proposal {mn:.2f}x (year {int(dt.loc[dt['dscr'].idxmin(), 'year'])})",
                "Step 5", "Lengthen tenor, add moratorium, change repayment method or reduce the amount")
        elif avg < kpis.THRESHOLDS["dscr_avg_min"]:
            add("Medium", "Repayment", f"Average DSCR on the proposal {avg:.2f}x", "Step 5", "Review repayment structure")
    flags = sorted(flags, key=lambda f: SEV_ORDER[f["severity"]])

    n_kyc, n_missing = len(kyc_step.kyc_docs()), len(kyc_step.missing())
    steps = [
        ("Loan request", "good" if case["facilities"] and b["name"] else "critical"),
        ("Security", "good" if case["securities"] else "critical"),
        ("KYC", "good" if n_kyc and not n_missing else "warning" if n_kyc else "critical"),
        ("Financials", "good" if res is not None and not (coverage(fin_docs())["Status"].str.startswith("❌")).any()
         else "warning" if res is not None else "critical"),
        ("Repayment", cap["verdict"]["status"] if cap is not None else "warning" if case["facilities"] else "critical"),
    ]
    return {"case": case, "res": res, "fin": fin, "totals": t, "cover": cov, "dscr": dt, "flags": pd.DataFrame(flags),
            "facilities": pd.DataFrame(fac_rows), "steps": steps,
            "capacity": cap}


def save_case() -> str:
    def ser(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        if isinstance(o, set):
            return list(o)
        return str(o)
    payload = {"version": 1, "saved": datetime.now().isoformat(timespec="seconds"), "case": ss.case,
               "financial_docs": as_dicts("docs", "demo"),
               "kyc_docs": [{k: v for k, v in d.items() if k != "data"} for d in kyc_step.kyc_docs()],
               "security_docs": as_dicts("sec_docs", "static_sec")}
    return json.dumps(payload, default=ser, indent=1)


def load_case(raw: bytes):
    from common import reset_all
    data = json.loads(raw)
    reset_all()
    case = data["case"]
    for s in case.get("securities", []):
        if s.get("valuation_date"):
            s["valuation_date"] = date.fromisoformat(s["valuation_date"])
    ss.case = case
    ss.demo = [d for d in data.get("financial_docs", []) if d.get("extracted")] or None
    ss.static_kyc = [{"doc_id": d["doc_id"], "filename": d["filename"], "person": d.get("person") or "",
                      "doc_type": d["doc_type"] if d.get("doc_type") in kyc.ALL_KINDS
                      else (d.get("extracted") or {}).get("doc_kind", "other")}
                     for d in data.get("kyc_docs", [])] or None
    ss.static_sec = [d for d in data.get("security_docs", []) if d.get("extracted")] or None


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", name)[:150]


def credit_file_zip(xls: bytes, name: str, folders: dict | None = None, pdf: bytes | None = None) -> bytes:
    """Credit report (PDF), credit note (Excel), every attached KYC file (numbered and named by document), and any
    other source files. folders: {folder name: [(file name, bytes)]}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if pdf:
            z.writestr(f"{name}_credit_report.pdf", pdf)
        z.writestr(f"{name}_credit_note.xlsx", xls)
        for folder, files in (folders or {}).items():
            for fname, data in files:
                z.writestr(f"{folder}/{_safe(fname)}", data)
        for d in kyc_step.kyc_docs():
            if not d["data"]:
                continue
            label = kyc.ALL_KINDS.get(d["doc_type"], "Other").split(" (")[0]  # "Aadhaar (masked…)" -> "Aadhaar"
            parts = [d["doc_id"], label] + ([d["person"]] if d.get("person") else [])
            z.writestr(f"KYC/{_safe(' - '.join(parts + [d['filename'].rsplit('/', 1)[-1]]))}", d["data"])
    return buf.getvalue()


def entity_details() -> dict:
    """GSTIN / bank accounts as read from the financial documents, for the borrower profile."""
    out, banks = {}, []
    for d in as_dicts("docs", "demo"):
        x = d.get("extracted") or {}
        if x.get("gstin") and "GSTIN" not in out:
            out["GSTIN"] = x["gstin"] + (f" ({x['state']})" if x.get("state") else "")
        if x.get("bank_name"):
            banks.append(" ".join(str(v) for v in (x["bank_name"], x.get("account_type"),
                                                   f"..{x['account_number_last4']}" if x.get("account_number_last4") else "")
                                  if v))
    if banks:
        out["Bank accounts (statements given)"] = "; ".join(dict.fromkeys(banks))
    return out


def report_pdf(s: dict) -> bytes:
    def files(store):
        return [(d.doc_id, d.filename, d.data) for d in ss[store].values()]
    info = {**ss.case.get("report", {}), "date": datetime.now().strftime("%d %b %Y")}
    return credit_report.build_pdf(s, info, kyc_step.kyc_docs(), kyc_step.missing(),
                                   {"Financial documents": files("docs") or
                                    [(d["doc_id"], d["filename"], None) for d in ss.demo or []],
                                    "Security documents": files("sec_docs") or
                                    [(d["doc_id"], d["filename"], None) for d in ss.static_sec or []]},
                                   schedules() if s["case"]["facilities"] else {}, entity_details())


def _report_details():
    rep = ss.case.setdefault("report", {})
    with st.expander("Report details — bank, preparer, recommendation", expanded=not rep.get("bank")):
        c1, c2 = st.columns(2)
        for col, key, label, ph in ((c1, "bank", "Bank", "e.g. State Bank of India"),
                                    (c2, "branch", "Branch", "e.g. MIDC Ambernath"),
                                    (c1, "prepared_by", "Prepared by", "Name, firm"),
                                    (c2, "reference", "Reference no.", "Your file / proposal reference")):
            rep[key] = col.text_input(label, value=rep.get(key, ""), placeholder=ph, key=f"rep_{key}")
        rep["recommendation"] = st.text_area(
            "Recommendation / remarks for the bank", value=rep.get("recommendation", ""), height=90,
            key="rep_recommendation", placeholder="e.g. Recommended for ₹10 Cr fund-based and ₹2.5 Cr non-fund limits, "
                                                  "subject to reducing CC to the assessed ₹4.38 Cr…")


def render(div, unit_label):
    s = build()
    case, b, t = s["case"], s["case"]["borrower"], s["totals"]
    st.markdown(f'<div class="card"><div style="font-size:1.15rem;font-weight:680">{b["name"] or "Unnamed borrower"}</div>'
                f'<div class="muted">{b["constitution"]} · {b["industry"] or "industry not set"} · MSME: {b["msme_class"]} · '
                f'{b["years_in_business"]} yrs in business</div><div style="margin-top:10px">'
                + " ".join(ui.pill(st_, name) for name, st_ in s["steps"]) + "</div></div>", unsafe_allow_html=True)

    dt, cov, res = s["dscr"], s["cover"], s["res"]
    fk = res["fin_kpis"] if res is not None else pd.DataFrame()
    fy = s["fin"].get("fy") if s["fin"] else None
    th = kpis.THRESHOLDS

    def kv(key):
        return fk.at[key, fy] if fy and key in fk.index and pd.notna(fk.at[key, fy]) else None
    cr, tol = kv("current_ratio"), kv("tol_tnw")
    ui.section("Key numbers")
    ui.kpi_tiles([
        {"label": "Total proposed", "value": inr(t["total"]), "sub": f"Fund {inr(t['fund'])} · NFB {inr(t['nonfund'])}"},
        {"label": "Security cover", "value": "—" if cov["cover"] is None else f"{cov['cover']:.2f}x",
         "status": None if cov["cover"] is None else ("good" if cov["cover"] >= 1.25 else "critical"), "bench": "≥ 1.25x"},
        {"label": "Min DSCR (proposal)", "value": "—" if dt is None or dt.empty else f"{dt['dscr'].min():.2f}x",
         "status": None if dt is None or dt.empty else ("good" if dt["dscr"].min() >= th["dscr_min"] else "critical"),
         "bench": f"≥ {th['dscr_min']:.2f}x"},
        {"label": f"Revenue {fy or ''}", "value": inr(kv("revenue"))},
        {"label": "Current ratio", "value": "—" if cr is None else f"{cr:.2f}x",
         "status": None if cr is None else ("good" if cr >= th["current_ratio_benchmark"] else "critical"), "bench": "≥ 1.33x"},
        {"label": "TOL / TNW", "value": "—" if tol is None else f"{tol:.2f}x",
         "status": None if tol is None else ("good" if tol <= th["tol_tnw_max"] else "critical"), "bench": "≤ 4.0x"},
        {"label": "High findings", "value": str(int((s["flags"]["severity"] == "High").sum())) if not s["flags"].empty else "0"},
        {"label": "Medium findings", "value": str(int((s["flags"]["severity"] == "Medium").sum())) if not s["flags"].empty else "0"},
    ])

    cap = s["capacity"]
    if cap is not None:
        ui.section("Repayment capacity")
        st.markdown(f'<div class="card">{ui.pill(cap["verdict"]["status"], cap["verdict"]["label"])} '
                    f'{escape(cap["verdict"]["meaning"])}<ul style="margin:8px 0 0 0">' +
                    "".join(f"<li><b>{escape(n['title'])}.</b> {escape(n['text'])}</li>" for n in cap["notes"]) + "</ul></div>",
                    unsafe_allow_html=True)

    ui.section("Proposal")
    if s["facilities"].empty:
        st.info("No facilities yet.")
    else:
        show = s["facilities"].copy()
        for c in ("Amount (₹)", "Eligible (₹)"):
            show[c] = show[c].map(inr)
        st.dataframe(show, hide_index=True, use_container_width=True)

    ui.section("All findings", "Everything open across loan request, security, KYC, financials and repayment.")
    if s["flags"].empty:
        st.success("No findings.")
    else:
        sev = st.segmented_control("Show", ["High", "Medium", "Low"], default=["High", "Medium"], selection_mode="multi",
                                   key="sum_sev") or []
        ui.flag_cards(s["flags"][s["flags"]["severity"].isin(sev)])

    ui.section("Report for the bank", "One PDF: the full appraisal note — proposal, borrower, financials, banking, GST, "
                                      "security, repayment capacity, risks, checks for the bank — followed by every KYC, "
                                      "financial and security document as annexures.")
    _report_details()
    c1, c2, c3, c4 = st.columns(4)
    name = (b["name"] or "Case").replace(" ", "_")
    extra = {
        "Proposal": s["facilities"],
        "Security": pd.DataFrame([{**{k: v for k, v in x.items() if k not in ("id",)},
                                   "realisable": sec.realisable(x)} for x in case["securities"]]),
        "Repayment capacity": capacity.report_rows(cap) if cap is not None else pd.DataFrame(),
        "All findings": s["flags"],
        "KYC documents": kyc_step.index_table(),
        "Repayment DSCR": dt if dt is not None else pd.DataFrame(),
    }
    summary_rows = [("Borrower", b["name"]), ("Constitution", b["constitution"]), ("Industry", b["industry"]),
                    ("MSME", b["msme_class"]), ("Total proposed (₹)", t["total"]), ("Fund based (₹)", t["fund"]),
                    ("Non-fund based (₹)", t["nonfund"]),
                    ("Security cover (x)", cov["cover"]),
                    ("Min DSCR on proposal (x)", None if dt is None or dt.empty else float(dt["dscr"].min())),
                    ("Repayment capacity", cap["verdict"]["label"] if cap is not None else "Not assessed")]
    empty_res = {"tables": {"financials": pd.DataFrame(), "bank": pd.DataFrame(), "gst": pd.DataFrame(),
                            "itr": pd.DataFrame()},
                 "fin_kpis": pd.DataFrame(), "bank_kpis": pd.DataFrame(), "gst_kpis": pd.DataFrame(),
                 "flags": pd.DataFrame(columns=["severity"])}
    xls = to_excel(res or empty_res, pd.DataFrame(), b["name"] or "Borrower", coverage(fin_docs()),
                   extra=extra, summary=summary_rows)
    n_files = sum(1 for d in kyc_step.kyc_docs() if d["data"])
    folders = {"Financial documents": [(f"{d.doc_id} - {d.filename.rsplit('/', 1)[-1]}", d.data) for d in ss.docs.values()],
               "Security documents": [(f"{d.doc_id} - {d.filename.rsplit('/', 1)[-1]}", d.data) for d in ss.sec_docs.values()]}
    try:
        pdf = report_pdf(s)
    except Exception as ex:  # never lose the other exports because of one bad file
        pdf = None
        st.error(f"Could not build the PDF report: {ex}")
    if pdf:
        c1.download_button("⬇  Credit report (PDF)", pdf, file_name=f"{name}_credit_report.pdf", type="primary",
                           use_container_width=True, mime="application/pdf",
                           help="Appraisal note with all KYC, financial and security documents attached as annexures.")
    c2.download_button("⬇  Credit file (.zip)", credit_file_zip(xls, name, folders, pdf),
                       file_name=f"{name}_credit_file.zip", use_container_width=True,
                       help=f"PDF report + Excel credit note + {n_files} KYC document(s), plus the financial and "
                            f"security documents in their original format.")
    c3.download_button("⬇  Credit note (Excel)", xls, file_name=f"{name}_credit_note.xlsx",
                       use_container_width=True)
    c4.download_button("⬇  Save case file (.json)", save_case(), file_name=f"{name}_case.json", use_container_width=True,
                       help="Everything entered and extracted (not the original files). Open it later from the sidebar.")
    st.caption("KYC documents are attached as received. They are not verified by this tool. Excel, Word and other "
               "non-PDF files are listed in the PDF and included in the ZIP in their original format.")
