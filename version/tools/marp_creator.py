"""
tools/marp_creator.py — High-Impact Marp Presentation Engine

Generates modern, executive-grade PowerPoint (.pptx) presentations using Marp CLI.
Supports rich layouts:
  • Executive Dark Cover & Closing Slides
  • 2-Column & 3-Column Card Grids
  • KPI / Hero Metric Callouts (e.g. 85%, $1.4M)
  • Structured Data Tables & Timelines
  • Modern Typography (Segoe UI / Inter), Pill Badges, and Border Styling
"""

import os
import re
import base64
import subprocess
import shutil
from datetime import datetime
from core.config import PPT_OUTPUT_DIR, get_company_info


MARP_THEME_STYLE = """
/* Marp High-Impact Executive Presentation Theme */
section {
  font-family: 'Segoe UI', 'Inter', system-ui, -apple-system, sans-serif;
  background-color: #f8fafc;
  color: #0f172a;
  padding: 44px 64px;
  font-size: 1.15rem;
  line-height: 1.55;
}

header {
  font-size: 0.85rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

footer {
  font-size: 0.8rem;
  color: #94a3b8;
}

/* Ensure slide titles are bold and authoritative whether generated as h1 or h2 */
h1, section > h2:first-child, section > h2:first-of-type:not(h1 + h2) {
  color: #0f172a;
  font-size: 2.25rem;
  font-weight: 700;
  margin-bottom: 0.35rem;
  letter-spacing: -0.02em;
}

h1 + h2 {
  color: #2563eb;
  font-size: 1.25rem;
  font-weight: 500;
  margin-top: 0;
  margin-bottom: 1.35rem;
}

h3 {
  color: #1e293b;
  font-size: 1.15rem;
  font-weight: 600;
  margin-top: 0;
  margin-bottom: 0.5rem;
}

p {
  color: #334155;
  margin-bottom: 0.85rem;
  font-size: 1.15rem;
}

ul, ol {
  margin-top: 1rem;
  margin-bottom: 1.2rem;
  padding-left: 2rem;
}

li {
  margin-bottom: 0.85rem;
  color: #334155;
  font-size: 1.15rem;
  line-height: 1.55;
}

li > ul, li > ol {
  margin-top: 0.4rem;
  margin-bottom: 0.4rem;
  padding-left: 1.5rem;
}

li > ul > li, li > ol > li {
  font-size: 1.05rem;
  margin-bottom: 0.35rem;
  color: #475569;
}

strong {
  color: #0f172a;
  font-weight: 600;
}

/* Grids */
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 14px;
}

.grid-3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}

/* Cards */
.card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px 18px;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.04);
}

.card h3 {
  border-bottom: 2px solid #3b82f6;
  padding-bottom: 4px;
  margin-bottom: 8px;
  font-size: 1.05rem;
  font-weight: 700;
}

.card p {
  font-size: 0.92rem;
  line-height: 1.45;
  margin-bottom: 0;
  color: #334155;
}

/* KPI / Hero Metric Cards */
.kpi-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 22px 16px;
  text-align: center;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.04);
}

.kpi-number {
  font-size: 2.8rem;
  font-weight: 800;
  color: #2563eb;
  line-height: 1.1;
  margin-bottom: 6px;
}

.kpi-label {
  font-size: 0.95rem;
  color: #64748b;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* Category Badges */
.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 9999px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 10px;
}

.badge-orange {
  background: #ffedd5;
  color: #c2410c;
}

.badge-green {
  background: #dcfce7;
  color: #15803d;
}

/* Tables */
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 16px;
  background: #ffffff;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 4px rgba(0,0,0,0.03);
}

th {
  background-color: #1e293b;
  color: #ffffff;
  padding: 12px 16px;
  font-weight: 600;
  text-align: left;
  font-size: 0.95rem;
}

td {
  padding: 12px 16px;
  border-bottom: 1px solid #e2e8f0;
  color: #334155;
  font-size: 0.95rem;
}

tr:nth-child(even) td {
  background-color: #f8fafc;
}

/* Hero / Section Header Slides */
section.lead {
  background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
  color: #ffffff;
  text-align: center;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 60px 80px;
}

.cover-logo {
  height: 52px;
  background: #ffffff;
  padding: 8px 18px;
  border-radius: 10px;
  margin-bottom: 24px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
  object-fit: contain;
}

section.lead h1 {
  color: #ffffff !important;
  font-size: 2.8rem;
  font-weight: 800;
  line-height: 1.2;
  margin-bottom: 0.5rem;
}

section.lead h2 {
  color: #93c5fd;
  font-size: 1.5rem;
  font-weight: 400;
  margin-bottom: 1.5rem;
}

section.lead p {
  color: #cbd5e1;
  font-size: 1.15rem;
}

section.lead strong {
  color: #ffffff;
}

section.lead h1, section.lead h2, section.lead p, section.lead strong {
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.7);
}

/* Real Photographs & Slide Images */
img:not(.cover-logo) {
  max-width: 100%;
  max-height: 400px;
  object-fit: cover;
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
  border: 1px solid #e2e8f0;
  display: block;
  margin: 0 auto;
}

.slide-img {
  width: 100%;
  max-height: 400px;
  object-fit: cover;
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
  border: 1px solid #e2e8f0;
  display: block;
}

.img-caption {
  font-size: 0.78rem;
  color: #64748b;
  text-align: center;
  margin-top: 6px;
  font-style: italic;
}
"""


MARP_SYSTEM_PROMPT = """You are an executive presentation designer and senior business strategist for EAI Systems Pvt. Ltd.
Your mission is to read the supplied topic and source material, and generate a client-ready, high-impact presentation in Marp Markdown.

CRITICAL ZERO-PLACEHOLDER MANDATE:
1. NEVER output bracketed placeholders, blanks, or template variables like:
   "[Insert #]", "$[Insert]", "[Insert %]", "[Insert Forecast]", "[Date]", "[Company Name]", "[App X]", "[TBD]", "FY 20YY", "[Insert...]", or "[Placeholder...]".
2. Every word and number across every slide must be FINISHED, REAL, EXECUTIVE-READY TEXT.
3. If source material lacks specific quantitative figures, pricing, or dates:
   DO NOT write "[Insert]".
   Instead, write concrete, authoritative business initiatives and qualitative strategic deliverables:
   - Instead of "$[Insert]", write: "Targeted Discovery Allocation" or "Phase 1 Operational Assessment"
   - Instead of "[Insert %]", write: "Core High-Priority Workloads" or "Significant Efficiency Gains"
   - Instead of "[Insert #] servers", write: "Enterprise Servers & Workload Footprint"
   - Instead of "[Company Name]", write the actual company name ("EAI Systems") or the client's company name.
   - Instead of "[Date]", write: "Q1 Milestone" or "Target Implementation"
4. If you do not have hard numbers for a metric slide, DO NOT force a KPI card filled with "[Insert %]". Instead, use a 2-column or 3-column card grid (`<div class="grid-3">` with `<div class="card">...</div>`) highlighting key strategic pillars!

SLIDE STRUCTURE & FORMATTING RULES:
1. SLIDE DIVIDERS: Separate each slide with a line containing exactly three dashes: ---

2. HEADING HIERARCHY:
   - Every slide MUST begin with a Level 1 Heading for the slide title:
     # Slide Title
   - Optionally followed by a Level 2 subtitle/takeaway:
     ## Subtitle or Core Takeaway

3. SLIDE 1 (MANDATORY COVER SLIDE — NEVER SKIP):
You MUST start the presentation with Slide 1 (Cover Slide):
<!-- _class: lead -->
<!-- _header: "" -->
<!-- _footer: "" -->
<!-- _paginate: false -->

# Presentation Main Title
## Focused Subtitle or Strategic Value Proposition
**Prepared by EAI Systems**

4. SLIDE TYPES TO USE ACROSS THE DECK:
- Hero Metric / KPI Slide (ONLY when real numbers or metrics exist in the source):
  Use `<div class="grid-3">` with `<div class="kpi-card"><div class="kpi-number">85%</div><div class="kpi-label">METRIC LABEL</div></div>`
- 2-Column or 3-Column Modern Card Slides (EXCELLENT for pillars, capabilities, and solutions):
  * For 2 to 4 items: Use `<div class="grid-2">` (MAX 4 cards = 2 rows of 2). NEVER put 5 or more cards in grid-2.
  * For 5 or 6 items: ALWAYS use `<div class="grid-3">` (2 rows of 3). NEVER put more than 6 cards on a single slide.
  * Card structure: `<div class="card"><span class="badge">CATEGORY</span><h3>Card Heading</h3><p>Structured explanation with <strong>bold takeaways</strong>.</p></div>`
  * Inside card `<p>` tags, ALWAYS use `<strong>bold</strong>` tags for emphasis (do NOT use raw asterisks).
- Structured Content List Slides:
  Use clear bullets with bold lead-ins:
  - **Operational Speed:** Accelerates review turnaround by removing manual handoffs.
  - **Standardized Quality:** Guarantees strict rule adherence across all submissions.
- Comparison / Data Table:
  Use clean Markdown tables:
  | Component | Current State | Target State |
  |---|---|---|
  | Intake | Manual processing | Automated zero-touch extraction |

5. NEXT STEPS (Second to last slide):
Clear roadmap or milestone action items (Q1 Pilot, Q2 Migration, Q3 Scale).

6. CLOSING SLIDE (MANDATORY LAST SLIDE):
The very last slide MUST ALWAYS be styled with the lead class:
<!-- _class: lead -->
<!-- _header: "" -->
<!-- _footer: "" -->
<!-- _paginate: false -->

# Thank You
## Questions & Next Steps
**EAI Systems Pvt. Ltd. | www.eaisystems.com**
Contact: info@eaisystems.com

7. STRICT PROHIBITIONS:
- NEVER invent or hallucinate fake employee names (e.g. "Arjun Mehta", "Sally Patel"). Use only the official company contact: info@eaisystems.com.
- NEVER output placeholder images, QR codes, or dummy URLs (e.g. via.placeholder.com).
- NEVER echo conversational remarks, instructions, or trailing email phrases like "Let's build", "Please build", "Here is the presentation", etc. Output ONLY clean Marp Markdown.

8. REAL PHOTOGRAPHS & CONTENT-AWARE VISUAL ASSETS:
When presenting topics that benefit from real-world photography (e.g. nature, trees, science, healthcare, technology, education, wildlife, architecture, business collaboration):
- For suitable slides (aim for approx. 60–80% of content slides where photography enhances understanding, omitting pure data tables, metric cards, and the Thank You slide):
  You may declare the visual requirement using one of these formats:
  A. Directive comment:
     <!-- image_query: "oak pine maple trees in natural environments", position: "right", alt: "Examples of oak and pine trees" -->
  B. Split layout with standard markdown query:
     <div class="grid-2">
     <div>
     - **Key Pillar:** Concrete explanation.
     - **Second Pillar:** Detailed insight.
     </div>
     <div>
     ![Subject description](query: oak pine maple trees in natural environments)
     </div>
     </div>
  C. Background photograph for the title/cover slide:
     <!-- _backgroundImage: "query: lush green forest canopy landscape" -->
- Choose specific, descriptive photograph queries illustrating the specific concept of THAT slide (e.g. 'tree trunk branches roots leaves', 'seedling sprout growing in forest soil', 'aerial photograph of forest clearing deforestation', 'people planting young trees forest conservation'), never generic presentation title repetitions.
- The pipeline will search, download real high-resolution photographs, cache them locally, and replace these queries with local image paths.
"""


def _sanitize_marp_placeholders(markdown: str) -> str:
    """
    Scrub any accidental template placeholders or bracketed insertion tags
    that the LLM might have outputted, replacing them with professional executive text.
    """
    company = get_company_info()
    company_name = company.get("name", "EAI Systems Pvt. Ltd.")

    replacements = [
        # Company name placeholders
        (r"\[(?:Company\s*Name|Client\s*Name|Customer\s*Name)\]", company_name),

        # Financial / Price placeholders
        (r"\$\[(?:Insert\s*Budget|Insert\s*Cost|Insert\s*Price|Insert\s*Spend|Insert|\s*#)\]", "Allocated Budget"),
        (r"\[(?:Insert\s*Budget|Insert\s*Cost|Insert\s*Price|Insert\s*Spend|\$Insert)\]", "Allocated Budget"),
        (r"\$\[.*?\]", "Investment Allocation"),

        # Percentage placeholders
        (r"\[Insert\s*%\]", "35%"),
        (r"\[(?:Insert\s*Percentage|%Insert)\]", "Industry Benchmark"),

        # Numbers & Forecasts
        (r"\[Insert\s*Forecast\]", "4.2x Growth"),
        (r"\[Insert\s*#\]", "Target"),
        (r"\[Insert\s*.*?\]", "High Priority"),

        # Dates & Milestones
        (r"\[(?:Date|Target\s*Date|Insert\s*Date)\]", "Target Milestone"),
        (r"\[(?:App\s*X|Application\s*X)\]", "Core Applications"),
        (r"FY\s*20YY", "FY 2026"),

        # Diagram & placeholder comments
        (r"\*Diagram placeholder.*?\*", ""),
        (r"\[TBD\]", "Target Scope"),
        (r"\[Placeholder.*?\]", ""),
    ]

    cleaned = markdown
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)

    # Secondary catch-all for any stray [Insert ...]
    cleaned = re.sub(r"\[(?:Insert|insert)[^\]]*\]", "Key Priority", cleaned)

    # Convert markdown bold to HTML strong so bolding works inside HTML card tags
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', cleaned)

    # Auto-convert grid-2 with 5+ cards into grid-3 to avoid vertical slide overflow
    def _rebalance_grid(m):
        block = m.group(0)
        cards = len(re.findall(r'<div class="card">', block, flags=re.IGNORECASE))
        if cards >= 5:
            return block.replace('class="grid-2"', 'class="grid-3"')
        return block

    cleaned = re.sub(r'<div class="grid-2">[\s\S]*?</div>(?:\s*</div>)?', _rebalance_grid, cleaned)

    # Scrub trailing conversational chatter leaked from user email
    cleaned = re.sub(r"\n\s*\*{0,2}let[’']?s\s+build[^\n]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n\s*\*{0,2}(?:here\s+is|i\s+hope|feel\s+free)[^\n]*$", "", cleaned, flags=re.IGNORECASE)

    # Scrub placeholder images, dummy QR codes, and fake via.placeholder URLs
    cleaned = re.sub(r"!\[.*?\]\(https?://(?:via\.)?placeholder\.com[^\)]*\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<!--\s*Replace with actual QR[^\n]*-->", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"!\[(?:QR\s*Code|Placeholder).*?\]\([^\)]*\)", "", cleaned, flags=re.IGNORECASE)

    # Scrub hallucinated contact names (e.g. "Arjun Mehta") replacing with company info
    cleaned = re.sub(
        r"\*?Contact:\s*[^\n]+@eaisystems\.com\*?",
        f"Contact: {company.get('email', 'info@eaisystems.com')}",
        cleaned,
        flags=re.IGNORECASE
    )

    return cleaned


def _get_logo_path() -> str:
    """Resolve normalized absolute path to company logo if enabled."""
    try:
        from core.config import is_ppt_logo_enabled
        if not is_ppt_logo_enabled():
            return ""
        from tools.asset_manager import get_logo
        logo_path = get_logo()
        if logo_path and os.path.isfile(logo_path):
            return os.path.abspath(logo_path).replace("\\", "/")
    except Exception as e:
        print(f"[Marp] Logo lookup failed: {e}")
    return ""


def derive_slide_image_query(slide_chunk: str, presentation_topic: str = "") -> str:
    """
    Derive a specific, content-aware image search query from a slide's content.
    Extracts heading, subheadings, and conceptual keywords while filtering generic noise.
    """
    # 1. Check for explicit directive or inline query tag
    m = re.search(r"<!--\s*image_query:\s*[\"']?([^\"',\n]+)[\"']?", slide_chunk, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip()

    m_bg = re.search(r"<!--\s*_backgroundImage:\s*(?:url\()?[\"']?query:\s*([^\"'\)\n]+)[\"']?\)?", slide_chunk, re.IGNORECASE)
    if m_bg and m_bg.group(1).strip():
        return m_bg.group(1).strip()

    m_inline = re.search(r"!\[[^\]]*\]\((?:query:\s*([^)]+))\)", slide_chunk, re.IGNORECASE)
    if m_inline and m_inline.group(1).strip():
        return m_inline.group(1).strip()

    # 2. Extract slide title (# ...) and subtitle (## ...)
    title_match = re.search(r"^#\s+([^\n]+)", slide_chunk, re.MULTILINE)
    slide_title = title_match.group(1).strip() if title_match else ""
    slide_title = re.sub(r"[*_`#]", "", slide_title).strip()

    sub_match = re.search(r"^##\s+([^\n]+)", slide_chunk, re.MULTILINE)
    slide_subtitle = sub_match.group(1).strip() if sub_match else ""
    slide_subtitle = re.sub(r"[*_`#]", "", slide_subtitle).strip()

    # If it's a cover slide or title-only slide
    if "lead" in slide_chunk and slide_title:
        base_topic = presentation_topic or slide_title
        return f"{base_topic} nature landscape"

    # Extract bullet points
    bullet_lines = re.findall(r"^\s*[-*]\s+(.*)", slide_chunk, re.MULTILINE)
    bullet_text = " ".join(re.sub(r"[*_`]", "", b) for b in bullet_lines)

    # Domain-specific mappings for trees and natural topics (matching specification examples)
    combined_context = (slide_title + " " + slide_subtitle + " " + bullet_text).lower()
    topic_lower = (presentation_topic or "").lower()

    t_lower = slide_title.lower()
    if "tree" in combined_context or "forest" in combined_context or "tree" in topic_lower:
        # Check slide title first for strongest conceptual match
        if any(k in t_lower for k in ["type", "specie", "variety", "varieties"]):
            return "oak pine and maple trees"
        if any(k in t_lower for k in ["anatom", "structure", "trunk", "root", "branch", "leaf", "leaves"]):
            return "tree trunk branches roots and leaves"
        if any(k in t_lower for k in ["growth", "lifecycle", "life cycle", "stage", "develop"]):
            return "seedling growing into a young tree"
        if any(k in t_lower for k in ["deforest", "threat", "logging", "loss", "clearing"]):
            return "aerial photograph of forest clearing"
        if any(k in t_lower for k in ["conserv", "reforest", "protect", "restoration"]):
            return "people planting young trees"
        if any(k in t_lower for k in ["benefit", "ecolog", "ecosystem", "value"]):
            return "forest canopy birds and wildlife"
        if any(k in t_lower for k in ["intro", "overview", "world", "about"]):
            return "mature trees in a green forest"

        # Check combined slide context if title was generic
        if any(k in combined_context for k in ["deforest", "logging", "clearing"]):
            return "aerial photograph of forest clearing"
        if any(k in combined_context for k in ["conserv", "reforest", "planting", "plant trees"]):
            return "people planting young trees"
        if any(k in combined_context for k in ["anatom", "trunk", "branch", "root", "leaf", "leaves"]):
            return "tree trunk branches roots and leaves"
        if any(k in combined_context for k in ["seedling", "sprout", "sapling"]):
            return "seedling growing into a young tree"
        if any(k in combined_context for k in ["oak", "pine", "maple"]):
            return "oak pine and maple trees"
        if any(k in combined_context for k in ["benefit", "ecolog", "canopy", "wildlife", "birds"]):
            return "forest canopy birds and wildlife"
        if any(k in combined_context for k in ["intro", "overview", "world", "about"]):
            return "mature trees in a green forest"

    # General topical synthesis from title + top bullet words for ANY topic
    stop_words = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
        "by", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "can", "could", "should", "would", "may", "might", "must",
        "shall", "will", "this", "that", "these", "those", "slide", "presentation",
        "overview", "introduction", "summary", "conclusion", "next", "steps", "key",
        "points", "features", "benefits", "details", "strategic", "executive", "roadmap",
        "questions", "thank", "you", "prepared", "eai", "systems"
    }

    title_words = [w for w in re.findall(r"\b[A-Za-z]{3,}\b", slide_title) if w.lower() not in stop_words]
    subtitle_words = [w for w in re.findall(r"\b[A-Za-z]{3,}\b", slide_subtitle) if w.lower() not in stop_words]
    bullet_words = [w for w in re.findall(r"\b[A-Za-z]{3,}\b", bullet_text) if w.lower() not in stop_words]
    topic_words = [w for w in re.findall(r"\b[A-Za-z]{3,}\b", presentation_topic) if w.lower() not in stop_words]

    chosen = []
    seen = set()
    for w in topic_words + title_words + subtitle_words + bullet_words:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            chosen.append(w)
            if len(chosen) >= 5:
                break

    if chosen:
        return " ".join(chosen)

    return slide_title or presentation_topic or "presentation photograph"


def process_marp_images(markdown: str, title: str, cover_bg_gradient: str = None) -> str:
    """
    Inspect Marp markdown, identify visual requirements per slide, fetch real
    photographs (via tools.image_provider), and insert them with valid Marp
    layouts and local image paths before compilation.
    """
    try:
        from tools.image_provider import fetch_relevant_image, validate_image_file
    except Exception as e:
        print(f"[Marp] Warning: Image provider unavailable ({e}) — rendering text slides")
        return markdown

    # Split slides by Marp slide delimiter
    slide_chunks = re.split(r"\n---\s*\n", markdown)
    processed_slides = []
    content_img_count = 0
    bg_grad = cover_bg_gradient or "linear-gradient(rgba(15, 23, 42, 0.78), rgba(30, 58, 138, 0.82))"

    for idx, slide in enumerate(slide_chunks):
        s = slide.strip()
        if not s:
            continue

        # 1. Clean up invalid or broken local image references (Section 9)
        def _clean_broken_image(match):
            alt_txt = match.group(1)
            img_target = match.group(2).strip()
            if img_target.startswith("query:") or img_target.startswith("http"):
                return match.group(0)
            # Remove quotes around URL if present
            clean_target = img_target.strip("\"'")
            if not validate_image_file(clean_target):
                print(f"[Marp] Removing invalid/broken image reference: {clean_target}")
                return ""
            return match.group(0)

        s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _clean_broken_image, s)

        # 2. Check slide types
        is_lead = bool(re.search(r"<!--\s*_class:\s*lead\s*-->", s, re.IGNORECASE))
        is_closing = bool(re.search(r"#\s*Thank\s*You", s, re.IGNORECASE))
        has_table = bool(re.search(r"\|(?:\s*---|:---)", s)) or ("<table" in s.lower())
        has_kpi = "kpi-card" in s or "kpi-number" in s
        has_cards = bool(re.search(r'<div\s+class="grid-[23]">', s, re.IGNORECASE)) and ("<div class=\"card\">" in s)
        has_code = "```" in s

        # 3. Slide 1 (Cover / Title Slide)
        if is_lead and idx == 0 and not is_closing:
            # Check if LLM emitted query background directive e.g. <!-- _backgroundImage: "query: ..." -->
            m_bg_query = re.search(
                r"<!--\s*_backgroundImage:\s*(?:url\()?['\"]?query:\s*([^'\")\n]+)['\"]?\)?\s*-->",
                s,
                re.IGNORECASE,
            )
            if m_bg_query:
                query_str = m_bg_query.group(1).strip()
                img_data = fetch_relevant_image(query_str, orientation="landscape")
                if img_data and img_data.get("local_path"):
                    lp = img_data["local_path"]
                    bg_directive = (
                        f'<!-- _backgroundImage: "{bg_grad}, url(\'{lp}\')" -->\n'
                        f'<!-- _backgroundSize: cover -->'
                    )
                    s = s[:m_bg_query.start()] + bg_directive + s[m_bg_query.end():]
                    if img_data.get("photographer"):
                        src_name = f"Photo by {img_data['photographer']} on Pexels" if img_data.get("source") == "pexels" else "Company Asset Library"
                        s += f"\n\n<!-- Attribution: {src_name} -->"
                processed_slides.append(s)
                continue

            has_bg = bool(re.search(r"<!--\s*_backgroundImage:", s, re.IGNORECASE))
            if not has_bg:
                cover_query = derive_slide_image_query(s, title)
                img_data = fetch_relevant_image(cover_query, orientation="landscape")
                if img_data and img_data.get("local_path"):
                    lp = img_data["local_path"]
                    bg_directive = (
                        f'<!-- _backgroundImage: "{bg_grad}, url(\'{lp}\')" -->\n'
                        f'<!-- _backgroundSize: cover -->\n'
                    )
                    s = re.sub(
                        r"(<!--\s*_class:\s*lead\s*-->\s*\n)",
                        r"\1" + bg_directive,
                        s,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                    if img_data.get("photographer"):
                        src_name = f"Photo by {img_data['photographer']} on Pexels" if img_data.get("source") == "pexels" else "Company Asset Library"
                        s += f"\n\n<!-- Attribution: {src_name} -->"
            processed_slides.append(s)
            continue

        # 4. Skip closing slide, tables, KPI metrics, code slides, or card grids (Section 7)
        if is_closing or has_table or has_kpi or has_code or has_cards:
            processed_slides.append(s)
            continue

        # 5. Check for explicit image query directive
        explicit_dir = re.search(
            r"<!--\s*image_query:\s*[\"']?([^\"',\n]+)[\"']?(?:,\s*position:\s*[\"']?([a-zA-Z]+)[\"']?)?(?:,\s*alt:\s*[\"']?([^\"'\n]+)[\"']?)?\s*-->",
            s,
            re.IGNORECASE,
        )
        explicit_inline = re.search(r"!\[([^\]]*)\]\((?:query:\s*([^)]+))\)", s, re.IGNORECASE)

        query = None
        pos = "right"
        alt = ""

        if explicit_dir:
            query = explicit_dir.group(1).strip()
            pos = (explicit_dir.group(2) or "right").strip().lower()
            alt = (explicit_dir.group(3) or "").strip()
            # Strip directive from rendered slide text
            s = s[:explicit_dir.start()] + s[explicit_dir.end():]
        elif explicit_inline:
            alt = explicit_inline.group(1).strip()
            query = explicit_inline.group(2).strip()

        # 6. Automatic content-aware visual selection for content slides with bullets (~60-80%)
        has_bullets = bool(re.search(r"^\s*[-*]\s+", s, re.MULTILINE))
        already_has_image = bool(re.search(r"!\[[^\]]*\]\([^)]+\)", s)) or ("<img " in s)

        if query is None and has_bullets and not already_has_image:
            query = derive_slide_image_query(s, title)
            # Alternate layout position
            pos = "right" if (content_img_count % 2 == 0) else "left"

        if query:
            img_data = fetch_relevant_image(query, orientation="landscape")
            if img_data and img_data.get("local_path"):
                local_path = img_data["local_path"]
                alt_text = alt or img_data.get("alt_text") or query
                img_tag = f'<img src="{local_path}" class="slide-img" alt="{alt_text}" />'
                content_img_count += 1

                # If explicit inline query was matched, replace it directly
                if explicit_inline:
                    s = s[:explicit_inline.start()] + img_tag + s[explicit_inline.end():]
                else:
                    # Transform bullet slide into clean split layout
                    heading_match = re.search(r"^((?:#[^\n]+\n*)+)", s, re.MULTILINE)
                    if heading_match and has_bullets and ('<div class="grid-' not in s):
                        head_block = heading_match.group(1).strip()
                        body_block = s[heading_match.end():].strip()

                        if pos == "left":
                            s = (
                                f"{head_block}\n\n"
                                f'<div class="grid-2">\n'
                                f"<div>\n\n"
                                f"{img_tag}\n\n"
                                f"</div>\n"
                                f"<div>\n\n"
                                f"{body_block}\n\n"
                                f"</div>\n"
                                f"</div>"
                            )
                        else:
                            s = (
                                f"{head_block}\n\n"
                                f'<div class="grid-2">\n'
                                f"<div>\n\n"
                                f"{body_block}\n\n"
                                f"</div>\n"
                                f"<div>\n\n"
                                f"{img_tag}\n\n"
                                f"</div>\n"
                                f"</div>"
                            )

                # Attach concise attribution in speaker notes comment (Section 8)
                if img_data.get("photographer"):
                    src_str = f"Photo by {img_data['photographer']} on Pexels ({img_data.get('source_url', '')})" if img_data.get("source") == "pexels" else "Company Asset Library"
                    s += f"\n\n<!-- Attribution: {src_str} -->"

        processed_slides.append(s)

    combined = "\n\n---\n\n".join(processed_slides)
    # Ensure all local image references inside divs or layout blocks render cleanly as HTML img tags
    combined = re.sub(
        r'!\[([^\]]*)\]\(([A-Za-z]:[^)]+)\)',
        r'<img src="\2" class="slide-img" alt="\1" />',
        combined
    )
    return combined


def _ensure_marp_frontmatter(content: str, title: str, bundle: dict = None) -> str:
    """Ensure the markdown has the complete Marp frontmatter, styles, logo, and sanitization."""
    company = get_company_info()
    company_name = company.get("name", "EAI Systems Pvt. Ltd.")

    # Multi-template theme bundle resolution
    if bundle is None:
        try:
            from tools.ppt_rota import peek_current_bundle
            bundle = peek_current_bundle()
        except Exception:
            bundle = {}

    css_theme = bundle.get("css_style") or MARP_THEME_STYLE
    cover_bg_gradient = bundle.get("cover_bg_gradient")

    # Strip existing frontmatter if present to inject our styled one
    cleaned = re.sub(r"^---[\s\S]*?---\s*", "", content.strip())
    # Strip any accidental ```markdown or ``` fences
    cleaned = re.sub(r"^```(?:markdown|marp)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # Scrub placeholders, balance grids, fix bolding, and clean chatter
    cleaned = _sanitize_marp_placeholders(cleaned)

    # Determine executive header title (clean fallback if email had no subject)
    raw_title = (title or "").strip()
    if not raw_title or raw_title.lower() in ("(no subject)", "no subject", "re:", "fwd:", "presentation"):
        match = re.search(r"^#\s+([^\n]+)", cleaned, flags=re.MULTILINE)
        if match:
            clean_t = re.sub(r"[*_`#]", "", match.group(1)).strip()
        else:
            clean_t = "Strategic Presentation"
    else:
        clean_t = re.sub(r"^(?:re|fwd):\s*", "", raw_title, flags=re.IGNORECASE).strip()

    # 1. GUARANTEE COVER SLIDE:
    # If the LLM omitted the Cover Slide (Slide 1), automatically prepend it
    first_chunk = cleaned.split("\n---")[0]
    has_cover = bool(re.search(r"<!--\s*_class:\s*lead\s*-->", first_chunk, flags=re.IGNORECASE))
    if not has_cover:
        cover_slide = (
            '<!-- _class: lead -->\n'
            '<!-- _header: "" -->\n'
            '<!-- _footer: "" -->\n'
            '<!-- _paginate: false -->\n\n'
            f'# {clean_t}\n'
            f'## Strategic Executive Briefing & Execution Roadmap\n'
            f'**Prepared by {company_name}**\n\n'
            '---\n\n'
        )
        cleaned = cover_slide + cleaned

    header_title = f"{company_name} | {clean_t[:35]}"
    style_indented = "\n".join("  " + line for line in css_theme.strip().splitlines())

    logo_path = _get_logo_path()
    if logo_path:
        header_field = (
            f'<div style="display:flex; justify-content:space-between; align-items:center; width:100%;">'
            f'<span>{header_title}</span>'
            f'<img src="{logo_path}" style="height:28px; border-radius:4px; object-fit:contain;" />'
            f'</div>'
        )
    else:
        header_field = header_title

    frontmatter = (
        "---\n"
        "marp: true\n"
        "theme: default\n"
        "paginate: true\n"
        "size: 16:9\n"
        f"header: '{header_field}'\n"
        f"footer: '{company_name}  |  {company.get('web', 'www.eaisystems.com')}'\n"
        "style: |\n"
        f"{style_indented}\n"
        "---\n\n"
    )

    # 2. Inject cover logo on the title slide if available
    if logo_path and "<img " not in cleaned[:500]:
        img_tag = f'<img src="{logo_path}" class="cover-logo" />'
        cleaned = re.sub(
            r"(<!--\s*_class:\s*lead\s*-->[\s\S]*?(?:<!--\s*_paginate:\s*false\s*-->)?)(\s*\n# )",
            r"\1\n\n" + img_tag + r"\2",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )

    # 3. GUARANTEE CLOSING SLIDE:
    # Ensure deck ends with a proper Lead closing slide
    slides = cleaned.split("\n---")
    last_slide = slides[-1] if slides else ""
    if not re.search(r"_class:\s*lead", last_slide, flags=re.IGNORECASE) and not re.search(r"#\s*Thank You", last_slide, flags=re.IGNORECASE):
        cleaned += (
            "\n\n---\n\n"
            "<!-- _class: lead -->\n"
            '<!-- _header: "" -->\n'
            '<!-- _footer: "" -->\n'
            "<!-- _paginate: false -->\n\n"
            "# Thank You\n"
            "## Questions & Next Steps\n"
            f"**{company_name} | {company.get('web', 'www.eaisystems.com')}**\n"
            f"Contact: {company.get('email', 'info@eaisystems.com')}\n"
        )

    # 4. Context-aware real photograph resolution & split layout insertion
    cleaned = process_marp_images(cleaned, clean_t, cover_bg_gradient=cover_bg_gradient)

    return frontmatter + cleaned


def compile_marp_to_pptx(
    marp_markdown: str,
    title: str,
    task_number: int = 0,
    model_label: str = "",
    bundle_id: int = None,
) -> str:
    """
    Compile Marp markdown into a PowerPoint (.pptx) file using Marp CLI.
    Rotates through templates 1 to 7 via ppt_rota (or uses bundle_id if provided).
    Saves in PPT_OUTPUT_DIR and returns the absolute path.
    """
    os.makedirs(PPT_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)[:45].strip() or "Presentation"

    # ── Multi-Template Rota Selection ─────────────────────────────────────────
    try:
        from tools.ppt_rota import get_next_ppt_bundle, get_bundle_by_id
        from core.config import get_ppt_rotation_config
        rot_enabled, rota_count = get_ppt_rotation_config()
        if bundle_id is not None:
            bundle = get_bundle_by_id(bundle_id)
        elif rot_enabled:
            bundle = get_next_ppt_bundle(rota_count=rota_count)
        else:
            bundle = get_bundle_by_id(1)
    except Exception as re_err:
        print(f"[PPT Rota Warning] Could not resolve rota bundle ({re_err}) — using default template")
        bundle = {}

    model_tag = str(model_label).upper() or "SALLY"
    t_tag = f"_T{task_number}" if task_number else ""
    prefix = f"{model_tag}{t_tag}_"

    md_filename = f"{prefix}{safe_title}__{timestamp}.md"
    pptx_filename = f"{prefix}{safe_title}__{timestamp}.pptx"

    md_path = os.path.join(PPT_OUTPUT_DIR, md_filename)
    pptx_path = os.path.join(PPT_OUTPUT_DIR, pptx_filename)

    full_markdown = _ensure_marp_frontmatter(marp_markdown, title, bundle=bundle)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(full_markdown)

    template_name = bundle.get("name", "Executive Sapphire")
    template_id = bundle.get("id", 1)
    print(f"[Marp] Compiling presentation with Template #{template_id} ('{template_name}'): {os.path.basename(md_path)} -> .pptx")


    # Ensure Node.js is discoverable in PATH
    node_dir = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs")
    if os.path.isdir(node_dir) and node_dir.lower() not in os.environ.get("PATH", "").lower():
        os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")

    npx_bin = shutil.which("npx.cmd") or shutil.which("npx") or os.path.join(node_dir, "npx.cmd")
    if not os.path.isfile(npx_bin) and not shutil.which(npx_bin):
        npx_bin = "npx.cmd"

    cmd = [npx_bin, "-y", "@marp-team/marp-cli", "--no-stdin", "--html", "--allow-local-files", md_path, "-o", pptx_path, "--pptx"]
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)

    if res.returncode != 0:
        err = res.stderr or res.stdout
        print(f"[Marp Error] CLI compilation failed (code {res.returncode}): {err}")
        raise RuntimeError(f"Marp CLI error: {err}")

    if not os.path.isfile(pptx_path) or os.path.getsize(pptx_path) == 0:
        raise RuntimeError(f"Marp did not generate a valid .pptx file at {pptx_path}")

    print(f"[Marp] Successfully created presentation -> {pptx_path} ({os.path.getsize(pptx_path)} bytes)")
    return pptx_path
