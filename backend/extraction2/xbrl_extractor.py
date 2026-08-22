"""
Platrixa
Extraction 2.0 - XBRL Extractor

Structured fact extraction from SEC XBRL / Inline XBRL documents.

Preserves, per fact:
    - original XBRL tag (concept), e.g. us-gaap:Revenues
    - value (decimals/scale applied)
    - unit (e.g. iso4217:USD)
    - start/end period and instant period
    - fiscal year / quarter (derived deterministically from dates)
    - filing type, accession number, amendment status (dei facts / meta)
    - source location (document reference)

Structured XBRL facts take precedence over regex extraction. Concept tags
are never collapsed: `us-gaap:Revenues`, `us-gaap:SalesRevenueNet` and
`us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` remain
distinct facts with distinct metric_definition values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from lxml import etree

# ---------------------------------------------------------------------------
# XBRL namespaces
# ---------------------------------------------------------------------------

NS = {
    "xbrli": "http://www.xbrl.org/2003/instance",
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "link": "http://www.xbrl.org/2003/linkbase",
    "dei": "http://xbrl.sec.gov/dei/2024",
    "us-gaap": "http://fasb.org/us-gaap/2024",
}

# Financial concept namespaces whose numeric elements are facts
_FACT_NAMESPACES = ("us-gaap", "ifrs-full", "dei")

# Elements that are structural, never facts
_STRUCTURAL_LOCAL = {
    "context", "unit", "schemaRef", "linkbaseRef", "roleRef", "arcroleRef",
    "footnoteLink", "locator", "label", "presentationArc", "definitionArc",
    "calculationArc", "reference",
}

_AMENDMENT_FLAG_RE = re.compile(
    r"(dei:)?AmendmentFlag.*?>(true|false|1|0)<",
    re.IGNORECASE | re.DOTALL,
)

_DOCUMENT_TYPE_RE = re.compile(
    r"(dei:)?DocumentType.*?>([^<]+)<",
    re.IGNORECASE | re.DOTALL,
)

_ACCESSION_RE = re.compile(
    r"(dei:)?AccessionNumber.*?>([^<]+)<",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class XbrlFact:
    """A single structured XBRL fact."""

    concept: str = ""                      # original tag, e.g. us-gaap:Revenues
    local_name: str = ""                   # Revenues
    value: Optional[float] = None
    raw_text: str = ""
    decimals: Optional[int] = None
    scale: Optional[int] = None            # stored value multiplier (Inline XBRL)
    unit: str = ""                         # iso4217:USD -> USD
    context_ref: str = ""
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    instant: Optional[str] = None
    fiscal_year: Optional[int] = None
    fiscal_quarter: Optional[str] = None
    duration_type: str = ""                # "duration" | "instant"
    filing_type: str = ""
    accession_number: str = ""
    is_amendment: bool = False
    source_location: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept": self.concept,
            "local_name": self.local_name,
            "value": self.value,
            "raw_text": self.raw_text,
            "decimals": self.decimals,
            "scale": self.scale,
            "unit": self.unit,
            "context_ref": self.context_ref,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "instant": self.instant,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "duration_type": self.duration_type,
            "filing_type": self.filing_type,
            "accession_number": self.accession_number,
            "is_amendment": self.is_amendment,
            "source_location": self.source_location,
        }


class XbrlExtractor:
    """Extract structured facts from raw XBRL instance or Inline XBRL."""

    def extract(self, content: str, meta: Optional[Dict[str, Any]] = None) -> List[XbrlFact]:
        """
        Extract facts from XBRL/Inline XBRL content.

        Args:
            content: XBRL instance XML or Inline XBRL (XHTML) string.
            meta: optional document metadata (filing_type, accession_number,
                  is_amendment) used when dei facts are absent.
        """
        meta = meta or {}

        # 1. Parse with lxml (Inline XBRL is XHTML; must be well-formed)
        facts: List[XbrlFact] = []
        try:
            root = etree.fromstring(content.encode("utf-8"))
            contexts, units = self._collect_contexts_units(root)
            facts = self._collect_facts(root, contexts, units, meta)
        except (etree.XMLSyntaxError, ValueError):
            # 2. Fallback: regex-based Inline XBRL extraction
            facts = self._regex_inline_fallback(content, meta)

        # 3. Enrich with dei metadata
        if not any(f.filing_type for f in facts):
            filing_type = self._extract_dei_text(content, _DOCUMENT_TYPE_RE) or meta.get("filing_type", "")
            accession = self._extract_dei_text(content, _ACCESSION_RE) or meta.get("accession_number", "")
            amendment_flag = self._extract_dei_text(content, _AMENDMENT_FLAG_RE) or ""
            is_amendment = amendment_flag.lower() in ("true", "1") or bool(meta.get("is_amendment"))
            for f in facts:
                f.filing_type = filing_type
                f.accession_number = accession
                f.is_amendment = is_amendment

        return facts

    # ------------------------------------------------------------------
    # lxml-based extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_contexts_units(root) -> tuple:
        contexts: Dict[str, Dict[str, str]] = {}
        units: Dict[str, str] = {}

        for ctx in root.iter():
            if etree.QName(ctx).localname != "context":
                continue
            cid = ctx.get("id")
            if not cid:
                continue
            period = {}
            # Period fields are nested under <period>; iterate ALL descendants
            for child in ctx.iter():
                local = etree.QName(child).localname
                if local in ("startDate", "endDate"):
                    period[local] = (child.text or "").strip()
                elif local == "instant":
                    period["instant"] = (child.text or "").strip()
            contexts[cid] = period

        for unit in root.iter():
            if etree.QName(unit).localname != "unit":
                continue
            uid = unit.get("id")
            if not uid:
                continue
            measures = []
            for m in unit.iter():
                if etree.QName(m).localname == "measure":
                    measures.append((m.text or "").strip())
            units[uid] = ",".join(measures)

        return contexts, units

    def _collect_facts(self, root, contexts, units, meta) -> List[XbrlFact]:
        facts: List[XbrlFact] = []
        for el in root.iter():
            # lxml Element: tag is '{namespace}localname' for namespaced
            # elements; el.prefix holds the declared prefix.
            tag = el.tag
            if not isinstance(tag, str) or not tag:
                continue
            if tag.startswith("{"):
                _, local = tag[1:].split("}", 1)
                prefix = el.prefix or ""
            else:
                local, prefix = tag, ""

            # Inline XBRL numeric fact
            if prefix == "ix" and local in ("nonFraction", "nonNumeric"):
                fact = self._fact_from_ix(el, contexts, units, meta)
                if fact is not None and fact.value is not None:
                    facts.append(fact)
                continue

            # Raw XBRL numeric fact: element in a financial namespace with
            # contextRef and numeric text, and not structural
            if (
                prefix in _FACT_NAMESPACES
                and el.get("contextRef")
                and local not in _STRUCTURAL_LOCAL
            ):
                text = (el.text or "").strip()
                if text and self._is_numeric(text):
                    fact = self._fact_from_raw(
                        prefix, local, text, el, contexts, units, meta
                    )
                    if fact is not None:
                        facts.append(fact)

        return facts

    @staticmethod
    def _fact_from_ix(el, contexts, units, meta) -> Optional[XbrlFact]:
        name = el.get("name") or ""
        value_str = (el.get("value") or (el.text or "")).strip()
        if not name or not value_str:
            return None

        local = name.split(":")[-1]
        unit_ref = el.get("unitRef", "")
        unit = units.get(unit_ref, "")

        try:
            decimals = int(el.get("decimals")) if el.get("decimals") is not None else None
        except (TypeError, ValueError):
            decimals = None
        try:
            scale = int(el.get("scale")) if el.get("scale") is not None else None
        except (TypeError, ValueError):
            scale = None

        numeric = XbrlExtractor._to_float(value_str)
        if numeric is None:
            return None
        if scale:
            numeric = numeric * (10 ** scale)

        period_start, period_end, instant = XbrlExtractor._period_from_context(
            el.get("contextRef", ""), contexts
        )
        fiscal_year, fiscal_quarter, duration_type = XbrlExtractor._derive_fiscal(
            period_start, period_end, instant
        )

        return XbrlFact(
            concept=name,
            local_name=local,
            value=numeric,
            raw_text=value_str,
            decimals=decimals,
            scale=scale,
            unit=XbrlExtractor._clean_unit(unit),
            context_ref=el.get("contextRef", ""),
            period_start=period_start,
            period_end=period_end,
            instant=instant,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            duration_type=duration_type,
            filing_type=meta.get("filing_type", ""),
            accession_number=meta.get("accession_number", ""),
            is_amendment=bool(meta.get("is_amendment")),
            source_location=f"inline-xbrl:{name}",
        )

    @staticmethod
    def _fact_from_raw(prefix, local, text, el, contexts, units, meta) -> Optional[XbrlFact]:
        numeric = XbrlExtractor._to_float(text)
        if numeric is None:
            return None

        try:
            scale = int(el.get("scale")) if el.get("scale") is not None else 0
        except (TypeError, ValueError):
            scale = 0
        if scale:
            numeric = numeric * (10 ** scale)

        try:
            decimals = int(el.get("decimals")) if el.get("decimals") is not None else None
        except (TypeError, ValueError):
            decimals = None

        unit_ref = el.get("unitRef", "")
        unit = units.get(unit_ref, "")

        period_start, period_end, instant = XbrlExtractor._period_from_context(
            el.get("contextRef", ""), contexts
        )
        fiscal_year, fiscal_quarter, duration_type = XbrlExtractor._derive_fiscal(
            period_start, period_end, instant
        )

        return XbrlFact(
            concept=f"{prefix}:{local}",
            local_name=local,
            value=numeric,
            raw_text=text,
            decimals=decimals,
            scale=scale or None,
            unit=XbrlExtractor._clean_unit(unit),
            context_ref=el.get("contextRef", ""),
            period_start=period_start,
            period_end=period_end,
            instant=instant,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            duration_type=duration_type,
            filing_type=meta.get("filing_type", ""),
            accession_number=meta.get("accession_number", ""),
            is_amendment=bool(meta.get("is_amendment")),
            source_location=f"xbrl:{prefix}:{local}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _period_from_context(context_ref: str, contexts: Dict[str, Dict[str, str]]):
        period = contexts.get(context_ref, {})
        return (
            period.get("startDate", ""),
            period.get("endDate", ""),
            period.get("instant", ""),
        )

    @staticmethod
    def _derive_fiscal(period_start, period_end, instant):
        duration_type = "duration" if period_end and not instant else "instant"
        end = period_end or instant or ""
        year = None
        m = re.search(r"(19|20)\d{2}", end)
        if m:
            year = int(m.group(0))

        quarter = ""
        if period_start and period_end:
            try:
                d1 = datetime.strptime(period_start[:10], "%Y-%m-%d")
                d2 = datetime.strptime(period_end[:10], "%Y-%m-%d")
                months = (d2.year - d1.year) * 12 + (d2.month - d1.month)
                if months >= 11:
                    quarter = "FY"  # annual
                elif months >= 8:
                    quarter = "Q4"
                elif months >= 5:
                    quarter = "Q3"
                elif months >= 2:
                    quarter = "Q2"
                else:
                    quarter = "Q1"
            except (ValueError, TypeError):
                quarter = ""
        elif year is not None:
            quarter = "FY"

        return year, quarter, duration_type

    @staticmethod
    def _clean_unit(unit: str) -> str:
        if not unit:
            return ""
        # iso4217:USD -> USD
        parts = [p for p in unit.split(",") if p]
        cleaned = []
        for p in parts:
            if ":" in p:
                cleaned.append(p.split(":")[-1])
            else:
                cleaned.append(p)
        return ",".join(cleaned)

    @staticmethod
    def _is_numeric(text: str) -> bool:
        return XbrlExtractor._to_float(text) is not None

    @staticmethod
    def _to_float(text: str) -> Optional[float]:
        t = text.strip().replace(",", "")
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Regex fallback for malformed / non-XML Inline XBRL
    # ------------------------------------------------------------------

    @staticmethod
    def _regex_inline_fallback(content: str, meta) -> List[XbrlFact]:
        facts: List[XbrlFact] = []
        pattern = re.compile(
            r'<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>',
            re.IGNORECASE | re.DOTALL,
        )
        attr_re = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
        for m in pattern.finditer(content):
            attrs = dict(attr_re.findall(m.group(1)))
            name = attrs.get("name", "")
            value_str = (attrs.get("value") or m.group(2) or "").strip()
            if not name or not value_str:
                continue
            numeric = XbrlExtractor._to_float(value_str)
            if numeric is None:
                continue
            try:
                scale = int(attrs.get("scale")) if attrs.get("scale") else 0
            except ValueError:
                scale = 0
            if scale:
                numeric *= 10 ** scale
            local = name.split(":")[-1]
            facts.append(XbrlFact(
                concept=name,
                local_name=local,
                value=numeric,
                raw_text=value_str,
                scale=scale or None,
                unit=XbrlExtractor._clean_unit(attrs.get("unitRef", "")),
                context_ref=attrs.get("contextRef", ""),
                filing_type=meta.get("filing_type", ""),
                accession_number=meta.get("accession_number", ""),
                is_amendment=bool(meta.get("is_amendment")),
                source_location=f"inline-xbrl-regex:{name}",
            ))
        return facts

    @staticmethod
    def _extract_dei_text(content: str, pattern: re.Pattern) -> str:
        m = pattern.search(content)
        if not m:
            return ""
        return m.group(2).strip() if m.lastindex and m.lastindex >= 2 else ""
