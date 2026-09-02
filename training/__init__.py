"""
Platrixa FYJC Accounting AI — Training Pipeline

Provider-independent pipeline for generating, validating, splitting,
formatting, training, and evaluating a specialist accounting AI model.

Architecture:
    Candidate cases (JSONL/PostgreSQL)
        → Generation (template + kernel verification)
        → Validation (schema + quality + dedup)
        → Splitting (train/val/test)
        → Formatting (Alpaca JSONL for Qwen2.5)
        → Training (optional GPU, LoRA/QLoRA)
        → Evaluation (field-level accuracy)

Safety: The deterministic accounting kernel is the source of truth.
This pipeline never invents accounting labels.
"""

__version__ = "0.1.0"
