"""Security (primary, collateral, guarantees) catalogue and cover calculation.

Margins are typical Indian bank haircuts used to get from market value to a
realisable / lendable value. Lenders differ - they are editable in the UI.

Security is optional (step 2): the case says whether security is offered - "yes", "no" (unsecured request) or
"later" - and the checks below follow that choice instead of treating every unsecured case as a defect.
"""

from datetime import date

from schemas import NUM, PAGES, STR, _obj

SECURITY_DOC_SCHEMA = _obj({
    "document_type": {"type": "string", "description": "Valuation report, title deed, sale deed, encumbrance certificate, "
                                                      "FD receipt, share statement, insurance policy, etc."},
    "suggested_security_kind": {"type": "string", "enum": [
        "Residential property", "Commercial property", "Industrial property / factory", "Open land", "Plant & machinery",
        "Stock (inventory)", "Book debts / receivables (< 90 days)", "Vehicles", "Fixed deposit", "Listed shares",
        "Mutual fund units", "Life insurance policy (surrender value)", "Gold", "Government securities (G-Secs / T-bills)",
        "Sovereign Gold Bonds (SGB)", "NSC / KVP / post office deposits", "Bonds / debentures (listed)",
        "Assignment of lease rentals / escrow", "Cash margin", "Personal guarantee", "Corporate guarantee", "CGTMSE cover"]},
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
    "Government securities (G-Secs / T-bills)": ("Collateral", "Lien / pledge", 10,
                                                 "Demat / SGL / RBI Retail Direct holding statement with lien marked"),
    "Sovereign Gold Bonds (SGB)": ("Collateral", "Lien / pledge", 25, "SGB holding statement with lien marked in favour of the bank"),
    "NSC / KVP / post office deposits": ("Collateral", "Pledge / transfer", 15, "Original certificates pledged / transferred to the bank"),
    "Bonds / debentures (listed)": ("Collateral", "Pledge", 25, "Demat pledge confirmation; credit rating of the issue"),
    "Assignment of lease rentals / escrow": ("Primary", "Assignment + escrow", 15, "Lease deeds, tripartite / escrow agreement"),
    "Cash margin": ("Primary", "Lien", 0, "FD under lien"),
    "Personal guarantee": ("Guarantee", "Guarantee", 100, "Guarantee deed, guarantor KYC, net-worth statement, ITRs"),
    "Corporate guarantee": ("Guarantee", "Guarantee", 100, "Board resolution of guarantor, financials"),
    "CGTMSE cover": ("Guarantee", "Credit guarantee", 100, "Udyam registration; facility ≤ ₹10 Cr; guarantee fee"),
}

PROPERTY = {"Residential property", "Commercial property", "Industrial property / factory", "Open land"}
OPTED = {"yes": "Offering security", "no": "No security (unsecured request)", "later": "Decide later"}
REVALUE_YEARS = 3  # property valuations are usually refreshed every 3 years


CASH_MARGIN = "Cash margin"  # already deducted from the exposure it backs, so it is not counted again as cover


def realisable(sec: dict) -> float:
    """Value counted towards cover. Guarantees count as nil (they are recourse, not realisable assets), and so does
    the cash margin on non-fund limits, because the exposure to be covered is already net of it."""
    if SECURITY_TYPES[sec["kind"]][0] == "Guarantee" or sec["kind"] == CASH_MARGIN:
        return 0.0
    return (sec.get("value") or 0) * (1 - (sec.get("margin_pct") or 0) / 100)


def cover(securities: list, total_exposure: float) -> dict:
    tangible = sum(realisable(s) for s in securities)
    market = sum((s.get("value") or 0) for s in securities
                 if SECURITY_TYPES[s["kind"]][0] != "Guarantee" and s["kind"] != CASH_MARGIN)
    collateral = sum(realisable(s) for s in securities if SECURITY_TYPES[s["kind"]][0] == "Collateral")
    return {
        "market_value": market,
        "realisable_value": tangible,
        "collateral_realisable": collateral,
        "cover": tangible / total_exposure if total_exposure else None,
        "collateral_cover": collateral / total_exposure if total_exposure else None,
        "guarantees": [s for s in securities if SECURITY_TYPES[s["kind"]][0] == "Guarantee"],
    }


def requirement_flags(securities: list, facilities: list, opted: str) -> list:
    """Warnings (never blocks) where the security chosen and the products chosen disagree, plus the note that an
    unsecured request usually prices higher. facilities: dicts with product and secured (bool). [(sev, finding, step)]"""
    out = []
    debt = [f for f in facilities if f.get("product")]
    secured = [f["product"] for f in debt if f.get("secured")]
    unsecured = [f["product"] for f in debt if not f.get("secured")]
    tangible = [s for s in securities if SECURITY_TYPES[s["kind"]][0] != "Guarantee"]
    if secured and (opted == "no" or not tangible):
        out.append(("Medium", f"Secured product(s) chosen but no security {'offered (marked unsecured)' if opted == 'no' else 'entered yet'}: "
                              + ", ".join(secured), "Add security in step 2, or mark the facility unsecured in step 3"))
    if unsecured and not secured and tangible:
        out.append(("Low", "Only unsecured products are chosen, yet security is offered - mark the facility secured in step 3, "
                           "or confirm the security is extra comfort", "Reclassify or confirm"))
    if debt and (opted == "no" or (unsecured and not secured)):
        out.append(("Low", "Unsecured request: lenders usually price it higher and set stricter terms (shorter tenor, "
                           "lower amount, promoter guarantees)", "Information only - no rate is assumed"))
    return out


def security_flags(securities: list, total_exposure: float, msme_class: str | None, fund_total: float,
                   cash_margins: float = 0.0, opted: str = "yes") -> list:
    """Returns [(severity, finding, next_step)]. cash_margins: cash margin on the non-fund limits, already netted
    from total_exposure. opted: 'yes' / 'no' / 'later' (step 2 choice)."""
    out, c = [], cover(securities, total_exposure)
    if not securities:
        if opted == "yes":
            out.append(("Medium", "Security is offered but none has been entered yet", "Add each security in step 2"))
        elif opted == "later":
            out.append(("Low", "Security not decided yet", "Decide in step 2 whether security is offered"))
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
    if cash_margins and any(s["kind"] == "Fixed deposit" for s in securities):
        out.append(("Low", "A fixed deposit is offered as security and the non-fund limits also carry a cash margin, which "
                           "is already deducted from the exposure", "If the FD is that cash margin, record it as "
                           "'Cash margin' so it is not counted twice"))
    has_cgtmse = any(s["kind"] == "CGTMSE cover" for s in securities)
    if has_cgtmse and (msme_class not in ("Micro", "Small") or fund_total > 1e8):
        out.append(("High", "CGTMSE cover selected but the borrower is not a micro/small enterprise or the exposure exceeds ₹10 Cr",
                    "Check CGTMSE eligibility"))
    if not any(SECURITY_TYPES[s["kind"]][0] == "Guarantee" and s["kind"] == "Personal guarantee" for s in securities):
        out.append(("Low", "No personal guarantee of promoters", "Banks usually take promoter guarantees for MSME loans"))
    return out
