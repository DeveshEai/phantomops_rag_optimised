"""
ui/settings_app.py — PhantomOps Control Center (settings dashboard)

Run:  streamlit run ui/settings_app.py --server.port 8502
URL:  http://localhost:8502
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

st.set_page_config(
    page_title="PhantomOps Control Center",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Theme ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    :root {
        --bg0:#0a0c14; --bg1:#11141f; --card:#161a28; --card2:#1b2031;
        --border:#262c42; --border2:#323a58;
        --txt:#e7eaf6; --txt2:#aab2d0; --txt3:#6b7290;
        --muted:#7780a4;
        --accent:#7c8cff; --accent2:#a07bff;
        --ok:#3ddc97; --okbg:#0f2b22; --okbd:#1f5a44;
        --warn:#ff6b8b; --warnbg:#2b1019; --warnbd:#5a1f30;
        --info:#5ab0ff;
    }

    .stApp {
        background:
            radial-gradient(1200px 600px at 12% -8%, rgba(124,140,255,.10), transparent 55%),
            radial-gradient(1000px 700px at 100% 0%, rgba(160,123,255,.08), transparent 50%),
            var(--bg0);
        font-family:'Inter','Segoe UI',-apple-system,sans-serif;
    }
    .block-container { padding:2.2rem 3rem 4rem; max-width:1080px; }
    #MainMenu, footer, header { visibility:hidden; }

    h1,h2,h3,h4 { font-family:'Inter',sans-serif; color:var(--txt); letter-spacing:-.01em; }

    /* ── Brand header ── */
    .brand {
        display:flex; align-items:center; gap:14px; margin-bottom:2px;
    }
    .brand-badge {
        width:46px; height:46px; border-radius:14px;
        background:linear-gradient(135deg,var(--accent),var(--accent2));
        display:flex; align-items:center; justify-content:center;
        font-size:24px; box-shadow:0 8px 24px rgba(124,140,255,.35);
    }
    .brand-title {
        font-size:1.9rem; font-weight:800; line-height:1;
        background:linear-gradient(90deg,#fff,#b9c0e8);
        -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    }
    .brand-sub { color:var(--muted); font-size:.86rem; margin-top:3px; }

    /* ── Status chips ── */
    .chips { display:flex; gap:10px; flex-wrap:wrap; margin:18px 0 6px; }
    .chip {
        display:inline-flex; align-items:center; gap:7px;
        background:var(--card); border:1px solid var(--border);
        border-radius:999px; padding:7px 14px; font-size:.8rem; color:var(--txt2);
        font-weight:500;
    }
    .dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
    .dot-ok { background:var(--ok); box-shadow:0 0 8px var(--ok); }
    .dot-bad { background:var(--warn); box-shadow:0 0 8px var(--warn); }
    .chip b { color:var(--txt); font-weight:600; }

    /* ── Section ── */
    .sec { margin:30px 0 6px; display:flex; align-items:center; gap:10px; }
    .sec-ic {
        width:30px; height:30px; border-radius:9px; background:var(--card2);
        border:1px solid var(--border); display:flex; align-items:center;
        justify-content:center; font-size:15px;
    }
    .sec-t { font-size:1.18rem; font-weight:700; color:var(--txt); }
    .hint { color:var(--muted); font-size:.85rem; margin:2px 0 14px 40px; line-height:1.5; }

    /* ── Provider cards ── */
    .pcard {
        background:linear-gradient(180deg,var(--card),var(--bg1));
        border:1px solid var(--border); border-radius:16px;
        padding:16px 18px; margin-bottom:6px; transition:.18s ease;
        height:100%;
    }
    .pcard:hover { border-color:var(--border2); transform:translateY(-2px); }
    .pcard.active {
        border-color:var(--accent);
        box-shadow:0 0 0 1px var(--accent), 0 10px 30px rgba(124,140,255,.18);
        background:linear-gradient(180deg,#1a2036,#141828);
    }
    .pcard-top { display:flex; align-items:center; justify-content:space-between; }
    .pcard-name { font-size:1.02rem; font-weight:700; color:var(--txt); display:flex; align-items:center; gap:9px; }
    .pcard-model { font-size:.76rem; color:var(--muted); margin:7px 0 0 33px; font-family:monospace; }
    .pcard-tip { font-size:.8rem; color:var(--txt2); margin-top:10px; line-height:1.45; }

    .bdg { border-radius:999px; padding:3px 11px; font-size:.72rem; font-weight:700; letter-spacing:.02em; }
    .bdg-active { background:linear-gradient(135deg,var(--accent),var(--accent2)); color:#fff; }
    .bdg-ok { background:var(--okbg); color:var(--ok); border:1px solid var(--okbd); }
    .bdg-no { background:var(--warnbg); color:var(--warn); border:1px solid var(--warnbd); }

    /* ── Allowlist pills ── */
    .pill {
        display:inline-flex; align-items:center; gap:8px;
        background:var(--card2); border:1px solid var(--border);
        border-radius:999px; padding:8px 6px 8px 14px; color:var(--txt);
        font-size:.88rem;
    }

    /* ── Inputs / widgets ── */
    .stTextInput input, .stSelectbox div[data-baseweb="select"]>div {
        background:var(--card) !important; border:1px solid var(--border) !important;
        border-radius:10px !important; color:var(--txt) !important;
    }
    .stTextInput input:focus { border-color:var(--accent) !important; box-shadow:0 0 0 2px rgba(124,140,255,.25) !important; }

    div[data-testid="stRadio"] > div { gap:8px; flex-wrap:wrap; }
    div[data-testid="stRadio"] label {
        background:var(--card); border:1px solid var(--border); border-radius:11px;
        padding:8px 16px !important; transition:.15s; cursor:pointer; color:var(--txt2);
    }
    div[data-testid="stRadio"] label:hover { border-color:var(--border2); color:var(--txt); }

    .stButton>button {
        border-radius:11px; font-weight:600; padding:.55rem 1.4rem;
        border:1px solid var(--border); background:var(--card2); color:var(--txt);
        transition:.15s;
    }
    .stButton>button:hover { border-color:var(--accent); color:#fff; }
    .stButton>button[kind="primary"], .stButton>button[data-testid="baseButton-primary"] {
        background:linear-gradient(135deg,var(--accent),var(--accent2)); border:none; color:#fff;
        box-shadow:0 6px 18px rgba(124,140,255,.3);
    }
    .stButton>button[kind="primary"]:hover { filter:brightness(1.08); }

    hr { border-color:var(--border); margin:1.6rem 0; }
    .save-ok {
        background:var(--okbg); border:1px solid var(--okbd); border-radius:12px;
        padding:14px 18px; color:var(--ok); font-size:.9rem; margin-top:14px;
    }
    .warnbox { background:var(--warnbg); border:1px solid var(--warnbd); border-radius:12px;
        padding:13px 17px; color:var(--warn); font-size:.86rem; }
</style>
""", unsafe_allow_html=True)

# ── Password gate ─────────────────────────────────────────────────────────────
_PWD = os.getenv("DASHBOARD_PASSWORD", "sally2026")
if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.markdown("<div style='height:8vh'></div>", unsafe_allow_html=True)
    c = st.columns([1, 1.3, 1])[1]
    with c:
        st.markdown(
            "<div class='brand' style='justify-content:center'>"
            "<div class='brand-badge'>🛰️</div>"
            "<div><div class='brand-title'>PhantomOps</div>"
            "<div class='brand-sub'>Control Center</div></div></div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        pwd = st.text_input("Password", type="password",
                            placeholder="Enter dashboard password", label_visibility="collapsed")
        if st.button("Sign in →", use_container_width=True, type="primary"):
            if pwd == _PWD:
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()

# ── Imports ───────────────────────────────────────────────────────────────────
# Settings persist to agent_config.yaml (via set_agent_config_value), NOT the
# database — the agent itself only ever reads agent_config.yaml for these
# knobs (see core/config.py's get_agent_model/get_vision_provider/etc.), so a
# DB-backed settings UI would silently have zero effect on the running agent.
from core.config import (
    GROQ_API_KEY, GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    GROQ_MODEL, GEMINI_MODEL, CLAUDE_MODEL, OPENAI_MODEL,
    OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID,
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_TOKEN_PATH,
    get_enabled_models, get_effective_allowlist, get_provider_model, get_agent_name,
    get_agent_model, get_vision_provider, get_email_provider, set_agent_config_value,
    get_company_info, get_ollama_models,
)
from core.database.sqlite_db import ping as _db_ping
from skills.email_skill.outlook_client import TOKEN_PATH as OUTLOOK_TOKEN_PATH
from skills.email_skill.email_connect import (
    is_outlook_connected, is_gmail_connected, disconnect as disconnect_mailbox,
    connect_outlook, connect_gmail,
)

PROVIDERS = {
    "groq":   {"name": "Groq",   "icon": "⚡", "key": GROQ_API_KEY,      "model": GROQ_MODEL,   "tip": "Fastest, free tier. Great for quick replies. Daily token cap on free plan."},
    "gemini": {"name": "Gemini", "icon": "✨", "key": GOOGLE_API_KEY,    "model": GEMINI_MODEL, "tip": "Best reasoning & analysis. Generous free tier. Ideal for detailed reports."},
    "claude": {"name": "Claude", "icon": "🎯", "key": ANTHROPIC_API_KEY, "model": CLAUDE_MODEL, "tip": "Top-tier writing quality. Best for proposals and formal documents."},
    "openai": {"name": "OpenAI", "icon": "🔮", "key": OPENAI_API_KEY,    "model": OPENAI_MODEL, "tip": "Well-rounded with strong structured output. Solid fallback."},
}
VISION_OPTIONS = {
    "":       "Auto — try every available provider in order",
    "groq":   "Groq — qwen3.6-27b",
    "gemini": "Gemini — gemini-1.5-flash",
    "claude": "Claude — claude-3-haiku",
    "openai": "OpenAI — gpt-4o-mini",
    "local":  "Local (Ollama) — opt-in only, never an automatic fallback",
}

AVAILABLE_MODELS = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it"
    ],
    "gemini": [
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-customtools",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-live-preview",
        "gemini-3-flash"
    ],
    "claude": [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001"
    ],
    "openai": [
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "o1-mini",
        "o3-mini"
    ]
}

db_ok          = _db_ping()
current_model  = get_agent_model()
current_vision = get_vision_provider()
enabled        = get_enabled_models()
eff_allow, allow_src = get_effective_allowlist()

# ── Header + live status ──────────────────────────────────────────────────────
top_l, top_r = st.columns([5, 1])
with top_l:
    st.markdown(
        "<div class='brand'><div class='brand-badge'>🛰️</div>"
        "<div><div class='brand-title'>PhantomOps</div>"
        "<div class='brand-sub'>Control Center · settings apply on the next email</div></div></div>",
        unsafe_allow_html=True,
    )
with top_r:
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    if st.button("Sign out", use_container_width=True):
        st.session_state.auth = False
        st.rerun()

active_model_name = PROVIDERS.get(current_model, {}).get("name", "Auto (multi-model)")
st.markdown(
    f"""<div class='chips'>
      <span class='chip'><span class='dot {'dot-ok' if db_ok else 'dot-bad'}'></span>
        {'Database online' if db_ok else 'Database offline'}</span>
      <span class='chip'>🤖 Active model: <b>{active_model_name}</b></span>
      <span class='chip'>🔌 Providers: <b>{len(enabled)}</b> connected</span>
      <span class='chip'>✉️ Allowlist: <b>{len(eff_allow)}</b> ({allow_src})</span>
    </div>""",
    unsafe_allow_html=True,
)

st.markdown("<hr/>", unsafe_allow_html=True)

# ── Section 0 · Agent Identity ────────────────────────────────────────────────
st.markdown("<div class='sec'><div class='sec-ic'>👤</div><div class='sec-t'>Agent Name</div></div>", unsafe_allow_html=True)
_cur_name = get_agent_name()
_c_name, _c_save = st.columns([3, 1])
with _c_name:
    _new_name = st.text_input("Agent name", value=_cur_name, label_visibility="collapsed",
                              placeholder="Sally")
with _c_save:
    if st.button("💾 Save name", use_container_width=True):
        nm = _new_name.strip()
        if nm and nm != _cur_name:
            set_agent_config_value("agent_name", nm)
            st.success(f"Agent name set to “{nm}”.")
            st.rerun()
        elif not nm:
            st.warning("Name can't be empty.")

st.markdown("<hr/>", unsafe_allow_html=True)

# ── Section 0b · Company Branding ──────────────────────────────────────────────
st.markdown("<div class='sec'><div class='sec-ic'>🏢</div><div class='sec-t'>Company Branding</div></div>", unsafe_allow_html=True)
st.markdown("<div class='hint'>Shown in email signatures, generated documents/PPTs, and "
            "the assistant's own persona in its writing prompts. Leave as-is to keep the "
            "default branding.</div>", unsafe_allow_html=True)

_cur_company = get_company_info()
_cc1, _cc2 = st.columns(2)
with _cc1:
    _new_cname = st.text_input("Company name", value=_cur_company["name"], placeholder="EAI Systems Pvt. Ltd.")
    _new_cemail = st.text_input("Contact email", value=_cur_company["email"], placeholder="info@eaisystems.com")
with _cc2:
    _new_cshort = st.text_input("Short name (logo badge)", value=_cur_company["short"], placeholder="EAI SYSTEMS")
    _new_cweb = st.text_input("Website", value=_cur_company["web"], placeholder="www.eaisystems.com")

if st.button("💾 Save company branding", use_container_width=True):
    _fields = {"name": _new_cname, "short": _new_cshort, "email": _new_cemail, "web": _new_cweb}
    _changed = False
    for _k, _v in _fields.items():
        _v = _v.strip()
        if _v and _v != _cur_company[_k]:
            set_agent_config_value(f"company.{_k}", _v)
            _changed = True
    if _changed:
        st.success("Company branding saved.")
        st.rerun()
    else:
        st.info("No changes to save.")

st.markdown("<hr/>", unsafe_allow_html=True)

# ── Section 1 · Agent Model ───────────────────────────────────────────────────
st.markdown("<div class='sec'><div class='sec-ic'>🤖</div><div class='sec-t'>Agent Model</div></div>", unsafe_allow_html=True)
st.markdown("<div class='hint'>Toggle a provider to make it PhantomOps's <b>only</b> model. "
            "Leave all off for <b>Auto</b> — PhantomOps fans out across every connected provider. "
            "Changes apply on the next email.</div>",
            unsafe_allow_html=True)

if not enabled:
    st.markdown("<div class='warnbox'>No API keys configured — set at least one provider key in <b>.env</b>.</div>", unsafe_allow_html=True)

# Auto status line
_auto = (current_model == "" or current_model not in enabled)
st.markdown(
    f"<div style='margin:0 0 12px 40px;font-size:.84rem;color:var(--txt2)'>"
    f"Mode: <b style='color:{'#a07bff' if _auto else 'var(--ok)'}'>"
    f"{'🌐 Auto — all connected providers' if _auto else PROVIDERS[current_model]['icon'] + ' ' + PROVIDERS[current_model]['name'] + ' only'}</b></div>",
    unsafe_allow_html=True,
)

# Single-select toggle state — initialise from agent_config.yaml once, persist
# via on_change. (Avoids the Streamlit value=+key= divergence that could leave
# a toggle visually on while the saved model stayed Auto.) "local" isn't in
# PROVIDERS (no API key to gate on — see the dedicated card below) but still
# participates in the same single-select group, so pinning it must also
# untoggle any cloud provider and vice versa.
_ALL_MODEL_IDS = list(PROVIDERS.keys()) + ["local"]

def _pin_model(pid):
    if st.session_state.get(f"tg_{pid}"):
        set_agent_config_value("agent_model", pid)
        for o in _ALL_MODEL_IDS:                   # enforce single-select
            if o != pid:
                st.session_state[f"tg_{o}"] = False
    else:
        set_agent_config_value("agent_model", "")  # toggled off → Auto

for _pid in _ALL_MODEL_IDS:
    st.session_state.setdefault(f"tg_{_pid}", current_model == _pid)

# Provider card grid (2 columns) — each card carries its own toggle (single-select, instant)
ids = list(PROVIDERS.keys())
for row in range(0, len(ids), 2):
    cols = st.columns(2)
    for col, pid in zip(cols, ids[row:row + 2]):
        info      = PROVIDERS[pid]
        has_key   = bool(info["key"])
        is_active = (current_model == pid)
        badge = ("<span class='bdg bdg-active'>● ACTIVE</span>" if is_active and has_key
                 else "<span class='bdg bdg-ok'>Connected</span>" if has_key
                 else "<span class='bdg bdg-no'>No key</span>")
        with col:
            st.markdown(
                f"""<div class='pcard {'active' if is_active and has_key else ''}'>
                    <div class='pcard-top'>
                        <div class='pcard-name'>{info['icon']} {info['name']}</div>{badge}
                    </div>
                    <div class='pcard-tip'>{info['tip']}</div>
                </div>""",
                unsafe_allow_html=True,
            )
            
            # Model Dropdown
            current_prov_model = get_provider_model(pid)
            models_list = AVAILABLE_MODELS.get(pid, [info['model']]).copy()
            if current_prov_model not in models_list and current_prov_model:
                models_list.append(current_prov_model)
            
            sel_model = st.selectbox(
                "Model",
                options=models_list,
                index=models_list.index(current_prov_model) if current_prov_model in models_list else 0,
                disabled=not has_key,
                key=f"sel_{pid}",
                label_visibility="collapsed"
            )
            if sel_model != current_prov_model:
                set_agent_config_value(f"provider_models.{pid}", sel_model)
                st.rerun()

            st.toggle(
                f"Use {info['name']} only", key=f"tg_{pid}",
                disabled=not has_key, on_change=_pin_model, args=(pid,),
                help=("No API key in .env" if not has_key else f"Make {info['name']} PhantomOps's only model"),
            )
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ── Local (Ollama) card — no API key to gate on; gated on whether the local
# Ollama server is reachable and has at least one model pulled instead. The
# model list is queried live (get_ollama_models() → GET /api/tags) rather than
# a free-text tag input, so the dropdown only ever offers models that are
# actually pulled and ready to use.
_ollama_models = get_ollama_models()
_has_ollama = bool(_ollama_models)
_current_local_model = get_provider_model("local")
_local_is_active = (current_model == "local")
_local_badge = ("<span class='bdg bdg-active'>● ACTIVE</span>" if _local_is_active and _has_ollama
                else "<span class='bdg bdg-ok'>Connected</span>" if _has_ollama
                else "<span class='bdg bdg-no'>Not reachable</span>")
st.markdown(
    f"""<div class='pcard {'active' if _local_is_active and _has_ollama else ''}'>
        <div class='pcard-top'>
            <div class='pcard-name'>🖥️ Local (Ollama)</div>{_local_badge}
        </div>
        <div class='pcard-tip'>Runs entirely on this machine — no API key, no per-token cost. Opt-in only: never part of Auto's fallback chain.</div>
    </div>""",
    unsafe_allow_html=True,
)
_local_options = _ollama_models.copy()
if _current_local_model and _current_local_model not in _local_options:
    _local_options.append(_current_local_model)
if not _local_options:
    st.markdown("<div class='warnbox'>Ollama isn't reachable, or no models are pulled — "
                "run <code>ollama pull &lt;model&gt;</code> and refresh this page.</div>",
                unsafe_allow_html=True)
else:
    _sel_local_model = st.selectbox(
        "Local model", options=_local_options,
        index=_local_options.index(_current_local_model) if _current_local_model in _local_options else 0,
        key="sel_local", label_visibility="collapsed",
    )
    if _sel_local_model != _current_local_model:
        set_agent_config_value("provider_models.local", _sel_local_model)
        st.rerun()

st.toggle(
    "Use Local (Ollama) only", key="tg_local",
    disabled=not _has_ollama, on_change=_pin_model, args=("local",),
    help=("Ollama not reachable / no models pulled" if not _has_ollama else "Make Local (Ollama) PhantomOps's only model"),
)

# ── Section 2 · Vision Provider ───────────────────────────────────────────────
st.markdown("<div class='sec'><div class='sec-ic'>👁️</div><div class='sec-t'>Vision Provider</div></div>", unsafe_allow_html=True)
st.markdown("<div class='hint'>Used to auto-tag images and logos dropped into the assets folder. "
            "<b>Auto</b> tries every available provider in order.</div>", unsafe_allow_html=True)

# "local" has no API key to gate on (Ollama, not a cloud provider) — always
# offered, same opt-in-only rule as agent_model/routing_model: it's never
# part of Auto's automatic fallback chain, only usable when explicitly
# selected here.
vkeys   = [""] + [k for k in ["groq", "gemini", "claude", "openai"] if k in enabled] + ["local"]
vlabels = [VISION_OPTIONS[k] for k in vkeys]
v_idx   = vkeys.index(current_vision) if current_vision in vkeys else 0
sel_vlabel = st.selectbox("Vision provider", vlabels, index=v_idx, label_visibility="collapsed")
selected_vision = vkeys[vlabels.index(sel_vlabel)]
if selected_vision != current_vision:           # instant save on change
    set_agent_config_value("vision_provider", selected_vision)
    st.rerun()

if selected_vision == "local":
    _current_local_vision = get_provider_model("local_vision")
    _lv_options = _ollama_models.copy()  # reuse the same live-fetched list from Agent Model above
    if _current_local_vision and _current_local_vision not in _lv_options:
        _lv_options.append(_current_local_vision)
    if not _lv_options:
        st.markdown("<div class='warnbox'>Ollama isn't reachable, or no models are pulled — "
                    "run <code>ollama pull &lt;vision model&gt;</code> and refresh this page.</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown("<div style='margin:8px 0 0 40px;font-size:.82rem;color:var(--txt2)'>Local vision model:</div>", unsafe_allow_html=True)
        _sel_local_vision = st.selectbox(
            "Local vision model", options=_lv_options,
            index=_lv_options.index(_current_local_vision) if _current_local_vision in _lv_options else 0,
            key="sel_local_vision", label_visibility="collapsed",
        )
        if _sel_local_vision != _current_local_vision:
            set_agent_config_value("provider_models.local_vision", _sel_local_vision)
            st.rerun()

# ── Section 3 · Sender Allowlist ──────────────────────────────────────────────
st.markdown("<div class='sec'><div class='sec-ic'>✉️</div><div class='sec-t'>Sender Allowlist</div></div>", unsafe_allow_html=True)
st.markdown("<div class='hint'>Only these senders can trigger PhantomOps. Use <b>@domain.com</b> to allow a whole company. "
            "Empty = anyone can email PhantomOps (not recommended).</div>", unsafe_allow_html=True)

current_allowlist = list(eff_allow)
st.markdown(f"<div style='color:var(--muted);font-size:.8rem;margin:0 0 10px 40px'>Source: <b style='color:var(--txt2)'>{allow_src}</b></div>", unsafe_allow_html=True)

if current_allowlist:
    for entry in current_allowlist:
        icon = "🏢" if entry.startswith("@") else "👤"
        c_e, c_x = st.columns([8, 1])
        with c_e:
            st.markdown(f"<div class='pill' style='margin-bottom:6px'>{icon}&nbsp; {entry}</div>", unsafe_allow_html=True)
        with c_x:
            if st.button("Remove", key=f"rm_{entry}"):
                set_agent_config_value("sender_allowlist", [e for e in current_allowlist if e != entry])
                st.rerun()
else:
    st.markdown("<div class='warnbox'>⚠️ No allowlist set — PhantomOps will reply to anyone.</div>", unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
c_in, c_add = st.columns([5, 1])
with c_in:
    new_entry = st.text_input("Add sender", placeholder="@eaisystems.com  or  person@company.com",
                              label_visibility="collapsed")
with c_add:
    if st.button("＋ Add", use_container_width=True, type="primary"):
        val = new_entry.strip().lower()
        if val and val not in current_allowlist:
            set_agent_config_value("sender_allowlist", current_allowlist + [val])
            st.rerun()
        elif val in current_allowlist:
            st.warning(f"'{val}' is already allowed.")

if current_allowlist:
    if st.button("🗑️ Clear allowlist (allow everyone)"):
        set_agent_config_value("sender_allowlist", [])
        st.rerun()

st.markdown("<hr/>", unsafe_allow_html=True)

# ── Section 4 · Email Provider ────────────────────────────────────────────────
st.markdown("<div class='sec'><div class='sec-ic'>📬</div><div class='sec-t'>Email Provider</div></div>", unsafe_allow_html=True)
st.markdown("<div class='hint'>Which mailbox PhantomOps monitors and replies from. "
            "<b>Requires restarting the agent process</b> to take effect — unlike "
            "everything else on this page, the mailbox connection authenticates "
            "once at startup, not per email.</div>", unsafe_allow_html=True)

EMAIL_PROVIDERS = {"outlook": "📧  Outlook (Microsoft 365 / Graph API)",
                    "gmail":   "📮  Gmail (Google API)"}
current_email_provider = get_email_provider()
ep_keys   = list(EMAIL_PROVIDERS.keys())
ep_labels = list(EMAIL_PROVIDERS.values())
ep_idx    = ep_keys.index(current_email_provider) if current_email_provider in ep_keys else 0
sel_ep_label = st.selectbox("Email provider", ep_labels, index=ep_idx, label_visibility="collapsed")
selected_ep = ep_keys[ep_labels.index(sel_ep_label)]
if selected_ep != current_email_provider:
    set_agent_config_value("email_provider", selected_ep)
    st.warning(f"Email provider set to **{EMAIL_PROVIDERS[selected_ep]}** — "
               f"restart the agent process for this to take effect.")

# ── Connect / Disconnect the SELECTED provider's mailbox ──────────────────────
# Applies to whichever provider is picked above (not necessarily the one
# currently active) — lets you connect a mailbox ahead of switching to it.
_ep_name = "Gmail" if selected_ep == "gmail" else "Outlook"
_token_path = GMAIL_TOKEN_PATH if selected_ep == "gmail" else OUTLOOK_TOKEN_PATH
_is_connected = (is_gmail_connected(GMAIL_TOKEN_PATH) if selected_ep == "gmail"
                 else is_outlook_connected(OUTLOOK_TOKEN_PATH))

_status_badge = ("<span class='bdg bdg-ok'>✅ Connected</span>" if _is_connected
                 else "<span class='bdg bdg-no'>⚠️ Not connected</span>")
st.markdown(f"<div style='margin:4px 0 12px 40px'>{_status_badge}</div>", unsafe_allow_html=True)

st.session_state.setdefault("confirm_disconnect", False)
c_conn, c_disc = st.columns(2)
with c_conn:
    if st.button(f"🔌 Connect {_ep_name}", use_container_width=True, type="primary",
                 disabled=_is_connected,
                 help="Already connected — disconnect first to switch mailboxes" if _is_connected else None):
        with st.spinner(f"Opening your browser for {_ep_name} sign-in — "
                         f"complete it there, then this page continues automatically..."):
            if selected_ep == "gmail":
                ok, msg = connect_gmail(GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_TOKEN_PATH)
            else:
                ok, msg = connect_outlook(OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID, OUTLOOK_TOKEN_PATH)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

with c_disc:
    if not _is_connected:
        st.button("🔓 Disconnect", use_container_width=True, disabled=True, help="Not connected")
    elif not st.session_state.confirm_disconnect:
        if st.button("🔓 Disconnect", use_container_width=True):
            st.session_state.confirm_disconnect = True
            st.rerun()
    else:
        st.warning(f"Disconnect {_ep_name}? PhantomOps won't be able to use this mailbox "
                   f"until it's reconnected.")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("Yes, disconnect", use_container_width=True, type="primary"):
                ok, msg = disconnect_mailbox(_token_path)
                st.session_state.confirm_disconnect = False
                (st.success if ok else st.error)(msg)
                st.rerun()
        with cc2:
            if st.button("Cancel", use_container_width=True):
                st.session_state.confirm_disconnect = False
                st.rerun()

st.markdown("<hr/>", unsafe_allow_html=True)

# ── Reset ─────────────────────────────────────────────────────────────────────
c_reset, _ = st.columns([2, 4])
with c_reset:
    if st.button("↩️ Reset model & vision to Auto", use_container_width=True):
        set_agent_config_value("agent_model", "")
        set_agent_config_value("vision_provider", "")
        for _pid in PROVIDERS:
            st.session_state[f"tg_{_pid}"] = False
        st.rerun()

st.markdown(
    "<div style='color:var(--muted);font-size:.78rem;margin-top:14px;text-align:center'>"
    "Everything saves instantly · all changes reach the agent on its next email.</div>",
    unsafe_allow_html=True,
)
