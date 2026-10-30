"""Visual layer: theme CSS and small HTML components rendered with st.markdown."""

from html import escape

import streamlit as st

# Chart palette (validated categorical slots 1-3) and reserved status colours.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
STATUS = {
    "good": ("#0ca30c", "✓", "Clear"),
    "info": ("#2a78d6", "i", "Note"),
    "warning": ("#c98500", "!", "Watch"),
    "critical": ("#d03b3b", "✕", "Critical"),
}
SEVERITY = {"High": "critical", "Medium": "warning", "Low": "info"}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {
  --ink: #101828; --ink-2: #475467; --ink-3: #667085; --line: #e4e7ec; --line-2: #eef0f3;
  --bg: #f6f7f9; --card: #ffffff; --navy: #0b1f3a; --accent: #2a78d6; --accent-soft: #eaf2fc;
  --good: #0ca30c; --warn: #c98500; --crit: #d03b3b;
}
.stMarkdown, .stButton button, .stDownloadButton button, [data-baseweb="tab"], label, input, textarea,
[data-testid="stSidebar"], [data-testid="stCaptionContainer"] {
  font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
}
[data-testid="stAppViewContainer"] { background: var(--bg); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"], [data-testid="stDecoration"] { display: none; }
.block-container { padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1240px; }
[data-testid="stSidebar"] { background: #fff; border-right: 1px solid var(--line); }
[data-testid="stSidebar"] .block-container { padding-top: 1.5rem; }
h1, h2, h3, h4 { color: var(--ink); letter-spacing: -0.01em; }

/* header */
.app-hero { background: linear-gradient(120deg, #0b1f3a 0%, #13325e 100%); border-radius: 16px;
  padding: 22px 28px; color: #fff; display: flex; align-items: center; justify-content: space-between;
  gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
.app-hero .brand { display: flex; align-items: center; gap: 14px; }
.app-hero .mark { width: 42px; height: 42px; border-radius: 10px; background: rgba(255,255,255,.12);
  display: grid; place-items: center; font-weight: 700; font-size: 18px; }
.app-hero h1 { color: #fff; font-size: 1.35rem; margin: 0; padding: 0; font-weight: 650; }
.app-hero p { color: #c7d2e3; margin: 2px 0 0; font-size: .88rem; }
.app-hero .chip { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.18);
  padding: 6px 12px; border-radius: 999px; font-size: .82rem; color: #e6ecf5; }

/* tabs as a stepper */
.stTabs [data-baseweb="tab-list"] { gap: 6px; background: #fff; padding: 6px; border-radius: 12px;
  border: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] { height: 40px; padding: 0 18px; border-radius: 8px; color: var(--ink-2);
  font-weight: 550; }
.stTabs [aria-selected="true"] { background: var(--accent-soft); color: var(--accent) !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 18px; }

/* cards */
/* bordered st.container()s that hold a .card-marker (innermost one only) become white cards */
[data-testid="stVerticalBlockBorderWrapper"]:has(.card-marker):not(:has([data-testid="stVerticalBlockBorderWrapper"] .card-marker)) {
  background: var(--card); border-radius: 14px; border-color: var(--line) !important;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);
}
.card-marker { display: none; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; }
.section-title { font-size: 1.02rem; font-weight: 650; color: var(--ink); margin: 22px 0 10px; }
.section-sub { color: var(--ink-3); font-size: .86rem; margin: -6px 0 12px; }
.muted { color: var(--ink-3); font-size: .84rem; }

/* upload cards */
.up-head { display: flex; gap: 12px; align-items: flex-start; }
.up-icon { width: 36px; height: 36px; border-radius: 9px; background: var(--accent-soft); color: var(--accent);
  display: grid; place-items: center; font-size: 17px; flex: none; }
.up-title { font-weight: 620; color: var(--ink); font-size: .95rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.up-ask { color: var(--ink-3); font-size: .82rem; line-height: 1.4; margin-top: 2px; }
[data-testid="stFileUploaderDropzone"] { background: #fafbfc; border: 1px dashed #cfd5dd; border-radius: 10px;
  padding: 10px 14px; }
[data-testid="stFileUploaderDropzoneInstructions"] small { font-size: .72rem; }

/* pills */
.pill { display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px; border-radius: 999px;
  font-size: .74rem; font-weight: 600; line-height: 1.6; white-space: nowrap; border: 1px solid transparent; }
.pill .ic { font-size: .72rem; font-weight: 800; }
.pill-neutral { background: #f2f4f7; color: var(--ink-2); }
.pill-req { background: #fdf0e9; color: #b4501f; }

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
.kpi { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
.kpi .lbl { color: var(--ink-3); font-size: .78rem; font-weight: 550; text-transform: uppercase; letter-spacing: .03em; }
.kpi .val { color: var(--ink); font-size: 1.6rem; font-weight: 680; margin: 4px 0 2px; font-variant-numeric: tabular-nums; }
.kpi .sub { color: var(--ink-3); font-size: .78rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

/* scorecard */
.score-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1000px) { .score-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 560px) { .score-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.score { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; border-top-width: 3px; }
.score .p { font-size: .8rem; color: var(--ink-2); font-weight: 600; min-height: 2.3em; }
.score .n { font-size: .76rem; color: var(--ink-3); margin-top: 6px; }

/* flags */
.flag { background: #fff; border: 1px solid var(--line); border-left-width: 4px; border-radius: 12px;
  padding: 12px 16px; margin-bottom: 10px; }
.flag .top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.flag .area { color: var(--ink-3); font-size: .78rem; font-weight: 600; }
.flag .finding { color: var(--ink); font-weight: 600; font-size: .92rem; margin: 6px 0 4px; }
.flag .meta { color: var(--ink-3); font-size: .8rem; }
.flag .meta b { color: var(--ink-2); font-weight: 600; }

/* buttons */
.stButton > button, .stDownloadButton > button { border-radius: 10px; font-weight: 600; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: var(--accent); border-color: var(--accent); }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero(subtitle: str, chip: str | None = None, title: str = "Credit Appraisal Workbench"):
    chip_html = f'<div class="chip">{escape(chip)}</div>' if chip else ""
    st.markdown(f"""<div class="app-hero"><div class="brand"><div class="mark">CA</div>
        <div><h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div></div>{chip_html}</div>""",
                unsafe_allow_html=True)


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
