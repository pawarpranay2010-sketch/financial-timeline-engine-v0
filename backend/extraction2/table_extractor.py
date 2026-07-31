"""
Financial Timeline Engine
Extraction 2.0 - Table Extractor

Layout-aware financial table extraction.

A table such as:

    (in $ millions)         FY2025     FY2024
    Revenue                 573,000    512,000
    Net income               30,000     27,000

is preserved as a structured Table with:

    table_id, page, headers, column_periods, rows (label -> values),
    currency, scale, source_location

so each value stays associated with its metric row and fiscal-period
column. Tables are never flattened into a bare list of numbers.

Sources supported:
  - HTML tables (BeautifulSoup)
  - native table data (xlsx/csv/docx via ingestion.parser `table_data`)
  - layout-aware text tables (PDF/TXT line scanning)
  - table continuation across pages (repeated headers are merged)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Period / currency / scale token patterns
# ---------------------------------------------------------------------------

# FY2025, FY 2025, F2025, 2025, Q1 FY2025, Q2'25, FY25, "31-Mar-2025",
# "March 31, 2025", "2024-09-28", "Dec 31, 2024"
_FISCAL_PERIOD_RE = re.compile(
    r"\b(FY\s?20\d{2}|F20\d{2}|Q[1-4][\s-]?FY\s?20\d{2}|"
    r"Q[1-4][\s-]?20\d{2}|20\d{2}|FY\s?\d{2})\b",
    re.IGNORECASE,
)

_DATE_PERIOD_RE = re.compile(
    r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b"
    r"|\b\d{1,2}[-/](?:19|20)\d{2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)

_CURRENCY_SYMBOL_RE = re.compile(r"[$€£¥₹₽₩₺₫₱₴]")
_CURRENCY_CODE_RE = re.compile(
    r"\b(USD|EUR|GBP|JPY|INR|CNY|CAD|AUD|CHF|HKD|SGD|NZD|SEK|NOK|"
    r"DKK|MXN|BRL|ZAR|RUB|KRW|IDR|MYR|THB|PLN|TRY|AED|SAR|VND|PHP)\b",
    re.IGNORECASE,
)

_SCALE_RE = re.compile(
    r"\b(in\s+)?(millions?|billions?|crores?|lakhs?|thousands?|trillions?)\b",
    re.IGNORECASE,
)

_NUMBER_TOKEN_RE = re.compile(r"\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?")

# A line that looks like a table data row: has a label + at least 1 numeric
_MULTI_SPACE_RE = re.compile(r"\t+| {2,}")


@dataclass
class Table:
    """Structured financial table."""

    table_id: str = ""
    page: Optional[int] = None
    headers: List[str] = field(default_factory=list)
    column_periods: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    currency: str = ""
    scale: str = ""
    source_location: str = ""
    raw_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_id": self.table_id,
            "page": self.page,
            "headers": self.headers,
            "column_periods": self.column_periods,
            "rows": self.rows,
            "currency": self.currency,
            "scale": self.scale,
            "source_location": self.source_location,
        }


class TableExtractor:
    """Extract structured financial tables from various sources."""

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------

    def extract_from_parsed(self, parsed: dict) -> List[Table]:
        """
        Extract tables from a parsed document dict produced by
        ingestion.parser.

        Priority:
          1. Native `table_data` (xlsx/csv/docx/html) - already structured
          2. Layout-aware text scanning (pdf/txt)
        """
        table_data = parsed.get("table_data")
        if table_data:
            return [self._from_native_dict(t, i) for i, t in enumerate(table_data)]

        text = parsed.get("text", "")
        doc_type = parsed.get("type", "")
        if doc_type == "pdf":
            return self.extract_text_tables(text, source="pdf")
        return self.extract_text_tables(text, source="text")

    def extract_html_tables(self, html: str) -> List[Table]:
        """Extract all <table> elements from an HTML document."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return self.extract_text_tables(
                re.sub(r"<[^>]+>", " ", html),
                source="html",
            )

        soup = BeautifulSoup(html, "lxml")
        tables: List[Table] = []
        for idx, table_el in enumerate(soup.find_all("table")):
            rows: List[List[str]] = []
            for tr in table_el.find_all("tr"):
                cells = [
                    " ".join(td.get_text(" ", strip=True).split())
                    for td in tr.find_all(["td", "th"])
                ]
                if cells:
                    rows.append(cells)

            if not rows:
                continue

            table = self._build_from_rows(
                rows,
                table_id=f"html_table_{idx + 1}",
                source_location=f"html table #{idx + 1}",
            )
            if table.rows or table.headers:
                tables.append(table)

        return tables

    def extract_text_tables(
        self,
        text: str,
        source: str = "text",
        page: Optional[int] = None,
    ) -> List[Table]:
        """
        Layout-aware table detection from plain text (PDF extraction or
        TXT). Lines are columnized on tabs / runs of whitespace; header
        rows containing fiscal periods define the columns; data rows map
        each value to its period column.

        Handles table continuation: a repeated header row starts a new
        block, and blocks sharing the same header signature are merged.
        """
        if not text:
            return []

        lines = [ln.rstrip() for ln in text.splitlines()]
        blocks: List[List[str]] = []
        current: List[str] = []
        header_sig = None

        for ln in lines:
            if not ln.strip():
                if current:
                    blocks.append(current)
                    current = []
                continue

            # Repeated header => start a new block with the same signature
            sig = self._header_signature(ln)
            if sig is not None:
                if header_sig is None:
                    header_sig = sig
                if current:
                    blocks.append(current)
                current = [ln]
                continue

            if current:
                current.append(ln)
            elif self._looks_like_data_line(ln):
                # Data row before any header seen - still start a block
                current = [ln]

        if current:
            blocks.append(current)

        tables: List[Table] = []
        for i, block in enumerate(blocks):
            table = self._build_from_lines(block, table_id=f"text_table_{i + 1}")
            if table.rows or table.headers:
                table.source_location = f"{source} text table #{i + 1}"
                tables.append(table)

        # Merge continuations: same header signature => same logical table
        tables = self._merge_continuations(tables)
        return tables

    # ------------------------------------------------------------------
    # Native structured table data (from parser table_data)
    # ------------------------------------------------------------------

    @staticmethod
    def _from_native_dict(data: dict, idx: int) -> Table:
        rows_raw = data.get("rows", [])
        headers = [str(h) for h in data.get("headers", [])]

        # Parse periods from headers
        column_periods = [TableExtractor._detect_period(h) or "" for h in headers]

        rows: List[Dict[str, Any]] = []
        for r in rows_raw:
            if isinstance(r, dict):
                rows.append(dict(r))
            else:
                cells = [str(c) for c in r]
                if cells:
                    label = cells[0]
                    rows.append({"label": label, "cells": cells[1:]})

        # Prefer explicit per-table metadata (Fix #2 scale propagation);
        # fall back to detecting from headers when not annotated.
        currency = str(data.get("currency") or "") or TableExtractor._detect_currency(" ".join(headers))
        scale = str(data.get("scale") or "") or TableExtractor._detect_scale(" ".join(headers))

        return Table(
            table_id=data.get("table_id", f"native_table_{idx + 1}"),
            headers=headers,
            column_periods=column_periods,
            rows=rows,
            currency=currency,
            scale=scale,
            source_location=data.get("source_location", f"native table #{idx + 1}"),
        )

    # ------------------------------------------------------------------
    # Row-array based builder (HTML)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_from_rows(
        rows: List[List[str]],
        table_id: str,
        source_location: str = "",
    ) -> Table:
        headers = rows[0] if rows else []
        column_periods = [
            TableExtractor._detect_period(h) or TableExtractor._detect_period_from_date(h)
            for h in headers
        ]
        currency = TableExtractor._detect_currency(" ".join(headers))
        scale = TableExtractor._detect_scale(" ".join(headers))

        # If the first row doesn't look like headers, treat it as data
        if not any(column_periods) and not TableExtractor._looks_like_header_row(headers):
            column_periods = []

        data_rows: List[Dict[str, Any]] = []
        start = 1 if (headers and (any(column_periods) or TableExtractor._looks_like_header_row(headers))) else 0
        for raw_row in rows[start:]:
            if not raw_row:
                continue
            cells = raw_row[1:]
            data_rows.append({
                "label": raw_row[0].strip(),
                "cells": cells,
            })

        return Table(
            table_id=table_id,
            headers=headers,
            column_periods=column_periods,
            rows=data_rows,
            currency=currency,
            scale=scale,
            source_location=source_location,
        )

    # ------------------------------------------------------------------
    # Line-based builder (text/pdf)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_from_lines(lines: List[str], table_id: str) -> Table:
        headers: List[str] = []
        column_periods: List[str] = []
        currency = ""
        scale = ""

        # Find the header row (line containing period tokens)
        header_idx = None
        for i, ln in enumerate(lines[:6]):
            tokens = TableExtractor._split_columns(ln)
            periods = [
                TableExtractor._detect_period(t) or TableExtractor._detect_period_from_date(t)
                for t in tokens
            ]
            if any(periods):
                headers = tokens
                column_periods = periods
                header_idx = i
                joined = " ".join(tokens)
                currency = TableExtractor._detect_currency(joined)
                scale = TableExtractor._detect_scale(joined)
                break

        rows: List[Dict[str, Any]] = []
        data_lines = lines[header_idx + 1:] if header_idx is not None else lines

        for ln in data_lines:
            tokens = TableExtractor._split_columns(ln)
            if not tokens:
                continue
            label = tokens[0]
            cells = tokens[1:]

            # Merge wrapped label continuation lines (indented continuation)
            if label and not any(TableExtractor._is_number(t) for t in tokens[1:]) \
               and rows and TableExtractor._is_indented(ln):
                rows[-1]["cells"].extend(cells)
                continue

            if not TableExtractor._looks_like_data_line(ln):
                # Non-data line (e.g. footnote, title) - skip
                if not TableExtractor._is_number(label):
                    continue

            rows.append({"label": label, "cells": cells})

        return Table(
            table_id=table_id,
            headers=headers,
            column_periods=column_periods,
            rows=rows,
            currency=currency,
            scale=scale,
        )

    @staticmethod
    def _merge_continuations(tables: List[Table]) -> List[Table]:
        """Merge tables whose header signatures match (continuation across pages)."""
        if not tables:
            return tables

        merged: List[Table] = []
        for table in tables:
            sig = "|".join(table.headers)
            if not sig:
                merged.append(table)
                continue

            target = None
            for m in merged:
                if "|".join(m.headers) == sig:
                    target = m
                    break
            if target is None:
                merged.append(table)
            else:
                # Extend: headers duplicated in continuation get merged
                target.rows.extend(table.rows)
                target.scale = target.scale or table.scale
                target.currency = target.currency or table.currency
                target.source_location = (
                    f"{target.source_location}; {table.source_location}"
                )
        return merged

    # ------------------------------------------------------------------
    # Token detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_columns(line: str) -> List[str]:
        line = line.replace("\t", "  ")
        parts = _MULTI_SPACE_RE.split(line.strip())
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _detect_period(token: str) -> str:
        m = _FISCAL_PERIOD_RE.search(token)
        if not m:
            return ""
        raw = m.group(0).upper()
        # Normalize "FY25" -> "FY2025"
        if re.fullmatch(r"FY\d{2}", raw):
            return f"FY20{raw[2:]}"
        # Normalize "Q1 FY2025" -> "Q1FY2025"
        qm = re.fullmatch(r"Q([1-4])[\s-]?(?:FY\s?)?20(\d{2})", raw)
        if qm:
            return f"Q{qm.group(1)}FY20{qm.group(2)}"
        # "Q1 2025"
        qm2 = re.fullmatch(r"Q([1-4])[\s-]?20(\d{2})", raw)
        if qm2:
            return f"Q{qm2.group(1)}FY20{qm2.group(2)}"
        # "FY2025" or "2025"
        if raw.startswith("FY"):
            return raw
        return f"FY{raw}"

    @staticmethod
    def _detect_period_from_date(token: str) -> str:
        m = _DATE_PERIOD_RE.search(token)
        if not m:
            return ""
        date_str = m.group(0)
        year_m = re.search(r"(19|20)\d{2}", date_str)
        if not year_m:
            return ""
        return f"FY{year_m.group(0)}"

    @staticmethod
    def _detect_currency(text: str) -> str:
        if _CURRENCY_SYMBOL_RE.search(text):
            if "₹" in text:
                return "INR"
            if "€" in text:
                return "EUR"
            if "£" in text:
                return "GBP"
            if "¥" in text:
                return "JPY"
            return "USD"
        m = _CURRENCY_CODE_RE.search(text)
        if m:
            return m.group(1).upper()
        return ""

    @staticmethod
    def _detect_scale(text: str) -> str:
        m = _SCALE_RE.search(text)
        if not m:
            return ""
        word = m.group(0).lower()
        for scale, forms in (
            ("trillions", ("trillion", "trillions")),
            ("billions", ("billion", "billions")),
            ("millions", ("million", "millions")),
            ("crores", ("crore", "crores")),
            ("lakhs", ("lakh", "lakhs")),
            ("thousands", ("thousand", "thousands")),
        ):
            if word in forms:
                return scale
        return word

    @staticmethod
    def _is_number(token: str) -> bool:
        return bool(_NUMBER_TOKEN_RE.fullmatch(token.strip()))

    @staticmethod
    def _header_signature(line: str) -> Optional[str]:
        """If this line is a header (contains period tokens), return a signature."""
        tokens = TableExtractor._split_columns(line)
        periods = [
            TableExtractor._detect_period(t) or TableExtractor._detect_period_from_date(t)
            for t in tokens
        ]
        if any(periods):
            return "|".join(tokens)
        return None

    @staticmethod
    def _looks_like_header_row(cells: List[str]) -> bool:
        return len(cells) >= 2 and any(
            TableExtractor._detect_period(c) or TableExtractor._detect_period_from_date(c)
            for c in cells
        )

    @staticmethod
    def _looks_like_data_line(line: str) -> bool:
        tokens = TableExtractor._split_columns(line)
        if len(tokens) < 2:
            return False
        return any(TableExtractor._is_number(t) for t in tokens[1:])

    @staticmethod
    def _is_indented(line: str) -> bool:
        stripped = line.lstrip()
        return len(line) - len(stripped) >= 2
