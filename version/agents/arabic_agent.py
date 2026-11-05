"""
agents/arabic_agent.py — Specialized Arabic Language Capability

Handles Arabic-language understanding, English-to-Arabic translation/localization,
preservation of entity names, numbers, and dates, right-to-left (RTL) review,
and transformation of structured business deliverables.
"""

import re
from typing import Any, Dict, List, Optional
from core.content_types import is_arabic_text


_ARABIC_SYSTEM_PROMPT = """You are a senior enterprise English-to-Arabic translator and localization expert.
Translate the provided business text into Modern Standard Arabic (Fusha) suitable for executive presentation.

Strict Rules:
1. Preserve all proper names, brand names, product names, and legal entity names accurately.
2. Preserve numbers, percentages, currency symbols, and dates exactly without distorting their values.
3. Use professional, modern corporate and commercial terminology.
4. Output only the Arabic translation without conversational preamble.
"""


# Standard dictionary fallback for deterministic offline tests and fallback
_TERMS_DICTIONARY = {
    "business proposal": "عرض أعمال",
    "sales proposal": "عرض مبيعات",
    "market analysis": "تحليل السوق",
    "executive summary": "ملخص تنفيذي",
    "financial overview": "نظرة مالية عامة",
    "recommendations": "التوصيات",
    "conclusion": "الخاتمة",
    "project scope": "نطاق المشروع",
    "budget": "الميزانية",
    "timeline": "الجدول الزمني",
    "deliverables": "المخرجات",
    "growth": "النمو",
    "revenue": "الإيرادات",
    "cost": "التكلفة",
    "company": "الشركة",
    "client": "العميل",
    "overview": "نظرة عامة",
}


class ArabicLanguageAgent:
    """Specialized agent for Arabic localization, translation, and RTL verification."""

    def is_arabic(self, text: str) -> bool:
        """Check whether the given text is predominantly Arabic."""
        return is_arabic_text(text)

    def translate_text(self, text: str, model_label: Optional[str] = None) -> str:
        """
        Translate English text into Modern Standard Arabic.
        Attempts LLM translation first; falls back to term-preserving substitution
        if LLM is unavailable or offline.
        """
        if not text or not text.strip():
            return ""

        # If already Arabic, return as-is
        if self.is_arabic(text) and len(re.findall(r"[a-zA-Z]", text)) < 5:
            return text

        # Attempt LLM translation
        try:
            from core.config import get_agent_model
            from langchain_groq import ChatGroq
            from langchain_core.messages import SystemMessage, HumanMessage
            from core.config import GROQ_API_KEY, GROQ_MODEL

            m_label = model_label or get_agent_model() or "groq"
            if m_label == "groq" and GROQ_API_KEY:
                llm = ChatGroq(
                    api_key=GROQ_API_KEY,
                    model_name=GROQ_MODEL,
                    temperature=0.1,
                    max_tokens=2048,
                )
                messages = [
                    SystemMessage(content=_ARABIC_SYSTEM_PROMPT),
                    HumanMessage(content=text),
                ]
                resp = llm.invoke(messages)
                translated = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
                if translated and self.is_arabic(translated):
                    return translated
        except Exception as e:
            print(f"[ArabicAgent] LLM translation fallback triggered: {e}")

        # Deterministic dictionary & rule-based localization fallback
        res = text
        for eng, ara in _TERMS_DICTIONARY.items():
            pattern = re.compile(rf"\b{re.escape(eng)}\b", re.IGNORECASE)
            res = pattern.sub(ara, res)

        # If no Arabic was injected, wrap with clear localized Arabic heading
        if not self.is_arabic(res):
            res = f"ملخص باللغة العربية:\n{res}"

        return res

    def transform_proposal(self, proposal_data: Dict[str, Any], model_label: Optional[str] = None) -> Dict[str, Any]:
        """
        Transform a structured English proposal dictionary into an Arabic proposal.
        Translates title, summary, section headings, and body content, while
        preserving table structure, numbers, and dates.
        """
        translated_proposal = dict(proposal_data)
        
        # Translate title
        raw_title = proposal_data.get("title", "")
        translated_proposal["title"] = self.translate_text(raw_title, model_label)
        translated_proposal["language"] = "ar"
        translated_proposal["is_rtl"] = True

        # Translate summary if present
        if "summary" in proposal_data and proposal_data["summary"]:
            translated_proposal["summary"] = self.translate_text(proposal_data["summary"], model_label)

        # Translate sections
        raw_sections = proposal_data.get("sections", [])
        translated_sections = []
        for sec in raw_sections:
            t_sec = dict(sec)
            if "heading" in sec and sec["heading"]:
                t_sec["heading"] = self.translate_text(sec["heading"], model_label)
            if "content" in sec and sec["content"]:
                t_sec["content"] = self.translate_text(sec["content"], model_label)
            
            # Translate table headers if table exists, keeping numbers intact
            if "table" in sec and isinstance(sec["table"], dict):
                t_tbl = dict(sec["table"])
                if "headers" in t_tbl:
                    t_tbl["headers"] = [self.translate_text(str(h), model_label) for h in t_tbl["headers"]]
                # Rows: translate strings, keep numbers and dates unchanged
                if "rows" in t_tbl:
                    new_rows = []
                    for row in t_tbl["rows"]:
                        new_row = []
                        for cell in row:
                            if isinstance(cell, (int, float)) or (isinstance(cell, str) and re.match(r"^[\d\.\-%$€£]+$", cell.strip())):
                                new_row.append(cell)
                            else:
                                new_row.append(self.translate_text(str(cell), model_label))
                        new_rows.append(new_row)
                    t_tbl["rows"] = new_rows
                t_sec["table"] = t_tbl

            translated_sections.append(t_sec)

        translated_proposal["sections"] = translated_sections
        return translated_proposal

    def review_arabic_content(self, text: str, target_format: str = "docx") -> Dict[str, Any]:
        """
        Review Arabic text for linguistic completeness and RTL compatibility warnings.
        """
        has_arabic = self.is_arabic(text)
        issues = []
        
        if not has_arabic:
            issues.append("Text does not contain recognized Arabic script.")

        # Check for mixed numbers / mismatched brackets
        open_brackets = text.count("(") + text.count("[") + text.count("{")
        close_brackets = text.count(")") + text.count("]") + text.count("}")
        if open_brackets != close_brackets:
            issues.append(f"Mismatched bracket count (open={open_brackets}, close={close_brackets}).")

        rtl_notes = "Standard RTL paragraph layout supported."
        if target_format == "xlsx":
            rtl_notes = "Excel sheet right-to-left direction should be configured for native display."

        return {
            "valid": len(issues) == 0,
            "has_arabic": has_arabic,
            "issues": issues,
            "rtl_notes": rtl_notes,
            "target_format": target_format,
        }
