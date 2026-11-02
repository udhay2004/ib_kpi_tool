"""Client details from the Connect Ventures fact-find form and loan detail sheet, shown at the end of step 1.
Nothing here can be read from the financial documents, so it is typed in. Saved in case["client"]."""

import pandas as pd
import streamlit as st

import ui
from common import inr, ss

TABLES = {  # stored name -> [(column, kind)]
    "promoters": [("Name", "text"), ("Designation", "text"), ("PAN", "text"), ("Aadhaar last 4", "text"), ("Mobile", "text"),
                  ("Email", "text"), ("DIN", "text"), ("Share %", "num"), ("Passport no", "text"), ("Age", "num"),
                  ("Qualification", "text"), ("Experience yrs", "num"), ("Residence", "choice:Owned|Rented"),
                  ("CIBIL", "num"), ("CRIF / Experian", "text")],
    "obligations": [("Lender", "text"), ("Product", "text"), ("Security", "choice:Secured|Unsecured"), ("Loan amount ₹", "num"),
                    ("EMI ₹", "num"), ("Outstanding ₹", "num"), ("Term months", "num"), ("ROI %", "num"), ("EMI start", "text"),
                    ("Months left", "num"), ("Count in FOIR", "choice:Y|N"), ("Close with new loan", "choice:Y|N")],
    "customers": [("Name", "text"), ("% of sales", "num"), ("Credit days", "num")],
    "suppliers": [("Name", "text"), ("% of purchases", "num"), ("Credit days", "num")],
    "receivables": [("Particulars", "text"), ("Amount ₹", "num")],
}
EXPENSES = [("salaries", "Salaries"), ("rent", "Rent"), ("monthly_emi", "Monthly EMI"), ("cc_interest", "CC interest"),
            ("office", "Office expense")]
YTD = [("sales", "Sales"), ("ebitda", "EBITDA"), ("depreciation", "Depreciation"), ("interest", "Interest"), ("npbt", "NPBT"),
       ("pat", "PAT"), ("net_worth", "Net worth"), ("tol", "Total outside liabilities"), ("stock", "Stock"),
       ("debtors", "Debtors"), ("creditors", "Creditors")]


def _text(c: dict, col, key: str, label: str, placeholder: str = ""):
    c[key] = col.text_input(label, value=c.get(key) or "", key=f"ff_{key}", placeholder=placeholder)


def _editor(c: dict, name: str):
    """Editable table. The editor keeps its own edits, so it is given the same table on every rerun, refreshed from the
    case only when it comes back on screen (Streamlit drops an editor's state when the step is left)."""
    spec = TABLES[name]
    start, key = f"ff_start_{name}", f"ff_tbl_{name}"
    if start not in ss or key not in ss:
        df = pd.DataFrame(c.get(name) or [], columns=[n for n, _ in spec])
        for n, kind in spec:
            df[n] = pd.to_numeric(df[n], errors="coerce").astype(float) if kind == "num" else df[n].astype(object)
        ss[start] = df
    cfg = {}
    for n, kind in spec:
        cfg[n] = (st.column_config.NumberColumn(n) if kind == "num" else
                  st.column_config.SelectboxColumn(n, options=kind.split(":")[1].split("|")) if kind.startswith("choice")
                  else st.column_config.TextColumn(n))
    edited = st.data_editor(ss[start], key=key, num_rows="dynamic", hide_index=True, use_container_width=True,
                            column_config=cfg)
    rows = edited.astype(object).where(edited.notna(), None).to_dict("records")
    c[name] = [r for r in rows if any(v not in (None, "") for v in r.values())]


def render():
    c = ss.case.setdefault("client", {})
    ui.section("Client details", "From the fact-find form and loan detail sheet. These go into the bank report and the "
               "internal CAM; anything left blank prints as a dash.")
    with st.expander("Business profile", expanded=not c.get("address")):
        c1, c2 = st.columns(2)
        _text(c, c1, "address", "Registered address")
        _text(c, c2, "city_pin", "City & PIN code")
        _text(c, c1, "contact_person", "Contact person")
        _text(c, c2, "mobile", "Contact number")
        _text(c, c1, "email", "Email ID")
        _text(c, c2, "website", "URL / website")
        _text(c, c1, "incorporated", "Date of incorporation", "DD/MM/YYYY")
        _text(c, c2, "employees", "No. of employees")
        _text(c, c1, "udyam_no", "Udyam registration number")
        _text(c, c2, "bank_particulars", "Bank particulars", "Bank, branch, account type")
        c["nature"] = st.text_area("Nature of business (brief)", value=c.get("nature") or "", key="ff_nature", height=70)
        c["history"] = st.text_area("Vintage & brief history", value=c.get("history") or "", key="ff_history", height=70)
        _text(c, st, "group_companies", "Group companies / associate concerns")
    with st.expander("Directors / partners / promoters"):
        st.caption("Enter only the last 4 digits of Aadhaar. The report always prints it masked.")
        _editor(c, "promoters")
    with st.expander("Existing loan obligations — every EMI and limit"):
        _editor(c, "obligations")
    with st.expander("Major customers, suppliers and bills receivable"):
        st.markdown("**Customers**")
        _editor(c, "customers")
        st.markdown("**Suppliers**")
        _editor(c, "suppliers")
        st.markdown("**Bills receivable**")
        _editor(c, "receivables")
    with st.expander("Fixed monthly expenses and decision maker"):
        exp = c.setdefault("expenses", {})
        cols = st.columns(len(EXPENSES))
        for col, (k, label) in zip(cols, EXPENSES):
            exp[k] = col.number_input(f"{label} (₹)", min_value=0.0, value=float(exp.get(k) or 0), step=10000.0,
                                      format="%.0f", key=f"ff_exp_{k}")
        st.caption(f"Total fixed monthly expenses: {inr(sum(exp.values()))}")
        c1, c2, c3 = st.columns(3)
        _text(c, c1, "decision_maker", "Name & designation of decision maker")
        _text(c, c2, "dm_mobile", "Contact number")
        _text(c, c3, "dm_email", "Email ID")
    with st.expander("Current year-to-date figures (provisional)"):
        y = c.setdefault("ytd", {})
        y["period"] = st.text_input("Period", value=y.get("period") or "", key="ff_ytd_period",
                                    placeholder="e.g. Apr-Sep 2026; leave blank if not available")
        cols = st.columns(4)
        for i, (k, label) in enumerate(YTD):
            y[k] = cols[i % 4].number_input(f"{label} (₹)", value=float(y.get(k) or 0), step=100000.0, format="%.0f",
                                            key=f"ff_ytd_{k}")
    with st.expander("Bureau summary"):
        c1, c2 = st.columns(2)
        _text(c, c1, "cmr", "CMR (if company)")
        _text(c, c2, "enquiries", "Recent enquiries (90 days)")
        _text(c, c1, "dpd", "DPD history / flags (settled, written-off, suit-filed)")
        _text(c, c2, "guarantees_given", "Guarantees given by promoters (group exposure)")
