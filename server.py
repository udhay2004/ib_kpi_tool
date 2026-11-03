"""Web server: the HTML / JS front end in static/ and a JSON API over engine.Workspace.

Run:  uvicorn server:app --port 8000
Each browser gets its own case, kept in memory on the server (a cookie names it). Save the case file to keep work
across restarts. Set APP_PASSWORD to require a team password, ANTHROPIC_API_KEY to read scanned documents.
"""

import hashlib
import hmac
import io
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import kpis
import views
from engine import Workspace

ROOT = Path(__file__).parent
APP_PASSWORD = os.environ.get("APP_PASSWORD") or None
SERVER_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
SESSION_HOURS = 12
MAX_SESSIONS = 200

app = FastAPI(title="Credit Appraisal Workbench", docs_url=None, redoc_url=None)
sessions: dict[str, tuple[Workspace, float]] = {}


def _token() -> str:
    return hmac.new(APP_PASSWORD.encode(), b"credit-workbench", hashlib.sha256).hexdigest() if APP_PASSWORD else ""


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if APP_PASSWORD and path.startswith("/api/") and path not in ("/api/login", "/api/health", "/api/session"):
        if not hmac.compare_digest(request.cookies.get("cw_auth", ""), _token()):
            return JSONResponse({"error": "Sign in first"}, status_code=401)
    response = await call_next(request)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def ws_of(request: Request) -> Workspace:
    sid = request.cookies.get("cw_sid")
    if not sid or sid not in sessions:
        raise HTTPException(409, "Session expired - reload the page")
    ws, _ = sessions[sid]
    sessions[sid] = (ws, time.time())
    return ws


def unit_of(request: Request) -> str:
    return request.query_params.get("unit") or "₹ Crore"


def ok(request: Request, ws: Workspace):
    return views.state(ws, unit_of(request), bool(SERVER_KEY))


# ---------------------------------------------------------------- session and sign-in

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    """Starts (or resumes) this browser's case. Says whether a sign-in is needed."""
    now = time.time()
    for k in [k for k, (_, t) in sessions.items() if now - t > SESSION_HOURS * 3600]:
        sessions.pop(k, None)
    sid = request.cookies.get("cw_sid")
    resp = JSONResponse({"auth_required": bool(APP_PASSWORD) and not hmac.compare_digest(request.cookies.get("cw_auth", ""), _token()),
                         "server_key": bool(SERVER_KEY)})
    if not sid or sid not in sessions:
        if len(sessions) >= MAX_SESSIONS:
            sessions.pop(min(sessions, key=lambda k: sessions[k][1]))
        sid = secrets.token_urlsafe(24)
        sessions[sid] = (Workspace(), now)
        resp.set_cookie("cw_sid", sid, httponly=True, samesite="lax", max_age=SESSION_HOURS * 3600)
    return resp


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if not APP_PASSWORD or hmac.compare_digest(str(body.get("password", "")), APP_PASSWORD):
        resp = JSONResponse({"ok": True})
        resp.set_cookie("cw_auth", _token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
        return resp
    return JSONResponse({"error": "Wrong password"}, status_code=401)


@app.get("/api/meta")
def meta():
    return views.meta()


# ---------------------------------------------------------------- case

@app.get("/api/state")
def get_state(request: Request):
    return ok(request, ws_of(request))


@app.put("/api/case")
async def put_case(request: Request):
    ws = ws_of(request)
    ws.set_case(await request.json())
    return ok(request, ws)


@app.post("/api/demo")
def demo(request: Request):
    ws = ws_of(request)
    ws.load_demo()
    return ok(request, ws)


@app.post("/api/reset")
def reset(request: Request):
    ws = ws_of(request)
    key = ws.api_key
    ws.reset()
    ws.api_key = key
    return ok(request, ws)


@app.post("/api/open")
async def open_case(request: Request, file: UploadFile = File(...)):
    ws = ws_of(request)
    try:
        ws.load_case(await file.read())
    except (ValueError, KeyError) as ex:
        raise HTTPException(400, f"Not a saved case file ({ex})")
    return ok(request, ws)


@app.put("/api/settings")
async def settings(request: Request):
    ws = ws_of(request)
    body = await request.json()
    if "api_key" in body and not SERVER_KEY:
        ws.api_key = (body["api_key"] or "").strip() or None
    for k, v in (body.get("thresholds") or {}).items():
        if k in kpis.THRESHOLDS:
            kpis.THRESHOLDS[k] = float(v)
    return ok(request, ws)


@app.put("/api/tables")
async def put_tables(request: Request):
    ws = ws_of(request)
    ws.set_tables(await request.json())
    return ok(request, ws)


# ---------------------------------------------------------------- files

STORES = {"docs", "kyc_docs", "sec_docs"}


@app.post("/api/files/{store}")
async def upload(request: Request, store: str, files: list[UploadFile] = File(...), doc_type: str = Form("auto"),
                 person: str = Form("")):
    if store not in STORES:
        raise HTTPException(404)
    ws = ws_of(request)
    for f in files:
        data = await f.read()
        if store == "docs" and doc_type == "zip":
            ws.add_zip(data)
        elif store == "kyc_docs":
            import kyc
            is_person = doc_type == "person"
            ws.add_file(store, f.filename, data, kyc.guess_kind(f.filename, is_person), person if is_person else None)
        else:
            ws.add_file(store, f.filename, data, doc_type)
    return ok(request, ws)


@app.patch("/api/files/{store}/{doc_id}")
async def update_file(request: Request, store: str, doc_id: str):
    ws = ws_of(request)
    body = await request.json()
    if doc_id not in ws.stores.get(store, {}):
        raise HTTPException(404)
    ws.update_file(store, doc_id, body.get("doc_type"), body.get("person"))
    return ok(request, ws)


@app.delete("/api/files/{store}/{doc_id}")
def delete_file(request: Request, store: str, doc_id: str):
    ws = ws_of(request)
    ws.remove_file(store, doc_id)
    return ok(request, ws)


@app.post("/api/files/{store}/{doc_id}/unlock")
async def unlock(request: Request, store: str, doc_id: str):
    ws = ws_of(request)
    body = await request.json()
    if doc_id not in ws.stores.get(store, {}):
        raise HTTPException(404)
    if not ws.unlock(store, doc_id, str(body.get("password", ""))):
        raise HTTPException(400, "Wrong password")
    return ok(request, ws)


@app.post("/api/read/{store}")
def read(request: Request, store: str):
    ws = ws_of(request)
    ws.read(store, SERVER_KEY or ws.api_key)
    return ok(request, ws)


# ---------------------------------------------------------------- downloads

def _download(data: bytes, name: str, mime: str) -> Response:
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/export/report.pdf")
def report(request: Request):
    ws = ws_of(request)
    return _download(ws.report_pdf(ws.build()), f"{ws.file_name()}_credit_report.pdf", "application/pdf")


@app.get("/api/export/cam.pdf")
def cam(request: Request):
    ws = ws_of(request)
    return _download(ws.cam_pdf(ws.build()), f"{ws.file_name()}_CAM_Lite_internal.pdf", "application/pdf")


@app.get("/api/export/credit_note.xlsx")
def credit_note(request: Request):
    ws = ws_of(request)
    return _download(ws.excel(ws.build()), f"{ws.file_name()}_credit_note.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/export/credit_file.zip")
def credit_file(request: Request):
    ws = ws_of(request)
    s = ws.build()
    try:
        pdf = ws.report_pdf(s)
    except Exception:  # never lose the other files because of one bad document
        pdf = None
    return _download(ws.credit_file_zip(ws.excel(s), ws.file_name(), pdf), f"{ws.file_name()}_credit_file.zip",
                     "application/zip")


@app.get("/api/export/case.json")
def case_file(request: Request):
    ws = ws_of(request)
    return _download(ws.save_case().encode(), f"{ws.file_name()}_case.json", "application/json")


@app.exception_handler(Exception)
async def crash(request: Request, ex: Exception):
    return JSONResponse({"error": f"{type(ex).__name__}: {ex}"}, status_code=500)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
