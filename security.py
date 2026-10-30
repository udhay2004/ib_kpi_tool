"""Security (primary, collateral, guarantees) catalogue and cover calculation.

Margins are typical Indian bank haircuts used to get from market value to a
realisable / lendable value. Lenders differ - they are editable in the UI.
"""

from datetime import date

from schemas import NUM, PAGES, STR, _obj

SECURITY_DOC_SCHEMA = _obj({
    "document_type": {"type": "string", "description": "Valuation report, title deed, sale deed, encumbrance certificate, "
                                                      "FD receipt, share statement, insurance policy, etc."},
    "suggested_security_kind": {"type": "string", "enum": [
        "Residential property", "Commercial property", "Industrial property / factory", "Open land", "Plant & machinery",
        "Stock (inventory)", "Book debts / receivables (< 90 days)", "Vehicles", "Fixed deposit", "Listed shares",
        "Mutual fund units", "Life insurance policy (surrender value)", "Gold", "Assignment of lease rentals / escrow",
        "Cash margin", "Personal guarantee", "Corporate guarantee", "CGTMSE cover"]},
    "description": {"type": ["string", "null"], "description": "Address / survey no. / asset description"},
    "owner": STR,
    "market_value": NUM,
    "realisable_or_distress_value": NUM,
    "valuation_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
    "valuer": STR,
    "existing_charge_or_encumbrance": STR,
    "area": STR,
    "source_pages": PAGES,
    "issues": {"type": "array", "items": {"type": "string"},
               "description": "Title defects, encumbrances, discrepancies, approvals missing, etc."},
})
SECURITY_PROMPT = ("This is a document about security offered for a business loan in India (valuation report, title / "
                   "sale deed, encumbrance certificate, FD receipt, etc.). Extract the fields. Amounts in absolute rupees.")

# kind -> (class, charge, default margin %, documents to collect)
SECURITY_TYPES = {
    "Residential property": ("Collateral", "Equitable / registered mortgage", 40,
                             "Title deed, 13–30 yr encumbrance certificate, valuation report, legal search report, tax receipt"),
    "Commercial property": ("Collateral", "Equitable / registered mortgage", 40,
                            "Title deed, encumbrance certificate, valuation report, legal search report, approved plan"),
    "Industrial property / factory": ("Collateral", "Equitable / registered mortgage", 50,
                                      "Title deed / lease deed, NOC from industrial authority, valuation report"),
    "Open land": ("Collateral", "Equitable / registered mortgage", 50,
                  "Title deed, land records (7/12, khata), conversion order, valuation report"),
    "Plant & machinery": ("Primary", "Hypothecation", 30, "Invoices, chartered engineer / valuer report, insurance"),
    "Stock (inventory)": ("Primary", "Hypothecation", 25, "Monthly stock statement, insurance, stock audit"),
    "Book debts / receivables (< 90 days)": ("Primary", "Hypothecation", 40, "Debtor ageing, stock-and-debtor statement"),
    "Vehicles": ("Primary", "Hypothecation (RC endorsement)", 25, "Invoice, RC with hypothecation, insurance"),
    "Fixed deposit": ("Collateral", "Lien", 10, "FD receipt with lien noted"),
    "Listed shares": ("Collateral", "Pledge", 50, "Demat pledge confirmation (depository)"),
    "Mutual fund units": ("Collateral", "Lien / pledge", 30, "Lien marking confirmation from RTA"),
    "Life insurance policy (surrender value)": ("Collateral", "Assignment", 10, "Policy + assignment deed"),
    "Gold": ("Collateral", "Pledge", 25, "Purity / valuation certificate"),
    "Assignment of lease rentals / escrow": ("Primary", "Assignment + escrow", 15, "Lease deeds, tripartite / escrow agreement"),
    "Cash margin": ("Primary", "Lien", 0, "FD under lien"),
    "Personal guarantee": ("Guarantee", "Guarantee", 100, "Guarantee deed, guarantor KYC, net-worth statement, ITRs"),
    "Corporate guarantee": ("Guarantee", "Guarantee", 100, "Board resolution of guarantor, financials"),
    "CGTMSE cover": ("Guarantee", "Credit guarantee", 100, "Udyam registration; facility ≤ ₹10 Cr; guarantee fee"),
}

PROPERTY = {"Residential property", "Commercial property", "Industrial property / factory", "Open land"}
REVALUE_YEARS = 3  # property valuations are usually refreshed every 3 years


def realisable(sec: dict) -> float:
    """Value counted towards cover; guarantees count as nil (they are recourse, not realisable assets)."""
    if SECURITY_TYPES[sec["kind"]][0] == "Guarantee":
        return 0.0
    return (sec.get("value") or 0) * (1 - (sec.get("margin_pct") or 0) / 100)


def cover(securities: list, total_exposure: float) -> dict:
    tangible = sum(realisable(s) for s in securities)
    market = sum((s.get("value") or 0) for s in securities if SECURITY_TYPES[s["kind"]][0] != "Guarantee")
    collateral = sum(realisable(s) for s in securities if SECURITY_TYPES[s["kind"]][0] == "Collateral")
    return {
        "market_value": market,
        "realisable_value": tangible,
        "collateral_realisable": collateral,
        "cover": tangible / total_exposure if total_exposure else None,
        "collateral_cover": collateral / total_exposure if total_exposure else None,
        "guarantees": [s for s in securities if SECURITY_TYPES[s["kind"]][0] == "Guarantee"],
    }


def security_flags(securities: list, total_exposure: float, msme_class: str | None, fund_total: float) -> list:
    """Returns [(severity, finding, next_step)]."""
    out, c = [], cover(securities, total_exposure)
    if not securities:
        out.append(("High", "No security offered", "Ask the client what security they can offer"))
        return out
    if c["cover"] is not None and c["cover"] < 1.0:
        out.append(("High", f"Security cover {c['cover']:.2f}x – below 1.0x of exposure",
                    "Seek additional collateral or reduce the limit"))
    elif c["cover"] is not None and c["cover"] < 1.25:
        out.append(("Medium", f"Security cover {c['cover']:.2f}x – below the usual 1.25x comfort",
                    "Consider additional collateral"))
    today = date.today()
    for s in securities:
        vd = s.get("valuation_date")
        if s["kind"] in PROPERTY:
            if not vd:
                out.append(("Low", f"{s['kind']}: no valuation date", "Get a valuation report"))
            elif (today - vd).days > REVALUE_YEARS * 365:
                out.append(("Medium", f"{s['kind']} valuation is from {vd:%b %Y} – older than {REVALUE_YEARS} years",
                            "Get a fresh valuation from an empanelled valuer"))
        if s.get("existing_charge"):
            out.append(("High", f"{s['kind']} ({s.get('description') or 'n/a'}) already has a charge: {s['existing_charge']}",
                        "Obtain NOC / pari passu consent from the existing lender"))
        if s.get("owner_is_third_party"):
            out.append(("Low", f"{s['kind']} is owned by a third party ({s.get('owner') or 'n/a'})",
                        "Take the owner's guarantee and consent to mortgage"))
    has_cgtmse = any(s["kind"] == "CGTMSE cover" for s in securities)
    if has_cgtmse and (msme_class not in ("Micro", "Small") or fund_total > 1e8):
        out.append(("High", "CGTMSE cover selected but the borrower is not a micro/small enterprise or the exposure exceeds ₹10 Cr",
                    "Check CGTMSE eligibility"))
    if not any(SECURITY_TYPES[s["kind"]][0] == "Guarantee" and s["kind"] == "Personal guarantee" for s in securities):
        out.append(("Low", "No personal guarantee of promoters", "Banks usually take promoter guarantees for MSME loans"))
    return out
