"""Credit Appraisal Memo (CAM-Lite): the Connect Ventures internal deal assessment, prepared before lender login.
Internal only, so it is a separate PDF and never part of the bank report or the credit file ZIP.

Pure module (no UI). Filled from the case where the app has the data, otherwise from what was typed in.
"""

import fitz
import pandas as pd

import checklist
import credit_report as cr
import kpis
from credit_report import _na, e, escape, kv_table, lakh, table


def _kv(pairs: list) -> str:
    return kv_table(pairs)


def _deal(s, info) -> str:
    case, c = s["case"], s["case"].get("client") or {}
    b = case["borrower"]
    fac = "<br>".join(e(f"{f['product']}, {cr.inr(f.get('amount'))}" + (f", {f['tenor_months']} months" if f.get("tenor_months") else ""))
                      for f in case["facilities"]) or "—"
    purpose = "<br>".join(e(f.get("purpose")) for f in case["facilities"]) or "—"
    secs = "<br>".join(e(f"{x['kind']}" + (f": {x['description']}" if x.get("description") else "")) for x in case["securities"]) or "—"
    return "<h1>1. Deal Summary</h1>" + _kv([
        ("Client & constitution", e(f"{b['name'] or 'Unnamed'}, {b['constitution']}")),
        ("Facility requested (product, amount, tenor)", fac), ("Purpose / end use", purpose),
        ("Primary repayment source", e(info.get("repayment_source"))), ("Security offered", secs),
        ("Proposed lenders (primary / fallback)", e(info.get("proposed_lenders"))),
        ("Prepared by / date", e(" / ".join(x for x in (info.get("prepared_by"), info.get("date")) if x)))])


def _profile(s) -> str:
    b, c = s["case"]["borrower"], s["case"].get("client") or {}
    promoters = "<br>".join(e(", ".join(str(x) for x in (
        p.get("Name"), p.get("Designation"), f"{p['Experience yrs']:g} yrs" if not _na(p.get("Experience yrs")) else None,
        p.get("Qualification")) if x)) for p in c.get("promoters") or []) or "—"

    def share(rows, col):
        return "; ".join(f"{r.get('Name')} ({r[col]:g}%)" if not _na(r.get(col)) else str(r.get("Name")) for r in rows or [])
    parties = " | ".join(x for x in (f"Customers: {share(c.get('customers'), '% of sales')}" if c.get("customers") else "",
                                     f"Suppliers: {share(c.get('suppliers'), '% of purchases')}" if c.get("suppliers") else "") if x)
    return "<h1>2. Borrower & Management Profile</h1>" + _kv([
        ("Business activity & products", e(c.get("nature") or b["industry"])),
        ("Vintage & brief history", e(c.get("history") or (f"{b['years_in_business']} years in business" if b["years_in_business"] else ""))),
        ("Promoters — experience & background", promoters), ("Group companies / associate concerns", e(c.get("group_companies"))),
        ("Key customers & suppliers (concentration %)", e(parties))])


def _financial(s) -> str:
    out = "<h1>3. Financial Summary (₹ lakh)</h1>"
    res = s["res"]
    fin = res["tables"]["financials"] if res is not None else pd.DataFrame()
    if fin.empty:
        return out + '<p class="critical">No financial statements were read.</p>'
    fk, years = res["fin_kpis"], list(fin.index)[-3:]
    y = (s["case"].get("client") or {}).get("ytd") or {}
    has_ytd = bool(y.get("period"))

    def amt(k):
        return [lakh(fin.at[yr, k]) if k in fin.columns else "—" for yr in years]

    def ytd(k):
        return [lakh(y.get(k) or None)] if has_ytd else []

    def ratio(k, unit):
        return [cr.val(fk.at[k, yr], unit) if k in fk.index and yr in fk.columns else "—" for yr in years]
    ebitda = []
    for yr in years:
        a, m = fk.at["ebitda", yr] if "ebitda" in fk.index else None, fk.at["ebitda_margin", yr] if "ebitda_margin" in fk.index else None
        ebitda.append("—" if _na(a) else lakh(a) + ("" if _na(m) else f" ({m:.1%})"))
    tol = [lakh(fin.at[yr, "total_assets"] - fin.at[yr, "net_worth"]) if {"total_assets", "net_worth"} <= set(fin.columns) else "—" for yr in years]
    sdc = [" / ".join(lakh(fin.at[yr, k]) if k in fin.columns else "—" for k in ("inventory", "trade_receivables", "trade_payables")) for yr in years]
    dt, f = s["dscr"], cr.foir(s)
    post_dscr = ["—"] * (len(years) - 1) + ["—" if dt is None or dt.empty else f"{dt['dscr'].min():.2f}x"]
    post_foir = ["—"] * (len(years) - 1) + ["—" if f is None else f"{f['foir']:.0%}"]
    blank = ["—"] if has_ytd else []
    rows = [["Sales"] + amt("revenue_from_operations") + ytd("sales"), ["EBITDA (and %)"] + ebitda + ytd("ebitda"),
            ["Depreciation"] + amt("depreciation") + ytd("depreciation"), ["Interest"] + amt("finance_cost") + ytd("interest"),
            ["PAT"] + amt("profit_after_tax") + ytd("pat"), ["Net worth"] + amt("net_worth") + ytd("net_worth"),
            ["Total outside liabilities"] + tol + ytd("tol"),
            ["Stock / Debtors / Creditors"] + sdc + ([" / ".join(lakh(y.get(k) or None) for k in ("stock", "debtors", "creditors"))] if has_ytd else []),
            ["DSCR (post-proposed loan)"] + post_dscr + blank, ["TOL/TNW"] + ratio("tol_tnw", "x") + blank,
            ["Current ratio"] + ratio("current_ratio", "x") + blank, ["FOIR (post-proposed EMI)"] + post_foir + blank]
    head = ["Head"] + [escape(str(yr)) for yr in years] + ([f"YTD {escape(y['period'])}"] if has_ytd else [])
    return out + table(head, rows, num_cols=tuple(range(1, len(head))))


def _banking(s, info) -> str:
    out = "<h1>4. Banking Conduct Summary</h1>"
    res = s["res"]
    if res is None or res["bank_kpis"].empty:
        return out + '<p class="muted">No bank statements were read.</p>'
    bk = res["bank_kpis"]

    def each(k, f):
        return "; ".join(f"{r['account']}: {f(r.get(k))}" for _, r in bk.iterrows())
    gst_avg = None
    if res["tables"]["gst"] is not None and not res["tables"]["gst"].empty:
        _, m = kpis.gst_last_12_months(res["tables"]["gst"])
        gst_avg = m["avg_monthly_turnover"] if m else None
    return out + _kv([
        ("Accounts analysed (banks, months)", e(each("months_covered", lambda v: f"{v} months"))),
        ("Average bank balance (ABB)", e(each("abb", cr.inr))),
        ("Average monthly credits vs GST turnover", e(each("avg_monthly_credits", cr.inr) + f" vs GST {cr.inr(gst_avg)} a month")),
        ("Inward / outward returns (12 months)", e("; ".join(f"{r['account']}: {r.get('inward_returns')} / {r.get('outward_bounces')}" for _, r in bk.iterrows()))),
        ("CC/OD utilisation pattern", e(each("limit_utilisation", lambda v: "—" if _na(v) else f"{v:.0%}"))),
        ("Large or unusual entries — explanation", e(info.get("large_entries")))])


def _bureau(s) -> str:
    c = s["case"].get("client") or {}
    scores = "<br>".join(e(f"{p.get('Name')}: CIBIL " + ("—" if _na(p.get("CIBIL")) else f"{p['CIBIL']:.0f}")
                           + (f"; {p['CRIF / Experian']}" if p.get("CRIF / Experian") else "")) for p in c.get("promoters") or []) or "—"
    return "<h1>5. Bureau Summary</h1>" + _kv([
        ("Scores — promoters (bureau-wise)", scores), ("CMR (if company)", e(c.get("cmr"))),
        ("DPD history / flags (settled, written-off, suit-filed)", e(c.get("dpd"))),
        ("Recent enquiries (90 days)", e(c.get("enquiries"))),
        ("Guarantees given by promoters (group exposure)", e(c.get("guarantees_given")))])


def _obligations(s) -> str:
    c = s["case"].get("client") or {}
    obs = c.get("obligations") or []
    fac = s["facilities"]
    closing = "<br>".join(e(f"{o.get('Lender')} {o.get('Product')}, outstanding {cr.inr(o.get('Outstanding ₹'))}")
                          for o in obs if o.get("Close with new loan") == "Y") or "None"
    elig = "<br>".join(e(f"{r['Facility']}: {r['Assessment']}, assessed {cr.inr(r['Eligible (₹)'])}") for _, r in fac.iterrows()) if not fac.empty else "—"
    f = cr.foir(s)
    if f:
        elig += "<br>" + e(f"FOIR post-proposed {f['foir']:.0%} against {kpis.THRESHOLDS['foir_max']:.0%} cap: income {cr.inr(f['income'])} "
                           f"a month, existing EMI {cr.inr(f['existing'])}, proposed EMI {cr.inr(f['proposed'])}")
    structure = "<br>".join(e(f"{r['Facility']}: {cr.inr(r['Amount (₹)'])}, {r['Repayment']}") for _, r in fac.iterrows()) if not fac.empty else "—"
    return "<h1>6. Obligations & Eligibility</h1>" + _kv([
        ("Total existing EMIs (from Obligation Sheet)", e(cr.inr(cr.existing_emi(c))) if obs else "—"),
        ("Loans to be closed / consolidated", closing), ("Eligibility — method & computation", elig),
        ("Proposed structure (facility-wise)", structure)])


def _collateral(s, info) -> str:
    secs = [x for x in s["case"]["securities"] if cr.sec.SECURITY_TYPES[x["kind"]][0] != "Guarantee"]
    cov, t = s["cover"], s["totals"]
    if not secs:
        return '<h1>7. Collateral (if applicable)</h1><p class="muted">No collateral offered.</p>'
    mv = cov["market_value"]
    ltv = t["net_exposure"] / mv if mv else None
    charges = "; ".join(f"{x['kind']}: {x['existing_charge']}" for x in secs if x.get("existing_charge"))
    return "<h1>7. Collateral (if applicable)</h1>" + _kv([
        ("Property / asset & owner", "<br>".join(e(f"{x['kind']}, {x.get('description') or ''} ({x.get('owner') or ''})") for x in secs)),
        ("Estimated market value & basis", "<br>".join(e(f"{x['kind']}: {cr.inr(x.get('value'))}" + (f", {x['valuer']}" if x.get("valuer") else "")) for x in secs)),
        ("LTV / FACR at proposed exposure", e("—" if ltv is None else f"LTV {ltv:.0%} / FACR {1 / ltv:.2f}x" if ltv else "—")),
        ("Title chain status / prior charges (ROC & CERSAI checked?)", e(" | ".join(x for x in (charges, info.get("title_status")) if x)))])


def _swot(s) -> str:
    fl, cap = s["flags"], s["capacity"]
    strengths = []
    if cap:
        m = cap["metrics"]
        strengths = [f"{r['Measure']}: {r['Value']}" for _, r in m[m["Status"] == "good"].head(5).iterrows()]
    weak = fl[fl["severity"].isin(["High", "Medium"])].head(5) if not fl.empty else fl
    weaknesses = [f"{r['area']}: {r['finding']}" for _, r in weak.iterrows()]
    mitigants = [r["next_step"] for _, r in weak.iterrows()]
    n = max(len(strengths), len(weaknesses), 1)
    pad = lambda x: x + [""] * (n - len(x))  # noqa: E731
    return '<h1 style="page-break-before:always">8. Strengths, Weaknesses &amp; Mitigants</h1>' + table(
        ["Strengths", "Weaknesses / risks", "Mitigants we will present"],
        [[escape(a), escape(b), escape(c)] for a, b, c in zip(pad(strengths), pad(weaknesses), pad(mitigants))],
        widths=[0.3, 0.38, 0.32]) + '<p class="muted">Drafted from the analysis. Edit before relying on it.</p>'


def _recommendation(info) -> str:
    return "<h1>9. Recommendation</h1>" + _kv([
        ("Go / No-go and rationale", e(info.get("go_no_go"))), ("Lender sequencing plan", e(info.get("lender_sequencing"))),
        ("Pre-empted credit queries & our answers", e(info.get("preempted_queries"))),
        ("Approved by (credit desk) / date", e(info.get("approved_by")))])


def _checklist(s, kyc_kinds, doc_types) -> str:
    items = checklist.build(s["case"], kyc_kinds, doc_types)
    word = {"Received": "good", "Pending": "critical"}
    return "<h1>Document checklist</h1>" + table(
        ["Section", "Document / item", "Status"],
        [[e(a), e(b), cr.status(word.get(c), c)] for a, b, c in items], widths=[0.22, 0.63, 0.15])


def build_pdf(s: dict, info: dict, kyc_kinds: set, doc_types: set) -> bytes:
    name = s["case"]["borrower"]["name"] or "Borrower"
    head = ('<div class="cover-sub" style="margin-bottom:2pt">Internal Deal Assessment — Prepare Before Lender Login</div>'
            f'<div class="cover-name" style="margin:0 0 8pt 0;font-size:18pt">Credit Appraisal Memo (CAM-Lite)</div><p><b>{e(name)}</b> '
            '&nbsp;<span class="critical">Internal: not for the lender</span></p>')
    html = (head + _deal(s, info) + _profile(s) + _financial(s) + _banking(s, info) + _bureau(s) + _obligations(s)
            + _collateral(s, info) + _swot(s) + _recommendation(info))
    pdf, _ = cr._render([html, _checklist(s, kyc_kinds, doc_types)])
    out = fitz.open("pdf", pdf)
    cr.brand(out, range(out.page_count))
    out.set_metadata({"title": f"CAM-Lite — {name}", "author": info.get("prepared_by") or "", "subject": "Internal deal assessment",
                      "creator": "Credit Appraisal Workbench"})
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data
