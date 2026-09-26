"""Document input handling for the Platrixa developer API.

This module resolves a developer-submitted upload into bytes + a filename
and enforces the document-input contract. It performs NO document
understanding and NO financial reasoning — it only validates transport.

Accepted inputs (mutually exclusive):
  * a JSON body with ``raw_input``  -> plain text, unchanged behavior
  * a multipart upload with a PDF or image file

Rejected:
  * both ``raw_input`` and a file in the same request
  * neither
  * unsupported media types / extensions
  * files over the size cap

Every rejection raises :class:`DocumentInputError`, which is a TRANSPORT
error, never a financial verdict.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Tuple

#: Upper bound for a single uploaded document. Chosen to be generous for
#: invoices/statements while still bounded; a request over this is refused
#: BEFORE any parsing work happens.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MiB

ALLOWED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
                      ".bmp", ".webp", ".txt", ".text")

ALLOWED_CONTENT_TYPES = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/webp",
    "text/plain",
)


class DocumentInputError(ValueError):
    """Transport-level rejection of a submitted document.

    This is NOT a Kernel status and never becomes one. The caller maps it
    to a 4xx transport error; no financial verdict is implied.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_document_input(
    data: bytes,
    source_name: str,
    *,
    content_type: Optional[str] = None,
) -> None:
    """Validate raw document bytes + name against the same rules the
    multipart path enforces (extension allowlist, content-type allowlist,
    size limit). Raises DocumentInputError with the SAME codes — used by
    the async submission path (Phase 5E), which receives bytes rather
    than an upload object. Adds no new validation semantics."""
    filename = os.path.basename(source_name or "")
    if not filename:
        raise DocumentInputError("FILE_NAME_MISSING", "Document has no name.")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentInputError(
            "FILE_TYPE_UNSUPPORTED",
            f"Unsupported document type {extension or '(none)'}. "
            f"Accepted: {', '.join(ALLOWED_EXTENSIONS)}",
        )
    ct = (content_type or "").lower()
    if ct and not ct.startswith(ALLOWED_CONTENT_TYPES):
        raise DocumentInputError(
            "CONTENT_TYPE_UNSUPPORTED",
            f"Unsupported content type {ct}.",
        )
    if not data:
        raise DocumentInputError("FILE_EMPTY", "Uploaded document is empty.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentInputError(
            "FILE_TOO_LARGE",
            f"Document exceeds the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB limit.",
        )


def resolve_document_input(
    raw_input: Optional[str],
    upload: Any,
) -> Tuple[bytes, str]:
    """Resolve the request into ``(bytes, source_name)``.

    Raises :class:`DocumentInputError` for every rejection case.
    """
    has_text = bool(raw_input and raw_input.strip())
    has_file = upload is not None

    if has_text and has_file:
        raise DocumentInputError(
            "INPUT_AMBIGUOUS",
            "Provide either raw_input text or a document file, not both.",
        )
    if not has_text and not has_file:
        raise DocumentInputError(
            "INPUT_MISSING",
            "Provide raw_input text or a document file (PDF/image).",
        )

    if has_text:
        # Plain text: byte-identical treatment to the existing text path.
        return raw_input.encode("utf-8"), "input.txt"

    filename = os.path.basename(getattr(upload, "filename", "") or "")
    if not filename:
        raise DocumentInputError("FILE_NAME_MISSING", "Uploaded file has no name.")

    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentInputError(
            "FILE_TYPE_UNSUPPORTED",
            f"Unsupported document type {extension or '(none)'}. "
            f"Accepted: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    content_type = (getattr(upload, "content_type", "") or "").lower()
    if content_type and not content_type.startswith(ALLOWED_CONTENT_TYPES):
        raise DocumentInputError(
            "CONTENT_TYPE_UNSUPPORTED",
            f"Unsupported content type {content_type}.",
        )

    data = upload.file.read() if hasattr(upload, "file") else upload.read()
    if not data:
        raise DocumentInputError("FILE_EMPTY", "Uploaded document is empty.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentInputError(
            "FILE_TOO_LARGE",
            f"Document exceeds the {MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB limit.",
        )

    return data, filename
