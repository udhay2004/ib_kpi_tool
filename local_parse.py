"""Read financial documents without any API: bank statements (CSV / Excel / digital PDF), GSTR-3B and
GST summaries, Schedule III financial statements and ITR-V acknowledgements.

Output has exactly the shape of the Claude extraction (schemas.py), so review, KPIs and flags work unchanged.
Scanned (image-only) PDFs have no text to read; those need the optional Claude reader or an Excel copy.
"""

import csv
import io
import re
import warnings
from datetime import date

import fitz
import pandas as pd

from schemas import EXTRACT_SCHEMAS, FIN_YEAR

# ------------------------------------------------------------------ reference data

IFSC_BANKS = {  # IFSC prefix -> bank (RBI IFSC directory)
    "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "State Bank of India", "UTIB": "Axis Bank",
    "KKBK": "Kotak Mahindra Bank", "YESB": "Yes Bank", "INDB": "IndusInd Bank", "IDFB": "IDFC First Bank",
    "BARB": "Bank of Baroda", "PUNB": "Punjab National Bank", "CNRB": "Canara Bank", "UBIN": "Union Bank of India",
    "BKID": "Bank of India", "FDRL": "Federal Bank", "RATN": "RBL Bank", "AUBL": "AU Small Finance Bank",
    "IDIB": "Indian Bank", "CBIN": "Central Bank of India", "IBKL": "IDBI Bank", "BDBL": "Bandhan Bank",
    "SIBL": "South Indian Bank", "KVBL": "Karur Vysya Bank", "CIUB": "City Union Bank", "DCBL": "DCB Bank",
    "SCBL": "Standard Chartered", "HSBC": "HSBC", "CITI": "Citibank", "IOBA": "Indian Overseas Bank",
    "UCBA": "UCO Bank", "MAHB": "Bank of Maharashtra", "PSIB": "Punjab & Sind Bank", "KARB": "Karnataka Bank",
    "TMBL": "Tamilnad Mercantile Bank", "JAKA": "J&K Bank", "ESFB": "Equitas Small Finance Bank",
    "UJVN": "Ujjivan Small Finance Bank", "DBSS": "DBS Bank", "DLXB": "Dhanlaxmi Bank", "CSBK": "CSB Bank",
}
BANK_NAMES = {re.sub(r"\s+", " ", v.lower()): v for v in IFSC_BANKS.values()} | {
    "sbi": "State Bank of India", "pnb": "Punjab National Bank", "bob": "Bank of Baroda", "kotak": "Kotak Mahindra Bank",
    "axis": "Axis Bank", "icici": "ICICI Bank", "hdfc": "HDFC Bank", "idfc": "IDFC First Bank", "indusind": "IndusInd Bank"}

UNITS = [(r"crores?|\bcr\b", 1e7), (r"lakhs?|lacs?", 1e5), (r"millions?|\bmn\b", 1e6), (r"thousands?|'000|000s", 1e3)]

# ------------------------------------------------------------------ text helpers

NUM_RE = re.compile(r"\(?-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\)?(?:\s*(?:cr|dr)\b)?", re.I)
MONEY_RE = re.compile(r"^\(?-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?\)?$")
DATE_RE = re.compile(r"\b(\d{1,2}[/\-. ](?:\d{1,2}|[A-Za-z]{3,9})[/\-. ,]{1,2}\d{2,4}|\d{4}-\d{2}-\d{2})\b")


def to_num(s) -> float | None:
    """'1,23,456.00' -> 123456.0; '(1,234)' -> -1234; '12.00 Dr' -> -12; blanks / dashes -> None."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("₹", "").replace("Rs.", "").replace("INR", "").strip()
    if not t or t in {"-", "--", "—", "–", "nil", "Nil", "NIL"}:
        return None
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    if re.search(r"\bdr\.?$", t, re.I):
        neg = True
    t = re.sub(r"\s*(cr|dr)\.?$", "", t, flags=re.I)
    t = t.strip("()").lstrip("-").replace(",", "").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def to_date(s) -> date | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if hasattr(s, "year") and hasattr(s, "month"):
        return date(s.year, s.month, s.day)
    m = DATE_RE.search(str(s))
    if not m:
        return None
    t = m.group(1)
    try:
        d = pd.to_datetime(t, dayfirst=not re.match(r"\d{4}-", t))
    except (ValueError, OverflowError):
        return None
    return None if pd.isna(d) or not 2000 <= d.year <= 2100 else d.date()


def pdf_lines(data: bytes) -> list[tuple[int, str, list]]:
    """(page, line text, words) per visual line, rebuilt from word positions so table rows stay on one line."""
    out = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for pno, page in enumerate(pdf, 1):
            words = page.get_text("words")  # x0, y0, x1, y1, word, block, line, word_no
            rows: list[list] = []
            for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
                mid = (w[1] + w[3]) / 2
                if rows and abs(rows[-1][0] - mid) <= 2.5:
                    rows[-1][1].append(w)
                else:
                    rows.append([mid, [w]])
            for _, ws in rows:
                ws.sort(key=lambda w: w[0])
                out.append((pno, " ".join(w[4] for w in ws), ws))
    return out


def pdf_text(data: bytes, max_pages: int | None = None) -> str:
    with fitz.open(stream=data, filetype="pdf") as pdf:
        return "\n".join(p.get_text() for i, p in enumerate(pdf) if max_pages is None or i < max_pages)


def read_sheets(data: bytes, ext: str) -> list[pd.DataFrame]:
    """Raw cell grids (no header inference) for CSV / Excel."""
    if ext == ".csv":
        text = data.decode("utf-8-sig", "replace")
        try:
            delim = csv.Sniffer().sniff(text[:5000], delimiters=",;	|").delimiter
        except csv.Error:
            delim = ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        width = max((len(r) for r in rows), default=0)
        return [pd.DataFrame([r + [None] * (width - len(r)) for r in rows])] if rows else []
    return list(pd.read_excel(io.BytesIO(data), header=None, sheet_name=None, dtype=object).values())


def doc_text(data: bytes, ext: str, max_pages: int | None = None) -> str:
    if ext == ".pdf":
        return pdf_text(data, max_pages)
    if ext in (".csv", ".xlsx", ".xls"):
        return "\n".join(" ".join(str(c) for c in row if pd.notna(c)) for df in read_sheets(data, ext)
                         for row in df.head(400).itertuples(index=False))
    if ext in (".txt", ".json"):
        return data.decode("utf-8", "ignore")
    return ""


def unit_multiplier(text: str) -> tuple[float, str]:
    head = text[:6000].lower()
    m = re.search(r"(?:\bin|₹|\brs\.?|\binr)\s*[\(\[]?\s*(crores?|lakhs?|lacs?|millions?|thousands?|'000)\b", head)
    if m:
        for pat, mult in UNITS:
            if re.search(pat, m.group(1)):
                return mult, m.group(1)
    return 1.0, "rupees"


# ------------------------------------------------------------------ document type

def detect_type(text: str, filename: str = "") -> str:
    t = (text[:20000] + " " + filename).lower()
    score = {
        "gst": len(re.findall(r"gstr-?\s?(?:3b|1|9)|outward taxable supplies|eligible itc|\bgstin\b", t)),
        "itr": len(re.findall(r"itr-v|indian income tax return|assessment year|gross total income|acknowledgement number", t)),
        "financials": len(re.findall(r"balance sheet|statement of profit|profit and loss|revenue from operations|"
                                     r"share capital|reserves and surplus|cash flow statement", t)),
        "bank_statement": len(re.findall(r"statement of account|account statement|closing balance|opening balance|"
                                         r"withdrawal|deposit|narration|\bifsc\b|cheque|chq", t)),
    }
    best = max(score, key=score.get)
    return best if score[best] >= 2 else "other"


def blank(doc_type: str) -> dict:
    props = EXTRACT_SCHEMAS[doc_type]["properties"]
    return {k: ([] if v.get("type") == "array" else None) for k, v in props.items()}


# ------------------------------------------------------------------ bank statements

HEAD = {
    "date": r"^(txn|tran|transaction|post(ing)?|value)?\s*\.?\s*date|^date",
    "narration": r"narration|description|particulars|remarks|details|transaction\s*details",
    "debit": r"withdrawal|debit|^dr\.?$|paid\s*out|\bdr\b\s*amount",
    "credit": r"deposit|credit|^cr\.?$|paid\s*in|\bcr\b\s*amount",
    "amount": r"^(txn\s*|transaction\s*)?amount|amount\s*\(",
    "type": r"^(dr\s*/\s*cr|cr\s*/\s*dr|type|txn\s*type|d/c)$",
    "balance": r"balance",
}
CHARGE_GST = re.compile(r"\b(gst|igst|cgst|sgst)\b", re.I)
BOUNCE = re.compile(r"insuff|inward\s*ret|i/w\s*ret|iw\s*rtn|(?:ach|nach|ecs)\b.{0,12}(?:rtn|ret|return|fail|bounce)|"
                    r"emi\s*bounce|exceeds\s*arrangement|dishono|funds\s*not|return.{0,20}insuff", re.I)
DEPOSIT_RETURN = re.compile(r"o/w\s*ret|outward\s*ret|ow\s*rtn|clg\s*ret|chq\s*ret|cheque\s*ret|returned|\brtn\b", re.I)
CASH_IN = re.compile(r"\bcash\b|by\s*cash|cash\s*dep|\bcdm\b|csh\s*dep", re.I)
CASH_OUT = re.compile(r"\batm\b|\batw\b|\bnwd\b|cash\s*w|\bcash\b|\bself\b", re.I)
NOT_CASH = re.compile(r"cashback|\bcms\b|cash\s*(mgmt|management)|cashfree", re.I)
EMI = re.compile(r"\bemi\b|\bloan\b|\bnach\b|\bach\b|\becs\b|repay", re.I)
DISBURSAL = re.compile(r"disb|loan\s*(a/?c|account)|disbursement", re.I)
OWN = re.compile(r"\bself\b|own\s*a/?c|own\s*account|sweep|\bfd\b|fixed\s*deposit", re.I)
CP_STOP = set("NEFT RTGS IMPS UPI CR DR INB MB TRF TRANSFER FROM TO BY INFT CMS CLG CHQ CHEQUE ACH NACH ECS DEP "
              "P2A P2M IB FT INW OUTW BIL BILLPAY PAYMENT PAY REF NO SENT RECEIVED RECD TXN THE AND A/C AC ACCOUNT "
              "BANK LTD-".split())


def counterparty(narr: str) -> str | None:
    s = re.sub(r"\s+", " ", str(narr).upper())
    for tok in re.split(r"[/\-:|,*@#]+| {2,}", s):
        words = [w for w in tok.split() if w not in CP_STOP]
        cand = " ".join(words).strip(" .")
        if len(cand) < 3 or re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", cand):  # IFSC
            continue
        digits = sum(ch.isdigit() for ch in cand)
        if digits > len(cand) * 0.3 or not re.search(r"[A-Z]{3}", cand):
            continue
        return cand[:40].title()
    return None


def _map_header(cells: list) -> dict | None:
    names = [re.sub(r"\s+", " ", str(c or "")).strip().lower() for c in cells]
    cols = {}
    for key, pat in HEAD.items():
        for i, n in enumerate(names):
            if n and i not in cols.values() and re.search(pat, n):
                if key == "date" and "value" in n and any(re.search(r"txn|tran|post", x) for x in names):
                    continue  # prefer transaction date over value date
                cols[key] = i
                break
    if "date" in cols and "balance" in cols and ({"debit", "credit"} & cols.keys() or "amount" in cols):
        return cols
    return None


def _rows_to_txns(rows: list[list]) -> list[dict]:
    """Find a header row, then turn every following row into a transaction."""
    cols, txns = None, []
    for row in rows:
        if cols is None or (len(row) != width and _map_header(row)):
            m = _map_header(row)
            if m:
                cols, width = m, len(row)
            continue
        if len(row) <= max(cols.values()):
            continue
        cell = lambda k: row[cols[k]] if k in cols else None  # noqa: E731
        d = to_date(cell("date"))
        narr = re.sub(r"\s+", " ", str(cell("narration") or "")).strip()
        if d is None:
            if txns and narr and not re.search(r"total|opening|closing", narr, re.I):
                txns[-1]["narration"] += " " + narr  # wrapped narration
            continue
        bal = to_num(cell("balance"))
        if bal is None:
            continue
        if "debit" in cols or "credit" in cols:
            dr, cr = abs(to_num(cell("debit")) or 0), abs(to_num(cell("credit")) or 0)
        else:
            amt = to_num(cell("amount")) or 0
            typ = str(cell("type") or "").lower()
            is_dr = "dr" in typ or "d" == typ.strip() or (not typ and amt < 0)
            dr, cr = (abs(amt), 0.0) if is_dr else (0.0, abs(amt))
        txns.append({"date": d, "narration": narr, "debit": dr, "credit": cr, "balance": bal})
    return txns


def _pdf_table_rows(data: bytes) -> list[list]:
    rows = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for page in pdf:
            try:
                for tab in page.find_tables().tables:
                    rows.extend(tab.extract())
            except Exception:
                continue
    return rows


def _pdf_line_txns(data: bytes) -> list[dict]:
    """Fallback for statements without ruled tables: a line starting with a date and ending with amount + balance.
    Debit or credit is decided from the change in balance."""
    txns, prev_bal = [], None
    for _, text, _ in pdf_lines(data):
        if re.search(r"opening\s*balance", text, re.I):
            nums = [to_num(x) for x in NUM_RE.findall(text)]
            nums = [x for x in nums if x is not None]
            if nums:
                prev_bal = nums[-1]
            continue
        m = DATE_RE.match(text.strip())
        tail = re.findall(r"\(?-?(?:\d{1,3}(?:,\d{2,3})+|\d+)\.\d{2}\)?(?:\s*(?:cr|dr)\b)?", text, re.I)
        if not m or len(tail) < 2:
            if txns and not m and not re.search(r"page|total|balance", text, re.I):
                txns[-1]["narration"] += " " + text.strip()
            continue
        d = to_date(m.group(1))
        if d is None:
            continue
        bal, amt = to_num(tail[-1]), abs(to_num(tail[-2]) or 0)
        narr = text[m.end():text.find(tail[-2])].strip() if tail[-2] in text else text[m.end():]
        narr = re.sub(DATE_RE, "", narr).strip()
        if prev_bal is not None:
            is_cr = bal >= prev_bal
        else:
            is_cr = bool(re.search(r"\bby\b|\bcr\b|credit|deposit", narr, re.I))
        txns.append({"date": d, "narration": narr, "debit": 0.0 if is_cr else amt, "credit": amt if is_cr else 0.0,
                     "balance": bal})
        prev_bal = bal
    return txns


def bank_transactions(data: bytes, ext: str) -> list[dict]:
    if ext == ".pdf":
        txns = _rows_to_txns(_pdf_table_rows(data))
        if len(txns) < 5:
            alt = _pdf_line_txns(data)
            txns = alt if len(alt) > len(txns) else txns
    else:
        txns = []
        for df in read_sheets(data, ext):
            txns += _rows_to_txns(df.where(pd.notna(df), None).values.tolist())
    if len(txns) > 1 and txns[0]["date"] > txns[-1]["date"]:  # newest-first statements
        txns.reverse()
    return txns


def _related_pattern(names: list[str]):
    toks = {w for n in names or [] for w in re.findall(r"[A-Za-z]{4,}", n)
            if w.lower() not in {"private", "limited", "pvt", "ltd", "llp", "company", "industries", "enterprises",
                                  "india", "director", "partner"}}
    return re.compile(r"\b(" + "|".join(map(re.escape, toks)) + r")\b", re.I) if toks else None


def bank_months(txns: list[dict], related_names: list[str] | None = None) -> list[dict]:
    warnings.filterwarnings("ignore", "This pattern is interpreted as a regular expression", UserWarning)
    rel = _related_pattern(related_names or [])
    df = pd.DataFrame(txns)
    df["month"] = df["date"].map(lambda d: f"{d.year}-{d.month:02d}")
    out = []
    for month, g in df.groupby("month", sort=True):
        first = g.iloc[0]
        opening = first["balance"] - first["credit"] + first["debit"]
        eod = g.groupby("date")["balance"].last()  # end-of-day balances
        y, mth = map(int, month.split("-"))
        samples = []
        for day in (5, 10, 15, 20, 25):
            upto = eod[eod.index <= date(y, mth, day)]
            samples.append(float(upto.iloc[-1]) if not upto.empty else float(opening))
        narr = g["narration"].fillna("")
        cr, dr = g["credit"] > 0, g["debit"] > 0
        not_gst = ~narr.str.contains(CHARGE_GST)
        bounce = narr.str.contains(BOUNCE) & not_gst
        dep_ret = narr.str.contains(DEPOSIT_RETURN) & ~bounce & not_gst & dr
        own = narr.str.contains(OWN) | (narr.str.contains(rel) if rel else False)
        out.append({
            "month": month, "opening_balance": float(opening), "closing_balance": float(g.iloc[-1]["balance"]),
            "total_credits": float(g["credit"].sum()), "total_debits": float(g["debit"].sum()),
            "credit_count": int(cr.sum()), "debit_count": int(dr.sum()),
            "min_balance": float(min(eod.min(), opening)), "max_balance": float(max(eod.max(), opening)),
            "cash_deposits": float(g.loc[cr & narr.str.contains(CASH_IN) & ~narr.str.contains(NOT_CASH), "credit"].sum()),
            "cash_withdrawals": float(g.loc[dr & narr.str.contains(CASH_OUT) & ~narr.str.contains(NOT_CASH), "debit"].sum()),
            "inward_cheque_returns": int(dep_ret.sum()),
            "outward_cheque_or_emi_bounces": int(bounce.sum()),
            "emi_or_loan_repayments": float(g.loc[dr & narr.str.contains(EMI) & ~bounce, "debit"].sum()),
            "balances_on_5_10_15_20_25": samples,
            "transfers_in_from_own_or_related_accounts": float(g.loc[cr & own, "credit"].sum()),
            "transfers_out_to_own_or_related_accounts": float(g.loc[dr & own, "debit"].sum()),
            "loan_disbursal_credits": float(g.loc[cr & narr.str.contains(DISBURSAL), "credit"].sum()),
            "source_pages": [],
        })
    return out


def _top_counterparties(txns: list[dict], side: str, n: int = 10) -> list[dict]:
    rows = [(counterparty(t["narration"]), t[side]) for t in txns if t[side] > 0]
    df = pd.DataFrame([r for r in rows if r[0]], columns=["name", "amount"])
    if df.empty:
        return []
    g = df.groupby("name")["amount"].agg(["sum", "count"]).sort_values("sum", ascending=False).head(n)
    return [{"name": k, "amount": float(r["sum"]), "count": int(r["count"])} for k, r in g.iterrows()]


def bank_meta(text: str, filename: str) -> dict:
    t = text[:8000]
    low = t.lower()
    bank = None
    ifsc = re.search(r"\b([A-Z]{4})0[A-Z0-9]{6}\b", t)
    if ifsc and ifsc.group(1) in IFSC_BANKS:
        bank = IFSC_BANKS[ifsc.group(1)]
    if not bank:
        hay = (low + " " + filename.lower()).replace("_", " ")
        bank = next((v for k, v in sorted(BANK_NAMES.items(), key=lambda kv: -len(kv[0]))
                     if re.search(rf"\b{re.escape(k)}\b", hay)), None)
    acct = re.search(r"(?:a/?c|account)\s*(?:no|number|#)?\.?\s*[:\-]?\s*([X\*x\d][\dXx\* ]{5,}\d)", t, re.I)
    holder = re.search(r"(?:account\s*name|a/c\s*name|customer\s*name|name)\s*[:\-]\s*([A-Za-z][A-Za-z .&()'-]{2,60})", t)
    limit = re.search(r"(?:sanctioned\s*limit|od\s*limit|cc\s*limit|drawing\s*power|limit)\s*(?:\(.*?\))?\s*[:\-]?\s*"
                      r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)", t, re.I)
    lim = to_num(limit.group(1)) if limit else None
    typ = ("Cash credit" if re.search(r"cash\s*credit|\bcc\s*a/?c", low) else "Overdraft" if re.search(r"overdraft|\bod\s*a/?c", low)
           else "Current" if "current" in low else "Savings" if "saving" in low else None)
    return {"bank_name": bank, "account_holder": holder.group(1).strip().title() if holder else None,
            "account_type": typ, "account_number_last4": re.sub(r"\D", "", acct.group(1))[-4:] if acct else None,
            "sanctioned_od_cc_limit": lim if lim and lim >= 10000 else None}


def parse_bank(data: bytes, ext: str, filename: str, related_names=None) -> dict | None:
    txns = bank_transactions(data, ext)
    if len(txns) < 3:
        return None
    x = blank("bank_statement") | bank_meta(doc_text(data, ext, 2), filename)
    x["detected_type"] = "bank_statement"
    x["statement_from"], x["statement_to"] = txns[0]["date"].isoformat(), txns[-1]["date"].isoformat()
    x["months"] = bank_months(txns, related_names)
    x["top_credit_counterparties"] = _top_counterparties(txns, "credit")
    x["top_debit_counterparties"] = _top_counterparties(txns, "debit")
    big = sorted(txns, key=lambda t: -(t["credit"] + t["debit"]))[:5]
    x["notable_transactions"] = [{"date": t["date"].isoformat(), "description": t["narration"][:120],
                                  "amount": t["credit"] or -t["debit"], "why_notable": "One of the 5 largest", "page": None}
                                 for t in big]
    x["notes"] = [f"Read without AI: {len(txns)} transactions, {len(x['months'])} months."]
    # Balance check: running balance should follow credits and debits.
    breaks = sum(1 for a, b in zip(txns, txns[1:]) if abs(a["balance"] + b["credit"] - b["debit"] - b["balance"]) > 1)
    if breaks:
        x["notes"].append(f"{breaks} place(s) where the balance does not follow the transactions — check the totals.")
    return x


# ------------------------------------------------------------------ GST

GST_ROWS = {  # GSTR-3B table label -> (field, number of values after the label)
    r"\(a\)\s*outward taxable supplies\s*\(other than zero": "taxable",
    r"\(b\)\s*outward taxable supplies\s*\(zero rated": "export",
    r"\(c\)\s*other outward supplies\s*\(nil": "exempt",
}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def _after(text: str, pat: str, n: int) -> list:
    m = re.search(pat, text, re.I | re.S)
    if not m:
        return []
    nums = [to_num(x) for x in re.findall(r"-?\d[\d,]*\.\d{2}", text[m.end():m.end() + 600])]
    return [x for x in nums if x is not None][:n]


def parse_gstr3b(text: str) -> dict | None:
    if not re.search(r"gstr-?\s?3b", text, re.I):
        return None
    yr = re.search(r"(?:year|financial year)\s*[:\-]?\s*(20\d\d)\s*-\s*(\d{2,4})", text, re.I)
    mo = re.search(r"(?:period|month|tax period)\s*[:\-]?\s*([A-Za-z]+)", text, re.I)
    if not yr or not mo or mo.group(1).lower() not in MONTHS:
        return None
    m = MONTHS[mo.group(1).lower()]
    y = int(yr.group(1)) + (1 if m <= 3 else 0)
    p = {k: None for k in ("taxable_turnover", "exempt_nil_turnover", "export_turnover", "igst", "cgst", "sgst",
                           "cess", "tax_paid_cash", "itc_claimed", "itc_available_gstr2b", "filing_date", "due_date")}
    a = _after(text, r"\(a\)\s*outward taxable supplies\s*\(other than zero[^)]*\)", 5)
    if a:
        p["taxable_turnover"], p["igst"], p["cgst"], p["sgst"], p["cess"] = (a + [None] * 5)[:5]
    b = _after(text, r"\(b\)\s*outward taxable supplies\s*\(zero rated\)", 1)
    c = _after(text, r"\(c\)\s*other outward supplies\s*\(nil rated,?\s*exempted\)", 1)
    p["export_turnover"], p["exempt_nil_turnover"] = (b or [None])[0], (c or [None])[0]
    itc = _after(text, r"\(c\)\s*net itc available\s*\(a\)\s*-?\s*\(b\)", 4) or _after(text, r"net itc available", 4)
    p["itc_claimed"] = sum(x for x in itc if x) if itc else None
    cash = _after(text, r"paid in cash", 4)
    p["tax_paid_cash"] = sum(x for x in cash if x) if cash else None
    fd = re.search(r"(?:date of (?:arn|filing)|filed on|arn date)\s*[:\-]?\s*(\S+)", text, re.I)
    p["filing_date"] = to_date(fd.group(1)).isoformat() if fd and to_date(fd.group(1)) else None
    nm = (m % 12) + 1
    p["due_date"] = date(y + (1 if m == 12 else 0), nm, 20).isoformat()
    if p["taxable_turnover"] is None:
        return None
    gstin = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b", text)
    name = re.search(r"legal name[^:\n]*[:\-]?\s*\n?\s*([A-Z][A-Za-z .&()'-]{2,80})", text, re.I)
    return {"gstin": gstin.group(0) if gstin else None, "legal_name": name.group(1).strip() if name else None,
            "period": {"period": f"{y}-{m:02d}", "return_type": "GSTR-3B", **p, "source_pages": [1]}}


def parse_gst_table(sheets: list[pd.DataFrame]) -> list[dict]:
    """A month-wise GST summary in Excel / CSV: Month | Taxable value | IGST | CGST | SGST | ITC ..."""
    out = []
    for df in sheets:
        rows = df.where(pd.notna(df), None).values.tolist()
        for i, row in enumerate(rows[:30]):
            names = [str(c or "").lower() for c in row]
            pi = next((j for j, n in enumerate(names) if re.search(r"month|period", n)), None)
            ti = next((j for j, n in enumerate(names) if "taxable" in n), None)
            if pi is None or ti is None:
                continue
            col = lambda pat: next((j for j, n in enumerate(names) if re.search(pat, n)), None)  # noqa: E731
            cols = {"igst": col(r"\bigst"), "cgst": col(r"\bcgst"), "sgst": col(r"\bsgst|utgst"),
                    "itc_claimed": col(r"itc.*(claim|availed|3b)|^itc$"), "itc_available_gstr2b": col(r"2b"),
                    "tax_paid_cash": col(r"cash"), "filing_date": col(r"fil(ed|ing)\s*date|date of filing"),
                    "return_type": col(r"return|form")}
            for r in rows[i + 1:]:
                d = to_date(r[pi]) if pi < len(r) else None
                if d is None and r[pi]:
                    mm = re.match(r"([A-Za-z]{3,9})[\s\-']*(\d{2,4})", str(r[pi]))
                    if mm and mm.group(1)[:3].lower() in [k[:3] for k in MONTHS]:
                        mon = [k[:3] for k in MONTHS].index(mm.group(1)[:3].lower()) + 1
                        yy = int(mm.group(2)) + (2000 if len(mm.group(2)) == 2 else 0)
                        d = date(yy, mon, 1)
                tv = to_num(r[ti]) if ti < len(r) else None
                if d is None or tv is None:
                    continue
                p = {"period": f"{d.year}-{d.month:02d}", "return_type": str(r[cols["return_type"]]) if
                     cols["return_type"] is not None and r[cols["return_type"]] else "GSTR-3B",
                     "taxable_turnover": tv, "exempt_nil_turnover": None, "export_turnover": None, "cess": None,
                     "due_date": None, "source_pages": []}
                for k in ("igst", "cgst", "sgst", "itc_claimed", "itc_available_gstr2b", "tax_paid_cash"):
                    p[k] = to_num(r[cols[k]]) if cols[k] is not None else None
                fdx = to_date(r[cols["filing_date"]]) if cols["filing_date"] is not None else None
                p["filing_date"] = fdx.isoformat() if fdx else None
                out.append(p)
            break
    return out


def parse_gst(data: bytes, ext: str) -> dict | None:
    x = blank("gst")
    x["detected_type"] = "gst"
    if ext == ".pdf":
        with fitz.open(stream=data, filetype="pdf") as pdf:
            texts = [p.get_text() for p in pdf]
        # One PDF may hold several monthly returns: split at each "GSTR-3B" form title.
        whole = "\n".join(texts)
        parts = re.split(r"(?=Form\s*GSTR-?\s?3B)", whole, flags=re.I)
        for part in parts:
            r = parse_gstr3b(part)
            if r:
                x["gstin"] = x["gstin"] or r["gstin"]
                x["legal_name"] = x["legal_name"] or r["legal_name"]
                x["periods"].append(r["period"])
    elif ext in (".csv", ".xlsx", ".xls"):
        x["periods"] = parse_gst_table(read_sheets(data, ext))
        text = doc_text(data, ext)
        g = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b", text)
        x["gstin"] = g.group(0) if g else None
    if not x["periods"]:
        return None
    x["notes"] = [f"Read without AI: {len(x['periods'])} return period(s)."]
    return x


# ------------------------------------------------------------------ financial statements (Schedule III)

FIN_LABELS = [  # field, label pattern (matched at start of the line label), sections it may appear in
    ("revenue_from_operations", r"revenue from operations|(net )?sales( and services)?$|income from operations|turnover"),
    ("other_income", r"other income"),
    ("cost_of_materials_and_purchases", r"cost of (raw )?materials? consumed|purchases? of stock|purchases? of traded|cost of goods sold"),
    ("change_in_inventory", r"changes? in inventor"),
    ("employee_cost", r"employee benefits? expenses?|employee cost|salaries"),
    ("finance_cost", r"finance costs?|interest (and finance charges|expenses?)|borrowing costs"),
    ("depreciation", r"depreciation"),
    ("other_expenses", r"other expenses"),
    ("profit_before_tax", r"profit\s*/?\s*\(?(loss)?\)?\s*before (exceptional items and )?tax"),
    ("tax_expense", r"total tax expenses?|tax expenses?|income tax expenses?"),
    ("profit_after_tax", r"profit\s*/?\s*\(?(loss)?\)?\s*(after tax|for the (year|period))"),
    ("share_capital", r"(equity )?share capital"),
    ("reserves_and_surplus", r"reserves and surplus|other equity"),
    ("net_worth", r"total equity$|total equity attributable|shareholders'? funds|net worth"),
    ("long_term_borrowings", r"long[- ]term borrowings"),
    ("short_term_borrowings", r"short[- ]term borrowings"),
    ("borrowings", r"borrowings$|\(i\)\s*borrowings"),
    ("current_maturities_of_long_term_debt", r"current maturities of long[- ]term"),
    ("trade_payables", r"trade payables"),
    ("total_current_liabilities", r"total current liabilities"),
    ("fixed_assets_net", r"property,? plant (and|&) equipment|tangible assets|fixed assets"),
    ("intangible_assets", r"(other )?intangible assets$|intangible assets\b"),
    ("inventory", r"inventor(y|ies)"),
    ("trade_receivables", r"trade receivables|sundry debtors"),
    ("cash_and_bank", r"cash and cash equivalents|cash and bank"),
    ("total_current_assets", r"total current assets"),
    ("total_assets", r"total assets|total equity and liabilities"),
    ("cash_from_operations", r"net cash (flow )?(generated )?(from|used in|from / \(used in\)) operating"),
    ("capex", r"(purchase|acquisition) of (property|fixed assets|ppe|tangible|plant)"),
    ("unsecured_loans_from_promoters", r"(unsecured )?loans? from (directors|promoters|related parties)"),
]
SECTION = [(r"^non[- ]current liabilities", "ncl"), (r"^current liabilities", "cl"),
           (r"^non[- ]current assets", "nca"), (r"^current assets", "ca")]


def fy_columns(text: str) -> list[str]:
    """Fiscal years in the statement, newest first, from 'as at 31 March 2025' style headings."""
    years = re.findall(r"31(?:st)?[\s.\-/]*(?:march|mar|03)[\s.\-/,]*(20\d\d)|march\s*31,?\s*(20\d\d)", text[:20000], re.I)
    ys = sorted({int(a or b) for a, b in years}, reverse=True)
    if not ys:
        ys = sorted({int(a) + 1 for a in re.findall(r"\bFY\s*(20\d\d)\s*-\s*\d{2}\b", text[:20000], re.I)}, reverse=True)
    return [f"FY{y - 1}-{str(y)[2:]}" for y in ys[:2]]


def _line_values(text: str) -> tuple[str, list[float]]:
    """Split a statement line into its label and trailing amounts (current year first)."""
    toks = text.split()
    nums = []
    while toks and (MONEY_RE.match(toks[-1]) or toks[-1] in {"-", "—", "–"}):
        nums.insert(0, to_num(toks.pop()))
    label = " ".join(toks)
    # A small integer right after the label is a note number, not an amount.
    if len(nums) >= 3 and nums[0] is not None and float(nums[0]).is_integer() and abs(nums[0]) < 100:
        nums = nums[1:]
    label = re.sub(r"\s+\d{1,2}(\.\d{1,2})?$", "", label)  # trailing note number
    return label, nums


def parse_financials(data: bytes, ext: str) -> dict | None:
    if ext == ".pdf":
        lines = [t for _, t, _ in pdf_lines(data)]
        text = pdf_text(data)
    elif ext in (".csv", ".xlsx", ".xls"):
        lines = [" ".join(str(c) for c in row if c is not None and str(c) != "nan")
                 for df in read_sheets(data, ext) for row in df.where(pd.notna(df), None).values.tolist()]
        text = "\n".join(lines)
    else:
        return None
    fys = fy_columns(text)
    if not fys:
        return None
    mult, unit = unit_multiplier(text)
    found: dict[str, list] = {}
    section = None
    for line in lines:
        label, nums = _line_values(line.strip())
        low = re.sub(r"^[\(\[]?[a-z0-9ivx]{1,4}[\)\].]\s*", "", label.lower()).strip(" :")
        for pat, sec in SECTION:
            if re.match(pat, low):
                section = sec
        vals = [v for v in nums if v is not None]
        if not vals or not low:
            continue
        for field, pat in FIN_LABELS:
            if not re.match(pat, low):
                continue
            if field == "borrowings":
                field = "long_term_borrowings" if section == "ncl" else "short_term_borrowings" if section == "cl" else None
            if field == "cost_of_materials_and_purchases" and field in found:
                prev = found[field]  # materials + purchases of traded goods
                found[field] = [(a or 0) + (b or 0) for a, b in zip(prev, nums + [None] * (len(prev) - len(nums)))]
                break
            if field and field not in found:
                found[field] = nums[-2:] if len(nums) >= 2 else nums
            break
    if len(found) < 6:  # not a financial statement we can read reliably
        return None
    years = []
    for i, fy in enumerate(fys):
        y = {k: None for k in FIN_YEAR["properties"]}
        y["fiscal_year"] = fy
        y["audited"] = bool(re.search(r"independent auditor", text, re.I)) or None
        for field, vals in found.items():
            if i < len(vals) and vals[i] is not None:
                v = vals[i] * mult
                y[field] = abs(v) if field in ("capex", "depreciation", "finance_cost") else v
        if y["net_worth"] is None and y["share_capital"] is not None and y["reserves_and_surplus"] is not None:
            y["net_worth"] = y["share_capital"] + y["reserves_and_surplus"]
        y["source_pages"] = []
        if any(y[k] is not None for k in ("revenue_from_operations", "profit_after_tax", "net_worth")):
            years.append(y)
    if not years:
        return None
    x = blank("financials")
    name = re.search(r"^\s*([A-Z][A-Z0-9 .&()'-]{3,80}(?:PRIVATE LIMITED|PVT\.? LTD\.?|LIMITED|LLP))", text, re.M)
    x.update({"detected_type": "financials", "entity_name": name.group(1).strip().title() if name else None,
              "unit_stated_in_document": unit, "years": years})
    q = re.search(r"basis for (qualified|adverse) opinion", text, re.I)
    if q:
        x["auditor_qualifications"] = [f"Auditor's report contains a {q.group(1).lower()} opinion — read the basis paragraph."]
    cl = re.search(r"contingent liabilit[^\n]{0,80}?(\d[\d,]*\.?\d*)", text, re.I)
    x["contingent_liabilities"] = to_num(cl.group(1)) * mult if cl and to_num(cl.group(1)) else None
    x["notes"] = [f"Read without AI from the Schedule III statements ({len(found)} line items found, figures in {unit})."]
    return x


# ------------------------------------------------------------------ ITR-V

def parse_itr(text: str) -> dict | None:
    if not re.search(r"assessment year", text, re.I):
        return None
    x = blank("itr")
    x["detected_type"] = "itr"
    ay = re.search(r"assessment year\s*[:\-]?\s*(20\d\d)\s*-\s*(\d{2,4})", text, re.I)
    x["assessment_year"] = f"AY{ay.group(1)}-{ay.group(2)[-2:]}" if ay else None
    pan = re.search(r"\b[A-Z]{3}([PCHFATBLJG])[A-Z]\d{4}[A-Z]\b", text)
    x["pan_last4"] = pan.group(0)[-4:] if pan else None
    x["assessee_type"] = {"P": "Individual", "C": "Company", "H": "HUF", "F": "Firm"}.get(pan.group(1)) if pan else None
    form = re.search(r"\bITR[- ]?([1-7])\b", text)
    x["itr_form"] = f"ITR-{form.group(1)}" if form else None
    nm = re.search(r"\bname\s*[:\-]?\s*([A-Z][A-Z .]{3,60})\n", text)
    x["assessee_name"] = nm.group(1).strip().title() if nm else None
    fd = re.search(r"(?:filed|submitted|e-?filed)\s*(?:on)?\s*[:\-]?\s*(\d{1,2}[-/.][\w]{2,3}[-/.]\d{2,4})", text, re.I)
    x["filing_date"] = to_date(fd.group(1)).isoformat() if fd and to_date(fd.group(1)) else None
    for field, pat in [("gross_total_income", r"gross total income"), ("deductions_chapter_via", r"(total )?deductions"),
                       ("total_income", r"\btotal income\b(?! *tax)"), ("tax_payable", r"(net )?tax payable|total tax"),
                       ("taxes_paid_tds_advance", r"taxes paid|total taxes paid"),
                       ("refund_or_demand", r"refundable|refund|\(\+\)\s*tax payable")]:
        m = re.search(pat + r"[^\n\d]{0,60}?(?:\n\s*)?(?:\b\d{1,2}\s+)?(?P<v>\d[\d,]{2,})\b", text, re.I)
        if m:
            x[field] = to_num(m.group("v"))
    if x["gross_total_income"] is None and x["total_income"] is None:
        return None
    x["source_pages"] = [1]
    x["notes"] = ["Read without AI from the ITR-V acknowledgement (totals only; income by head needs the full return)."]
    return x


# ------------------------------------------------------------------ entry point

def extract(doc, related_names: list[str] | None = None):
    """Fill doc.extracted from the file itself. Sets doc.status 'extracted' or 'failed'. Never raises."""
    if doc.status == "unreadable":
        return doc
    if doc.encrypted:
        doc.status, doc.error = "locked", "PDF is password protected - enter the password and retry"
        return doc
    ext = doc.ext
    try:
        text = doc_text(doc.data, ext, 6)
        if ext == ".pdf" and len(text.strip()) < 50:
            doc.status, doc.error = "failed", ("Scanned image with no text layer - add an API key to read it with "
                                               "Claude, or upload the digital / Excel version")
            return doc
        kind = doc.doc_type if doc.doc_type and doc.doc_type != "auto" else detect_type(text, doc.filename)
        order = [kind] + [k for k in ("bank_statement", "financials", "gst", "itr") if k != kind]
        result = None
        for k in order:
            if k == "bank_statement":
                result = parse_bank(doc.data, ext, doc.filename, related_names)
            elif k == "financials":
                result = parse_financials(doc.data, ext)
            elif k == "gst":
                result = parse_gst(doc.data, ext)
            elif k == "itr":
                result = parse_itr(doc_text(doc.data, ext)) if ext in (".pdf", ".txt") else None
            if result:
                if k != kind and kind != "other":
                    doc.warnings.append(f"Uploaded as {kind}, but it reads as {k} - treated as {k}")
                doc.doc_type = k
                break
        if not result:
            doc.status, doc.error = "failed", ("Couldn't read this layout without AI - check it is the digital "
                                               "statement (not a scan), upload the Excel/CSV version, or add an API key")
            return doc
        doc.extracted = result
        doc.entity = doc.entity or result.get("entity_name") or result.get("legal_name") \
            or result.get("account_holder") or result.get("assessee_name")
        doc.status, doc.error = "extracted", None
    except Exception as exc:
        doc.status, doc.error = "failed", f"Couldn't read the file ({type(exc).__name__}: {exc})"
    return doc
