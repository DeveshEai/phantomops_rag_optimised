"""
tools/ppt_rota.py — Multi-Template Rota & State Manager for Presentations

Rotates through curated presentation design bundles (1 to 7 or up to 10) sequentially
every time a presentation is generated, giving each PPT deck a distinct, high-end,
custom-crafted aesthetic (like Claude / top-tier management consultancy decks).

Each bundle contains:
  • Visual theme & CSS palette (background, headings, typography, cards, badges, tables)
  • Master layout styles (cover slide gradient overlay, lead closing slide, border styles)
  • Matched master PPTX template mapping for native PPTX compatibility
"""

import os
import json
import threading
from datetime import datetime
from core.config import DATABASE_DIR, ASSETS_TEMPLATES_DIR

_ROTA_STATE_FILE = os.path.join(DATABASE_DIR, "ppt_rota_state.json")
_LOCK = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
#  DESIGN BUNDLES REGISTRY (1 through 10)
# ══════════════════════════════════════════════════════════════════════════════

def _build_css_style(
    font_family: str,
    bg_color: str,
    text_color: str,
    h1_color: str,
    h2_subtitle_color: str,
    h3_color: str,
    card_bg: str,
    card_border: str,
    card_radius: str,
    card_accent_border: str,
    card_p_color: str,
    kpi_number_color: str,
    kpi_card_bg: str,
    kpi_border: str,
    badge_bg: str,
    badge_text: str,
    badge_border: str,
    badge_orange_bg: str,
    badge_orange_text: str,
    badge_green_bg: str,
    badge_green_text: str,
    table_header_bg: str,
    table_header_text: str,
    table_border: str,
    table_even_bg: str,
    lead_gradient: str,
    lead_h2_color: str,
    lead_p_color: str,
    img_border: str,
    card_shadow: str = "0 4px 6px -1px rgba(0, 0, 0, 0.04)",
) -> str:
    """Helper to generate a complete, rock-solid Marp CSS theme string."""
    return f"""/* Marp High-Impact Executive Presentation Theme */
section {{
  font-family: {font_family};
  background-color: {bg_color};
  color: {text_color};
  padding: 44px 64px;
  font-size: 1.15rem;
  line-height: 1.55;
}}

header {{
  font-size: 0.85rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}

footer {{
  font-size: 0.8rem;
  color: #94a3b8;
}}

/* Authoritative Slide Titles */
h1, section > h2:first-child, section > h2:first-of-type:not(h1 + h2) {{
  color: {h1_color};
  font-size: 2.25rem;
  font-weight: 700;
  margin-bottom: 0.35rem;
  letter-spacing: -0.02em;
}}

h1 + h2 {{
  color: {h2_subtitle_color};
  font-size: 1.25rem;
  font-weight: 500;
  margin-top: 0;
  margin-bottom: 1.35rem;
}}

h3 {{
  color: {h3_color};
  font-size: 1.15rem;
  font-weight: 600;
  margin-top: 0;
  margin-bottom: 0.5rem;
}}

p {{
  color: {text_color};
  margin-bottom: 0.85rem;
  font-size: 1.15rem;
}}

ul, ol {{
  margin-top: 1rem;
  margin-bottom: 1.2rem;
  padding-left: 2rem;
}}

li {{
  margin-bottom: 0.85rem;
  color: {text_color};
  font-size: 1.15rem;
  line-height: 1.55;
}}

li > ul, li > ol {{
  margin-top: 0.4rem;
  margin-bottom: 0.4rem;
  padding-left: 1.5rem;
}}

li > ul > li, li > ol > li {{
  font-size: 1.05rem;
  margin-bottom: 0.35rem;
  color: #475569;
}}

strong {{
  color: {h1_color};
  font-weight: 600;
}}

/* Grids */
.grid-2 {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 14px;
}}

.grid-3 {{
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}}

/* Cards */
.card {{
  background: {card_bg};
  border: {card_border};
  border-radius: {card_radius};
  padding: 14px 18px;
  box-shadow: {card_shadow};
}}

.card h3 {{
  border-bottom: {card_accent_border};
  padding-bottom: 4px;
  margin-bottom: 8px;
  font-size: 1.05rem;
  font-weight: 700;
}}

.card p {{
  font-size: 0.92rem;
  line-height: 1.45;
  margin-bottom: 0;
  color: {card_p_color};
}}

/* KPI / Hero Metric Cards */
.kpi-card {{
  background: {kpi_card_bg};
  border: {kpi_border};
  border-radius: 12px;
  padding: 22px 16px;
  text-align: center;
  box-shadow: {card_shadow};
}}

.kpi-number {{
  font-size: 2.8rem;
  font-weight: 800;
  color: {kpi_number_color};
  line-height: 1.1;
  margin-bottom: 6px;
}}

.kpi-label {{
  font-size: 0.95rem;
  color: #64748b;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}}

/* Category Badges */
.badge {{
  display: inline-block;
  padding: 4px 10px;
  border-radius: 9999px;
  background: {badge_bg};
  color: {badge_text};
  border: {badge_border};
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 10px;
}}

.badge-orange {{
  background: {badge_orange_bg};
  color: {badge_orange_text};
}}

.badge-green {{
  background: {badge_green_bg};
  color: {badge_green_text};
}}

/* Tables */
table {{
  width: 100%;
  border-collapse: collapse;
  margin-top: 16px;
  background: #ffffff;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 4px rgba(0,0,0,0.03);
}}

th {{
  background-color: {table_header_bg};
  color: {table_header_text};
  padding: 12px 16px;
  font-weight: 600;
  text-align: left;
  font-size: 0.95rem;
}}

td {{
  padding: 12px 16px;
  border-bottom: {table_border};
  color: #334155;
  font-size: 0.95rem;
}}

tr:nth-child(even) td {{
  background-color: {table_even_bg};
}}

/* Hero / Section Header Slides */
section.lead {{
  background: {lead_gradient};
  color: #ffffff;
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 60px 80px;
}}

.cover-logo {{
  height: 52px;
  background: #ffffff;
  padding: 8px 18px;
  border-radius: 10px;
  margin-bottom: 24px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
  object-fit: contain;
}}

section.lead h1 {{
  color: #ffffff !important;
  font-size: 2.8rem;
  font-weight: 800;
  line-height: 1.2;
  margin-bottom: 0.5rem;
}}

section.lead h2 {{
  color: {lead_h2_color};
  font-size: 1.5rem;
  font-weight: 400;
  margin-bottom: 1.5rem;
}}

section.lead p {{
  color: {lead_p_color};
  font-size: 1.15rem;
}}

section.lead strong {{
  color: #ffffff;
}}

section.lead h1, section.lead h2, section.lead p, section.lead strong {{
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.7);
}}

/* Real Photographs & Slide Images */
img:not(.cover-logo) {{
  max-width: 100%;
  max-height: 400px;
  object-fit: cover;
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
  border: {img_border};
  display: block;
  margin: 0 auto;
}}

.slide-img {{
  width: 100%;
  max-height: 400px;
  object-fit: cover;
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
  border: {img_border};
  display: block;
}}

.img-caption {{
  font-size: 0.78rem;
  color: #64748b;
  text-align: center;
  margin-top: 6px;
  font-style: italic;
}}
"""


PPT_TEMPLATES_REGISTRY = {
    # ── Bundle 1: Executive Sapphire & Navy (Current Baseline) ─────────────────
    1: {
        "id": 1,
        "name": "Executive Sapphire",
        "tagline": "Authoritative corporate blue with high-contrast executive cards",
        "primary_color": "#2563eb",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(15, 23, 42, 0.82) 0%, rgba(30, 58, 138, 0.86) 100%)",
        "lead_h2_color": "#93c5fd",
        "lead_p_color": "#cbd5e1",
        "pptx_master_template": "Template 1 - A Rating Blue - Customers Sales  .pptx",
        "css_style": _build_css_style(
            font_family="'Segoe UI', 'Inter', system-ui, sans-serif",
            bg_color="#f8fafc",
            text_color="#0f172a",
            h1_color="#0f172a",
            h2_subtitle_color="#2563eb",
            h3_color="#1e293b",
            card_bg="#ffffff",
            card_border="1px solid #e2e8f0",
            card_radius="10px",
            card_accent_border="2px solid #3b82f6",
            card_p_color="#334155",
            kpi_number_color="#2563eb",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #e2e8f0",
            badge_bg="#dbeafe",
            badge_text="#1d4ed8",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#dcfce7",
            badge_green_text="#15803d",
            table_header_bg="#1e293b",
            table_header_text="#ffffff",
            table_border="1px solid #e2e8f0",
            table_even_bg="#f8fafc",
            lead_gradient="linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%)",
            lead_h2_color="#93c5fd",
            lead_p_color="#cbd5e1",
            img_border="1px solid #e2e8f0",
        ),
    },

    # ── Bundle 2: Minimalist Slate & Charcoal (Apple / Editorial Luxury) ───────
    2: {
        "id": 2,
        "name": "Minimalist Charcoal",
        "tagline": "Stark editorial aesthetic, hairline borders, and pure monochrome focus",
        "primary_color": "#18181b",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(9, 9, 11, 0.90) 0%, rgba(39, 39, 42, 0.88) 100%)",
        "lead_h2_color": "#e4e4e7",
        "lead_p_color": "#a1a1aa",
        "pptx_master_template": "Template 4 - C Rating - Internal _ Technical .pptx",
        "css_style": _build_css_style(
            font_family="'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            bg_color="#ffffff",
            text_color="#18181b",
            h1_color="#09090b",
            h2_subtitle_color="#52525b",
            h3_color="#18181b",
            card_bg="#fafafa",
            card_border="1px solid #e4e4e7",
            card_radius="6px",
            card_accent_border="2px solid #18181b",
            card_p_color="#3f3f46",
            kpi_number_color="#09090b",
            kpi_card_bg="#fafafa",
            kpi_border="1px solid #d4d4d8",
            badge_bg="#f4f4f5",
            badge_text="#18181b",
            badge_border="1px solid #e4e4e7",
            badge_orange_bg="#fef3c7",
            badge_orange_text="#78350f",
            badge_green_bg="#f4f4f5",
            badge_green_text="#27272a",
            table_header_bg="#18181b",
            table_header_text="#ffffff",
            table_border="1px solid #e4e4e7",
            table_even_bg="#f4f4f5",
            lead_gradient="linear-gradient(135deg, #09090b 0%, #27272a 100%)",
            lead_h2_color="#e4e4e7",
            lead_p_color="#a1a1aa",
            img_border="1px solid #e4e4e7",
        ),
    },

    # ── Bundle 3: Modern Emerald & Mint (Fintech & Sustainability) ─────────────
    3: {
        "id": 3,
        "name": "Modern Emerald",
        "tagline": "Deep forest emerald with crisp mint badges and fresh growth accents",
        "primary_color": "#059669",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(6, 78, 59, 0.88) 0%, rgba(15, 23, 42, 0.92) 100%)",
        "lead_h2_color": "#a7f3d0",
        "lead_p_color": "#d1fae5",
        "pptx_master_template": "Template 1 - A Rating Blue - Customers Sales  .pptx",
        "css_style": _build_css_style(
            font_family="'Segoe UI', 'Inter', system-ui, sans-serif",
            bg_color="#f0fdf4",
            text_color="#064e3b",
            h1_color="#064e3b",
            h2_subtitle_color="#059669",
            h3_color="#065f46",
            card_bg="#ffffff",
            card_border="1px solid #a7f3d0",
            card_radius="12px",
            card_accent_border="2px solid #10b981",
            card_p_color="#1f2937",
            kpi_number_color="#059669",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #6ee7b7",
            badge_bg="#d1fae5",
            badge_text="#065f46",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#bbf7d0",
            badge_green_text="#14532d",
            table_header_bg="#064e3b",
            table_header_text="#ffffff",
            table_border="1px solid #a7f3d0",
            table_even_bg="#ecfdf5",
            lead_gradient="linear-gradient(135deg, #064e3b 0%, #022c22 100%)",
            lead_h2_color="#a7f3d0",
            lead_p_color="#d1fae5",
            img_border="1px solid #a7f3d0",
        ),
    },

    # ── Bundle 4: Consulting Amber & Warm Paper (McKinsey / BCG Advisory) ──────
    4: {
        "id": 4,
        "name": "Consulting Amber",
        "tagline": "Warm executive ivory, deep slate navy headers, and refined amber accents",
        "primary_color": "#d97706",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(15, 23, 42, 0.90) 0%, rgba(120, 53, 15, 0.85) 100%)",
        "lead_h2_color": "#fde68a",
        "lead_p_color": "#e2e8f0",
        "pptx_master_template": "Template 2 - A Rating Orange - Customers Sales _ Tech Solutions.pptx",
        "css_style": _build_css_style(
            font_family="'Georgia', 'Segoe UI', 'Inter', serif",
            bg_color="#fdfbf7",
            text_color="#1c1917",
            h1_color="#1c1917",
            h2_subtitle_color="#d97706",
            h3_color="#292524",
            card_bg="#ffffff",
            card_border="1px solid #e7e5e4",
            card_radius="8px",
            card_accent_border="2px solid #d97706",
            card_p_color="#44403c",
            kpi_number_color="#b45309",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #fde68a",
            badge_bg="#fef3c7",
            badge_text="#92400e",
            badge_border="1px solid #fde68a",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#9a3412",
            badge_green_bg="#dcfce7",
            badge_green_text="#166534",
            table_header_bg="#1c1917",
            table_header_text="#ffffff",
            table_border="1px solid #e7e5e4",
            table_even_bg="#fffbeb",
            lead_gradient="linear-gradient(135deg, #1c1917 0%, #78350f 100%)",
            lead_h2_color="#fde68a",
            lead_p_color="#e7e5e4",
            img_border="1px solid #e7e5e4",
        ),
    },

    # ── Bundle 5: Tech Violet & Electric Indigo (Claude AI / SaaS Visionary) ───
    5: {
        "id": 5,
        "name": "Tech Violet",
        "tagline": "Vivid electric violet and deep indigo gradients for cutting-edge tech",
        "primary_color": "#7c3aed",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(46, 16, 101, 0.90) 0%, rgba(30, 27, 75, 0.92) 100%)",
        "lead_h2_color": "#c4b5fd",
        "lead_p_color": "#e0e7ff",
        "pptx_master_template": "Template 3 - B Rating Dark- Marketing _ Events  .pptx",
        "css_style": _build_css_style(
            font_family="'Inter', 'Segoe UI', system-ui, sans-serif",
            bg_color="#faf5ff",
            text_color="#3b0764",
            h1_color="#3b0764",
            h2_subtitle_color="#7c3aed",
            h3_color="#581c87",
            card_bg="#ffffff",
            card_border="1px solid #e9d5ff",
            card_radius="14px",
            card_accent_border="2px solid #8b5cf6",
            card_p_color="#334155",
            kpi_number_color="#7c3aed",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #c084fc",
            badge_bg="#ede9fe",
            badge_text="#5b21b6",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#dcfce7",
            badge_green_text="#15803d",
            table_header_bg="#4c1d95",
            table_header_text="#ffffff",
            table_border="1px solid #e9d5ff",
            table_even_bg="#f5f3ff",
            lead_gradient="linear-gradient(135deg, #2e1065 0%, #1e1b4b 100%)",
            lead_h2_color="#c4b5fd",
            lead_p_color="#e0e7ff",
            img_border="1px solid #e9d5ff",
            card_shadow="0 4px 12px rgba(124, 58, 237, 0.06)",
        ),
    },

    # ── Bundle 6: High-Impact Crimson & Charcoal (Leadership & Security) ───────
    6: {
        "id": 6,
        "name": "High-Impact Crimson",
        "tagline": "Bold crimson red accent bars, dark charcoal headers, and urgent leadership energy",
        "primary_color": "#dc2626",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(69, 10, 10, 0.92) 0%, rgba(15, 23, 42, 0.92) 100%)",
        "lead_h2_color": "#fca5a5",
        "lead_p_color": "#fecaca",
        "pptx_master_template": "Template 3 - B Rating Dark- Marketing _ Events  .pptx",
        "css_style": _build_css_style(
            font_family="'Segoe UI', 'Inter', system-ui, sans-serif",
            bg_color="#ffffff",
            text_color="#0f172a",
            h1_color="#0f172a",
            h2_subtitle_color="#dc2626",
            h3_color="#18181b",
            card_bg="#ffffff",
            card_border="1px solid #e2e8f0",
            card_radius="8px",
            card_accent_border="2px solid #dc2626",
            card_p_color="#334155",
            kpi_number_color="#b91c1c",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #fecaca",
            badge_bg="#fee2e2",
            badge_text="#991b1b",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#dcfce7",
            badge_green_text="#15803d",
            table_header_bg="#18181b",
            table_header_text="#ffffff",
            table_border="1px solid #fecaca",
            table_even_bg="#fef2f2",
            lead_gradient="linear-gradient(135deg, #450a0a 0%, #0f172a 100%)",
            lead_h2_color="#fca5a5",
            lead_p_color="#fecaca",
            img_border="1px solid #e2e8f0",
        ),
    },

    # ── Bundle 7: Frost Cyan & Nordic Glass (Modern Cloud Enterprise) ─────────
    7: {
        "id": 7,
        "name": "Frost Cyan",
        "tagline": "Crisp Scandinavian ice-blue, oceanic cyan pills, and clean modern architecture",
        "primary_color": "#0284c7",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(12, 74, 110, 0.90) 0%, rgba(15, 23, 42, 0.90) 100%)",
        "lead_h2_color": "#7dd3fc",
        "lead_p_color": "#e0f2fe",
        "pptx_master_template": "Template 1 - A Rating Blue - Customers Sales  .pptx",
        "css_style": _build_css_style(
            font_family="'Inter', 'Segoe UI', system-ui, sans-serif",
            bg_color="#f0f9ff",
            text_color="#083344",
            h1_color="#083344",
            h2_subtitle_color="#0284c7",
            h3_color="#0e7490",
            card_bg="#ffffff",
            card_border="1px solid #bae6fd",
            card_radius="12px",
            card_accent_border="2px solid #06b6d4",
            card_p_color="#334155",
            kpi_number_color="#0284c7",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #7dd3fc",
            badge_bg="#e0f2fe",
            badge_text="#0369a1",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#dcfce7",
            badge_green_text="#15803d",
            table_header_bg="#0c4a6e",
            table_header_text="#ffffff",
            table_border="1px solid #bae6fd",
            table_even_bg="#ecfeff",
            lead_gradient="linear-gradient(135deg, #0c4a6e 0%, #082f49 100%)",
            lead_h2_color="#7dd3fc",
            lead_p_color="#e0f2fe",
            img_border="1px solid #bae6fd",
        ),
    },

    # ── Bundle 8: Sunset Terracotta & Copper (Warm Brand / Creative Agency) ────
    8: {
        "id": 8,
        "name": "Sunset Terracotta",
        "tagline": "Warm terracotta, roasted copper accents, and engaging storytelling feel",
        "primary_color": "#ea580c",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(124, 45, 18, 0.90) 0%, rgba(15, 23, 42, 0.92) 100%)",
        "lead_h2_color": "#fed7aa",
        "lead_p_color": "#ffedd5",
        "pptx_master_template": "Template 2 - A Rating Orange - Customers Sales _ Tech Solutions.pptx",
        "css_style": _build_css_style(
            font_family="'Segoe UI', 'Inter', sans-serif",
            bg_color="#fffaf5",
            text_color="#431407",
            h1_color="#431407",
            h2_subtitle_color="#ea580c",
            h3_color="#7c2d12",
            card_bg="#ffffff",
            card_border="1px solid #fed7aa",
            card_radius="10px",
            card_accent_border="2px solid #ea580c",
            card_p_color="#334155",
            kpi_number_color="#ea580c",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #fdba74",
            badge_bg="#ffedd5",
            badge_text="#9a3412",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#dcfce7",
            badge_green_text="#15803d",
            table_header_bg="#7c2d12",
            table_header_text="#ffffff",
            table_border="1px solid #fed7aa",
            table_even_bg="#fff7ed",
            lead_gradient="linear-gradient(135deg, #7c2d12 0%, #431407 100%)",
            lead_h2_color="#fed7aa",
            lead_p_color="#ffedd5",
            img_border="1px solid #fed7aa",
        ),
    },

    # ── Bundle 9: Deep Midnight / Premium Dark Surface ────────────────────────
    9: {
        "id": 9,
        "name": "Midnight Dark Surface",
        "tagline": "Luminous neon-accented dark surface deck for developer, crypto, or AI keynotes",
        "primary_color": "#38bdf8",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(3, 7, 18, 0.94) 0%, rgba(15, 23, 42, 0.95) 100%)",
        "lead_h2_color": "#38bdf8",
        "lead_p_color": "#94a3b8",
        "pptx_master_template": "Template 3 - B Rating Dark- Marketing _ Events  .pptx",
        "css_style": _build_css_style(
            font_family="'Inter', 'Segoe UI', system-ui, sans-serif",
            bg_color="#0f172a",
            text_color="#f1f5f9",
            h1_color="#f8fafc",
            h2_subtitle_color="#38bdf8",
            h3_color="#e2e8f0",
            card_bg="#1e293b",
            card_border="1px solid #334155",
            card_radius="10px",
            card_accent_border="2px solid #38bdf8",
            card_p_color="#cbd5e1",
            kpi_number_color="#38bdf8",
            kpi_card_bg="#1e293b",
            kpi_border="1px solid #38bdf8",
            badge_bg="#0369a1",
            badge_text="#e0f2fe",
            badge_border="none",
            badge_orange_bg="#7c2d12",
            badge_orange_text="#fed7aa",
            badge_green_bg="#064e3b",
            badge_green_text="#a7f3d0",
            table_header_bg="#0284c7",
            table_header_text="#ffffff",
            table_border="1px solid #334155",
            table_even_bg="#1e293b",
            lead_gradient="linear-gradient(135deg, #030712 0%, #0f172a 100%)",
            lead_h2_color="#38bdf8",
            lead_p_color="#94a3b8",
            img_border="1px solid #334155",
        ),
    },

    # ── Bundle 10: Titanium Executive / Modern Clean Steel ────────────────────
    10: {
        "id": 10,
        "name": "Titanium Steel",
        "tagline": "Neutral steel grey, refined typography, and precision engineering aesthetic",
        "primary_color": "#475569",
        "cover_bg_gradient": "linear-gradient(135deg, rgba(30, 41, 59, 0.92) 0%, rgba(15, 23, 42, 0.92) 100%)",
        "lead_h2_color": "#cbd5e1",
        "lead_p_color": "#e2e8f0",
        "pptx_master_template": "Template 4 - C Rating - Internal _ Technical .pptx",
        "css_style": _build_css_style(
            font_family="'Inter', 'Segoe UI', system-ui, sans-serif",
            bg_color="#f8fafc",
            text_color="#1e293b",
            h1_color="#0f172a",
            h2_subtitle_color="#475569",
            h3_color="#1e293b",
            card_bg="#ffffff",
            card_border="1px solid #cbd5e1",
            card_radius="8px",
            card_accent_border="2px solid #64748b",
            card_p_color="#334155",
            kpi_number_color="#334155",
            kpi_card_bg="#ffffff",
            kpi_border="1px solid #94a3b8",
            badge_bg="#e2e8f0",
            badge_text="#334155",
            badge_border="none",
            badge_orange_bg="#ffedd5",
            badge_orange_text="#c2410c",
            badge_green_bg="#dcfce7",
            badge_green_text="#15803d",
            table_header_bg="#334155",
            table_header_text="#ffffff",
            table_border="1px solid #cbd5e1",
            table_even_bg="#f1f5f9",
            lead_gradient="linear-gradient(135deg, #1e293b 0%, #0f172a 100%)",
            lead_h2_color="#cbd5e1",
            lead_p_color="#e2e8f0",
            img_border="1px solid #cbd5e1",
        ),
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  STATE MANAGER & ROTA ROTATION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def _load_rota_state() -> dict:
    """Read the current rota state from JSON file or return initial state."""
    if os.path.isfile(_ROTA_STATE_FILE):
        try:
            with open(_ROTA_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"[PPT Rota Warning] Could not read state file ({e}) — initializing fresh.")
    return {"current_index": 1, "total_generated": 0, "history": []}


def _save_rota_state(state: dict) -> None:
    """Persist rota state atomically to disk."""
    try:
        os.makedirs(os.path.dirname(_ROTA_STATE_FILE), exist_ok=True)
        tmp_file = _ROTA_STATE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        if os.path.exists(_ROTA_STATE_FILE):
            os.replace(tmp_file, _ROTA_STATE_FILE)
        else:
            os.rename(tmp_file, _ROTA_STATE_FILE)
    except Exception as e:
        print(f"[PPT Rota Error] Failed to persist rota state: {e}")


def get_next_ppt_bundle(rota_count: int = 10) -> dict:
    """
    Select the next template bundle in the rota (1 to rota_count),
    increment the persistent counter, and return the full bundle dictionary.
    
    Safe & resilient: wraps around sequentially (1 -> 2 -> ... -> rota_count -> 1).
    """
    with _LOCK:
        state = _load_rota_state()
        cur_idx = state.get("current_index", 1)
        total_gen = state.get("total_generated", 0) + 1

        # Bound to active rota count (e.g. 7 or up to 10)
        valid_rota_count = max(1, min(rota_count, len(PPT_TEMPLATES_REGISTRY)))
        selected_id = ((cur_idx - 1) % valid_rota_count) + 1

        # Advance for the NEXT run
        next_idx = (selected_id % valid_rota_count) + 1

        # Update state
        state["current_index"] = next_idx
        state["total_generated"] = total_gen
        state["last_selected_bundle"] = selected_id
        state["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_rota_state(state)

        bundle = PPT_TEMPLATES_REGISTRY.get(selected_id, PPT_TEMPLATES_REGISTRY[1])
        print(
            f"[PPT Rota] [*] Rotating to Template Bundle #{selected_id} of {valid_rota_count}: "
            f"'{bundle['name']}' (Deck #{total_gen})"
        )
        return bundle



def peek_current_bundle(rota_count: int = 7) -> dict:
    """Preview which template bundle will be used next WITHOUT advancing counter."""
    with _LOCK:
        state = _load_rota_state()
        cur_idx = state.get("current_index", 1)
        valid_rota_count = max(1, min(rota_count, len(PPT_TEMPLATES_REGISTRY)))
        selected_id = ((cur_idx - 1) % valid_rota_count) + 1
        return PPT_TEMPLATES_REGISTRY.get(selected_id, PPT_TEMPLATES_REGISTRY[1])


def get_bundle_by_id(bundle_id: int) -> dict:
    """Fetch an explicit bundle by ID (1 to 10). Falls back to 1 if not found."""
    return PPT_TEMPLATES_REGISTRY.get(bundle_id, PPT_TEMPLATES_REGISTRY[1])


def reset_rota() -> None:
    """Reset rota back to Template 1."""
    with _LOCK:
        state = _load_rota_state()
        state["current_index"] = 1
        _save_rota_state(state)
        print("[PPT Rota] Reset to Template 1.")
