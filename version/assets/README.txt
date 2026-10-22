SALLY — Company Assets  (C:\sally\Rag-Based-Bot\assets\)
=========================================================

Files here are used automatically by Sally. Do not rename them —
exact filenames are registered in core/config.py.

assets/
  logo/
    EAI Systems Logo.jpg     ← Stamped top-right on every PPT slide
    EAI logo.svg             ← Reference only (python-pptx cannot embed SVG)

  templates/
    Template 1 - A Rating Blue - ...pptx     → key: "sales_blue"   (client sales — DEFAULT)
    Template 2 - A Rating Orange - ...pptx   → key: "sales_orange" (sales + tech solutions)
    Template 3 - B Rating Dark - ...pptx     → key: "marketing"    (marketing & events)
    Template 4 - C Rating - ...pptx          → key: "internal"     (internal / technical)

    Sally's LLM picks the right template from the email context automatically.

  letterhead/
    India Letter Head.docx       ← India-region clients
    Letter Head Format_UK.docx   ← UK-region clients

    Sally detects the region from email context and mentions the correct letterhead.
    Future: auto-attach as a Word document.
