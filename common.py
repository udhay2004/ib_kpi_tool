"""Shared state, formatting and the document upload / read component used by every step."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import streamlit as st

from extractor import SourceDoc, anthropic

ss = st.session_state

UNITS = {"₹ Crore": 1e7, "₹ Lakh": 1e5, "₹": 1}
STORES = {"docs": "D", "kyc_docs": "K", "sec_docs": "S"}  # store -> document ID prefix


def init_state():
    defaults = {
        "step": 0, "tables": None, "seen": set(), "zip_skipped": [], "files_ver": 0, "upload_round": 0,
        "demo": None, "static_kyc": None, "static_sec": None, "last_run": {},
        "case": {"borrower": {"name": "", "constitution": "Private limited company", "industry": "",
                              "msme_class": "Not registered", "years_in_business": 0, "existing_exposure": 0.0},
                 "facilities": [], "securities": [], "repay": {},
                 "dscr": {"growth_pct": 5.0, "tax_pct": 25.0, "wc_utilisation_pct": 80.0}},
    }
    for k, v in defaults.items():
        ss.setdefault(k, v)
    for store, prefix in STORES.items():
        ss.setdefault(store, {})
        ss.setdefault(f"{store}_next", 1)


def reset_all():
    keep = {"authed": ss.get("authed")}
    round_ = ss.get("upload_round", 0) + 1
    ss.clear()
    ss.update(keep)
    init_state()
    ss.upload_round = round_  # fresh, empty upload widgets


def secret(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    # Touching st.secrets without a secrets.toml renders an error box, so check first.
    if not any(p.exists() for p in (Path(".streamlit/secrets.toml"), Path.home() / ".streamlit/secrets.toml")):
        return None
    try:
        return st.secrets.get(name)
    except Exception:
        return None


# ---------------------------------------------------------------- formatting

def money(v, div):
    return "—" if v is None or pd.isna(v) else f"{v / div:,.2f}"


def fmt(unit, v, div):
    if v is None or pd.isna(v):
        return "—"
    return {"%": f"{v:.1%}", "x": f"{v:.2f}x", "days": f"{v:.0f}"}.get(unit, money(v, div))


def pct(v):
    return "—" if v is None or pd.isna(v) else f"{v:.0%}"


def inr(v):
    if v is None or pd.isna(v):
        return "—"
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


# ---------------------------------------------------------------- documents

def add_file(store: str, name: str, data: bytes, doc_type: str) -> SourceDoc | None:
    """Add a file to a store; returns the new doc, or None if the same file is already there."""
    doc = SourceDoc(doc_id="", filename=name, data=data)
    if doc.sha256 in ss[store]:
        return None
    doc.doc_id, doc.doc_type = f"{STORES[store]}{ss[f'{store}_next']:02d}", doc_type
    ss[f"{store}_next"] += 1
    ss[store][doc.sha256] = doc
    if store == "docs":
        ss.demo, ss.tables = None, None
    return doc


def new_uploads(files) -> list:
    """Uploaders return the same files on every rerun; handle each once."""
    fresh = [f for f in files or [] if f.file_id not in ss.seen]
    ss.seen.update(f.file_id for f in fresh)
    return fresh


def as_dicts(store: str, static_key: str | None = None) -> list:
    if static_key and ss.get(static_key):
        return ss[static_key]
    return [{"doc_id": d.doc_id, "filename": d.filename, "doc_type": d.doc_type or "auto", "status": d.status,
             "error": d.error, "warnings": d.warnings, "extracted": d.extracted} for d in ss[store].values()]


STATUS_TXT = {"pending": "⏳ Ready", "extracted": "✅ Read", "failed": "❌ Failed", "locked": "🔒 Password",
              "unreadable": "❌ Unreadable"}


def doc_table(store: str, type_labels: dict | None = None, describe=None):
    """Editable list of uploaded files: status, type (optional), note, remove."""
    docs = ss[store]
    if not docs:
        return
    rows = []
    for k, d in docs.items():
        row = {"key": k, "ID": d.doc_id, "File": d.filename.rsplit("/", 1)[-1],
               "Status": "🔒 Password" if d.encrypted else STATUS_TXT.get(d.status, d.status)}
        if type_labels:
            row["Type"] = type_labels.get(d.doc_type or "auto", d.doc_type)
        if describe:
            row["Detected"] = describe(d)
        row.update({"Note": d.error or "; ".join(d.warnings) or "", "Pages": d.pages, "✕": False})
        rows.append(row)
    table = pd.DataFrame(rows).set_index("key")
    cfg = {"File": st.column_config.TextColumn("File", width="medium"),
           "Note": st.column_config.TextColumn("Note", width="large"),
           "✕": st.column_config.CheckboxColumn("✕", width="small", help="Remove"),
           "Pages": st.column_config.NumberColumn("Pages", width="small")}
    if type_labels:
        cfg["Type"] = st.column_config.SelectboxColumn("Type", options=list(type_labels.values()), required=True)
    edited = st.data_editor(table, key=f"tbl_{store}_{ss.files_ver}", use_container_width=True, hide_index=True,
                            disabled=[c for c in table.columns if c not in ("Type", "✕")], column_config=cfg)
    changed = False
    to_type = {v: k for k, v in (type_labels or {}).items()}
    for key, row in edited.iterrows():
        d = docs.get(key)
        if d is None:
            continue
        if row["✕"]:
            del docs[key]
            changed = True
            continue
        if type_labels:
            new_type = to_type[row["Type"]]
            if new_type != (d.doc_type or "auto"):
                d.doc_type, d.extracted, d.warnings = new_type, None, []
                d.status = "pending" if d.status in ("extracted", "failed") else d.status
                changed = True
    if changed:
        ss.files_ver += 1
        if store == "docs":
            ss.tables = None
        st.rerun()

    locked = [d for d in docs.values() if d.encrypted]
    if locked:
        with st.container(border=True):
            st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
            st.markdown("**🔒 Password-protected PDFs** — often the PAN in capitals, or name + date of birth. "
                        "Unlocking happens on this server; the password is not stored.")
            for d in locked:
                c1, c2 = st.columns([3, 1])
                pw = c1.text_input(d.filename, type="password", key=f"pw_{d.sha256}", placeholder="PDF password")
                if c2.button("Unlock", key=f"unlock_{d.sha256}", use_container_width=True) and pw:
                    if d.unlock(pw):
                        d.status, d.error = "pending", None
                        ss.files_ver += 1
                        st.rerun()
                    st.error("Wrong password")


def read_button(store: str, api_key: str | None, run_one, hint: str = "", local_one=None):
    """'Read N documents' button. local_one(doc) reads a doc without any API (tried first when given);
    run_one(client, doc) reads it with Claude, used for what the local reader can't handle when a key is set."""
    docs = ss[store]
    pending = [d for d in docs.values() if d.status in ("pending", "failed", "locked") and not d.encrypted]
    last = ss.last_run.get(store)
    if last:
        ok, bad, errors = last
        if bad:
            st.error(f"{ok} read, {bad} failed. " + " · ".join(errors))
        else:
            st.success(f"All {ok} document(s) read.")
    c1, c2 = st.columns([2, 3], vertical_alignment="center")
    noun = f"{len(pending)} document{'s' if len(pending) != 1 else ''}"
    go = c1.button(f"Read {noun}  →" if local_one else f"Read {noun} with Claude  →", type="primary",
                   disabled=not pending, use_container_width=True, key=f"read_{store}")
    if hint:
        c2.markdown(f'<span class="muted">{hint}</span>', unsafe_allow_html=True)
    if not go:
        return
    if not local_one and not api_key:
        st.error("Add an Anthropic API key in Settings (left sidebar) first.")
        return
    with st.status(f"Reading {len(pending)} document(s)…", expanded=True) as status:
        todo = pending
        if local_one:
            for d in pending:
                local_one(d)
                if d.status == "extracted" or not api_key:
                    st.write(("✅ " if d.status == "extracted" else "❌ ") + d.filename +
                             (f" — {d.error}" if d.error else " — read on this server"))
            todo = [d for d in pending if d.status != "extracted" and d.status != "locked"] if api_key else []
        if todo:
            client = anthropic.Anthropic(api_key=api_key, max_retries=4)
            for d in todo:
                d.status, d.error = "pending", None
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(run_one, client, d) for d in todo]
                for fut in as_completed(futs):
                    d = fut.result()
                    st.write(("✅ " if d.status == "extracted" else "❌ ") + d.filename +
                             (f" — {d.error}" if d.error else " — read with Claude"))
        failed = [d for d in pending if d.status != "extracted"]
        status.update(label=f"Done — {len(pending) - len(failed)} read, {len(failed)} failed",
                      state="error" if failed else "complete")
    ss.last_run[store] = (len(pending) - len(failed), len(failed), sorted({d.error for d in failed if d.error}))
    ss.files_ver += 1
    if store == "docs":
        ss.tables = None
    st.rerun()


def facility_totals(facilities: list) -> dict:
    import loans
    fund = sum(f.get("amount") or 0 for f in facilities if loans.is_fund(f))
    nonfund = sum(f.get("amount") or 0 for f in facilities if not loans.is_fund(f))
    net = sum(loans.exposure(f) for f in facilities)
    return {"fund": fund, "nonfund": nonfund, "total": fund + nonfund, "net_exposure": net}
