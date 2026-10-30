"""Step 2 - security offered by the client."""

import uuid
from datetime import date

import pandas as pd
import streamlit as st

import security as sec
import ui
from common import add_file, as_dicts, doc_table, facility_totals, inr, new_uploads, read_button, ss
from extractor import extract_with_schema
from intake import SUPPORTED

EXTS = sorted(e.strip(".") for e in SUPPORTED)


def _new(kind: str, **kw) -> dict:
    return {"id": uuid.uuid4().hex[:8], "kind": kind, "description": "", "owner": "", "owner_is_third_party": False,
            "value": 0.0, "valuation_date": None, "valuer": "", "existing_charge": "",
            "margin_pct": float(sec.SECURITY_TYPES[kind][2]), **kw}


def _from_doc(x: dict) -> dict:
    vd = None
    try:
        vd = date.fromisoformat(x.get("valuation_date") or "")
    except ValueError:
        pass
    return _new(x["suggested_security_kind"], description=x.get("description") or "", owner=x.get("owner") or "",
                value=float(x.get("market_value") or 0), valuation_date=vd, valuer=x.get("valuer") or "",
                existing_charge=x.get("existing_charge_or_encumbrance") or "")


def render(api_key):
    case = ss.case
    ui.section("Security offered", "What the client offers as primary security, collateral and guarantees. "
               "Margins turn market value into realisable value — change them to your lender's policy.")
    with st.container(border=True):
        st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        kind = c1.selectbox("Add a security", list(sec.SECURITY_TYPES), key="new_sec_kind")
        if c2.button("＋ Add", type="primary", use_container_width=True):
            case["securities"].append(_new(kind))
            st.rerun()
        cls, charge, margin, docs = sec.SECURITY_TYPES[kind]
        st.markdown(f'<div class="muted">{cls} · {charge} · typical margin {margin}% · '
                    f'Documents: {docs}</div>', unsafe_allow_html=True)

    for s in case["securities"]:
        cls, charge, _, docs = sec.SECURITY_TYPES[s["kind"]]
        head = f"{s['kind']} — {inr(s['value']) if s['value'] else 'value not set'}"
        with st.expander(head, expanded=not s["value"] and cls != "Guarantee"):
            st.markdown(ui.neutral_pill(cls) + " " + ui.neutral_pill(charge), unsafe_allow_html=True)
            k = f"sec_{s['id']}"
            c1, c2 = st.columns(2)
            s["description"] = c1.text_input("Description / address / details", s["description"], key=f"{k}_d")
            s["owner"] = c2.text_input("Owner / guarantor", s["owner"], key=f"{k}_o")
            if cls != "Guarantee":
                c1, c2, c3 = st.columns(3)
                s["value"] = c1.number_input("Market value (₹)", min_value=0.0, value=float(s["value"]), step=100000.0,
                                             format="%.0f", key=f"{k}_v")
                s["margin_pct"] = c2.number_input("Margin / haircut (%)", 0.0, 100.0, float(s["margin_pct"]), 5.0, key=f"{k}_m")
                c3.metric("Realisable value", inr(sec.realisable(s)))
                c1, c2, c3 = st.columns(3)
                s["valuation_date"] = c1.date_input("Valuation date", s["valuation_date"], key=f"{k}_vd", format="DD/MM/YYYY")
                s["valuer"] = c2.text_input("Valuer", s["valuer"], key=f"{k}_vr")
                s["existing_charge"] = c3.text_input("Existing charge (if any)", s["existing_charge"], key=f"{k}_c",
                                                     placeholder="e.g. Mortgaged to SBI")
            else:
                s["value"] = st.number_input("Guarantor net worth (₹, optional)", min_value=0.0, value=float(s["value"]),
                                             step=100000.0, format="%.0f", key=f"{k}_v")
            s["owner_is_third_party"] = st.checkbox("Owned by a third party (not the borrower)", s["owner_is_third_party"],
                                                    key=f"{k}_tp")
            st.caption(f"Collect: {docs}")
            if st.button("Remove", key=f"{k}_rm"):
                case["securities"].remove(s)
                st.rerun()

    ui.section("Security documents (optional)", "Upload valuation reports, title deeds, FD receipts. They "
               "go into the credit file. With an API key, Claude can also read them so you can add each one as a "
               "security with one click; without one, enter the values above.")
    with st.container(border=True):
        ui.upload_header("🏠", "Valuation reports, title deeds, encumbrance certificates", "PDF, image or Excel",
                         False, len(ss.sec_docs))
        fresh = new_uploads(st.file_uploader("Security documents", type=EXTS, accept_multiple_files=True,
                                             key=f"up_sec_{ss.upload_round}", label_visibility="collapsed"))
        for f in fresh:
            add_file("sec_docs", f.name, f.getvalue(), "security")
        if fresh:
            st.rerun()
    doc_table("sec_docs")
    if ss.sec_docs:
        read_button("sec_docs", api_key,
                    lambda c, d: extract_with_schema(c, d, sec.SECURITY_DOC_SCHEMA, sec.SECURITY_PROMPT))
    for d in as_dicts("sec_docs", "static_sec"):
        x = d.get("extracted")
        if not x:
            continue
        with st.container(border=True):
            st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
            c1, c2 = st.columns([4, 1], vertical_alignment="center")
            c1.markdown(f"**{d['doc_id']} · {x.get('document_type')}** — {x.get('suggested_security_kind')}  \n"
                        f"<span class='muted'>{x.get('description') or ''} · owner {x.get('owner') or '—'} · "
                        f"market value {inr(x.get('market_value'))} · valued {x.get('valuation_date') or '—'}"
                        f"{' · ⚠️ ' + '; '.join(x['issues']) if x.get('issues') else ''}</span>", unsafe_allow_html=True)
            added = any(s.get("from_doc") == d["doc_id"] for s in case["securities"])
            if c2.button("Added ✓" if added else "Add as security", key=f"add_{d['doc_id']}", disabled=added,
                         use_container_width=True):
                case["securities"].append({**_from_doc(x), "from_doc": d["doc_id"]})
                st.rerun()

    if case["securities"] or case["facilities"]:
        t = facility_totals(case["facilities"])
        c = sec.cover(case["securities"], t["net_exposure"])
        ui.section("Cover", "Realisable value against exposure (non-fund limits net of cash margin).")
        status = None if c["cover"] is None else ("good" if c["cover"] >= 1.25 else "warning" if c["cover"] >= 1 else "critical")
        ui.kpi_tiles([
            {"label": "Exposure to cover", "value": inr(t["net_exposure"])},
            {"label": "Market value", "value": inr(c["market_value"])},
            {"label": "Realisable value", "value": inr(c["realisable_value"])},
            {"label": "Security cover", "value": "—" if c["cover"] is None else f"{c['cover']:.2f}x",
             "status": status, "bench": "≥ 1.25x"},
        ])
        flags = sec.security_flags(case["securities"], t["net_exposure"], case["borrower"]["msme_class"], t["fund"])
        if flags:
            st.write("")
            ui.flag_cards(pd.DataFrame([{"severity": a, "area": "Security", "finding": b, "evidence": "Step 2",
                                         "next_step": n} for a, b, n in flags]))
