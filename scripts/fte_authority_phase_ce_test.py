#!/usr/bin/env python3
"""
Platrixa
Authority Expansion - Phase C + Phase E deterministic gate
scripts/fte_authority_phase_ce_test.py

  PHASE C - Capability Registry contract:
    C-1  registry loads; every entry satisfies the structural contract
         (authority vocabulary, status vocabulary, id-prefix namespace)
    C-2  no duplicate capability ids; no id migrates across authorities
    C-3  provenance presence: SUPPORTED/PARTIAL entries carry
         implementation_ref + test_ref; UNSUPPORTED carries refusal
         evidence; PLANNED entries carry limitations
    C-4  FORMULA_AUTHORITY coverage is DERIVED from the live extended
         registry (no drift, no copies) and every entry is SUPPORTED
         (no formula is registered that the authority cannot execute)
    C-5  FINANCE_KNOWLEDGE entries are registry-only: since Phase F the
         authority is SUPPORTED but it remains a deterministic REGISTRY
         with no calculate/post/journal surface (fail-closed boundary;
         model claims can never route to a knowledge entry to compute)
    C-6  registration contract rejects: duplicate ids, unknown
         authorities, invalid statuses, wrong id namespaces,
         SUPPORTED-without-provenance, UNSUPPORTED-without-evidence

  PHASE E - Formula Authority (strict C++ path, no Python arithmetic):
    E-1  every new formula solves through production_solve with
         authority_state == cpp and DERIVED status (never VERIFIED)
    E-2  test vectors: normal, decimal, large, small values
    E-3  zero denominators fail closed with a specific reason
    E-4  missing inputs fail closed (never silently substituted)
    E-5  unprovenanced facts fail closed (evidence discipline)
    E-6  mathematically-valid negative results compute (FCF)
    E-7  reverse solving works for the single-op formulas with
         registered inverses (growth formulas are forward-only)
    E-8  Python<->C++ coverage parity (production gate A3 contract)
"""

import subprocess
import sys

sys.path.insert(0, ".")

from backend.maths.authority import production_solve  # noqa: E402
from backend.maths.capability_registry import (  # noqa: E402
    AUTHORITY_ACCOUNTING_KERNEL,
    AUTHORITY_FINANCE_KNOWLEDGE,
    AUTHORITY_FORMULA,
    CAPABILITIES,
    Capability,
    RegistrationError,
    by_authority,
    summary,
)
from backend.maths.extended_registry import EXTENDED_REGISTRY  # noqa: E402
from backend.formula_engine_cpp import (  # noqa: E402
    CPP_COVERED_KEYS,
    binary_path,
)

CHECKS = 0
FAILURES = []


def check(name, ok, detail=""):
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILURES.append(f"{name}{(' :: ' + str(detail)) if detail else ''}")


def F(value, **extra):
    """One verified pipeline fact (Tier 1 document)."""
    fact = {"value": value, "provenance_tier": "DOCUMENT",
            "reporting_period": "FY2025", "document_name": "AR2025.pdf",
            "page": "42", "evidence": "statement line",
            "source": "AR2025.pdf"}
    fact.update(extra)
    return fact


# ---------------------------------------------------------------------------
# PHASE C - capability registry contract
# ---------------------------------------------------------------------------

print("PHASE C - CAPABILITY REGISTRY")
check("C-1 registry loads", len(CAPABILITIES) > 0, str(len(CAPABILITIES)))
for cid, c in CAPABILITIES.items():
    check(f"C-1 structure[{cid}]",
          c.authority in (AUTHORITY_ACCOUNTING_KERNEL, AUTHORITY_FORMULA,
                          AUTHORITY_FINANCE_KNOWLEDGE)
          and c.supported_status in ("SUPPORTED", "PARTIAL", "UNSUPPORTED",
                                     "PLANNED")
          and isinstance(c.canonical_name, str) and c.canonical_name)

prefixes = {"ACCOUNTING_KERNEL": "KERNEL.", "FORMULA_AUTHORITY": "FORMULA.",
            "FINANCE_KNOWLEDGE": "KNOWLEDGE."}
for cid, c in CAPABILITIES.items():
    check(f"C-2 namespace[{cid}]", cid.startswith(prefixes[c.authority]))

formulas = by_authority(AUTHORITY_FORMULA)
kernel = by_authority(AUTHORITY_ACCOUNTING_KERNEL)
knowledge = by_authority(AUTHORITY_FINANCE_KNOWLEDGE)

for c in formulas + [x for x in kernel if x.supported_status == "SUPPORTED"]:
    check(f"C-3 provenance[{c.capability_id}]",
          bool(c.implementation_ref) and bool(c.test_ref))
for c in [x for x in kernel if x.supported_status == "UNSUPPORTED"]:
    check(f"C-3 refusal evidence[{c.capability_id}]",
          bool(c.limitations))
for c in knowledge:
    check(f"C-3 planned limitations[{c.capability_id}]",
          bool(c.limitations))

check("C-4 formula coverage derived from live registry",
      {c.capability_id[len("FORMULA."):] for c in formulas}
      == set(EXTENDED_REGISTRY.all_ids()),
      f"registry={len(EXTENDED_REGISTRY.all_ids())} "
      f"capabilities={len(formulas)}")
check("C-4 every formula capability SUPPORTED",
      all(c.supported_status == "SUPPORTED" for c in formulas))
# C-5: Phase F flipped the knowledge authority to SUPPORTED - but the
# boundary that matters is not the status, it is that the authority is a
# registry, not an executor. The Phase F gate (F-6.1-F-6.3) proves the
# absence of any calculate/post/journal surface directly on the class.
from backend.maths.finance_knowledge import KnowledgeAuthority as _KA
check("C-5 knowledge authority is registry-only (no execution surface)",
      all(c.supported_status == "SUPPORTED" for c in knowledge)
      and not any(callable(getattr(_KA, m, None))
                  for m in ("calculate", "post", "journal", "solve", "execute", "compute")),
      str([c.supported_status for c in knowledge]))
check("C-5 knowledge root declares the boundary",
      any("never become authority" in (c.description or "")
          for c in knowledge))

# C-6: the registration contract rejects malformed registrations.
def rejects(label, cap, fragment, keep_existing=False):
    try:
        saved = (cr.CAPABILITIES.get(cap.capability_id)
                 if keep_existing else None)
        cr.register(cap)
        if not keep_existing:
            cr.CAPABILITIES.pop(cap.capability_id, None)
        check(f"C-6 reject[{label}]", False, "accepted a malformed entry")
    except RegistrationError as e:
        check(f"C-6 reject[{label}]", fragment in str(e), str(e))
    finally:
        if keep_existing and saved is not None:
            cr.CAPABILITIES[cap.capability_id] = saved


import backend.maths.capability_registry as cr  # noqa: E402

_ok = Capability("FORMULA.X", "FORMULA_AUTHORITY", "X", "SUPPORTED",
                 implementation_ref="m", test_ref="t")
# duplicate id: attempt to RE-REGISTER an existing capability
rejects("duplicate id",
        Capability("FORMULA.ROI", "FORMULA_AUTHORITY", "X", "PLANNED"),
        "already registered", keep_existing=True)
rejects("unknown authority",
        Capability("FORMULA.Y", "NOPE", "Y", "PLANNED"), "unknown authority")
rejects("invalid status",
        Capability("FORMULA.Y", "FORMULA_AUTHORITY", "Y", "MAYBE"),
        "invalid status")
rejects("wrong namespace",
        Capability("BAD.Z", "FORMULA_AUTHORITY", "Z", "PLANNED"),
        "must start with")
rejects("supported without provenance",
        Capability("FORMULA.Z", "FORMULA_AUTHORITY", "Z", "SUPPORTED"),
        "implementation_ref")
rejects("unsupported without evidence",
        Capability("KERNEL.ZZ", "ACCOUNTING_KERNEL", "Z", "UNSUPPORTED"),
        "UNSUPPORTED requires")
check("C-6 registry unchanged after rejection probes",
      # 43 pre-Phase-F entries + KNOWLEDGE.AUTHORITY_ROOT flip (was counted)
      # + KNOWLEDGE.FS_PURPOSE_ELEMENTS added in Phase F = 44, plus the
      # 9 Sprint INV-ROLE (Phase 3) KERNEL.INVOICE_* entries = 53.
      len(CAPABILITIES) == 53, str(len(CAPABILITIES)))

# ---------------------------------------------------------------------------
# PHASE E - formula authority test vectors (strict C++ authority)
# ---------------------------------------------------------------------------

print("PHASE E - FORMULA AUTHORITY")

# E-2 test vectors: (target, facts, expected display)
VECTORS = [
    ("ROI", {"Net Profit": F(2000), "Investment Cost": F(8000)}, "25.00%"),
    ("ROI", {"Net Profit": F(1250.5), "Investment Cost": F(5000)}, "25.01%"),
    ("ROI", {"Net Profit": F(2.0), "Investment Cost": F(8.0)}, "25.00%"),
    ("ROI", {"Net Profit": F(2000000000), "Investment Cost": F(8000000000)},
     "25.00%"),
    ("ROI", {"Net Profit": F(0.0002), "Investment Cost": F(0.0008)},
     "25.00%"),
    ("Free Cash Flow",
     {"Operating Cash Flow": F(5000), "Capital Expenditure": F(1200)},
     "3800.00"),
    ("Free Cash Flow",
     {"Operating Cash Flow": F(1000.25), "Capital Expenditure": F(300.75)},
     "699.50"),
    ("DSCR", {"Operating Cash Flow": F(6000), "Debt Service": F(4000)},
     "1.50"),
    ("DSCR", {"Operating Cash Flow": F(0.6), "Debt Service": F(0.4)},
     "1.50"),
    ("Revenue Growth",
     {"Revenue": F(281700000000),
      "Previous Revenue": F(245100000000, reporting_period="FY2024")},
     "14.93%"),
    ("Revenue Growth",
     {"Revenue": F(110.0),
      "Previous Revenue": F(100.0, reporting_period="FY2024")},
     "10.00%"),
    ("Profit Growth",
     {"Net Profit": F(1105),
      "Previous Net Profit": F(998, reporting_period="FY2024")},
     "10.72%"),
]

for target, facts, want in VECTORS:
    r = production_solve(target, facts)
    check(f"E-1 cpp[{target}]", r.get("authority_state") == "cpp",
          str(r.get("authority_state")))
    check(f"E-1 derived-not-verified[{target}]", r.get("status") == "DERIVED",
          str(r.get("status")))
    check(f"E-2 vector[{target} {want}]",
          r.get("display_value") == want,
          f"got {r.get('display_value')} want {want}")

# E-3 zero denominators fail closed
for target, facts, fragment in [
    ("DSCR", {"Operating Cash Flow": F(6000), "Debt Service": F(0)},
     "zero"),
    ("ROI", {"Net Profit": F(500), "Investment Cost": F(0)}, "zero"),
    ("Revenue Growth",
     {"Revenue": F(100),
      "Previous Revenue": F(0, reporting_period="FY2024")},
     "zero"),
    ("Profit Growth",
     {"Net Profit": F(100),
      "Previous Net Profit": F(0, reporting_period="FY2024")},
     "zero"),
]:
    r = production_solve(target, facts)
    check(f"E-3 zero-denominator[{target}]",
          r.get("status") == "BLOCKED" and r.get("value") is None
          and fragment in str(r.get("reason", "")).lower(),
          f"status={r.get('status')} reason={r.get('reason')}")

# E-4 missing inputs fail closed (never substituted)
r = production_solve("ROI", {"Net Profit": F(500)})
check("E-4 missing input blocked", r.get("status") == "BLOCKED"
      and "Investment Cost" in str(r.get("reason", "")),
      str(r.get("reason")))

# E-5 unprovenanced facts fail closed
r = production_solve("ROI", {"Net Profit": 500, "Investment Cost": 8000})
check("E-5 unprovenanced blocked", r.get("status") == "BLOCKED",
      f"status={r.get('status')} value={r.get('value')}")

# E-6 mathematically valid negative result computes
r = production_solve("Free Cash Flow",
                     {"Operating Cash Flow": F(1000),
                      "Capital Expenditure": F(3000)})
check("E-6 negative FCF computes", r.get("status") == "DERIVED"
      and r.get("value") == -2000.0,
      f"status={r.get('status')} value={r.get('value')}")

# E-7 reverse solving on the registered-inverse formulas (C++ authority)
from backend.maths.solver import Solver  # noqa: E402
from backend.maths.fact_model import build_fact_graph  # noqa: E402

REV = [
    ("Free Cash Flow", "Operating Cash Flow",
     {"Free Cash Flow": F(3800), "Capital Expenditure": F(1200)}, 5000.0),
    ("Free Cash Flow", "Capital Expenditure",
     {"Free Cash Flow": F(3800), "Operating Cash Flow": F(5000)}, 1200.0),
    ("DSCR", "Operating Cash Flow",
     {"DSCR": F(1.5), "Debt Service": F(4000)}, 6000.0),
    ("DSCR", "Debt Service",
     {"DSCR": F(1.5), "Operating Cash Flow": F(6000)}, 4000.0),
    ("ROI", "Net Profit", {"ROI": F(25), "Investment Cost": F(8000)},
     2000.0),
]
for formula, solve_for, facts, want in REV:
    sol = Solver(EXTENDED_REGISTRY, prefer_cpp=True,
                 cpp_authority=True).solve(solve_for,
                                           build_fact_graph(facts))
    check(f"E-7 inverse[{formula}.{solve_for}]",
          sol.status == "DERIVED" and sol.value is not None
          and abs(float(sol.value) - want) < 1e-9,
          f"status={sol.status} value={sol.value}")

# Growth formulas are FORWARD-ONLY: reverse solving must fail closed.
fwd = {"Profit Growth": F(10.72), "Net Profit": F(1105)}
sol = Solver(EXTENDED_REGISTRY, prefer_cpp=True,
             cpp_authority=True).solve("Previous Net Profit",
                                       build_fact_graph(fwd))
check("E-7 growth reverse fails closed",
      sol.status == "BLOCKED" and sol.value is None,
      f"status={sol.status} value={sol.value}")

# E-8 Python<->C++ coverage parity (production gate A3 contract)
bin_path = binary_path()
check("E-8 binary resolves", bin_path is not None)
if bin_path:
    out = subprocess.run([bin_path, "--registry"], capture_output=True,
                         text=True, timeout=30)
    reg_keys = {e["metric_key"] for e in __import__("json").loads(out.stdout)}
    out = subprocess.run([bin_path, "--registry-ext"], capture_output=True,
                         text=True, timeout=30)
    ext_keys = {e["metric_key"] for e in __import__("json").loads(out.stdout)}
    check("E-8 parity", (reg_keys | ext_keys) == set(CPP_COVERED_KEYS),
          f"diff={sorted((reg_keys | ext_keys) ^ set(CPP_COVERED_KEYS))}")

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

s = summary()
print(f"capability matrix: {s}")
print(f"PHASE C+E GATE: {CHECKS - len(FAILURES)}/{CHECKS} checks passed")
if FAILURES:
    for f in FAILURES:
        print(f"  FAIL - {f}")
    print("PHASE C+E FAIL")
    sys.exit(1)
print("PHASE C+E PASS - REGISTRY + FORMULA AUTHORITY VERIFIED")
