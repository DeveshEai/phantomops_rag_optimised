"""
tools/ppt_reviewer.py — Structural QA for generated PowerPoint slides

Inspects a .pptx file directly with python-pptx — no LibreOffice, no rendering,
no external services. Works on any cloud environment.

Only non-placeholder, non-background content shapes are checked so template
master shapes (which intentionally span the full slide or sit at the edges)
don't generate false positives.
"""

import os

from langchain_core.tools import tool
from pptx.util import Inches


_MIN_MARGIN       = Inches(0.28)   # edge distance below which content shapes are flagged
_CHARS_PER_SQ_IN  = 115            # legibility limit; overflow flagged at 1.5×
_OVERLAP_THRESHOLD = 0.30          # fraction of smaller shape's area that must overlap


def _is_background(shape, sw, sh) -> bool:
    """True for full-slide or near-full-slide spanning shapes (intentional backgrounds)."""
    w = shape.width  or 0
    h = shape.height or 0
    return (w * h) > (sw * sh * 0.45)


def _is_footer_zone(shape, sh) -> bool:
    """True for shapes in the bottom 12% of the slide (footer bar, page numbers, etc.)."""
    t = shape.top or 0
    return t >= sh * 0.88


def _is_brand_stripe(shape) -> bool:
    """True for the intentional narrow left-edge brand stripe."""
    l = shape.left  or 0
    w = shape.width or 0
    return l == 0 and w < Inches(0.15)


def _is_logo(shape) -> bool:
    """True for Picture shapes (logos are always pictures and sit at intended positions)."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    return shape.shape_type == MSO_SHAPE_TYPE.PICTURE


def _review_pptx(pptx_path: str) -> dict:
    """
    Check every content slide for structural problems.
    Skips: placeholders, backgrounds, footer zone, brand stripe, logo pictures.
    Returns {"lgtm": bool, "issues": [str]}.
    """
    from pptx import Presentation

    prs  = Presentation(pptx_path)
    sw   = prs.slide_width
    sh   = prs.slide_height
    all_issues: list[str] = []

    total_slides = len(prs.slides)
    for slide_num, slide in enumerate(prs.slides, start=1):
        # Cover, section divider, and closing slides intentionally use
        # full-bleed brand shapes, so only review generated content slides.
        if slide_num <= 2 or slide_num == total_slides:
            continue
        prefix = f"Slide {slide_num}"
        content_boxes: list[tuple] = []

        for shape in slide.shapes:
            # ── Skip template / design shapes ─────────────────────────────────
            if shape.is_placeholder:
                continue
            if _is_background(shape, sw, sh):
                continue
            if _is_footer_zone(shape, sh):
                continue
            if _is_brand_stripe(shape):
                continue
            if _is_logo(shape):
                continue

            l = shape.left  or 0
            t = shape.top   or 0
            w = shape.width or 0
            h = shape.height or 0
            r, b = l + w, t + h

            # ── Edge proximity ────────────────────────────────────────────────
            if l < _MIN_MARGIN:
                all_issues.append(
                    f"{prefix}: '{shape.name}' too close to left edge ({l/914400:.2f}\")."
                )
            if t < _MIN_MARGIN:
                all_issues.append(
                    f"{prefix}: '{shape.name}' too close to top edge ({t/914400:.2f}\")."
                )
            if r > sw - _MIN_MARGIN:
                all_issues.append(
                    f"{prefix}: '{shape.name}' too close to right edge ({r/914400:.2f}\")."
                )

            if shape.has_text_frame:
                text = shape.text_frame.text.strip()

                # ── Empty large card ──────────────────────────────────────────
                if not text and w > Inches(1.2) and h > Inches(0.6):
                    all_issues.append(
                        f"{prefix}: '{shape.name}' is a large box with no text (empty card slot)."
                    )

                # ── Rough text overflow ───────────────────────────────────────
                if text:
                    area_sq_in = (w / 914400) * (h / 914400)
                    capacity   = area_sq_in * _CHARS_PER_SQ_IN
                    if len(text) > capacity * 1.5:
                        all_issues.append(
                            f"{prefix}: '{shape.name}' may overflow "
                            f"({len(text)} chars, box fits ~{capacity:.0f})."
                        )

            content_boxes.append((l, t, r, b, shape.name))

        # ── Overlap check among content shapes only ───────────────────────────
        for i in range(len(content_boxes)):
            for j in range(i + 1, len(content_boxes)):
                l1, t1, r1, b1, n1 = content_boxes[i]
                l2, t2, r2, b2, n2 = content_boxes[j]
                ox = max(0, min(r1, r2) - max(l1, l2))
                oy = max(0, min(b1, b2) - max(t1, t2))
                if ox > 0 and oy > 0:
                    overlap = ox * oy
                    a1 = (r1 - l1) * (b1 - t1)
                    a2 = (r2 - l2) * (b2 - t2)
                    smaller = min(a1, a2)
                    if smaller > 0 and overlap / smaller > _OVERLAP_THRESHOLD:
                        all_issues.append(
                            f"{prefix}: '{n1}' overlaps '{n2}' "
                            f"({overlap / smaller:.0%} of smaller shape)."
                        )

    lgtm  = len(all_issues) == 0
    label = os.path.basename(pptx_path)
    if lgtm:
        print(f"[PPT Review] {label} — structural check passed.")
    else:
        print(f"[PPT Review] {label} — {len(all_issues)} issue(s) found.")
        for iss in all_issues:
            print(f"  • {iss}")

    return {"lgtm": lgtm, "issues": all_issues}


@tool
def review_presentation(pptx_path: str) -> str:
    """
    Structurally review a generated PowerPoint file for layout problems.

    Call this immediately after create_presentation, passing ONLY the file path
    string that create_presentation returned (e.g. "C:/sally/.../MyDeck.pptx").
    Do NOT pass the slides_json or any other value — only the returned path.

    Checks every content slide for:
      - Empty card slots (large shapes with no text)
      - Shapes too close to slide edges
      - Text that likely overflows its box
      - Significantly overlapping content shapes

    If issues are found, call create_presentation again with a corrected
    slides_json that addresses each problem. Repeat at most once.

    Returns "LGTM" if the slides look structurally sound, or a list of problems.
    """
    # Guard against the model passing a raw tool-call string instead of a path
    if not pptx_path or not pptx_path.strip().endswith(".pptx"):
        return (
            "Error: pptx_path must be the file path returned by create_presentation "
            "(e.g. 'C:/sally/output/ppts/MyDeck.pptx'). Do not pass slides_json here."
        )

    if not os.path.isfile(pptx_path):
        return f"File not found: {pptx_path}"

    result = _review_pptx(pptx_path)
    issues = result.get("issues") or []

    if result.get("lgtm") and not issues:
        return "LGTM"

    summary = "\n".join(f"- {i}" for i in issues)
    return (
        "Issues found — fix these in the next create_presentation call:\n"
        f"{summary}"
    )
