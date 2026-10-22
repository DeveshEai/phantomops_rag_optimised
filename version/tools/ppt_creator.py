"""
tools/ppt_creator.py — Branded PowerPoint Creator

Opens one of four company templates (LLM selects by key), wipes its content
slides to keep only the slide master and layouts, then adds new slides using
textboxes for title and bullets. This works regardless of the template's
custom placeholder structure.

Templates (assets/templates/):
  sales_blue   - Template 1: client-facing sales (blue)
  sales_orange - Template 2: sales + tech solutions (orange)
  marketing    - Template 3: marketing and events (dark)
  internal     - Template 4: internal / technical (light)
"""

import os
import json
from datetime import datetime
from langchain_core.tools import tool
from core.config import PPT_OUTPUT_DIR


# ── Layout constants (13.33 x 7.5 inch widescreen) ───────────────────────────
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

_SLIDE_W = Inches(13.33)
_SLIDE_H = Inches(7.50)

# Title slide text zones
_TITLE_LEFT   = Inches(0.8)
_TITLE_TOP    = Inches(2.8)
_TITLE_W      = Inches(9.0)
_TITLE_H      = Inches(1.2)

_SUBTITLE_LEFT = Inches(0.8)
_SUBTITLE_TOP  = Inches(4.1)
_SUBTITLE_W    = Inches(9.0)
_SUBTITLE_H    = Inches(0.7)

# Content slide text zones
_CTITLE_LEFT  = Inches(0.6)
_CTITLE_TOP   = Inches(0.3)
_CTITLE_W     = Inches(11.5)
_CTITLE_H     = Inches(0.9)

_CBODY_LEFT   = Inches(0.6)
_CBODY_TOP    = Inches(1.35)
_CBODY_W      = Inches(11.5)
_CBODY_H      = Inches(5.8)

# Logo (top-right)
_LOGO_H       = Inches(0.45)
_LOGO_MARGIN  = Inches(0.15)

_NAVY = (18, 38, 74)
_BLUE = (0, 91, 170)
_LIGHT_BLUE = (229, 241, 252)
_ORANGE = (242, 114, 43)
_TEXT = (35, 38, 47)
_MUTED = (98, 105, 118)
_WHITE = (255, 255, 255)
_LINE = (218, 225, 235)


# ── Image lookup ──────────────────────────────────────────────────────────────

def _find_image(query: str) -> str | None:
    """
    Return the best-matching image path from the indexed asset library.
    Delegates to asset_manager which uses vision-LLM-generated tags for scoring.
    """
    if not query:
        return None
    try:
        from tools.asset_manager import get_best_image
        return get_best_image(query)
    except Exception as e:
        print(f"[PPT] Image lookup failed: {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scrub_master_text(prs, old: str, new: str) -> None:
    """Replace any hard-coded text in slide masters and layouts (e.g. PhantomOps branding)."""
    for master in prs.slide_masters:
        for shape in list(master.shapes) + [s for layout in master.slide_layouts for s in layout.shapes]:
            if not hasattr(shape, "text_frame"):
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if old in run.text:
                        run.text = run.text.replace(old, new)


def _initialize_presentation():
    """
    Create a fully coded blank presentation — no template file needed.
    All slide design (backgrounds, fonts, colors, shapes) is drawn
    programmatically by the slide builder functions.
    """
    from pptx import Presentation
    from pptx.oxml.ns import qn as _qn
    import lxml.etree as _etree

    prs = Presentation()
    prs.slide_width  = _SLIDE_W
    prs.slide_height = _SLIDE_H

    # Set Calibri as the default theme font on the slide master
    _FONT = "Calibri"
    try:
        for master in prs.slide_masters:
            txStyles = master.element.find(
                "{http://schemas.openxmlformats.org/presentationml/2006/main}txStyles"
            )
            if txStyles is not None:
                for defRPr in txStyles.iter(
                    "{http://schemas.openxmlformats.org/drawingml/2006/main}defRPr"
                ):
                    ln = defRPr.find(
                        "{http://schemas.openxmlformats.org/drawingml/2006/main}latin"
                    )
                    if ln is None:
                        ln = _etree.SubElement(
                            defRPr,
                            "{http://schemas.openxmlformats.org/drawingml/2006/main}latin"
                        )
                    ln.set("typeface", _FONT)
    except Exception:
        pass  # font override is best-effort; slide builders set fonts explicitly anyway

    print("[PPT] Initialized coded presentation (no template file)")
    return prs


def _open_template(template_query: str):
    """
    Open an existing company template (.pptx) or custom template,
    clearing placeholder slides while preserving master layouts and themes.
    Falls back to a coded blank presentation if template is not found.
    """
    from pptx import Presentation
    from tools.asset_manager import get_best_template

    template_path = None
    if template_query and os.path.isfile(template_query) and template_query.lower().endswith(".pptx"):
        template_path = template_query
    elif template_query and template_query.lower() not in ("new", "none", "scratch", "blank", "coded"):
        template_path = get_best_template(template_query)

    if template_path and os.path.isfile(template_path):
        try:
            prs = Presentation(template_path)
            _clear_slides(prs)
            print(f"[PPT] Using existing template: {os.path.basename(template_path)}")
            return prs
        except Exception as e:
            print(f"[PPT Warning] Could not open template '{template_path}': {e}")

    print("[PPT] Initialized coded presentation (no template file)")
    return _initialize_presentation()


def _clear_slides(prs) -> None:
    """Remove all content slides while preserving slide master and layouts."""
    sldIdLst = prs.slides._sldIdLst
    for _ in range(len(sldIdLst)):
        rId = sldIdLst[0].get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        prs.part.drop_rel(rId)
        del sldIdLst[0]


def _best_layout(prs, preferred_index: int):
    """Return a slide layout by index, clamped to what the template has."""
    layouts = prs.slide_layouts
    return layouts[min(preferred_index, len(layouts) - 1)]


def _blank_layout(prs):
    layouts = prs.slide_layouts
    return layouts[6] if len(layouts) > 6 else _best_layout(prs, 0)


def _fill(shape, color):
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*color)
    shape.line.color.rgb = RGBColor(*color)


def _add_rect(slide, left, top, width, height, fill, line=None, radius=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*fill)
    shape.line.color.rgb = RGBColor(*(line or fill))
    return shape


def _add_textbox(slide, left, top, width, height, text, font_size, bold=False,
                 color=_TEXT, wrap=True, align=PP_ALIGN.LEFT):
    """Add a textbox with a single run of styled text."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = wrap
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    tf.margin_left = Inches(0.04)
    tf.margin_right = Inches(0.04)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name  = "Calibri"
    run.font.size  = Pt(font_size)
    run.font.bold  = bold
    run.font.color.rgb = RGBColor(*color)
    return txBox


def _text_size(text: str, base: float, minimum: float = 11) -> float:
    """Choose a smaller font when generated prose gets dense."""
    length = len(text or "")
    if length > 170:
        return max(minimum, base - 4)
    if length > 125:
        return max(minimum, base - 3)
    if length > 90:
        return max(minimum, base - 2)
    return base


def _set_shape_text(shape, text: str, font_size: float, color=_TEXT):
    tf = shape.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    tf.margin_left = Inches(0.18)
    tf.margin_right = Inches(0.18)
    tf.margin_top = Inches(0.14)
    tf.margin_bottom = Inches(0.12)
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.name  = "Calibri"
    run.font.size  = Pt(font_size)
    run.font.color.rgb = RGBColor(*color)


_CARD_BG = (0xE8, 0xEE, 0xF8)   # soft blue-gray, clearly visible on white


def _add_card(slide, left, top, width, height, text: str, number: str | None = None,
              font_size: float = 15):
    card = _add_rect(slide, left, top, width, height, _CARD_BG, (0xB0, 0xBD, 0xD8), radius=True)
    prefix = f"{number}  " if number else ""
    _set_shape_text(card, f"{prefix}{text}", font_size=font_size)
    return card


def _add_bullets(slide, left, top, width, height, bullets, font_size=16,
                 color=_TEXT):
    """Add a textbox with bullet points."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    tf.margin_left = Inches(0.08)
    tf.margin_right = Inches(0.08)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    bullets = _clean_bullets(bullets)
    total_chars = sum(len(b) for b in bullets)
    actual_size = font_size
    if total_chars > 560:
        actual_size = max(12, font_size - 3)
    elif total_chars > 420:
        actual_size = max(13, font_size - 2)
    bullet_char = chr(8226)
    for i, bullet in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(5 if total_chars > 420 else 9)
        run = p.add_run()
        run.text = f"{bullet_char}  {bullet}"
        run.font.name  = "Calibri"
        run.font.size  = Pt(actual_size)
        run.font.color.rgb = RGBColor(*color)


def _add_logo(slide, prs) -> None:
    """
    Stamp the EAI logo in the top-right corner.
    Uses the image from the asset library if available;
    falls back to a branded text box so the logo always shows.
    """
    from tools.asset_manager import get_logo
    logo_path = get_logo()

    if logo_path and os.path.isfile(logo_path):
        try:
            pic = slide.shapes.add_picture(
                logo_path, left=0, top=0, height=_LOGO_H
            )
            pic.left = int(prs.slide_width - pic.width - _LOGO_MARGIN)
            pic.top  = int(_LOGO_MARGIN)
            return
        except Exception as e:
            print(f"[PPT] Logo image error: {e} — using text logo")

    # Text-based logo fallback — always renders, no image file needed
    logo_w = Inches(1.6)
    logo_h = Inches(0.38)
    logo_l = prs.slide_width - logo_w - _LOGO_MARGIN
    logo_t = _LOGO_MARGIN

    # Navy background pill
    box = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, logo_l, logo_t, logo_w, logo_h
    )
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(*_NAVY)
    box.line.fill.background()

    tf = box.text_frame
    tf.word_wrap = False
    tf.margin_left   = Inches(0.1)
    tf.margin_right  = Inches(0.1)
    tf.margin_top    = Inches(0.04)
    tf.margin_bottom = Inches(0.04)

    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER

    from core.config import get_company_info
    short = get_company_info()["short"]
    first, _, rest = short.partition(" ")

    # First word in bold orange
    r1 = p.add_run()
    r1.text = first
    r1.font.bold  = True
    r1.font.size  = Pt(13)
    r1.font.color.rgb = RGBColor(*_ORANGE)

    # Remainder (if any) in white
    if rest:
        r2 = p.add_run()
        r2.text = f" {rest}"
        r2.font.bold  = True
        r2.font.size  = Pt(10)
        r2.font.color.rgb = RGBColor(*_WHITE)


def _add_footer(slide, prs, index: int) -> None:
    from core.config import get_company_info
    _company = get_company_info()
    _add_rect(slide, Inches(0), Inches(7.18), prs.slide_width, Inches(0.04), _LINE)
    _add_textbox(
        slide, Inches(0.65), Inches(7.24), Inches(5.5), Inches(0.18),
        f"{_company['name']} | {_company['web']}", 7.5, color=_MUTED
    )
    _add_textbox(
        slide, Inches(12.25), Inches(7.24), Inches(0.45), Inches(0.18),
        str(index), 7.5, color=_MUTED, align=PP_ALIGN.RIGHT
    )


def _clean_bullets(bullets) -> list[str]:
    cleaned = []
    for item in bullets or []:
        text = " ".join(str(item).replace("\n", " ").split()).strip(" -•")
        if text:
            cleaned.append(text)
    return cleaned


def _needs_list_layout(bullets: list[str]) -> bool:
    bullets = _clean_bullets(bullets)
    if not bullets:
        return False
    return (
        len(bullets) >= 4
        or sum(len(b) for b in bullets) > 390
        or any(len(b) > 120 for b in bullets)
    )


def _normalize_slides(slides_content, honor_exact_count: bool = False) -> list[dict]:
    """
    honor_exact_count: True when the sender explicitly requested a specific
    number of slides (generate_presentation's slide_count). The LLM already
    honors that count in the JSON it returns — splitting a bullet-heavy slide
    here would silently produce MORE slides than were asked for (a slide with
    "more information" simply has more bullets, not a request for an extra
    slide). _add_bullets() already shrinks font size for dense slides, and
    ppt_reviewer's structural check + one fix round still catches genuine
    overflow, so skipping the split doesn't trade correctness for readability.
    """
    normalized = []
    for slide in slides_content or []:
        heading     = str(slide.get("heading", "Untitled")).strip() or "Untitled"
        bullets     = _clean_bullets(slide.get("bullets", []))
        layout      = slide.get("layout", "auto")
        image_query = slide.get("image_query", "")
        table       = slide.get("table")   # preserve table key — never strip it

        if table or not bullets or honor_exact_count:
            # Table slides, empty slides, and (when an exact count was
            # requested) every slide pass through as a single slide.
            normalized.append({
                "heading":     heading,
                "bullets":     bullets,
                "layout":      layout,
                "image_query": image_query,
                "table":       table,
            })
            continue

        # No hard count requirement — split bullet-heavy slides across
        # multiple slides for readability (max 4 per slide).
        for i in range(0, len(bullets), 4):
            suffix = "" if i == 0 else f" ({i // 4 + 1})"
            normalized.append({
                "heading":     f"{heading}{suffix}",
                "bullets":     bullets[i:i + 4],
                "layout":      layout,
                "image_query": image_query if i == 0 else "",
                "table":       None,
            })
    return normalized


def _cover_slide(prs, title: str, subtitle: str):
    from core.config import get_company_info
    _company = get_company_info()
    slide = prs.slides.add_slide(_blank_layout(prs))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, _WHITE)
    _add_rect(slide, Inches(0), Inches(0), Inches(4.1), prs.slide_height, _NAVY)
    _add_rect(slide, Inches(3.75), Inches(0), Inches(0.18), prs.slide_height, _ORANGE)
    _add_textbox(slide, Inches(0.65), Inches(0.55), Inches(2.8), Inches(0.35),
                 "CLIENT PROPOSITION", 10, bold=True, color=(175, 215, 255))
    _add_textbox(slide, Inches(4.55), Inches(2.15), Inches(7.7), Inches(1.1),
                 title, 38, bold=True, color=_NAVY)
    _add_textbox(slide, Inches(4.58), Inches(3.28), Inches(6.6), Inches(0.65),
                 subtitle, 18, color=_TEXT)
    _add_textbox(slide, Inches(4.58), Inches(4.35), Inches(6.9), Inches(0.9),
                 f"Prepared by {_company['name']}  |  {_company['web']}", 18, color=_MUTED)
    _add_rect(slide, Inches(4.58), Inches(5.72), Inches(2.0), Inches(0.08), _ORANGE)
    _add_logo(slide, prs)
    return slide


def _section_slide(prs, heading: str, kicker: str, index: int):
    slide = prs.slides.add_slide(_blank_layout(prs))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, _NAVY)
    _add_rect(slide, Inches(0.72), Inches(1.25), Inches(1.2), Inches(0.08), _ORANGE)
    _add_textbox(slide, Inches(0.72), Inches(1.55), Inches(10.8), Inches(0.5),
                 kicker.upper(), 11, bold=True, color=(175, 215, 255))
    _add_textbox(slide, Inches(0.72), Inches(2.15), Inches(10.5), Inches(1.5),
                 heading, 34, bold=True, color=_WHITE)
    _add_logo(slide, prs)
    _add_footer(slide, prs, index)
    return slide


def _content_slide(prs, heading: str, bullets: list[str], index: int):
    slide = prs.slides.add_slide(_blank_layout(prs))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, (0xF4, 0xF6, 0xFB))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, Inches(1.10), _NAVY)
    _add_rect(slide, Inches(0), Inches(1.10), prs.slide_width, Inches(0.06), _ORANGE)

    # Title in white on navy header
    _add_textbox(slide, Inches(0.45), Inches(0.18), Inches(10.8), Inches(0.85),
                 heading, _text_size(heading, 26, 18), bold=True, color=_WHITE)

    bullets = _clean_bullets(bullets)[:4]
    n = len(bullets)

    # Content area: x=0.35", y=1.3", width=12.48", height=5.5"
    # (bottom edge 6.8" safely clears footer at 7.18")
    L, T, W, H = 0.55, 1.35, 11.85, 5.45

    if n == 0:
        pass

    elif n == 1:
        # One wide statement card
        _add_card(slide, Inches(L), Inches(T), Inches(W), Inches(H),
                  bullets[0], font_size=_text_size(bullets[0], 20, 14))

    elif n == 2:
        # Two equal columns
        gap = 0.24
        cw = (W - gap) / 2
        for idx, bullet in enumerate(bullets):
            lx = L + idx * (cw + gap)
            _add_card(slide, Inches(lx), Inches(T), Inches(cw), Inches(H),
                      bullet, number=f"0{idx + 1}",
                      font_size=_text_size(bullet, 17, 12))

    elif n == 3:
        # Three equal columns — clean, no empty slot
        gap = 0.18
        cw = (W - 2 * gap) / 3
        for idx, bullet in enumerate(bullets):
            lx = L + idx * (cw + gap)
            _add_card(slide, Inches(lx), Inches(T), Inches(cw), Inches(H),
                      bullet, number=f"0{idx + 1}",
                      font_size=_text_size(bullet, 14.5, 10.5))

    else:
        # Four cards in 2×2 grid — computed positions, no magic numbers
        gap_x, gap_y = 0.22, 0.22
        cw = (W - gap_x) / 2
        ch = (H - gap_y) / 2
        for idx, bullet in enumerate(bullets):
            col, row = idx % 2, idx // 2
            lx = L + col * (cw + gap_x)
            ty = T + row * (ch + gap_y)
            _add_card(slide, Inches(lx), Inches(ty), Inches(cw), Inches(ch),
                      bullet, number=str(idx + 1),
                      font_size=_text_size(bullet, 13.5, 10.5))

    _add_logo(slide, prs)
    _add_footer(slide, prs, index)
    return slide


def _list_slide(prs, heading: str, bullets: list[str], index: int):
    """
    Text-heavy layout: full-width flowing bullet list with navy accent bar.
    Supports up to 6 bullets — font scales down automatically for longer lists.
    """
    slide = prs.slides.add_slide(_blank_layout(prs))

    # Light gray background — gives slides depth without being white
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, (0xF4, 0xF6, 0xFB))

    # Navy header band — full width, clean
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, Inches(1.10), _NAVY)

    # Orange accent stripe at bottom of header
    _add_rect(slide, Inches(0), Inches(1.10), prs.slide_width, Inches(0.06), _ORANGE)

    # Slide title — white, left-aligned inside header
    _add_textbox(slide, Inches(0.45), Inches(0.18), Inches(10.8), Inches(0.85),
                 heading, _text_size(heading, 26, 18), bold=True, color=_WHITE)

    # White content card for bullets — gives clean contrast on gray background
    _add_rect(slide, Inches(0.35), Inches(1.28), Inches(12.65), Inches(5.88), _WHITE, _WHITE)

    # Bullets inside the white card
    cleaned = _clean_bullets(bullets)[:6]
    base_font = 15 if len(cleaned) <= 4 else 13
    _add_bullets(slide, Inches(0.55), Inches(1.40), Inches(12.30), Inches(5.65),
                 cleaned, font_size=base_font, color=_TEXT)

    _add_logo(slide, prs)
    _add_footer(slide, prs, index)
    return slide


def _image_split_slide(prs, heading: str, bullets: list[str], image_path: str,
                       image_on_left: bool, index: int):
    """
    Split layout: image on one side, title + bulleted list on the other.
    Falls back to list layout if image_path is missing or unreadable.
    """
    if not image_path or not os.path.isfile(image_path):
        return _list_slide(prs, heading, bullets, index)

    slide = prs.slides.add_slide(_blank_layout(prs))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, _WHITE)
    _add_rect(slide, Inches(0), Inches(0), Inches(0.07), prs.slide_height, _NAVY)

    # Title spans full width
    _add_textbox(slide, Inches(0.45), Inches(0.34), Inches(11.45), Inches(0.78),
                 heading, _text_size(heading, 28, 21), bold=True, color=_NAVY)
    _add_rect(slide, Inches(0.45), Inches(1.18), Inches(11.45), Inches(0.03), _LINE)

    # Image occupies ~38% of slide width in the content zone
    img_w = Inches(4.55)
    img_h = Inches(5.50)
    txt_x_offset = Inches(5.0)   # left edge of text when image is left
    txt_w = Inches(7.73)

    try:
        if image_on_left:
            pic = slide.shapes.add_picture(image_path, Inches(0.35), Inches(1.25), img_w, img_h)
            _add_bullets(slide, txt_x_offset, Inches(1.28), txt_w, Inches(5.5),
                         _clean_bullets(bullets)[:4], font_size=16, color=_TEXT)
        else:
            pic = slide.shapes.add_picture(image_path, Inches(8.43), Inches(1.25), img_w, img_h)
            _add_bullets(slide, Inches(0.35), Inches(1.28), Inches(7.8), Inches(5.5),
                         _clean_bullets(bullets)[:4], font_size=16, color=_TEXT)
        # Keep image aspect ratio by letting python-pptx scale height
        _ = pic
    except Exception as e:
        print(f"[PPT] Image insert failed ({image_path}): {e} — falling back to list layout")
        return _list_slide(prs, heading, bullets, index)

    _add_logo(slide, prs)
    _add_footer(slide, prs, index)
    return slide


def _table_slide(prs, heading: str, headers: list, rows: list, index: int):
    """Slide with a styled data table — navy header row, alternating row shading."""
    slide = prs.slides.add_slide(_blank_layout(prs))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, _WHITE)
    _add_rect(slide, Inches(0), Inches(0), Inches(0.07), prs.slide_height, _NAVY)

    _add_textbox(slide, Inches(0.45), Inches(0.22), Inches(11.45), Inches(0.78),
                 heading, _text_size(heading, 26, 18), bold=True, color=_NAVY)
    _add_rect(slide, Inches(0.45), Inches(1.05), Inches(11.45), Inches(0.03), _LINE)

    n_cols = max(len(headers), 1)
    n_rows = len(rows) + 1  # +1 for header row

    tbl_shape = slide.shapes.add_table(
        n_rows, n_cols,
        Inches(0.45), Inches(1.15),
        Inches(12.43), Inches(6.0),
    )
    tbl = tbl_shape.table

    col_width = Inches(12.43) // n_cols
    for col in tbl.columns:
        col.width = col_width

    # Header row — navy background, white bold text
    for col_idx, hdr in enumerate(headers):
        cell = tbl.cell(0, col_idx)
        cell.text = str(hdr)
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor(*_NAVY)
        para = cell.text_frame.paragraphs[0]
        if para.runs:
            run = para.runs[0]
            run.font.bold = True
            run.font.color.rgb = RGBColor(*_WHITE)
            run.font.size = Pt(11)

    # Data rows — alternating white / light blue
    for row_idx, row_data in enumerate(rows, 1):
        bg = _LIGHT_BLUE if row_idx % 2 == 0 else _WHITE
        for col_idx in range(n_cols):
            val = str(row_data[col_idx]) if col_idx < len(row_data) else ""
            cell = tbl.cell(row_idx, col_idx)
            cell.text = val
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(*bg)
            para = cell.text_frame.paragraphs[0]
            if para.runs:
                para.runs[0].font.size = Pt(10)

    _add_logo(slide, prs)
    _add_footer(slide, prs, index)
    return slide


def _closing_slide(prs, title: str, subtitle: str):
    """
    Thank-You / CTA closure slide matching the cover slide style.
    """
    from core.config import get_company_info
    _company = get_company_info()
    slide = prs.slides.add_slide(_blank_layout(prs))
    _add_rect(slide, Inches(0), Inches(0), prs.slide_width, prs.slide_height, _WHITE)
    _add_rect(slide, Inches(0), Inches(0), Inches(4.1), prs.slide_height, _NAVY)
    _add_rect(slide, Inches(3.75), Inches(0), Inches(0.18), prs.slide_height, _ORANGE)

    _add_textbox(slide, Inches(0.65), Inches(0.55), Inches(2.8), Inches(0.35),
                 _company["short"], 10, bold=True, color=(175, 215, 255))

    _add_textbox(slide, Inches(4.55), Inches(1.8), Inches(7.7), Inches(0.9),
                 "Thank You", 44, bold=True, color=_NAVY)

    _add_textbox(slide, Inches(4.58), Inches(2.85), Inches(7.5), Inches(0.65),
                 subtitle, 18, color=_TEXT)

    _add_rect(slide, Inches(4.58), Inches(3.7), Inches(7.5), Inches(0.03), _LINE)

    _add_textbox(slide, Inches(4.58), Inches(3.88), Inches(7.5), Inches(0.42),
                 "Get in touch:", 13, bold=True, color=_NAVY)
    _add_textbox(slide, Inches(4.58), Inches(4.38), Inches(7.5), Inches(0.4),
                 f"{_company['email']}  |  {_company['web']}", 14, color=_BLUE)

    _add_rect(slide, Inches(4.58), Inches(5.3), Inches(2.0), Inches(0.08), _ORANGE)
    _add_logo(slide, prs)
    return slide


# ── LangChain Tool ────────────────────────────────────────────────────────────

@tool
def create_presentation(slides_json: str) -> str:
    """
    Create a branded PowerPoint presentation (company identity from agent_config.yaml)
    using the correct template.

    Choose the template based on the presentation purpose:
      - "sales_blue"   -> client-facing sales pitch (use for most external / client decks)
      - "sales_orange" -> sales combined with a technical or solution overview
      - "marketing"    -> marketing campaigns, events, product launches
      - "internal"     -> internal reports, technical documentation, team updates

    Input MUST be a valid JSON string:
    {
        "title":    "Presentation Title",
        "subtitle": "Tagline or client name",
        "template": "sales_blue",
        "slides": [
            {
                "heading": "Slide Title",
                "bullets": ["Point 1", "Point 2", "Point 3"]
            }
        ]
    }

    Returns the absolute file path of the saved .pptx file, or an error message.
    """
    if isinstance(slides_json, dict):
        data = slides_json
    else:
        try:
            data = json.loads(slides_json)
        except json.JSONDecodeError:
            # LLMs sometimes emit invalid escape sequences (e.g. \P, \T inside strings).
            # Replace every backslash not followed by a valid JSON escape char.
            import re
            cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', slides_json)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON -- {e}"

    try:
        from core.config import get_company_info
        _company = get_company_info()
        template_key   = data.get("template", "sales blue client")
        title          = data.get("title",    "Presentation")
        subtitle       = data.get("subtitle", f"Prepared by {_company['name']}")
        slides_content = _normalize_slides(data.get("slides", []),
                                           honor_exact_count=bool(data.get("slide_count")))

        use_existing = data.get("use_existing_template")
        if use_existing is None:
            use_existing = bool(template_key and str(template_key).lower() not in ("new", "none", "scratch", "blank", "coded"))

        if use_existing:
            prs = _open_template(template_key)
        else:
            prs = _initialize_presentation()

        # ── Cover / title slide ───────────────────────────────────────────────
        _cover_slide(prs, title, subtitle)

        # ── Section divider + content slides ─────────────────────────────────
        if slides_content:
            _section_slide(prs, title, _company["name"], 2)

        for idx, s in enumerate(slides_content, start=3):
            heading     = s.get("heading", "")
            bullets     = s.get("bullets", [])
            layout      = (s.get("layout") or "auto").strip().lower()
            image_query = s.get("image_query", "")
            table_data  = s.get("table")

            # Table slide — takes priority over bullet layouts
            if table_data and isinstance(table_data, dict):
                tbl_headers = table_data.get("headers", [])
                tbl_rows    = table_data.get("rows", [])
                if tbl_headers:
                    _table_slide(prs, heading, tbl_headers, tbl_rows, idx)
                    continue

            # Resolve image path from the folder (None if not found or not requested)
            image_path = _find_image(image_query) if image_query else None

            if layout == "list" or (layout == "auto" and _needs_list_layout(bullets)):
                _list_slide(prs, heading, bullets, idx)
            elif layout in ("image_left", "image_right"):
                # image_path may be None (no genuine library match) —
                # _image_split_slide already degrades to a list layout in
                # that case, so this never shows an unrelated picture.
                _image_split_slide(prs, heading, bullets, image_path,
                                   image_on_left=(layout == "image_left"), index=idx)
            else:
                # A resolved image_path must NOT force an image layout here —
                # the model deliberately chose "auto"/something else for this
                # slide; forcing a picture onto it overrides that choice with
                # whatever happened to match image_query, even when the slide
                # was never meant to be image-illustrated.
                _content_slide(prs, heading, bullets, idx)

        # ── Closing slide ─────────────────────────────────────────────────────
        _closing_slide(prs, title, "We look forward to working with you.")

        # ── Save ──────────────────────────────────────────────────────────────
        os.makedirs(PPT_OUTPUT_DIR, exist_ok=True)
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = (
            "".join(c if c.isalnum() or c in " _-" else "_" for c in title)[:50].strip()
        )
        task_number  = data.get("task_number", 0)
        model_label  = str(data.get("model_label", "")).upper() or "SALLY"
        task_tag     = f"_T{task_number}" if task_number else ""
        model_prefix = f"{model_label}{task_tag}_" if model_label else ""
        filepath = os.path.join(PPT_OUTPUT_DIR, f"{model_prefix}{safe_title}__{timestamp}.pptx")
        prs.save(filepath)
        print(f"[PPT] Saved -> {filepath}")
        return filepath

    except Exception as e:
        return f"Error creating presentation: {e}"
