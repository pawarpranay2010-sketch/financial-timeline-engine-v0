"""
Financial Timeline Engine
Module 2 - Universal Document Parser

Supported:
- PDF
- DOCX
- XLSX
- CSV
- TXT

High fidelity extraction while preserving ordering,
tables (where possible), page sequence and metadata.
"""

from __future__ import annotations

import io
import os
import pandas as pd
from docx import Document
from pypdf import PdfReader


# ============================================================
# Supported Extensions
# ============================================================

SUPPORTED_TYPES = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".txt",
}


# ============================================================
# File Type Detection
# ============================================================

def detect_file_type(filename: str) -> str:
    ext = os.path.splitext(filename.lower())[1]

    if ext not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported file type: {ext}")

    return ext


# ============================================================
# TXT
# ============================================================

def parse_txt(uploaded_file) -> dict:

    uploaded_file.seek(0)

    text = uploaded_file.read().decode(
        "utf-8",
        errors="ignore"
    )

    return {
        "type": "txt",
        "pages": 1,
        "tables": 0,
        "text": text
    }


# ============================================================
# CSV
# ============================================================

def parse_csv(uploaded_file) -> dict:

    uploaded_file.seek(0)

    df = pd.read_csv(uploaded_file)

    return {
        "type": "csv",
        "pages": 1,
        "tables": 1,
        "text": df.to_string(index=False)
    }


# ============================================================
# Excel
# ============================================================

def parse_excel(uploaded_file) -> dict:

    uploaded_file.seek(0)

    sheets = pd.read_excel(
        uploaded_file,
        sheet_name=None
    )

    output = []

    table_count = 0

    for name, df in sheets.items():

        table_count += 1

        output.append(
            f"\n========== SHEET : {name} ==========\n"
        )

        output.append(
            df.to_string(index=False)
        )

    return {
        "type": "xlsx",
        "pages": len(sheets),
        "tables": table_count,
        "text": "\n".join(output)
    }


# ============================================================
# DOCX
# ============================================================

def parse_docx(uploaded_file) -> dict:

    uploaded_file.seek(0)

    document = Document(uploaded_file)

    paragraphs = []

    for para in document.paragraphs:

        txt = para.text.strip()

        if txt:

            paragraphs.append(txt)

    return {
        "type": "docx",
        "pages": 1,
        "tables": len(document.tables),
        "text": "\n".join(paragraphs)
    }


# ============================================================
# PDF
# ============================================================

def parse_pdf(uploaded_file) -> dict:

    uploaded_file.seek(0)

    reader = PdfReader(uploaded_file)

    pages = []

    page_count = 0

    for i, page in enumerate(reader.pages):

        page_count += 1

        text = page.extract_text()

        if text:

            pages.append(
                f"\n========== PAGE {i+1} ==========\n"
            )

            pages.append(text)

    return {
        "type": "pdf",
        "pages": page_count,
        "tables": 0,
        "text": "\n".join(pages)
    }


# ============================================================
# Universal Parser
# ============================================================

def parse_document(uploaded_file):

    file_type = detect_file_type(uploaded_file.name)

    if file_type == ".pdf":
        return parse_pdf(uploaded_file)

    if file_type == ".docx":
        return parse_docx(uploaded_file)

    if file_type == ".xlsx":
        return parse_excel(uploaded_file)

    if file_type == ".csv":
        return parse_csv(uploaded_file)

    if file_type == ".txt":
        return parse_txt(uploaded_file)

    raise RuntimeError("Parser not found")
