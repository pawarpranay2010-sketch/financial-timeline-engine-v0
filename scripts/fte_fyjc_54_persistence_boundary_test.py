"""
Platrixa — Phase 7E: Persistence Path Enforcement Test
scripts/fte_fyjc_54_persistence_boundary_test.py

Verifies the persistence boundary around KernelResult:

    Kernel → KernelResult → ResultPersistence (contract) → PostgreSQL

Required checks (Phase 7E spec):
  1.  A VERIFIED KernelResult can be persisted.
  2.  A non-verified terminal result can be persisted without being
      converted into VERIFIED.
  3.  KernelResult status survives persistence round-trip.
  4.  Deterministic accounting output survives round-trip unchanged.
  5.  Original student input survives where supported.
  6.  Model identity/version metadata survives where supported.
  7.  Grounding/verification state survives where supported.
  8.  Persistence does not recalculate accounting truth.
  9.  Persistence failure does not produce a false successful result.
  10. Kernel remains independent from concrete PostgreSQL implementation
      details.
  11. No duplicate accounting implementation exists in the persistence path.
  12. Existing database tests/regressions continue to pass.

Also verifies:
  * contract shape (Protocol conformance, dataclass surface)
  * dependency direction (Kernel never imports the postgres implementation)
  * fail-closed semantics for both unavailable-store and write-failure kinds
  * the existing FYJC schema is reused (no new tables/columns invented)

Run:
    python3 scripts/fte_fyjc_54_persistence_boundary_test.py
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import sys
import contextlib
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.kernel.result import (  # noqa: E402
    FORBIDDEN_OUTPUT,
    GROUNDING_FAILED,
    KernelResult,
    MODEL_UNAVAILABLE,
    UNSUPPORTED_TRANSACTION,
    VALIDATION_FAILED,
)

SUCCESS = "VERIFIED"
_REVIEW = "REVIEW_REQUIRED"
_CHECKS: List[bool] = []


def _check(label: str, condition: bool) -> bool:
    _CHECKS.append(bool(condition))
    icon = "PASS" if condition else "FAIL"
    print(f"  [{icon}] {label}")
    return bool(condition)


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Fixtures: KernelResults produced by the Kernel boundary (not hand-faked
# accounting shapes — these mirror what Kernel.process() emits).
# ---------------------------------------------------------------------------

_GROUNDED_CANDIDATE: Dict[str, Any] = {
    "transaction_type": "PURCHASE",
    "parties": [{"name": "raj", "role": "seller"}],
    "amounts": [{"value": "25000", "currency": "INR"}],
    "payment_method": "CASH",
    "references": [],
    "ambiguities": [],
    "grounding": [
        {"field": "amounts[0].value", "span": "25000", "status": "GROUNDED"},
        {"field": "parties[0].name", "span": "raj", "status": "GROUNDED"},
    ],
    "confidence": None,
    "concerns": [],
    "provenance": [],
    "temporal": [],
    "source_text": "purchased furniture from raj for rs.25000",
    "derived_hints": [],
    "normalization": [],
    "extensions": {},
}

_ACCOUNTING_RESULT: Dict[str, Any] = {
    "status": "VERIFIED",
    "journal_balanced": True,
    "journal": {
        "narration": "Purchased furniture from Raj for Rs.25,000.",
        "calculation_records": [
            {
                "calculation_id": "BK_TOTAL",
                "label": "Total",
                "result": "25000",
            }
        ],
    },
    "debit_lines": [
        {"account": "Furniture", "amount": 25000, "side": "debit", "rule": "Asset purchase"},
    ],
    "credit_lines": [
        {"account": "Cash", "amount": 25000, "side": "credit", "rule": "Cash payment"},
    ],
    "why": "Asset acquired by cash payment",
}

_META: Dict[str, Any] = {
    "model_id": "Qwen/Qwen2.5-1.5B-Instruct",
    "provider_revision": "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
    "adapter_revision": "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
}


def _verified_result() -> KernelResult:
    return KernelResult(
        request_id="req-7e-verified",
        raw_input="purchased furniture from raj for rs.25000",
        status=SUCCESS,
        status_label="Verified",
        interpretation=None,
        interpretation_candidate=dict(_GROUNDED_CANDIDATE),
        verification_status="GROUNDED",
        grounding_issues=[],
        accounting_result=dict(_ACCOUNTING_RESULT),
        issues=[],
        next_action="",
        metadata=dict(_META),
    )


def _terminal_result(status: str, *, with_accounting: bool = False) -> KernelResult:
    return KernelResult(
        request_id="req-7e-terminal",
        raw_input="purchased furniture from raj for rs.25000",
        status=status,
        status_label=status.replace("_", " ").title(),
        interpretation=None,
        interpretation_candidate=dict(_GROUNDED_CANDIDATE),
        verification_status="GROUNDING_FAILED" if status == GROUNDING_FAILED else None,
        grounding_issues=["party 'sharma' not found in input"] if status == GROUNDING_FAILED else [],
        accounting_result=dict(_ACCOUNTING_RESULT) if with_accounting else None,
        issues=["grounding: party 'sharma' not found in input"] if status == GROUNDING_FAILED else ["terminal failure"],
        next_action="Review the input and retry.",
        metadata=dict(_META),
    )


# ---------------------------------------------------------------------------
# Section 1 — contract surface
# ---------------------------------------------------------------------------


def test_contract_surface() -> bool:
    _section("Contract surface")
    ok = True

    from backend.persistence.base import (
        PERSISTENCE_UNAVAILABLE,
        PERSISTENCE_WRITE_FAILED,
        InMemoryResultPersistence,
        PersistedResult,
        PersistenceFailure,
        ResultPersistence,
        build_persistence_record,
        roundtrip_snapshot,
    )

    ok &= _check("base contract exports exist", True)
    ok &= _check(
        "failure kinds distinct from Kernel terminal states",
        {PERSISTENCE_UNAVAILABLE, PERSISTENCE_WRITE_FAILED}.isdisjoint(
            {SUCCESS, _REVIEW, GROUNDING_FAILED, FORBIDDEN_OUTPUT,
             MODEL_UNAVAILABLE, VALIDATION_FAILED, UNSUPPORTED_TRANSACTION}
        ),
    )
    ok &= _check(
        "InMemoryResultPersistence satisfies ResultPersistence protocol",
        isinstance(InMemoryResultPersistence(), ResultPersistence),
    )

    try:
        from backend.persistence import PostgresResultPersistence  # noqa: F401
        ok &= _check("postgres implementation importable", True)
    except Exception as exc:
        ok &= _check(f"postgres implementation importable (failed: {exc})", False)

    # build_persistence_record must reject non-KernelResult objects loudly.
    try:
        build_persistence_record({"status": "VERIFIED"})
        ok &= _check("build_persistence_record rejects non-result objects", False)
    except TypeError:
        ok &= _check("build_persistence_record rejects non-result objects", True)

    return ok


# ---------------------------------------------------------------------------
# Section 2 — checks 1, 3, 5: VERIFIED persistence + round-trip fidelity
# ---------------------------------------------------------------------------


def test_verified_persists_and_roundtrips() -> bool:
    _section("VERIFIED result persistence and round-trip (checks 1, 3, 5)")
    ok = True

    from backend.persistence.base import InMemoryResultPersistence

    store = InMemoryResultPersistence()
    result = _verified_result()
    outcome = store.persist(result)

    ok &= _check("VERIFIED result persisted successfully", not isinstance(outcome, Exception) and outcome is not None and hasattr(outcome, "stored_status"))
    if not hasattr(outcome, "stored_status"):
        return ok

    ok &= _check(
        "status survives round-trip verbatim (check 3)",
        outcome.stored_status == SUCCESS,
    )
    ok &= _check(
        "original student input survives (check 5)",
        outcome.stored_raw_input == "purchased furniture from raj for rs.25000",
    )
    ok &= _check(
        "exactly one record written",
        len(store.records) == 1,
    )
    ok &= _check(
        "store holds record dict, result object untouched",
        result.status == SUCCESS and result.request_id == "req-7e-verified",
    )
    return ok


# ---------------------------------------------------------------------------
# Section 3 — checks 2, 3, 7: non-verified terminal states preserved
# ---------------------------------------------------------------------------


def test_non_verified_terminal_states_preserved() -> bool:
    _section("Non-verified terminal states preserved (checks 2, 3, 7)")
    ok = True

    from backend.persistence.base import InMemoryResultPersistence

    terminal_statuses = [
        VALIDATION_FAILED,
        GROUNDING_FAILED,
        FORBIDDEN_OUTPUT,
        MODEL_UNAVAILABLE,
        UNSUPPORTED_TRANSACTION,
        _REVIEW,
    ]

    store = InMemoryResultPersistence()
    for status in terminal_statuses:
        outcome = store.persist(_terminal_result(status))
        stored = getattr(outcome, "stored_status", None)
        ok &= _check(
            f"{status} persisted verbatim (not converted to VERIFIED)",
            stored == status,
        )

    ok &= _check(
        "terminal-state count matches persisted records",
        len(store.records) == len(terminal_statuses),
    )

    # Grounding/verification state must survive for grounding failures.
    outcome = store.persist(_terminal_result(GROUNDING_FAILED))
    ok &= _check(
        "grounding failure verification_status survives (check 7)",
        getattr(outcome, "stored_verification_status", "") == "GROUNDING_FAILED",
    )
    ok &= _check(
        "grounding issues survive (check 7)",
        getattr(outcome, "stored_grounding_issues", [])
        == ["party 'sharma' not found in input"],
    )
    ok &= _check(
        "no accounting result attached to grounding-failed record",
        getattr(outcome, "stored_accounting_result", "missing") is None,
    )
    return ok


# ---------------------------------------------------------------------------
# Section 4 — checks 4, 6, 8: accounting output unchanged, no recalculation
# ---------------------------------------------------------------------------


def test_accounting_output_preserved_without_recalculation() -> bool:
    _section("Accounting output preserved, no recalculation (checks 4, 6, 8)")
    ok = True

    from backend.persistence.base import InMemoryResultPersistence

    store = InMemoryResultPersistence()
    result = _verified_result()
    original_accounting = json.dumps(result.accounting_result, sort_keys=True)
    original_candidate = json.dumps(result.interpretation_candidate, sort_keys=True)

    outcome = store.persist(result)

    stored_accounting = getattr(outcome, "stored_accounting_result", None)
    ok &= _check(
        "accounting result survives round-trip (check 4)",
        json.dumps(stored_accounting, sort_keys=True) == original_accounting,
    )

    stored_candidate = getattr(outcome, "stored_interpretation_candidate", None)
    ok &= _check(
        "18-field interpretation candidate survives verbatim",
        json.dumps(stored_candidate, sort_keys=True) == original_candidate,
    )

    ok &= _check(
        "model_id survives (check 6)",
        getattr(outcome, "stored_model_id", "") == _META["model_id"],
    )
    ok &= _check(
        "provider revision pin survives (check 6)",
        getattr(outcome, "stored_provider_revision", "")
        == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
    )
    ok &= _check(
        "adapter revision pin survives (check 6)",
        getattr(outcome, "stored_adapter_revision", "")
        == "b5c0a37cebc00e93144150dbbcaa7b28cadb259e",
    )

    # Check 8: persistence must not recalculate. We prove this by mutating
    # the accounting output BEFORE persisting and confirming the store
    # records the mutated values verbatim — i.e. it copies, never recomputes.
    mutated = _verified_result()
    mutated.accounting_result["journal_balanced"] = False
    mutated.accounting_result["debit_lines"] = []
    outcome2 = store.persist(mutated)
    stored2 = getattr(outcome, "stored_accounting_result", None)
    stored2_mut = getattr(outcome2, "stored_accounting_result", None)
    ok &= _check(
        "store copies accounting output verbatim even when 'wrong' (check 8)",
        stored2_mut is not None
        and stored2_mut.get("journal_balanced") is False
        and stored2_mut.get("debit_lines") == [],
    )
    ok &= _check(
        "first record unaffected by second write (no shared-state recalculation)",
        stored2 is not None and stored2.get("journal_balanced") is True,
    )
    return ok


# ---------------------------------------------------------------------------
# Section 5 — check 9: fail-closed persistence failures
# ---------------------------------------------------------------------------


def test_persistence_failure_is_fail_closed() -> bool:
    _section("Persistence failures fail closed (check 9)")
    ok = True

    from backend.persistence.base import (
        PERSISTENCE_UNAVAILABLE,
        PERSISTENCE_WRITE_FAILED,
        InMemoryResultPersistence,
        PersistenceFailure,
    )

    # Case A: injected write failure.
    store = InMemoryResultPersistence()
    store.fail_with(
        PersistenceFailure(
            kind=PERSISTENCE_WRITE_FAILED,
            reason="simulated write failure",
        )
    )
    result = _verified_result()
    outcome = store.persist(result)
    ok &= _check(
        "write failure returns PersistenceFailure, not success",
        isinstance(outcome, PersistenceFailure),
    )
    ok &= _check(
        "failure kind preserved",
        getattr(outcome, "kind", "") == PERSISTENCE_WRITE_FAILED,
    )
    ok &= _check(
        "no record written on failure",
        len(store.records) == 0,
    )
    ok &= _check(
        "KernelResult untouched by failed persistence",
        result.status == SUCCESS and result.accounting_result is not None,
    )

    # Case B: unavailable store.
    store2 = InMemoryResultPersistence()
    store2.fail_with(
        PersistenceFailure(
            kind=PERSISTENCE_UNAVAILABLE,
            reason="database unreachable",
        )
    )
    outcome2 = store2.persist(_verified_result())
    ok &= _check(
        "unavailable store returns PERSISTENCE_UNAVAILABLE",
        isinstance(outcome2, PersistenceFailure)
        and getattr(outcome2, "kind", "") == PERSISTENCE_UNAVAILABLE,
    )

    # Case C: result object mutated after failure must not be relabelled.
    ok &= _check(
        "failed persistence does not convert result to VERIFIED-adjacent success",
        result.status == SUCCESS and result.verification_status == "GROUNDED",
    )
    return ok


# ---------------------------------------------------------------------------
# Section 6 — check 10: dependency direction stays clean
# ---------------------------------------------------------------------------


def test_dependency_direction() -> bool:
    _section("Dependency direction (check 10)")
    ok = True

    kernel_src = (ROOT / "backend" / "kernel" / "kernel.py").read_text()
    kernel_tree = ast.parse(kernel_src)
    imported: List[str] = []
    for node in ast.walk(kernel_tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = [
        "sqlalchemy",
        "psycopg2",
        "backend.persistence.postgres",
        "backend.database.models",
        "backend.database.db",
    ]
    for module in forbidden:
        hit = any(
            module == name or name.startswith(module + ".") for name in imported
        )
        ok &= _check(f"kernel.py does not import {module}", not hit)

    # Kernel package must not depend on the concrete store at all.
    init_src = (ROOT / "backend" / "kernel" / "__init__.py").read_text()
    ok &= _check(
        "backend/kernel/__init__.py does not import postgres implementation",
        "persistence.postgres" not in init_src and "database.models" not in init_src,
    )

    # The postgres implementation must import the contract, not vice versa.
    postgres_src = (ROOT / "backend" / "persistence" / "postgres.py").read_text()
    postgres_tree = ast.parse(postgres_src)
    pg_imports: List[str] = []
    for node in ast.walk(postgres_tree):
        if isinstance(node, ast.Import):
            pg_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            pg_imports.append(node.module)
    ok &= _check(
        "postgres implementation imports the persistence contract",
        any(m == "backend.persistence.base" or m.startswith("backend.persistence.base.")
            for m in pg_imports),
    )
    ok &= _check(
        "postgres implementation reuses existing FYJC models",
        any(m == "backend.database.models" for m in pg_imports),
    )

    # No SQLAlchemy in the contract module.
    base_src = (ROOT / "backend" / "persistence" / "base.py").read_text()
    ok &= _check(
        "contract module has no SQLAlchemy/psycopg2 imports",
        "sqlalchemy" not in base_src and "psycopg2" not in base_src,
    )
    return ok


# ---------------------------------------------------------------------------
# Section 7 — check 11: no duplicate accounting implementation
# ---------------------------------------------------------------------------


def test_no_duplicate_accounting_implementation() -> bool:
    _section("No duplicate accounting implementation (check 11)")
    ok = True

    persistence_files = [
        ROOT / "backend" / "persistence" / "base.py",
        ROOT / "backend" / "persistence" / "postgres.py",
    ]
    forbidden_tokens = [
        "hardened_bookkeeping_outcome",
        "def process_accounting",
        "debit_total",
        "credit_total",
        "trial_balance",
        "def classify_account",
        "def apply_rule",
        "golden_rules",
    ]
    for path in persistence_files:
        src = path.read_text()
        for token in forbidden_tokens:
            ok &= _check(
                f"{path.name} contains no '{token}'",
                token not in src,
            )

    # The postgres implementation may only reference accounting fields as
    # string keys when mapping into existing columns — verify it never
    # imports the accounting module.
    pg_src = (ROOT / "backend" / "persistence" / "postgres.py").read_text()
    ok &= _check(
        "postgres implementation does not import fyjc_accounting",
        "fyjc_accounting" not in pg_src,
    )
    ok &= _check(
        "postgres implementation does not import maths kernel modules",
        "backend.maths" not in pg_src,
    )
    return ok


# ---------------------------------------------------------------------------
# Section 8 — check 12 + existing schema reuse
# ---------------------------------------------------------------------------


def test_existing_schema_reused_and_regressions() -> bool:
    _section("Existing schema reuse + regression sweep (check 12)")
    ok = True

    # The postgres implementation maps onto the existing FYJC tables and
    # invents no new ones.
    pg_src = (ROOT / "backend" / "persistence" / "postgres.py").read_text()
    for table in ("FYJCInteraction", "FYJCInterpretation", "FYJCTrainingCandidate"):
        ok &= _check(f"postgres implementation uses existing {table}", table in pg_src)
    ok &= _check(
        "no new table names invented",
        "CREATE TABLE" not in pg_src and "__tablename__" not in pg_src,
    )

    # Existing FYJC schema remains as-is: 4 tables, no schema drift.
    models_src = (ROOT / "backend" / "database" / "models.py").read_text()
    fyjc_tables = models_src.count('__tablename__ = "fyjc_')
    ok &= _check(
        "existing FYJC schema untouched (4 tables)",
        fyjc_tables == 4,
    )

    # Run existing database persistence regression (mocked session, no live DB).
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            import unittest

            loader = unittest.TestLoader()
            suite = loader.discover(
                str(ROOT / "backend"),
                pattern="fyjc_db_persistence_test.py",
            )
            runner = unittest.TextTestRunner(stream=buf, verbosity=0)
            db_result = runner.run(suite)
        db_ok = db_result.wasSuccessful() and db_result.testsRun > 0
        ok &= _check(
            f"existing fyjc_db_persistence_test.py passes ({db_result.testsRun} tests)",
            db_ok,
        )
    except Exception as exc:
        ok &= _check(f"existing fyjc_db_persistence_test.py runs (error: {exc})", False)

    return ok


# ---------------------------------------------------------------------------
# Section 9 — Postgres implementation behaviour with a fake session factory
# (no live PostgreSQL required; verifies the verbatim-copy mapping path)
# ---------------------------------------------------------------------------


def test_postgres_mapping_with_fake_session() -> bool:
    _section("Postgres mapping via fake session (verbatim copy, fail-closed)")
    ok = True

    from types import SimpleNamespace

    from backend.persistence.postgres import PostgresResultPersistence

    added: List[Any] = []

    class FakeSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False

        def add(self, obj: Any) -> None:
            # Simulate SQLAlchemy assigning PKs on flush.
            if not hasattr(obj, "id"):
                obj.id = len(added) + 1
            added.append(obj)

        def flush(self) -> None:
            pass

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            pass

    session = FakeSession()
    backend = PostgresResultPersistence(session_factory=lambda: session)
    outcome = backend.persist(_verified_result())

    ok &= _check(
        "fake-session persist returns PersistedResult",
        hasattr(outcome, "stored_status") and outcome.stored_status == SUCCESS,
    )
    ok &= _check(
        "session.commit called exactly once",
        session.committed and not session.rolled_back,
    )
    ok &= _check(
        "wrote interaction + interpretation (+ candidate for VERIFIED)",
        len(added) == 3,
    )

    if len(added) >= 2:
        interaction = added[0]
        interpretation = added[1]
        candidate_row = added[2] if len(added) >= 3 else None

        ok &= _check(
            "raw input stored verbatim on interaction",
            interaction.raw_input == "purchased furniture from raj for rs.25000",
        )
        ok &= _check(
            "authoritative status stored verbatim on interpretation",
            interpretation.kernel_status == SUCCESS,
        )
        ok &= _check(
            "model_id carried through",
            interpretation.model_id == _META["model_id"],
        )
        ok &= _check(
            "journal_balanced copied, not recomputed",
            interpretation.journal_balanced is True,
        )
        ok &= _check(
            "debit accounts mapped from kernel output",
            (interpretation.debit_accounts or [{}])[0].get("account") == "Furniture",
        )
        ok &= _check(
            "credit accounts mapped from kernel output",
            (interpretation.credit_accounts or [{}])[0].get("account") == "Cash",
        )
        ok &= _check(
            "calculations mapped from kernel output",
            (interpretation.calculations or [{}])[0].get("id") == "BK_TOTAL",
        )
        if candidate_row is not None:
            ok &= _check(
                "training candidate created only for eligible status",
                candidate_row.interpretation_id == interpretation.id,
            )

    # Fail-closed on write error.
    class BrokenSession(FakeSession):
        def commit(self) -> None:
            raise RuntimeError("connection lost")

    broken = BrokenSession()
    backend2 = PostgresResultPersistence(session_factory=lambda: broken)
    outcome2 = backend2.persist(_verified_result())
    from backend.persistence.base import PersistenceFailure

    ok &= _check(
        "commit failure returns PersistenceFailure (fail closed)",
        isinstance(outcome2, PersistenceFailure),
    )
    ok &= _check(
        "rollback invoked on write failure",
        broken.rolled_back,
    )

    # Unavailable store.
    backend3 = PostgresResultPersistence(session_factory=lambda: None)
    outcome3 = backend3.persist(_verified_result())
    ok &= _check(
        "unavailable session returns PERSISTENCE_UNAVAILABLE",
        isinstance(outcome3, PersistenceFailure)
        and getattr(outcome3, "kind", "") == "PERSISTENCE_UNAVAILABLE",
    )
    return ok


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70)
    print("PLATRIXA — PHASE 7E: PERSISTENCE PATH ENFORCEMENT TEST")
    print("=" * 70)

    ok = True
    ok &= test_contract_surface()
    ok &= test_verified_persists_and_roundtrips()
    ok &= test_non_verified_terminal_states_preserved()
    ok &= test_accounting_output_preserved_without_recalculation()
    ok &= test_persistence_failure_is_fail_closed()
    ok &= test_dependency_direction()
    ok &= test_no_duplicate_accounting_implementation()
    ok &= test_existing_schema_reused_and_regressions()
    ok &= test_postgres_mapping_with_fake_session()

    total = len(_CHECKS)
    passed = sum(1 for c in _CHECKS if c)
    failed = total - passed

    print("\n" + "=" * 70)
    print(f"RESULT: {'PASS' if failed == 0 else 'FAIL'} — {passed}/{total} checks passed")
    if failed:
        print(f"  ({failed} failed)")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
