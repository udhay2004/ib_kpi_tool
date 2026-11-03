"""Document checklist, ZIP intake and coverage check (what was asked for vs. what arrived)."""

import io
import re
import unicodedata
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


ARCHIVES = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
MB = 1024 * 1024


def clean_path(name: str) -> str:
    """A ZIP member name made safe to show and keep: no control characters, no '..' or absolute parts, forward
    slashes, at most the last three folders and 200 characters. Nothing is ever written to disk."""
    n = re.sub(r"[\x00-\x1f\x7f]", "", unicodedata.normalize("NFC", name)).replace("\\", "/")
    parts = [p for p in n.split("/") if p not in ("", ".", "..") and not re.fullmatch(r"[A-Za-z]:", p)]
    return "/".join(parts[-3:])[-200:]


def unzip(data: bytes, max_files: int = 300, max_total_mb: int = 200, max_file_mb: int = 50, max_ratio: int = 200):
    """Returns ([(path, bytes)], [skipped messages]). Nothing is written to disk.

    Guards against ZIP bombs and odd archives: at most max_files files, max_file_mb per file and max_total_mb in
    all once expanded (checked against what is actually read, not just the sizes the archive claims), members
    that expand more than max_ratio times are refused, archives inside the archive are skipped, names are cleaned,
    and a damaged or encrypted member is skipped with a message instead of failing the whole upload."""
    files, skipped, total = [], [], 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError):
        return [], ["Not a valid ZIP file"]
    with zf:
        infos = zf.infolist()
        if len(infos) > max_files * 10:
            return [], [f"ZIP has {len(infos)} entries - more than this app reads ({max_files} files)"]
        for info in infos:
            name = clean_path(info.filename)
            base = name.rsplit("/", 1)[-1]
            if info.is_dir() or not base or name.startswith("__MACOSX") or base.startswith((".", "~$")):
                continue
            ext = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
            if ext in ARCHIVES:
                skipped.append(f"{name}: archive inside the ZIP - extract it and upload separately")
                continue
            if ext not in SUPPORTED:
                skipped.append(f"{name}: unsupported file type")
                continue
            if len(files) >= max_files:
                skipped.append(f"{name}: ZIP limit reached ({max_files} files)")
                continue
            if info.file_size > max_file_mb * MB:
                skipped.append(f"{name}: larger than {max_file_mb} MB once expanded")
                continue
            if info.compress_size and info.file_size / info.compress_size > max_ratio and info.file_size > MB:
                skipped.append(f"{name}: compressed {info.file_size // max(info.compress_size, 1)}:1 - refused as a "
                               "possible ZIP bomb")
                continue
            if total + info.file_size > max_total_mb * MB:
                skipped.append(f"{name}: ZIP limit reached ({max_total_mb} MB in all)")
                continue
            try:
                with zf.open(info) as f:  # read at most the declared size + 1 byte, so a lying header can't overrun
                    content = f.read(info.file_size + 1)
                if len(content) > info.file_size:
                    skipped.append(f"{name}: size does not match the ZIP header - skipped")
                    continue
            except RuntimeError:
                skipped.append(f"{name}: ZIP is password protected - upload this file individually")
                continue
            except (zipfile.BadZipFile, NotImplementedError, OSError, EOFError, ValueError) as ex:
                skipped.append(f"{name}: could not be read from the ZIP ({type(ex).__name__})")
                continue
            total += len(content)
            files.append((name, content))
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
