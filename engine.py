"""One credit case and everything worked out from it, with no UI: the files on the case, the analysis, repayment,
the summary, and the PDF / Excel / ZIP exports. server.py keeps one Workspace per browser session and turns
state() into JSON for the web front end."""

import io
import json
import math
import re
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import numpy as np
import pandas as pd

import cam_lite
import capacity
import credit_report
import kpis
import kyc
import loans
import repayment as rp
import security as sec
from demo_data import demo_case, demo_docs, demo_kyc, demo_security_docs
from extractor import SourceDoc, anthropic, extract, extract_with_schema
from intake import CHECKLIST, coverage, guess_type, unzip
from local_parse import extract as extract_local
from report import to_excel

UNITS = {"₹ Crore": 1e7, "₹ Lakh": 1e5, "₹": 1}
STORES = {"docs": "D", "kyc_docs": "K", "sec_docs": "S"}  # store -> document ID prefix
MSME = ["Micro", "Small", "Medium", "Not MSME", "Not registered"]
FIN_TYPES = {"auto": "Detect automatically", "financials": "Financial statements", "bank_statement": "Bank statement",
             "gst": "GST return", "itr": "ITR", "other": "Other"}
SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}
BENCH = {  # key -> (direction, threshold key, label)
    "dscr": (">=", "dscr_min", "≥ {t:.2f}x"), "current_ratio": (">=", "current_ratio_benchmark", "≥ {t:.2f}x"),
    "debt_equity": ("<=", "debt_equity_max", "≤ {t:.1f}x"), "tol_tnw": ("<=", "tol_tnw_max", "≤ {t:.1f}x"),
    "interest_coverage": (">=", "interest_coverage_min", "≥ {t:.1f}x"),
}


def new_case() -> dict:
    return {"borrower": {"name": "", "constitution": "Private limited company", "industry": "", "msme_class": "Not registered",
                         "years_in_business": 0, "existing_exposure": 0.0},
            "facilities": [], "securities": [], "repay": {}, "client": {}, "report": {},
            "dscr": {"growth_pct": 5.0, "tax_pct": 25.0, "wc_utilisation_pct": 80.0}}


# ---------------------------------------------------------------- formatting

def na(v) -> bool:
    try:
        return v is None or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def inr(v) -> str:
    if na(v):
        return "—"
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def money(v, div) -> str:
    return "—" if na(v) else f"{v / div:,.2f}"


def fmt(unit, v, div) -> str:
    if na(v):
        return "—"
    return {"%": f"{v:.1%}", "x": f"{v:.2f}x", "days": f"{v:.0f}"}.get(unit, money(v, div))


def pct(v) -> str:
    return "—" if na(v) else f"{v:.0%}"


def clean(o):
    """JSON-safe copy: numpy / pandas scalars to Python, NaN to None, dates to ISO strings."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, pd.DataFrame):
        return records(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if math.isnan(o) or math.isinf(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (date, datetime, pd.Timestamp)):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


def records(df: pd.DataFrame) -> list:
    if df is None or df.empty:
        return []
    return [clean(r) for r in df.astype(object).where(df.notna(), None).to_dict("records")]


def table(df: pd.DataFrame) -> dict:
    """{columns, rows} for display: every cell already a string."""
    if df is None or df.empty:
        return {"columns": [], "rows": []}
    return {"columns": [str(c) for c in df.columns],
            "rows": [["" if na(v) else str(v) for v in r] for r in df.astype(object).values.tolist()]}


# ---------------------------------------------------------------- workspace

class Workspace:
    def __init__(self):
        self.reset()

    def reset(self):
        self.case = new_case()
        self.stores = {s: {} for s in STORES}
        self.next = {s: 1 for s in STORES}
        self.demo = self.static_kyc = self.static_sec = None
        self.tables = None  # analyst-edited extracted tables
        self.zip_skipped = []
        self.last_run = {}
        self.api_key = None

    def load_demo(self):
        key = self.api_key
        self.reset()
        self.api_key = key
        self.case = {**new_case(), **demo_case()}
        self.demo, self.static_kyc, self.static_sec = demo_docs(), demo_kyc(), demo_security_docs()

    # ------------------------------------------------ files

    def add_file(self, store: str, name: str, data: bytes, doc_type: str, person: str | None = None):
        doc = SourceDoc(doc_id="", filename=name, data=data)
        if any(d.sha256 == doc.sha256 for d in self.stores[store].values()):
            return None
        if store == "kyc_docs":  # numbering continues after documents listed in a demo / saved case
            self.next[store] = max(self.next[store], len(self.static_kyc or []) + 1)
        doc.doc_id, doc.doc_type, doc.entity = f"{STORES[store]}{self.next[store]:02d}", doc_type, person or None
        self.next[store] += 1
        self.stores[store][doc.doc_id] = doc
        if store == "docs":
            self.demo, self.tables = None, None
        return doc

    def add_zip(self, data: bytes):
        files, self.zip_skipped = unzip(data)
        for path, content in files:
            self.add_file("docs", path, content, guess_type(path))

    def update_file(self, store: str, doc_id: str, doc_type: str | None = None, person: str | None = None):
        d = self.stores[store][doc_id]
        if doc_type is not None and doc_type != (d.doc_type or "auto"):
            d.doc_type = doc_type
            if store == "docs":
                d.extracted, d.warnings = None, []
                d.status = "pending" if d.status in ("extracted", "failed") else d.status
                self.tables = None
        if person is not None:
            d.entity = person.strip() or None

    def remove_file(self, store: str, doc_id: str):
        self.stores[store].pop(doc_id, None)
        if store == "docs":
            self.tables = None

    def unlock(self, store: str, doc_id: str, password: str) -> bool:
        d = self.stores[store][doc_id]
        if d.unlock(password):
            d.status, d.error = "pending", None
            return True
        return False

    def as_dicts(self, store: str, static: list | None = None) -> list:
        if static:
            return static
        return [{"doc_id": d.doc_id, "filename": d.filename, "doc_type": d.doc_type or "auto", "status": d.status,
                 "error": d.error, "warnings": d.warnings, "extracted": d.extracted} for d in self.stores[store].values()]

    def read(self, store: str, api_key: str | None):
        """Reads pending files: on this server first (financial documents), then with Claude when a key is set."""
        pending = [d for d in self.stores[store].values() if d.status in ("pending", "failed", "locked") and not d.encrypted]
        if store == "docs":
            names = self.related_names()
            for d in pending:
                extract_local(d, names)
            todo = [d for d in pending if d.status not in ("extracted", "locked")] if api_key else []
        else:
            todo = pending if api_key else []
        if todo:
            client = anthropic.Anthropic(api_key=api_key, max_retries=4)
            run = (lambda d: extract(client, d)) if store == "docs" else \
                (lambda d: extract_with_schema(client, d, sec.SECURITY_DOC_SCHEMA, sec.SECURITY_PROMPT))
            for d in todo:
                d.status, d.error = "pending", None
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(run, todo))
        failed = [d for d in pending if d.status != "extracted"]
        if store == "sec_docs" and not api_key:
            failed = []  # security documents are read only with Claude; without a key they are just attached
        self.last_run[store] = {"ok": len(pending) - len(failed), "failed": len(failed),
                                "errors": sorted({d.error for d in failed if d.error})}
        if store == "docs":
            self.tables = None

    # ------------------------------------------------ KYC

    def kyc_docs(self) -> list:
        docs = [dict(d, data=None) for d in self.static_kyc or []]
        docs += [{"doc_id": d.doc_id, "filename": d.filename, "doc_type": d.doc_type, "person": d.entity or "",
                  "data": d.data} for d in self.stores["kyc_docs"].values()]
        return docs

    def kyc_missing(self) -> list:
        have = {d["doc_type"] for d in self.kyc_docs()}
        return [kyc.ENTITY_DOCS[k] for k in kyc.REQUIRED[kyc.family(self.case["borrower"]["constitution"])] if k not in have]

    def kyc_index(self) -> pd.DataFrame:
        return pd.DataFrame([{"ID": d["doc_id"], "Document": kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]),
                              "Person": d.get("person") or "", "File": d["filename"],
                              "Included in ZIP": "Yes" if d["data"] else "No (listed in saved case only)"}
                             for d in self.kyc_docs()])

    # ------------------------------------------------ financials and repayment

    def fin_docs(self) -> list:
        return self.as_dicts("docs", self.demo)

    def related_names(self) -> list:
        return [self.case["borrower"]["name"]] + [d["person"] for d in self.kyc_docs() if d.get("person")]

    def base_tables(self):
        ready = [d for d in self.fin_docs() if d.get("extracted")]
        return kpis.build_tables(ready) if ready else None

    def analysis(self):
        docs = self.fin_docs()
        if not any(d.get("extracted") for d in docs):
            return None
        return kpis.analyse(docs, self.tables)

    def set_tables(self, edited: dict):
        """Analyst edits to the extracted tables: {financials|bank|gst|itr: [records]}."""
        base = self.base_tables()
        if base is None:
            return
        t = dict(base)
        for key, rows in edited.items():
            if key not in ("financials", "bank", "gst", "itr") or base[key].empty:
                continue
            df = pd.DataFrame(rows, columns=list(base[key].reset_index().columns) if key == "financials" else list(base[key].columns))
            if key == "financials":
                df = df.set_index("fiscal_year")
            for c in df.columns:
                if c in base[key].columns and pd.api.types.is_numeric_dtype(base[key][c]):
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            t[key] = df
        self.tables = t

    @staticmethod
    def latest(res) -> dict:
        if not res or res["fin_kpis"].empty:
            return {}
        fk, fin = res["fin_kpis"], res["tables"]["financials"]
        fy = [c for c in fk.columns if str(c).startswith("FY")][-1]
        r = fin.loc[fy]

        def g(k):
            v = r.get(k)
            return float(v) if v is not None and pd.notna(v) else None
        growth = fk.at["revenue_growth", fy]
        return {"fy": fy, "revenue": g("revenue_from_operations"), "growth": float(growth) if pd.notna(growth) else 0.0,
                "pat": g("profit_after_tax"), "depreciation": g("depreciation"), "finance_cost": g("finance_cost"),
                "current_maturities": g("current_maturities_of_long_term_debt"),
                "total_current_assets": g("total_current_assets"), "total_current_liabilities": g("total_current_liabilities"),
                "short_term_borrowings": g("short_term_borrowings")}

    def repay_cfg(self, fac: dict) -> dict:
        return self.case["repay"].setdefault(fac["id"], {"method": "EMI – equated instalments", "freq": "Monthly",
                                                         "step_up_pct": 10.0, "structured": "10,15,20,25,30"})

    def schedules(self, rate_add: float = 0.0) -> dict:
        out = {}
        for fac in self.case["facilities"]:
            p = loans.product(fac["product"])
            if p["repay"] not in ("amortising", "bullet") or not fac.get("amount") or not fac.get("tenor_months"):
                continue
            cfg = self.repay_cfg(fac)
            method = "Bullet" if p["repay"] == "bullet" and cfg["method"].startswith("EMI") else cfg["method"]
            pcts = [float(x) for x in str(cfg.get("structured", "")).split(",") if x.strip()]
            out[fac["id"]] = rp.schedule(fac["amount"], (fac.get("rate_pct") or 0) + rate_add, fac["tenor_months"], method,
                                         cfg["freq"], fac.get("moratorium_months") or 0, cfg["step_up_pct"], pcts)
        return out

    def assessments(self, fin: dict) -> dict:
        prop_value = sum(s.get("value") or 0 for s in self.case["securities"]
                         if sec.SECURITY_TYPES[s["kind"]][0] == "Collateral")
        return {f["id"]: loans.assess(f, fin, prop_value) for f in self.case["facilities"]}

    def wc_interest(self, rate_add: float = 0.0) -> float:
        u = self.case["dscr"]["wc_utilisation_pct"] / 100
        return sum((f.get("amount") or 0) * u * ((f.get("rate_pct") or 0) + rate_add) / 100
                   for f in self.case["facilities"] if loans.product(f["product"])["repay"] in ("revolving", "demand"))

    def dscr_table(self, fin: dict):
        d = self.case["dscr"]
        if not fin.get("pat") and fin.get("pat") != 0:
            return None
        return rp.dscr_projection(list(self.schedules().values()), (fin.get("pat") or 0) + (fin.get("depreciation") or 0),
                                  fin.get("finance_cost") or 0, fin.get("current_maturities") or 0, self.wc_interest(),
                                  d["growth_pct"], d["tax_pct"])

    def capacity_result(self, res):
        t = self.totals()
        return capacity.assess(res, self.case["facilities"], lambda r: list(self.schedules(r).values()), self.case["dscr"],
                               sec.cover(self.case["securities"], t["net_exposure"]), self.wc_interest)

    def totals(self) -> dict:
        f = self.case["facilities"]
        fund = sum(x.get("amount") or 0 for x in f if loans.is_fund(x))
        nonfund = sum(x.get("amount") or 0 for x in f if not loans.is_fund(x))
        return {"fund": fund, "nonfund": nonfund, "total": fund + nonfund, "net_exposure": sum(loans.exposure(x) for x in f)}

    # ------------------------------------------------ summary

    def build(self) -> dict:
        """Case summary across all steps (the input to the reports)."""
        case = self.case
        b = case["borrower"]
        res = self.analysis()
        fin = self.latest(res)
        t = self.totals()
        ass = self.assessments(fin)
        cov = sec.cover(case["securities"], t["net_exposure"])
        dt = self.dscr_table(fin) if fin else None
        cap = self.capacity_result(res) if case["facilities"] else None
        flags = []

        def add(sev, area, finding, step, next_step):
            flags.append({"severity": sev, "area": area, "finding": finding, "evidence": step, "next_step": next_step})

        if not case["facilities"]:
            add("High", "Proposal", "No facilities entered", "Step 1", "Add the facilities requested")
        fac_rows = []
        for f in case["facilities"]:
            p, a = loans.product(f["product"]), ass[f["id"]]
            method = self.repay_cfg(f)["method"] if p["repay"] in ("amortising", "bullet") else p["repay"].replace("_", "-").capitalize()
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
        for label in self.kyc_missing():
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
        n_kyc, n_missing = len(self.kyc_docs()), len(self.kyc_missing())
        fin_cov = coverage(self.fin_docs())
        steps = [
            ("Loan request", "good" if case["facilities"] and b["name"] else "critical"),
            ("Security", "good" if case["securities"] else "critical"),
            ("KYC", "good" if n_kyc and not n_missing else "warning" if n_kyc else "critical"),
            ("Financials", "good" if res is not None and not fin_cov["Status"].str.startswith("❌").any()
             else "warning" if res is not None else "critical"),
            ("Repayment", cap["verdict"]["status"] if cap is not None else "warning" if case["facilities"] else "critical"),
        ]
        return {"case": case, "res": res, "fin": fin, "totals": t, "cover": cov, "dscr": dt, "flags": pd.DataFrame(flags),
                "facilities": pd.DataFrame(fac_rows), "steps": steps, "capacity": cap, "assessments": ass}

    # ------------------------------------------------ save / open

    def save_case(self) -> str:
        def ser(o):
            if isinstance(o, (date, datetime)):
                return o.isoformat()
            if isinstance(o, set):
                return list(o)
            return str(o)
        payload = {"version": 1, "saved": datetime.now().isoformat(timespec="seconds"), "case": self.case,
                   "financial_docs": self.fin_docs(),
                   "kyc_docs": [{k: v for k, v in d.items() if k != "data"} for d in self.kyc_docs()],
                   "security_docs": self.as_dicts("sec_docs", self.static_sec)}
        return json.dumps(payload, default=ser, indent=1)

    def load_case(self, raw: bytes):
        data = json.loads(raw)
        key = self.api_key
        self.reset()
        self.api_key = key
        case = {**new_case(), **data["case"]}
        for s in case.get("securities", []):
            if isinstance(s.get("valuation_date"), str) and s["valuation_date"]:
                s["valuation_date"] = date.fromisoformat(s["valuation_date"][:10])
        self.case = case
        self.demo = [d for d in data.get("financial_docs", []) if d.get("extracted")] or None
        self.static_kyc = [{"doc_id": d["doc_id"], "filename": d["filename"], "person": d.get("person") or "",
                            "doc_type": d["doc_type"] if d.get("doc_type") in kyc.ALL_KINDS
                            else (d.get("extracted") or {}).get("doc_kind", "other")}
                           for d in data.get("kyc_docs", [])] or None
        self.static_sec = [d for d in data.get("security_docs", []) if d.get("extracted")] or None

    def set_case(self, case: dict):
        """The case as edited in the browser. Dates arrive as ISO strings."""
        for s in case.get("securities", []):
            v = s.get("valuation_date")
            s["valuation_date"] = date.fromisoformat(v[:10]) if isinstance(v, str) and v else None
        self.case = {**new_case(), **case}

    # ------------------------------------------------ exports

    def entity_details(self) -> dict:
        out, banks = {}, []
        for d in self.fin_docs():
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

    def _info(self) -> dict:
        return {**self.case.get("report", {}), "date": datetime.now().strftime("%d %b %Y")}

    def report_pdf(self, s: dict) -> bytes:
        def files(store, static):
            return [(d.doc_id, d.filename, d.data) for d in self.stores[store].values()] or \
                [(d["doc_id"], d["filename"], None) for d in static or []]
        return credit_report.build_pdf(s, self._info(), self.kyc_docs(), self.kyc_missing(),
                                       {"Financial documents": files("docs", self.demo),
                                        "Security documents": files("sec_docs", self.static_sec)},
                                       self.schedules() if s["case"]["facilities"] else {}, self.entity_details())

    def cam_pdf(self, s: dict) -> bytes:
        return cam_lite.build_pdf(s, self._info(), {d["doc_type"] for d in self.kyc_docs()},
                                  {d["doc_type"] for d in self.fin_docs() if d.get("extracted")})

    def excel(self, s: dict) -> bytes:
        case, b, t, cov, dt, cap = s["case"], s["case"]["borrower"], s["totals"], s["cover"], s["dscr"], s["capacity"]
        extra = {
            "Proposal": s["facilities"],
            "Security": pd.DataFrame([{**{k: v for k, v in x.items() if k != "id"}, "realisable": sec.realisable(x)}
                                      for x in case["securities"]]),
            "Repayment capacity": capacity.report_rows(cap) if cap is not None else pd.DataFrame(),
            "All findings": s["flags"], "KYC documents": self.kyc_index(),
            "Repayment DSCR": dt if dt is not None else pd.DataFrame(),
        }
        rows = [("Borrower", b["name"]), ("Constitution", b["constitution"]), ("Industry", b["industry"]),
                ("MSME", b["msme_class"]), ("Total proposed (₹)", t["total"]), ("Fund based (₹)", t["fund"]),
                ("Non-fund based (₹)", t["nonfund"]), ("Security cover (x)", cov["cover"]),
                ("Min DSCR on proposal (x)", None if dt is None or dt.empty else float(dt["dscr"].min())),
                ("Repayment capacity", cap["verdict"]["label"] if cap is not None else "Not assessed")]
        empty = {"tables": {"financials": pd.DataFrame(), "bank": pd.DataFrame(), "gst": pd.DataFrame(), "itr": pd.DataFrame()},
                 "fin_kpis": pd.DataFrame(), "bank_kpis": pd.DataFrame(), "gst_kpis": pd.DataFrame(),
                 "flags": pd.DataFrame(columns=["severity"])}
        return to_excel(s["res"] or empty, pd.DataFrame(), b["name"] or "Borrower", coverage(self.fin_docs()),
                        extra=extra, summary=rows)

    def credit_file_zip(self, xls: bytes, name: str, pdf: bytes | None = None) -> bytes:
        """Credit report (PDF), credit note (Excel), every attached KYC file and the other source files."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            if pdf:
                z.writestr(f"{name}_credit_report.pdf", pdf)
            z.writestr(f"{name}_credit_note.xlsx", xls)
            for folder, store in (("Financial documents", "docs"), ("Security documents", "sec_docs")):
                for d in self.stores[store].values():
                    z.writestr(f"{folder}/{safe(d.doc_id + ' - ' + d.filename.rsplit('/', 1)[-1])}", d.data)
            for d in self.kyc_docs():
                if not d["data"]:
                    continue
                label = kyc.ALL_KINDS.get(d["doc_type"], "Other").split(" (")[0]
                parts = [d["doc_id"], label] + ([d["person"]] if d.get("person") else [])
                z.writestr(f"KYC/{safe(' - '.join(parts + [d['filename'].rsplit('/', 1)[-1]]))}", d["data"])
        return buf.getvalue()

    def file_name(self) -> str:
        return re.sub(r"[^\w.-]+", "_", self.case["borrower"]["name"] or "Case").strip("_") or "Case"

    # ------------------------------------------------ view model

    def state(self, unit: str = "₹ Crore", server_key: bool = False) -> dict:
        from views import state  # the view model is built in views.py
        return state(self, unit, server_key)


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", name)[:150]


def new_id() -> str:
    return uuid.uuid4().hex[:8]


__all__ = ["Workspace", "UNITS", "CHECKLIST", "FIN_TYPES", "MSME", "BENCH", "inr", "money", "fmt", "pct", "clean", "records",
           "table", "na", "new_id"]
