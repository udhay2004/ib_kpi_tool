"""Step 4 - financial documents: upload, review, analysis (bank statements, GST, ITRs, audited financials)."""

import altair as alt
import pandas as pd
import streamlit as st

import kpis
import ui
from common import add_file, as_dicts, doc_table, fmt, money, new_uploads, pct, read_button, ss
from extractor import extract
from local_parse import extract as extract_local
from intake import CHECKLIST, SUPPORTED, coverage, guess_type, unzip
from schemas import DOC_TYPES

TYPE_LABELS = {"auto": "Detect automatically", "financials": "Financial statements",
               "bank_statement": "Bank statement", "gst": "GST return", "itr": "ITR", "other": "Other"}
ICONS = {"financials": "📑", "bank_statement": "🏦", "gst": "🧾", "itr": "👤", "other": "📎"}
EXTS = sorted(e.strip(".") for e in SUPPORTED)

BENCH = {  # key -> (direction, threshold key, label)
    "dscr": (">=", "dscr_min", "≥ {t:.2f}x"),
    "current_ratio": (">=", "current_ratio_benchmark", "≥ {t:.2f}x"),
    "debt_equity": ("<=", "debt_equity_max", "≤ {t:.1f}x"),
    "tol_tnw": ("<=", "tol_tnw_max", "≤ {t:.1f}x"),
    "interest_coverage": (">=", "interest_coverage_min", "≥ {t:.1f}x"),
}


def related_names() -> list:
    """Borrower and its people: transfers to these names count as own / related-party flows."""
    from steps.kyc_step import kyc_docs
    return [ss.case["borrower"]["name"]] + [d["person"] for d in kyc_docs() if d.get("person")]


def fin_docs():
    return as_dicts("docs", "demo")


def analysis():
    docs = fin_docs()
    if not any(d.get("extracted") for d in docs):
        return None
    return kpis.analyse(docs, ss.tables)


def latest(res) -> dict:
    """Latest-year figures used by loan assessment and repayment."""
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


# ---------------------------------------------------------------- charts

def _axis_cfg(chart):
    return (chart.configure(background="#ffffff").configure_view(stroke=None)
            .configure_axis(labelColor="#475467", titleColor="#667085", gridColor="#eef0f3", domain=False)
            .configure_legend(labelColor="#475467", orient="top", title=None))


def bar_chart(df, y_title, stack=False):
    long = df.reset_index(names="Period").melt("Period", var_name="Measure", value_name="Value")
    order = list(df.columns)
    enc = dict(x=alt.X("Period:N", title=None, axis=alt.Axis(labelAngle=0)),
               y=alt.Y("Value:Q", title=y_title, stack="zero" if stack else None),
               color=alt.Color("Measure:N", sort=order, scale=alt.Scale(domain=order, range=ui.SERIES[:len(order)])),
               tooltip=["Period", "Measure", alt.Tooltip("Value:Q", format=",.2f")])
    if not stack:
        enc["xOffset"] = alt.XOffset("Measure:N", sort=order)
    return _axis_cfg(alt.Chart(long).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=22,
                                              stroke="#ffffff", strokeWidth=2 if stack else 0)
                     .encode(**enc).properties(height=280))


def line_chart(df, y_title, rule=None):
    long = df.reset_index(names="Period").melt("Period", var_name="Series", value_name="Value").dropna()
    order = list(df.columns)
    base = alt.Chart(long).encode(
        x=alt.X("Period:N", title=None, axis=alt.Axis(labelAngle=-40)),
        y=alt.Y("Value:Q", title=y_title),
        color=alt.Color("Series:N", sort=order, scale=alt.Scale(domain=order, range=ui.SERIES[:len(order)])))
    hover = alt.selection_point(fields=["Period"], nearest=True, on="pointerover", empty=False)
    layers = [base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=36, filled=True)),
              alt.Chart(long).mark_rule(color="#98a2b3").encode(
                  x="Period:N", opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
                  tooltip=[alt.Tooltip("Period:N")] + [alt.Tooltip(f"{c}:Q", format=",.2f") for c in order])
              .transform_pivot("Series", value="Value", groupby=["Period"]).add_params(hover)]
    if rule is not None:
        layers.append(alt.Chart(pd.DataFrame({"y": [rule]})).mark_rule(color="#d03b3b", strokeDash=[4, 4]).encode(y="y:Q"))
    return _axis_cfg(alt.layer(*layers).properties(height=280))


def show_table(df, **kw):
    st.dataframe(df, hide_index=True, use_container_width=True, **kw)


def tile(fk, key, fy, prev, div, label=None):
    unit, v = fk.at[key, "unit"], fk.at[key, fy]
    t = {"label": label or fk.at[key, "KPI"], "value": fmt(unit, v, div), "status": None, "bench": ""}
    if prev is not None and pd.notna(fk.at[key, prev]):
        t["sub"] = f"{prev}: {fmt(unit, fk.at[key, prev], div)}"
    if key in BENCH and pd.notna(v):
        op, th_key, text = BENCH[key]
        th = kpis.THRESHOLDS[th_key]
        ok = v >= th if op == ">=" else v <= th
        t["status"], t["bench"] = ("good" if ok else "critical"), text.format(t=th)
    return t


# ---------------------------------------------------------------- render

def render(api_key, div, unit_label):
    t_docs, t_review, t_analysis = st.tabs(["Documents", "Review extracted data", "Analysis"])
    docs = fin_docs()
    ready = [d for d in docs if d.get("extracted")]

    with t_docs:
        mode = st.segmented_control("Upload method", ["Upload by section", "One ZIP with everything"],
                                    default="Upload by section", label_visibility="collapsed") or "Upload by section"
        counts = {t: sum(d.doc_type == t for d in ss.docs.values()) for t in DOC_TYPES}
        if mode == "One ZIP with everything":
            with st.container(border=True):
                ui.upload_header("🗂️", "All financial documents in one ZIP",
                                 "Sub-folders are fine. Files are sorted by name (HDFC_statement, GSTR3B_Apr24, "
                                 "Audited_FS_FY25, ITR_Ramesh_AY25); the rest are recognised from their contents.", True, len(ss.docs))
                z = st.file_uploader("ZIP file", type=["zip"], key=f"up_zip_{ss.upload_round}", label_visibility="collapsed")
                for f in new_uploads([z] if z else []):
                    files, ss.zip_skipped = unzip(f.getvalue())
                    for path, data in files:
                        add_file("docs", path, data, guess_type(path))
                    st.rerun()
                for msg in ss.zip_skipped:
                    st.caption(f"⚠️ Skipped {msg}")
        else:
            cols = st.columns(2, gap="medium")
            for i, item in enumerate(CHECKLIST):
                with cols[i % 2].container(border=True):
                    ui.upload_header(ICONS[item["doc_type"]], item["title"], item["ask"], item["required"],
                                     counts[item["doc_type"]])
                    fresh = new_uploads(st.file_uploader(item["title"], type=EXTS, accept_multiple_files=True,
                                                         key=f"up_{item['doc_type']}_{ss.upload_round}",
                                                         label_visibility="collapsed"))
                    for f in fresh:
                        add_file("docs", f.name, f.getvalue(), item["doc_type"])
                    if fresh:
                        st.rerun()
        if docs:
            ui.section("Checklist status", "What has arrived against what a credit appraisal needs.")
            ui.checklist(coverage(docs))
        if ss.docs:
            ui.section("Uploaded files", "Change a type if a file landed in the wrong place. Tick ✕ to remove.")
            doc_table("docs", TYPE_LABELS)
            missing = coverage(docs)
            missing = missing[missing["Status"].str.startswith("❌")]["Document"].tolist()
            read_button("docs", api_key, extract,
                        (f"Still missing: <b>{', '.join(missing)}</b>. " if missing else "") +
                        "Digital PDFs, Excel and CSV are read on this server in seconds, no API key needed."
                        + ("" if api_key else " Scanned images need an API key."),
                        local_one=lambda d: extract_local(d, related_names()))
        elif ss.demo:
            st.info("Demo company loaded (fictional data). Open **Review** and **Analysis**.")

    with t_review:
        if not ready:
            st.info("Nothing read yet. Upload documents and click **Read**.")
        else:
            st.markdown('<div class="card"><b>Check before you rely on it.</b> <span class="muted">Each value shows the '
                        'document (doc_id) and page it came from. Edit any cell — KPIs recalculate from your numbers. '
                        'Blank means not in the documents.</span></div>', unsafe_allow_html=True)
            base = kpis.build_tables(ready)
            edited = dict(base)
            if base["conflicts"]:
                st.warning("Conflicting figures across files:\n\n" + "\n".join(f"- {c}" for c in base["conflicts"]))
            if not base["financials"].empty:
                ui.section("Financial statements", "Amounts in ₹ (absolute). Rows are line items, columns are years.")
                edited["financials"] = st.data_editor(base["financials"].round(2).T, use_container_width=True,
                                                      key="ed_fin", height=560).T
                for c in edited["financials"].columns:
                    if c not in ("doc_id", "source_pages", "audited"):
                        edited["financials"][c] = pd.to_numeric(edited["financials"][c], errors="coerce")
            for key, title in (("bank", "Bank statements — monthly"), ("gst", "GST returns"),
                               ("itr", "Director / promoter ITRs")):
                if not base[key].empty:
                    ui.section(title)
                    edited[key] = st.data_editor(base[key], use_container_width=True, hide_index=True, key=f"ed_{key}")
            ss.tables = edited
            for d in ready:
                x = d["extracted"]
                notes = [*(x.get("notes") or []), *(x.get("related_party_notes") or [])]
                notes += [f"{n.get('date')}: {n.get('description')} ({n.get('amount')}) — {n.get('why_notable')}"
                          for n in x.get("notable_transactions") or []]
                if notes:
                    with st.expander(f"Extractor notes · {d['doc_id']} {d['filename']}"):
                        st.markdown("\n".join(f"- {n}" for n in notes))

    with t_analysis:
        res = analysis()
        if not res:
            st.info("Nothing to analyse yet.")
            return
        render_analysis(res, div, unit_label)


def render_analysis(res, div, unit_label):
    fk, bk, gk, flags = res["fin_kpis"], res["bank_kpis"], res["gst_kpis"], res["flags"]
    years = [c for c in fk.columns if str(c).startswith("FY")] if not fk.empty else []
    ui.section("Scorecard", "Worst finding per area — where to look, not a credit decision.")
    ui.scorecard(res["scorecard"])
    if years:
        fy, prev = years[-1], (years[-2] if len(years) > 1 else None)
        ui.section(f"Headline KPIs · {fy}", f"Amounts in {unit_label}. Badges compare with the benchmarks in Settings.")
        ui.kpi_tiles([tile(fk, k, fy, prev, div, lbl) for k, lbl in (
            ("revenue", f"Revenue ({unit_label})"), ("revenue_growth", None), ("ebitda_margin", None), ("pat_margin", None),
            ("dscr", None), ("current_ratio", None), ("debt_equity", "Debt / TNW"), ("tol_tnw", None))])
    top = flags[flags["severity"].isin(["High", "Medium"])]
    ui.section("Key red flags", f"{len(flags)} finding(s) in total — all under Risk flags below.")
    if top.empty:
        st.success("No high or medium findings against the current benchmarks.")
    else:
        ui.flag_cards(top.head(6))
    st.write("")
    t_fin, t_bank, t_gst, t_itr, t_flags = st.tabs(["Financials", "Banking", "GST", "Promoters", f"Risk flags ({len(flags)})"])
    with t_fin:
        if fk.empty:
            st.info("No financial statements read.")
        else:
            chart = fk.loc[["revenue", "ebitda", "pat"], years].T.astype(float) / div
            chart.columns = ["Revenue", "EBITDA", "PAT"]
            st.altair_chart(bar_chart(chart, unit_label), use_container_width=True)
            show = fk.copy()
            for y in years:
                show[y] = [fmt(u, v, div) for u, v in zip(show["unit"], show[y])]
            show_table(show[["KPI", *years, "formula"]], height=36 * (len(show) + 1))
    with t_bank:
        if bk.empty:
            st.info("No bank statements read.")
        else:
            b = res["tables"]["bank"].groupby("month")[["total_credits", "total_debits"]].sum() / div
            b.columns = ["Credits", "Debits"]
            g = res["tables"]["gst"]
            if not g.empty:
                b = b.join(g[g["return_type"].fillna("").str.upper().str.contains("3B")]
                           .groupby("period")["taxable_turnover"].sum().rename("GST turnover") / div)
            st.altair_chart(line_chart(b, unit_label), use_container_width=True)
            rows = {
                "Months covered": lambda r: r["months_covered"], "Period": lambda r: r["period"],
                f"Total credits ({unit_label})": lambda r: money(r["total_credits"], div),
                f"Business credits ({unit_label})": lambda r: money(r["business_credits"], div),
                f"Avg monthly credits ({unit_label})": lambda r: money(r["avg_monthly_credits"], div),
                f"Avg bank balance — ABB ({unit_label})": lambda r: money(r["abb"], div),
                f"Lowest balance ({unit_label})": lambda r: money(r["lowest_balance"], div),
                "ABB / monthly EMI": lambda r: fmt("x", r["abb_to_emi"], 1),
                "Cash deposits % of credits": lambda r: pct(r["cash_deposit_share"]),
                "Own / related transfers % of debits": lambda r: pct(r["related_transfer_share"]),
                "Top 5 payers % of credits": lambda r: pct(r["top5_counterparty_share"]),
                "Outward bounces": lambda r: r["outward_bounces"], "Inward returns": lambda r: r["inward_returns"],
                "OD/CC utilisation": lambda r: pct(r["limit_utilisation"]),
            }
            tbl = pd.DataFrame({r["account"]: {k: str(f(r)) for k, f in rows.items()} for _, r in bk.iterrows()})
            show_table(tbl.reset_index(names="Metric"))
    with t_gst:
        if gk.empty:
            st.info("No GST returns read.")
        else:
            show = gk.copy()
            for c in ("gst_turnover", "avg_monthly_turnover", "tax_paid_cash", "itc_claimed", "itc_available_2b"):
                show[c] = show[c].map(lambda v: money(v, div))
            for c in ("gstr1_vs_3b_gap", "itc_excess_over_2b", "top5_customer_share"):
                if c in show:
                    show[c] = show[c].map(lambda v: "—" if v is None or pd.isna(v) else f"{v:+.1%}")
            show.columns = [c.replace("_", " ").capitalize() for c in show.columns]
            show_table(show)
    with t_itr:
        itr = res["tables"]["itr"]
        if itr.empty:
            st.info("No director ITRs uploaded (optional).")
        else:
            cols = ["assessee_name", "assessment_year", "income_salary", "income_business_profession",
                    "income_other_sources", "gross_total_income", "total_income", "tax_payable", "doc_id"]
            show = itr[[c for c in cols if c in itr]].sort_values(["assessee_name", "assessment_year"]).copy()
            for c in cols[2:-1]:
                if c in show:
                    show[c] = show[c].map(lambda v: money(v, div))
            show.columns = [c.replace("_", " ").capitalize() for c in show.columns]
            show_table(show)
    with t_flags:
        if flags.empty:
            st.success("No flags raised.")
        else:
            ui.flag_cards(flags)
