#!/usr/bin/env python3
"""fte_fyjc_73_dual_payment_sweep.py — Phase 22 addendum: full-corpus dual-payment sweep.

Context: fte_fyjc_72 check G flagged 11 canonical train rows whose inputs state
BOTH cash and cheque while the legacy target claims a single payment method
(cash) + VERIFIED — the exact Pattern D label-integrity defect documented in
PLATRIXA_PHASE22_22ROW_LABEL_AUDIT.md. Those 11 rows passed the grounding gate
(their claimed values are individually grounded), so the Phase 22 audit — which
inspected only the 22 gate-FAILING rows — never saw them.

This sweep (read-only, no file modified) answers, for rule 8 ("stop and report
rather than invent"):

  1. How many rows in phase22_v02_train.jsonl carry suggested_status VERIFIED
     while the input mentions both cash and cheque — grouped by transaction
     type and by phase22 provenance (canonical vs corrective vs verbatim_18field).
  2. Fresh deterministic specialist evidence (unmodified production
     FYJCAISpecialist) for each flagged canonical row: which payment mode the
     parser itself resolves, and whether it records ambiguity.

Determinism: PYTHONHASHSEED=0 re-exec (Phase 21 finding R1).
"""
import json
import os
import sys

if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter

from backend.maths.fyjc_ai_specialist import FYJCAISpecialist

V02 = "training_data/phase22_v02_train.jsonl"


def main() -> None:
    rows = [json.loads(l) for l in open(V02) if l.strip()]
    suspects = [
        r for r in rows
        if r["output"].get("suggested_status") == "VERIFIED"
        and "cash" in r["input"].lower()
        and "cheque" in r["input"].lower()
        and " both " not in r["input"].lower()
    ]
    print(f"VERIFIED rows whose input states both cash and cheque: {len(suspects)}")
    print("by transaction_type_enum:", dict(Counter(r["output"].get("transaction_type_enum") for r in suspects)))
    print("by provenance:", dict(Counter(r["metadata"].get("phase22") for r in suspects)))

    sp = FYJCAISpecialist()
    print("\n--- fresh specialist evidence (canonical rows only) ---")
    for r in suspects:
        if r["metadata"].get("phase22") != "canonical":
            continue
        f = sp.parse(r["input"])
        print(
            f"{r['id']}: legacy pm={r['output'].get('payment_method')!r} "
            f"| fresh pm={f['payment_method']!r} tx={f['transaction_type']!r} "
            f"parties={f['parties']} ambiguities={f['ambiguities']}"
        )


if __name__ == "__main__":
    main()
