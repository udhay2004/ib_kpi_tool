"""Document checklist, ZIP intake and coverage check (what was asked for vs. what arrived)."""

import io
import zipfile

import pandas as pd

from kpis import _fy_of

SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xlsx", ".xls", ".csv", ".txt", ".json"}

# What the analyst must ask the client for. Edit to match your firm's checklist.
CHECKLIST = [
    {"doc_type": "financials", "title": "Audited financial statements",
     "ask": "Last 2 financial years - balance sheet, P&L, cash flow, notes and auditor's report",
     "required": True},
    {"doc_type": "bank_statement", "title": "Bank statements",
     "ask": "Last 12 months, every current / CC / OD account (PDF downloaded from net banking)",
     "required": True},
    {"doc_type": "gst", "title": "GST returns",
     "ask": "GSTR-3B (and GSTR-1) for the last 12 months, plus GSTR-9 if filed",
     "required": True},
    {"doc_type": "itr", "title": "Director / promoter ITRs",
     "ask": "Optional - last 2 assessment years per director (ITR acknowledgement + computation of income)",
     "required": False},
    {"doc_type": "other", "title": "Other supporting documents",
     "ask": "Optional - sanction letters, loan statements, CMA data, MCA filings, provisional financials",
     "required": False},
]

KEYWORDS = [  # checked in order against the lower-cased file path
    ("gst", ("gst", "gstr", "3b")),
    ("itr", ("itr", "income tax", "incometax", "computation", "26as")),
    ("financials", ("balance sheet", "balancesheet", "audit", "financial", "p&l", "pnl", "profit",
                    "annual report")),
    ("bank_statement", ("bank", "statement", "stmt", "passbook", "hdfc", "icici", "sbi", "axis", "kotak",
                        "indusind", "canara", "pnb", "idfc")),
]


def guess_type(path: str) -> str:
    """Cheap first guess from the file/folder name; 'auto' lets Claude classify it."""
    p = path.lower().replace("-", " ").replace("_", " ")
    for doc_type, words in KEYWORDS:
        if any(w in p for w in words):
            return doc_type
    return "auto"


def unzip(data: bytes, max_files: int = 300, max_total_mb: int = 500):
    """Returns ([(path, bytes)], [skipped messages]). Nothing is written to disk."""
    files, skipped, total = [], [], 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [], ["Not a valid ZIP file"]
    with zf:
        for info in zf.infolist():
            name = info.filename
            base = name.rsplit("/", 1)[-1]
            if info.is_dir() or name.startswith("__MACOSX") or base.startswith((".", "~$")):
                continue
            ext = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
            if ext == ".zip":
                skipped.append(f"{name}: ZIP inside ZIP - extract it and upload separately")
                continue
            if ext not in SUPPORTED:
                skipped.append(f"{name}: unsupported file type")
                continue
            total += info.file_size
            if len(files) >= max_files or total > max_total_mb * 1024 * 1024:
                skipped.append(f"{name}: ZIP limit reached ({max_files} files / {max_total_mb} MB)")
                continue
            try:
                files.append((name, zf.read(info)))
            except RuntimeError:
                skipped.append(f"{name}: ZIP is password protected - upload this file individually")
    return files, skipped


def coverage(docs: list, fy_needed: int = 2, months_needed: int = 12,
             itr_years_needed: int = 2) -> pd.DataFrame:
    """docs: dicts with doc_type / status / extracted. One row per checklist item."""
    rows = []
    for item in CHECKLIST:
        kind = item["doc_type"]
        mine = [d for d in docs if d["doc_type"] == kind]
        read = [d.get("extracted") for d in mine if d.get("extracted")]
        status, detail = "❌ Missing", "Nothing uploaded"
        if mine and not read:
            status, detail = "⏳ Uploaded", f"{len(mine)} file(s) - not read yet"
        elif read:
            if kind == "financials":
                fys = sorted({y["fiscal_year"] for x in read for y in x.get("years", [])})
                ok = len(fys) >= fy_needed
                detail = f"Years found: {', '.join(fys) or 'none'} (need {fy_needed})"
            elif kind == "bank_statement":
                months = sorted({m["month"] for x in read for m in x.get("months", [])})
                accts = {f"{x.get('bank_name')} ..{x.get('account_number_last4')}" for x in read}
                ok = len(months) >= months_needed
                span = f"{months[0]} to {months[-1]}" if months else "none"
                detail = f"{len(months)} of {months_needed} months ({span}) · {len(accts)} account(s)"
            elif kind == "gst":
                months = sorted({p["period"] for x in read for p in x.get("periods", [])
                                 if "3B" in (p.get("return_type") or "").upper()})
                ok = len(months) >= months_needed
                fys = sorted({f for f in map(_fy_of, months) if f})
                detail = f"GSTR-3B for {len(months)} of {months_needed} months ({', '.join(fys) or '-'})"
            elif kind == "itr":
                people = {}
                for x in read:
                    people.setdefault(x.get("assessee_name") or "Unknown", set()).add(x.get("assessment_year"))
                complete = [p for p, ays in people.items() if len(ays) >= itr_years_needed]
                ok = len(complete) == len(people)
                detail = "; ".join(f"{p}: {', '.join(sorted(a for a in ays if a))}" for p, ays in people.items())
                detail += f" ({itr_years_needed} years each recommended)"
            else:
                ok, detail = True, f"{len(read)} file(s) read"
            status = "✅ Complete" if ok else "⚠️ Partial"
        if not item["required"] and status == "❌ Missing":
            status = "➖ Optional"
        rows.append({"Document": item["title"], "Status": status, "Detail": detail, "What to ask for": item["ask"]})
    unsorted = [d for d in docs if d["doc_type"] == "auto"]
    if unsorted:
        rows.append({"Document": "Not yet sorted", "Status": "⏳ Uploaded",
                     "Detail": f"{len(unsorted)} file(s) - Claude will identify the type when you extract",
                     "What to ask for": ""})
    return pd.DataFrame(rows)
