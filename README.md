# Credit Appraisal Workbench

A guided credit-appraisal case file for Indian business loans, in six steps:

| Step | What happens |
|---|---|
| ① Loan request | Borrower profile (constitution, MSME class, existing exposure). Pick from 30 facilities: **fund based** (CC, OD, WCDL, bill discounting, supply-chain finance, packing credit, post-shipment, term loans, machinery, vehicle, project, construction, LAP, LRD, unsecured, bridge, acquisition, loan against FD) and **non-fund based** (financial / performance / bid / advance-payment / retention / customs guarantees, DPG, SBLC, letters of credit, co-acceptance, partial credit enhancement, derivative limits). Each facility asks for its own fields. |
| ② Security | Property, plant & machinery, stock, receivables, vehicles, FD, shares, MF, LIC, gold, lease rentals, cash margin, personal and corporate guarantees, CGTMSE. Market value → realisable value via editable margins → **security cover**. Upload valuation reports and title deeds; Claude reads them and you can add each one as a security with one click. Flags stale valuations (> 3 yrs), existing charges, third-party ownership and CGTMSE eligibility (MSE, ≤ ₹10 Cr). |
| ③ KYC | Document checklist by constitution (RBI KYC Master Direction): COI, MOA, AOA, PAN, GST, board resolution, shareholding, address proof (+ Udyam, LEI if applicable); PAN + ID proof for each director / partner / guarantor. Files are **attached to the credit file as documentation only, not read or verified**. The type is guessed from the file name and can be corrected. |
| ④ Financials | Audited financials, bank statements, GST, optional ITRs → KPIs, cross-checks, red flags (details below). |
| ⑤ Repayment | **How they will repay**: sources of repayment (cash accruals for term debt, the operating cycle for CC/OD), DSCR year by year with the new debt on top of existing debt, cash left in the tightest year, how far earnings can fall before DSCR < 1.25x and < 1.0x, a stress test (no growth, revenue −10% / −20%, interest +2%), debt / EBITDA after the proposal, and what the bank statements and GST confirm (credits vs revenue, EMI burden, bounces). Ends in a verdict (Comfortable / Adequate / Stretched / Insufficient) with written observations that go into the credit note. Also the repayment structure requested (EMI, step-up, ballooning, bullet, structured; moratorium) and eligibility per facility (turnover method, MPBF, LTV, LRD…). |
| ⑥ Credit summary | Readiness by step, key numbers, the proposal table, every finding from all steps. Fill in the report details (bank, branch, prepared by, reference, your recommendation) and download the **credit report (PDF)** for the bank — see below. Also the **credit file (.zip)** (the PDF, the Excel credit note and every original file), the Excel credit note alone, or the **case file** (.json) to reopen later from the sidebar. |

---

# Financial analysis (step ④)

Upload a company's documents (audited financials, bank statements, GST returns and, optionally, director ITRs) and get
credit/IB KPIs, cross-document checks and evidence-linked red flags. It follows the diligence route of the Oria agent pack:

| Pack agent | Here |
|---|---|
| Data Room Synthesis | Each file gets a stable ID and SHA-256 hash. `local_parse.py` reads it **on the server, with no API**: bank statements (CSV / Excel / digital PDF, transaction by transaction), GSTR-3B PDFs and GST summaries, Schedule III financial statements, ITR-V. Scanned images fall back to Claude (`extractor.py`) when an API key is set. A missing value stays empty and is never guessed. A file uploaded in the wrong section is detected and read as what it really is. |
| Human review gate | Tab ②: the analyst checks and edits every extracted figure before it's used. |
| Earnings Quality Review | `kpis.py` compares GST vs books, bank credits vs revenue, and reported vs computed EBITDA. |
| Risk Flag Register | `kpis.py` ranks the flags (High/Medium/Low), each with its evidence and a next step, plus a scorecard per area. |

Every KPI is a plain Python formula you can check; an AI model, if used at all, only reads scanned documents.

### Reading without an API key

| Document | Read locally | Notes |
|---|---|---|
| Bank statement | CSV / XLS / XLSX download from net-banking, or the digital PDF | Every transaction is parsed; monthly totals, balances on the 5/10/15/20/25th, cash deposits, EMIs, bounces (charge lines counted once, GST on charges ignored), own / related transfers (borrower and KYC names), top counterparties. Bank name from the IFSC code. A running-balance check flags pages that did not parse cleanly. |
| GST | GSTR-3B PDFs from the portal (one or many months in one file), or a month-wise Excel summary | Taxable, zero-rated and exempt turnover, tax, net ITC, filing date vs due date. |
| Financial statements | Digital PDF or Excel in Schedule III format | Both years, units (₹ / thousands / lakhs / crores) converted; borrowings split by non-current / current section; auditor qualification detected. |
| ITR | ITR-V acknowledgement | Totals only. |
| Scanned PDFs, photos | — | Need an API key (Claude), or ask the client for the digital / Excel version. |

Always check the **Review** tab: the parser is rule-based and unusual layouts can be misread.

## The credit report (PDF) for the bank

One PDF in the usual credit appraisal memorandum (CAM) order. There is no single RBI format — each bank has its own — so the
sections follow what bank credit notes commonly contain:

1. Proposal at a glance — key numbers vs benchmarks, facilities, repayment verdict, high findings, your recommendation
2. Borrower profile — constitution, industry, MSME class, GSTIN, bank accounts, promoters / directors / guarantors
3. Proposed facilities and terms · 4. Assessment of limits (turnover method / MPBF / project cost / NFB)
5. Financial analysis — summary financials and ratios by year · 6. Banking conduct — account KPIs, month-wise table,
   largest payers · 7. GST returns · 8. Promoters' ITRs
9. Security — each item with charge, value, margin, realisable value; cover; documents needed to create the charge
10. Repayment capacity — verdict, written observations, measures, stress tests, DSCR by year, repayment schedules
11. Risks and mitigants — every finding from all steps
12. Compliance checks for the bank — credit bureau, RBI wilful-defaulter list, CKYC, GSTIN, CRILC (≥ ₹5 Cr), MCA charges,
    Udyam, legal / valuation, CERSAI, ECGC (exports), site visit — with a blank column for the bank's remarks
13. Suggested terms and conditions — before and after disbursement
14. KYC document index (and what is still missing) · 15. Other documents relied on · 16. Basis of preparation and signature

Then the **annexures**: every KYC, financial and security file uploaded, merged into the same PDF (PDFs as they are,
photos / scans on A4 pages), each page stamped with its annexure number. Excel, Word and CSV files are listed and go in the
ZIP instead. Password-protected PDFs must be unlocked in the app first. The PDF has bookmarks for every section and
annexure. KYC is attached as received; verification and the checks in section 12 are for the bank.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501 and click **Load demo**, or start a case. No API key is needed for digital documents;
add one in the sidebar only to read scanned PDFs and valuation reports.

> **"Connection error" on Windows?** Antivirus HTTPS scanning (Avast, Kaspersky, etc.) or a corporate proxy replaces
> the site certificate. The app uses the operating system's certificate store (`truststore`), so this works out of the box.
> If it still fails, check your firewall.

## Hosting

The app keeps uploaded documents in memory for the browser session only and writes nothing to disk.
Set two secrets on your host, as environment variables or in `.streamlit/secrets.toml` (see `secrets.toml.example`):

| Secret | Purpose |
|---|---|
| `APP_PASSWORD` | **Set this.** A team password screen in front of the app. |
| `ANTHROPIC_API_KEY` | Optional. Only for scanned documents and valuation reports. Users then don't need their own key. |

- **Railway:** upload this folder to a GitHub repository, then in Railway choose *New Project → Deploy from GitHub repo*.
  Railway builds the `Dockerfile` (`railway.json` sets the health check). Add `APP_PASSWORD` (and optionally
  `ANTHROPIC_API_KEY`) under *Variables*, then *Settings → Networking → Generate Domain* for the https URL.
- **Render (recommended, paid ~US$7/month for the always-on Starter plan):** push this folder to a GitHub repository,
  then in Render choose *New + → Blueprint* and pick the repository. `render.yaml` sets up the Docker service in the
  Singapore region with a health check; enter `APP_PASSWORD` when asked. You get an `https://….onrender.com` URL
  (a custom domain can be added in Render).
- **Streamlit Community Cloud (free):** push this folder to GitHub, then create an app with `app.py` as the entry file and paste the secrets under *Settings → Secrets*.
- **Docker (Render, Railway, AWS, Azure, a VM):**
  ```bash
  docker build -t diligence-kpi .
  docker run -p 8501:8501 -e ANTHROPIC_API_KEY=sk-ant-... -e APP_PASSWORD=... diligence-kpi
  ```
  Put it behind HTTPS (most hosts do this for you).

Before using real client data, confirm your firm allows sending documents to the Anthropic API.
Uploads are limited to 200 MB per file (`.streamlit/config.toml`).

## Uploading documents

Tab ① lists what to ask the client for. Two ways to upload:

- **Upload by section:** one card per document type.
- **One ZIP with everything:** sub-folders are fine. Files are sorted by name, and Claude identifies the rest.

| Document | Required? |
|---|---|
| Audited financial statements, 2 FYs | Required |
| Bank statements, 12 months, every operating account | Required |
| GST returns: GSTR-3B (+ GSTR-1 and 2B if available), 12 months | Required |
| Director / promoter ITRs | Optional |
| Sanction letters, CMA data, MCA filings | Optional |

Password-protected PDFs (common for bank statements and ITRs) are unlocked on the server with the password you type in.

## What it checks

The benchmarks are common Indian bank/NBFC norms, and every one can be changed in the sidebar. They vary by lender, so set them to the policy you work to.

**Financials (per FY):** revenue growth, EBITDA and PAT margins, tangible net worth, current and quick ratio
(benchmark 1.33, floor 1.17), Debt/TNW (≤3), TOL/TNW (≤4) and TOL/TNW with promoter loans counted as quasi-equity,
Debt/EBITDA, interest coverage (≥2), DSCR (≥1.25 in every year, 1.5 on average), ROE, ROCE, receivable/inventory/payable days,
cash conversion cycle, CFO/EBITDA, free cash flow.

**Bank statements:**
- **Balances:** ABB from the balances on the 5th, 10th, 15th, 20th and 25th; lowest balance; ABB vs. EMI; ABB vs. monthly inflows.
- **Credits:** business credits (excluding own-account transfers and loan disbursals); top-5 payer concentration.
- **Conduct:** cash-deposit share (refer above 20%, high above 40%); transfers to own or related accounts (above 30% flagged as possible fund diversion); outward bounces (0–2 acceptable, 3–5 refer, 6+ serious); OD/CC utilisation.

**GST:**
- Turnover vs. book revenue (more than 10% apart is flagged).
- GSTR-1 vs. 3B gap (Rule 88C notice risk).
- ITC claimed vs. GSTR-2B (more than 20% excess is flagged; Rule 88D).
- Late filings and top-customer concentration.

**Director ITRs (optional):** income by head, year-on-year change.

## Files

`app.py` shell and stepper · `steps/` one module per step · `common.py` shared state and document upload component ·
`loans.py` facility catalogue and eligibility · `security.py` security types and cover · `kyc.py` KYC checklist and file-name
guessing · `repayment.py` schedules and DSCR · `ui.py` theme and components · `intake.py` checklist, ZIP sorting, coverage ·
`extractor.py` Claude extraction · `schemas.py` fields extracted per document type · `kpis.py` formulas, flags, scorecard ·
`report.py` Excel export · `credit_report.py` PDF credit report · `demo_data.py` fictional sample · `test_*.py` tests (`python -m pytest`)

The output is a draft for an analyst to review. It is not a credit or investment decision.
