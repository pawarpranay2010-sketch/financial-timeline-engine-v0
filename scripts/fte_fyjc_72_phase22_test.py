#!/usr/bin/env python3
"""fte_fyjc_72_phase22_test.py — Phase 22 verification suite.

Proves (without touching production code or frozen artifacts):

  A. Frozen hardcore dataset byte-identical (sha256 56be5be1…, 1000 rows).
  B. Original specialist corpus unchanged (audit was read-only).
  C. Production runtime untouched: `git status --porcelain` reports no
     modifications to any TRACKED file (only untracked Phase 22 additions).
  D. Classification reproduces exactly: CLEAN 251 / FORMAT_ONLY 727 /
     GENUINE 22 (reruns training/phase22_label_audit.py deterministically).
  E. phase22_v02_train.jsonl: 1793 rows, every row valid JSON with the exact
     18-field output contract, unique ids, unique inputs.
  F. Leakage: zero overlap with canonical validation/test splits; zero exact
     or near-dup (Jaccard >= 0.75) rows vs the Phase 17 locked benchmark;
     hc_00092 / hc_00928 absent.
  G. Corrective re-labels: exactly 21 rows (4 audited + 17 from the
     fte_fyjc_73 full-corpus sweep: 11 purchase + 6 payment) carry
     CONFLICTING_INFORMATION with pm=UNKNOWN + REVIEW_REQUIRED; no
     dual-payment VERIFIED purchase or payment target remains in training.
  H. Kept canonical rows are byte-identical to their v0.1 train source.
  I. Manifest hashes match the actual files on disk.
  J. Determinism: rebuilding the corpus reproduces the same sha256.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

if os.environ.get("PYTHONHASHSEED") != "0":
    os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.maths.schema_verifier import validate_structured_interpretation

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def key(t):
    return re.sub(r"\W+", " ", t.lower()).strip()


def tok(t):
    return frozenset(key(t).split())


def jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def main():
    print("=== A. frozen artifacts unchanged ===")
    hc_sha = sha256_file("training_data/fyjc_hardcore_1000.jsonl")
    check("hardcore sha256 == 56be5be1…", hc_sha == "56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd", hc_sha)
    check("hardcore rows == 1000", len(load("training_data/fyjc_hardcore_1000.jsonl")) == 1000)
    orig_sha = sha256_file("training_data/fyjc_specialist_1000.jsonl")
    check("original corpus sha256 unchanged vs git HEAD",
          subprocess.run(["git", "diff", "HEAD", "--stat", "--", "training_data/fyjc_specialist_1000.jsonl"],
                         capture_output=True, text=True).stdout.strip() == "")
    m = json.load(open("training_data/fyjc_hardcore_1000.manifest.json"))
    check("hardcore manifest sha matches file", m.get("candidate_dataset_sha256") == hc_sha)

    print("=== B/C. production runtime untouched ===")
    st = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    tracked_modified = [l for l in st.splitlines() if l and not l.startswith("??")]
    check("no tracked file modified (runtime + frozen data intact)", tracked_modified == [], tracked_modified[:5])

    print("=== D. classification reproduction (251/727/22) ===")
    out = subprocess.run([sys.executable, "training/phase22_label_audit.py"],
                         capture_output=True, text=True)
    check("audit script exits 0", out.returncode == 0, out.stderr[-300:])
    check("CLEAN=251", "'CLEAN': 251" in out.stdout, out.stdout[:200])
    check("FORMAT_ONLY=727", "'FORMAT_ONLY': 727" in out.stdout)
    check("GENUINE=22", "'GENUINE': 22" in out.stdout)
    genuine = load("training_data/phase22_genuine22.jsonl")
    check("genuine22.jsonl has exactly 22 records", len(genuine) == 22, str(len(genuine)))
    check("every genuine record carries the Rs-party gate issue",
          all("Party 'Rs' not supported by input text" in g["grounding_issues"] for g in genuine))

    print("=== E/F/G. v0.2 dataset integrity ===")
    rows = load("training_data/phase22_v02_train.jsonl")
    check("v02 rows == 1793", len(rows) == 1793, str(len(rows)))
    check("every output has exactly the 18 contract fields",
          all(len(r["output"]) == 18 for r in rows))
    schema_bad = []
    for r in rows:
        rep = validate_structured_interpretation(r["output"], allow_expanded=True)
        if not rep.valid:
            schema_bad.append(r["id"])
    check("all 1793 rows pass production schema verifier", not schema_bad, schema_bad[:5])
    check("ids unique", len({r["id"] for r in rows}) == len(rows))
    check("inputs unique (normalized)", len({key(r["input"]) for r in rows}) == len(rows))

    print("=== F. leakage ===")
    val = load("training_data/fyjc_specialist_validation.jsonl")
    test = load("training_data/fyjc_specialist_test.jsonl")
    eval_keys = {key(r["input"]) for r in val} | {key(r["input"]) for r in test}
    check("zero overlap vs canonical validation/test",
          not any(key(r["input"]) in eval_keys for r in rows))
    bench = load("training/phase17_benchmark.jsonl")
    bench_keys = {key(b["input"]) for b in bench}
    bench_toks = [(b["id"], tok(b["input"])) for b in bench]
    v02_ids = {r["id"] for r in rows}
    check("hc_00092 / hc_00928 absent", not ({"hc_00092", "hc_00928"} & v02_ids))
    leaks = []
    for r in rows:
        k = key(r["input"])
        if k in bench_keys:
            leaks.append(r["id"]); continue
        t = tok(r["input"])
        for bid, bt in bench_toks:
            if jaccard(t, bt) >= 0.75:
                leaks.append(f"{r['id']}~{bid}"); break
    check("zero exact/near-dup vs Phase 17 benchmark", not leaks, leaks[:5])

    print("=== G. corrective re-labels ===")
    corr = [r for r in rows if r["metadata"].get("phase22") == "corrective_relabel"]
    check("exactly 21 corrective rows (4 audited + 17 swept)", len(corr) == 21, [c["id"] for c in corr])
    check("corrective rows record the payment conflict",
          all(c["output"]["ambiguity_flags"] == ["CONFLICTING_INFORMATION"]
              and c["output"]["payment_method_enum"] == "UNKNOWN"
              and c["output"]["suggested_status"] == "REVIEW_REQUIRED" for c in corr))
    dual = [r for r in rows
            if r["output"]["transaction_type_enum"] in ("PURCHASE", "PAYMENT")
            and r["output"]["suggested_status"] == "VERIFIED"
            and "cheque" in r["input"].lower() and "cash" in r["input"].lower()
            and " both " not in r["input"].lower()]
    check("no uncorrected dual-payment VERIFIED purchase/payment target remains", not dual, [d["id"] for d in dual])

    print("=== H. canonical passthrough fidelity ===")
    canon = {r["id"]: r for r in load("training_data/fyjc_specialist_train.jsonl")}
    kept = [r for r in rows if r["metadata"].get("phase22") == "canonical"]
    mismatched = [r["id"] for r in kept if json.dumps(r["output"], sort_keys=True) != json.dumps(canon[r["id"]]["output"], sort_keys=True)]
    check("kept canonical rows byte-identical to v0.1 train source", not mismatched, mismatched[:5])

    print("=== I. manifest consistency ===")
    man = json.load(open("training_data/phase22_v02_train.manifest.json"))
    check("manifest row_count matches", man["row_count"] == len(rows))
    check("manifest output sha matches file", man["output_sha256"] == sha256_file("training_data/phase22_v02_train.jsonl"))
    check("manifest leakage result PASS", man["leakage_checks"]["result"] == "PASS")

    print("=== J. determinism of the builder ===")
    before = sha256_file("training_data/phase22_v02_train.jsonl")
    r2 = subprocess.run([sys.executable, "training/phase22_build_v02.py"], capture_output=True, text=True)
    after = sha256_file("training_data/phase22_v02_train.jsonl")
    check("builder rerun exits 0", r2.returncode == 0, r2.stderr[-300:])
    check("rebuild byte-identical", before == after)

    print(f"\n=== fte_fyjc_72: {PASS} PASS / {FAIL} FAIL ===")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
