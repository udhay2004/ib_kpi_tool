"""Turns uploaded files into structured, source-cited data using Claude.

Mirrors the pack's Data Room Synthesis agent: every file gets a stable ID and
hash in a source ledger, values the model cannot find come back as null, and
nothing is calculated here - KPIs are computed deterministically in kpis.py.
"""

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass, field

import truststore

# Use the OS certificate store so HTTPS works behind antivirus / corporate proxies
# that inspect TLS (e.g. Avast, Zscaler). Harmless on a normal server.
truststore.inject_into_ssl()

import anthropic  # noqa: E402  (must import after inject_into_ssl)
import fitz  # noqa: E402  PyMuPDF
import pandas as pd  # noqa: E402

from schemas import CLASSIFY_SCHEMA, DOC_TYPES, EXTRACT_SCHEMAS  # noqa: E402

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
TABLE_TYPES = {".xlsx", ".xls", ".csv"}
TEXT_TYPES = {".txt", ".json", ".md"}

SYSTEM_PROMPT = """You are a diligence fact extractor for an Indian investment-banking / credit team.
You read one client document and fill the given JSON schema exactly.

Rules:
- Report only what the document shows. If a value is not in the document, return null. Never estimate or fill gaps.
- Convert every amount to absolute Indian Rupees (e.g. 12.5 lakhs -> 1250000, 3.2 crores -> 32000000). Record the unit the document used where the schema asks.
- Use the sign convention of the document; expenses are positive numbers.
- Fiscal years run April-March and are written FY2024-25. Months are YYYY-MM.
- For bank statements, aggregate transactions per calendar month. Count a bounce/return only when the narration says so (e.g. "RTN", "RETURN", "INSUFFICIENT FUNDS", "ECS RETURN", "NACH FAIL").
- Put the 1-indexed page numbers you read each value from in source_pages.
- Use notes for anything ambiguous, illegible, inconsistent, or suspicious (round-tripping, related-party flows, unusual cash, totals that do not add up)."""


@dataclass
class SourceDoc:
    doc_id: str
    filename: str
    data: bytes
    sha256: str = ""
    pages: int | None = None
    encrypted: bool = False
    doc_type: str | None = None
    entity: str | None = None
    period: str | None = None
    status: str = "pending"
    extracted: dict | None = None
    error: str | None = None
    warnings: list = field(default_factory=list)
    as_images: bool = False  # send PDF pages as images (when the API rejects the PDF itself)

    def __post_init__(self):
        self.sha256 = hashlib.sha256(self.data).hexdigest()  # hash of the original, for the source ledger
        if self.ext == ".pdf":
            try:
                with fitz.open(stream=self.data, filetype="pdf") as pdf:
                    self.encrypted = pdf.needs_pass
                    self.pages = None if pdf.needs_pass else pdf.page_count
            except Exception as exc:  # corrupt file
                self.status, self.error = "unreadable", f"Not a readable PDF ({exc})"

    @property
    def ext(self) -> str:
        return "." + self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""

    def unlock(self, password: str) -> bool:
        """Decrypt a password-protected PDF locally (common for bank statements and ITRs)."""
        with fitz.open(stream=self.data, filetype="pdf") as pdf:
            if not pdf.authenticate(password):
                return False
            self.data = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
        self.encrypted = False
        with fitz.open(stream=self.data, filetype="pdf") as pdf:
            self.pages = pdf.page_count
        return True


def repair_pdf(data: bytes) -> bytes:
    """Rewrites a PDF cleanly: strips owner-password restrictions (common on bank / GST portal
    downloads), rebuilds broken cross-reference tables and recompresses. The API rejects
    encrypted or malformed PDFs; this fixes most of them. Returns the input if it can't help."""
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                return data
            fixed = pdf.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
        with fitz.open(stream=fixed, filetype="pdf") as check:
            ok = check.page_count > 0 and not check.is_encrypted
        return fixed if ok else data
    except Exception:
        return data


MAX_IMAGE_PAGES = 100  # API limit on images per request


def pdf_page_images(data: bytes, dpi: int = 130) -> list:
    """Renders each PDF page as a JPEG image block, each preceded by its page number."""
    blocks = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        if pdf.page_count > MAX_IMAGE_PAGES:
            raise ValueError(f"PDF has {pdf.page_count} pages; split it into parts of {MAX_IMAGE_PAGES} pages or fewer")
        for i, page in enumerate(pdf, 1):
            img = page.get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=80)
            blocks.append({"type": "text", "text": f"Page {i}:"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(img).decode()}})
    return blocks


def content_blocks(doc: SourceDoc) -> list:
    """Converts a file into Claude content blocks."""
    if doc.ext == ".pdf" and getattr(doc, "as_images", False):
        return pdf_page_images(doc.data)
    if doc.ext == ".pdf":
        return [{
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(repair_pdf(doc.data)).decode()},
            "title": doc.filename,
        }]
    if doc.ext in IMAGE_TYPES:
        return [{
            "type": "image",
            "source": {"type": "base64", "media_type": IMAGE_TYPES[doc.ext],
                       "data": base64.standard_b64encode(doc.data).decode()},
        }]
    if doc.ext in TABLE_TYPES:
        if doc.ext == ".csv":
            sheets = {"csv": pd.read_csv(io.BytesIO(doc.data), header=None, dtype=str)}
        else:
            sheets = pd.read_excel(io.BytesIO(doc.data), sheet_name=None, header=None, dtype=str)
        text = "\n\n".join(f"### Sheet: {name}\n{df.fillna('').to_csv(index=False, header=False)}"
                           for name, df in sheets.items())
        return [{"type": "text", "text": f"<document name=\"{doc.filename}\">\n{text}\n</document>"}]
    if doc.ext in TEXT_TYPES:
        return [{"type": "text", "text": f"<document name=\"{doc.filename}\">\n{doc.data.decode(errors='replace')}\n</document>"}]
    raise ValueError(f"Unsupported file type: {doc.ext or 'none'}")


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("Model did not return JSON")
    return json.loads(match.group(0))


def _ask(client: anthropic.Anthropic, blocks: list, instruction: str, schema: dict, effort: str) -> dict:
    """One structured-output call. Falls back to schema-in-prompt JSON if the
    schema is too complex for constrained decoding."""
    messages = [{"role": "user", "content": blocks + [{"type": "text", "text": instruction}]}]
    common = dict(
        model=MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        betas=[FALLBACK_BETA],
        extra_body={"fallbacks": "default"},
    )
    try:
        with client.beta.messages.stream(
            messages=messages,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            **common,
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.BadRequestError as exc:
        if "schema" not in str(exc).lower():
            raise
        messages[0]["content"][-1]["text"] += (
            "\n\nReturn ONLY a JSON object matching this JSON schema, no prose:\n" + json.dumps(schema))
        with client.beta.messages.stream(messages=messages, output_config={"effort": effort}, **common) as stream:
            msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this document")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Output was cut off (document too long) - split the file and retry")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return _parse_json(text)


def classify(client: anthropic.Anthropic, doc: SourceDoc) -> None:
    options = "\n".join(f"- {k}: {v}" for k, v in DOC_TYPES.items())
    result = _ask(client, content_blocks(doc),
                  f"Classify this document (file name: {doc.filename}) into one of:\n{options}",
                  CLASSIFY_SCHEMA, effort="low")
    doc.doc_type = result["doc_type"]
    doc.entity = doc.entity or result.get("entity_name")
    doc.period = doc.period or result.get("period_covered")


def extract(client: anthropic.Anthropic, doc: SourceDoc) -> SourceDoc:
    """Classifies (if needed) and extracts one financial document. Never raises; errors land on doc."""
    return _safely(doc, lambda: _extract_once(client, doc))


def extract_with_schema(client: anthropic.Anthropic, doc: SourceDoc, schema: dict, instruction: str) -> SourceDoc:
    """Extracts a KYC / security document with its own schema. Never raises; errors land on doc."""
    def run():
        doc.extracted = _ask(client, content_blocks(doc), f"{instruction} (file name: {doc.filename})",
                             schema, effort="medium")
    return _safely(doc, run)


def _safely(doc: SourceDoc, run) -> SourceDoc:
    if doc.status == "unreadable":
        return doc
    if doc.encrypted:
        doc.status, doc.error = "locked", "PDF is password protected - enter the password and retry"
        return doc
    try:
        try:
            run()
        except anthropic.BadRequestError as exc:
            # Some PDFs (odd generators, scanned-image wrappers) are rejected even after repair_pdf().
            # Reading the rendered pages as images works for those.
            if doc.ext != ".pdf" or getattr(doc, "as_images", False) or "pdf" not in exc.message.lower():
                raise
            doc.as_images = True
            doc.warnings.append("The API could not open this PDF, so its pages were read as images")
            run()
        doc.entity = doc.entity or doc.extracted.get("entity_name") or doc.extracted.get("legal_name") \
            or doc.extracted.get("account_holder") or doc.extracted.get("assessee_name") or doc.extracted.get("name")
        doc.status, doc.error = "extracted", None
    except anthropic.AuthenticationError:
        doc.status, doc.error = "failed", "API key rejected - check the key in Settings"
    except anthropic.PermissionDeniedError as exc:
        doc.status, doc.error = "failed", f"API access denied: {exc.message}"
    except anthropic.RateLimitError:
        doc.status, doc.error = "failed", "Rate limit hit - wait a minute and extract again"
    except anthropic.APIConnectionError:
        doc.status, doc.error = "failed", "Can't reach the Anthropic API - check internet / firewall / proxy"
    except anthropic.APIStatusError as exc:
        msg = exc.message
        if "credit balance" in msg.lower():
            msg = "No API credit left - add credit at console.anthropic.com → Billing"
        doc.status, doc.error = "failed", msg
    except Exception as exc:
        doc.status, doc.error = "failed", f"{type(exc).__name__}: {exc}"
    return doc


def _extract_once(client: anthropic.Anthropic, doc: SourceDoc) -> None:
    if not doc.doc_type or doc.doc_type == "auto":
        classify(client, doc)
    doc.extracted = _extract_as(client, doc)
    detected = doc.extracted.get("detected_type")
    if detected and detected not in (doc.doc_type, "other"):
        # Uploaded in the wrong section (e.g. an ITR acknowledgement under financials): re-read as what it is.
        doc.warnings.append(f"Uploaded as {doc.doc_type}, but it is a {detected} - re-read as {detected}")
        doc.doc_type = detected
        doc.extracted = _extract_as(client, doc)


def _extract_as(client: anthropic.Anthropic, doc: SourceDoc) -> dict:
    return _ask(
        client, content_blocks(doc),
        f"The user filed this under: {DOC_TYPES[doc.doc_type]} (file name: {doc.filename}). "
        "First set detected_type to what the document really is. Then extract every field in the schema, "
        "covering every fiscal year / month / period present.",
        EXTRACT_SCHEMAS[doc.doc_type], effort="high")
