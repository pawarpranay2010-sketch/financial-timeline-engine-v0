"""
Financial Timeline Engine
Sprint 15I-G - Verified Question Bank (Question Infrastructure)
backend/maths/fyjc_question_bank.py

The lifecycle + storage layer around the Sprint 15I-G Content Compiler
(backend.maths.fyjc_content_compiler). Owns the question lifecycle, the
JSON store, the internal API and the human/LLM metadata workflow.

Lifecycle (hard gates enforced):

    DRAFT  --compile-->  COMPILED  --validate(PASS)-->  VALIDATING
                                                     \\--validate(FAIL)--> REJECTED
                                                      \\--validate(review)--> REVIEW_REQUIRED
    VALIDATING --approve--> APPROVED
    APPROVED / VALIDATING / COMPILED / DRAFT --reject--> REJECTED

Invariants
----------
* APPROVED is only reachable through deterministic verification: approve()
  re-runs the FT-E engine + accounting invariants and refuses if anything
  fails. Teacher metadata edits NEVER bypass verification - an edit that
  touches raw_text drops the question back to DRAFT.
* LLM output is CANDIDATE EVIDENCE ONLY. apply_llm_suggestions() merges
  suggested metadata with provenance 'llm_suggested'; it can never write
  expected_journal / validation results / status, and it can never change
  raw_text.
* Rejected material is never silently repaired.
* Provenance is preserved end-to-end (source block + per-field
  metadata_provenance).
* Duplicate detection is conservative: exact normalized duplicates are
  flagged (duplicate_of) but never merged or deleted.

Storage: a JSON file (default content_bank/questions.json at the repo
root, overridable for tests / deployments). This module is pure: no
Streamlit, no AI calls, no network.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from backend.maths.fyjc_content_compiler import (
    COMPILER_VERSION,
    STATUS_APPROVED,
    STATUS_COMPILED,
    STATUS_DRAFT,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_VALIDATING,
    UNKNOWN,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_REVIEW,
    build_provenance,
    compare_expected,
    default_metadata,
    find_duplicate,
    make_question_id,
    normalize_question_text,
    question_fingerprint,
    verify_question,
)

SCHEMA_VERSION = "15I-G-1"
FYJC_CONTENT_BANK_PATH = os.path.join("content_bank", "questions.json")

# Metadata fields a TEACHER may correct directly. Everything else (the
# verified journal, amounts, accounts, validation results) is owned by the
# deterministic pipeline.
TEACHER_EDITABLE_METADATA = (
    "chapter", "concept", "concept_key", "difficulty", "board",
    "question_style", "tags", "source_name", "source_reference",
)


class QuestionBank:
    """Internal verified question bank with JSON persistence."""

    def __init__(self, store_path: Optional[str] = None) -> None:
        self.store_path = store_path or FYJC_CONTENT_BANK_PATH
        self._questions: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self.store_path):
            return
        with open(self.store_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            self._questions = data.get("questions") or {}

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "questions": self._questions,
        }
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
        return self.store_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_question(
            self,
            raw_text: str,
            source_type: str,
            source_name: Optional[str] = None,
            source_reference: Optional[str] = None,
            expected: Optional[Dict[str, List[List[Any]]]] = None,
            tags: Optional[List[str]] = None,
            ingestion_timestamp: Optional[str] = None) -> str:
        """Create a question in DRAFT state.

        expected: optional teacher/candidate expected journal in the
        compact form {'debit': [[account, amount], ...],
        'credit': [[account, amount], ...]}. It is stored SEPARATELY and
        compared against the engine at validation time - a disagreement
        forces REVIEW_REQUIRED, never a silent override.
        """
        raw = str(raw_text or "")
        if not raw.strip():
            raise ValueError("create_question: raw_text is required")

        qid = make_question_id(raw, source_reference)
        # Exact content identity (same text AND same source) is a hard
        # duplicate - the bank never stores two entries with the same
        # identity, and never silently overwrites one either.
        if qid in self._questions:
            raise ValueError(
                f"create_question: exact duplicate (same question text "
                f"and source) already exists: {qid}")

        # Conservative duplicate detection: flag, never merge.
        duplicate_of = find_duplicate(
            raw, list(self._questions.values()), source_reference)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        question: Dict[str, Any] = {
            "question_id": qid,
            "raw_text": raw,
            "normalized_text": normalize_question_text(raw),
            "_normalized_fingerprint": question_fingerprint(raw),
            "status": STATUS_DRAFT,
            "subject": UNKNOWN,
            "curriculum": "FYJC",
            "class_year": "FYJC",
            "board": UNKNOWN,
            "chapter": UNKNOWN,
            "concept": UNKNOWN,
            "concept_key": UNKNOWN,
            "difficulty": UNKNOWN,
            "transaction_count": UNKNOWN,
            "transaction_types": [],
            "question_style": UNKNOWN,
            "expected_journal": None,
            "teacher_expected_journal": expected,
            "expected_accounts": [],
            "expected_amounts": [],
            "canonical_id": None,
            "variants": [],
            "tags": list(tags or []),
            "source": build_provenance(
                source_type, source_name, source_reference,
                ingestion_timestamp),
            "metadata_provenance": {},
            "validation_status": None,
            "validation_errors": [],
            "validation_warnings": [],
            "verification": None,
            "duplicate_of": duplicate_of,
            "lifecycle": [{
                "action": "create",
                "status": STATUS_DRAFT,
                "at": now,
                "note": ("flagged duplicate of "
                         f"{duplicate_of}") if duplicate_of else None,
            }],
            "created_at": now,
            "updated_at": now,
        }
        self._questions[qid] = question
        return qid

    def compile_question(self, qid: str) -> str:
        """DRAFT -> COMPILED. Fills structured metadata deterministically
        (subject, curriculum, chapter hints, transaction count/types)."""
        q = self._require(qid)
        from backend.maths.fyjc_content_compiler import (
            detect_chapter, transaction_breakdown)
        meta = default_metadata(q["raw_text"])
        for key, value in meta.items():
            if key == "concept":
                q["concept"] = value
            elif key == "concept_key":
                q["concept_key"] = value
            else:
                q[key] = value
        q["normalized_text"] = normalize_question_text(q["raw_text"])
        q["status"] = STATUS_COMPILED
        q["metadata_provenance"] = {
            k: "deterministic" for k in (
                "subject", "curriculum", "class_year", "chapter", "concept",
                "difficulty", "transaction_count", "transaction_types",
                "question_style") if q.get(k) != UNKNOWN}
        q["lifecycle"].append({
            "action": "compile", "status": STATUS_COMPILED,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        q["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return qid

    def validate_question(self, qid: str) -> str:
        """COMPILED -> VALIDATING / REJECTED / REVIEW_REQUIRED.

        Runs the candidate through the FT-E engine + every accounting
        invariant (verify_question). Also compares a teacher-supplied
        expected journal: disagreement -> REVIEW_REQUIRED.
        """
        q = self._require(qid)
        if q["status"] not in (STATUS_COMPILED, STATUS_VALIDATING,
                               STATUS_REVIEW_REQUIRED):
            raise ValueError(
                f"validate_question: cannot validate from status "
                f"{q['status']}")
        q["status"] = STATUS_VALIDATING
        verification = verify_question(q["raw_text"])
        q["verification"] = verification
        q["validation_errors"] = list(verification["errors"])
        q["validation_warnings"] = list(verification["warnings"])

        teacher_expected = q.get("teacher_expected_journal")
        if teacher_expected is not None:
            match = compare_expected(
                verification["expected_journal"], teacher_expected)
            if not match:
                verification = dict(verification)
                verification["verdict"] = VERDICT_REVIEW
                verification["errors"] = list(verification["errors"]) + [
                    "teacher/candidate expected journal disagrees with the "
                    "FT-E verified journal"]
                q["verification"] = verification
                q["validation_errors"] = verification["errors"]

        verdict = (verification or {}).get("verdict")
        if verdict == VERDICT_PASS:
            q["status"] = STATUS_VALIDATING
            q["validation_status"] = "PENDING_APPROVAL"
        elif verdict == VERDICT_REVIEW:
            q["status"] = STATUS_REVIEW_REQUIRED
            q["validation_status"] = verdict
        else:
            q["status"] = STATUS_REJECTED
            q["validation_status"] = verdict

        # Verified journal is adopted ONLY from the deterministic pipeline.
        q["expected_journal"] = verification.get("expected_journal")
        q["expected_accounts"] = list(verification.get("expected_accounts")
                                      or [])
        q["expected_amounts"] = list(verification.get("expected_amounts")
                                     or [])
        q["lifecycle"].append({
            "action": "validate", "status": q["status"],
            "verdict": verdict,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        q["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return qid

    def approve_question(self, qid: str) -> str:
        """VALIDATING -> APPROVED. Hard gate: re-runs deterministic
        verification at approval time so no edit can sneak in."""
        q = self._require(qid)
        if q["status"] != STATUS_VALIDATING:
            raise ValueError(
                f"approve_question: only VALIDATING can be approved "
                f"(status is {q['status']})")
        # Re-verify at approval - verification is ALWAYS authoritative.
        verification = verify_question(q["raw_text"])
        q["verification"] = verification
        q["validation_errors"] = list(verification["errors"])
        if verification["verdict"] != VERDICT_PASS:
            q["status"] = STATUS_REJECTED
            q["validation_status"] = verification["verdict"]
            q["lifecycle"].append({
                "action": "approve_denied", "status": STATUS_REJECTED,
                "verdict": verification["verdict"],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            q["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            raise ValueError(
                "approve_question: verification failed at approval time "
                f"({verification['verdict']})")
        q["status"] = STATUS_APPROVED
        q["validation_status"] = "APPROVED"
        q["expected_journal"] = verification.get("expected_journal")
        q["expected_accounts"] = list(verification.get("expected_accounts")
                                      or [])
        q["expected_amounts"] = list(verification.get("expected_amounts")
                                     or [])
        q["lifecycle"].append({
            "action": "approve", "status": STATUS_APPROVED,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        q["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return qid

    def reject_question(self, qid: str,
                        reason: Optional[str] = None) -> str:
        """Move any non-approved question to REJECTED. Approved questions
        are immutable - rejecting them is out of scope (content lifecycle
        policy, not a code limitation)."""
        q = self._require(qid)
        if q["status"] == STATUS_APPROVED:
            raise ValueError("reject_question: approved questions are "
                             "immutable; edit + re-approve instead")
        q["status"] = STATUS_REJECTED
        q["validation_status"] = "REJECTED"
        if reason:
            q["validation_errors"] = list(q.get("validation_errors") or []) \
                + [reason]
        q["lifecycle"].append({
            "action": "reject", "status": STATUS_REJECTED,
            "note": reason, "at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        q["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return qid

    # ------------------------------------------------------------------
    # Retrieval / listing / filtering
    # ------------------------------------------------------------------

    def get_question(self, qid: str) -> Dict[str, Any]:
        return dict(self._require(qid))

    def list_questions(self,
                       include_internal: bool = False) -> List[Dict[str, Any]]:
        out = []
        for q in self._questions.values():
            item = dict(q)
            if not include_internal:
                item.pop("_normalized_fingerprint", None)
            out.append(item)
        return sorted(out, key=lambda q: q["question_id"])

    def list_approved(self) -> List[Dict[str, Any]]:
        return [q for q in self.list_questions()
                if q["status"] == STATUS_APPROVED]

    def filter_by_chapter(self, chapter: str) -> List[Dict[str, Any]]:
        return [q for q in self.list_questions()
                if q.get("chapter") == chapter]

    def filter_by_concept(self, concept: str) -> List[Dict[str, Any]]:
        return [q for q in self.list_questions()
                if q.get("concept") == concept]

    def filter_by_difficulty(self, difficulty: Any) -> List[Dict[str, Any]]:
        return [q for q in self.list_questions()
                if q.get("difficulty") == difficulty]

    def filter_by_transaction_type(
            self, type_key: str) -> List[Dict[str, Any]]:
        return [q for q in self.list_questions()
                if type_key in (q.get("transaction_types") or [])
                or q.get("concept_key") == type_key]

    # ------------------------------------------------------------------
    # Teacher workflow (metadata corrections)
    # ------------------------------------------------------------------

    def set_metadata(self, qid: str,
                     updates: Dict[str, Any]) -> str:
        """Teacher/corrector metadata edit.

        * Only TEACHER_EDITABLE_METADATA fields may be written.
        * Edits never touch raw_text / expected_journal / validation.
        * Every edited field is recorded in metadata_provenance as
          'teacher'.
        * Any attempt to edit raw_text (or anything else non-editable)
          raises ValueError - a text change is a new question that must
          flow through compile -> validate -> approve again.
        """
        q = self._require(qid)
        disallowed = set(updates) - set(TEACHER_EDITABLE_METADATA)
        if disallowed:
            raise ValueError(
                "set_metadata: field(s) not teacher-editable: "
                f"{sorted(disallowed)}. Changing the question text or the "
                "verified journal is not allowed - create a new question.")
        provenance = dict(q.get("metadata_provenance") or {})
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for key, value in updates.items():
            q[key] = value
            provenance[key] = "teacher"
        q["metadata_provenance"] = provenance
        q["lifecycle"].append({
            "action": "teacher_edit", "fields": sorted(updates),
            "at": now})
        q["updated_at"] = now
        return qid

    # ------------------------------------------------------------------
    # LLM suggestions (candidate evidence ONLY)
    # ------------------------------------------------------------------

    def apply_llm_suggestions(self, qid: str,
                              suggestions: Dict[str, Any]) -> str:
        """Merge LLM-suggested metadata as CANDIDATE EVIDENCE.

        * Only editable metadata fields may be suggested.
        * Suggested values carry metadata_provenance 'llm_suggested' and
          are stored separately from any teacher override.
        * A suggestion can NEVER write expected_journal, verification
          results, status, validation fields or raw_text.
        * Verification remains authoritative: status is untouched and the
          next validate/approve re-runs the deterministic engine.
        """
        q = self._require(qid)
        editable = set(TEACHER_EDITABLE_METADATA)
        disallowed = set(suggestions) - editable
        if disallowed:
            raise ValueError(
                "apply_llm_suggestions: suggestions may only cover "
                f"teacher-editable metadata, not {sorted(disallowed)}")
        provenance = dict(q.get("metadata_provenance") or {})
        llm_store = dict(q.get("llm_suggested_metadata") or {})
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for key, value in suggestions.items():
            if q.get("metadata_provenance", {}).get(key) != "teacher":
                # An LLM suggestion fills an unknown field; a teacher
                # override always wins.
                q[key] = value
                provenance[key] = "llm_suggested"
            llm_store[key] = value
        q["llm_suggested_metadata"] = llm_store
        q["metadata_provenance"] = provenance
        q["lifecycle"].append({
            "action": "llm_suggest", "fields": sorted(suggestions),
            "at": now})
        q["updated_at"] = now
        return qid

    # ------------------------------------------------------------------
    # Variants (point back to one canonical answer)
    # ------------------------------------------------------------------

    def link_variant(self, canonical_id: str, raw_text: str,
                     source_type: str,
                     source_name: Optional[str] = None,
                     source_reference: Optional[str] = None,
                     tags: Optional[List[str]] = None) -> str:
        """Add a wording/punctuation variant of an existing APPROVED
        canonical question.

        Hard gate: the variant must verify to the SAME journal as the
        canonical (accounts + amounts, order-insensitive). A variant that
        changes accounting meaning is REJECTED - variants never change
        the canonical answer.
        """
        canonical = self._require(canonical_id)
        if canonical["status"] != STATUS_APPROVED:
            raise ValueError(
                "link_variant: canonical question must be APPROVED "
                f"(status is {canonical['status']})")
        vid = self.create_question(
            raw_text, source_type, source_name, source_reference,
            expected=canonical.get("expected_journal"), tags=tags)
        vq = self._questions[vid]
        vq["canonical_id"] = canonical_id
        vq["lifecycle"][-1]["note"] = "variant of " + canonical_id
        self.compile_question(vid)
        self.validate_question(vid)
        if vq["status"] == STATUS_VALIDATING:
            # expected == canonical journal was enforced by validate();
            # adopt the canonical link on approval.
            self.approve_question(vid)
            canonical.setdefault("variants", []).append(vid)
            self._questions[canonical_id]["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        else:
            vq["lifecycle"].append({
                "action": "variant_rejected", "status": vq["status"],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return vid

    # ------------------------------------------------------------------
    # Seed from the existing verified benchmarks (provenance preserved)
    # ------------------------------------------------------------------

    def seed_from_benchmark(self, module: Any,
                            source_name: Optional[str] = None,
                            status_filter: str = "VERIFIED") -> Dict[str, Any]:
        """Ingest questions from an existing benchmark module (e.g.
        backend.maths.fyjc_bk_15e_benchmark) through the FULL compile ->
        validate -> approve pipeline.

        Only cases whose oracle status matches status_filter are admitted.
        Provenance records the benchmark module as the source. Duplicate
        candidates inside the benchmark are flagged (duplicate_of), never
        merged. Returns {created, approved, rejected, review_required,
        duplicates}.
        """
        cases: List[Dict[str, Any]] = []
        for name in dir(module):
            value = getattr(module, name)
            if isinstance(value, list) and value and \
                    isinstance(value[0], dict) and \
                    "question" in value[0]:
                cases.extend(value)
        result = {"created": 0, "approved": 0, "rejected": 0,
                  "review_required": 0, "duplicates": 0}
        mod_name = source_name or getattr(module, "__name__", UNKNOWN)
        seen_ids: set = set()
        for case in cases:
            if case.get("status") != status_filter:
                continue
            # The same wording repeated inside one benchmark corpus is a
            # content duplicate, not a new question - flag and skip (the
            # bank never stores two identical identities).
            qid = make_question_id(
                case["question"],
                str(case.get("source") or case.get("category") or UNKNOWN))
            if qid in seen_ids or qid in self._questions:
                result["duplicates"] += 1
                continue
            seen_ids.add(qid)
            qid = self.create_question(
                case["question"],
                source_type="textbook",
                source_name=mod_name,
                source_reference=str(case.get("source")
                                     or case.get("category") or UNKNOWN),
                expected=({"debit": case.get("debit") or [],
                           "credit": case.get("credit") or []}
                          if case.get("debit") is not None
                          or case.get("credit") is not None else None),
                tags=[str(t) for t in (case.get("category"),
                                       case.get("chapter")) if t],
            )
            result["created"] += 1
            if self._questions[qid].get("duplicate_of"):
                result["duplicates"] += 1
            self.compile_question(qid)
            self.validate_question(qid)
            q = self._questions[qid]
            if q["status"] == STATUS_VALIDATING:
                try:
                    self.approve_question(qid)
                    result["approved"] += 1
                except ValueError:
                    result["rejected"] += 1
            elif q["status"] == STATUS_REJECTED:
                result["rejected"] += 1
            else:
                result["review_required"] += 1
        return result

    # ------------------------------------------------------------------

    def _require(self, qid: str) -> Dict[str, Any]:
        if qid not in self._questions:
            raise KeyError(f"unknown question_id: {qid}")
        return self._questions[qid]
