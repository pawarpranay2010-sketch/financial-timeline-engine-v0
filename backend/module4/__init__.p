"""
Financial Timeline Engine
Module 4

Investment Memo Generation Module

This package contains the complete investment memo generation
pipeline.

Components
----------
- MemoGenerator
- MemoSections
- RecommendationEngine
- ValuationEngine
- RiskEngine
- CatalystEngine
- OutputFormatter
- Module4Controller
"""

from .memo_generator import MemoGenerator
from .memo_sections import MemoSections
from .recommendation_engine import RecommendationEngine
from .valuation_engine import ValuationEngine
from .risk_engine import RiskEngine
from .catalyst_engine import CatalystEngine
from .output_formatter import OutputFormatter
from .module4_controller import Module4Controller

__version__ = "1.0.0"

__all__ = [
    "MemoGenerator",
    "MemoSections",
    "RecommendationEngine",
    "ValuationEngine",
    "RiskEngine",
    "CatalystEngine",
    "OutputFormatter",
    "Module4Controller",
]
