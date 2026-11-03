"""Run: python -m pytest test_audit.py

Regression tests for the November 2026 audit: formulas recomputed by hand, threshold boundaries, per-case
benchmarks, input checks, ZIP limits, parser fixes and the server's security behaviour."""

import io
import json
import time
import zipfile
from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import kpis
import loans
import local_parse as lp
import repayment as rp
import security as sec
import server
from demo_data import demo_docs
from engine import Workspace, clean_case
from intake import unzip

L = 1e5


# ---------------------------------------------------------------- formulas, recomputed by hand

def test_demo_kpis_match_hand_calculation():
    y = demo_docs()[0]["extracted"]["years"][1]  # FY2024-25
    fk = kpis.analyse(demo_docs())["fin_kpis"]
    v = lambda k: fk.at[k, "FY2024-25"]  # noqa: E731
    rev, pbt, dep, fc, oth = 4280 * L, y["profit_before_tax"], 55 * L, 68 * L, 12 * L
    nw = 200 * L + 820 * L
    tnw = nw - 15 * L
    debt = (420 + 310 + 95) * L
    ebitda = pbt + fc + dep - oth
    ta = 900 * L + rev * 0.36 + 60 * L
    assert v("ebitda") == pytest.approx(ebitda)
    assert v("current_ratio") == pytest.approx(0.36 / 0.24)
    assert v("quick_ratio") == pytest.approx((0.36 - 0.12) / 0.24)
    assert v("debt_equity") == pytest.approx(debt / tnw)
    assert v("tol_tnw") == pytest.approx((ta - nw) / tnw)
    assert v("adj_tol_tnw") == pytest.approx((ta - nw - 120 * L) / (tnw + 120 * L))
    assert v("debt_ebitda") == pytest.approx(debt / ebitda)
    assert v("interest_coverage") == pytest.approx(ebitda / fc)
    assert v("dscr") == pytest.approx((y["profit_after_tax"] + dep + fc) / (fc + 95 * L))
    assert v("roe") == pytest.approx(y["profit_after_tax"] / tnw)
    assert v("roce") == pytest.approx((pbt + fc) / (tnw + debt))
    assert v("receivable_days") == pytest.approx(0.21 * 365)
    assert v("inventory_days") == pytest.approx(0.12 / 0.62 * 365)
    assert v("payable_days") == pytest.approx(0.11 / 0.62 * 365)
    assert v("cash_conversion_cycle") == pytest.approx(0.21 * 365 + 0.12 / 0.62 * 365 - 0.11 / 0.62 * 365)
    assert v("cfo_to_ebitda") == pytest.approx(rev * 0.09 / ebitda)
    assert v("free_cash_flow") == pytest.approx(rev * 0.09 - 110 * L)


def test_dscr_projection_year_one_by_hand():
    s = rp.schedule(100 * L, 12, 12, "EMI – equated instalments")
    t = rp.dscr_projection([s], cash_accrual=50 * L, existing_interest=5 * L, existing_principal=10 * L,
                           new_wc_interest=2 * L, growth_pct=0, tax_pct=25)
    i, p = s["interest"].sum(), s["principal"].sum()
    ca = 50 * L - 0.75 * (i + 2 * L)              # new interest reduces PAT after tax
    want = (ca + 5 * L + i) / (5 * L + 10 * L + i + p)  # interest added back, all debt service below
    assert t.iloc[0]["dscr"] == pytest.approx(want)


def test_eligibility_methods_by_hand():
    fin = {"revenue": 1000 * L, "growth": 0.10, "total_current_assets": 800 * L, "total_current_liabilities": 500 * L,
           "short_term_borrowings": 200 * L}
    a = loans.assess({"product": "Cash Credit (CC)", "amount": 200 * L}, fin)
    assert a["method"] == "Turnover method" and a["eligible"] == pytest.approx(0.20 * 1100 * L)  # 25% need, 5% margin
    big = loans.assess({"product": "Cash Credit (CC)", "amount": 600 * L}, fin)
    assert big["eligible"] == pytest.approx(0.75 * 800 * L - (500 - 200) * L)  # Tandon II above Rs 5 Cr
    lap = loans.assess({"product": "Loan against property (LAP)", "amount": 50 * L}, {}, 100 * L)
    assert lap["eligible"] == pytest.approx(60 * L)
    secs = [{"kind": "Residential property", "value": 100 * L, "margin_pct": 40},
            {"kind": "Fixed deposit", "value": 50 * L, "margin_pct": 10}, {"kind": "Gold", "value": 20 * L, "margin_pct": 25}]
    lap = loans.assess({"product": "Loan against property (LAP)", "amount": 50 * L}, {}, secs)
    assert lap["eligible"] == pytest.approx(60 * L)  # FD and gold do not lift a LAP limit
    lafd = loans.assess({"product": "Loan against FD / securities", "amount": 50 * L}, {}, secs)
    assert lafd["eligible"] == pytest.approx(45 * L + 15 * L) and lafd["ok"]  # each at value less its own margin
    lrd = loans.assess({"product": "Lease rental discounting (LRD)", "amount": 50 * L, "monthly_rent": 1 * L,
                        "lease_remaining_months": 12, "rate_pct": 0}, {})
    assert lrd["eligible"] == pytest.approx(0.9 * 0.85 * 1 * L * 12)


def test_interest_stress_adds_two_points_on_existing_debt():
    ws = Workspace(); ws.load_demo()
    cap = ws.build()["capacity"]
    stress = cap["stress"].set_index("Scenario")
    fin = ws.latest(ws.analysis())
    debt = ws.analysis()["fin_kpis"].at["total_debt", fin["fy"]]
    base = stress.at["Base case", "Minimum DSCR"]
    assert stress.at["Interest +2%", "Minimum DSCR"] < base
    # recompute the scenario by hand: 2% of existing debt added to existing interest, new loans re-priced +2%
    import capacity
    s = [sc for sc in ws.schedules(2.0).values()]
    want = capacity.min_dscr(s, fin["pat"] + fin["depreciation"], fin["finance_cost"] + debt * 0.02,
                             fin["current_maturities"], ws.wc_interest(2.0), ws.case["dscr"]["growth_pct"],
                             ws.case["dscr"]["tax_pct"])[0]
    assert stress.at["Interest +2%", "Minimum DSCR"] == pytest.approx(want)


def test_break_even_fall_lands_on_the_benchmark():
    import capacity
    s = [rp.schedule(300 * L, 10, 60, "EMI – equated instalments")]
    fall = capacity.break_even_fall(1.25, s, 150 * L, 10 * L, 20 * L, 0, 0, 25)
    at = capacity.min_dscr(s, 150 * L * (1 - fall), 10 * L, 20 * L, 0, 0, 25)[0]
    assert 0 < fall < 1 and at == pytest.approx(1.25, abs=1e-3)


# ---------------------------------------------------------------- security

def test_cash_margin_counted_once_and_fd_flagged():
    secs = [{"kind": "Cash margin", "value": 20 * L, "margin_pct": 0}, {"kind": "Stock (inventory)", "value": 100 * L, "margin_pct": 25}]
    assert sec.cover(secs, 75 * L)["cover"] == pytest.approx(1.0)
    fd = [{"kind": "Fixed deposit", "value": 20 * L, "margin_pct": 10}]
    assert any("cash margin" in f for _, f, _ in sec.security_flags(fd, 80 * L, "Small", 0, cash_margins=20 * L))


def test_stale_valuation_and_cgtmse_boundaries():
    def flags(**kw):
        s = {"kind": "Residential property", "value": 100 * L, "margin_pct": 40, **kw}
        return [f for _, f, _ in sec.security_flags([s], 50 * L, "Small", 50 * L)]
    assert not any("older than" in f for f in flags(valuation_date=date.today() - timedelta(days=3 * 365)))
    assert any("older than" in f for f in flags(valuation_date=date.today() - timedelta(days=3 * 365 + 1)))
    cg = [{"kind": "CGTMSE cover", "value": 0, "margin_pct": 100}]
    assert not any("CGTMSE" in f for _, f, _ in sec.security_flags(cg, 1e8, "Micro", 1e8))      # exactly Rs 10 Cr
    assert any("CGTMSE" in f for _, f, _ in sec.security_flags(cg, 1e8, "Micro", 1e8 + 1))
    assert any("CGTMSE" in f for _, f, _ in sec.security_flags(cg, 1e7, "Medium", 1e7))


# ---------------------------------------------------------------- flag boundaries

def _bank(**over):
    row = {"account": "HDFC ..1", "months_covered": 12, "period": "x", "outward_bounces": 0, "inward_returns": 0,
           "cash_deposit_share": 0.0, "related_transfer_share": 0.0, "top5_counterparty_share": None,
           "abb_to_monthly_inflow": None, "annualised_business_credits": None}
    row.update(over)
    return pd.DataFrame([row])


def _flags(bk=None, gk=None, th=None):
    t = {"financials": pd.DataFrame(), "conflicts": [], "itr": pd.DataFrame()}
    return [(f.severity, f.finding) for f in kpis.risk_flags(t, pd.DataFrame(), bk if bk is not None else pd.DataFrame(),
                                                           gk if gk is not None else pd.DataFrame(), [], th)]


@pytest.mark.parametrize("bounces,severity", [(0, None), (2, "Low"), (3, "Medium"), (5, "Medium"), (6, "High")])
def test_bounce_bands(bounces, severity):
    got = [s for s, f in _flags(_bank(outward_bounces=bounces)) if "bounce" in f]
    assert got == ([severity] if severity else [])


@pytest.mark.parametrize("share,severity", [(0.20, None), (0.2001, "Medium"), (0.40, "Medium"), (0.4001, "High")])
def test_cash_deposit_boundaries(share, severity):
    got = [s for s, f in _flags(_bank(cash_deposit_share=share)) if "Cash deposits" in f]
    assert got == ([severity] if severity else [])


@pytest.mark.parametrize("share,flagged", [(0.30, False), (0.3001, True)])
def test_fund_diversion_boundary(share, flagged):
    assert any("own / related" in f for _, f in _flags(_bank(related_transfer_share=share))) == flagged


@pytest.mark.parametrize("gap,flagged", [(0.20, False), (0.2001, True)])
def test_itc_over_2b_boundary(gap, flagged):
    gk = pd.DataFrame([{"fiscal_year": "FY2024-25", "months_filed": 12, "gst_turnover": 1.0, "late_filings": 0,
                        "gstr1_vs_3b_gap": None, "itc_excess_over_2b": gap}])
    assert any("GSTR-2B" in f for _, f in _flags(gk=gk)) == flagged


# ---------------------------------------------------------------- per-case benchmarks (issue A)

def test_thresholds_are_per_case_and_saved():
    a, b = Workspace(), Workspace()
    a.load_demo(); b.load_demo()
    a.set_thresholds({"debt_equity_max": 0.5, "dscr_min": "bad", "nonsense": 3, "tol_tnw_max": -1})
    assert a.th["debt_equity_max"] == 0.5 and a.th["dscr_min"] == 1.25 and a.th["tol_tnw_max"] == 4.0
    assert b.th == kpis.THRESHOLDS and kpis.THRESHOLDS["debt_equity_max"] == 3.0
    assert any("above 0.5x" in f for f in a.analysis()["flags"]["finding"])
    assert not any("Debt/TNW" in f for f in b.analysis()["flags"]["finding"])
    c = Workspace()
    c.load_case(a.save_case().encode())
    assert c.th["debt_equity_max"] == 0.5


# ---------------------------------------------------------------- input checks

def test_clean_case_survives_bad_input():
    ws = Workspace(); ws.load_demo()
    raw = json.loads(json.dumps(ws.case, default=str))
    raw["facilities"][1].update(tenor_months=-12, amount="abc", rate_pct=1e9, moratorium_months=999)
    raw["facilities"].append({"product": "Not a product", "amount": 1})
    raw["securities"][0].update(value=float("inf"), margin_pct=-5, valuation_date="31-31-2020", id='x" onclick="alert(1)')
    raw["dscr"] = {"tax_pct": None, "growth_pct": "x"}
    c = clean_case(raw)
    f = c["facilities"][1]
    assert f["tenor_months"] == 0 and f["amount"] == 0 and f["rate_pct"] == 100 and len(c["facilities"]) == 4
    s = c["securities"][0]
    assert s["value"] == 0 and s["margin_pct"] == 0 and s["valuation_date"] is None and s["id"] == "xonclickalert1"
    assert c["dscr"] == {"growth_pct": 5.0, "tax_pct": 25.0, "wc_utilisation_pct": 80.0}
    ws.set_case(raw)
    ws.state(); ws.report_pdf(ws.build())


def test_no_debt_service_at_all():
    ws = Workspace(); ws.load_demo()
    ws.case["facilities"] = [f for f in ws.case["facilities"] if f["product"] == "Cash Credit (CC)"]
    for y in ws.demo[0]["extracted"]["years"]:
        y["finance_cost"] = y["current_maturities_of_long_term_debt"] = 0
    ws.case["dscr"]["wc_utilisation_pct"] = 0
    st = ws.state()
    assert st["repay"]["dscr"]["tiles"][0]["value"] == "—"
    ws.report_pdf(ws.build()); ws.cam_pdf(ws.build()); ws.excel(ws.build())


def test_case_file_is_checked_before_it_replaces_the_case():
    ws = Workspace(); ws.load_demo()
    for bad in (b"not json", b"[]", b'{"case": 1}'):
        with pytest.raises(ValueError):
            ws.load_case(bad)
    assert ws.case["borrower"]["name"] == "Demo Polymers Pvt Ltd"
    evil = json.loads(ws.save_case())
    evil["financial_docs"][0]["doc_id"] = 'D01"><img src=x onerror=alert(1)>'
    ws.load_case(json.dumps(evil).encode())
    assert all(d["doc_id"].isalnum() for d in ws.demo)


# ---------------------------------------------------------------- ZIP limits (issue B)

def _zip(members, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_bomb_traversal_nesting_and_damage():
    files, skipped = unzip(_zip([("big.csv", b"0" * (60 * 1024 * 1024))]))
    assert not files and "50 MB" in skipped[0]
    files, skipped = unzip(_zip([("bomb.csv", b"0" * (20 * 1024 * 1024))]))
    assert not files and "ZIP bomb" in skipped[0]
    files, _ = unzip(_zip([("../../etc/x.pdf", b"%PDF-1.4"), ("C:/win/y.pdf", b"%PDF-1.4"), ("inner.zip", b"PK")]))
    assert [p for p, _ in files] == ["etc/x.pdf", "win/y.pdf"]
    raw = bytearray(_zip([("a.pdf", b"%PDF-1.4 hello"), ("b.pdf", b"%PDF-1.4 world")], zipfile.ZIP_STORED))
    raw[raw.find(b"hello")] ^= 0xFF
    files, skipped = unzip(bytes(raw))
    assert [p for p, _ in files] == ["b.pdf"] and "a.pdf" in skipped[0]
    files, skipped = unzip(_zip([(f"f{i}.pdf", b"%PDF") for i in range(305)]))
    assert len(files) == 300 and "limit" in skipped[-1]


# ---------------------------------------------------------------- parser

def test_amount_and_related_party_fixes():
    assert lp.to_num("12.00Dr") == -12 and lp.to_num("5,000.00DR.") == -5000 and lp.to_num("12.00 Cr") == 12
    rel = lp._related_pattern(["Demo Polymers Pvt Ltd", "Ramesh Kumar"])
    assert rel.search("NEFT DEMO POLYMERS PRIVATE LTD") and rel.search("IMPS-RAMESH KUMAR-SAL")
    assert not rel.search("NEFT RELIANCE POLYMERS LTD") and not rel.search("RAMESH TRADERS")


def test_gst_top5_share_uses_latest_year():
    g = pd.DataFrame([{"period": p, "return_type": "GSTR-3B", "taxable_turnover": 200.0, "exempt_nil_turnover": None,
                       "export_turnover": None, "tax_paid_cash": None, "itc_claimed": None, "filing_date": None,
                       "due_date": None} for p in ("2023-05", "2024-05")])
    out = kpis.gst_kpis(g, pd.DataFrame([{"taxable_value": 100.0}]))
    assert out["top5_customer_share"].iloc[-1] == pytest.approx(0.5) and pd.isna(out["top5_customer_share"].iloc[0])


# ---------------------------------------------------------------- server

@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(server, "APP_PASSWORD", "पासवर्ड-secret")
    server.failed_logins.clear()
    return TestClient(server.app, raise_server_exceptions=False)


def _signed_in(c):
    assert c.post("/api/login", json={"password": "पासवर्ड-secret"}).status_code == 200
    assert c.get("/api/session").status_code == 200
    return c


def test_sign_in_unicode_password_rate_limit_and_expiry(gated, monkeypatch):
    n = len(server.sessions)
    assert gated.get("/api/session").json()["auth_required"] and len(server.sessions) == n  # no case before sign-in
    assert gated.post("/api/login", json={"password": "गलत"}).status_code == 401
    assert gated.post("/api/login", content=b"{", headers={"Content-Type": "application/json"}).status_code == 400
    _signed_in(gated)
    assert gated.get("/api/state").status_code == 200
    gated.cookies.set("cw_auth", server._sign(int(time.time()) - 5))  # expired
    assert gated.get("/api/state").status_code == 401
    gated.cookies.set("cw_auth", "9999999999.forged")
    assert gated.get("/api/state").status_code == 401
    server.failed_logins.clear()
    for _ in range(server.LOGIN_TRIES):
        gated.post("/api/login", json={"password": "wrong"})
    r = gated.post("/api/login", json={"password": "पासवर्ड-secret"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    server.failed_logins.clear()


def test_errors_are_clean_and_headers_set(gated):
    c = _signed_in(gated)
    assert c.post("/api/read/evil").status_code == 404
    assert c.patch("/api/files/docs/D99", json={}).status_code == 404
    r = c.put("/api/case", content=b"[1,2]", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = c.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
    assert c.get("/openapi.json").status_code == 404


def test_unicode_company_name_downloads(gated):
    c = _signed_in(gated)
    case = c.post("/api/demo").json()["case"]
    case["borrower"]["name"] = "श्री गणेश ट्रेडर्स प्राइवेट लिमिटेड"
    c.put("/api/case", json=case)
    for path in ("report.pdf", "cam.pdf", "credit_note.xlsx", "credit_file.zip", "case.json"):
        r = c.get(f"/api/export/{path}")
        assert r.status_code == 200, path
        assert "filename*=UTF-8''%E0%A4%B6" in r.headers["Content-Disposition"]


def test_upload_limits(gated, monkeypatch):
    c = _signed_in(gated)
    monkeypatch.setattr(server, "MAX_FILE_MB", 1)
    r = c.post("/api/files/docs", files=[("files", ("big.pdf", b"%PDF" + b"0" * (2 * 1024 * 1024), "application/pdf"))])
    assert r.status_code == 413
    monkeypatch.setattr(server, "MAX_UPLOAD_MB", 1)
    r = c.post("/api/files/docs", files=[("files", ("big.pdf", b"0" * (2 * 1024 * 1024), "application/pdf"))])
    assert r.status_code == 413


def test_thresholds_do_not_leak_between_users(gated):
    a = _signed_in(gated)
    b = _signed_in(TestClient(server.app, raise_server_exceptions=False))
    a.post("/api/demo"); b.post("/api/demo")
    a.put("/api/settings", json={"thresholds": {"dscr_min": 9}})
    bench = lambda c: [t["bench"] for t in c.get("/api/state").json()["summary"]["tiles"] if t["label"].startswith("Min DSCR")]  # noqa: E731
    assert bench(a) == ["≥ 9.00x"] and bench(b) == ["≥ 1.25x"]
    assert a.put("/api/settings", json={"thresholds": [1]}).status_code == 400
    assert a.put("/api/settings", json={"api_key": "not-a-key"}).status_code in (200, 400)


def test_unexpected_errors_do_not_leak_details(gated, monkeypatch):
    c = _signed_in(gated)
    monkeypatch.setattr(server.views, "state", lambda *a: (_ for _ in ()).throw(RuntimeError("secret detail C:/path")))
    r = c.get("/api/state")
    assert r.status_code == 500 and "secret detail" not in r.text and "reference" in r.text
