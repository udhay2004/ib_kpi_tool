"""Visual layer: theme CSS and small HTML components rendered with st.markdown."""

from html import escape

import streamlit as st

# Chart palette and status colours, from the ComplyGlobally compliance calendar's design tokens.
SERIES = ["#23847C", "#C68A2E", "#5B7FD6"]
STATUS = {
    "good": ("#1F8A6A", "✓", "Clear"),
    "info": ("#5C6E72", "i", "Note"),
    "warning": ("#C68A2E", "!", "Watch"),
    "critical": ("#D64545", "✕", "Critical"),
}
SEVERITY = {"High": "critical", "Medium": "warning", "Low": "info"}
LOGO = "https://compliance.complyglobally.com/logo.png"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap');
:root {
  --brand: #34A9A1; --brand-dark: #278A83; --brand-tint: #E3F5F3; --brand-text: #1A6F69; --btn-a: #23847C; --btn-b: #1A6F69;
  --ink: #1B2A2E; --ink-2: #3E5256; --ink-3: #5C6E72; --line: #E1E9E8; --line-2: #ECF2F1; --paper: #F5F8F8;
  --field: #F7FAFA; --card: #FFFFFF; --deep-1: #0E2B2E; --deep-2: #123F42;
  --good: #1F8A6A; --warn: #C68A2E; --crit: #D64545;
  --display: 'Plus Jakarta Sans', Inter, system-ui, sans-serif;
  --shadow-sm: 0 1px 2px rgba(27,42,46,.05), 0 2px 8px rgba(27,42,46,.04);
  --shadow-md: 0 1px 2px rgba(27,42,46,.05), 0 12px 28px -14px rgba(27,42,46,.18);
  --shadow-brand: 0 10px 30px -10px rgba(52,169,161,.5);
}
html, body, .stApp, .stMarkdown, button, label, input, textarea, [data-baseweb="select"], [data-testid="stSidebar"],
[data-testid="stCaptionContainer"] { font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
[data-testid="stAppViewContainer"], .stApp { background: var(--paper); color: var(--ink); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] { display: none; }
.block-container { padding-top: 1.4rem; padding-bottom: 4rem; max-width: 1280px; }
h1, h2, h3, h4 { color: var(--ink); font-family: var(--display); letter-spacing: -0.015em; }

/* sidebar */
[data-testid="stSidebar"] { background: #fff; border-right: 1px solid var(--line); }
.side-brand { display: flex; align-items: center; gap: 10px; padding: 0 0 14px; border-bottom: 1px solid var(--line-2);
  margin-bottom: 6px; }
.side-brand img { width: 36px; height: 36px; border-radius: 50%; }
.side-brand .t { font-family: var(--display); font-weight: 800; font-size: .95rem; color: var(--ink); line-height: 1.15; }
.side-brand .s { font-size: .74rem; color: var(--brand-text); font-weight: 600; }
.side-h { font-size: .7rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3);
  margin: 18px 0 8px; }
.side-note { font-size: .76rem; color: var(--ink-3); line-height: 1.45; margin-top: 18px; padding-top: 12px;
  border-top: 1px solid var(--line-2); }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] { padding: 8px 10px; }

/* hero */
.app-hero { position: relative; overflow: hidden; border-radius: 20px; padding: 22px 26px; color: #fff; margin-bottom: 14px;
  background: radial-gradient(120% 140% at 100% 0%, #1d5a5a 0%, rgba(29,90,90,0) 55%), linear-gradient(135deg, var(--deep-1), var(--deep-2));
  box-shadow: var(--shadow-md); display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; flex-wrap: wrap; }
.app-hero .eyebrow { display: inline-flex; align-items: center; gap: 7px; font-size: .74rem; font-weight: 600; color: #d6efec;
  border: 1px solid rgba(255,255,255,.16); background: rgba(255,255,255,.06); padding: 4px 11px; border-radius: 999px; }
.app-hero .eyebrow i { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); display: inline-block; }
.app-hero h1 { color: #fff; font-family: var(--display); font-size: 1.75rem; font-weight: 800; margin: 10px 0 4px; padding: 0;
  line-height: 1.15; }
.app-hero p { color: #b9d3d1; margin: 0; font-size: .9rem; }
.app-hero .chips { display: flex; gap: 8px; flex-wrap: wrap; }
.app-hero .chip { background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.14); padding: 7px 13px;
  border-radius: 12px; font-size: .76rem; color: #cfe6e3; }
.app-hero .chip b { display: block; font-family: var(--display); font-size: 1.02rem; color: #fff; font-weight: 800; }

/* stepper: a row of step buttons inside a white card */
.st-key-stepper { background: #fff; border: 1px solid var(--line); border-radius: 16px; padding: 8px; box-shadow: var(--shadow-sm);
  margin-bottom: 6px; }
.st-key-stepper [data-testid="stHorizontalBlock"] { gap: 6px; flex-wrap: nowrap; }
.st-key-stepper .stButton > button { width: 100%; border: 0; background: transparent; color: var(--ink-3); box-shadow: none;
  min-height: 46px; border-radius: 12px; }
.st-key-stepper .stButton > button:hover { background: var(--field); color: var(--ink); }
.st-key-stepper .stButton > button p { font-weight: 600; font-size: .87rem; white-space: nowrap; }
.st-key-stepper .stButton > button[kind="primary"] { background: var(--brand-tint); box-shadow: inset 0 0 0 1px #bfe5e1; }
.st-key-stepper .stButton > button[kind="primary"],
.st-key-stepper .stButton > button[kind="primary"] p { color: var(--brand-text) !important; font-weight: 800; }
[data-testid="stSidebarHeader"] { height: 2.2rem; margin-bottom: 0; padding-top: .6rem; padding-bottom: 0; }
[data-testid="stSidebarUserContent"] { padding-top: 0; }

/* sections and cards */
.section-title { font-family: var(--display); font-size: 1.12rem; font-weight: 800; color: var(--ink); margin: 26px 0 4px;
  letter-spacing: -0.01em; }
.section-sub { color: var(--ink-3); font-size: .87rem; margin: 0 0 12px; max-width: 820px; }
[data-testid="stVerticalBlockBorderWrapper"]:has(.card-marker):not(:has([data-testid="stVerticalBlockBorderWrapper"] .card-marker)) {
  background: var(--card); border-radius: 16px; border-color: var(--line) !important; box-shadow: var(--shadow-sm); }
.card-marker { display: none; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 16px 20px; box-shadow: var(--shadow-sm); }
.muted { color: var(--ink-3); font-size: .84rem; }

/* inputs */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  background: var(--field) !important; border-color: var(--line) !important; border-radius: 12px !important; }
[data-baseweb="input"] > div, [data-baseweb="textarea"] > div { background: transparent !important; }
[data-testid="stNumberInputContainer"] { border-radius: 12px; }
[data-testid="stNumberInputContainer"] [data-baseweb="input"] { border-top-right-radius: 0 !important; border-bottom-right-radius: 0 !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within, [data-baseweb="textarea"]:focus-within {
  border-color: var(--brand) !important; }
[data-testid="stWidgetLabel"] p { font-size: .8rem; font-weight: 600; color: var(--ink); }

/* buttons */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  border-radius: 12px; font-weight: 600; border: 1px solid var(--line); background: #fff; color: var(--ink-2); min-height: 42px; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color: var(--brand); color: var(--brand-text); }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"] {
  background: linear-gradient(135deg, var(--btn-a), var(--btn-b)); border: 0; color: #fff; box-shadow: var(--shadow-brand); }
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover { color: #fff; filter: brightness(1.06); }
[data-testid="stButtonGroup"] button { border-radius: 10px; }
[data-testid="stButtonGroup"] button[kind="segmented_controlActive"] { background: var(--brand-tint); color: var(--brand-text);
  border-color: var(--brand); }

/* expanders, tabs, tables, uploads */
[data-testid="stExpander"] details { background: #fff; border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow-sm); }
[data-testid="stExpander"] summary { font-weight: 600; color: var(--ink); }
[data-testid="stExpander"] summary:hover { color: var(--brand-text); }
[data-testid="stSidebar"] [data-testid="stExpander"] details { box-shadow: none; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; background: #fff; padding: 6px; border-radius: 14px; border: 1px solid var(--line);
  box-shadow: var(--shadow-sm); }
.stTabs [data-baseweb="tab"] { height: 38px; padding: 0 16px; border-radius: 10px; color: var(--ink-3); font-weight: 600; }
.stTabs [aria-selected="true"] { background: var(--brand-tint); color: var(--brand-text) !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 16px; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
[data-testid="stFileUploaderDropzone"] { background: var(--field); border: 1.5px dashed #c9dad8; border-radius: 12px; padding: 10px 14px; }
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--brand); background: var(--brand-tint); }
[data-testid="stFileUploaderDropzoneInstructions"] small { font-size: .72rem; }
[data-testid="stAlert"] { border-radius: 12px; }
hr { border-color: var(--line-2); }

/* upload cards */
.up-head { display: flex; gap: 12px; align-items: flex-start; }
.up-icon { width: 38px; height: 38px; border-radius: 11px; background: var(--brand-tint); color: var(--brand-text);
  display: grid; place-items: center; font-size: 17px; flex: none; }
.up-title { font-weight: 700; color: var(--ink); font-size: .95rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.up-ask { color: var(--ink-3); font-size: .82rem; line-height: 1.45; margin-top: 2px; }

/* pills */
.pill { display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px; border-radius: 999px;
  font-size: .74rem; font-weight: 600; line-height: 1.6; white-space: nowrap; border: 1px solid transparent; }
.pill .ic { font-size: .72rem; font-weight: 800; }
.pill-neutral { background: #F1F4F3; color: var(--ink-2); }
.pill-req { background: #FBF1E1; color: #85560A; }

/* checklist */
.cl-row { display: grid; grid-template-columns: 1.3fr .7fr 2.4fr; gap: 12px; padding: 11px 4px;
  border-bottom: 1px solid var(--line-2); align-items: center; font-size: .87rem; }
.cl-row:last-child { border-bottom: none; }
.cl-name { font-weight: 600; color: var(--ink); }
.cl-detail { color: var(--ink-2); }
@media (max-width: 720px) { .cl-row { grid-template-columns: 1fr; gap: 4px; } }

/* KPI tiles */
.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 900px) { .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.kpi { background: #fff; border: 1px solid var(--line); border-radius: 16px; padding: 15px 17px; box-shadow: var(--shadow-sm); }
.kpi .lbl { color: var(--ink-3); font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }
.kpi .val { color: var(--ink); font-family: var(--display); font-size: 1.55rem; font-weight: 800; margin: 5px 0 4px;
  font-variant-numeric: tabular-nums; }
.kpi .sub { color: var(--ink-3); font-size: .78rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

/* scorecard */
.score-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1000px) { .score-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 560px) { .score-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.score { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; border-top-width: 3px; }
.score .p { font-size: .8rem; color: var(--ink-2); font-weight: 600; min-height: 2.3em; }
.score .n { font-size: .76rem; color: var(--ink-3); margin-top: 6px; }

/* flags */
.flag { background: #fff; border: 1px solid var(--line); border-left-width: 4px; border-radius: 14px;
  padding: 12px 16px; margin-bottom: 10px; box-shadow: var(--shadow-sm); }
.flag .top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.flag .area { color: var(--ink-3); font-size: .78rem; font-weight: 600; }
.flag .finding { color: var(--ink); font-weight: 600; font-size: .92rem; margin: 6px 0 4px; }
.flag .meta { color: var(--ink-3); font-size: .8rem; }
.flag .meta b { color: var(--ink-2); font-weight: 600; }
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str, chips: list | None = None, eyebrow: str = "Credit Appraisal Workbench"):
    """Dark teal banner: eyebrow, title and subtitle, with (label, value) chips on the right."""
    chip_html = "".join(f'<div class="chip">{escape(k)}<b>{escape(v)}</b></div>' for k, v in chips or [])
    st.markdown(f'''<div class="app-hero"><div><span class="eyebrow"><i></i>{escape(eyebrow)}</span>
        <h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div><div class="chips">{chip_html}</div></div>''',
                unsafe_allow_html=True)


def side_brand():
    st.markdown(f'<div class="side-brand"><img src="{LOGO}" alt=""><div><div class="t">Credit Appraisal Workbench</div>'
                f'<div class="s">by ComplyGlobally</div></div></div>', unsafe_allow_html=True)


def side_heading(text: str):
    st.markdown(f'<div class="side-h">{escape(text)}</div>', unsafe_allow_html=True)


def pill(status: str, text: str | None = None) -> str:
    color, icon, label = STATUS[status]
    return (f'<span class="pill" style="background:{color}14;color:{color};border-color:{color}33">'
            f'<span class="ic">{icon}</span>{escape(text or label)}</span>')


def neutral_pill(text: str, cls: str = "pill-neutral") -> str:
    return f'<span class="pill {cls}">{escape(text)}</span>'


def section(title: str, sub: str | None = None):
    st.markdown(f'<div class="section-title">{escape(title)}</div>'
                + (f'<div class="section-sub">{escape(sub)}</div>' if sub else ""), unsafe_allow_html=True)


def upload_header(icon: str, title: str, ask: str, required: bool, count: int):
    tag = neutral_pill("Required", "pill-req") if required else neutral_pill("Optional")
    got = pill("good", f"{count} file{'s' if count != 1 else ''} added") if count else ""
    st.markdown(f"""<span class="card-marker"></span><div class="up-head"><div class="up-icon">{icon}</div><div>
        <div class="up-title">{escape(title)} {tag} {got}</div>
        <div class="up-ask">{escape(ask)}</div></div></div>""", unsafe_allow_html=True)


CHECK_STATUS = {"✅": "good", "⚠️": "warning", "❌": "critical", "⏳": "info", "➖": None}


def checklist(df):
    rows = []
    for _, r in df.iterrows():
        key = next((k for k in CHECK_STATUS if r["Status"].startswith(k)), "➖")
        label = r["Status"][len(key):].strip()
        badge = pill(CHECK_STATUS[key], label) if CHECK_STATUS[key] else neutral_pill(label)
        rows.append(f'<div class="cl-row"><div class="cl-name">{escape(r["Document"])}</div>'
                    f'<div>{badge}</div><div class="cl-detail">{escape(r["Detail"])}</div></div>')
    st.markdown(f'<div class="card" style="padding:4px 16px">{"".join(rows)}</div>', unsafe_allow_html=True)


def kpi_tiles(tiles: list[dict]):
    """tiles: {label, value, sub, status (good/warning/critical/None), bench}"""
    html = []
    for t in tiles:
        badge = pill(t["status"], t["bench"]) if t.get("status") else ""
        html.append(f'<div class="kpi"><div class="lbl">{escape(t["label"])}</div>'
                    f'<div class="val">{escape(t["value"])}</div>'
                    f'<div class="sub">{badge}<span>{escape(t.get("sub") or "")}</span></div></div>')
    st.markdown(f'<div class="kpi-grid">{"".join(html)}</div>', unsafe_allow_html=True)


def scorecard(items: list[dict]):
    html = []
    for s in items:
        color = STATUS[s["status"]][0]
        n = f'{s["count"]} flag{"s" if s["count"] != 1 else ""}' if s["count"] else "No flags"
        html.append(f'<div class="score" style="border-top-color:{color}"><div class="p">{escape(s["pillar"])}</div>'
                    f'{pill(s["status"])}<div class="n">{n}</div></div>')
    st.markdown(f'<div class="score-grid">{"".join(html)}</div>', unsafe_allow_html=True)


def flag_cards(flags):
    out = []
    for _, f in flags.iterrows():
        status = SEVERITY[f["severity"]]
        color = STATUS[status][0]
        out.append(f'<div class="flag" style="border-left-color:{color}"><div class="top">'
                   f'{pill(status, f["severity"])}<span class="area">{escape(f["area"])}</span></div>'
                   f'<div class="finding">{escape(f["finding"])}</div>'
                   f'<div class="meta"><b>Evidence:</b> {escape(str(f["evidence"]))} &nbsp;·&nbsp; '
                   f'<b>Next step:</b> {escape(str(f["next_step"]))}</div></div>')
    st.markdown("".join(out), unsafe_allow_html=True)
