"""
agents/business_agent.py — Specialized Sales & Business Capability

Responsible for:
- Market & company research (Web search + Hybrid RAG)
- Proposal and executive summary authoring
- Delivering structured Word, Excel, and PDF documents via existing tools
- Preserving and dispatching presentation requests to the protected PPT pipeline
"""

import os
from typing import Any, Dict, List, Optional
from core.content_types import StructuredDocument, ContentSection, ContentTable


class BusinessAgent:
    """Specialized agent for enterprise business research, proposals, and deliverables."""

    def __init__(self):
        pass

    def perform_research(
        self,
        query: str,
        use_web: bool = True,
        use_rag: bool = True,
        time_range: str = "",
    ) -> Dict[str, Any]:
        """
        Execute business research across Web search (Tavily/SerpAPI) and internal RAG.
        Returns gathered text snippets and source references.
        """
        findings = []
        sources = []

        # 1. Internal RAG lookup if requested
        if use_rag:
            try:
                from tools.rag_search import search_knowledge_base
                kb_res = search_knowledge_base.invoke({"query": query})
                if kb_res and "no relevant" not in kb_res.lower() and not kb_res.startswith("Error"):
                    findings.append(f"Internal Knowledge Base Context:\n{kb_res}")
                    sources.append("Internal Knowledge Base (ChromaDB)")
            except Exception as e:
                err_str = str(e)
                # Ollama not running is expected on Groq-only deployments — skip silently
                if "ollama" not in err_str.lower() and "connect" not in err_str.lower():
                    print(f"[BusinessAgent] RAG query skipped or failed: {e}")


        # 2. Web search lookup if requested
        if use_web:
            search_output = ""
            try:
                from tools.web_search import tavily_search, serpapi_search
                # Prefer Tavily for research; fallback to SerpAPI
                search_output = tavily_search.invoke({"query": query, "time_range": time_range})
                if not search_output or "no results found" in search_output.lower() or "failed" in search_output.lower():
                    search_output = serpapi_search.invoke({"query": query, "time_range": time_range})
            except Exception as e:
                print(f"[BusinessAgent] Web search call failed: {e}")

            if search_output and not search_output.startswith("No results"):
                findings.append(f"Web Research Findings:\n{search_output}")
                sources.append("Web Search Engine")

        combined_findings = "\n\n---\n\n".join(findings) if findings else f"Research summary on {query}: Detailed analysis based on available context."

        return {
            "query": query,
            "findings": combined_findings,
            "sources": sources,
            "has_data": bool(findings),
        }

    def prepare_proposal(
        self,
        topic: str,
        research_context: str = "",
        client_name: str = "",
    ) -> Dict[str, Any]:
        """
        Draft a structured business proposal containing executive summary,
        market analysis, and deliverables table.
        """
        clean_topic = topic.strip() or "Business Proposal"
        clean_client = client_name.strip() or "Client Organization"

        summary = (
            f"This proposal outlines strategic recommendations and delivery specifications "
            f"for {clean_topic}, prepared specifically for {clean_client}. "
            f"Grounded in comprehensive research and operational requirements."
        )

        sections = [
            {
                "heading": "Executive Summary",
                "content": summary + ("\n\n" + research_context[:800] if research_context else ""),
            },
            {
                "heading": "Market & Technical Assessment",
                "content": (
                    f"Our findings indicate significant operational advantages for {clean_topic}. "
                    f"Implementation aligns with current industry benchmarks and compliance standards."
                ),
            },
            {
                "heading": "Scope of Deliverables & Milestones",
                "content": "The following structured table details phased deliverables, target dates, and scope definitions:",
                "table": {
                    "headers": ["Milestone", "Deliverable Scope", "Timeline", "Target Outcome"],
                    "rows": [
                        ["Phase 1", f"Discovery & {clean_topic} Architecture", "Week 1-2", "Approved Baseline"],
                        ["Phase 2", "System Integration & Validation", "Week 3-4", "Verification Complete"],
                        ["Phase 3", "Final Rollout & Executive Handover", "Week 5", "Production Delivery"],
                    ],
                },
            },
            {
                "heading": "Commercial Investment & Terms",
                "content": (
                    "Engagements are delivered on fixed-fee milestones with comprehensive SLA guarantees. "
                    "Invoicing follows milestone sign-off with 30-day payment terms."
                ),
                "table": {
                    "headers": ["Item", "Cost Basis", "Amount (USD)"],
                    "rows": [
                        ["Architecture & Implementation", "Fixed milestone", "25,000"],
                        ["Quality Assurance & Validation", "Fixed milestone", "10,000"],
                        ["Total Estimated Investment", "Project Total", "35,000"],
                    ],
                },
            },
        ]

        return {
            "title": clean_topic,
            "recipient": clean_client,
            "summary": summary,
            "sections": sections,
            "language": "en",
        }

    def render_deliverable(
        self,
        proposal_dict: Dict[str, Any],
        output_format: str = "docx",
        model_label: str = "groq",
        task_number: int = 1,
    ) -> Optional[str]:
        """
        Produce output file using existing tools:
        - "ppt"  -> protected PPT pipeline (tools/ppt_agent.py)
        - "docx" -> tools/doc_creator.py
        - "pdf"  -> tools/doc_creator.py with verified PDF conversion
        - "xlsx" -> tools/excel_creator.py
        """
        fmt = (output_format or "docx").lower().strip()
        title = proposal_dict.get("title", "Business Deliverable")

        # ── 1. Protected PPT Pipeline (CRITICAL: PRESERVE EXACT INVOCATION) ──
        if fmt in ("ppt", "pptx", "presentation"):
            from tools.ppt_agent import generate_presentation
            context_text = proposal_dict.get("summary", "")
            for sec in proposal_dict.get("sections", []):
                context_text += f"\n\n## {sec.get('heading', '')}\n{sec.get('content', '')}"

            ppt_res = generate_presentation.invoke({
                "topic": title,
                "context": context_text,
                "template": "sales_blue",
                "model_label": model_label,
                "task_number": task_number,
                "content_language": proposal_dict.get("language", "en"),
                "slide_count": 5,
            })
            if isinstance(ppt_res, str) and os.path.isfile(ppt_res) and os.path.getsize(ppt_res) > 0:
                return ppt_res
            return None

        # ── 2. Excel Spreadsheet ──
        elif fmt in ("xlsx", "excel", "csv", "spreadsheet"):
            from tools.excel_creator import create_excel_report
            sheets = []
            for sec in proposal_dict.get("sections", []):
                tbl = sec.get("table")
                if tbl and isinstance(tbl, dict) and tbl.get("headers"):
                    sheet_name = sec.get("heading", "Data Sheet")[:30]
                    sheets.append({
                        "name": sheet_name,
                        "headers": tbl.get("headers", []),
                        "rows": tbl.get("rows", []),
                    })
            if not sheets:
                # Default summary sheet
                sheets.append({
                    "name": "Summary",
                    "headers": ["Section", "Details"],
                    "rows": [[s.get("heading", ""), s.get("content", "")[:200]] for s in proposal_dict.get("sections", [])],
                })

            res = create_excel_report.invoke({
                "title": title,
                "sheets": sheets,
                "model_label": model_label,
                "task_number": task_number,
            })
            if isinstance(res, str) and os.path.isfile(res) and os.path.getsize(res) > 0:
                return res
            return None

        # ── 3. Word (.docx) or PDF Document ──
        else:
            from tools.doc_creator import create_document
            # Build markdown body from structured proposal
            md_lines = [f"# {title}", ""]
            if proposal_dict.get("summary"):
                md_lines.append(f"> {proposal_dict['summary']}\n")

            for sec in proposal_dict.get("sections", []):
                if sec.get("heading"):
                    md_lines.append(f"## {sec['heading']}")
                if sec.get("content"):
                    md_lines.append(sec["content"])
                tbl = sec.get("table")
                if tbl and isinstance(tbl, dict) and tbl.get("headers"):
                    headers = tbl.get("headers", [])
                    md_lines.append("| " + " | ".join(str(h) for h in headers) + " |")
                    md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                    for row in tbl.get("rows", []):
                        md_lines.append("| " + " | ".join(str(c) for c in row) + " |")
                md_lines.append("")

            markdown_content = "\n".join(md_lines)
            target_format = "pdf" if fmt == "pdf" else "word"
            res = create_document.invoke({
                "title": title,
                "content": markdown_content,
                "format": target_format,
            })
            if isinstance(res, str) and os.path.isfile(res) and os.path.getsize(res) > 0:
                return res
            return None
