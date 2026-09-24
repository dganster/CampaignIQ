"""PDF-to-text extraction boundary for broker source documents."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class PdfTextExtractionError(RuntimeError):
    """Raised when a PDF source document cannot be extracted safely."""


def extract_pdf_text(
    source: str | Path,
    destination: str | Path,
) -> Path:
    """Extract a PDF to layout-preserving UTF-8 text using Poppler."""
    source_path = Path(source)
    destination_path = Path(destination)

    executable = shutil.which("pdftotext")
    if executable is None:
        raise PdfTextExtractionError(
            "PDF import requires the 'pdftotext' utility, but it is not available."
        )

    destination_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        completed = subprocess.run(
            [
                executable,
                "-layout",
                str(source_path),
                str(destination_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise PdfTextExtractionError(
            f"Could not run pdftotext for {source_path.name}."
        ) from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip()
        message = f"Could not extract text from PDF {source_path.name}."
        if detail:
            message = f"{message} pdftotext reported: {detail}"
        raise PdfTextExtractionError(message)

    if not destination_path.exists():
        raise PdfTextExtractionError(
            f"PDF extraction produced no text file for {source_path.name}."
        )

    try:
        extracted = destination_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PdfTextExtractionError(
            f"Could not read extracted text for {source_path.name}."
        ) from exc

    if not extracted.strip():
        raise PdfTextExtractionError(
            f"PDF extraction produced no usable text for {source_path.name}."
        )

    return destination_path
