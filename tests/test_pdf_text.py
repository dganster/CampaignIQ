from types import SimpleNamespace

import pytest

from campaigniq.pdf_text import PdfTextExtractionError, extract_pdf_text


def test_extract_pdf_text_uses_poppler_layout(monkeypatch, tmp_path) -> None:
    source = tmp_path / "statement.pdf"
    destination = tmp_path / "statement.txt"
    source.write_bytes(b"%PDF-test")

    monkeypatch.setattr(
        "campaigniq.pdf_text.shutil.which",
        lambda name: "/usr/bin/pdftotext",
    )

    observed = {}

    def fake_run(args, *, check, capture_output, text):
        observed["args"] = args
        observed["check"] = check
        observed["capture_output"] = capture_output
        observed["text"] = text
        destination.write_text("Positions - Summary\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(
        "campaigniq.pdf_text.subprocess.run",
        fake_run,
    )

    result = extract_pdf_text(source, destination)

    assert result == destination
    assert observed["args"] == [
        "/usr/bin/pdftotext",
        "-layout",
        str(source),
        str(destination),
    ]
    assert observed["check"] is False
    assert observed["capture_output"] is True
    assert observed["text"] is True


def test_extract_pdf_text_fails_when_pdftotext_is_unavailable(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "statement.pdf"
    source.write_bytes(b"%PDF-test")

    monkeypatch.setattr(
        "campaigniq.pdf_text.shutil.which",
        lambda name: None,
    )

    with pytest.raises(
        PdfTextExtractionError,
        match="pdftotext.*not available",
    ):
        extract_pdf_text(source, tmp_path / "statement.txt")


def test_extract_pdf_text_fails_when_poppler_reports_error(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "statement.pdf"
    source.write_bytes(b"%PDF-test")

    monkeypatch.setattr(
        "campaigniq.pdf_text.shutil.which",
        lambda name: "/usr/bin/pdftotext",
    )
    monkeypatch.setattr(
        "campaigniq.pdf_text.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="damaged PDF",
        ),
    )

    with pytest.raises(
        PdfTextExtractionError,
        match="damaged PDF",
    ):
        extract_pdf_text(source, tmp_path / "statement.txt")


def test_extract_pdf_text_rejects_empty_extraction(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "statement.pdf"
    destination = tmp_path / "statement.txt"
    source.write_bytes(b"%PDF-test")

    monkeypatch.setattr(
        "campaigniq.pdf_text.shutil.which",
        lambda name: "/usr/bin/pdftotext",
    )

    def fake_run(*args, **kwargs):
        destination.write_text("   \n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(
        "campaigniq.pdf_text.subprocess.run",
        fake_run,
    )

    with pytest.raises(
        PdfTextExtractionError,
        match="no usable text",
    ):
        extract_pdf_text(source, destination)
