"""
Intelligence Package

Components for the investment memo generation pipeline:
  - DataAgent          : Fetches live Module 4 company intelligence
  - RetrievalAgent     : Retrieves cached/stored data from PostgreSQL
  - EvidenceConsolidator : Merges all evidence sources for the AI prompt
  - MemoGenerator      : Generates professional investment memo via AI

Pipeline:
  User Documents
         ↓
  Extraction & Summarization (ingestion/)
         ↓
  Module 3 Financial Intelligence
         ↓
  DataAgent (Module 4 live data)
         ↓
  RetrievalAgent (DB/Cache)
         ↓
  EvidenceConsolidator
         ↓
  MemoGenerator (LLM)
         ↓
  Professional Investment Memo
"""

from backend.intelligence.data_agent import DataAgent
from backend.intelligence.retrieval_agent import RetrievalAgent
from backend.intelligence.evidence_consolidator import EvidenceConsolidator
from backend.intelligence.memo_generator import MemoGenerator

__all__ = [
    "DataAgent",
    "RetrievalAgent",
    "EvidenceConsolidator",
    "MemoGenerator",
]
