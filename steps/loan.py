"""Step 1 - borrower profile and the facilities requested."""

import uuid

import streamlit as st

import loans
import ui
from common import facility_totals, inr, ss
from kyc import CONSTITUTIONS

MSME = ["Micro", "Small", "Medium", "Not MSME", "Not registered"]


def field_input(fac: dict, key: str, prefix: str):
    k, label, kind, default, help_ = loans.F[key]
    wkey = f"{prefix}_{k}"
    val = fac.get(k, default)
    if kind == "money":
        fac[k] = st.number_input(label, min_value=0.0, value=float(val or 0), step=100000.0, format="%.0f", key=wkey,
                                 help=help_)
        if fac[k]:
            st.caption(inr(fac[k]))
    elif kind in ("int", "days"):
        fac[k] = int(st.number_input(label, min_value=0, value=int(val or 0), step=1, key=wkey, help=help_))
    elif kind == "pct":
        fac[k] = st.number_input(label, min_value=0.0, max_value=100.0, value=float(val or 0), step=0.25, key=wkey,
                                 help=help_)
    elif kind.startswith("choice:"):
        opts = kind.split(":", 1)[1].split("|")
        fac[k] = st.selectbox(label, opts, index=opts.index(val) if val in opts else 0, key=wkey, help=help_)
    else:
        fac[k] = st.text_input(label, value=val or "", key=wkey, help=help_)


def render():
    case = ss.case
    b = case["borrower"]
    ui.section("Borrower", "Who is borrowing. The constitution decides the KYC checklist in step 3.")
    with st.container(border=True):
        st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([2, 1.4, 1])
        b["name"] = c1.text_input("Legal name of the borrower", value=b["name"], key="b_name", placeholder="e.g. Acme Polymers Pvt Ltd")
        b["constitution"] = c2.selectbox("Constitution", CONSTITUTIONS, index=CONSTITUTIONS.index(b["constitution"]), key="b_const")
        b["msme_class"] = c3.selectbox("MSME (Udyam)", MSME, index=MSME.index(b["msme_class"]), key="b_msme")
        c1, c2, c3 = st.columns([2, 1.4, 1])
        b["industry"] = c1.text_input("Industry / activity", value=b["industry"], key="b_ind", placeholder="e.g. Plastic packaging manufacturer")
        b["existing_exposure"] = c2.number_input("Existing bank exposure (₹, all lenders)", min_value=0.0,
                                                 value=float(b["existing_exposure"]), step=100000.0, format="%.0f", key="b_exist")
        b["years_in_business"] = int(c3.number_input("Years in business", min_value=0, value=int(b["years_in_business"]), key="b_years"))

    ui.section("Facilities requested", "Add each limit the client is asking for. Fund-based facilities involve "
               "lending money; non-fund-based ones are commitments (guarantees, LCs) that become loans only if invoked.")
    with st.container(border=True):
        st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
        cat = st.segmented_control("Category", list(loans.CATALOGUE), default="Fund based", key="new_cat") or "Fund based"
        groups = loans.CATALOGUE[cat]
        c1, c2 = st.columns(2)
        grp = c1.selectbox("Type", list(groups), key=f"new_grp_{cat}")
        prod = c2.selectbox("Facility", list(groups[grp]), key=f"new_prod_{cat}_{grp}")
        spec = groups[grp][prod]
        st.markdown(f'<div class="muted">Typical tenor: <b>{spec["tenor"]}</b> · Usual security: <b>{spec["security"]}</b></div>',
                    unsafe_allow_html=True)
        if st.button("＋ Add this facility", type="primary"):
            fac = {"id": uuid.uuid4().hex[:8], "product": prod}
            for f in spec["fields"]:
                k, _, _, default, _ = loans.F[f]
                fac[k] = default
            if cat == "Non-fund based":
                fac["rate_pct"] = 1.5  # guarantee / LC commission, not interest
                if "tenor_months" in fac:
                    fac["tenor_months"] = 12
            case["facilities"].append(fac)
            st.rerun()

    for i, fac in enumerate(case["facilities"]):
        p = loans.product(fac["product"])
        title = f"{i + 1}. {fac['product']} — {inr(fac.get('amount')) if fac.get('amount') else 'amount not set'}"
        with st.expander(title, expanded=not fac.get("amount")):
            st.markdown(ui.neutral_pill(p["category"]) + " " + ui.neutral_pill(p["group"]), unsafe_allow_html=True)
            fields = p["fields"]
            cols = st.columns(2)
            for j, f in enumerate(fields):
                with cols[j % 2]:
                    field_input(fac, f, f"fac_{fac['id']}")
            if st.button("Remove facility", key=f"rm_{fac['id']}"):
                case["facilities"].remove(fac)
                case["repay"].pop(fac["id"], None)
                st.rerun()

    if case["facilities"]:
        t = facility_totals(case["facilities"])
        ui.kpi_tiles([
            {"label": "Fund based", "value": inr(t["fund"])},
            {"label": "Non-fund based", "value": inr(t["nonfund"])},
            {"label": "Total proposed", "value": inr(t["total"])},
            {"label": "Total incl. existing", "value": inr(t["total"] + b["existing_exposure"]),
             "sub": "LEI needed (≥ ₹5 Cr)" if t["total"] + b["existing_exposure"] >= 5e7 else "Below LEI threshold"},
        ])
