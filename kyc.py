"""KYC document checklist for the borrowing entity and its directors / partners / guarantors.

KYC is collected and attached to the credit file as documentation only; nothing here verifies it.
Checklists follow the RBI KYC Master Direction (2016, as updated) Part III (legal entities) and
Part IV (beneficial owners: >10% for companies and firms, >15% for unincorporated associations),
plus lender practice (board resolution; LEI for non-individual borrowers with exposure >= Rs 5 Cr).
"""

import re

CONSTITUTIONS = ["Private limited company", "Public limited company", "One person company (OPC)",
                 "Limited liability partnership (LLP)", "Partnership firm", "Sole proprietorship", "Trust / society"]

# kind -> label
ENTITY_DOCS = {
    "certificate_of_incorporation": "Certificate of incorporation",
    "moa": "Memorandum of association (MOA)",
    "aoa": "Articles of association (AOA)",
    "entity_pan": "PAN of the entity",
    "gst_certificate": "GST registration certificate (REG-06)",
    "udyam": "Udyam registration certificate",
    "board_resolution": "Board resolution to borrow & authorised signatories",
    "shareholding": "Shareholding pattern / list of members (MGT-7 / CA-certified)",
    "address_proof": "Registered office address proof (utility bill / rent agreement)",
    "lei": "Legal Entity Identifier (LEI) certificate",
    "llp_agreement": "LLP agreement",
    "partnership_deed": "Partnership deed",
    "firm_registration": "Registration certificate of the firm",
    "partners_authority": "Letter of authority from all partners",
    "business_proof": "Proof of business (shop & establishment / trade licence)",
    "trust_deed": "Trust deed / society bye-laws",
}
PERSON_DOCS = {
    "pan": "PAN card",
    "aadhaar": "Aadhaar (masked – last 4 digits only)",
    "passport": "Passport",
    "voter_id": "Voter ID",
    "driving_licence": "Driving licence",
    "photo": "Photograph",
    "networth": "Net-worth statement (guarantors)",
}
ALL_KINDS = {**ENTITY_DOCS, **PERSON_DOCS, "other": "Other"}

REQUIRED = {
    "company": ["certificate_of_incorporation", "moa", "aoa", "entity_pan", "gst_certificate", "board_resolution",
                "shareholding", "address_proof"],
    "llp": ["certificate_of_incorporation", "llp_agreement", "entity_pan", "gst_certificate", "board_resolution",
            "address_proof"],
    "partnership": ["partnership_deed", "entity_pan", "gst_certificate", "partners_authority", "address_proof"],
    "proprietorship": ["gst_certificate", "business_proof", "address_proof"],
    "trust": ["trust_deed", "entity_pan", "board_resolution", "address_proof"],
}


def family(constitution: str) -> str:
    c = constitution.lower()
    if "company" in c or "opc" in c:
        return "company"
    if "llp" in c:
        return "llp"
    if "partnership" in c:
        return "partnership"
    if "proprietor" in c:
        return "proprietorship"
    return "trust"


# Filename keywords -> kind, checked in order (first match wins).
KEYWORDS = [
    ("aadhaar", "aadhaar"), ("aadhar", "aadhaar"), ("passport", "passport"), ("voter", "voter_id"),
    ("driving", "driving_licence"), ("licence", "driving_licence"), ("photo", "photo"), ("net worth", "networth"),
    ("networth", "networth"), ("incorporation", "certificate_of_incorporation"), ("coi", "certificate_of_incorporation"),
    ("moa", "moa"), ("memorandum", "moa"), ("aoa", "aoa"), ("articles", "aoa"), ("udyam", "udyam"),
    ("resolution", "board_resolution"), ("shareholding", "shareholding"), ("mgt", "shareholding"), ("lei", "lei"),
    ("llp agreement", "llp_agreement"), ("partnership deed", "partnership_deed"), ("deed", "partnership_deed"),
    ("authority", "partners_authority"), ("trust", "trust_deed"), ("bye", "trust_deed"), ("gst", "gst_certificate"),
    ("reg-06", "gst_certificate"), ("address", "address_proof"), ("electricity", "address_proof"),
    ("rent", "address_proof"), ("shop", "business_proof"), ("trade licence", "business_proof"),
]


def guess_kind(filename: str, person: bool) -> str:
    """Best guess from the file name; the analyst corrects it in the table."""
    n = re.sub(r"[_\-.]+", " ", filename.lower())
    for word, kind in KEYWORDS:
        if re.search(rf"\b{word.replace('-', ' ')}", n) and (kind in PERSON_DOCS) == person:
            return kind
    if re.search(r"\bpan\b", n):
        return "pan" if person else "entity_pan"
    return "other"
