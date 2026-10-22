"""
skills/email_skill/html_formatter.py — Markdown → email-HTML renderer

Provider-neutral. Both outlook_client.py and gmail_client.py import
`format_reply_html` from here so the reply formatting is identical no matter
which backend sends the message.

Renders: ## headings, **bold**/*italic*, [label](url) links, '- ' bullet
lists, '1.' numbered lists, and paragraphs — into inline-styled HTML that
displays consistently across email clients. ALL-CAPS "HEADER:" lines are also
treated as headings for backward compatibility.
"""

import html as _html_lib
import re as _re


def _md_inline(text: str) -> str:
    """Escape HTML, then apply inline markdown: **bold**, *italic*, and links."""
    text = _html_lib.escape(text)
    # **bold** / __bold__
    text = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = _re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    # *italic* / _italic_  (avoid matching inside words)
    text = _re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", text)
    # [label](url)
    text = _re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                   r'<a href="\2" style="color:#1a56db;">\1</a>', text)
    return text


def format_reply_html(text: str) -> str:
    """
    Convert a markdown/plain-text LLM reply into clean, presentable HTML:
    headings, bold/italic, bullet + numbered lists, and paragraphs that
    render consistently across email clients.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = text.split("\n")

    parts: list[str] = []
    list_buf: list[str] = []
    list_kind: str | None = None   # "ul" or "ol"

    def _flush_list():
        nonlocal list_buf, list_kind
        if not list_buf:
            return
        tag = list_kind or "ul"
        items = "".join(
            f'<li style="margin-bottom:6px;">{_md_inline(it)}</li>' for it in list_buf
        )
        parts.append(
            f'<{tag} style="margin:6px 0 14px 22px; padding:0;">{items}</{tag}>'
        )
        list_buf, list_kind = [], None

    para_buf: list[str] = []

    def _flush_para():
        nonlocal para_buf
        if not para_buf:
            return
        inner = "<br>".join(_md_inline(l) for l in para_buf)
        parts.append(f'<p style="margin:0 0 12px 0;">{inner}</p>')
        para_buf = []

    H_STYLE = {
        2: "font-size:18px;font-weight:700;color:#111;margin:18px 0 8px 0;",
        3: "font-size:15px;font-weight:700;color:#222;margin:16px 0 6px 0;",
    }

    for raw in lines:
        stripped = raw.strip()

        if not stripped:                      # blank line → end current block
            _flush_list(); _flush_para()
            continue

        # Markdown headings: #, ##, ###
        h = _re.match(r"^(#{1,6})\s+(.*)$", stripped)
        # ALL-CAPS plain header ending with ':' (e.g. "TOP 5 STOCKS:")
        caps = (stripped.endswith(":") and len(stripped) <= 60
                and stripped.upper() == stripped and any(c.isalpha() for c in stripped))

        if h or caps:
            _flush_list(); _flush_para()
            level = 3
            content = stripped
            if h:
                level = 2 if len(h.group(1)) <= 2 else 3
                content = h.group(2).strip()
            parts.append(f'<div style="{H_STYLE.get(level, H_STYLE[3])}">{_md_inline(content)}</div>')
            continue

        # Bullet list item: -, •, *
        m_ul = _re.match(r"^[-•*]\s+(.*)$", stripped)
        # Numbered list item: 1. / 1)
        m_ol = _re.match(r"^\d+[.)]\s+(.*)$", stripped)

        if m_ul:
            _flush_para()
            if list_kind == "ol":
                _flush_list()
            list_kind = "ul"
            list_buf.append(m_ul.group(1).strip())
            continue
        if m_ol:
            _flush_para()
            if list_kind == "ul":
                _flush_list()
            list_kind = "ol"
            list_buf.append(m_ol.group(1).strip())
            continue

        # Regular text line
        _flush_list()
        para_buf.append(stripped)

    _flush_list(); _flush_para()

    body_html = "\n".join(parts)
    return (
        '<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#222222;line-height:1.65;max-width:680px;">\n'
        f"{body_html}\n"
        "</body></html>"
    )
