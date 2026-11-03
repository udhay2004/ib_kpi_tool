"""Run: python -m pytest test_server.py"""

import io
import json
import zipfile

import fitz
import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client():
    c = TestClient(server.app)
    assert c.get("/api/session").status_code == 200
    return c


def _pdf(text="page") -> bytes:
    d = fitz.open()
    d.new_page().insert_text((72, 100), text)
    return d.tobytes()


def test_empty_case_renders(client):
    s = client.get("/api/state").json()
    assert s["case"]["borrower"]["name"] == "" and s["repay"] is None
    assert s["summary"]["flags"][0]["finding"] == "No facilities entered"
    assert client.get("/").status_code == 200 and "Credit Appraisal Workbench" in client.get("/").text


def test_demo_runs_every_section(client):
    s = client.post("/api/demo?unit=₹ Lakh").json()
    assert s["case"]["borrower"]["name"] == "Demo Polymers Pvt Ltd" and s["unit"] == "₹ Lakh"
    assert s["fin"]["analysis"]["tiles"] and s["fin"]["review"]["financials"]["years"]
    assert s["repay"]["capacity"]["verdict"]["label"] == "Comfortable"
    assert len(s["repay"]["structure"]) == 4 and s["repay"]["dscr"]["tiles"]
    assert s["security"]["tiles"][3]["value"] == "1.41x"
    assert [x["name"] for x in s["summary"]["steps"]][0] == "Loan request"
    json.dumps(s)  # all JSON-safe


def test_case_edit_round_trips_dates_and_recalculates(client):
    s = client.post("/api/demo").json()
    case = s["case"]
    assert case["securities"][0]["valuation_date"] == "2022-03-15"
    case["facilities"][1]["amount"] = 40 * 1e7
    s2 = client.put("/api/case", json=case).json()
    assert s2["totals"]["total"] > s["totals"]["total"]
    assert s2["case"]["securities"][0]["valuation_date"] == "2022-03-15"


def test_upload_read_remove_and_exports(client):
    client.post("/api/demo")
    s = client.post("/api/files/kyc_docs", files=[("files", ("Company_PAN.pdf", _pdf("PAN"), "application/pdf"))],
                    data={"doc_type": "entity"}).json()
    f = s["kyc"]["files"][0]
    assert f["doc_type"] == "entity_pan" and f["doc_id"] == "K14"
    s = client.patch(f"/api/files/kyc_docs/{f['doc_id']}", json={"doc_type": "moa"}).json()
    assert s["kyc"]["files"][0]["doc_type"] == "moa"
    for path, kind in (("report.pdf", b"%PDF"), ("cam.pdf", b"%PDF"), ("credit_note.xlsx", b"PK"), ("credit_file.zip", b"PK")):
        r = client.get(f"/api/export/{path}")
        assert r.status_code == 200 and r.content[:len(kind)] == kind, path
    names = zipfile.ZipFile(io.BytesIO(client.get("/api/export/credit_file.zip").content)).namelist()
    assert any(n.startswith("KYC/K14") for n in names)
    saved = client.get("/api/export/case.json").content
    s = client.delete(f"/api/files/kyc_docs/{f['doc_id']}").json()
    assert not s["kyc"]["files"]
    s = client.post("/api/reset").json()
    assert s["case"]["borrower"]["name"] == ""
    s = client.post("/api/open", files=[("file", ("case.json", saved, "application/json"))]).json()
    assert s["case"]["borrower"]["name"] == "Demo Polymers Pvt Ltd" and s["fin"]["analysis"]


def test_password_gate(monkeypatch):
    monkeypatch.setattr(server, "APP_PASSWORD", "secret")
    c = TestClient(server.app)
    assert c.get("/api/session").json()["auth_required"]
    assert c.get("/api/state").status_code == 401
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"password": "secret"}).status_code == 200
    assert c.get("/api/state").status_code == 200
