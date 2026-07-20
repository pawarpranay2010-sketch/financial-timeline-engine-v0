"""
Module 4 - Validation Engine

Purpose:
Validate raw provider data before it enters the database.

Pipeline

External Provider
        ↓
Validation
        ↓
Normalization
        ↓
Database

The validator NEVER modifies data.
It only checks whether data is safe to store.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List


class ValidationResult:
    """
    Result returned by the validator.
    """

    def __init__(self):

        self.valid = True

        self.errors: List[str] = []

        self.warnings: List[str] = []

    def add_error(self, message: str):

        self.valid = False

        self.errors.append(message)

    def add_warning(self, message: str):

        self.warnings.append(message)


class Validator:

    REQUIRED_COMPANY_FIELDS = [
        "company_id",
        "ticker",
        "company_name",
        "exchange"
    ]

    REQUIRED_FINANCIAL_FIELDS = [
        "financial_year",
        "metric_name",
        "metric_value"
    ]

    MAX_DATA_AGE_DAYS = 3650

    def validate_company(self, data: Dict[str, Any]) -> ValidationResult:

        result = ValidationResult()

        for field in self.REQUIRED_COMPANY_FIELDS:

            if field not in data:

                result.add_error(f"Missing required field: {field}")

            elif data[field] in [None, "", []]:

                result.add_error(f"Empty required field: {field}")

        return result

    def validate_financial(self, data: Dict[str, Any]) -> ValidationResult:

        result = ValidationResult()

        for field in self.REQUIRED_FINANCIAL_FIELDS:

            if field not in data:

                result.add_error(f"Missing required field: {field}")

        value = data.get("metric_value")

        if value is not None:

            if not isinstance(value, (int, float)):

                result.add_error("Metric value must be numeric")

        return result

    def validate_timestamp(self, timestamp: datetime) -> ValidationResult:

        result = ValidationResult()

        if timestamp.tzinfo is None:

            result.add_warning("Timestamp has no timezone")

        age = datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)

        if age.days > self.MAX_DATA_AGE_DAYS:

            result.add_warning("Timestamp appears extremely old")

        return result

    def validate_duplicate(
        self,
        existing_value,
        incoming_value
    ) -> ValidationResult:

        result = ValidationResult()

        if existing_value == incoming_value:

            result.add_warning("Duplicate record detected")

        return result

    def validate_numeric_consistency(
        self,
        metric_name: str,
        metric_value: float
    ) -> ValidationResult:

        result = ValidationResult()

        if metric_value < 0:

            if metric_name.lower() not in [
                "net_income",
                "pat",
                "profit_after_tax",
                "cash_flow"
            ]:

                result.add_warning(
                    f"Negative value detected for {metric_name}"
                )

        return result


validator = Validator()
