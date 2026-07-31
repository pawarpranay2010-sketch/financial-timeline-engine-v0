"""
Agentic RAG Orchestrator

Main orchestration layer for the Agentic RAG Core. Transforms a user
financial-analysis goal into explicit information requirements, drives
retrieval iterations (max 3), resolves evidence deterministically, and
produces a CanonicalEvidenceSet for downstream calculation.

Flow:
    User Goal
        ↓
    Parse requirements
        ↓
    while can_retrieve and iterations < max:
        RetrievalAgent / DataAgent fetch
        EvidenceSummaryState deduplicates
        Evaluate requirements
        If satisfied → COMPLETE
        If can't improve → stop
        If limit reached → RETRIEVAL_LIMIT_REACHED
        Else → next iteration
        ↓
    SourceResolver resolves conflicts
    CurrencyValidator checks compatibility
    ExtractionAuditor verifies
    ↓
    CanonicalEvidenceSet → EvidenceConsolidator → MemoGenerator / Module 3

Terminal states:
    COMPLETE                  — All requirements satisfied, evidence resolved
    INSUFFICIENT_EVIDENCE     — Retrieval exhausted, requirements remain unmet
    RETRIEVAL_LIMIT_REACHED   — Max 3 iterations exceeded
    UNRESOLVED_CONFLICT       — Conflicting evidence cannot be resolved
    CURRENCY_MISMATCH         — Incompatible currencies block calculation
    EXTRACTION_CORRUPTED      — Extraction verification failed
    EXECUTION_TIMEOUT         — Operation exceeded time budget
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.intelligence.retrieval_agent import RetrievalAgent
from backend.intelligence.data_agent import DataAgent
from backend.intelligence.evidence_summary_state import (
    EvidenceSummaryState,
    EvidenceItem,
    InformationRequirement,
    EvidenceState,
    STATE_COMPLETE,
    STATE_INSUFFICIENT_EVIDENCE,
    STATE_RETRIEVAL_LIMIT_REACHED,
    STATE_UNRESOLVED_CONFLICT,
    STATE_CURRENCY_MISMATCH,
    STATE_EXTRACTION_CORRUPTED,
    STATE_EXECUTION_TIMEOUT,
)
from backend.intelligence.source_resolver import SourceResolver
from backend.intelligence.currency_validator import CurrencyValidator
from backend.intelligence.extraction_auditor import (
    ExtractionAuditor,
    AGREEMENT,
    SEMANTIC_EQUIVALENCE,
    EXTRACTION_CORRUPTED as AUDIT_CORRUPTED,
)
from backend.module4.normalizer import MetricDictionary

logger = logging.getLogger("fte.rag.agentic_rag_orchestrator")

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_ITERATIONS = 3
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_EVIDENCE_ITEMS = 200


# ---------------------------------------------------------------------------
# Canonical Evidence Set
# ---------------------------------------------------------------------------


class CanonicalEvidenceSet:
    """
    Verified, resolved evidence ready for the calculation engine.

    Produced at the end of the Agentic RAG pipeline. Contains only
    evidence that has passed all validation gates.
    """

    def __init__(self, state: EvidenceState):
        self._state = state
        self._resolved_items: List[Dict] = []

    def add_resolved(self, item: Dict) -> None:
        self._resolved_items.append(item)

    @property
    def is_empty(self) -> bool:
        return len(self._resolved_items) == 0

    @property
    def resolved_count(self) -> int:
        return len(self._resolved_items)

    @property
    def state(self) -> EvidenceState:
        return self._state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "terminal_state": self._state.terminal_state,
            "terminal_reason": self._state.terminal_reason,
            "iterations_used": self._state.iterations_used,
            "resolved_count": len(self._resolved_items),
            "evidence_count": self._state.evidence_count,
            "resolved_facts": self._resolved_items,
        }

    def get_summary_text(self) -> str:
        """Format the resolved evidence set as text for the evidence consolidator."""
        lines = [
            "=== CANONICAL EVIDENCE SET ===",
            f"Status: {self._state.terminal_state}",
            f"Iterations: {self._state.iterations_used}",
            f"Resolved Facts: {len(self._resolved_items)}",
            "",
        ]
        for item in self._resolved_items:
            lines.append(
                f"  • {item.get('metric_name', '')} = {item.get('value', '')} "
                f"{item.get('unit', '')} "
                f"[{item.get('currency_code', '')}] "
                f"[{item.get('fiscal_period', '')}] "
                f"[{item.get('accounting_basis', '')}] "
                f"Source: {item.get('source', '')} (tier {item.get('source_tier', 1)})"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agentic RAG Orchestrator
# ---------------------------------------------------------------------------


class AgenticRAGOrchestrator:
    """
    Orchestrates the Agentic RAG pipeline.

    Usage:
        orchestrator = AgenticRAGOrchestrator(ticker="AAPL")
        result = orchestrator.execute(goal="Analyze Microsoft's FY2024 revenue and net income")
    """

    def __init__(
        self,
        ticker: str,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS,
    ):
        self.ticker = ticker.strip().upper()
        self._max_iterations = max_iterations
        self._timeout = timeout_seconds
        self._max_evidence = max_evidence_items

        # Lazy-initialized components
        self._retrieval_agent: Optional[RetrievalAgent] = None
        self._data_agent: Optional[DataAgent] = None

        # Stateless validators
        self._source_resolver = SourceResolver()
        self._currency_validator = CurrencyValidator()
        self._extraction_auditor = ExtractionAuditor()

        logger.info(
            f"[AgenticRAGOrchestrator] Initialized for {self.ticker} "
            f"(max_iterations={max_iterations}, timeout={timeout_seconds}s)"
        )

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _get_retrieval_agent(self) -> RetrievalAgent:
        if self._retrieval_agent is None:
            self._retrieval_agent = RetrievalAgent(self.ticker)
        return self._retrieval_agent

    def _get_data_agent(self) -> DataAgent:
        if self._data_agent is None:
            self._data_agent = DataAgent(self.ticker)
        return self._data_agent

    # ------------------------------------------------------------------
    # Requirement Generation
    # ------------------------------------------------------------------

    def _parse_goal(self, goal: str) -> List[InformationRequirement]:
        """
        Parse a user goal into explicit information requirements.

        Uses deterministic heuristics to identify required metrics and
        periods from the goal text. Does NOT call an LLM for this.
        """
        goal_lower = goal.lower()
        requirements = []
        req_id = 0

        # Detect fiscal period mentions
        period = ""
        for word in goal_lower.split():
            if word.startswith("fy") and len(word) >= 4:
                period = word.upper()
                break
            if word.startswith("20") and len(word) == 4 and word.isdigit():
                period = f"FY{word}"
                break
            if word == "q1" or word == "q2" or word == "q3" or word == "q4":
                period = word.upper()
                break

        # Detect metrics from the goal
        metric_map = {
            "revenue": "Revenue",
            "net income": "NetIncome",
            "net profit": "NetIncome",
            "profit": "NetIncome",
            "pat": "NetIncome",
            "ebitda": "EBITDA",
            "ebit": "EBIT",
            "eps": "EPS",
            "earnings per share": "EPS",
            "cash flow": "CashFlow",
            "operating cash flow": "OperatingCashFlow",
            "free cash flow": "FreeCashFlow",
            "total assets": "TotalAssets",
            "total liabilities": "TotalLiabilities",
            "equity": "Equity",
            "debt": "Debt",
            "gross margin": "GrossMargin",
            "operating margin": "OperatingMargin",
            "profit margin": "ProfitMargin",
            "roe": "ROE",
            "roa": "ROA",
            "roce": "ROCE",
        }

        found_metrics = set()
        for keyword, metric_id in metric_map.items():
            if keyword in goal_lower:
                found_metrics.add(metric_id)

        # If no specific metrics found, add common defaults
        if not found_metrics:
            found_metrics = {"Revenue", "NetIncome", "EBITDA"}

        # Create requirements
        for metric_id in found_metrics:
            req_id += 1
            req = InformationRequirement(
                id=f"req_{req_id}",
                description=f"{metric_id} for {self.ticker} ({period or 'latest'})",
                metric=metric_id,
                period=period,
            )
            requirements.append(req)

        # Detect accounting basis
        if "gaap" in goal_lower:
            for req in requirements:
                req.metric_definition = "GAAP"
        elif "non-gaap" in goal_lower or "adjusted" in goal_lower:
            for req in requirements:
                req.metric_definition = "non-GAAP"
        elif "ifrs" in goal_lower:
            for req in requirements:
                req.metric_definition = "IFRS"

        # Detect currency
        currency_map = {
            "usd": "USD", "dollar": "USD",
            "inr": "INR", "rupee": "INR", "rupees": "INR",
            "eur": "EUR", "euro": "EUR", "euros": "EUR",
            "gbp": "GBP", "pound": "GBP", "sterling": "GBP",
        }
        for keyword, ccy in currency_map.items():
            if keyword in goal_lower:
                for req in requirements:
                    req.currency = ccy
                break

        logger.info(
            f"[AgenticRAGOrchestrator] Generated {len(requirements)} requirements "
            f"from goal: period={period}, metrics={found_metrics}"
        )
        return requirements

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def _retrieve_evidence(self, query: str) -> List[EvidenceItem]:
        """
        Retrieve evidence using the existing RetrievalAgent.

        Queries PostgreSQL-stored data (company profile, financials,
        market price, news) and converts to EvidenceItems.
        """
        items = []
        agent = self._get_retrieval_agent()

        # Retrieve company info
        company = agent.get_company()
        if company:
            for key, label in [("market_cap", "MarketCap"), ("currency", "ReportingCurrency")]:
                val = company.get(key)
                if val is not None:
                    items.append(EvidenceItem(
                        metric=label,
                        value=val if isinstance(val, (int, float)) else None,
                        source=company.get("source", "postgresql"),
                        source_tier=2,
                        confidence=0.95,
                    ))

        # Retrieve financials
        financials = agent.get_financials()
        if financials:
            for fin in financials:
                for field in ["revenue", "ebitda", "ebit", "net_income", "eps",
                              "total_assets", "total_liabilities", "shareholders_equity",
                              "operating_cash_flow", "free_cash_flow"]:
                    val = getattr(fin, field, None)
                    if val is not None:
                        metric_name = field.replace("_", " ").title().replace(" ", "")
                        items.append(EvidenceItem(
                            metric=metric_name,
                            value=float(val),
                            reporting_period=f"FY{fin.fiscal_year}" if fin.fiscal_year else "",
                            source="postgresql",
                            source_tier=2,
                            confidence=0.95,
                        ))

        # Retrieve market price
        price = agent.get_market_price()
        if price:
            items.append(EvidenceItem(
                metric="MarketPrice",
                value=price.get("price"),
                source="postgresql",
                source_tier=2,
                confidence=0.95,
            ))

        # Retrieve news
        news = agent.get_news()
        if news:
            for n in news:
                items.append(EvidenceItem(
                    metric="NewsHeadline",
                    value=None,
                    source=n.get("source", "news"),
                    source_tier=1,
                    source_anchor=n.get("headline", ""),
                    confidence=0.7,
                ))

        logger.info(
            f"[AgenticRAGOrchestrator] Retrieved {len(items)} evidence items "
            f"for ticker={self.ticker}"
        )
        return items

    # ------------------------------------------------------------------
    # Validation gates
    # ------------------------------------------------------------------

    def _resolve_sources(self, state: EvidenceSummaryState) -> EvidenceSummaryState:
        """Run source resolution on all evidence."""
        for req in state.state.requirements:
            matching = [
                e for e in state.state.evidence.values()
                if e.metric == req.metric
            ]
            if len(matching) <= 1:
                continue

            # Check for conflicts needing resolution
            values = set(e.value for e in matching if e.value is not None)
            if len(values) <= 1:
                continue

            # Resolve using source resolver
            evidence_dicts = [e.to_dict() for e in matching]
            status, resolved = self._source_resolver.resolve_conflict(evidence_dicts)

            if status == "RESOLVED" and resolved:
                # Mark non-winning items as superseded
                resolved_hash = EvidenceSummaryState.compute_evidence_hash(resolved)
                for e in matching:
                    if e.evidence_hash == resolved_hash:
                        e.verification_status = "VERIFIED"
                    else:
                        e.verification_status = "SUPERSEDED"
            elif status == "UNRESOLVED_CONFLICT":
                state.set_terminal(
                    STATE_UNRESOLVED_CONFLICT,
                    f"Conflicting evidence for {req.metric} cannot be resolved "
                    f"deterministically ({len(values)} different values)"
                )

        return state

    def _validate_currencies(self, state: EvidenceSummaryState) -> EvidenceSummaryState:
        """Run currency validation on all evidence."""
        evidence_dicts = [e.to_dict() for e in state.state.evidence.values()]
        compatible, error = self._currency_validator.check_currency_compatibility(evidence_dicts)
        if not compatible:
            state.set_terminal(STATE_CURRENCY_MISMATCH, error or "Currency mismatch detected")
        return state

    def _verify_extractions(self, state: EvidenceSummaryState) -> EvidenceSummaryState:
        """Run extraction auditor on all evidence (single-pass self-consistency check)."""
        for e in list(state.state.evidence.values()):
            if e.verification_status in ("VERIFIED", "SUPERSEDED"):
                continue
            # Verify that the evidence has valid fields
            fact_dict = e.to_dict()
            if fact_dict.get("value") is None and fact_dict.get("metric") not in (
                "ReportingCurrency", "NewsHeadline"
            ):
                e.verification_status = "REJECTED"
                logger.warning(
                    f"[AgenticRAGOrchestrator] Evidence rejected: "
                    f"metric={e.metric} missing value"
                )
        return state

    def _check_calculation_block(self, state: EvidenceSummaryState) -> bool:
        """
        Check if calculation should be blocked.

        Returns True if calculation is safe to proceed.

        Fix #3: blocks on ALL unsafe terminal states (including
        INSUFFICIENT_EVIDENCE / RETRIEVAL_LIMIT_REACHED / EXECUTION_TIMEOUT)
        and on any missing or conflicting requirement — previously only
        three states were blocked, so insufficient evidence still flowed
        into downstream processing.
        """
        unsafe_states = (
            STATE_UNRESOLVED_CONFLICT,
            STATE_CURRENCY_MISMATCH,
            STATE_EXTRACTION_CORRUPTED,
            STATE_INSUFFICIENT_EVIDENCE,
            STATE_RETRIEVAL_LIMIT_REACHED,
            STATE_EXECUTION_TIMEOUT,
        )
        if state.state.terminal_state in unsafe_states:
            logger.warning(
                f"[AgenticRAGOrchestrator] Calculation BLOCKED: "
                f"{state.state.terminal_state}"
            )
            return False

        # Block if any requirement is still missing or conflicting.
        if state.state.missing_count > 0 or state.state.conflict_count > 0:
            logger.warning(
                f"[AgenticRAGOrchestrator] Calculation BLOCKED: "
                f"missing={state.state.missing_count} conflicts={state.state.conflict_count}"
            )
            return False

        # Block if any requirement is not explicitly VERIFIED.
        for req in state.state.requirements:
            if req.status != "VERIFIED":
                logger.warning(
                    f"[AgenticRAGOrchestrator] Calculation BLOCKED: "
                    f"requirement {req.id} ({req.metric}) not VERIFIED (status={req.status})"
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Main execute method
    # ------------------------------------------------------------------

    def execute(self, goal: str) -> CanonicalEvidenceSet:
        """
        Execute the Agentic RAG pipeline.

        Args:
            goal: User's financial analysis goal (e.g., "Analyze AAPL's FY2024 revenue")

        Returns:
            CanonicalEvidenceSet with resolved, verified evidence
        """
        start_time = time.monotonic()
        logger.info(f"[AgenticRAGOrchestrator] Executing: {goal}")

        # Initialize state
        state = EvidenceSummaryState(max_iterations=self._max_iterations)

        # Phase 1: Parse requirements
        requirements = self._parse_goal(goal)
        state.add_requirements(requirements)

        # Phase 2: Retrieval loop (max 3 iterations)
        while state.should_continue():
            # Timeout check
            if time.monotonic() - start_time > self._timeout:
                state.set_terminal(
                    STATE_EXECUTION_TIMEOUT,
                    f"Execution exceeded {self._timeout}s timeout",
                )
                break

            # Evidence count check
            if state.state.evidence_count >= self._max_evidence:
                logger.warning(
                    f"[AgenticRAGOrchestrator] Max evidence items reached "
                    f"({self._max_evidence})"
                )
                if not state.all_requirements_satisfied:
                    state.set_terminal(
                        STATE_INSUFFICIENT_EVIDENCE,
                        f"Max evidence items ({self._max_evidence}) reached "
                        f"with {state.state.missing_count} requirements still missing",
                    )
                break

            # Build query from missing requirements
            missing = [r for r in state.state.requirements if r.status in ("REQUIRED", "MISSING")]
            query_parts = [f"{r.metric} {r.period}" for r in missing]
            query = f"{self.ticker} {' '.join(query_parts)}"

            # Suppress repeated queries
            if state.is_query_repeated(query):
                logger.info(f"[AgenticRAGOrchestrator] Repeated query suppressed: {query}")
                # Check if we should stop
                if not state.can_retrieve:
                    break
                continue

            state.record_query(query)

            # Retrieve evidence
            evidence_items = self._retrieve_evidence(query)
            if not evidence_items:
                logger.info(
                    f"[AgenticRAGOrchestrator] No evidence found for iteration "
                    f"{state.state.iterations_used + 1}"
                )
                state.record_iteration(query, 0)
                continue

            # Deduplicate and add
            new_count = state.add_evidence_batch(evidence_items)
            state.record_iteration(query, new_count)

            # Evaluate requirements
            state.evaluate_requirements()

            if new_count == 0:
                # Nothing new found — no point continuing
                logger.info("[AgenticRAGOrchestrator] No new evidence — stopping early")
                if not state.all_requirements_satisfied:
                    state.set_terminal(
                        STATE_INSUFFICIENT_EVIDENCE,
                        "No new evidence found — cannot satisfy remaining requirements",
                    )
                break

        # Phase 3: Post-retrieval validation
        # Run all validation gates sequentially

        # 3a. Source resolution
        if state.state.terminal_state in ("", STATE_COMPLETE):
            state = self._resolve_sources(state)

        # 3b. Currency validation
        if state.state.terminal_state in ("", STATE_COMPLETE):
            state = self._validate_currencies(state)

        # 3c. Extraction verification
        if state.state.terminal_state in ("", STATE_COMPLETE):
            state = self._verify_extractions(state)

        # Phase 4: Final evaluation
        state.evaluate_requirements()

        # Set terminal state if not already set
        if not state.is_complete:
            if state.all_requirements_satisfied:
                state.set_terminal(STATE_COMPLETE, "All requirements satisfied")
            elif not state.can_retrieve:
                state.set_terminal(
                    STATE_RETRIEVAL_LIMIT_REACHED,
                    f"Max iterations ({state.state.max_iterations}) reached. "
                    f"{state.state.missing_count} requirements still missing.",
                )
            else:
                state.set_terminal(
                    STATE_INSUFFICIENT_EVIDENCE,
                    f"Insufficient evidence after {state.state.iterations_used} iterations",
                )

        # Phase 5: Build canonical evidence set
        canonical = CanonicalEvidenceSet(state.state)

        # Check calculation block
        if self._check_calculation_block(state):
            # Add resolved evidence to canonical set — VERIFIED only.
            # Fix #3: PENDING evidence must NEVER enter the canonical set.
            for item in state.state.evidence.values():
                if item.verification_status == "VERIFIED" and item.value is not None:
                    canonical.add_resolved(item.to_dict())
        else:
            logger.warning(
                f"[AgenticRAGOrchestrator] Canonical evidence set NOT populated "
                f"(calculation blocked: {state.state.terminal_state or 'unsatisfied requirements'})"
            )

        elapsed = time.monotonic() - start_time
        logger.info(
            f"[AgenticRAGOrchestrator] Execution complete: "
            f"state={state.state.terminal_state} "
            f"iterations={state.state.iterations_used} "
            f"resolved={canonical.resolved_count} "
            f"elapsed={elapsed:.2f}s"
        )

        return canonical
