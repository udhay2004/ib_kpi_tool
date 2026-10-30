"""Credit Appraisal Workbench - Streamlit app.

Run:  streamlit run app.py
Six steps: loan request -> security -> KYC -> financials -> repayment & eligibility -> credit summary.

Hosting: set ANTHROPIC_API_KEY (and APP_PASSWORD) as environment variables or in
.streamlit/secrets.toml. See README.
"""

import hmac

import streamlit as st

import kpis
import ui
from common import UNITS, facility_totals, init_state, inr, reset_all, secret, ss
from demo_data import demo_case, demo_docs, demo_kyc, demo_security_docs

st.set_page_config(page_title="Credit Appraisal Workbench", page_icon="📊", layout="wide")
ui.inject_css()

# ---------------------------------------------------------------- access gate (hosted use)
APP_PASSWORD = secret("APP_PASSWORD")
if APP_PASSWORD and not ss.get("authed"):
    ui.hero("Sign in to continue")
    with st.form("login"):
        pw = st.text_input("Team password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if hmac.compare_digest(pw, APP_PASSWORD):
                ss.authed = True
                st.rerun()
            st.error("Wrong password")
    st.stop()

init_state()
SERVER_KEY = secret("ANTHROPIC_API_KEY")
STEPS = ["① Loan request", "② Security", "③ KYC", "④ Financials", "⑤ Repayment", "⑥ Credit summary"]


def load_demo():
    reset_all()
    ss.case = demo_case()
    ss.demo, ss.static_kyc, ss.static_sec = demo_docs(), demo_kyc(), demo_security_docs()


def go(i: int):
    ss.nav = STEPS[i]


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    api_key = SERVER_KEY
    if SERVER_KEY:
        st.caption("✓ API key configured on the server")
    else:
        api_key = st.text_input("Anthropic API key (optional)", type="password", placeholder="sk-ant-…",
                                help="Not needed for digital PDFs, Excel or CSV: those are read on this server. "
                                     "Only scanned documents and valuation reports use Claude. Used only in this "
                                     "browser session; never saved.")
    unit_label = st.segmented_control("Amounts in", list(UNITS), default="₹ Crore") or "₹ Crore"
    div = UNITS[unit_label]
    with st.expander("Credit benchmarks"):
        st.caption("Common Indian bank / NBFC norms. Change them to your lender's policy.")
        for k, v in kpis.THRESHOLDS.items():
            kpis.THRESHOLDS[k] = st.number_input(k.replace("_", " ").capitalize(), value=float(v), key=f"th_{k}")
    st.divider()
    st.markdown("**Case**")
    c1, c2 = st.columns(2)
    c1.button("Load demo", use_container_width=True, on_click=load_demo, help="A fictional company — no API key needed")
    c2.button("New case", use_container_width=True, on_click=reset_all)
    case_file = st.file_uploader("Open a saved case (.json)", type=["json"], key=f"open_case_{ss.upload_round}")
    if case_file is not None and ss.get("opened_case") != case_file.file_id:
        from steps.summary import load_case
        load_case(case_file.getvalue())
        ss.opened_case = case_file.file_id
        st.rerun()
    st.caption("Draft analysis. AI-extracted figures must be checked against the source documents. Documents go to "
               "the Anthropic API and are not stored by this app; save the case file to keep your work.")

# ---------------------------------------------------------------- header + stepper
b = ss.case["borrower"]
t = facility_totals(ss.case["facilities"])
chip = f"{b['name']} · {inr(t['total'])} proposed" if b["name"] or t["total"] else None
ui.hero("Loan request → security → KYC → financials → repayment → credit note", chip, title="Credit Appraisal Workbench")

ss.setdefault("nav", STEPS[0])
choice = st.segmented_control("Step", STEPS, key="nav", label_visibility="collapsed")
idx = STEPS.index(choice) if choice in STEPS else ss.get("last_idx", 0)
ss.last_idx = idx
st.write("")

if idx == 0:
    from steps import loan
    loan.render()
elif idx == 1:
    from steps import security_step
    security_step.render(api_key)
elif idx == 2:
    from steps import kyc_step
    kyc_step.render(api_key)
elif idx == 3:
    from steps import financials
    financials.render(api_key, div, unit_label)
elif idx == 4:
    from steps import repayment_step
    repayment_step.render(div, unit_label)
else:
    from steps import summary
    summary.render(div, unit_label)

st.write("")
c1, _, c3 = st.columns([1, 3, 1])
if idx > 0:
    c1.button(f"← {STEPS[idx - 1][2:]}", on_click=go, args=(idx - 1,), use_container_width=True)
if idx < len(STEPS) - 1:
    c3.button(f"{STEPS[idx + 1][2:]} →", on_click=go, args=(idx + 1,), type="primary", use_container_width=True)
