"""Step 5 - repayment capacity (how they will repay), structure requested, eligibility per facility."""

from html import escape

import pandas as pd
import streamlit as st

import capacity
import kpis
import loans
import repayment as rp
import security as sec
import ui
from common import facility_totals, inr, ss
from steps.financials import analysis, bar_chart, latest, line_chart


def repay_cfg(fac: dict) -> dict:
    cfg = ss.case["repay"].setdefault(fac["id"], {"method": "EMI – equated instalments", "freq": "Monthly",
                                                    "step_up_pct": 10.0, "structured": "10,15,20,25,30"})
    return cfg


def schedules(rate_add: float = 0.0) -> dict:
    """fac_id -> schedule DataFrame for amortising / bullet facilities with an amount and tenor.
    rate_add: percentage points added to every rate (stress test)."""
    out = {}
    for fac in ss.case["facilities"]:
        p = loans.product(fac["product"])
        if p["repay"] not in ("amortising", "bullet") or not fac.get("amount") or not fac.get("tenor_months"):
            continue
        cfg = repay_cfg(fac)
        method = "Bullet" if p["repay"] == "bullet" and cfg["method"].startswith("EMI") else cfg["method"]
        pcts = [float(x) for x in str(cfg.get("structured", "")).split(",") if x.strip()]
        out[fac["id"]] = rp.schedule(fac["amount"], (fac.get("rate_pct") or 0) + rate_add, fac["tenor_months"], method,
                                     cfg["freq"],
                                     fac.get("moratorium_months") or 0, cfg["step_up_pct"], pcts)
    return out


def assessments(fin: dict) -> dict:
    prop_value = sum(s.get("value") or 0 for s in ss.case["securities"]
                     if sec.SECURITY_TYPES[s["kind"]][0] == "Collateral")
    return {f["id"]: loans.assess(f, fin, prop_value) for f in ss.case["facilities"]}


def wc_interest(rate_add: float = 0.0) -> float:
    """Annual interest on new CC / OD limits at the assumed average utilisation."""
    u = ss.case["dscr"]["wc_utilisation_pct"] / 100
    return sum((f.get("amount") or 0) * u * ((f.get("rate_pct") or 0) + rate_add) / 100
               for f in ss.case["facilities"] if loans.product(f["product"])["repay"] in ("revolving", "demand"))


def dscr_table(fin: dict):
    """Year-wise DSCR on the proposal, or None without financials / term debt."""
    d = ss.case["dscr"]
    if not fin.get("pat") and fin.get("pat") != 0:
        return None
    return rp.dscr_projection(list(schedules().values()), (fin.get("pat") or 0) + (fin.get("depreciation") or 0),
                              fin.get("finance_cost") or 0, fin.get("current_maturities") or 0, wc_interest(),
                              d["growth_pct"], d["tax_pct"])


def capacity_result(res=None):
    """Repayment capacity analysis for the case, or None without financials."""
    res = res if res is not None else analysis()
    t = facility_totals(ss.case["facilities"])
    return capacity.assess(res, ss.case["facilities"], lambda r: list(schedules(r).values()), ss.case["dscr"],
                           sec.cover(ss.case["securities"], t["net_exposure"]), wc_interest)


def render(div, unit_label):
    case = ss.case
    if not case["facilities"]:
        st.info("Add the facilities requested in step ① first.")
        return
    res = analysis()
    fin = latest(res)
    if not fin:
        st.warning("No financial statements read yet (step ④). Repayment capacity, turnover / MPBF eligibility and "
                   "DSCR need them; you can still set the repayment structure.")
    t1, t2, t3 = st.tabs(["How they will repay", "Repayment structure requested", "Eligibility"])
    with t2:
        _structure(case, div, unit_label)
        _dscr(case, fin, div)
    with t3:
        _eligibility(case, fin)
    with t1:
        _capacity(res, div)


def _capacity(res, div):
    cap = capacity_result(res)
    if cap is None:
        st.info("Needs the financial statements from step ④.")
        return
    v = cap["verdict"]
    st.markdown(f'<div class="card"><div class="muted">Repayment capacity · based on {cap["fy"]} financials, bank '
                f'statements, GST and the structure requested</div><div style="margin:6px 0 4px">'
                f'{ui.pill(v["status"], v["label"])}</div><div>{escape(v["meaning"])}</div></div>',
                unsafe_allow_html=True)
    ui.section("Observations", "Written from the numbers below. They go into the credit note.")
    for n in cap["notes"]:
        st.markdown(f'<div class="card" style="margin-bottom:10px"><div style="font-weight:640;margin-bottom:4px">'
                    f'{ui.pill(n["status"] or "good", n["title"])}</div><div>{escape(n["text"])}</div></div>',
                    unsafe_allow_html=True)
    ui.section("Key measures")
    m = cap["metrics"].copy()
    m["Status"] = m["Status"].map({"good": "✅ OK", "warning": "⚠️ Watch", "critical": "⛔ Concern"}).fillna("")
    st.dataframe(m, hide_index=True, use_container_width=True)
    ui.section("Stress test", "Minimum and average DSCR on the proposal if conditions worsen. Revenue falls hit cash "
               "through the contribution margin (revenue less materials), after tax.")
    stx = cap["stress"].copy()
    for c in ("Minimum DSCR", "Average DSCR"):
        stx[c] = stx[c].map(lambda x: "—" if x is None or pd.isna(x) else f"{x:.2f}x")
    stx["Status"] = stx["Status"].map({"good": "✅ ≥ 1.25x", "warning": "⚠️ 1.0–1.25x", "critical": "⛔ < 1.0x"}).fillna("")
    st.dataframe(stx, hide_index=True, use_container_width=True)
    st.caption("Change growth, CC/OD utilisation and tax rate in the “Repayment structure requested” tab.")


def _eligibility(case, fin):
    ui.section("Eligibility", "Requested amount against the standard assessment method for each facility.")
    for fac in case["facilities"]:
        a = assessments(fin)[fac["id"]]
        p = loans.product(fac["product"])
        with st.container(border=True):
            st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
            c1, c2 = st.columns([3, 2], vertical_alignment="center")
            c1.markdown(f"**{fac['product']}** · requested **{inr(fac.get('amount'))}**  \n"
                        f"<span class='muted'>{p['category']} · {a['method'] or 'No formula'}</span>", unsafe_allow_html=True)
            if a["eligible"] is not None:
                badge = ui.pill("good" if a["ok"] else "critical",
                                f"Eligible {inr(a['eligible'])} · {'within' if a['ok'] else 'above'} limit")
            else:
                badge = ui.neutral_pill("Needs more inputs")
            c2.markdown(badge, unsafe_allow_html=True)
            for w in a["workings"]:
                st.caption("• " + w)



def _structure(case, div, unit_label):
    ui.section("Repayment requested", "How the client wants to repay each facility.")
    sch = schedules()
    for fac in case["facilities"]:
        p = loans.product(fac["product"])
        with st.container(border=True):
            st.markdown('<span class="card-marker"></span>', unsafe_allow_html=True)
            st.markdown(f"**{fac['product']}** — {inr(fac.get('amount'))}"
                        + (f" · {fac.get('tenor_months')} months · {fac.get('rate_pct')}%" if fac.get("tenor_months") else ""))
            if p["repay"] not in ("amortising", "bullet"):
                st.markdown(f'<span class="muted">{rp.NON_AMORTISING[p["repay"]]}</span>', unsafe_allow_html=True)
                continue
            cfg = repay_cfg(fac)
            k = f"rp_{fac['id']}"
            c1, c2, c3 = st.columns([2, 1, 1])
            methods = list(rp.METHODS)
            cfg["method"] = c1.selectbox("Repayment method", methods, index=methods.index(cfg["method"]), key=f"{k}_m",
                                         help=rp.METHODS[cfg["method"]])
            cfg["freq"] = c2.selectbox("Frequency", list(rp.FREQUENCIES), index=list(rp.FREQUENCIES).index(cfg["freq"]),
                                       key=f"{k}_f")
            fac["moratorium_months"] = int(c3.number_input("Moratorium (months)", 0, int(fac.get("tenor_months") or 0),
                                                           int(fac.get("moratorium_months") or 0), key=f"{k}_mor"))
            if cfg["method"].startswith("Step-up"):
                cfg["step_up_pct"] = st.number_input("Step-up per year (%)", 0.0, 100.0, float(cfg["step_up_pct"]), key=f"{k}_s")
            if cfg["method"].startswith("Structured"):
                cfg["structured"] = st.text_input("Principal % per year (comma separated, should total 100)",
                                                  cfg["structured"], key=f"{k}_p")
                total = sum(float(x) for x in cfg["structured"].split(",") if x.strip().replace(".", "", 1).isdigit())
                if abs(total - 100) > 0.5:
                    st.warning(f"These add up to {total:.0f}% — the balance is collected in the last instalment.")
            s = sch.get(fac["id"])
            if s is None:
                st.caption("Set amount and tenor in step ① to see the schedule.")
                continue
            y = rp.yearly(s)
            first = s[s["principal"] > 0].head(1)
            ui.kpi_tiles([
                {"label": "First full instalment", "value": inr(first["instalment"].iloc[0]) if not first.empty else "—"},
                {"label": "Total interest", "value": inr(s["interest"].sum())},
                {"label": "Largest year's outgo", "value": inr(y["instalment"].max())},
                {"label": "Instalments", "value": f"{int((s['principal'] > 0).sum())}"},
            ])
            chart = y[["principal", "interest"]].rename(columns=str.capitalize) / div
            chart.index = [f"Y{i}" for i in chart.index]
            st.altair_chart(bar_chart(chart, unit_label, stack=True), use_container_width=True)
            with st.expander("Full schedule"):
                show = s[["period", "month", "opening", "interest", "principal", "instalment", "closing"]].copy()
                for c in ("opening", "interest", "principal", "instalment", "closing"):
                    show[c] = show[c].map(lambda v: f"{v:,.0f}")
                st.dataframe(show, hide_index=True, use_container_width=True, height=320)



def _dscr(case, fin, div):
    ui.section("Debt service on the proposal", "DSCR = (PAT + depreciation + interest) ÷ (interest + principal), "
               "year by year, with the new facilities added on top of existing debt.")
    d = case["dscr"]
    c1, c2, c3 = st.columns(3)
    d["growth_pct"] = c1.number_input("Cash-accrual growth per year (%)", -50.0, 100.0, float(d["growth_pct"]), key="dscr_g")
    d["wc_utilisation_pct"] = c2.number_input("Average utilisation of new CC/OD (%)", 0.0, 100.0,
                                              float(d["wc_utilisation_pct"]), key="dscr_u")
    d["tax_pct"] = c3.number_input("Tax rate (%)", 0.0, 50.0, float(d["tax_pct"]), key="dscr_t")
    if not fin:
        st.info("Needs the financial statements from step ④.")
        return
    st.caption(f"Base ({fin['fy']}): PAT {inr(fin.get('pat'))} + depreciation {inr(fin.get('depreciation'))}; existing "
               f"interest {inr(fin.get('finance_cost'))} and principal {inr(fin.get('current_maturities'))} assumed to "
               "continue (conservative).")
    t = dscr_table(fin)
    if t is None or t.empty:
        return
    ds = t["dscr"].dropna()
    th = kpis.THRESHOLDS
    ui.kpi_tiles([
        {"label": "Minimum DSCR", "value": f"{ds.min():.2f}x", "status": "good" if ds.min() >= th["dscr_min"] else "critical",
         "bench": f"≥ {th['dscr_min']:.2f}x"},
        {"label": "Average DSCR", "value": f"{ds.mean():.2f}x",
         "status": "good" if ds.mean() >= th["dscr_avg_min"] else "critical", "bench": f"≥ {th['dscr_avg_min']:.2f}x"},
        {"label": "Peak debt service", "value": inr(t["total_debt_service"].max())},
        {"label": "Years", "value": str(len(t))},
    ])
    chart = t.set_index("year")[["dscr"]].rename(columns={"dscr": "DSCR"})
    chart.index = [f"Y{i}" for i in chart.index]
    st.altair_chart(line_chart(chart, "DSCR (x)", rule=th["dscr_min"]), use_container_width=True)
    show = t.copy()
    for c in ("cash_accrual", "available_for_debt_service", "new_interest", "new_principal", "existing_debt_service",
              "total_debt_service"):
        show[c] = show[c].map(lambda v: f"{v / div:,.2f}")
    show["dscr"] = show["dscr"].map(lambda v: f"{v:.2f}x")
    show.columns = [c.replace("_", " ").capitalize() for c in show.columns]
    st.dataframe(show, hide_index=True, use_container_width=True)
