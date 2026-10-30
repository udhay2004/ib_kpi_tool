"""Step 3 - KYC documents of the entity and its directors / partners / guarantors.

KYC is collected and attached to the credit file as documentation. It is not read or verified here.
"""

from html import escape

import pandas as pd
import streamlit as st

import kyc
import ui
from common import add_file, new_uploads, ss
from intake import SUPPORTED

EXTS = sorted(e.strip(".") for e in SUPPORTED)
LABEL_TO_KIND = {v: k for k, v in kyc.ALL_KINDS.items()}


def kyc_docs() -> list:
    """Every KYC document on the case: uploaded files (with bytes) plus any from a demo / saved case (names only)."""
    docs = [dict(d, data=None) for d in ss.get("static_kyc") or []]
    docs += [{"doc_id": d.doc_id, "filename": d.filename, "doc_type": d.doc_type, "person": d.entity or "",
              "data": d.data} for d in ss.kyc_docs.values()]
    return docs


def missing(case=None) -> list:
    """Required entity documents with nothing attached, as labels."""
    case = case or ss.case
    have = {d["doc_type"] for d in kyc_docs()}
    return [kyc.ENTITY_DOCS[k] for k in kyc.REQUIRED[kyc.family(case["borrower"]["constitution"])] if k not in have]


def index_table() -> pd.DataFrame:
    """One row per attached document, for the credit note and the ZIP."""
    return pd.DataFrame([{"ID": d["doc_id"], "Document": kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]),
                          "Person": d.get("person") or "", "File": d["filename"],
                          "Included in ZIP": "Yes" if d["data"] else "No (listed in saved case only)"}
                         for d in kyc_docs()])


def _uploader(label, person: bool, who: str = ""):
    fresh = new_uploads(st.file_uploader(label, type=EXTS, accept_multiple_files=True,
                                         key=f"up_kyc_{'p' if person else 'e'}_{ss.upload_round}",
                                         label_visibility="collapsed"))
    for f in fresh:
        doc = add_file("kyc_docs", f.name, f.getvalue(), kyc.guess_kind(f.name, person))
        if doc is not None and person:
            doc.entity = who.strip() or None
    if fresh:
        ss.files_ver += 1
        st.rerun()


def _table():
    docs = ss.kyc_docs
    if not docs:
        return
    table = pd.DataFrame([{"key": k, "ID": d.doc_id, "File": d.filename.rsplit("/", 1)[-1],
                           "Document": kyc.ALL_KINDS.get(d.doc_type, "Other"), "Person": d.entity or "", "✕": False}
                          for k, d in docs.items()]).set_index("key")
    edited = st.data_editor(
        table, key=f"tbl_kyc_{ss.files_ver}", use_container_width=True, hide_index=True, disabled=["ID", "File"],
        column_config={"File": st.column_config.TextColumn("File", width="medium"),
                       "Document": st.column_config.SelectboxColumn("Document", options=list(kyc.ALL_KINDS.values()),
                                                                    required=True, width="large"),
                       "Person": st.column_config.TextColumn("Person", help="Director / partner / guarantor, for "
                                                             "personal documents"),
                       "✕": st.column_config.CheckboxColumn("✕", width="small", help="Remove")})
    changed = False
    for key, row in edited.iterrows():
        d = docs.get(key)
        if d is None:
            continue
        if row["✕"]:
            del docs[key]
            changed = True
        elif LABEL_TO_KIND[row["Document"]] != d.doc_type or (row["Person"] or "") != (d.entity or ""):
            d.doc_type, d.entity = LABEL_TO_KIND[row["Document"]], row["Person"] or None
            changed = True
    if changed:
        ss.files_ver += 1
        st.rerun()


def render(api_key=None):
    b = ss.case["borrower"]
    fam = kyc.family(b["constitution"])
    # New uploads continue numbering after documents listed in a demo / saved case.
    ss.kyc_docs_next = max(ss.kyc_docs_next, len(ss.get("static_kyc") or []) + 1)
    docs = kyc_docs()
    have = {}
    for d in docs:
        have.setdefault(d["doc_type"], []).append(d["doc_id"])

    ui.section("KYC checklist", f"Documents to collect for a {b['constitution'].lower()}. They are attached to the "
               "credit file as documentation; the app does not read or verify them.")
    rows = []
    for k in kyc.REQUIRED[fam]:
        got = have.get(k)
        rows.append((kyc.ENTITY_DOCS[k], ui.pill("good", "Attached · " + ", ".join(got)) if got
                     else ui.neutral_pill("Not attached")))
    for k in ("udyam", "lei"):
        if k not in kyc.REQUIRED[fam]:
            got = have.get(k)
            rows.append((kyc.ENTITY_DOCS[k] + ' <span class="muted">(if applicable)</span>',
                         ui.pill("good", "Attached · " + ", ".join(got)) if got else ui.neutral_pill("—")))
    st.markdown('<div class="card" style="padding:4px 16px">' + "".join(
        f'<div class="cl-row" style="grid-template-columns:2fr 1.4fr"><div class="cl-name">{k}</div><div>{v}</div></div>'
        for k, v in rows) + '<div class="muted" style="margin:10px 0 8px">For every director, partner, guarantor and '
        'beneficial owner (&gt;10% holding): PAN and one ID / address proof (masked Aadhaar, passport, voter ID or '
        'driving licence), plus a photograph.</div></div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2, gap="medium")
    with c1.container(border=True):
        ui.upload_header("🏢", "Entity documents", "Certificate of incorporation, MOA, AOA, PAN, GST certificate, "
                         "board resolution, shareholding, address proof, Udyam, LEI…", True,
                         sum(1 for d in ss.kyc_docs.values() if d.doc_type in kyc.ENTITY_DOCS))
        _uploader("Entity KYC", person=False)
    with c2.container(border=True):
        ui.upload_header("👥", "Directors, partners & guarantors", "PAN, masked Aadhaar, passport / voter ID / DL, "
                         "photo. Type the person's name, then upload their documents.", True,
                         sum(1 for d in ss.kyc_docs.values() if d.doc_type in kyc.PERSON_DOCS))
        who = st.text_input("Person", key="kyc_person", placeholder="Name of the director / partner / guarantor")
        _uploader("Individual KYC", person=True, who=who)

    if ss.kyc_docs:
        ui.section("Attached documents", "The document type is guessed from the file name. Correct it here if needed.")
        _table()
    static = ss.get("static_kyc") or []
    if static:
        st.caption("From the demo / saved case (names only, no files): " +
                   ", ".join(escape(f"{d['doc_id']} {kyc.ALL_KINDS.get(d['doc_type'], d['doc_type'])}"
                                    + (f" – {d['person']}" if d.get("person") else "")) for d in static))

    people = sorted({d.get("person") for d in docs if d.get("person")})
    if people:
        ui.section("People")
        st.dataframe(pd.DataFrame([{"Person": p, "Documents attached": ", ".join(
            kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]) for d in docs if d.get("person") == p)} for p in people]),
            hide_index=True, use_container_width=True)
