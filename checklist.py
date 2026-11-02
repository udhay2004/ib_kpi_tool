"""Master document checklist (Connect Ventures format) for a case: entity pack, financial and banking documents,
add-ons for the facilities requested, and the guarantor pack.

Each item is (text, what shows it was received). "kyc:x" and "doc:x" are matched against the files on the case;
None means the app can't tell, so the item stays for the analyst to confirm.
"""

import kyc

ENTITY_PACK = {
    "proprietorship": [
        ("Photograph, PAN & Aadhaar of applicant and co-applicant", "kyc:pan"),
        ("GST and Udyam registration (Shop & Establishment / trade licence where applicable)", "kyc:gst_certificate"),
        ("Latest electricity bill of residence and office", "kyc:address_proof"),
        ("Ownership proof of residence / office, or registered rent agreement", None),
    ],
    "partnership": [
        ("PAN card of the firm", "kyc:entity_pan"),
        ("Registered partnership deed with all amendments", "kyc:partnership_deed"),
        ("Photograph, PAN & Aadhaar of all partners", "kyc:pan"),
        ("Udyam and GST registration certificates", "kyc:gst_certificate"),
        ("Address proof of the firm (latest electricity bill / rent agreement)", "kyc:address_proof"),
        ("Authority letter / partners' resolution authorising the signing partner", "kyc:partners_authority"),
    ],
    "llp": [
        ("PAN card of the LLP", "kyc:entity_pan"),
        ("LLP agreement with Certificate of Incorporation", "kyc:llp_agreement"),
        ("Photograph, PAN & Aadhaar of all partners", "kyc:pan"),
        ("Udyam and GST registration certificates", "kyc:gst_certificate"),
        ("Address proof of the LLP (latest electricity bill / rent agreement)", "kyc:address_proof"),
        ("Authority letter / partners' resolution authorising the signing partner", "kyc:board_resolution"),
    ],
    "company": [
        ("PAN card of the company", "kyc:entity_pan"),
        ("Certificate of Incorporation", "kyc:certificate_of_incorporation"),
        ("MOA", "kyc:moa"),
        ("AOA", "kyc:aoa"),
        ("Latest list of directors (CA/CS certified)", "kyc:directors_list"),
        ("Latest shareholding pattern (CA/CS certified)", "kyc:shareholding"),
        ("Photograph, PAN & Aadhaar of all directors and major shareholders", "kyc:pan"),
        ("Board resolution for the facility and authorised signatories", "kyc:board_resolution"),
        ("Udyam and GST registration certificates", "kyc:gst_certificate"),
        ("Registered office ownership proof (electricity / water bill; rent agreement if leased)", "kyc:address_proof"),
    ],
    "trust": [
        ("PAN of the trust / society", "kyc:entity_pan"),
        ("Trust deed / society bye-laws", "kyc:trust_deed"),
        ("Resolution authorising the borrowing and signatories", "kyc:board_resolution"),
        ("Address proof", "kyc:address_proof"),
    ],
}

COMMON = [
    ("Financial documents", [
        ("Complete financials for the last 2-3 years: ITR with computation of income", "doc:itr"),
        ("Audited Balance Sheet and P&L with all schedules, notes and annexures", "doc:financials"),
        ("Audit report with Form 3CA/3CB and 3CD; Directors' Report for companies", None),
        ("Provisional / YTD financials for the current year", None),
        ("Projections / CMA data (working-capital limits and large term loans)", None),
        ("Net-worth statements of promoters / proposed guarantors", "kyc:networth"),
    ]),
    ("Banking and existing debt", [
        ("Last 12 months' bank statements of all major accounts, downloaded PDF only", "doc:bank_statement"),
        ("Last 12 months' CC/OD statements showing limit and utilisation", None),
        ("Sanction letters of all existing facilities", None),
        ("Repayment schedules / statement of account of all running loans", None),
        ("Completed Loan Obligation Sheet covering every EMI and limit", "client:obligations"),
        ("GSTR-3B returns for the last 12 months", "doc:gst"),
    ]),
]

# facility name keywords -> add-on pack
ADD_ONS = [
    (("unsecured",), "Unsecured business loan", [
        "Details of top 3-5 customers and suppliers (name, share of business, terms)"]),
    (("cash credit", "overdraft", "wcdl", "working capital"), "Working capital (CC / OD)", [
        "CMA data / projected financials for next 2 years", "Latest debtor and creditor ageing lists",
        "Latest stock statement", "Order book / confirmed orders in hand",
        "Existing WC sanction letter and latest renewal (takeover or enhancement)"]),
    (("term loan", "machinery", "vehicle"), "Term / machinery loan", [
        "Quotation / proforma invoice of machinery or assets", "Project cost and means-of-finance statement",
        "Brief project note: capacity, timeline, expected revenue impact", "Capex ledger / proof of margin already spent"]),
    (("property", "lap", "lease rental", "construction"), "Loan against property / secured facility", [
        "Complete chain of title documents", "Latest registered sale deed / conveyance",
        "Approved building plan and Occupancy Certificate", "Latest property tax receipt and utility bill",
        "Release deed / closure proof of any prior charge", "Property photographs; owner's KYC if third-party collateral",
        "For takeover: List of Documents held by the existing lender"]),
    (("invoice", "bill /", "supply chain"), "Invoice discounting / supply-chain finance", [
        "Supplier information: legal name, IEC, goods", "Buyer-wise information, payment terms, mode and Incoterm",
        "Supplier-buyer ledger for the last 2 years", "Latest paid invoices with transport and payment proof",
        "Latest working-capital sanction letter", "3 years' audited financials"]),
    (("packing", "pcfc", "post-shipment"), "Export / import finance", [
        "Import Export Code (IEC) certificate", "Confirmed export orders / export LCs in hand",
        "Last 2 years' shipping bills and e-BRC / FIRC record", "Country and buyer concentration summary"]),
    (("guarantee", "letter of credit", "sblc", "co-acceptance"), "LC / bank guarantee", [
        "Underlying contract / purchase order / tender document", "Proforma invoice of supplier (LC)",
        "Required BG format and beneficiary details (BG)", "Margin availability (FD / cash margin)",
        "Existing non-fund limits and utilisation"]),
    (("project", "acquisition"), "Project / structured finance", [
        "Detailed Project Report with implementation schedule", "Statutory approvals, licences and land papers",
        "Proof of promoter contribution", "TEV study / lender's-engineer report where available",
        "Group company financials and group exposure summary"]),
]

GUARANTOR_PACK = [
    ("Photograph, PAN & Aadhaar of each guarantor / co-applicant", "kyc:pan"),
    ("Latest 2 years' ITR with computation (income-earning guarantors)", "doc:itr"),
    ("Net-worth statement and residence ownership proof", "kyc:networth"),
]


def build(case: dict, kyc_kinds: set, doc_types: set) -> list:
    """[(section, item, status)]; status is Received, Pending or To confirm."""
    client = case.get("client") or {}

    def state(mark):
        if mark is None:
            return "To confirm"
        kind, key = mark.split(":")
        have = {"kyc": key in kyc_kinds, "doc": key in doc_types, "client": bool(client.get(key))}[kind]
        return "Received" if have else "Pending"

    out = [("KYC and constitution", t, state(m)) for t, m in ENTITY_PACK[kyc.family(case["borrower"]["constitution"])]]
    for section, items in COMMON:
        out += [(section, t, state(m)) for t, m in items]
    products = " ".join(f["product"].lower() for f in case["facilities"])
    for words, section, items in ADD_ONS:
        if any(w in products for w in words):
            out += [(section, t, "To confirm") for t in items]
    if any(s["kind"] in ("Personal guarantee", "Corporate guarantee") for s in case["securities"]):
        out += [("Guarantor / co-applicant", t, state(m)) for t, m in GUARANTOR_PACK]
    return out
