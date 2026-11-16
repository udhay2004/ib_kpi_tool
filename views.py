"""The JSON the web front end renders: the case plus everything worked out from it, formatted for display.
Amounts are shown in the unit picked in the sidebar (₹ Crore / Lakh / ₹)."""

from datetime import date

import pandas as pd

import kpis
import kyc
import loans
import repayment as rp
import security as sec
import borrower as bw
from engine import (BENCH, EQUITY_FIELDS, FIN_TYPES, MSME, PROFILE_TABLES, STEPS, UNITS, clean, fmt, inr, money, na,
                    new_id, pct, records, table)
from intake import CHECKLIST, coverage

STATUS_TXT = {"pending": "Ready to read", "extracted": "Read", "failed": "Failed", "locked": "Password",
              "unreadable": "Unreadable"}
CHECK_STATUS = {"✅": "good", "⚠️": "warning", "❌": "critical", "⏳": "info", "➖": None}


def meta() -> dict:
    """Lists the forms need; they do not change while the app runs."""
    return {
        "units": list(UNITS), "constitutions": kyc.CONSTITUTIONS, "msme": MSME,
        "catalogue": {cat: {grp: {name: {k: v for k, v in spec.items()} for name, spec in prods.items()}
                            for grp, prods in groups.items()} for cat, groups in loans.CATALOGUE.items()},
        "fields": {k: {"key": v[0], "label": v[1], "kind": v[2], "default": v[3], "help": v[4]} for k, v in loans.F.items()},
        "security_types": {k: {"class": v[0], "charge": v[1], "margin": v[2], "docs": v[3]} for k, v in sec.SECURITY_TYPES.items()},
        "kyc_kinds": kyc.ALL_KINDS, "entity_kinds": list(kyc.ENTITY_DOCS), "person_kinds": list(kyc.PERSON_DOCS),
        "fin_types": FIN_TYPES, "checklist": CHECKLIST,
        "repay_methods": rp.METHODS, "frequencies": list(rp.FREQUENCIES), "threshold_defaults": kpis.THRESHOLDS,
        "steps": STEPS, "taxonomy": loans.taxonomy(), "trade_finance": sorted(loans.TRADE_FINANCE), "ccf": loans.CCF,
        "unsecured_default": sorted(loans.UNSECURED_BY_DEFAULT), "profile_tables": PROFILE_TABLES,
        "equity_fields": EQUITY_FIELDS, "security_opted": sec.OPTED, "kyc_statuses": kyc.STATUSES,
    }


def _status(s: str):
    key = next((k for k in CHECK_STATUS if s.startswith(k)), "➖")
    return CHECK_STATUS[key], s[len(key):].strip()


def _files(ws, store: str) -> list:
    out = []
    for d in ws.stores[store].values():
        out.append({"doc_id": d.doc_id, "filename": d.filename.rsplit("/", 1)[-1], "doc_type": d.doc_type or "auto",
                    "person": d.entity or "", "status": "locked" if d.encrypted else d.status,
                    "status_text": "Password" if d.encrypted else STATUS_TXT.get(d.status, d.status),
                    "note": d.error or "; ".join(d.warnings) or "", "pages": d.pages, "encrypted": d.encrypted,
                    "kind_label": kyc.ALL_KINDS.get(d.doc_type, FIN_TYPES.get(d.doc_type or "auto", d.doc_type or ""))})
    return out


def _flags(df: pd.DataFrame) -> list:
    return records(df[["severity", "area", "finding", "evidence", "next_step"]]) if df is not None and not df.empty else []


def _tile(fk, key, fy, prev, div, label, th_all):
    unit, v = fk.at[key, "unit"], fk.at[key, fy]
    t = {"label": label or fk.at[key, "KPI"], "value": fmt(unit, v, div), "status": None, "bench": "", "sub": ""}
    if prev is not None and pd.notna(fk.at[key, prev]):
        t["sub"] = f"{prev}: {fmt(unit, fk.at[key, prev], div)}"
    if key in BENCH and pd.notna(v):
        op, th_key, text = BENCH[key]
        th = th_all[th_key]
        t["status"] = "good" if (v >= th if op == ">=" else v <= th) else "critical"
        t["bench"] = text.format(t=th)
    return t


# ---------------------------------------------------------------- sections

def _security(ws, t) -> dict:
    c = sec.cover(ws.case["securities"], t["net_exposure"])
    opted = ws.case.get("security_opted", "later")
    st_ = None if c["cover"] is None else ("good" if c["cover"] >= 1.25 else "warning" if c["cover"] >= 1 else "critical")
    flags = sec.security_flags(ws.case["securities"], t["net_exposure"], ws.case["borrower"]["msme_class"], t["fund"],
                               t["total"] - t["net_exposure"], opted)
    flags += sec.requirement_flags(ws.case["securities"], [{"product": f["product"], "secured": loans.is_secured(f)}
                                                           for f in ws.case["facilities"]], opted)
    suggestions = []
    for d in ws.as_dicts("sec_docs", ws.static_sec):
        x = d.get("extracted")
        if not x:
            continue
        vd = None
        try:
            vd = date.fromisoformat(x.get("valuation_date") or "").isoformat()
        except ValueError:
            pass
        kind = x.get("suggested_security_kind")
        if kind not in sec.SECURITY_TYPES:
            continue
        suggestions.append({
            "doc_id": d["doc_id"], "title": f"{d['doc_id']} · {x.get('document_type') or 'Document'} — {kind}",
            "detail": f"{x.get('description') or ''} · owner {x.get('owner') or '—'} · market value {inr(x.get('market_value'))}"
                      f" · valued {x.get('valuation_date') or '—'}" + (f" · {'; '.join(x['issues'])}" if x.get("issues") else ""),
            "added": any(s.get("from_doc") == d["doc_id"] for s in ws.case["securities"]),
            "security": {"id": new_id(), "kind": kind, "description": x.get("description") or "", "owner": x.get("owner") or "",
                         "owner_is_third_party": False, "value": float(x.get("market_value") or 0), "valuation_date": vd,
                         "valuer": x.get("valuer") or "", "existing_charge": x.get("existing_charge_or_encumbrance") or "",
                         "margin_pct": float(sec.SECURITY_TYPES[kind][2]), "from_doc": d["doc_id"]}})
    unsecured = opted == "no"
    return {
        "opted": opted, "secured_products": [f["product"] for f in ws.case["facilities"] if loans.is_secured(f)],
        "realisable": {s["id"]: inr(sec.realisable(s)) for s in ws.case["securities"]},
        "tiles": [{"label": "Exposure to cover", "value": inr(t["net_exposure"])},
                  {"label": "Market value", "value": "n/a" if unsecured else inr(c["market_value"])},
                  {"label": "Realisable value", "value": "n/a" if unsecured else inr(c["realisable_value"])},
                  {"label": "Security cover", "value": "n/a - unsecured" if unsecured else "—" if c["cover"] is None else f"{c['cover']:.2f}x",
                   "status": None if unsecured else st_, "bench": "≥ 1.25x"}],
        "flags": [{"severity": a, "area": "Security", "finding": b, "evidence": "Step 2", "next_step": n} for a, b, n in flags],
        "suggestions": suggestions, "files": _files(ws, "sec_docs"), "last_run": ws.last_run.get("sec_docs"),
    }


def _kyc(ws) -> dict:
    docs = ws.kyc_docs()
    rows = ws.kyc_rows()
    people = sorted({d.get("person") for d in docs if d.get("person")})
    return {"checklist": rows, "resolved": sum(r["resolved"] for r in rows), "files": _files(ws, "kyc_docs"),
            "static": [{"doc_id": d["doc_id"], "label": kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]), "person": d.get("person") or ""}
                       for d in ws.static_kyc or []],
            "people": [{"person": p, "docs": ", ".join(kyc.ALL_KINDS.get(d["doc_type"], d["doc_type"]) for d in docs
                                                       if d.get("person") == p)} for p in people],
            "missing": ws.kyc_missing()}


def _money_rows(rows, cols):
    return [{**r, **{c: (None if r.get(c) is None else r[c]) for c in cols}} for r in rows]


def _borrower(ws, s, div, unit) -> dict:
    """Step 1: the tables as entered plus the repayment-potential panel."""
    pot, th = s["potential"], s["thresholds"]
    inc, nw, ob, sc, tr = pot["income"], pot["net_worth"], pot["obligations"], pot["scores"], pot["track"]
    v = pot["verdict"]
    fin = s["fin"]
    accrual = (fin.get("pat") or 0) + (fin.get("depreciation") or 0) if fin and fin.get("pat") is not None else None
    return {
        "verdict": v, "observations": pot["observations"], "missing": pot["missing"],
        "flags": pot["flags"],
        "tiles": [
            {"label": "Eligible income (a month)", "value": inr(pot["monthly_income"]),
             "sub": f"{inr(inc['total_annual'])} a year in all" if inc["total_annual"] is not None else "No income entered"},
            {"label": "Existing obligations (a month)", "value": inr(ob["monthly"]) if ob["count"] else "—",
             "sub": f"Outstanding {inr(ob['total_outstanding'])}" if ob["count"] else "None entered"},
            {"label": "FOIR (existing)", "value": "—" if pot["foir"] is None else f"{pot['foir']:.0%}",
             "status": None if pot["foir"] is None else "good" if pot["foir"] <= th["foir_max"] else
             "warning" if pot["foir"] <= th["foir_weak"] else "critical", "bench": f"≤ {th['foir_max']:.0%}"},
            {"label": "Room for a new EMI", "value": inr(pot["max_emi"]), "sub": "a month, within the FOIR cap"},
            {"label": "Indicative max loan", "value": inr(pot["max_loan"]),
             "sub": f"at {pot['rate_pct']:g}% over {pot['tenure_months']} months"},
            {"label": "Net worth", "value": inr(nw["net_worth"]),
             "sub": "" if nw["documented_share"] is None else f"{nw['documented_share']:.0%} of assets documented"},
            {"label": "Bureau (weakest)", "value": sc["worst"] or "—", "status": sc["status"] if sc["worst"] else None,
             "bench": f"{sc['count']} score(s)" if sc["count"] else ""},
            {"label": "Track record", "value": tr["rating"], "status": tr["status"], "bench": f"{len(tr['rows'])} account(s)"}],
        "scores": [{"band": r["band"], "status": r["status"]} for r in sc["rows"]],
        "income": {"total": inr(inc["total_annual"]), "stable": inr(inc["stable_annual"]),
                   "eligible": inr(inc["eligible_annual"]), "annual": [inr(r["annual"]) for r in inc["rows"]]},
        "net_worth": {"assets": inr(nw["total_assets"]), "liabilities": inr(nw["total_liabilities"]),
                      "net": inr(nw["net_worth"])},
        "track": tr["reasons"],
        "obligations": {"outstanding": inr(ob["total_outstanding"]), "nonfund": inr(ob["nonfund_outstanding"]),
                        "monthly": inr(ob["monthly"]), "closing": len(ob["closing"])},
        "from_financials": None if accrual is None else f"{fin['fy']}: PAT + depreciation = {inr(accrual)} a year "
                                                         f"({inr(accrual / 12)} a month)",
    }


def _product(ws, s) -> dict:
    """Step 3: classification of every facility, the non-fund limit table and equity, kept apart from debt."""
    t = s["totals"]
    nfb = []
    for f in ws.case["facilities"]:
        lim = loans.nfb_limit(f)
        if lim:
            nfb.append({"id": f["id"], "product": f["product"], "limit": inr(lim["limit"]), "utilised": inr(lim["utilised"]),
                        "utilisation": "—" if lim["utilisation"] is None else f"{lim['utilisation']:.0%}",
                        "available": inr(lim["available"]), "margin": f"{lim['margin_pct']:g}%",
                        "margin_money": inr(lim["margin_money"]), "ccf": f"{lim['ccf_pct']:g}%",
                        "fund_equivalent": inr(lim["fund_equivalent"]),
                        "commission": "—" if lim["commission_pct"] is None else f"{lim['commission_pct']:g}%",
                        "tenor": lim["tenor_months"] or "—"})
    nl = [loans.nfb_limit(f) for f in ws.case["facilities"] if not loans.is_fund(f)]
    used = sum(x["utilised"] for x in nl)
    return {
        "classes": {f["id"]: loans.classification(f) for f in ws.case["facilities"]},
        "nfb": nfb,
        "nfb_totals": {"limit": inr(t["nonfund"]), "utilised": inr(used),
                       "utilisation": f"{used / t['nonfund']:.0%}" if t["nonfund"] else "—",
                       "margin_money": inr(sum(x["margin_money"] for x in nl)), "fund_equivalent": inr(t["nfb_fund_equivalent"])},
        "equity": [{"implied_dilution": "—" if r["implied_dilution_pct"] is None else f"{r['implied_dilution_pct']:.1f}%"}
                   for r in s["equity"]["rows"]],
        "tiles": [{"label": "Fund-based debt", "value": inr(t["fund"])},
                  {"label": "Non-fund limits", "value": inr(t["nonfund"]), "sub": f"Fund-based equivalent {inr(t['nfb_fund_equivalent'])}"},
                  {"label": "Equity (separate)", "value": inr(t["equity"]), "sub": "Not part of debt exposure"},
                  {"label": "Credit-equivalent exposure", "value": inr(t["credit_equivalent"]),
                   "sub": f"Secured {inr(t['secured'])} · unsecured {inr(t['unsecured'])}"}],
    }


def _review(ws) -> dict | None:
    base = ws.base_tables()
    if base is None:
        return None
    t = ws.tables or base
    out = {"conflicts": base["conflicts"], "notes": []}
    fin = t["financials"]
    if not fin.empty:
        f = fin.reset_index()
        cols = [c for c in f.columns if c != "fiscal_year"]
        out["financials"] = {"years": [str(y) for y in f["fiscal_year"]], "fields": cols,
                             "values": {c: [clean(v) for v in f[c].tolist()] for c in cols}}
    for key in ("bank", "gst", "itr"):
        df = t[key]
        out[key] = {"columns": list(df.columns), "rows": records(df)} if not df.empty else None
    for d in ws.fin_docs():
        x = d.get("extracted") or {}
        notes = [*(x.get("notes") or []), *(x.get("related_party_notes") or [])]
        notes += [f"{n.get('date')}: {n.get('description')} ({n.get('amount')}) — {n.get('why_notable')}"
                  for n in x.get("notable_transactions") or []]
        if notes:
            out["notes"].append({"title": f"{d['doc_id']} {d['filename']}", "notes": [str(n) for n in notes]})
    return out


def _analysis(res, div, unit, th) -> dict | None:
    if not res:
        return None
    fk, bk, gk, flags = res["fin_kpis"], res["bank_kpis"], res["gst_kpis"], res["flags"]
    years = [c for c in fk.columns if str(c).startswith("FY")] if not fk.empty else []
    out = {"scorecard": clean(res["scorecard"]), "flags": _flags(flags), "years": years, "tiles": [],
           "fin_table": None, "fin_chart": None, "bank_chart": None, "bank_table": None, "gst_table": None, "itr_table": None}
    if years:
        fy, prev = years[-1], (years[-2] if len(years) > 1 else None)
        out["tiles_title"] = fy
        out["tiles"] = [_tile(fk, k, fy, prev, div, lbl, th) for k, lbl in (
            ("revenue", f"Revenue ({unit})"), ("revenue_growth", None), ("ebitda_margin", None), ("pat_margin", None),
            ("dscr", None), ("current_ratio", None), ("debt_equity", "Debt / TNW"), ("tol_tnw", None))]
        out["fin_chart"] = {"labels": years, "series": {lbl: [None if na(v) else float(v) / div for v in fk.loc[k, years]]
                                                        for k, lbl in (("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pat", "PAT"))}}
        show = fk.copy()
        for y in years:
            show[y] = [fmt(u, v, div) for u, v in zip(show["unit"], show[y])]
        out["fin_table"] = table(show[["KPI", *years, "formula"]].rename(columns={"formula": "Formula"}))
    if not bk.empty:
        b = res["tables"]["bank"].groupby("month")[["total_credits", "total_debits"]].sum() / div
        b.columns = ["Credits", "Debits"]
        g = res["tables"]["gst"]
        if not g.empty:
            b = b.join(g[g["return_type"].fillna("").str.upper().str.contains("3B")]
                       .groupby("period")["taxable_turnover"].sum().rename("GST turnover") / div)
        out["bank_chart"] = {"labels": [str(i) for i in b.index],
                             "series": {c: [None if na(v) else float(v) for v in b[c]] for c in b.columns}}
        rows = {
            "Months covered": lambda r: r["months_covered"], "Period": lambda r: r["period"],
            f"Total credits ({unit})": lambda r: money(r["total_credits"], div),
            f"Business credits ({unit})": lambda r: money(r["business_credits"], div),
            f"Avg monthly credits ({unit})": lambda r: money(r["avg_monthly_credits"], div),
            f"Avg bank balance — ABB ({unit})": lambda r: money(r["abb"], div),
            f"Lowest balance ({unit})": lambda r: money(r["lowest_balance"], div),
            "ABB / monthly EMI": lambda r: fmt("x", r["abb_to_emi"], 1),
            "Cash deposits % of credits": lambda r: pct(r["cash_deposit_share"]),
            "Own / related transfers % of debits": lambda r: pct(r["related_transfer_share"]),
            "Top 5 payers % of credits": lambda r: pct(r["top5_counterparty_share"]),
            "Outward bounces": lambda r: r["outward_bounces"], "Inward returns": lambda r: r["inward_returns"],
            "OD/CC utilisation": lambda r: pct(r["limit_utilisation"]),
        }
        tbl = pd.DataFrame({r["account"]: {k: str(f(r)) for k, f in rows.items()} for _, r in bk.iterrows()})
        out["bank_table"] = table(tbl.reset_index(names="Metric"))
    if not gk.empty:
        show = gk.copy()
        for c in ("gst_turnover", "avg_monthly_turnover", "tax_paid_cash", "itc_claimed", "itc_available_2b"):
            if c in show:
                show[c] = show[c].map(lambda v: money(v, div))
        for c in ("gstr1_vs_3b_gap", "itc_excess_over_2b", "top5_customer_share"):
            if c in show:
                show[c] = show[c].map(lambda v: "—" if na(v) else f"{v:+.1%}")
        show.columns = [c.replace("_", " ").capitalize() for c in show.columns]
        out["gst_table"] = table(show)
    itr = res["tables"]["itr"]
    if not itr.empty:
        cols = ["assessee_name", "assessment_year", "income_salary", "income_business_profession", "income_other_sources",
                "gross_total_income", "total_income", "tax_payable", "doc_id"]
        show = itr[[c for c in cols if c in itr]].sort_values(["assessee_name", "assessment_year"]).copy()
        for c in cols[2:-1]:
            if c in show:
                show[c] = show[c].map(lambda v: money(v, div))
        show.columns = [c.replace("_", " ").capitalize() for c in show.columns]
        out["itr_table"] = table(show)
    return out


def _repayment(ws, s, div, unit) -> dict | None:
    case = ws.case
    if not case["facilities"]:
        return None
    fin, cap, ass = s["fin"], s["capacity"], s["assessments"]
    pot, vs, th = s["potential"], s["proposal_vs_potential"], s["thresholds"]
    out = {"has_fin": bool(fin), "capacity": None, "structure": [], "dscr": None, "eligibility": [],
           "potential": {"verdict": pot["verdict"], "tiles": [
               {"label": "Room for a new EMI (step 1)", "value": inr(pot["max_emi"])},
               {"label": "Proposal's monthly outgo", "value": inr(vs["new_emi"]) if vs else "—",
                "status": None if not vs or vs["within_headroom"] is None else "good" if vs["within_headroom"] else "critical",
                "bench": "within room" if vs and vs["within_headroom"] else "above room" if vs and vs["within_headroom"] is False else ""},
               {"label": "FOIR after the proposal", "value": "—" if not vs or vs["foir_after"] is None else f"{vs['foir_after']:.0%}",
                "status": vs["status"] if vs else None, "bench": f"≤ {th['foir_max']:.0%}"},
               {"label": "Credit-equivalent exposure", "value": inr(s["totals"]["credit_equivalent"]),
                "sub": f"incl. non-fund equivalent {inr(s['totals']['nfb_fund_equivalent'])}"}],
               "missing": pot["missing"]}}
    if cap is not None:
        m = cap["metrics"].copy()
        m["Status"] = m["Status"].map({"good": "OK", "warning": "Watch", "critical": "Concern"}).fillna("")
        stx = cap["stress"].copy()
        for c in ("Minimum DSCR", "Average DSCR"):
            stx[c] = stx[c].map(lambda x: "—" if na(x) else f"{x:.2f}x")
        stx["Status"] = stx["Status"].map({"good": "≥ 1.25x", "warning": "1.0–1.25x", "critical": "< 1.0x"}).fillna("")
        out["capacity"] = {"fy": cap["fy"], "verdict": cap["verdict"], "notes": clean(cap["notes"]),
                           "metrics": table(m), "stress": table(stx)}
    sch = ws.schedules()
    for fac in case["facilities"]:
        p = loans.product(fac["product"])
        item = {"id": fac["id"], "product": fac["product"], "amount": inr(fac.get("amount")), "tenor": fac.get("tenor_months"),
                "rate": fac.get("rate_pct"), "amortising": p["repay"] in ("amortising", "bullet"),
                "note": rp.NON_AMORTISING.get(p["repay"], ""), "cfg": ws.repay_cfg(fac) if p["repay"] in ("amortising", "bullet") else None,
                "schedule": None}
        sc = sch.get(fac["id"])
        if sc is not None:
            y = rp.yearly(sc)
            first = sc[sc["principal"] > 0].head(1)
            rows = sc[["period", "month", "opening", "interest", "principal", "instalment", "closing"]].copy()
            for c in ("opening", "interest", "principal", "instalment", "closing"):
                rows[c] = rows[c].map(lambda v: f"{v:,.0f}")
            item["schedule"] = {
                "tiles": [{"label": "First full instalment", "value": inr(first["instalment"].iloc[0]) if not first.empty else "—"},
                          {"label": "Total interest", "value": inr(sc["interest"].sum())},
                          {"label": "Largest year's outgo", "value": inr(y["instalment"].max())},
                          {"label": "Instalments", "value": str(int((sc["principal"] > 0).sum()))}],
                "chart": {"labels": [f"Y{i}" for i in y.index],
                          "series": {"Principal": [float(v) / div for v in y["principal"]],
                                     "Interest": [float(v) / div for v in y["interest"]]}},
                "table": table(rows.rename(columns=str.capitalize))}
        out["structure"].append(item)
        a = ass[fac["id"]]
        out["eligibility"].append({"product": fac["product"], "requested": inr(fac.get("amount")), "category": p["category"],
                                   "method": a["method"] or "No formula", "eligible": inr(a["eligible"]) if a["eligible"] is not None else None,
                                   "ok": a["ok"], "workings": a["workings"]})
    dt = s["dscr"]
    if fin:
        d = {"base": f"Base ({fin['fy']}): PAT {inr(fin.get('pat'))} + depreciation {inr(fin.get('depreciation'))}; existing "
                     f"interest {inr(fin.get('finance_cost'))} and principal {inr(fin.get('current_maturities'))} assumed to "
                     "continue (conservative).", "tiles": [], "table": None, "chart": None}
        if dt is not None and not dt.empty:
            (mn, avg), th = rp.dscr_range(dt), ws.th
            d["tiles"] = [
                {"label": "Minimum DSCR", "value": "—" if mn is None else f"{mn:.2f}x",
                 "status": None if mn is None else "good" if mn >= th["dscr_min"] else "critical", "bench": f"≥ {th['dscr_min']:.2f}x",
                 "sub": "No debt service" if mn is None else ""},
                {"label": "Average DSCR", "value": "—" if avg is None else f"{avg:.2f}x",
                 "status": None if avg is None else "good" if avg >= th["dscr_avg_min"] else "critical",
                 "bench": f"≥ {th['dscr_avg_min']:.2f}x"},
                {"label": "Peak debt service", "value": inr(dt["total_debt_service"].max())},
                {"label": "Years", "value": str(len(dt))}]
            d["chart"] = {"labels": [f"Y{int(i)}" for i in dt["year"]], "series": {"DSCR": [None if na(v) else float(v) for v in dt["dscr"]]},
                          "rule": th["dscr_min"]}
            show = dt.copy()
            for c in ("cash_accrual", "available_for_debt_service", "new_interest", "new_principal", "existing_debt_service",
                      "total_debt_service"):
                show[c] = show[c].map(lambda v: f"{v / div:,.2f}")
            show["dscr"] = show["dscr"].map(lambda v: "—" if na(v) else f"{v:.2f}x")
            show.columns = [c.replace("_", " ").capitalize() for c in show.columns]
            d["table"] = table(show)
        out["dscr"] = d
    return out


def _summary(ws, s, div) -> dict:
    t, cov, dt, res = s["totals"], s["cover"], s["dscr"], s["res"]
    fk = res["fin_kpis"] if res is not None else pd.DataFrame()
    fy = s["fin"].get("fy") if s["fin"] else None
    th = s["thresholds"]
    mn = rp.dscr_range(dt)[0]

    def kv(key):
        return fk.at[key, fy] if fy and key in fk.index and pd.notna(fk.at[key, fy]) else None
    cr, tol = kv("current_ratio"), kv("tol_tnw")
    fl = s["flags"]
    n = {k: int((fl["severity"] == k).sum()) if not fl.empty else 0 for k in ("High", "Medium")}
    fac = s["facilities"].copy()
    for c in ("Amount (₹)", "Eligible (₹)"):
        if c in fac:
            fac[c] = fac[c].map(inr)
    cap, pot = s["capacity"], s["potential"]
    unsecured = s["security_opted"] == "no"
    return {
        "steps": s["steps"],
        "potential": {"verdict": pot["verdict"], "observations": pot["observations"]},
        "equity": [[r.get("instrument") or "—", inr(r.get("amount")), r.get("investor") or "—",
                    "—" if r.get("dilution_pct") is None else f"{r['dilution_pct']:.1f}%", r.get("use_of_funds") or "—"]
                   for r in s["equity"]["rows"]],
        "tiles": [
            {"label": "Debt proposed", "value": inr(t["total"]), "sub": f"Fund {inr(t['fund'])} · NFB limits {inr(t['nonfund'])}"},
            {"label": "Repayment potential", "value": pot["verdict"]["label"], "status": pot["verdict"]["status"],
             "bench": "step 1"},
            {"label": "Equity requested", "value": inr(t["equity"]), "sub": "Separate from debt"},
            {"label": "Security cover", "value": "n/a - unsecured" if unsecured else "—" if cov["cover"] is None else f"{cov['cover']:.2f}x",
             "status": None if unsecured or cov["cover"] is None else ("good" if cov["cover"] >= 1.25 else "critical"), "bench": "≥ 1.25x"},
            {"label": "Min DSCR (proposal)", "value": "—" if mn is None else f"{mn:.2f}x",
             "status": None if mn is None else ("good" if mn >= th["dscr_min"] else "critical"),
             "bench": f"≥ {th['dscr_min']:.2f}x"},

            {"label": "Current ratio", "value": "—" if cr is None else f"{cr:.2f}x",
             "status": None if cr is None else ("good" if cr >= th["current_ratio_benchmark"] else "critical"),
             "bench": f"≥ {th['current_ratio_benchmark']:.2f}x"},
            {"label": "TOL / TNW", "value": "—" if tol is None else f"{tol:.2f}x",
             "status": None if tol is None else ("good" if tol <= th["tol_tnw_max"] else "critical"),
             "bench": f"≤ {th['tol_tnw_max']:.1f}x"},
            {"label": "High findings", "value": str(n["High"])}, {"label": "Medium findings", "value": str(n["Medium"])}],
        "capacity": None if cap is None else {"verdict": cap["verdict"], "notes": clean(cap["notes"])},
        "proposal": table(fac), "flags": _flags(fl),
        "kyc_files": sum(1 for d in ws.kyc_docs() if d["data"]),
    }


def state(ws, unit: str, server_key: bool) -> dict:
    unit = unit if unit in UNITS else "₹ Crore"
    div = UNITS[unit]
    ws.case.setdefault("client", {})
    ws.case.setdefault("report", {})
    s = ws.build()
    t = s["totals"]
    fin_docs = ws.fin_docs()
    cov = coverage(fin_docs)
    out = {
        "case": clean(ws.case), "unit": unit, "server_key": server_key, "has_key": bool(server_key or ws.api_key),
        "thresholds": ws.th,
        "borrower": _borrower(ws, s, div, unit),
        "product": _product(ws, s),
        "totals": {**t, "text": {k: inr(v) for k, v in t.items()},
                   "with_existing": inr(t["total"] + (ws.case["borrower"].get("existing_exposure") or 0)),
                   "lei": t["total"] + (ws.case["borrower"].get("existing_exposure") or 0) >= 5e7},
        "demo": bool(ws.demo),
        "security": _security(ws, t),
        "kyc": _kyc(ws),
        "fin": {"files": _files(ws, "docs"), "zip_skipped": ws.zip_skipped, "last_run": ws.last_run.get("docs"),
                "counts": {c["doc_type"]: sum(1 for d in ws.stores["docs"].values() if d.doc_type == c["doc_type"]) for c in CHECKLIST},
                "coverage": [dict(zip(("status", "label"), _status(r["Status"])), document=r["Document"], detail=r["Detail"])
                             for _, r in cov.iterrows()] if fin_docs else [],
                "missing": [r["Document"] for _, r in cov.iterrows() if r["Status"].startswith("❌")],
                "review": _review(ws), "analysis": _analysis(s["res"], div, unit, s["thresholds"])},
        "repay": _repayment(ws, s, div, unit),
        "summary": _summary(ws, s, div),
    }
    return clean(out)
