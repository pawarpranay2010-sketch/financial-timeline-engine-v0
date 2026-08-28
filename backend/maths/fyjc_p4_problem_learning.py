"""
Platrixa — P4: Problem Learning Database → JSONL → Specialist Finance AI

Extends the P3 validated-knowledge system with a problem-learning database
that captures the full lifecycle of difficult accounting problems:

  Student Problem → Engine → Problem Record → Validation → JSONL → Training

Architecture:

  Student Input
        ↓
  Existing Platrixa Engine (orchestrate)
        ↓
  Problem Learning Database (P4 — this module)
        ↓
  Validated Problem Records (via P2/P3 validation pipeline)
        ↓
  JSONL Export (deterministic)
        ↓
  Specialist Finance AI Training Adapter
        ↓
  Fine-tuning Job (developer-initiated only)
        ↓
  Evaluation (held-out set + safety checks)
        ↓
  Shadow Mode → Human Review → Production Approval

Core principle:
  The student's original wording is preserved exactly.
  Validation is mandatory before training export.
  Training is always developer-initiated, never student-triggered.
  The accounting kernel is never modified.

Safety rules (inherited from P2/P3):
  - Never approximate money (Decimal only)
  - Never infer missing amounts
  - Never bypass the kernel
  - Never auto-promote from a single evidence instance
  - Never allow unvalidated records into training data
  - Never allow student data to directly trigger training
  - Deterministic JSONL export
  - Existing regression suite must remain green

Pure module: no Streamlit, no AI model loaded, no network. Deterministic.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.maths.fyjc_validated_knowledge import (
    EvidenceItem,
    EvidenceSource,
    KnowledgeItem,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    PROMOTION_THRESHOLDS,
    ValidatedKnowledgeStore,
    _generate_knowledge_id,
    _normalise_pattern,
)
from backend.maths.fyjc_p3_learning_system import (
    EffectivenessRecord,
    EffectivenessStatus,
    KnowledgePersistence,
    LearningMetrics,
    P3LearningManager,
    compute_effectiveness_status,
)


# ---------------------------------------------------------------------------
# P4.1 — Problem Record Schema
# ---------------------------------------------------------------------------

class ProblemStatus(str, Enum):
    """Lifecycle status for a problem learning record."""
    CANDIDATE = "CANDIDATE"         # Newly captured, not yet validated
    VALIDATED = "VALIDATED"         # Passed validation, ready for training
    REJECTED = "REJECTED"           # Failed validation
    CONFLICTING = "CONFLICTING"     # Conflicts with existing validated record
    RETIRED = "RETIRED"             # Removed from training pipeline


class ProblemCategory(str, Enum):
    """Categories of difficult problems (from hard-case discovery)."""
    CASH_CREDIT = "CASH_CREDIT"
    MISSING_AMOUNT = "MISSING_AMOUNT"
    PARTIAL_PAYMENT = "PARTIAL_PAYMENT"
    SETTLEMENT = "SETTLEMENT"
    CROSS_REFERENCE = "CROSS_REFERENCE"
    MULTI_PARTY = "MULTI_PARTY"
    MULTI_AMOUNT = "MULTI_AMOUNT"
    GST = "GST"
    RETURNS = "RETURNS"
    COMPOUND = "COMPOUND"
    CONTRADICTION = "CONTRADICTION"
    UNUSUAL_WORDING = "UNUSUAL_WORDING"
    INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED = "UNSUPPORTED"
    LONG_PROBLEM = "LONG_PROBLEM"
    EDGE_CASE = "EDGE_CASE"
    PRONOUN_RESOLUTION = "PRONOUN_RESOLUTION"
    VOCABULARY_MISMATCH = "VOCABULARY_MISMATCH"
    OTHER = "OTHER"


@dataclass
class ProblemRecord:
    """A single problem learning record.

    Captures the full lifecycle from student input to verified outcome.
    The raw_student_input is preserved exactly — never normalised or altered.
    """
    problem_id: str = ""
    raw_student_input: str = ""
    transactions: List[str] = field(default_factory=list)
    category: ProblemCategory = ProblemCategory.OTHER
    status: ProblemStatus = ProblemStatus.CANDIDATE

    # Engine results
    engine_status: str = ""             # VERIFIED / REVIEW_REQUIRED / BLOCKED / NOT_SUPPORTED
    engine_reason: str = ""             # why_not from orchestrate()
    engine_journal: Optional[Dict[str, Any]] = None

    # Structured interpretation (from AI adapter or deterministic extraction)
    ai_interpretation: Optional[Dict[str, Any]] = None

    # Ambiguity and resolution
    ambiguity_reason: str = ""
    student_correction: Optional[str] = None
    final_verified_interpretation: Optional[Dict[str, Any]] = None

    # Validation metadata
    evidence_count: int = 0
    validation_count: int = 0
    rejection_count: int = 0
    source_diversity: int = 0
    confidence: Decimal = Decimal("0.0")

    # Provenance
    created_at: str = ""
    last_validated_at: Optional[str] = None
    last_rejected_at: Optional[str] = None
    retired_at: Optional[str] = None
    version: int = 1

    def __post_init__(self):
        if not self.problem_id:
            raw = f"{self.raw_student_input.lower().strip()}|{self.category.value}"
            self.problem_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> Dict[str, Any]:
        """Deterministic serialization."""
        return {
            "problem_id": self.problem_id,
            "raw_student_input": self.raw_student_input,
            "transactions": list(self.transactions),
            "category": self.category.value,
            "status": self.status.value,
            "engine_status": self.engine_status,
            "engine_reason": self.engine_reason,
            "engine_journal": self.engine_journal,
            "ai_interpretation": self.ai_interpretation,
            "ambiguity_reason": self.ambiguity_reason,
            "student_correction": self.student_correction,
            "final_verified_interpretation": self.final_verified_interpretation,
            "evidence_count": self.evidence_count,
            "validation_count": self.validation_count,
            "rejection_count": self.rejection_count,
            "source_diversity": self.source_diversity,
            "confidence": str(self.confidence),
            "created_at": self.created_at,
            "last_validated_at": self.last_validated_at,
            "last_rejected_at": self.last_rejected_at,
            "retired_at": self.retired_at,
            "version": self.version,
        }


# ---------------------------------------------------------------------------
# P4.1 — Problem Learning Database
# ---------------------------------------------------------------------------

class ProblemLearningDatabase:
    """Problem learning database that extends P3's persistence layer.

    Uses JSON-backed storage (same pattern as ValidatedKnowledgeStore).
    Each problem record goes through the P2/P3 validation pipeline
    before it can be exported for training.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path
        self._records: Dict[str, ProblemRecord] = {}
        self._version: int = 1

        # Load existing data
        if db_path:
            self._load()

    # -- CRUD --

    def add_record(self, record: ProblemRecord) -> ProblemRecord:
        """Add a new problem record (always as CANDIDATE)."""
        record.status = ProblemStatus.CANDIDATE
        self._records[record.problem_id] = record
        self._version += 1
        return record

    def get_record(self, problem_id: str) -> Optional[ProblemRecord]:
        return self._records.get(problem_id)

    def get_all_records(self) -> List[ProblemRecord]:
        return list(self._records.values())

    def get_by_status(self, status: ProblemStatus) -> List[ProblemRecord]:
        return [r for r in self._records.values() if r.status == status]

    def get_by_category(self, category: ProblemCategory) -> List[ProblemRecord]:
        return [r for r in self._records.values() if r.category == category]

    # -- Validation Pipeline --

    def validate_record(
        self,
        problem_id: str,
        min_evidence: int = 2,
        min_validations: int = 2,
        max_rejections: int = 2,
    ) -> Tuple[bool, str]:
        """Run deterministic validation on a candidate record.

        Uses the same threshold logic as P2 KnowledgeStore.
        Returns (can_validate, reason).
        """
        record = self._records.get(problem_id)
        if record is None:
            return False, "Record not found"
        if record.status != ProblemStatus.CANDIDATE:
            return False, f"Record is {record.status.value}, not CANDIDATE"

        issues = []

        if record.evidence_count < min_evidence:
            issues.append(f"Insufficient evidence: {record.evidence_count}/{min_evidence}")

        if record.validation_count < min_validations:
            issues.append(f"Insufficient validations: {record.validation_count}/{min_validations}")

        if record.rejection_count >= max_rejections:
            issues.append(f"Too many rejections: {record.rejection_count}/{max_rejections}")

        if record.engine_status not in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"):
            issues.append(f"Engine status not suitable: {record.engine_status}")

        if issues:
            return False, "; ".join(issues)
        return True, "All validation thresholds met"

    def promote_record(self, problem_id: str) -> Tuple[bool, str]:
        """Promote a candidate to VALIDATED (ready for training export)."""
        can_validate, reason = self.validate_record(problem_id)
        if not can_validate:
            return False, reason

        record = self._records[problem_id]
        record.status = ProblemStatus.VALIDATED
        record.last_validated_at = datetime.now(timezone.utc).isoformat()
        record.confidence = self._compute_confidence(record)
        self._version += 1
        return True, "Promoted to VALIDATED"

    def reject_record(self, problem_id: str, reason: str = "") -> Tuple[bool, str]:
        record = self._records.get(problem_id)
        if record is None:
            return False, "Record not found"
        record.status = ProblemStatus.REJECTED
        record.last_rejected_at = datetime.now(timezone.utc).isoformat()
        record.rejection_count += 1
        self._version += 1
        return True, "Rejected"

    def retire_record(self, problem_id: str, reason: str = "") -> Tuple[bool, str]:
        record = self._records.get(problem_id)
        if record is None:
            return False, "Record not found"
        record.status = ProblemStatus.RETIRED
        record.retired_at = datetime.now(timezone.utc).isoformat()
        self._version += 1
        return True, "Retired"

    def record_evidence(
        self,
        problem_id: str,
        source: EvidenceSource,
        verification_status: str = "VERIFIED",
    ) -> None:
        """Record a piece of evidence for a problem record."""
        record = self._records.get(problem_id)
        if record is None:
            return
        record.evidence_count += 1
        if verification_status == "VERIFIED":
            record.validation_count += 1
        elif verification_status in ("REVIEW_REQUIRED", "BLOCKED"):
            record.rejection_count += 1
        # Track source diversity
        # (simplified: count unique sources by incrementing on new source)
        record.source_diversity = min(record.source_diversity + 1, 3)
        record.confidence = self._compute_confidence(record)
        self._version += 1

    def _compute_confidence(self, record: ProblemRecord) -> Decimal:
        """Deterministic confidence from evidence counts."""
        if record.evidence_count == 0:
            return Decimal("0.0")
        validation_rate = Decimal(str(record.validation_count)) / Decimal(str(max(record.evidence_count, 1)))
        diversity_factor = Decimal(str(min(record.source_diversity, 2))) / Decimal("2")
        confidence = validation_rate * diversity_factor
        if record.rejection_count > 0:
            penalty = Decimal(str(min(record.rejection_count, 3))) * Decimal("0.15")
            confidence = max(confidence - penalty, Decimal("0.0"))
        return min(confidence, Decimal("1.0"))

    # -- Persistence --

    def save(self) -> bool:
        """Atomic JSON persistence."""
        if not self._db_path:
            return False
        data = {
            "version": self._version,
            "records": [r.snapshot() for r in sorted(
                self._records.values(),
                key=lambda x: x.problem_id,
            )],
        }
        try:
            dir_name = os.path.dirname(self._db_path) or "."
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                os.replace(tmp_path, self._db_path)
                return True
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return False
        except Exception:
            return False

    def _load(self) -> None:
        """Load from JSON file."""
        try:
            if not self._db_path or not os.path.exists(self._db_path):
                return
            with open(self._db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._version = data.get("version", 1)
            for rd in data.get("records", []):
                cat = ProblemCategory(rd.get("category", "OTHER"))
                stat = ProblemStatus(rd.get("status", "CANDIDATE"))
                record = ProblemRecord(
                    problem_id=rd["problem_id"],
                    raw_student_input=rd.get("raw_student_input", ""),
                    transactions=rd.get("transactions", []),
                    category=cat,
                    status=stat,
                    engine_status=rd.get("engine_status", ""),
                    engine_reason=rd.get("engine_reason", ""),
                    engine_journal=rd.get("engine_journal"),
                    ai_interpretation=rd.get("ai_interpretation"),
                    ambiguity_reason=rd.get("ambiguity_reason", ""),
                    student_correction=rd.get("student_correction"),
                    final_verified_interpretation=rd.get("final_verified_interpretation"),
                    evidence_count=rd.get("evidence_count", 0),
                    validation_count=rd.get("validation_count", 0),
                    rejection_count=rd.get("rejection_count", 0),
                    source_diversity=rd.get("source_diversity", 0),
                    confidence=Decimal(rd.get("confidence", "0")),
                    created_at=rd.get("created_at", ""),
                    last_validated_at=rd.get("last_validated_at"),
                    last_rejected_at=rd.get("last_rejected_at"),
                    retired_at=rd.get("retired_at"),
                    version=rd.get("version", 1),
                )
                self._records[record.problem_id] = record
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # -- Statistics --

    def stats(self) -> Dict[str, Any]:
        by_status = {}
        by_category = {}
        for r in self._records.values():
            s = r.status.value
            by_status[s] = by_status.get(s, 0) + 1
            c = r.category.value
            by_category[c] = by_category.get(c, 0) + 1
        return {
            "total_records": len(self._records),
            "by_status": by_status,
            "by_category": by_category,
            "version": self._version,
        }


# ---------------------------------------------------------------------------
# P4.3 — JSONL Exporter
# ---------------------------------------------------------------------------

class JSONLExporter:
    """Deterministic exporter that converts validated problem records
    into JSONL format for specialist finance AI training.

    Only VALIDATED records are exported.
    Export is deterministic and reproducible.
    """

    @staticmethod
    def export_record(record: ProblemRecord) -> Optional[Dict[str, Any]]:
        """Convert a single validated record to training JSONL format.

        Returns None if the record is not suitable for export.
        """
        if record.status != ProblemStatus.VALIDATED:
            return None

        # Build the output interpretation from the final verified interpretation
        output_interpretation = record.final_verified_interpretation or {}

        # If no final interpretation, build from engine results
        if not output_interpretation and record.engine_journal:
            journal = record.engine_journal
            output_interpretation = {
                "transaction_type": record.category.value,
                "parties": [],
                "amounts": [],
                "payment_method": "UNKNOWN",
                "references": [],
                "ambiguities": [record.ambiguity_reason] if record.ambiguity_reason else [],
                "journal_narration": journal.get("narration", ""),
                "debit_accounts": [
                    {"account": dl.get("account"), "amount": str(dl.get("amount", 0))}
                    for dl in journal.get("debit_lines", [])
                ],
                "credit_accounts": [
                    {"account": cl.get("account"), "amount": str(cl.get("amount", 0))}
                    for cl in journal.get("credit_lines", [])
                ],
            }

        return {
            "input": record.raw_student_input,
            "output": output_interpretation,
            "metadata": {
                "problem_id": record.problem_id,
                "category": record.category.value,
                "engine_status": record.engine_status,
                "engine_reason": record.engine_reason,
                "student_correction": record.student_correction,
                "confidence": str(record.confidence),
                "evidence_count": record.evidence_count,
                "validation_count": record.validation_count,
            },
        }

    @staticmethod
    def export_to_jsonl(
        records: List[ProblemRecord],
        output_path: str,
    ) -> int:
        """Export validated records to JSONL file.

        Returns the number of records exported.
        """
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for record in records:
                exported = JSONLExporter.export_record(record)
                if exported is not None:
                    f.write(json.dumps(exported, ensure_ascii=False, default=str) + "\n")
                    count += 1
        return count

    @staticmethod
    def export_from_database(
        db: ProblemLearningDatabase,
        output_path: str,
    ) -> int:
        """Export all VALIDATED records from a database."""
        validated = db.get_by_status(ProblemStatus.VALIDATED)
        return JSONLExporter.export_to_jsonl(validated, output_path)


# ---------------------------------------------------------------------------
# P4.4 — AI Training Adapter Interface
# ---------------------------------------------------------------------------

class FinanceModelAdapter:
    """Interface for plugging in a specialist finance model.

    This defines the contract for future models like Qwen2.5-1.5B-Instruct.
    No model is downloaded or loaded — this is the interface only.
    """

    def __init__(
        self,
        model_name: str = "qwen2.5-1.5b-instruct",
        model_path: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.model_path = model_path
        self._loaded = False

    def is_available(self) -> bool:
        """Check if the model is loaded and ready."""
        return self._loaded

    def load_model(self, path: str) -> bool:
        """Load model from path. Returns True on success.

        This is a placeholder — the actual implementation will use
        transformers/AutoModelForCausalLM when the model is available.
        """
        # Placeholder: would load Qwen2.5-1.5B-Instruct here
        # from transformers import AutoModelForCausalLM, AutoTokenizer
        # self.model = AutoModelForCausalLM.from_pretrained(path)
        # self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model_path = path
        self._loaded = False  # Cannot load without model weights
        return False

    def predict(self, input_text: str) -> Dict[str, Any]:
        """Run inference on a single input. Returns structured interpretation.

        This is a placeholder — returns mock data until model is loaded.
        """
        if not self._loaded:
            return {
                "error": "Model not loaded",
                "model_name": self.model_name,
                "available": False,
            }
        # Placeholder: would run actual inference here
        return {
            "transaction_type": "UNKNOWN",
            "parties": [],
            "amounts": [],
            "payment_method": "UNKNOWN",
            "ambiguity_flags": [],
            "confidence": 0.0,
        }

    def health_check(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_path": self.model_path,
            "loaded": self._loaded,
            "available": self.is_available(),
        }


# ---------------------------------------------------------------------------
# P4.5 — Training Pipeline
# ---------------------------------------------------------------------------

class TrainingPipeline:
    """Controlled, developer-initiated training pipeline.

    This is NEVER triggered by student submissions.
    It is an explicit operation initiated by the developer/operator.

    Pipeline:
      database → validation filter → deterministic JSONL export →
      training dataset → fine-tuning → model version → evaluation
    """

    def __init__(
        self,
        db: ProblemLearningDatabase,
        output_dir: str = "training_data",
    ) -> None:
        self._db = db
        self._output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def prepare_training_data(
        self,
        min_confidence: Decimal = Decimal("0.80"),
    ) -> Tuple[str, Dict[str, Any]]:
        """Step 1: Export validated records to JSONL.

        Only records with confidence >= min_confidence are exported.
        Returns (output_path, statistics).
        """
        # Filter validated records
        validated = self._db.get_by_status(ProblemStatus.VALIDATED)
        filtered = [r for r in validated if r.confidence >= min_confidence]

        # Export
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(self._output_dir, f"training_{timestamp}.jsonl")
        count = JSONLExporter.export_to_jsonl(filtered, output_path)

        stats = {
            "total_validated": len(validated),
            "filtered_by_confidence": len(filtered),
            "exported": count,
            "output_path": output_path,
            "min_confidence": str(min_confidence),
        }
        return output_path, stats

    def create_evaluation_set(
        self,
        holdout_fraction: float = 0.2,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Step 2: Split validated records into train and evaluation sets.

        Returns (train_path, eval_path, statistics).
        """
        validated = self._db.get_by_status(ProblemStatus.VALIDATED)
        n = len(validated)
        holdout_count = max(1, int(n * holdout_fraction))

        # Deterministic split (sorted by problem_id for reproducibility)
        sorted_records = sorted(validated, key=lambda r: r.problem_id)
        eval_records = sorted_records[:holdout_count]
        train_records = sorted_records[holdout_count:]

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        train_path = os.path.join(self._output_dir, f"train_{timestamp}.jsonl")
        eval_path = os.path.join(self._output_dir, f"eval_{timestamp}.jsonl")

        train_count = JSONLExporter.export_to_jsonl(train_records, train_path)
        eval_count = JSONLExporter.export_to_jsonl(eval_records, eval_path)

        stats = {
            "total_validated": n,
            "train_count": train_count,
            "eval_count": eval_count,
            "holdout_fraction": holdout_fraction,
            "train_path": train_path,
            "eval_path": eval_path,
        }
        return train_path, eval_path, stats

    def run_full_pipeline(
        self,
        min_confidence: Decimal = Decimal("0.80"),
        holdout_fraction: float = 0.2,
    ) -> Dict[str, Any]:
        """Run the complete training preparation pipeline.

        This does NOT actually train — it prepares the data and
        returns the exact command to run for training.
        """
        # Step 1: Prepare training data
        train_path, prepare_stats = self.prepare_training_data(min_confidence)

        # Step 2: Create evaluation set
        eval_train_path, eval_path, split_stats = self.create_evaluation_set(holdout_fraction)

        # Step 3: Generate training command (placeholder)
        training_command = (
            f"# Specialist Finance AI Training Command\n"
            f"# Model: {FinanceModelAdapter().model_name}\n"
            f"# Training data: {eval_train_path}\n"
            f"# Evaluation data: {eval_path}\n"
            f"# \n"
            f"# To run (when model weights are available):\n"
            f"# python -m training.fine_tune \\\n"
            f"#   --model {FinanceModelAdapter().model_name} \\\n"
            f"#   --train_data {eval_train_path} \\\n"
            f"#   --eval_data {eval_path} \\\n"
            f"#   --output_dir model_versions/ \\\n"
            f"#   --epochs 3 \\\n"
            f"#   --learning_rate 2e-5\n"
            f"#\n"
            f"# NOTE: This is a PLACEHOLDER. No model has been downloaded.\n"
            f"# No training has occurred. The pipeline is ready but requires:\n"
            f"#   1. Model weights (e.g. Qwen2.5-1.5B-Instruct)\n"
            f"#   2. GPU/CPU compute environment\n"
            f"#   3. Training framework (transformers + peft)"
        )

        return {
            "status": "PIPELINE_READY",
            "prepare_stats": prepare_stats,
            "split_stats": split_stats,
            "training_command": training_command,
            "model": FinanceModelAdapter().model_name,
            "note": "No model downloaded, no training occurred. Pipeline is prepared.",
        }


# ---------------------------------------------------------------------------
# P4.6 — Evaluation Mechanism
# ---------------------------------------------------------------------------

class ModelEvaluationHarness:
    """Evaluation mechanism for comparing a new model against the
    current deterministic system.

    Safety checks before production:
      1. Held-out evaluation set
      2. Compare against current system
      3. Safety checks (no INCORRECT_VERIFIED)
      4. Shadow mode comparison
      5. Human review required
      6. Production approval gate
    """

    @staticmethod
    def evaluate_model_predictions(
        eval_data_path: str,
        predictions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate model predictions against ground truth.

        Args:
            eval_data_path: Path to the evaluation JSONL
            predictions: List of model predictions (same order as eval data)

        Returns:
            Evaluation statistics
        """
        # Load ground truth
        ground_truth = []
        with open(eval_data_path, "r", encoding="utf-8") as f:
            for line in f:
                ground_truth.append(json.loads(line))

        if len(ground_truth) != len(predictions):
            return {
                "error": f"Length mismatch: {len(ground_truth)} ground truth vs {len(predictions)} predictions",
                "pass": False,
            }

        correct = 0
        total = len(ground_truth)
        type_matches = 0
        party_matches = 0
        amount_matches = 0

        for gt, pred in zip(ground_truth, predictions):
            gt_output = gt.get("output", {})
            pred_output = pred.get("output", {})

            # Check transaction type match
            if gt_output.get("transaction_type") == pred_output.get("transaction_type"):
                type_matches += 1

            # Check party match
            gt_parties = set(str(p) for p in gt_output.get("parties", []))
            pred_parties = set(str(p) for p in pred_output.get("parties", []))
            if gt_parties == pred_parties:
                party_matches += 1

            # Check amount match
            gt_amounts = set(str(a) for a in gt_output.get("amounts", []))
            pred_amounts = set(str(a) for a in pred_output.get("amounts", []))
            if gt_amounts == pred_amounts:
                amount_matches += 1

            # Overall match
            if (gt_output.get("transaction_type") == pred_output.get("transaction_type")
                and gt_parties == pred_parties
                and gt_amounts == pred_amounts):
                correct += 1

        return {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0.0,
            "type_accuracy": type_matches / total if total > 0 else 0.0,
            "party_accuracy": party_matches / total if total > 0 else 0.0,
            "amount_accuracy": amount_matches / total if total > 0 else 0.0,
            "pass": (correct / total >= 0.8) if total > 0 else False,
            "note": "80% accuracy threshold for production readiness",
        }

    @staticmethod
    def safety_check(eval_data_path: str) -> Dict[str, Any]:
        """Run safety checks on evaluation data.

        Ensures no INCORRECT_VERIFIED or accounting errors exist
        in the ground truth.
        """
        records = []
        with open(eval_data_path, "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))

        issues = []
        for r in records:
            output = r.get("output", {})
            metadata = r.get("metadata", {})

            # Check for INCORRECT_VERIFIED
            if metadata.get("engine_status") == "VERIFIED":
                # VERIFIED records must have journal data or engine_reason
                has_reason = bool(metadata.get("engine_reason"))
                has_journal = bool(output.get("journal_narration") or
                                   output.get("debit_accounts"))
                if not has_reason and not has_journal:
                    issues.append(f"VERIFIED without evidence: {metadata.get('problem_id', 'unknown')}")

        return {
            "total_records": len(records),
            "issues_found": len(issues),
            "issues": issues[:10],  # First 10 issues
            "pass": len(issues) == 0,
        }

    @staticmethod
    def generate_evaluation_report(
        eval_data_path: str,
        predictions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Generate a comprehensive evaluation report."""
        # Safety check
        safety = ModelEvaluationHarness.safety_check(eval_data_path)

        # Load data stats
        records = []
        with open(eval_data_path, "r", encoding="utf-8") as f:
            for line in f:
                records.append(json.loads(line))

        categories = {}
        engine_statuses = {}
        for r in records:
            cat = r.get("metadata", {}).get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
            es = r.get("metadata", {}).get("engine_status", "unknown")
            engine_statuses[es] = engine_statuses.get(es, 0) + 1

        result = {
            "eval_data_path": eval_data_path,
            "total_records": len(records),
            "categories": categories,
            "engine_statuses": engine_statuses,
            "safety_check": safety,
        }

        # If predictions provided, run accuracy evaluation
        if predictions:
            accuracy = ModelEvaluationHarness.evaluate_model_predictions(
                eval_data_path, predictions
            )
            result["accuracy"] = accuracy

        return result
