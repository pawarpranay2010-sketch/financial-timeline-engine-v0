"""
Financial Timeline Engine
Extraction 2.0 - FinancialExtractorV2

The new PRIMARY financial extractor. Structured-first, source-grounded.

Extraction priority (never regex-first):

    1. XBRL / Inline XBRL structured facts      (highest)
    2. Structured tables (HTML/PDF/XLSX/DOCX)   (very high)
    3. Layout-aware text extraction             (medium/high)
    4. Contextual regex                         (LAST RESORT, guarded)

A candidate financial value is only accepted when it carries contextual
evidence: a metric label, a valid period, a unit/scale marker, a currency
marker, or a table relationship. Bare "Revenue -> next number" matching
(which caused the real SEC stress-test failure) is impossible here:
fiscal years, page numbers, footnote references and cross-references are
explicitly rejected.

Output: list of ExtractedFact-shaped dicts (see backend/database/models.py
ExtractedFact) plus a stats block. Each fact also maps onto the frozen
EvidenceItem interface for Agentic RAG compatibility, and receives a
SHA-256 evidence_hash using the same algorithm as EvidenceSummaryState so
downstream deduplication works unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.extraction2.confidence_scorer import (
    ConfidenceScorer,
    METHOD_XBRL,
    METHOD_HTML_TABLE,
    METHOD_PDF_TABLE,
    METHOD_LAYOUT_AWARE,
    METHOD_CONTEXTUAL_REGEX,
)
from backend.extraction2.negative_detector import NegativeDetector
from backend.extraction2.table_extractor import TableExtractor, Table
from backend.extraction2.xbrl_extractor import XbrlExtractor, XbrlFact
from backend.intelligence.evidence_summary_state import EvidenceSummaryState

# ---------------------------------------------------------------------------
# Metric registry: canonical metric_id -> matching labels (longest first)
# ---------------------------------------------------------------------------

METRIC_REGISTRY: Dict[str, List[str]] = {
    "Revenue": [
        "total net sales", "net sales", "total revenue", "revenue",
        "sales revenue", "operating revenue", "sales",
    ],
    "NetIncome": [
        "net income attributable to", "net income", "net earnings",
        "profit after tax", "net profit", "profit for the year",
    ],
    "EBITDA": ["ebitda"],
    "OperatingIncome": [
        "income from operations", "operating income", "operating profit",
    ],
    "GrossProfit": ["gross profit"],
    "GrossMargin": ["gross margin"],
    "OperatingMargin": ["operating margin"],
    "NetMargin": ["net margin", "net profit margin"],
    "EPS": ["earnings per share", "earnings per common share", "diluted eps", "basic eps", "eps"],
    "TotalDebt": [
        "total debt", "total borrowings", "long-term debt and finance leases",
        "long-term debt", "long term debt", "short-term debt",
    ],
    "TotalAssets": ["total assets"],
    "TotalLiabilities": ["total liabilities"],
    "ShareholdersEquity": [
        "total stockholders' equity", "total shareholders' equity",
        "stockholders' equity", "shareholders' equity", "total equity",
    ],
    "OperatingCashFlow": [
        "net cash provided by operating activities", "operating cash flow",
        "cash flow from operations", "cash from operating activities",
        "cash generated from operating activities",
    ],
    "FreeCashFlow": ["free cash flow"],
    "ResearchAndDevelopment": ["research and development", "r&d"],
    "CostOfRevenue": ["cost of sales", "cost of revenue", "cost of goods sold"],
    "SellingGeneralAndAdmin": [
        "selling, general and administrative", "selling general and administrative",
        "sga",
    ],
    "IncomeTax": ["provision for income taxes", "income tax expense", "income tax"],
    "InterestExpense": ["interest expense", "interest and finance costs"],
    "DepreciationAmortization": ["depreciation and amortization", "depreciation & amortization"],
    "CashAndEquivalents": [
        "cash and cash equivalents", "cash & cash equivalents",
        "cash and bank balances",
    ],
    "RetainedEarnings": ["retained earnings", "retained profits"],
    "Inventories": ["inventories", "inventory"],
    "AccountsReceivable": [
        "accounts receivable", "trade receivables", "trade and other receivables",
    ],
    "DividendPerShare": ["dividend per share"],
    "CapitalExpenditure": ["capital expenditure", "capital expenditures", "capex"],
}

# Concept tags that get preserved as-is (never collapsed into generic metrics)
_XBRL_CONCEPT_MAP: Dict[str, str] = {
    "Revenues": "Revenue",
    "SalesRevenueNet": "Revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "Revenue",
    "NetIncomeLoss": "NetIncome",
    "ProfitLoss": "NetIncome",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "NetIncome",
    "OperatingIncomeLoss": "OperatingIncome",
    "GrossProfit": "GrossProfit",
    "CostOfGoodsAndServicesSold": "CostOfRevenue",
    "CostOfGoodsSold": "CostOfRevenue",
    "ResearchAndDevelopmentExpense": "ResearchAndDevelopment",
    "SellingGeneralAndAdministrativeExpense": "SellingGeneralAndAdmin",
    "IncomeTaxExpenseBenefit": "IncomeTax",
    "EarningsPerShareBasic": "EPS",
    "EarningsPerShareDiluted": "EPS",
    "Assets": "TotalAssets",
    "Liabilities": "TotalLiabilities",
    "StockholdersEquity": "ShareholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "ShareholdersEquity",
    "RetainedEarningsAccumulatedDeficit": "RetainedEarnings",
    "CashAndCashEquivalentsAtCarryingValue": "CashAndEquivalents",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": "CashAndEquivalents",
    "AccountsReceivableNetCurrent": "AccountsReceivable",
    "InventoriesNet": "Inventories",
    "NetCashProvidedByUsedInOperatingActivities": "OperatingCashFlow",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations": "OperatingCashFlow",
    "NetCashProvidedByUsedInInvestingActivities": "InvestingCashFlow",
    "NetCashProvidedByUsedInFinancingActivities": "FinancingCashFlow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "CapitalExpenditure",
    "InterestExpense": "InterestExpense",
    "InterestIncomeExpenseNet": "InterestExpense",
    "LongTermDebtNoncurrent": "TotalDebt",
    "LongTermDebtCurrent": "TotalDebt",
    "DebtInstrumentCarryingAmount": "TotalDebt",
    "ShortTermBorrowings": "TotalDebt",
}

# ---------------------------------------------------------------------------
# IFRS taxonomy concept map (ifrs-full). Keyed by local name; resolution is
# namespace-scoped to ifrs-full only, so no cross-taxonomy collisions occur.
# IFRS uses different local names than US-GAAP (e.g. ifrs-full:Revenue vs
# us-gaap:Revenues, ifrs-full:Equity vs us-gaap:StockholdersEquity,
# ifrs-full:CashFlowsFromUsedInOperatingActivities vs
# us-gaap:NetCashProvidedByUsedInOperatingActivities).
# ---------------------------------------------------------------------------

_IFRS_CONCEPT_MAP: Dict[str, str] = {
    # --- Statement of profit or loss -----------------------------------
    "Revenue": "Revenue",
    "RevenueFromContractsWithCustomers": "Revenue",
    "GrossProfit": "GrossProfit",
    "ProfitLoss": "NetIncome",
    "ProfitLossFromContinuingOperations": "NetIncome",
    "ProfitLossAttributableToOwnersOfParent": "NetIncome",
    "ProfitLossFromOperatingActivities": "OperatingIncome",
    "OperatingProfitLoss": "OperatingIncome",
    "CostOfSales": "CostOfRevenue",
    "ResearchAndDevelopmentExpense": "ResearchAndDevelopment",
    "SellingGeneralAndAdministrativeExpense": "SellingGeneralAndAdmin",
    "GeneralAndAdministrativeExpense": "SellingGeneralAndAdmin",
    "SellingAndDistributionExpense": "SellingGeneralAndAdmin",
    "DistributionCosts": "SellingGeneralAndAdmin",
    "IncomeTaxExpenseContinuingOperations": "IncomeTax",
    "IncomeTaxExpense": "IncomeTax",
    "FinanceCosts": "InterestExpense",
    "InterestExpense": "InterestExpense",
    "DepreciationDepletionAndAmortisation": "DepreciationAmortization",
    "DepreciationAndAmortisationExpense": "DepreciationAmortization",
    "EarningsPerShareBasic": "EPS",
    "EarningsPerShareDiluted": "EPS",
    "EarningsPerShareBasicAndDiluted": "EPS",
    "BasicEarningsPerShare": "EPS",
    "DilutedEarningsPerShare": "EPS",
    # --- Statement of financial position --------------------------------
    "Assets": "TotalAssets",
    "Liabilities": "TotalLiabilities",
    "Equity": "ShareholdersEquity",
    "EquityAttributableToOwnersOfParent": "ShareholdersEquity",
    "CashAndCashEquivalents": "CashAndEquivalents",
    "RetainedEarnings": "RetainedEarnings",
    "Inventories": "Inventories",
    "TradeAndOtherCurrentReceivables": "AccountsReceivable",
    "CurrentTradeReceivables": "AccountsReceivable",
    "TradeAndOtherReceivables": "AccountsReceivable",
    "Borrowings": "TotalDebt",
    "TotalBorrowings": "TotalDebt",
    "LongTermBorrowings": "TotalDebt",
    "ShortTermBorrowings": "TotalDebt",
    # --- Statement of cash flows ----------------------------------------
    "CashFlowsFromUsedInOperatingActivities": "OperatingCashFlow",
    "NetCashFlowsFromUsedInOperatingActivities": "OperatingCashFlow",
    "CashFlowsFromUsedInInvestingActivities": "InvestingCashFlow",
    "CashFlowsFromUsedInFinancingActivities": "FinancingCashFlow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "CapitalExpenditure",
}

# Concepts kept even when unmapped (headline items worth surfacing)
_KEEP_UNMAPPED_CONCEPTS = {
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "ProfitLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
    "OperatingExpenses",
    "OperatingIncomeLoss",
    "InterestAndDividendIncomeOperating",
    "NonoperatingIncomeExpense",
    "OtherNonoperatingIncomeExpense",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "PropertyPlantAndEquipmentNet",
    "Goodwill",
    "IntangibleAssetsNetExcludingGoodwill",
    "MarketableSecuritiesCurrent",
    "MarketableSecuritiesNoncurrent",
}

# Token patterns ------------------------------------------------------------

# Matches full digit runs so 4-digit years are NEVER truncated into a
# 3-digit prefix + remainder ("1978" -> "197"+"8"), which previously
# produced phantom values like Revenue=197.0 with period FY1978 (Fix #4).
_NUMBER_INLINE_RE = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")
_BARE_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SMALL_INT_RE = re.compile(r"^\d{1,2}$")
_CURRENCY_SYMBOLS = set("$€£¥₹₽₩")
_CURRENCY_CODE_RE = re.compile(
    r"\b(USD|EUR|GBP|JPY|INR|CNY|CAD|AUD|CHF|HKD|SGD|NZD|SEK|NOK|"
    r"DKK|MXN|BRL|ZAR|RUB|KRW|IDR|MYR|THB|PLN|TRY|AED|SAR|VND|PHP)\b",
    re.IGNORECASE,
)
_SCALE_WORD_RE = re.compile(
    r"\b(millions?|billions?|crores?|lakhs?|thousands?|trillions?)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"%|percent", re.IGNORECASE)
# Fix #4 (period contamination): period detection is STRICTLY contextual.
# Explicit FY/Q tokens are always periods; bare years and dates only become
# periods when financial-period phraseology supports them and no
# contamination marker (founded/incorporated/during/note/page/...) is
# present. NO year blacklist — the rules are structural.
_FY_TOKEN_RE = re.compile(
    r"\b(FY\s?20\d{2}|F20\d{2}|Q[1-4][\s-]?FY\s?20\d{2}|Q[1-4][\s-]?20\d{2}|FY\s?\d{2})\b",
    re.IGNORECASE,
)
_BARE_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_PERIOD_PHRASE_RE = re.compile(
    r"\b(fiscal year|financial year|year ended|period ended|quarter ended|"
    r"month ended|for the year|for the period|as at|as of|comparative|"
    r"current year|previous year|prior year)\b",
    re.IGNORECASE,
)
_PERIOD_CONTAMINATION_RE = re.compile(
    r"\b(founded|incorporated|established|registered|commenced|since|during|"
    r"note|notes|page|pages|see|refer|reference|glossary|annexure|legal|"
    r"historical|until|per|against|over)\b",
    re.IGNORECASE,
)
_PREPOSITION_YEAR_RE = re.compile(r"\b(in|for|of|at|from)\s+$", re.IGNORECASE)
# Month-first ("March 31, 2025") AND day-first Indian format
# ("31 March 2025", "31st March, 2025").
_DATE_PERIOD_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}"
    r"(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)

SCALE_MULTIPLIERS = {
    "thousands": 1_000,
    "millions": 1_000_000,
    "crores": 10_000_000,
    "lakhs": 100_000,
    "billions": 1_000_000_000,
    "trillions": 1_000_000_000_000,
}

# Metrics whose legitimate value is a percentage (e.g. "Gross margin 40%").
# For every OTHER metric, a number immediately followed by '%' is a ratio /
# share ("accounted for 12 %") -- never the metric value itself.
PERCENTAGE_METRICS = {
    "GrossMargin",
    "OperatingMargin",
    "NetMargin",
}


class FinancialExtractorV2:
    """Structured-first financial fact extractor."""

    def __init__(self):
        self.table_extractor = TableExtractor()
        self.xbrl_extractor = XbrlExtractor()
        self.confidence_scorer = ConfidenceScorer()

    # ======================================================================
    # Main entry point
    # ======================================================================

    def extract_document(
        self,
        parsed_document: Dict[str, Any],
        file_name: Optional[str] = None,
        source_tier_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Extract financial facts from a parsed document dict
        (as produced by ingestion.parser).

        Returns:
            {
                "facts": [ExtractedFact-shaped dict, ...],
                "stats": {...}
            }
        """
        start_ns = __import__("time").time_ns()
        text = parsed_document.get("text", "") or ""
        doc_type = parsed_document.get("type", "")

        base_tier = source_tier_override or self._base_tier(doc_type)

        # --- 1. XBRL structured facts (highest priority) -------------------
        facts: List[Dict[str, Any]] = []
        xbrl_facts = parsed_document.get("xbrl_facts")
        if xbrl_facts:
            facts.extend(self._facts_from_xbrl(xbrl_facts, doc_type, base_tier))

        # --- 2. Structured tables ------------------------------------------
        tables = self.table_extractor.extract_from_parsed(parsed_document)
        table_facts = self._facts_from_tables(tables, doc_type, base_tier)
        facts.extend(table_facts)

        # --- 3. Layout-aware / contextual text extraction ------------------
        context_facts = self._extract_contextual(text, doc_type, base_tier)
        facts.extend(context_facts)

        # --- 4. Dedup by evidence hash (first/highest-priority wins) -------
        unique: Dict[str, Dict[str, Any]] = {}
        for fact in facts:
            h = fact.get("evidence_hash") or self._evidence_hash(fact)
            fact["evidence_hash"] = h
            if h not in unique:
                unique[h] = fact

        unique_facts = list(unique.values())
        elapsed_ms = (__import__("time").time_ns() - start_ns) / 1_000_000

        stats = {
            "facts_total": len(facts),
            "facts_unique": len(unique_facts),
            "duplicates_suppressed": len(facts) - len(unique_facts),
            "from_xbrl": sum(1 for f in facts if f.get("source_type") == "XBRL"),
            "from_tables": sum(1 for f in facts if "TABLE" in (f.get("source_type") or "")),
            "from_text": sum(1 for f in facts if f.get("source_type") == "TEXT"),
            "from_regex": sum(1 for f in facts if f.get("source_type") == "REGEX"),
            "tables_detected": len(tables),
            "extraction_time_ms": round(elapsed_ms, 2),
            "document_type": doc_type,
        }

        return {"facts": unique_facts, "stats": stats}

    # ======================================================================
    # 1. XBRL facts
    # ======================================================================

    def _facts_from_xbrl(
        self,
        xbrl_facts: List[Dict[str, Any]],
        doc_type: str,
        base_tier: int,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for raw in xbrl_facts:
            if isinstance(raw, XbrlFact):
                x = raw
            else:
                x = XbrlFact(**{k: v for k, v in raw.items() if k in XbrlFact.__dataclass_fields__})

            if x.value is None:
                continue

            local = x.local_name
            prefix = x.concept.split(":")[0] if ":" in x.concept else ""

            if prefix == "ifrs-full":
                # IFRS taxonomy: map known concepts to canonical metrics;
                # unknown IFRS concepts are PRESERVED as structured facts
                # (never silently discarded).
                metric_id = _IFRS_CONCEPT_MAP.get(local) or local
                accounting_basis = "IFRS"
            elif prefix == "us-gaap":
                metric_id = _XBRL_CONCEPT_MAP.get(local)
                if metric_id is None and local not in _KEEP_UNMAPPED_CONCEPTS:
                    continue  # keep evidence compact; only headline concepts
                metric_id = metric_id or local
                accounting_basis = "GAAP"
            else:
                # dei metadata or company/extension taxonomy: keep only
                # concepts with an explicit mapping (existing behavior).
                metric_id = _XBRL_CONCEPT_MAP.get(local)
                if metric_id is None:
                    if local in _KEEP_UNMAPPED_CONCEPTS:
                        metric_id = local
                    else:
                        continue
                accounting_basis = ""

            # Semantic definition is the ORIGINAL concept tag -- never collapsed
            definition = f"XBRL concept {x.concept}"

            fiscal_period = ""
            if x.fiscal_year:
                fiscal_period = (
                    f"{x.fiscal_quarter}{x.fiscal_year}"
                    if x.fiscal_quarter and x.fiscal_quarter != "FY"
                    else f"FY{x.fiscal_year}"
                )

            scale_meta = f"10^{x.scale}" if x.scale else ""
            if not scale_meta and "share" in (x.unit or "").lower():
                scale_meta = "per-share"  # e.g. EPS unit USD/shares
            elif not scale_meta and metric_id in PERCENTAGE_METRICS:
                scale_meta = "percentage"

            confidence = self.confidence_scorer.score(
                METHOD_XBRL,
                has_period=bool(x.period_end or x.instant),
                has_currency=bool(x.unit),
                has_unit_scale=bool(scale_meta),
                has_anchor=True,
            )

            out.append({
                "metric_id": metric_id,
                "metric_name": metric_id,
                "metric_definition": definition,
                "metric_value": x.value,
                "raw_value": x.raw_text,
                "normalized_value": x.value,
                "unit": x.unit,
                "scale": scale_meta or None,
                "currency_code": FinancialExtractorV2._currency_from_unit(x.unit),
                "currency_role": "REPORTING",
                "period_start": x.period_start,
                "period_end": x.period_end,
                "fiscal_period": fiscal_period,
                "taxonomy": prefix,
                "accounting_basis": accounting_basis,
                "scope": "",
                "source": "SEC XBRL",
                "source_tier": max(base_tier, 3),
                "source_type": "XBRL",
                "source_url": "",
                "filing_type": x.filing_type,
                "accession_number": x.accession_number,
                "amendment_relationship": "amendment" if x.is_amendment else "",
                "evidence_text_anchor": x.source_location,
                "confidence_score": confidence,
                "verification_status": "PENDING",
                "page": None,
                "table_id": None,
                "extraction_method": METHOD_XBRL,
            })
        return out

    # ======================================================================
    # 2. Table facts
    # ======================================================================

    def _facts_from_tables(
        self,
        tables: List[Table],
        doc_type: str,
        base_tier: int,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        is_html = doc_type in ("html", "SEC_HTML", "xbrl")
        method = METHOD_HTML_TABLE if is_html else (
            METHOD_PDF_TABLE if doc_type == "pdf" else METHOD_LAYOUT_AWARE
        )
        tier = max(base_tier, 2) if doc_type in ("xlsx", "csv") else base_tier

        for table in tables:
            scale = table.scale
            currency = table.currency
            has_period_cols = any(table.column_periods)

            for row in table.rows:
                label = (row.get("label") or "").strip()
                cells = row.get("cells") or []
                if not label or not cells:
                    continue

                metric_id = self._match_metric(label)
                if metric_id is None:
                    continue

                for idx, cell in enumerate(cells):
                    cell_text = (cell or "").strip()
                    if not cell_text:
                        continue

                    value, cell_neg = self._parse_cell_value(cell_text)
                    if value is None:
                        continue

                    # column_periods may include the label-column slot (first
                    # header has no period); align to the data cells.
                    col_periods = table.column_periods
                    if col_periods and not col_periods[0]:
                        col_periods = col_periods[1:]

                    period = ""
                    if col_periods and idx < len(col_periods):
                        period = col_periods[idx]
                    else:
                        period = self._find_period_in_table_context(
                            table.headers, label, cell_text
                        )

                    # Explicit table-level scale annotation (e.g. "(in ₹ millions)")
                    # is authoritative and applies unconditionally — scale is never
                    # inferred from the magnitude of a number (Fix #2). The magnitude
                    # guard applies ONLY to the header/row-text fallback heuristic.
                    explicit_scale = scale or ""
                    inferred_scale = self._find_scale_word(
                        f"{label} {cell_text}"
                    )
                    scale_meta = explicit_scale or inferred_scale
                    normalized = value
                    if explicit_scale and explicit_scale in SCALE_MULTIPLIERS:
                        normalized = value * SCALE_MULTIPLIERS[explicit_scale]
                    elif inferred_scale and abs(value) < 10_000:
                        normalized = value * SCALE_MULTIPLIERS.get(inferred_scale, 1)

                    confidence = self.confidence_scorer.score(
                        method,
                        has_period=bool(period),
                        has_currency=bool(currency),
                        has_unit_scale=bool(scale_meta),
                        has_anchor=True,
                    )

                    out.append(self._build_fact(
                        metric_id=metric_id,
                        metric_definition=f"Table row: {label}",
                        value=value,
                        raw_value=cell_text,
                        normalized_value=normalized,
                        unit="",
                        currency_code=currency,
                        currency_role="REPORTING",
                        fiscal_period=period,
                        source="Table",
                        source_tier=tier,
                        source_type="TABLE",
                        anchor=f"{table.source_location} | row: {label} | col: {idx + 1}",
                        confidence=confidence,
                        page=table.page,
                        table_id=table.table_id,
                        method=method,
                        scale_word=scale_meta,
                    ))
        return out

    @staticmethod
    def _parse_cell_value(cell_text: str) -> Optional[tuple]:
        """Parse a table cell into (value, is_negative)."""
        token = cell_text.strip()

        # Parenthesized accounting negative
        if token.startswith("(") and token.endswith(")"):
            neg = NegativeDetector.parse_parenthesized(token, context=cell_text)
            if neg is not None:
                return neg, True
            return None, False

        # Strip currency symbols and scale words, keep number
        cleaned = token
        for sym in _CURRENCY_SYMBOLS:
            cleaned = cleaned.replace(sym, "")
        for word in list(SCALE_MULTIPLIERS):
            cleaned = re.sub(rf"\b{word}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        m = re.fullmatch(r"(-?\d{1,3}(?:,\d{3})*(?:\.\d+)?)", cleaned)
        if not m:
            return None, False
        return float(cleaned.replace(",", "")), False

    # ======================================================================
    # 3. Contextual text extraction (guarded)
    # ======================================================================

    def _extract_contextual(
        self,
        text: str,
        doc_type: str,
        base_tier: int,
    ) -> List[Dict[str, Any]]:
        if not text:
            return []
        out: List[Dict[str, Any]] = []
        lower = text.lower()

        for metric_id, labels in METRIC_REGISTRY.items():
            for label in labels:
                for match in re.finditer(rf"\b{re.escape(label)}\b", lower):
                    window = text[match.end():match.end() + 280]
                    if not window.strip():
                        continue

                    value, raw_token, num_start, num_end = self._first_valid_number(
                        window, context_before=text[max(0, match.start() - 120):match.end()]
                    )
                    if value is None:
                        continue

                    # A number immediately followed by '%' is a ratio/share
                    # ("accounted for 12 %"), NOT the metric value -- unless
                    # the metric is itself a percentage metric.
                    if metric_id not in PERCENTAGE_METRICS:
                        after = window[num_end:num_end + 4]
                        if re.search(r"%|percent", after, re.IGNORECASE):
                            continue

                    # Contextual evidence required: currency, scale, % or period
                    has_currency = self._has_currency(window)
                    # Scale word must be adjacent to the number (inline scale)
                    nearby = window[max(0, num_start - 25):num_end + 45]
                    scale_word = self._find_scale_word(nearby)
                    has_percent = bool(_PERCENT_RE.search(window))
                    # Period must come from the window NEAR the value first;
                    # preceding text is only a fallback (avoids period bleed
                    # from an earlier sentence, e.g. FY2024 leaking into an
                    # FY2025 statement). Strong context (FY tokens, dated
                    # phrases) may come from anywhere in the value window; a
                    # bare preposition year ("in 2025") is accepted ONLY
                    # immediately after the extracted value — so "Revenue
                    # Service in 1978" never attaches FY1978 to an unrelated
                    # number. The far fallback never uses preposition years
                    # ("Founded in 1945. Revenue ..." stays unresolved) —
                    # Fix #4.
                    period = self._find_period_in_text(window[:200])
                    if not period:
                        period = self._find_period_in_text(
                            window[num_end:num_end + 40],
                            allow_preposition_year=True,
                        )
                    if not period:
                        # Far preceding-text fallback is limited to ~100
                        # chars (≈1 clause) so a period from several
                        # sentences earlier cannot bleed onto this fact
                        # (Fix #4: R&D=7360.5 was tagged FY2014 from 300
                        # chars of preceding narrative). Prefer unresolved.
                        period = self._find_period_in_text(
                            text[max(0, match.start() - 100):match.end()]
                        )
                    has_context = has_currency or scale_word or has_percent or bool(period)
                    if not has_context:
                        continue  # leave unresolved rather than guess

                    normalized = value
                    if scale_word:
                        normalized = value * SCALE_MULTIPLIERS.get(scale_word, 1)

                    currency = self._currency_in_text(window)
                    confidence = self.confidence_scorer.score(
                        METHOD_CONTEXTUAL_REGEX,
                        has_period=bool(period),
                        has_currency=has_currency,
                        has_unit_scale=bool(scale_word),
                        has_anchor=True,
                    )

                    out.append(self._build_fact(
                        metric_id=metric_id,
                        metric_definition=f"Contextual text match: {label}",
                        value=value,
                        raw_value=raw_token,
                        normalized_value=normalized,
                        unit="",
                        currency_code=currency,
                        currency_role="REPORTING",
                        fiscal_period=period,
                        source="Document text",
                        source_tier=base_tier,
                        source_type="TEXT",
                        anchor=raw_token,
                        confidence=confidence,
                        page=None,
                        table_id=None,
                        method=METHOD_CONTEXTUAL_REGEX,
                        scale_word=scale_word,
                    ))
        return out

    # ------------------------------------------------------------------
    # Guarded number extraction from a text window
    # ------------------------------------------------------------------

    @staticmethod
    def _first_valid_number(
        window: str,
        context_before: str = "",
    ) -> Optional[tuple]:
        """Return (value, raw_token, start, end) of the first number in
        `window` that passes the guards.

        Guards (reject):
          - bare fiscal years (2024, 2025) with no scale/currency/% context
          - small integers (1-20) with no financial context (page/footnote)
          - parenthesized footnote references (handled by NegativeDetector)
        """
        for m in _NUMBER_INLINE_RE.finditer(window):
            token = m.group(0)
            start = m.start()
            end = m.end()

            # Fix #4: a hyphen-minus attached to a word OR digit
            # ("COVID-19", "A-19", fiscal-year range "FY 2018-19") is NOT
            # a negative financial value — the regex would otherwise read
            # "COVID-19" as -19.0 or "FY 2018-19" as -19.0.
            if token.startswith("-") and start > 0 and window[start - 1].isalnum():
                continue

            # Fix #4: strip trailing commas BEFORE the guards so "31,"
            # from "March 31, 2023" is seen as "31" and rejected by the
            # small-int page/footnote guard instead of bypassing it (a
            # trailing comma broke ^\d{1,2}$ matching and produced
            # garbage values like IncomeTax=31.0 / R&D=1.0).
            while token.endswith(","):
                token = token[:-1]
                end -= 1

            if token.startswith("(") and token.endswith(")"):
                context = f"{context_before[-120:]} {window[max(0, start - 40):start + 60]}"
                neg = NegativeDetector.parse_parenthesized(token, context=context)
                if neg is not None:
                    return neg, token, start, end
                continue

            # Fix #4: a number immediately followed by a letter is an
            # identifier, not a financial value — "Section 115AC",
            # "Item 10-K", "Note 12b" — the regex would otherwise read
            # "Section 115AC" as IncomeTax=115.0.
            if end < len(window) and window[end].isalpha():
                continue

            num_str = token
            is_negative = num_str.startswith("-")
            cleaned = num_str.lstrip("-")

            # 1. A bare fiscal year (19xx/20xx with no thousands
            #    separator) is never a financial VALUE — reject
            #    unconditionally (Fix #4). "100% of sales from pure battery
            #    EVs by 2036" must not yield Revenue=2036, and "Income Tax
            #    Act 1961" must not yield IncomeTax=1961. (2,025 with a
            #    thousands separator remains a legitimate value.)
            if _BARE_YEAR_RE.match(cleaned) and "," not in cleaned:
                continue

            # 2. Small integer without financial context => page/footnote/cross-ref
            if _SMALL_INT_RE.match(cleaned) and "," not in cleaned and "." not in cleaned:
                near = f"{context_before[-80:]} {window[max(0, start - 40):start + 60]}"
                if not (FinancialExtractorV2._has_currency(near)
                        or FinancialExtractorV2._find_scale_word(near)
                        or _PERCENT_RE.search(near)):
                    continue

            try:
                value = float(cleaned.replace(",", ""))
            except ValueError:
                continue
            if is_negative:
                value = -value

            return value, token, start, end
        return None, None, None, None

    # ======================================================================
    # Shared fact builder
    # ======================================================================

    @staticmethod
    def _build_fact(
        metric_id: str,
        metric_definition: str,
        value: float,
        raw_value: str,
        normalized_value: float,
        unit: str,
        currency_code: str,
        currency_role: str,
        fiscal_period: str,
        source: str,
        source_tier: int,
        source_type: str,
        anchor: str,
        confidence: float,
        page: Optional[int],
        table_id: Optional[str],
        method: str,
        filing_type: str = "",
        accession_number: str = "",
        accounting_basis: str = "",
        scope: str = "",
        source_url: str = "",
        scale_word: str = "",
    ) -> Dict[str, Any]:
        scale_val = scale_word or FinancialExtractorV2._scale_of(raw_value, metric_definition)
        if not scale_val and metric_id in PERCENTAGE_METRICS:
            scale_val = "percentage"
        elif not scale_val and "share" in (unit or "").lower():
            scale_val = "per-share"
        fact = {
            "metric_id": metric_id,
            "metric_name": metric_id,
            "metric_definition": metric_definition,
            "metric_value": value,
            "raw_value": raw_value,
            "normalized_value": normalized_value,
            "unit": unit,
            "scale": scale_val,
            "currency_code": currency_code,
            "currency_role": currency_role,
            "fx_rate": None,
            "fx_source": None,
            "fx_timestamp": None,
            "period_start": None,
            "period_end": None,
            "fiscal_period": fiscal_period or None,
            "accounting_basis": accounting_basis,
            "scope": scope,
            "source": source,
            "source_tier": source_tier,
            "source_type": source_type,
            "source_url": source_url,
            "filing_type": filing_type,
            "accession_number": accession_number,
            "amendment_relationship": "",
            "evidence_text_anchor": anchor,
            "confidence_score": confidence,
            "verification_status": "PENDING",
            "page": page,
            "table_id": table_id,
            "extraction_method": method,
        }
        fact["evidence_hash"] = FinancialExtractorV2._evidence_hash(fact)
        return fact

    @staticmethod
    def _scale_of(raw_value: str, definition: str) -> Optional[str]:
        for word in SCALE_MULTIPLIERS:
            if re.search(rf"\b{word}\b", raw_value, re.IGNORECASE):
                return word
        m = re.search(r"10\^(\d+)", definition)
        if m:
            return f"10^{m.group(1)}"
        return None

    # ======================================================================
    # EvidenceItem compatibility + hashing
    # ======================================================================

    @staticmethod
    def to_evidence_item_dict(fact: Dict[str, Any]) -> Dict[str, Any]:
        """Map an ExtractedFact-shaped dict onto the EvidenceItem fields.

        Fix #2 (scale propagation): the EvidenceItem `value` is the
        NORMALIZED magnitude (never an ambiguous raw value), while the
        original extracted value and its scale/unit metadata are preserved
        alongside for auditability. The normalized value is also what the
        evidence hash and downstream conflict detection operate on, so
        equivalent magnitudes expressed in different scale notations
        (2,900,069 million vs 2,900.069 billion vs 290,006.9 crore) are
        recognized as the same fact instead of false conflicts.
        """
        raw_value = fact.get("metric_value")
        normalized = fact.get("normalized_value")
        if normalized is None:
            normalized = raw_value
        return {
            "fact_id": fact.get("metric_id", ""),
            "metric": fact.get("metric_name") or fact.get("metric_id", ""),
            "metric_definition": fact.get("metric_definition", ""),
            "value": normalized,
            "original_value": raw_value,
            "scale": fact.get("scale") or "",
            "normalized_value": normalized,
            "unit": fact.get("unit", ""),
            "currency_code": fact.get("currency_code", ""),
            "currency_role": fact.get("currency_role", ""),
            "fx_rate": fact.get("fx_rate"),
            "fx_source": fact.get("fx_source", ""),
            "fx_timestamp": fact.get("fx_timestamp"),
            "reporting_period": fact.get("fiscal_period", ""),
            "accounting_basis": fact.get("accounting_basis", ""),
            "scope": fact.get("scope", ""),
            "source": fact.get("source", ""),
            "source_tier": fact.get("source_tier", 1),
            "document_id": fact.get("source_url", ""),
            "page_section": fact.get("page") or fact.get("table_id") or "",
            "source_anchor": fact.get("evidence_text_anchor", ""),
            "confidence": fact.get("confidence_score", 0.0),
            "verification_status": fact.get("verification_status", "PENDING"),
            "evidence_hash": fact.get("evidence_hash", ""),
        }

    @staticmethod
    def _evidence_hash(fact: Dict[str, Any]) -> str:
        """SHA-256 hash identical in inputs to EvidenceSummaryState so
        downstream Agentic RAG deduplication works unchanged."""
        item = FinancialExtractorV2.to_evidence_item_dict(fact)
        return EvidenceSummaryState.compute_evidence_hash(item)

    # ======================================================================
    # Small helpers
    # ======================================================================

    @staticmethod
    def _match_metric(label: str) -> Optional[str]:
        lower = label.lower()
        best: Optional[str] = None
        best_len = 0
        for metric_id, labels in METRIC_REGISTRY.items():
            for lab in labels:
                if re.search(rf"\b{re.escape(lab)}\b", lower):
                    if len(lab) > best_len:
                        best = metric_id
                        best_len = len(lab)
        return best

    @staticmethod
    def _base_tier(doc_type: str) -> int:
        if doc_type in ("xbrl", "SEC_XBRL", "html", "SEC_HTML"):
            return 3
        if doc_type in ("xlsx", "csv"):
            return 2
        return 1

    @staticmethod
    @staticmethod
    def _currency_from_unit(unit: str) -> str:
        """Extract a pure ISO currency code from an XBRL unit string (Fix #5).

        XBRL units may be 'INR', 'USD/shares' (per-share facts) or
        non-currency units ('shares', 'pure'). The currency_code must be the
        bare ISO code (or empty) so currency compatibility checks never
        compare 'USD/shares' against 'USD'.
        """
        if not unit:
            return ""
        first = str(unit).split("/", 1)[0].strip()
        m = _CURRENCY_CODE_RE.search(first)
        return m.group(1) if m else ""

    @staticmethod
    def _has_currency(text: str) -> bool:
        if any(ch in text for ch in _CURRENCY_SYMBOLS):
            return True
        return bool(_CURRENCY_CODE_RE.search(text))

    @staticmethod
    def _currency_in_text(text: str) -> str:
        for sym, code in (("₹", "INR"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("$", "USD")):
            if sym in text:
                return code
        m = _CURRENCY_CODE_RE.search(text)
        return m.group(1).upper() if m else ""

    @staticmethod
    def _find_scale_word(text: str) -> str:
        m = _SCALE_WORD_RE.search(text)
        if not m:
            return ""
        word = m.group(0).lower()
        for scale, forms in (
            ("trillions", ("trillion", "trillions")),
            ("billions", ("billion", "billions")),
            ("crores", ("crore", "crores")),
            ("lakhs", ("lakh", "lakhs")),
            ("millions", ("million", "millions")),
            ("thousands", ("thousand", "thousands")),
        ):
            if word in forms:
                return scale
        return ""

    @staticmethod
    def _has_period_phrase(text: str) -> bool:
        """True when financial-period phraseology precedes the candidate."""
        return bool(_PERIOD_PHRASE_RE.search(text))

    @staticmethod
    def _has_contamination(text: str) -> bool:
        """True when a contamination marker (founded/incorporated/during/
        note/page/glossary/...) is structurally present. These are context
        markers, NOT a year blacklist (Fix #4)."""
        return bool(_PERIOD_CONTAMINATION_RE.search(text))

    @staticmethod
    def _find_period_in_text(
        text: str,
        allow_preposition_year: bool = False,
    ) -> str:
        """Strictly contextual period detection (Fix #4).

        A year becomes a financial period ONLY when structural context
        supports that interpretation:

          1. Explicit FY/Q tokens (FY2025, Q1FY2025, F2025, FY25) are
             always periods.
          2. A date expression ("March 31, 2025", "31 March 2025")
             preceded by financial-period phraseology ("year ended",
             "as at", "as of", "for the year", "period ended", ...)
             is a period.
          3. A bare year preceded by period phraseology ("fiscal year
             2025", "comparative 2023") is a period.
          4. When allow_preposition_year is set (value window only), a
             bare year directly preceded by a preposition ("in 2025",
             "for 2024") is a period. The far preceding-text fallback
             NEVER uses this rule, so "Founded in 1945"-style years in
             earlier sentences cannot bleed into the fact.

        Contamination markers (founded/incorporated/during/note/page/
        glossary/...) block the interpretation. A bare year with no
        contextual support returns "" (unresolved, never guessed).
        """
        m = _FY_TOKEN_RE.search(text)
        if m:
            return FinancialExtractorV2._normalize_period(m.group(0))

        for dm in _DATE_PERIOD_RE.finditer(text):
            pre = text[max(0, dm.start() - 40):dm.start()]
            if FinancialExtractorV2._has_contamination(pre):
                continue
            if FinancialExtractorV2._has_period_phrase(pre):
                year = re.search(r"(19|20)\d{2}", dm.group(0))
                if year:
                    return f"FY{year.group(0)}"

        for ym in _BARE_YEAR_RE.finditer(text):
            pre = text[max(0, ym.start() - 25):ym.start()]
            if FinancialExtractorV2._has_contamination(pre):
                continue
            if FinancialExtractorV2._has_period_phrase(pre):
                return f"FY{ym.group(0)}"
            if allow_preposition_year:
                near = text[max(0, ym.start() - 8):ym.start()]
                if _PREPOSITION_YEAR_RE.search(near):
                    return f"FY{ym.group(0)}"
        return ""

    @staticmethod
    def _find_period_in_table_context(
        headers: List[Any],
        label: str,
        cell_text: str,
    ) -> str:
        """Table-header-aware period detection (Fix #4 requirement N).

        Table headers are authoritative: a bare year in the header row is
        a legitimate comparative-period column ("2025 2024 2023"), never
        contamination. Falls back to strict text rules when headers carry
        no period information.
        """
        header_text = " ".join(str(h) for h in headers if str(h).strip())
        if header_text and not FinancialExtractorV2._has_contamination(header_text):
            m = _FY_TOKEN_RE.search(header_text)
            if m:
                return FinancialExtractorV2._normalize_period(m.group(0))
            ym = _BARE_YEAR_RE.search(header_text)
            if ym:
                return f"FY{ym.group(0)}"
        return FinancialExtractorV2._find_period_in_text(f"{label} {cell_text}")

    @staticmethod
    def _normalize_period(token: str) -> str:
        t = re.sub(r"\s+", "", token.strip().upper())  # "FY 2018" -> "FY2018"
        if re.fullmatch(r"FY\d{2}", t):
            return f"FY20{t[2:]}"
        m = re.fullmatch(r"Q([1-4])[\s-]?FY20(\d{2})", t)
        if m:
            return f"Q{m.group(1)}FY20{m.group(2)}"
        m2 = re.fullmatch(r"Q([1-4])[\s-]?20(\d{2})", t)
        if m2:
            return f"Q{m2.group(1)}FY20{m2.group(2)}"
        m3 = re.fullmatch(r"20(\d{2})", t)
        if m3:
            return f"FY20{m3.group(1)}"
        return t


def extract_financial_facts_v2(
    parsed_document: Dict[str, Any],
    file_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Module-level convenience wrapper (compatible with the old
    `extract_financial_data` call shape but returns structured facts)."""
    return FinancialExtractorV2().extract_document(parsed_document, file_name=file_name)
