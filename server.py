"""Web server: the HTML / JS front end in static/ and a JSON API over engine.Workspace.

Run:  uvicorn server:app --port 8000
Each signed-in browser gets its own case, kept in memory on the server (a cookie names it) and dropped after
SESSION_HOURS idle or on a restart. Save the case file to keep work.

Environment
  APP_PASSWORD       team password screen (strongly recommended; without it anyone with the URL can use the app)
  SESSION_SECRET     optional; signs the sign-in cookie (defaults to one derived from APP_PASSWORD)
  ANTHROPIC_API_KEY  optional; only for scanned documents and valuation reports
  MAX_FILE_MB / MAX_UPLOAD_MB / MAX_CASE_MB / MAX_SESSIONS / SESSION_HOURS / AUTH_DAYS  optional limits
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import kyc
import views
from engine import Workspace

ROOT = Path(__file__).parent
APP_PASSWORD = os.environ.get("APP_PASSWORD") or None
SERVER_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
SESSION_SECRET = os.environ.get("SESSION_SECRET") or None


def _int_env(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


SESSION_HOURS = _int_env("SESSION_HOURS", 12)
AUTH_DAYS = _int_env("AUTH_DAYS", 7)
MAX_SESSIONS = _int_env("MAX_SESSIONS", 200)
MAX_FILE_MB = _int_env("MAX_FILE_MB", 50)        # one uploaded file
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 200)   # one upload request (several files)
MAX_CASE_MB = _int_env("MAX_CASE_MB", 400)       # all files kept on one case
MAX_JSON_KB = 5 * 1024                            # a JSON request body (the case, edited tables)
LOGIN_TRIES, LOGIN_WINDOW = 8, 15 * 60            # failed sign-ins per IP address per 15 minutes
STORES = {"docs", "kyc_docs", "sec_docs"}
MB = 1024 * 1024

log = logging.getLogger("workbench")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
if not APP_PASSWORD:
    log.warning("APP_PASSWORD is not set: anyone with the URL can use the app")

app = FastAPI(title="Credit Appraisal Workbench", docs_url=None, redoc_url=None, openapi_url=None)
sessions: dict[str, tuple[Workspace, float]] = {}
sessions_lock = threading.Lock()
failed_logins: dict[str, list[float]] = {}

CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: blob: "
       "https://compliance.complyglobally.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")


# ---------------------------------------------------------------- sign-in token

def _secret() -> bytes:
    return hashlib.sha256(f"cw-auth|{SESSION_SECRET or ''}|{APP_PASSWORD or ''}".encode("utf-8")).digest()


def _sign(expires: int) -> str:
    return f"{expires}.{hmac.new(_secret(), f'auth:{expires}'.encode(), hashlib.sha256).hexdigest()}"


def _authed(request: Request) -> bool:
    """True without a password, or with an unexpired, correctly signed cookie. Changing APP_PASSWORD (or
    SESSION_SECRET) signs everyone out."""
    if not APP_PASSWORD:
        return True
    token = request.cookies.get("cw_auth", "")
    exp, _, _ = token.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    return hmac.compare_digest(token.encode("utf-8"), _sign(int(exp)).encode("utf-8"))


def _password_ok(given) -> bool:
    # compare bytes: hmac.compare_digest refuses str with non-ASCII characters
    return isinstance(given, str) and hmac.compare_digest(given.encode("utf-8"), APP_PASSWORD.encode("utf-8"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"  # the real address: uvicorn runs with --proxy-headers


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


# ---------------------------------------------------------------- middleware and errors

@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/login", "/api/health", "/api/session"):
        if not _authed(request):
            return JSONResponse({"error": "Sign in first"}, status_code=401)
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            upload = path.startswith("/api/files/") or path == "/api/open"
            limit = (MAX_UPLOAD_MB * MB) if upload else MAX_JSON_KB * 1024
            if length is None and request.headers.get("transfer-encoding"):
                return JSONResponse({"error": "Send the request with a Content-Length"}, status_code=411)
            if length and length.isdigit() and int(length) > limit:
                return JSONResponse({"error": f"Too large - the limit is {limit // MB if upload else limit // 1024} "
                                              f"{'MB' if upload else 'KB'} per request"}, status_code=413)
    response = await call_next(request)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "same-origin", "Content-Security-Policy": CSP})
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, ex: StarletteHTTPException):
    return JSONResponse({"error": str(ex.detail)}, status_code=ex.status_code)


@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, ex: RequestValidationError):
    return JSONResponse({"error": "The request is missing a field or has the wrong format"}, status_code=400)


@app.exception_handler(Exception)
async def crash(request: Request, ex: Exception):
    ref = uuid.uuid4().hex[:8]
    log.exception("unhandled error %s on %s %s", ref, request.method, request.url.path)
    return JSONResponse({"error": f"Something went wrong on the server (reference {ref}). Try again; if it keeps "
                                  "happening, save the case file and report the reference."}, status_code=500)


async def body_json(request: Request) -> dict:
    """The JSON body, or 400. Bodies are capped at MAX_JSON_KB even without a Content-Length."""
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_JSON_KB * 1024:
            raise HTTPException(413, f"Too large - the limit is {MAX_JSON_KB} KB")
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "The request body is not valid JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object")
    return data


# ---------------------------------------------------------------- sessions

def ws_of(request: Request) -> Workspace:
    sid = request.cookies.get("cw_sid")
    with sessions_lock:
        if not sid or sid not in sessions:
            raise HTTPException(409, "Session expired - reload the page")
        ws, _ = sessions[sid]
        sessions[sid] = (ws, time.time())
    return ws


def _store(store: str) -> str:
    if store not in STORES:
        raise HTTPException(404, "Unknown document store")
    return store


def unit_of(request: Request) -> str:
    return request.query_params.get("unit") or "₹ Crore"


def state_of(request: Request, ws: Workspace) -> dict:
    return views.state(ws, unit_of(request), bool(SERVER_KEY))


async def run(request: Request, work=None):
    """Runs work(ws) and builds the new state in a worker thread (never on the event loop), one request at a time
    per case. work may raise ValueError for bad input (400) or KeyError for an unknown document (404)."""
    ws = ws_of(request)

    def job():
        with ws.lock:
            if work:
                try:
                    work(ws)
                except ValueError as ex:
                    raise HTTPException(400, str(ex))
                except KeyError:
                    raise HTTPException(404, "No such document")
            return state_of(request, ws)
    return await run_in_threadpool(job)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    """Says whether a sign-in is needed and, once signed in, starts (or resumes) this browser's case."""
    authed = _authed(request)
    resp = JSONResponse({"auth_required": not authed, "server_key": bool(SERVER_KEY)})
    if not authed:
        return resp
    now = time.time()
    sid = request.cookies.get("cw_sid")
    with sessions_lock:
        for k in [k for k, (_, t) in sessions.items() if now - t > SESSION_HOURS * 3600]:
            sessions.pop(k, None)
        if not sid or sid not in sessions:
            if len(sessions) >= MAX_SESSIONS:
                sessions.pop(min(sessions, key=lambda k: sessions[k][1]))
                log.warning("session limit reached: dropped the longest-idle case")
            sid = secrets.token_urlsafe(24)
            sessions[sid] = (Workspace(), now)
            resp.set_cookie("cw_sid", sid, httponly=True, samesite="lax", secure=_secure(request),
                            max_age=SESSION_HOURS * 3600)
    return resp


@app.post("/api/login")
async def login(request: Request):
    ip, now = _ip(request), time.time()
    recent = [t for t in failed_logins.get(ip, []) if now - t < LOGIN_WINDOW]
    failed_logins[ip] = recent
    if len(recent) >= LOGIN_TRIES:
        wait = int(LOGIN_WINDOW - (now - recent[0])) + 1
        return JSONResponse({"error": f"Too many attempts - try again in {wait // 60 + 1} minutes"}, status_code=429,
                            headers={"Retry-After": str(wait)})
    body = await body_json(request)
    if APP_PASSWORD and not _password_ok(body.get("password")):
        failed_logins[ip].append(now)
        if len(failed_logins) > 10000:  # keep the table small
            failed_logins.clear()
        log.info("failed sign-in from %s", ip)
        return JSONResponse({"error": "Wrong password"}, status_code=401)
    failed_logins.pop(ip, None)
    resp = JSONResponse({"ok": True})
    expires = int(now + AUTH_DAYS * 86400)
    resp.set_cookie("cw_auth", _sign(expires), httponly=True, samesite="lax", secure=_secure(request),
                    max_age=AUTH_DAYS * 86400)
    return resp


@app.post("/api/logout")
def logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("cw_auth")
    return resp


@app.get("/api/meta")
def meta():
    return views.meta()


# ---------------------------------------------------------------- case

@app.get("/api/state")
async def get_state(request: Request):
    return await run(request)


@app.put("/api/case")
async def put_case(request: Request):
    body = await body_json(request)
    return await run(request, lambda ws: ws.set_case(body))


@app.post("/api/demo")
async def demo(request: Request):
    return await run(request, lambda ws: ws.load_demo())


@app.post("/api/reset")
async def reset(request: Request):
    def work(ws):
        key = ws.api_key
        ws.reset()
        ws.api_key = key
    return await run(request, work)


@app.post("/api/open")
async def open_case(request: Request, file: UploadFile = File(...)):
    raw = await _read_capped(file, MAX_JSON_KB * 1024 * 4)
    return await run(request, lambda ws: ws.load_case(raw))


@app.put("/api/settings")
async def settings(request: Request):
    body = await body_json(request)

    def work(ws):
        if "api_key" in body and not SERVER_KEY:
            key = str(body["api_key"] or "").strip()
            if key and not re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{10,200}", key):
                raise ValueError("That does not look like an Anthropic API key (sk-ant-…)")
            ws.api_key = key or None
        if "thresholds" in body:
            if not isinstance(body["thresholds"], dict):
                raise ValueError("thresholds must be an object")
            ws.set_thresholds(body["thresholds"])
    return await run(request, work)


@app.put("/api/tables")
async def put_tables(request: Request):
    body = await body_json(request)
    return await run(request, lambda ws: ws.set_tables(body))


# ---------------------------------------------------------------- files

async def _read_capped(f: UploadFile, limit: int) -> bytes:
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{f.filename}: larger than {limit // MB or 1} MB")
    return data


@app.post("/api/files/{store}")
async def upload(request: Request, store: str, files: list[UploadFile] = File(...), doc_type: str = Form("auto"),
                 person: str = Form("")):
    _store(store)
    blobs = [(f.filename or "file", await _read_capped(f, MAX_FILE_MB * MB if doc_type != "zip" else MAX_UPLOAD_MB * MB))
             for f in files]

    def work(ws):
        if ws.stored_bytes() + sum(len(b) for _, b in blobs) > MAX_CASE_MB * MB:
            raise ValueError(f"This case already holds close to {MAX_CASE_MB} MB of files - remove some first")
        for name, data in blobs:
            if store == "docs" and doc_type == "zip":
                ws.add_zip(data)
            elif store == "kyc_docs":
                is_person = doc_type == "person"
                ws.add_file(store, name, data, kyc.guess_kind(name, is_person), person if is_person else None)
            elif store == "docs":
                ws.add_file(store, name, data, doc_type if doc_type in views.FIN_TYPES else "auto")
            else:
                ws.add_file(store, name, data, "security")
    return await run(request, work)


@app.patch("/api/files/{store}/{doc_id}")
async def update_file(request: Request, store: str, doc_id: str):
    _store(store)
    body = await body_json(request)
    return await run(request, lambda ws: ws.update_file(store, doc_id, body.get("doc_type"), body.get("person")))


@app.delete("/api/files/{store}/{doc_id}")
async def delete_file(request: Request, store: str, doc_id: str):
    _store(store)
    return await run(request, lambda ws: ws.remove_file(store, doc_id))


@app.post("/api/files/{store}/{doc_id}/unlock")
async def unlock(request: Request, store: str, doc_id: str):
    _store(store)
    body = await body_json(request)

    def work(ws):
        if not ws.unlock(store, doc_id, str(body.get("password", ""))[:200]):
            raise ValueError("Wrong password")
    return await run(request, work)


@app.post("/api/read/{store}")
async def read(request: Request, store: str):
    _store(store)
    return await run(request, lambda ws: ws.read(store, SERVER_KEY or ws.api_key))


# ---------------------------------------------------------------- downloads

def disposition(name: str) -> str:
    """Content-Disposition that works for any company name: an ASCII fallback plus the UTF-8 name (RFC 6266 /
    RFC 5987). Header values must be Latin-1, so a raw Hindi or Tamil name would otherwise crash the response."""
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


async def _export(request: Request, make, suffix: str, mime: str) -> Response:
    ws = ws_of(request)

    def job():
        with ws.lock:
            return make(ws), f"{ws.file_name()}{suffix}"
    data, name = await run_in_threadpool(job)
    return Response(data, media_type=mime, headers={"Content-Disposition": disposition(name)})


@app.get("/api/export/report.pdf")
async def report(request: Request):
    return await _export(request, lambda ws: ws.report_pdf(ws.build()), "_credit_report.pdf", "application/pdf")


@app.get("/api/export/cam.pdf")
async def cam(request: Request):
    return await _export(request, lambda ws: ws.cam_pdf(ws.build()), "_CAM_Lite_internal.pdf", "application/pdf")


@app.get("/api/export/credit_note.xlsx")
async def credit_note(request: Request):
    return await _export(request, lambda ws: ws.excel(ws.build()), "_credit_note.xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/export/credit_file.zip")
async def credit_file(request: Request):
    def make(ws):
        s = ws.build()
        try:
            pdf = ws.report_pdf(s)
        except Exception:  # never lose the other files because of one bad document
            log.exception("credit report failed while building the ZIP")
            pdf = None
        return ws.credit_file_zip(ws.excel(s), ws.file_name(), pdf)
    return await _export(request, make, "_credit_file.zip", "application/zip")


@app.get("/api/export/case.json")
async def case_file(request: Request):
    return await _export(request, lambda ws: ws.save_case().encode(), "_case.json", "application/json")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
