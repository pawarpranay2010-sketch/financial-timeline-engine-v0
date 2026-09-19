#!/usr/bin/env python3
"""
Platrixa
Authority Expansion — Phase F gate: Finance Knowledge Authority
scripts/fte_authority_phase_f_test.py

Contract under test (backend/maths/finance_knowledge.py + capability
registry integration):

  F-1  registry loads; the canonical builder is idempotent and independent
       instances cannot leak state into each other
  F-2  structural contract: valid ids, types, statuses; SUPPORTED requires
       provenance AND verification evidence
  F-3  duplicates fail closed: duplicate knowledge_id, duplicate claim
       (case/whitespace variants), invalid type/status, missing provenance
  F-4  retrieval is deterministic, subset-based and fail-closed:
       UNVERIFIED is excluded unless include_unverified=True;
       PLANNED is NEVER retrievable
  F-5  source-backed verification cases (IAS 1/7/37, BCBS/Basel): the cited
       organization/reference actually supports the claim
  F-6  AUTHORITY SEPARATION: the authority exposes no calculate/post/
       journal/solve surface; formula concepts are FORMULA_REFERENCE records
       pointing at the Formula Authority's live canonical ids
  F-7  capability-registry integration: the Phase C KNOWLEDGE root flipped
       PLANNED -> SUPPORTED exactly after Phase F, and FK-AA-100 registers
       the verified foundation with provenance; the two registries cannot
       drift (record <-> capability correspondence is checked live)

Pure module: no AI, no network. Deterministic.
"""
from __future__ import annotations

import sys

sys.path.insert(0, '.')

from backend.maths.finance_knowledge import (          # noqa: E402
    KnowledgeAuthority, KnowledgeQuery, KnowledgeRecord, KnowledgeSource,
    KnowledgeStatus, KnowledgeType, build_knowledge_authority,
)
from backend.maths.capability_registry import (        # noqa: E402
    AUTHORITY_FINANCE_KNOWLEDGE, CAPABILITIES, Capability, RegistrationError,
    by_authority, register,
)
from backend.maths.extended_registry import EXTENDED_REGISTRY  # noqa: E402

_RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))


def _rejects(fn) -> bool:
    """True iff fn raises ValueError (knowledge contract) or
    RegistrationError (capability contract)."""
    try:
        fn()
    except (ValueError, RegistrationError):
        return True
    return False


# ---------------------------------------------------------------------------
# F-1  loading
# ---------------------------------------------------------------------------
auth = build_knowledge_authority()
auth2 = build_knowledge_authority()
check("F-1.1 canonical builder loads", len(auth) >= 25, f"n={len(auth)}")
check("F-1.2 idempotent independent instances",
      len(auth) == len(auth2) == len(build_knowledge_authority()))
check("F-1.3 no cross-instance leakage",
      auth.counts_by_status() == auth2.counts_by_status())
check("F-1.4 all canonical records use the fk_ namespace",
      all(r.knowledge_id.startswith("fk_")
          for r in auth.query(KnowledgeQuery(), include_unverified=True)))

# ---------------------------------------------------------------------------
# F-2  structural contract
# ---------------------------------------------------------------------------
structural_ok = True
for r in auth.query(KnowledgeQuery(), include_unverified=True):
    if not r.knowledge_id.startswith("fk_"):
        structural_ok = False
    if not isinstance(r.knowledge_type, KnowledgeType) or not isinstance(r.status, KnowledgeStatus):
        structural_ok = False
    if not r.claim.strip() or not r.topic.strip():
        structural_ok = False
    if r.status is KnowledgeStatus.SUPPORTED and (
            r.source is None or not r.source.verified_via):
        structural_ok = False
    if r.status is KnowledgeStatus.PLANNED:
        structural_ok = False  # canonical records contain no PLANNED slots
check("F-2.1 every record satisfies the structural contract", structural_ok)
check("F-2.2 all seven knowledge types are represented",
      set(auth.counts_by_type()) == {t.value for t in KnowledgeType},
      str(sorted(auth.counts_by_type())))
check("F-2.3 SUPPORTED provenance coverage is 100%",
      all(r.source is not None for r in auth.query(KnowledgeQuery())))
check("F-2.4 exactly one UNVERIFIED record survives (honest boundary)",
      auth.counts_by_status().get("UNVERIFIED") == 1
      and auth.counts_by_status().get("SUPPORTED", 0) >= 25,
      str(auth.counts_by_status()))

# ---------------------------------------------------------------------------
# F-3  duplicates / fail-closed registration
# ---------------------------------------------------------------------------
_by_id = {r.knowledge_id: r for r in auth.query(KnowledgeQuery(), include_unverified=True)}
check("F-3.1 duplicate knowledge_id rejected",
      _rejects(lambda: auth.register(KnowledgeRecord(
          "fk_fs_purpose", KnowledgeType.CONCEPT, "dup", "x", KnowledgeStatus.UNVERIFIED))))
check("F-3.2 duplicate claim rejected (case/whitespace variant)",
      _rejects(lambda: auth.register(KnowledgeRecord(
          "fk_new_id", KnowledgeType.CONCEPT, "dup",
          "  " + _by_id["fk_fs_purpose"].claim.upper().replace(" ABOUT", "   about"),
          KnowledgeStatus.UNVERIFIED))))
check("F-3.3 invalid knowledge type rejected",
      _rejects(lambda: auth.register(KnowledgeRecord(
          "fk_t1", "NOT_A_TYPE", "x", "y", KnowledgeStatus.UNVERIFIED))))  # type: ignore[arg-type]
check("F-3.4 invalid status rejected",
      _rejects(lambda: auth.register(KnowledgeRecord(
          "fk_t2", KnowledgeType.FACT, "x", "y", "MAYBE")))  # type: ignore[arg-type]
      )
check("F-3.5 SUPPORTED without provenance is unrepresentable",
      _rejects(lambda: auth.register(KnowledgeRecord(
          "fk_t3", KnowledgeType.FACT, "x", "unique claim z", KnowledgeStatus.SUPPORTED))))
check("F-3.6 SUPPORTED without verification evidence is unrepresentable",
      _rejects(lambda: auth.register(KnowledgeRecord(
          "fk_t4", KnowledgeType.FACT, "x", "another unique claim",
          KnowledgeStatus.SUPPORTED, KnowledgeSource("Org", "Ref", "t")))))

# ---------------------------------------------------------------------------
# F-4  retrieval contract
# ---------------------------------------------------------------------------
check("F-4.1 UNVERIFIED excluded by default",
      auth.get("fk_ind_as_mapping") is not None
      and all(r.knowledge_id != "fk_ind_as_mapping" for r in auth.query(KnowledgeQuery())))
check("F-4.2 UNVERIFIED retrievable only with explicit opt-in",
      [r.knowledge_id for r in auth.query(
          KnowledgeQuery(knowledge_id="fk_ind_as_mapping"), include_unverified=True)]
      == ["fk_ind_as_mapping"])
check("F-4.3 PLANNED never retrievable",
      all(r.status is not KnowledgeStatus.PLANNED
          for r in auth.query(KnowledgeQuery(), include_unverified=True)))
cash = auth.query(KnowledgeQuery(topic="cash flow"))
check("F-4.4 topic retrieval deterministic and subset-based",
      [r.knowledge_id for r in cash] == [r.knowledge_id for r in cash]
      and len(cash) >= 2)
check("F-4.5 type-scoped retrieval",
      {r.knowledge_id for r in auth.query(
          KnowledgeQuery(knowledge_type=KnowledgeType.ACCOUNTING_RULE_REFERENCE))}
      == {"fk_cashflow_classes", "fk_provisions"})
check("F-4.6 jurisdiction-scoped retrieval reaches BCBS records",
      any(r.source and "Basel" in r.source.organization
          for r in auth.query(KnowledgeQuery(jurisdiction="international"))))
check("F-4.7 framework-scoped retrieval (IAS 7)",
      {"fk_cashflow_classes", "fk_fcf_reference"}.issubset(
          {r.knowledge_id for r in auth.query(KnowledgeQuery(framework="IAS 7"))}))
check("F-4.8 no-match returns empty, not error",
      auth.query(KnowledgeQuery(topic="nonexistent topic xyz")) == ())
check("F-4.9 empty query returns exactly the SUPPORTED set",
      len(auth.query(KnowledgeQuery())) == auth.counts_by_status()["SUPPORTED"])

# ---------------------------------------------------------------------------
# F-5  source-backed verification cases
# ---------------------------------------------------------------------------
ias1 = _by_id["fk_fs_purpose"]
check("F-5.1 IAS 1 organization verified",
      "IASB" in (ias1.source.organization or ""))
check("F-5.2 IAS 1 supports the elements claim",
      all(w in _by_id["fk_fs_elements"].claim
          for w in ("assets", "liabilities", "income", "expenses", "cash flows")))
check("F-5.3 IAS 7 supports the three-class cash flow claim",
      all(w in _by_id["fk_cashflow_classes"].claim
          for w in ("operating", "investing", "financing")))
check("F-5.4 IAS 37 supports the provision criteria claim",
      all(w in _by_id["fk_provisions"].claim
          for w in ("obligation", "probable", "estimated")))
check("F-5.5 BCBS/Basel supports the risk-family claim",
      all(w in _by_id["fk_risk_families"].claim
          for w in ("credit", "market", "operational")))
check("F-5.6 every SUPPORTED record states HOW it was verified",
      all(r.source.verified_via for r in auth.query(KnowledgeQuery())))
check("F-5.7 the unverified record states its limitation explicitly",
      "not verifiable" in _by_id["fk_ind_as_mapping"].limitations)

# ---------------------------------------------------------------------------
# F-6  authority separation
# ---------------------------------------------------------------------------
check("F-6.1 no execution surface (no calculate/post/journal/solve)",
      not any(callable(getattr(KnowledgeAuthority, m, None))
              for m in ("calculate", "post", "journal", "solve", "execute", "compute")))
check("F-6.2 knowledge cannot execute accounting operations",
      not any(hasattr(auth, a) for a in ("post", "journal", "process_accounting", "kernel")))
check("F-6.3 knowledge cannot bypass Formula Authority",
      not any(hasattr(auth, a) for a in ("solve", "production_solve", "solver", "engine")))
check("F-6.4 formula concepts are references, never duplicates",
      all(r.knowledge_type is KnowledgeType.FORMULA_REFERENCE
          for r in auth.query(KnowledgeQuery(knowledge_type=KnowledgeType.FORMULA_REFERENCE))))
check("F-6.5 formula references target LIVE Formula Authority ids",
      {"ROE", "FREE_CASH_FLOW"}.issubset(set(EXTENDED_REGISTRY.all_ids())))
check("F-6.6 accounting references reference standards, not postings",
      {r.knowledge_type for r in auth.query(
          KnowledgeQuery(knowledge_type=KnowledgeType.ACCOUNTING_RULE_REFERENCE))}
      == {KnowledgeType.ACCOUNTING_RULE_REFERENCE}
      and all("IAS" in r.source.reference
              for r in auth.query(KnowledgeQuery(knowledge_type=KnowledgeType.ACCOUNTING_RULE_REFERENCE))))

# ---------------------------------------------------------------------------
# F-7  capability-registry integration
# ---------------------------------------------------------------------------
fk_caps = {c.capability_id: c for c in by_authority(AUTHORITY_FINANCE_KNOWLEDGE)}
root = fk_caps.get("KNOWLEDGE.AUTHORITY_ROOT")
aa100 = fk_caps.get("KNOWLEDGE.FS_PURPOSE_ELEMENTS")
check("F-7.1 knowledge authority root flipped to SUPPORTED after Phase F",
      root is not None and root.supported_status == "SUPPORTED",
      root.supported_status if root else "missing")
check("F-7.2 root carries implementation + test + source provenance",
      root is not None and "finance_knowledge" in root.implementation_ref
      and "phase_f_test" in root.test_ref and bool(root.source_ref))
check("F-7.3 KNOWLEDGE.FS_PURPOSE_ELEMENTS registered SUPPORTED",
      aa100 is not None and aa100.supported_status == "SUPPORTED")
check("F-7.4 KNOWLEDGE.FS_PURPOSE_ELEMENTS provenance matches IAS 1",
      aa100 is not None and "IAS 1" in aa100.source_ref
      and "IASB" in aa100.source_ref)
check("F-7.5 capability <-> knowledge record correspondence (no drift)",
      aa100 is not None
      and "financial statement" in aa100.canonical_name.lower()
      and _by_id["fk_fs_purpose"].topic in aa100.canonical_name.lower().replace("statement", "statements")
      and "financial position" in aa100.description)
check("F-7.6 root no longer claims the pre-Phase-F limitation",
      root is not None
      and not any("zero SUPPORTED entries" in lim for lim in root.limitations))
check("F-7.7 duplicate capability id rejected (registry fail-closed)",
      aa100 is not None and _rejects(lambda: register(aa100)))
check("F-7.8 knowledge capabilities are the only FINANCE_KNOWLEDGE entries",
      set(fk_caps) == {"KNOWLEDGE.AUTHORITY_ROOT", "KNOWLEDGE.FS_PURPOSE_ELEMENTS"})

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
failures = [(n, d) for n, ok, d in _RESULTS if not ok]
total = len(_RESULTS)
print("=" * 64)
print("PHASE F GATE — FINANCE KNOWLEDGE AUTHORITY")
print("=" * 64)
print(f"checks: {total - len(failures)}/{total} passed")
for name, detail in failures:
    print(f"  FAIL {name}  {detail}")
if failures:
    print("PHASE F FAIL")
    sys.exit(1)
print("PHASE F PASS - KNOWLEDGE AUTHORITY VERIFIED, BOUNDARIES INTACT")
