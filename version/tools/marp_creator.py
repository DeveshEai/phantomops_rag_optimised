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


def _ensure_marp_frontmatter(content: str, title: str) -> str:
    """Ensure the markdown has the complete Marp frontmatter, styles, logo, and sanitization."""
    company = get_company_info()
    company_name = company.get("name", "EAI Systems Pvt. Ltd.")

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
    style_indented = "\n".join("  " + line for line in MARP_THEME_STYLE.strip().splitlines())

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

    return frontmatter + cleaned


def compile_marp_to_pptx(
    marp_markdown: str,
    title: str,
    task_number: int = 0,
    model_label: str = "",
) -> str:
    """
    Compile Marp markdown into a PowerPoint (.pptx) file using Marp CLI.
    Saves in PPT_OUTPUT_DIR and returns the absolute path.
    """
    os.makedirs(PPT_OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)[:45].strip() or "Presentation"

    model_tag = str(model_label).upper() or "SALLY"
    t_tag = f"_T{task_number}" if task_number else ""
    prefix = f"{model_tag}{t_tag}_"

    md_filename = f"{prefix}{safe_title}__{timestamp}.md"
    pptx_filename = f"{prefix}{safe_title}__{timestamp}.pptx"

    md_path = os.path.join(PPT_OUTPUT_DIR, md_filename)
    pptx_path = os.path.join(PPT_OUTPUT_DIR, pptx_filename)

    full_markdown = _ensure_marp_frontmatter(marp_markdown, title)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(full_markdown)

    print(f"[Marp] Compiling presentation: {os.path.basename(md_path)} -> .pptx")

    cmd = ["npx.cmd", "-y", "@marp-team/marp-cli", "--no-stdin", "--html", "--allow-local-files", md_path, "-o", pptx_path, "--pptx"]
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)

    if res.returncode != 0:
        err = res.stderr or res.stdout
        print(f"[Marp Error] CLI compilation failed (code {res.returncode}): {err}")
        raise RuntimeError(f"Marp CLI error: {err}")

    if not os.path.isfile(pptx_path) or os.path.getsize(pptx_path) == 0:
        raise RuntimeError(f"Marp did not generate a valid .pptx file at {pptx_path}")

    print(f"[Marp] Successfully created presentation -> {pptx_path} ({os.path.getsize(pptx_path)} bytes)")
    return pptx_path
