/* Credit Appraisal Workbench - front end. Renders the state from /api/state and sends every change back to the API.
   No framework: each render rebuilds the current view from the latest state.
   Every value that comes from the server, a case file or a document is passed through esc() before it goes into
   HTML - text and attributes alike. */
"use strict";

const STEPS = ["Loan request", "Security", "KYC", "Financials", "Repayment", "Credit summary"];
const FIN_ICONS = { financials: "📑", bank_statement: "🏦", gst: "🧾", itr: "👤", other: "📎" };
const CLIENT_TABLES = {
  promoters: [["Name", "text"], ["Designation", "text"], ["PAN", "text"], ["Aadhaar last 4", "text"], ["Mobile", "text"], ["Email", "text"],
    ["DIN", "text"], ["Share %", "num"], ["Passport no", "text"], ["Age", "num"], ["Qualification", "text"], ["Experience yrs", "num"],
    ["Residence", "Owned|Rented"], ["CIBIL", "num"], ["CRIF / Experian", "text"]],
  obligations: [["Lender", "text"], ["Product", "text"], ["Security", "Secured|Unsecured"], ["Loan amount ₹", "num"], ["EMI ₹", "num"],
    ["Outstanding ₹", "num"], ["Term months", "num"], ["ROI %", "num"], ["EMI start", "text"], ["Months left", "num"],
    ["Count in FOIR", "Y|N"], ["Close with new loan", "Y|N"]],
  customers: [["Name", "text"], ["% of sales", "num"], ["Credit days", "num"]],
  suppliers: [["Name", "text"], ["% of purchases", "num"], ["Credit days", "num"]],
  receivables: [["Particulars", "text"], ["Amount ₹", "num"]],
};
const EXPENSES = [["salaries", "Salaries"], ["rent", "Rent"], ["monthly_emi", "Monthly EMI"], ["cc_interest", "CC interest"], ["office", "Office expense"]];
const YTD = [["sales", "Sales"], ["ebitda", "EBITDA"], ["depreciation", "Depreciation"], ["interest", "Interest"], ["npbt", "NPBT"], ["pat", "PAT"],
  ["net_worth", "Net worth"], ["tol", "Total outside liabilities"], ["stock", "Stock"], ["debtors", "Debtors"], ["creditors", "Creditors"]];
const REPORT_FIELDS = [["bank", "Bank", "e.g. State Bank of India"], ["branch", "Branch", "e.g. MIDC Ambernath"],
  ["prepared_by", "Prepared by", "Name, firm"], ["reference", "Reference no.", "Your file / proposal reference"]];
const CAM_FIELDS = [["repayment_source", "Primary repayment source"], ["proposed_lenders", "Proposed lenders (primary / fallback)"],
  ["large_entries", "Large or unusual bank entries — explanation"], ["title_status", "Title chain status (ROC & CERSAI checked?)"],
  ["go_no_go", "Go / No-go and rationale"], ["lender_sequencing", "Lender sequencing plan"],
  ["preempted_queries", "Pre-empted credit queries & our answers"], ["approved_by", "Approved by (credit desk) / date"]];

let S = null;          // state from the server
let M = null;          // lists that never change (catalogue, security types...)
const ui = { step: 0, unit: localGet("unit") || "₹ Crore", finTab: "docs", anaTab: "fin", repTab: "cap", upMode: "section",
  newCat: "Fund based", newGrp: null, newProd: null, newSec: null, sev: ["High", "Medium"], kycPerson: "", open: {}, edits: null };
let charts = [];

// ---------------------------------------------------------------- helpers

function localGet(k) { try { return localStorage.getItem("cw_" + k); } catch (e) { return null; } }
function localSet(k, v) { try { localStorage.setItem("cw_" + k, v); } catch (e) { /* private window */ } }
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uid = () => Math.random().toString(16).slice(2, 10);
function inr(v) {
  if (v === null || v === undefined || v === "" || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e7) return "₹" + (v / 1e7).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " Cr";
  if (a >= 1e5) return "₹" + (v / 1e5).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " L";
  return "₹" + Math.round(v).toLocaleString("en-IN");
}
function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj); }
function setPath(obj, path, val) {
  const ks = path.split(".");
  let o = obj;
  ks.slice(0, -1).forEach((k) => { if (o[k] == null || typeof o[k] !== "object") o[k] = /^\d+$/.test(k) ? [] : {}; o = o[k]; });
  o[ks[ks.length - 1]] = val;
}
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), bad ? 6000 : 2600);
}
function busy(on, text) {
  $("#busy").hidden = !on;
  $("#busy-text").textContent = on ? (text || "Working…") : "";
}

const TIMEOUT_MS = 90000, SLOW_TIMEOUT_MS = 600000;  // reading scanned documents with Claude can take minutes
async function fetchWithTimeout(url, opt = {}, ms = TIMEOUT_MS) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try { return await fetch(url, { ...opt, signal: ctl.signal, credentials: "same-origin" }); }
  catch (e) { throw new Error(e.name === "AbortError" ? "The server took too long to answer. Check your connection and try again." : "Could not reach the server. Check your connection and try again."); }
  finally { clearTimeout(timer); }
}

async function api(method, path, body, label, retried) {
  const sep = path.includes("?") ? "&" : "?";
  const url = path.startsWith("/api/") ? path + sep + "unit=" + encodeURIComponent(ui.unit) : path;
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  if (label) busy(true, label);
  try {
    const slow = path.startsWith("/api/read/") || path.startsWith("/api/files/");
    const r = await fetchWithTimeout(url, opt, slow ? SLOW_TIMEOUT_MS : TIMEOUT_MS);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) { showLogin(); throw new Error(data.error || "Sign in first"); }
    if (r.status === 409 && !retried) {
      // The server restarted or the case expired: start a new session, put back the case details still on
      // screen (uploaded files are lost), then repeat the request once.
      await fetchWithTimeout("/api/session");
      if (S && S.case) await fetchWithTimeout("/api/case?unit=" + encodeURIComponent(ui.unit),
        { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(S.case) });
      toast("Your session had expired. The case details were restored; upload any documents again.", true);
      return api(method, path, body, label, true);
    }
    if (!r.ok) throw new Error(data.error || data.detail || r.statusText);
    return data;
  } finally { if (label) busy(false); }
}
async function act(method, path, body, label) {
  try { S = await api(method, path, body, label); render(); return true; } catch (e) { toast(e.message, true); return false; }
}
let saveTimer = null;
function saveCase(now) {
  clearTimeout(saveTimer);
  const go = () => act("PUT", "/api/case", S.case);
  if (now) return go();
  saveTimer = setTimeout(go, 350);
}

// ---------------------------------------------------------------- small components

const pill = (status, text) => `<span class="pill ${status || "neutral"}">${status === "good" ? "✓ " : status === "critical" ? "✕ " : status === "warning" ? "! " : ""}${esc(text)}</span>`;
const section = (title, sub) => `<h2 class="section-title">${esc(title)}</h2>${sub ? `<p class="section-sub">${sub}</p>` : ""}`;
const note = (kind, html) => `<div class="note ${kind}">${html}</div>`;
function tiles(list) {
  return `<div class="tiles">${list.map((t) => `<div class="tile"><div class="l">${esc(t.label)}</div><div class="v">${esc(t.value)}</div>
    <div class="s">${t.status ? pill(t.status, t.bench) : ""}<span>${esc(t.sub || "")}</span></div></div>`).join("")}</div>`;
}
const SEV = { High: "critical", Medium: "warning", Low: "info" };
const SEV_COLOR = { critical: "var(--crit)", warning: "var(--warn)", info: "var(--info)", good: "var(--good)" };
function flags(list) {
  if (!list.length) return note("ok", "No findings.");
  return list.map((f) => `<div class="flag" style="border-left-color:${SEV_COLOR[SEV[f.severity]]}"><div class="hstack">${pill(SEV[f.severity], f.severity)}
    <span class="area">${esc(f.area)}</span></div><div class="f">${esc(f.finding)}</div>
    <div class="m"><b>Evidence:</b> ${esc(f.evidence)} &nbsp;·&nbsp; <b>Next step:</b> ${esc(f.next_step)}</div></div>`).join("");
}
function tbl(t, opts = {}) {
  if (!t || !t.columns || !t.columns.length) return `<p class="muted">Nothing to show.</p>`;
  const num = (v) => /^[-+₹(]?[\d,.]+(\s?(Cr|L|x|%))?\)?$/.test(String(v).trim());
  return `<div class="tbl-wrap ${opts.scroll ? "scroll-y" : ""}"><table class="tbl"><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${t.rows.map((r) => `<tr>${r.map((v, i) => `<td class="${i && num(v) ? "num" : ""}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function field(label, inner, help) { return `<label class="field"><span>${esc(label)}</span>${inner}${help ? `<small>${esc(help)}</small>` : ""}</label>`; }
function input(bind, value, kind = "text", attrs = "") {
  const v = value ?? "";
  if (kind === "textarea") return `<textarea data-bind="${esc(bind)}" ${attrs}>${esc(v)}</textarea>`;
  return `<input type="${kind === "num" ? "number" : kind}" data-bind="${esc(bind)}" data-kind="${kind}" value="${esc(v)}" ${kind === "num" ? 'step="any"' : ""} ${attrs}>`;
}
function select(bind, value, options, attrs = "") {
  const opts = options.map((o) => (Array.isArray(o) ? o : [o, o]));
  return `<select data-bind="${esc(bind)}" ${attrs}>${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
}
function seg(name, value, options) {
  return `<div class="seg" data-seg="${name}">${options.map((o) => `<button type="button" data-val="${esc(o)}" class="${o === value ? "on" : ""}">${esc(o)}</button>`).join("")}</div>`;
}
function tabs(name, value, options) {
  return `<div class="tabs" data-tabs="${name}">${options.map(([k, l]) => `<button type="button" data-val="${k}" class="${k === value ? "on" : ""}">${esc(l)}</button>`).join("")}</div>`;
}
function fold(key, title, body, openByDefault) {
  const open = ui.open[key] ?? openByDefault;
  return `<details class="fold" data-fold="${esc(key)}" ${open ? "open" : ""}><summary><span>${title}</span></summary><div class="body">${body}</div></details>`;
}
function uploader(store, opts) {
  const tag = opts.required === undefined ? "" : opts.required ? `<span class="pill req">Required</span>` : `<span class="pill neutral">Optional</span>`;
  const got = opts.count ? pill("good", `${opts.count} file${opts.count === 1 ? "" : "s"} added`) : "";
  return `<div class="card up"><div class="up-head"><div class="up-icon">${opts.icon}</div><div><div class="up-title">${esc(opts.title)} ${tag} ${got}</div>
    <div class="up-ask">${esc(opts.ask || "")}</div></div></div>${opts.extra || ""}
    <label class="drop" data-drop="${store}" data-type="${esc(opts.type || "")}"><div><div class="t">Drop files here or click to choose</div>
    <div class="h">${esc(opts.accept_text || "PDF, images, Excel, CSV")}</div></div><span class="btn sm">Browse</span>
    <input type="file" ${opts.multiple === false ? "" : "multiple"} accept="${esc(opts.accept || ".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.csv,.txt,.json")}"></label></div>`;
}
async function upload(store, type, files) {
  if (!files.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  fd.append("doc_type", type || "auto");
  if (store === "kyc_docs" && type === "person") fd.append("person", ui.kycPerson || "");
  await act("POST", `/api/files/${store}`, fd, `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`);
}
function fileTable(store, files, opts = {}) {
  if (!files.length) return "";
  const rows = files.map((f) => {
    const st = { extracted: "good", failed: "critical", unreadable: "critical", locked: "warning", pending: "info" }[f.status];
    const id = esc(f.doc_id);
    const typeCell = opts.types ? `<select data-file="${store}|${id}|doc_type">${Object.entries(opts.types)
      .map(([k, l]) => `<option value="${k}" ${k === f.doc_type ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>` : "";
    const personCell = opts.person ? `<input type="text" data-file="${store}|${id}|person" value="${esc(f.person)}" placeholder="Person" aria-label="Person">` : "";
    const lock = f.encrypted ? `<div class="hstack" style="margin-top:6px"><input type="password" placeholder="PDF password" aria-label="PDF password" data-pw="${store}|${id}" style="max-width:200px">
      <button class="btn sm" data-unlock="${store}|${id}">Unlock</button></div>` : "";
    return `<tr><td>${esc(f.doc_id)}</td><td>${esc(f.filename)}${lock}</td>${opts.types ? `<td style="min-width:200px">${typeCell}</td>` : ""}
      ${opts.person ? `<td style="min-width:150px">${personCell}</td>` : ""}<td>${opts.noStatus ? "" : pill(st, f.status_text)}</td>
      <td class="muted small">${esc(f.note)}</td><td>${esc(f.pages ?? "")}</td><td><button class="x" title="Remove" aria-label="Remove ${esc(f.filename)}" data-remove="${store}|${id}">✕</button></td></tr>`;
  }).join("");
  return `<div class="tbl-wrap"><table class="tbl edit"><thead><tr><th>ID</th><th>File</th>${opts.types ? "<th>Type</th>" : ""}${opts.person ? "<th>Person</th>" : ""}
    <th>${opts.noStatus ? "" : "Status"}</th><th>Note</th><th>Pages</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function readButton(store, lastRun, hint, needsKey) {
  const files = (store === "docs" ? S.fin.files : S.security.files).filter((f) => ["pending", "failed"].includes(f.status) && !f.encrypted);
  const n = files.length;
  const last = lastRun ? (lastRun.failed ? note("error", `${lastRun.ok} read, ${lastRun.failed} failed. ${esc(lastRun.errors.join(" · "))}`)
    : note("ok", `All ${lastRun.ok} document(s) read.`)) : "";
  const blocked = needsKey && !S.has_key;
  return `${last}<div class="hstack"><button class="btn primary" data-read="${store}" ${!n || blocked ? "disabled" : ""}>Read ${n} document${n === 1 ? "" : "s"} →</button>
    <span class="muted">${hint}</span></div>`;
}

// ---------------------------------------------------------------- charts

const SERIES = ["#23847C", "#C68A2E", "#5B7FD6"];
function chartBox(id) { return `<div class="chart"><canvas id="${esc(id)}" role="img" aria-label="Chart"></canvas></div>`; }
function drawChart(id, data, kind, opts = {}) {
  const el = document.getElementById(id);
  if (!el || !data || !window.Chart) return;
  const names = Object.keys(data.series);
  const datasets = names.map((n, i) => ({ label: n, data: data.series[n], backgroundColor: SERIES[i % 3], borderColor: SERIES[i % 3],
    borderRadius: kind === "bar" ? 6 : 0, borderWidth: kind === "line" ? 2 : 0, pointRadius: kind === "line" ? 3 : 0, tension: 0.25, spanGaps: true,
    maxBarThickness: 34 }));
  if (opts.rule !== undefined) datasets.push({ label: "Benchmark", data: data.labels.map(() => opts.rule), borderColor: "#D64545",
    borderDash: [5, 5], borderWidth: 1.5, pointRadius: 0, type: "line" });
  charts.push(new Chart(el, { type: kind, data: { labels: data.labels, datasets },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top", align: "start", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { family: "Inter" } } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y == null ? "—" : c.parsed.y.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` } } },
      scales: { x: { stacked: !!opts.stack, grid: { display: false }, ticks: { font: { family: "Inter" }, color: "#5C6E72" } },
        y: { stacked: !!opts.stack, title: { display: !!opts.y, text: opts.y || "", color: "#5C6E72" }, grid: { color: "#ECF2F1" },
          ticks: { font: { family: "Inter" }, color: "#5C6E72" } } } } }));
}

// ---------------------------------------------------------------- frame

function renderSidebar() {
  const th = S.thresholds, defaults = M.threshold_defaults;
  $("#sidebar").innerHTML = `
    <div class="brand"><img src="https://compliance.complyglobally.com/logo.png" alt=""><div><div class="t">Credit Appraisal Workbench</div>
      <div class="s">by ComplyGlobally</div></div></div>
    <div class="side-h">Case</div>
    <div class="row2"><button class="btn" data-action="demo" title="A fictional company">Load demo</button><button class="btn" data-action="reset">New case</button></div>
    <div class="spacer"></div>
    <label class="drop" data-open-case><div><div class="t">Open a saved case</div><div class="h">.json case file</div></div><span class="btn sm">Browse</span>
      <input type="file" accept=".json,application/json"></label>
    <div class="side-h">Display</div>
    <div class="field"><span>Amounts in</span>${seg("unit", ui.unit, M.units)}</div>
    <div class="spacer"></div>
    ${fold("bench", "Credit benchmarks", `<p class="muted small" style="margin-top:0">Common Indian bank / NBFC norms. Change them to your lender's policy.</p>
      <p class="muted small">Saved with this case only; other cases keep their own.</p>
      ${Object.entries(th).map(([k, v]) => field(k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
        `<input type="number" step="any" min="0" data-threshold="${esc(k)}" value="${esc(v)}">`, v !== defaults[k] ? `Default ${defaults[k]}` : "")).join("<div style='height:8px'></div>")}`, false)}
    ${S.server_key ? "" : fold("key", "Claude API key (scanned documents)", `<p class="muted small" style="margin-top:0">Not needed for digital PDFs, Excel or CSV:
      those are read on this server. Only scanned documents and valuation reports use Claude. Kept for this session only.</p>
      <input type="password" placeholder="sk-ant-…" data-apikey value="">${S.has_key ? `<div class="spacer"></div>${pill("good", "Key set for this session")}` : ""}`, false)}
    <div class="side-note">Draft analysis. Figures read from documents must be checked against the originals. Save the case file to keep your work.</div>`;
}

function renderFrame() {
  const b = S.case.borrower, t = S.totals;
  const chips = [];
  if (t.total) chips.push(["Proposed", t.text.total]);
  if (S.case.facilities.length) chips.push(["Facilities", String(S.case.facilities.length)]);
  chips.push(["Step", `${ui.step + 1} of ${STEPS.length}`]);
  const sub = b.name ? (b.industry ? `${b.constitution} · ${b.industry}` : b.constitution)
    : "Loan request, security, KYC, financials and repayment, then the credit report for the bank.";
  $("#hero").innerHTML = `<div class="hero"><div><span class="eyebrow"><i></i>Credit Appraisal Workbench${S.demo ? " · demo data" : ""}</span>
    <h1>${esc(b.name || "New credit case")}</h1><p>${esc(sub)}</p></div>
    <div class="chips">${chips.map(([k, v]) => `<div class="chip">${esc(k)}<b>${esc(v)}</b></div>`).join("")}</div></div>`;
  $("#stepper").innerHTML = STEPS.map((s, i) => `<button class="step ${i === ui.step ? "on" : ""}" data-step="${i}"><span class="n">${i + 1}</span>${s}</button>`).join("");
  $("#pager").innerHTML = (ui.step > 0 ? `<button class="btn" data-step="${ui.step - 1}">← ${STEPS[ui.step - 1]}</button>` : "<span></span>")
    + (ui.step < STEPS.length - 1 ? `<button class="btn primary" data-step="${ui.step + 1}">${STEPS[ui.step + 1]} →</button>` : "");
}

function render() {
  if (!S || !M) return;
  const focus = document.activeElement;
  const fkey = focus && (focus.dataset.bind || focus.dataset.cell || focus.dataset.file) ? [focus.dataset.bind, focus.dataset.cell, focus.dataset.file] : null;
  const caret = focus && "selectionStart" in focus ? (() => { try { return focus.selectionStart; } catch (e) { return null; } })() : null;
  charts.forEach((c) => c.destroy()); charts = [];
  renderSidebar();
  renderFrame();
  $("#view").innerHTML = [viewLoan, viewSecurity, viewKyc, viewFinancials, viewRepayment, viewSummary][ui.step]();
  afterRender();
  if (fkey) {
    const sel = fkey[0] ? `[data-bind="${CSS.escape(fkey[0])}"]` : fkey[1] ? `[data-cell="${CSS.escape(fkey[1])}"]` : `[data-file="${CSS.escape(fkey[2])}"]`;
    const el = document.querySelector(sel);
    if (el) { el.focus({ preventScroll: true }); if (caret !== null) try { el.setSelectionRange(caret, caret); } catch (e) { /* number inputs */ } }
  }
}

// ---------------------------------------------------------------- step 1: loan request

function facilityFields(f, i) {
  const p = productSpec(f.product);
  return p.fields.map((k) => {
    const spec = M.fields[k], bind = `facilities.${i}.${spec.key}`, v = f[spec.key] ?? spec.default;
    let inner;
    if (spec.kind.startsWith("choice:")) inner = select(bind, v, spec.kind.slice(7).split("|"));
    else if (spec.kind === "text") inner = input(bind, v);
    else inner = input(bind, v, "num", `min="0" ${spec.kind === "pct" ? 'max="100"' : ""}`) + (spec.kind === "money" && v ? `<small>${inr(v)}</small>` : "");
    return field(spec.label, inner, spec.help);
  }).join("");
}
function productSpec(name) {
  for (const [cat, groups] of Object.entries(M.catalogue)) for (const [grp, prods] of Object.entries(groups))
    if (prods[name]) return { ...prods[name], category: cat, group: grp };
  return { fields: [] };
}
function viewLoan() {
  const b = S.case.borrower, t = S.totals;
  const groups = M.catalogue[ui.newCat];
  if (!ui.newGrp || !groups[ui.newGrp]) ui.newGrp = Object.keys(groups)[0];
  const prods = groups[ui.newGrp];
  if (!ui.newProd || !prods[ui.newProd]) ui.newProd = Object.keys(prods)[0];
  const spec = prods[ui.newProd];
  let h = section("Borrower", "Who is borrowing. The constitution decides the KYC checklist in step 3.");
  h += `<div class="card"><div class="grid g-2-1-1">
    ${field("Legal name of the borrower", input("borrower.name", b.name, "text", 'placeholder="e.g. Acme Polymers Pvt Ltd"'))}
    ${field("Constitution", select("borrower.constitution", b.constitution, M.constitutions))}
    ${field("MSME (Udyam)", select("borrower.msme_class", b.msme_class, M.msme))}
    ${field("Industry / activity", input("borrower.industry", b.industry, "text", 'placeholder="e.g. Plastic packaging manufacturer"'))}
    ${field("Existing bank exposure (₹, all lenders)", input("borrower.existing_exposure", b.existing_exposure, "num", 'min="0"') + (b.existing_exposure ? `<small>${inr(b.existing_exposure)}</small>` : ""))}
    ${field("Years in business", input("borrower.years_in_business", b.years_in_business, "num", 'min="0" step="1"'))}</div></div>`;
  h += section("Facilities requested", "Add each limit the client is asking for. Fund-based facilities lend money; non-fund-based ones (guarantees, LCs) become loans only if invoked.");
  h += `<div class="card"><div class="field"><span>Category</span>${seg("newCat", ui.newCat, Object.keys(M.catalogue))}</div><div class="spacer"></div>
    <div class="grid g2">${field("Type", `<select data-ui="newGrp">${Object.keys(groups).map((g) => `<option ${g === ui.newGrp ? "selected" : ""}>${esc(g)}</option>`).join("")}</select>`)}
    ${field("Facility", `<select data-ui="newProd">${Object.keys(prods).map((g) => `<option ${g === ui.newProd ? "selected" : ""}>${esc(g)}</option>`).join("")}</select>`)}</div>
    <p class="muted">Typical tenor: <b>${esc(spec.tenor)}</b> · Usual security: <b>${esc(spec.security)}</b></p>
    <button class="btn primary" data-action="add-facility">＋ Add this facility</button></div>`;
  S.case.facilities.forEach((f, i) => {
    const p = productSpec(f.product);
    h += fold("fac" + f.id, `${i + 1}. ${esc(f.product)} — ${f.amount ? inr(f.amount) : "amount not set"}`,
      `<div class="hstack" style="margin-bottom:12px">${pill("neutral", p.category)} ${pill("neutral", p.group)}</div>
       <div class="grid g2">${facilityFields(f, i)}</div><div class="spacer"></div>
       <button class="btn sm danger" data-remove-fac="${i}">Remove facility</button>`, !f.amount);
  });
  if (S.case.facilities.length) h += tiles([{ label: "Fund based", value: t.text.fund }, { label: "Non-fund based", value: t.text.nonfund },
    { label: "Total proposed", value: t.text.total }, { label: "Total incl. existing", value: t.with_existing, sub: t.lei ? "LEI needed (≥ ₹5 Cr)" : "Below LEI threshold" }]);
  return h + viewClient();
}

function clientTable(name) {
  const spec = CLIENT_TABLES[name], rows = S.case.client[name] || [];
  const head = spec.map(([c]) => `<th>${esc(c)}</th>`).join("");
  const body = rows.map((r, i) => `<tr>${spec.map(([c, kind]) => {
    const bind = `client.${name}.${i}.${c}`;
    const cell = kind === "text" || kind === "num" ? input(bind, r[c], kind) : select(bind, r[c] ?? "", [["", "—"], ...kind.split("|")]);
    return `<td style="min-width:${kind === "num" ? 110 : 140}px">${cell}</td>`;
  }).join("")}<td><button class="x" data-del-row="${name}|${i}">✕</button></td></tr>`).join("");
  return `<div class="tbl-wrap"><table class="tbl edit"><thead><tr>${head}<th></th></tr></thead><tbody>${body ||
    `<tr><td colspan="${spec.length + 1}" class="muted">No rows yet.</td></tr>`}</tbody></table></div>
    <button class="btn sm" data-add-row="${name}">＋ Add row</button>`;
}
function viewClient() {
  const c = S.case.client;
  const t = (k, label, ph = "") => field(label, input(`client.${k}`, c[k], "text", ph ? `placeholder="${esc(ph)}"` : ""));
  const exp = c.expenses || {}, ytd = c.ytd || {};
  let h = section("Client details", "From the fact-find form and loan detail sheet. These go into the bank report and the internal CAM; anything left blank prints as a dash.");
  h += fold("ff-profile", "Business profile", `<div class="grid g2">${t("address", "Registered address")}${t("city_pin", "City & PIN code")}
    ${t("contact_person", "Contact person")}${t("mobile", "Contact number")}${t("email", "Email ID")}${t("website", "URL / website")}
    ${t("incorporated", "Date of incorporation", "DD/MM/YYYY")}${t("employees", "No. of employees")}${t("udyam_no", "Udyam registration number")}
    ${t("bank_particulars", "Bank particulars", "Bank, branch, account type")}</div><div class="spacer"></div>
    ${field("Nature of business (brief)", input("client.nature", c.nature, "textarea"))}<div class="spacer"></div>
    ${field("Vintage & brief history", input("client.history", c.history, "textarea"))}<div class="spacer"></div>
    ${t("group_companies", "Group companies / associate concerns")}`, !c.address);
  h += fold("ff-promoters", "Directors / partners / promoters", `<p class="muted" style="margin-top:0">Enter only the last 4 digits of Aadhaar. The report always prints it masked.</p>${clientTable("promoters")}`);
  h += fold("ff-obl", "Existing loan obligations — every EMI and limit", clientTable("obligations"));
  h += fold("ff-parties", "Major customers, suppliers and bills receivable", `<h4 class="small" style="margin:4px 0 8px">Customers</h4>${clientTable("customers")}
    <h4 class="small" style="margin:18px 0 8px">Suppliers</h4>${clientTable("suppliers")}<h4 class="small" style="margin:18px 0 8px">Bills receivable</h4>${clientTable("receivables")}`);
  const expTotal = EXPENSES.reduce((a, [k]) => a + (Number(exp[k]) || 0), 0);
  h += fold("ff-exp", "Fixed monthly expenses and decision maker", `<div class="grid g3">${EXPENSES.map(([k, l]) => field(`${l} (₹)`, input(`client.expenses.${k}`, exp[k] || 0, "num", 'min="0"'))).join("")}</div>
    <p class="muted">Total fixed monthly expenses: <b>${inr(expTotal)}</b></p><div class="grid g3">${t("decision_maker", "Name & designation of decision maker")}
    ${t("dm_mobile", "Contact number")}${t("dm_email", "Email ID")}</div>`);
  h += fold("ff-ytd", "Current year-to-date figures (provisional)", `${field("Period", input("client.ytd.period", ytd.period, "text", 'placeholder="e.g. Apr-Sep 2026; leave blank if not available"'))}
    <div class="spacer"></div><div class="grid g4">${YTD.map(([k, l]) => field(`${l} (₹)`, input(`client.ytd.${k}`, ytd[k] || 0, "num"))).join("")}</div>`);
  h += fold("ff-bureau", "Bureau summary", `<div class="grid g2">${t("cmr", "CMR (if company)")}${t("enquiries", "Recent enquiries (90 days)")}
    ${t("dpd", "DPD history / flags (settled, written-off, suit-filed)")}${t("guarantees_given", "Guarantees given by promoters (group exposure)")}</div>`);
  return h;
}

// ---------------------------------------------------------------- step 2: security

function viewSecurity() {
  const types = M.security_types, sx = S.security;
  if (!ui.newSec) ui.newSec = Object.keys(types)[0];
  const st = types[ui.newSec];
  let h = section("Security offered", "Primary security, collateral and guarantees. Margins turn market value into realisable value — change them to your lender's policy.");
  h += `<div class="card"><div class="grid g-3-1" style="align-items:end">${field("Add a security", `<select data-ui="newSec">${Object.keys(types).map((k) =>
    `<option ${k === ui.newSec ? "selected" : ""}>${esc(k)}</option>`).join("")}</select>`)}<button class="btn primary" data-action="add-security">＋ Add</button></div>
    <p class="muted" style="margin-bottom:0">${esc(st.class)} · ${esc(st.charge)} · typical margin ${st.margin}% · Documents: ${esc(st.docs)}</p></div>`;
  S.case.securities.forEach((s, i) => {
    const ty = types[s.kind], guar = ty.class === "Guarantee", k = `securities.${i}`;
    const body = `<div class="hstack" style="margin-bottom:12px">${pill("neutral", ty.class)} ${pill("neutral", ty.charge)}</div>
      <div class="grid g2">${field("Description / address / details", input(`${k}.description`, s.description))}${field("Owner / guarantor", input(`${k}.owner`, s.owner))}</div><div class="spacer"></div>
      ${guar ? field("Guarantor net worth (₹, optional)", input(`${k}.value`, s.value, "num", 'min="0"'))
        : `<div class="grid g3">${field("Market value (₹)", input(`${k}.value`, s.value, "num", 'min="0"') + (s.value ? `<small>${inr(s.value)}</small>` : ""))}
        ${field("Margin / haircut (%)", input(`${k}.margin_pct`, s.margin_pct, "num", 'min="0" max="100"'))}
        <div class="field"><span>Realisable value</span><div class="tile" style="padding:9px 13px;box-shadow:none"><b>${esc(sx.realisable[s.id] || "—")}</b></div></div>
        ${field("Valuation date", input(`${k}.valuation_date`, s.valuation_date ? String(s.valuation_date).slice(0, 10) : "", "date"))}
        ${field("Valuer", input(`${k}.valuer`, s.valuer))}${field("Existing charge (if any)", input(`${k}.existing_charge`, s.existing_charge, "text", 'placeholder="e.g. Mortgaged to SBI"'))}</div>`}
      <div class="spacer"></div><label class="check"><input type="checkbox" data-bind="${k}.owner_is_third_party" ${s.owner_is_third_party ? "checked" : ""}> Owned by a third party (not the borrower)</label>
      <p class="muted">Collect: ${esc(ty.docs)}</p><button class="btn sm danger" data-remove-sec="${i}">Remove</button>`;
    h += fold("sec" + s.id, `${esc(s.kind)} — ${s.value ? inr(s.value) : "value not set"}`, body, !s.value && !guar);
  });
  h += section("Security documents (optional)", "Valuation reports, title deeds, FD receipts. They go into the credit file. With a Claude API key they can also be read, so you can add each one as a security with one click.");
  h += uploader("sec_docs", { icon: "🏠", title: "Valuation reports, title deeds, encumbrance certificates", ask: "PDF, image or Excel", required: false, count: sx.files.length, type: "security" });
  h += fileTable("sec_docs", sx.files, { noStatus: !S.has_key });
  if (sx.files.length && S.has_key) h += readButton("sec_docs", sx.last_run, "Claude reads each document and suggests the security it describes.", true);
  sx.suggestions.forEach((g) => {
    h += `<div class="card hstack between"><div><b>${esc(g.title)}</b><div class="muted">${esc(g.detail)}</div></div>
      <button class="btn ${g.added ? "" : "primary"} sm" data-suggest="${esc(g.doc_id)}" ${g.added ? "disabled" : ""}>${g.added ? "Added ✓" : "Add as security"}</button></div>`;
  });
  if (S.case.securities.length || S.case.facilities.length) {
    h += section("Cover", "Realisable value against exposure (non-fund limits net of cash margin).") + tiles(sx.tiles);
    if (sx.flags.length) h += `<div class="spacer"></div>` + flags(sx.flags);
  }
  return h;
}

// ---------------------------------------------------------------- step 3: KYC

function viewKyc() {
  const k = S.kyc, b = S.case.borrower;
  let h = section("KYC checklist", `Documents to collect for a ${esc(b.constitution.toLowerCase())}. They are attached to the credit file as documentation; the app does not read or verify them.`);
  h += `<div class="card tight">${k.checklist.map((r) => `<div class="cl two"><div class="n">${esc(r.label)}${r.optional ? ' <span class="muted">(if applicable)</span>' : ""}</div>
    <div>${r.ids.length ? pill("good", "Attached · " + r.ids.join(", ")) : pill("neutral", r.optional ? "—" : "Not attached")}</div></div>`).join("")}
    <p class="muted" style="margin:10px 0">For every director, partner, guarantor and beneficial owner (&gt;10% holding): PAN and one ID / address proof (masked Aadhaar, passport, voter ID or driving licence), plus a photograph.</p></div>`;
  const entityN = k.files.filter((f) => M.entity_kinds.includes(f.doc_type)).length, personN = k.files.filter((f) => M.person_kinds.includes(f.doc_type)).length;
  h += `<div class="grid g2">${uploader("kyc_docs", { icon: "🏢", title: "Entity documents", ask: "Certificate of incorporation, MOA, AOA, PAN, GST certificate, board resolution, shareholding, address proof, Udyam, LEI…", required: true, count: entityN, type: "entity" })}
    ${uploader("kyc_docs", { icon: "👥", title: "Directors, partners & guarantors", ask: "PAN, masked Aadhaar, passport / voter ID / DL, photo. Type the person's name, then upload their documents.", required: true, count: personN, type: "person",
      extra: `<input type="text" data-ui="kycPerson" value="${esc(ui.kycPerson)}" placeholder="Name of the director / partner / guarantor">` })}</div>`;
  if (k.files.length) {
    h += section("Attached documents", "The document type is guessed from the file name. Correct it here if needed.");
    h += fileTable("kyc_docs", k.files, { types: M.kyc_kinds, person: true, noStatus: true });
  }
  if (k.static.length) h += `<p class="muted">From the demo / saved case (names only, no files): ${k.static.map((d) => esc(`${d.doc_id} ${d.label}${d.person ? " – " + d.person : ""}`)).join(", ")}</p>`;
  if (k.people.length) h += section("People") + tbl({ columns: ["Person", "Documents attached"], rows: k.people.map((p) => [p.person, p.docs]) });
  return h;
}

// ---------------------------------------------------------------- step 4: financials

function viewFinancials() {
  const f = S.fin;
  let h = tabs("finTab", ui.finTab, [["docs", "Documents"], ["review", "Review extracted data"], ["analysis", "Analysis"]]);
  if (ui.finTab === "docs") {
    h += seg("upMode", ui.upMode === "zip" ? "One ZIP with everything" : "Upload by section", ["Upload by section", "One ZIP with everything"]);
    h += `<div class="spacer"></div>`;
    if (ui.upMode === "zip") {
      h += uploader("docs", { icon: "🗂️", title: "All financial documents in one ZIP", ask: "Sub-folders are fine. Files are sorted by name (HDFC_statement, GSTR3B_Apr24, Audited_FS_FY25, ITR_Ramesh_AY25); the rest are recognised from their contents.",
        required: true, count: f.files.length, type: "zip", accept: ".zip", accept_text: "ZIP file", multiple: false });
      f.zip_skipped.forEach((m) => (h += `<p class="muted">⚠️ Skipped ${esc(m)}</p>`));
    } else {
      h += `<div class="grid g2">${M.checklist.map((c) => uploader("docs", { icon: FIN_ICONS[c.doc_type], title: c.title, ask: c.ask, required: c.required, count: f.counts[c.doc_type], type: c.doc_type })).join("")}</div>`;
    }
    if (f.coverage.length) {
      h += section("Checklist status", "What has arrived against what a credit appraisal needs.");
      h += `<div class="card tight">${f.coverage.map((r) => `<div class="cl"><div class="n">${esc(r.document)}</div><div>${pill(r.status, r.label)}</div><div class="muted">${esc(r.detail)}</div></div>`).join("")}</div>`;
    }
    if (f.files.length) {
      h += section("Uploaded files", "Change a type if a file landed in the wrong place.") + fileTable("docs", f.files, { types: M.fin_types });
      h += readButton("docs", f.last_run, (f.missing.length ? `Still missing: <b>${esc(f.missing.join(", "))}</b>. ` : "")
        + "Digital PDFs, Excel and CSV are read on this server in seconds, no API key needed." + (S.has_key ? "" : " Scanned images need an API key."), false);
    } else if (S.demo) h += note("info", "Demo company loaded (fictional data). Open <b>Review</b> and <b>Analysis</b>.");
  } else if (ui.finTab === "review") h += viewReview();
  else h += viewAnalysis();
  return h;
}

function viewReview() {
  const r = S.fin.review;
  if (!r) return note("info", "Nothing read yet. Upload documents and click <b>Read</b>.");
  if (!ui.edits) ui.edits = JSON.parse(JSON.stringify({ financials: r.financials, bank: r.bank, gst: r.gst, itr: r.itr }));
  const e = ui.edits;
  let h = `<div class="card"><b>Check before you rely on it.</b> <span class="muted">Each value shows the document (doc_id) and page it came from. Edit any cell, then
    <b>Apply changes</b> — KPIs recalculate from your numbers. Blank means not in the documents.</span>
    <div class="spacer"></div><div class="hstack"><button class="btn primary" data-action="apply-tables">Apply changes</button>
    <button class="btn" data-action="discard-tables">Discard edits</button></div></div>`;
  if (r.conflicts.length) h += note("warn", "<b>Conflicting figures across files:</b><br>" + r.conflicts.map(esc).join("<br>"));
  if (e.financials) {
    const fin = e.financials;
    h += section("Financial statements", "Amounts in ₹ (absolute). Rows are line items, columns are years.");
    h += `<div class="tbl-wrap scroll-y" style="max-height:560px"><table class="tbl edit"><thead><tr><th>Line item</th>${fin.years.map((y) => `<th>${esc(y)}</th>`).join("")}</tr></thead><tbody>
      ${fin.fields.map((fld) => `<tr><td class="small"><b>${esc(fld.replace(/_/g, " "))}</b></td>${fin.years.map((y, j) => {
        const v = fin.values[fld][j];
        const isText = ["doc_id", "source_pages", "audited"].includes(fld) || (v !== null && typeof v !== "number");
        return `<td style="min-width:140px">${isText ? `<span class="muted small">${esc(Array.isArray(v) ? v.join(", ") : v)}</span>`
          : `<input type="number" step="any" data-cell="financials|${esc(fld)}|${j}" value="${esc(v ?? "")}" aria-label="${esc(fld)} ${esc(y)}">`}</td>`;
      }).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  for (const [key, title] of [["bank", "Bank statements — monthly"], ["gst", "GST returns"], ["itr", "Director / promoter ITRs"]]) {
    const t = e[key];
    if (!t) continue;
    h += section(title) + `<div class="tbl-wrap scroll-y"><table class="tbl edit"><thead><tr>${t.columns.map((c) => `<th>${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead>
      <tbody>${t.rows.map((row, i) => `<tr>${t.columns.map((c) => {
        const v = row[c];
        return typeof v === "number" || v === null ? `<td style="min-width:120px"><input type="number" step="any" data-cell="${key}|${esc(c)}|${i}" value="${esc(v ?? "")}" aria-label="${esc(c)}"></td>`
          : `<td class="small muted">${esc(Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v)}</td>`;
      }).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  r.notes.forEach((n, i) => (h += fold("notes" + i, `Extractor notes · ${esc(n.title)}`, `<ul>${n.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`, false)));
  return h;
}

function viewAnalysis() {
  const a = S.fin.analysis;
  if (!a) return note("info", "Nothing to analyse yet.");
  let h = section("Scorecard", "Worst finding per area — where to look, not a credit decision.");
  h += `<div class="scores">${a.scorecard.map((s) => `<div class="score" style="border-top-color:${SEV_COLOR[s.status]}"><div class="p">${esc(s.pillar)}</div>
    ${pill(s.status, { good: "Clear", warning: "Watch", critical: "Critical", info: "Note" }[s.status])}<div class="c">${s.count ? `${s.count} flag${s.count === 1 ? "" : "s"}` : "No flags"}</div></div>`).join("")}</div>`;
  if (a.tiles.length) h += section(`Headline KPIs · ${a.tiles_title}`, `Amounts in ${esc(S.unit)}. Badges compare with the benchmarks in the sidebar.`) + tiles(a.tiles);
  const top = a.flags.filter((f) => f.severity !== "Low");
  h += section("Key red flags", `${a.flags.length} finding(s) in total — all under Risk flags below.`);
  h += top.length ? flags(top.slice(0, 6)) : note("ok", "No high or medium findings against the current benchmarks.");
  h += `<div class="spacer"></div>` + tabs("anaTab", ui.anaTab, [["fin", "Financials"], ["bank", "Banking"], ["gst", "GST"], ["itr", "Promoters"], ["flags", `Risk flags (${a.flags.length})`]]);
  if (ui.anaTab === "fin") h += a.fin_table ? chartBox("c-fin") + tbl(a.fin_table) : note("info", "No financial statements read.");
  if (ui.anaTab === "bank") h += a.bank_table ? chartBox("c-bank") + tbl(a.bank_table) : note("info", "No bank statements read.");
  if (ui.anaTab === "gst") h += a.gst_table ? tbl(a.gst_table) : note("info", "No GST returns read.");
  if (ui.anaTab === "itr") h += a.itr_table ? tbl(a.itr_table) : note("info", "No director ITRs uploaded (optional).");
  if (ui.anaTab === "flags") h += flags(a.flags);
  return h;
}

// ---------------------------------------------------------------- step 5: repayment

function viewRepayment() {
  const r = S.repay;
  if (!r) return note("info", "Add the facilities requested in step 1 first.");
  let h = r.has_fin ? "" : note("warn", "No financial statements read yet (step 4). Repayment capacity, turnover / MPBF eligibility and DSCR need them; you can still set the repayment structure.");
  h += tabs("repTab", ui.repTab, [["cap", "How they will repay"], ["structure", "Repayment structure requested"], ["elig", "Eligibility"]]);
  if (ui.repTab === "cap") {
    const c = r.capacity;
    if (!c) return h + note("info", "Needs the financial statements from step 4.");
    h += `<div class="card"><div class="muted">Repayment capacity · based on ${esc(c.fy)} financials, bank statements, GST and the structure requested</div>
      <div style="margin:8px 0 4px">${pill(c.verdict.status, c.verdict.label)}</div><div>${esc(c.verdict.meaning)}</div></div>`;
    h += section("Observations", "Written from the numbers below. They go into the credit note.");
    h += c.notes.map((n) => `<div class="card"><div style="margin-bottom:6px">${pill(n.status || "good", n.title)}</div><div>${esc(n.text)}</div></div>`).join("");
    h += section("Key measures") + tbl(c.metrics);
    h += section("Stress test", "Minimum and average DSCR on the proposal if conditions worsen. Revenue falls hit cash through the contribution margin (revenue less materials), after tax.") + tbl(c.stress);
    h += `<p class="muted">Change growth, CC/OD utilisation and tax rate in the “Repayment structure requested” tab.</p>`;
  } else if (ui.repTab === "structure") {
    h += section("Repayment requested", "How the client wants to repay each facility.");
    r.structure.forEach((s) => {
      const i = S.case.facilities.findIndex((f) => f.id === s.id);
      let body = `<div class="hstack between"><b>${esc(s.product)} — ${esc(s.amount)}${s.tenor ? ` · ${s.tenor} months · ${s.rate}%` : ""}</b></div>`;
      if (!s.amortising) { h += `<div class="card">${body}<p class="muted" style="margin-bottom:0">${esc(s.note)}</p></div>`; return; }
      const cfg = s.cfg, base = `repay.${s.id}`;
      body += `<div class="spacer"></div><div class="grid g3">${field("Repayment method", select(`${base}.method`, cfg.method, Object.keys(M.repay_methods)), M.repay_methods[cfg.method])}
        ${field("Frequency", select(`${base}.freq`, cfg.freq, M.frequencies))}
        ${field("Moratorium (months)", input(`facilities.${i}.moratorium_months`, S.case.facilities[i].moratorium_months || 0, "num", `min="0" max="${s.tenor || 0}" step="1"`))}</div>`;
      if (cfg.method.startsWith("Step-up")) body += `<div class="spacer"></div>` + field("Step-up per year (%)", input(`${base}.step_up_pct`, cfg.step_up_pct, "num", 'min="0" max="100"'));
      if (cfg.method.startsWith("Structured")) {
        const total = String(cfg.structured).split(",").map(Number).filter((x) => !isNaN(x)).reduce((a, b) => a + b, 0);
        body += `<div class="spacer"></div>` + field("Principal % per year (comma separated, should total 100)", input(`${base}.structured`, cfg.structured));
        if (Math.abs(total - 100) > 0.5) body += note("warn", `These add up to ${total.toFixed(0)}% — the balance is collected in the last instalment.`);
      }
      if (!s.schedule) body += `<p class="muted">Set amount and tenor in step 1 to see the schedule.</p>`;
      else body += `<div class="spacer"></div>${tiles(s.schedule.tiles)}<div class="spacer"></div>${chartBox("c-sch-" + s.id)}
        ${fold("sch" + s.id, "Full schedule", `<div class="scroll-y">${tbl(s.schedule.table)}</div>`, false)}`;
      h += `<div class="card">${body}</div>`;
    });
    const d = S.case.dscr;
    h += section("Debt service on the proposal", "DSCR = (PAT + depreciation + interest) ÷ (interest + principal), year by year, with the new facilities added on top of existing debt.");
    h += `<div class="card"><div class="grid g3">${field("Cash-accrual growth per year (%)", input("dscr.growth_pct", d.growth_pct, "num", 'min="-50" max="100"'))}
      ${field("Average utilisation of new CC/OD (%)", input("dscr.wc_utilisation_pct", d.wc_utilisation_pct, "num", 'min="0" max="100"'))}
      ${field("Tax rate (%)", input("dscr.tax_pct", d.tax_pct, "num", 'min="0" max="50"'))}</div></div>`;
    if (!r.dscr) h += note("info", "Needs the financial statements from step 4.");
    else {
      h += `<p class="muted">${esc(r.dscr.base)}</p>`;
      if (r.dscr.table) h += tiles(r.dscr.tiles) + `<div class="spacer"></div>` + chartBox("c-dscr") + tbl(r.dscr.table);
    }
  } else {
    h += section("Eligibility", "Requested amount against the standard assessment method for each facility.");
    r.eligibility.forEach((e) => {
      const badge = e.eligible ? pill(e.ok ? "good" : "critical", `Eligible ${e.eligible} · ${e.ok ? "within" : "above"} limit`) : pill("neutral", "Needs more inputs");
      h += `<div class="card"><div class="hstack between"><div><b>${esc(e.product)}</b> · requested <b>${esc(e.requested)}</b><div class="muted">${esc(e.category)} · ${esc(e.method)}</div></div>${badge}</div>
        ${e.workings.length ? `<ul class="muted small" style="margin:10px 0 0">${e.workings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}</div>`;
    });
  }
  return h;
}

// ---------------------------------------------------------------- step 6: summary

function viewSummary() {
  const s = S.summary, b = S.case.borrower, rep = S.case.report;
  let h = `<div class="card"><div style="font-family:var(--display);font-size:1.15rem;font-weight:800">${esc(b.name || "Unnamed borrower")}</div>
    <div class="muted">${esc(b.constitution)} · ${esc(b.industry || "industry not set")} · MSME: ${esc(b.msme_class)} · ${b.years_in_business} yrs in business</div>
    <div class="hstack" style="margin-top:10px">${s.steps.map((x) => pill(x.status, x.name)).join(" ")}</div></div>`;
  h += section("Key numbers") + tiles(s.tiles);
  if (s.capacity) h += section("Repayment capacity") + `<div class="card">${pill(s.capacity.verdict.status, s.capacity.verdict.label)} ${esc(s.capacity.verdict.meaning)}
    <ul style="margin:10px 0 0">${s.capacity.notes.map((n) => `<li><b>${esc(n.title)}.</b> ${esc(n.text)}</li>`).join("")}</ul></div>`;
  h += section("Proposal") + (s.proposal.rows.length ? tbl(s.proposal) : note("info", "No facilities yet."));
  h += section("All findings", "Everything open across loan request, security, KYC, financials and repayment.");
  h += `<div class="seg" data-sev>${["High", "Medium", "Low"].map((x) => `<button type="button" data-val="${x}" class="${ui.sev.includes(x) ? "on" : ""}">${x}</button>`).join("")}</div><div class="spacer"></div>`;
  h += flags(s.flags.filter((f) => ui.sev.includes(f.severity)));
  h += section("Report for the bank", "One PDF: company name, company KYC, director KYC, financials (last 2 years), GST (last 12 months), proposed plan, security and repayment, then supporting analysis and the financial and security documents as annexures. The CAM-Lite is internal and separate.");
  h += fold("rep-details", "Report details — bank, preparer, recommendation", `<div class="grid g2">${REPORT_FIELDS.map(([k, l, ph]) => field(l, input(`report.${k}`, rep[k], "text", `placeholder="${esc(ph)}"`))).join("")}</div>
    <div class="spacer"></div>${field("Recommendation / remarks for the bank", input("report.recommendation", rep.recommendation, "textarea", 'placeholder="e.g. Recommended for ₹10 Cr fund-based and ₹2.5 Cr non-fund limits, subject to…"'))}`, !rep.bank);
  h += fold("cam-details", "Internal CAM details — not sent to the lender", `<div class="grid g2">${CAM_FIELDS.map(([k, l]) => field(l, input(`report.${k}`, rep[k]))).join("")}</div>`, false);
  h += `<div class="card"><div class="grid g4">
    <a class="btn primary" href="/api/export/report.pdf" data-dl>⬇ Credit report (PDF)</a>
    <a class="btn" href="/api/export/credit_file.zip" data-dl title="PDF report + Excel credit note + ${s.kyc_files} KYC document(s), plus the financial and security documents">⬇ Credit file (.zip)</a>
    <a class="btn" href="/api/export/credit_note.xlsx" data-dl>⬇ Credit note (Excel)</a>
    <a class="btn" href="/api/export/case.json" data-dl title="Everything entered and extracted (not the original files)">⬇ Save case file (.json)</a></div>
    <div class="spacer"></div><a class="btn wide" href="/api/export/cam.pdf" data-dl title="Internal deal assessment, prepared before lender login. Not in the bank report or the ZIP.">⬇ CAM-Lite (internal PDF)</a>
    <p class="muted" style="margin-bottom:0">KYC documents are attached as received. They are not verified by this tool. Excel, Word and other non-PDF files are listed in the PDF and included in the ZIP in their original format.</p></div>`;
  return h;
}

// ---------------------------------------------------------------- after render: charts and drop zones

function afterRender() {
  if (ui.step === 3 && ui.finTab === "analysis" && S.fin.analysis) {
    if (ui.anaTab === "fin") drawChart("c-fin", S.fin.analysis.fin_chart, "bar", { y: S.unit });
    if (ui.anaTab === "bank") drawChart("c-bank", S.fin.analysis.bank_chart, "line", { y: S.unit });
  }
  if (ui.step === 4 && S.repay && ui.repTab === "structure") {
    S.repay.structure.forEach((s) => s.schedule && drawChart("c-sch-" + s.id, s.schedule.chart, "bar", { stack: true, y: S.unit }));
    if (S.repay.dscr && S.repay.dscr.chart) drawChart("c-dscr", S.repay.dscr.chart, "line", { rule: S.repay.dscr.chart.rule, y: "DSCR (x)" });
  }
  document.querySelectorAll("[data-drop]").forEach((z) => {
    const inp = z.querySelector("input");
    inp.onchange = () => { upload(z.dataset.drop, z.dataset.type, inp.files); inp.value = ""; };
    z.ondragover = (e) => { e.preventDefault(); z.classList.add("over"); };
    z.ondragleave = () => z.classList.remove("over");
    z.ondrop = (e) => { e.preventDefault(); z.classList.remove("over"); upload(z.dataset.drop, z.dataset.type, e.dataTransfer.files); };
  });
  const oc = document.querySelector("[data-open-case] input");
  if (oc) oc.onchange = async () => {
    if (!oc.files.length) return;
    const fd = new FormData(); fd.append("file", oc.files[0]); oc.value = "";
    ui.step = 0; ui.edits = null;
    if (await act("POST", "/api/open", fd, "Opening the case…")) toast("Case opened");
  };
}

// ---------------------------------------------------------------- events

function coerce(el) {
  if (el.type === "checkbox") return el.checked;
  if (el.dataset.kind === "num") return el.value === "" ? null : Number(el.value);
  return el.value;
}
document.addEventListener("change", async (e) => {
  const el = e.target;
  if (el.dataset.bind) {
    let v = coerce(el);
    if (v === null && /(amount|value|pct|months|exposure|years_in_business|expenses|ytd)/.test(el.dataset.bind) && !/client\.(promoters|obligations|customers|suppliers|receivables)/.test(el.dataset.bind)) v = 0;
    setPath(S.case, el.dataset.bind, v);
    if (el.dataset.bind === "borrower.constitution" || el.tagName === "SELECT" || el.type === "checkbox" || el.type === "date") return saveCase(true);
    return saveCase();
  }
  if (el.dataset.ui) {
    ui[el.dataset.ui] = el.value;
    if (el.dataset.ui !== "kycPerson") render();
    return;
  }
  if (el.dataset.file) {
    const [store, id, key] = el.dataset.file.split("|");
    return act("PATCH", `/api/files/${store}/${id}`, { [key]: el.value });
  }
  if (el.dataset.cell) {
    const [key, col, idx] = el.dataset.cell.split("|");
    const v = el.value === "" ? null : Number(el.value);
    if (key === "financials") ui.edits.financials.values[col][Number(idx)] = v;
    else ui.edits[key].rows[Number(idx)][col] = v;
    return;
  }
  if (el.dataset.threshold) {
    if (el.value === "" || Number(el.value) < 0) { toast("Benchmarks must be zero or more", true); return render(); }
    return act("PUT", "/api/settings", { thresholds: { [el.dataset.threshold]: Number(el.value) } });
  }
  if ("apikey" in el.dataset) {
    if (await act("PUT", "/api/settings", { api_key: el.value })) toast(el.value ? "API key set for this session" : "API key cleared");
  }
});
document.addEventListener("input", (e) => { if (e.target.dataset.ui === "kycPerson") ui.kycPerson = e.target.value; });
document.addEventListener("toggle", (e) => { if (e.target.dataset && e.target.dataset.fold) ui.open[e.target.dataset.fold] = e.target.open; }, true);

document.addEventListener("click", async (e) => {
  const el = e.target.closest("button, a");
  if (!el) return;
  const d = el.dataset;
  if (d.step !== undefined) { ui.step = Number(d.step); render(); window.scrollTo({ top: 0, behavior: "smooth" }); $("#sidebar").classList.remove("open"); return; }
  const segEl = el.closest("[data-seg]");
  if (segEl && d.val !== undefined) {
    const name = segEl.dataset.seg;
    if (name === "unit") { ui.unit = d.val; localSet("unit", d.val); return act("GET", "/api/state"); }
    if (name === "upMode") ui.upMode = d.val.startsWith("One") ? "zip" : "section";
    else ui[name] = d.val;
    return render();
  }
  if (el.closest("[data-sev]") && d.val) { ui.sev = ui.sev.includes(d.val) ? ui.sev.filter((x) => x !== d.val) : [...ui.sev, d.val]; return render(); }
  const tabEl = el.closest("[data-tabs]");
  if (tabEl && d.val) { ui[tabEl.dataset.tabs] = d.val; return render(); }
  if (d.action === "demo") { ui.step = 0; ui.edits = null; if (await act("POST", "/api/demo", undefined, "Loading the demo…")) toast("Demo company loaded"); return; }
  if (d.action === "reset") { if (!confirm("Start a new case? Unsaved work in this case will be cleared.")) return; ui.step = 0; ui.edits = null; return act("POST", "/api/reset"); }
  if (d.action === "add-facility") {
    const spec = M.catalogue[ui.newCat][ui.newGrp][ui.newProd], fac = { id: uid(), product: ui.newProd };
    spec.fields.forEach((k) => (fac[M.fields[k].key] = M.fields[k].default));
    if (ui.newCat === "Non-fund based") { fac.rate_pct = 1.5; if ("tenor_months" in fac) fac.tenor_months = 12; }
    S.case.facilities.push(fac); ui.open["fac" + fac.id] = true;
    return saveCase(true);
  }
  if (d.removeFac !== undefined) { const f = S.case.facilities.splice(Number(d.removeFac), 1)[0]; delete S.case.repay[f.id]; return saveCase(true); }
  if (d.action === "add-security") {
    const t = M.security_types[ui.newSec], s = { id: uid(), kind: ui.newSec, description: "", owner: "", owner_is_third_party: false, value: 0,
      valuation_date: null, valuer: "", existing_charge: "", margin_pct: Number(t.margin) };
    S.case.securities.push(s); ui.open["sec" + s.id] = true;
    return saveCase(true);
  }
  if (d.removeSec !== undefined) { S.case.securities.splice(Number(d.removeSec), 1); return saveCase(true); }
  if (d.suggest) { const g = S.security.suggestions.find((x) => x.doc_id === d.suggest); S.case.securities.push(g.security); return saveCase(true); }
  if (d.addRow) { (S.case.client[d.addRow] = S.case.client[d.addRow] || []).push({}); return render(); }
  if (d.delRow) { const [name, i] = d.delRow.split("|"); S.case.client[name].splice(Number(i), 1); return saveCase(true); }
  if (d.remove) { const [store, id] = d.remove.split("|"); ui.edits = null; return act("DELETE", `/api/files/${store}/${id}`); }
  if (d.unlock) {
    const [store, id] = d.unlock.split("|"), pw = document.querySelector(`[data-pw="${CSS.escape(d.unlock)}"]`).value;
    if (await act("POST", `/api/files/${store}/${id}/unlock`, { password: pw }, "Unlocking…")) toast("Unlocked");
    return;
  }
  if (d.read) { ui.edits = null; return act("POST", `/api/read/${d.read}`, undefined, "Reading documents… this can take a minute for scanned files"); }
  if (d.action === "apply-tables") {
    const e2 = ui.edits, body = {};
    if (e2.financials) body.financials = e2.financials.years.map((y, j) => Object.fromEntries([["fiscal_year", y], ...e2.financials.fields.map((f) => [f, e2.financials.values[f][j]])]));
    for (const k of ["bank", "gst", "itr"]) if (e2[k]) body[k] = e2[k].rows;
    if (await act("PUT", "/api/tables", body, "Recalculating…")) { ui.edits = null; render(); toast("Changes applied — KPIs recalculated"); }
    return;
  }
  if (d.action === "discard-tables") { ui.edits = null; return render(); }
  if (d.dl !== undefined) {
    e.preventDefault();
    clearTimeout(saveTimer);
    await act("PUT", "/api/case", S.case);
    busy(true, "Preparing the file…");
    try {
      const r = await fetchWithTimeout(el.getAttribute("href"), {}, SLOW_TIMEOUT_MS);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
      const cd = r.headers.get("Content-Disposition") || "";
      const utf = cd.match(/filename\*=UTF-8''([^;]+)/);
      const name = utf ? decodeURIComponent(utf[1]) : cd.match(/filename="([^"]+)"/)?.[1] || "download";
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) { toast("Could not build the file: " + err.message, true); } finally { busy(false); }
  }
});
$("#menu-btn").addEventListener("click", (e) => { e.stopPropagation(); $("#sidebar").classList.toggle("open"); });
// on a phone the menu slides over the page: close it after an action in it, or a tap outside it
document.addEventListener("click", (e) => {
  const sb = $("#sidebar");
  if (sb.classList.contains("open") && (!sb.contains(e.target) || e.target.closest("[data-action]"))) sb.classList.remove("open");
});

// ---------------------------------------------------------------- start

function showScreen(id) { for (const s of ["boot", "login", "app"]) $("#" + s).hidden = s !== id; }
function showLogin() { showScreen("login"); setTimeout(() => $("#login-form input[name=password]").focus(), 0); }
function bootError(msg) {
  showScreen("boot");
  $("#boot-text").textContent = msg;
  $("#boot-spinner").hidden = true;
  $("#boot-retry").hidden = false;
}
$("#boot-retry").addEventListener("click", () => start());
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#login-form button[type=submit]"), err = $("#login-error");
  err.textContent = "";
  btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const r = await fetchWithTimeout("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: e.target.password.value }) }, 30000);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { err.textContent = data.error || "Could not sign in"; return; }
    e.target.password.value = "";
    start();
  } catch (ex) { err.textContent = ex.message; }
  finally { btn.disabled = false; btn.textContent = "Sign in"; }
});
async function start() {
  showScreen("boot");
  $("#boot-text").textContent = "Loading the workbench…";
  $("#boot-spinner").hidden = false;
  $("#boot-retry").hidden = true;
  try {
    const r = await fetchWithTimeout("/api/session", {}, 30000);
    if (!r.ok) throw new Error(`The server answered ${r.status}. Try again in a moment.`);
    const sess = await r.json();
    if (sess.auth_required) return showLogin();
    [M, S] = await Promise.all([api("GET", "/api/meta"), api("GET", "/api/state")]);
    showScreen("app");
    render();
  } catch (e) { bootError(e.message); }
}
start();
