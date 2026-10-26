"""Pure monthly upload staging; no dashboard import, runtime or UI startup.

The extraction logic matches the existing dashboard helper. Files are written
only to the caller-owned temporary directory; this module never publishes.
"""
from pathlib import Path
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.pdf_text import extract_pdf_text


def stage_monthly_uploads(*, upload_dir, uploads):
    supplied = {}

    tos_upload = uploads.get(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY)
    if tos_upload is not None:
        suffix = Path(tos_upload.name).suffix
        tos_path = upload_dir / f"thinkorswim_trade_history{suffix}"
        tos_path.write_bytes(tos_upload.getvalue())
        supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY] = tos_path

    schwab_upload = uploads.get("schwab_brokerage_statement")
    if schwab_upload is not None:
        suffix = Path(schwab_upload.name).suffix
        schwab_path = upload_dir / f"schwab_brokerage_statement{suffix}"
        schwab_path.write_bytes(schwab_upload.getvalue())

        schwab_source_path = schwab_path
        if suffix.lower() == ".pdf":
            schwab_source_path = extract_pdf_text(
                schwab_path,
                upload_dir / "schwab_brokerage_statement.txt",
            )

        # The Brokerage Statement supplies closing positions and any
        # assignment/exercise evidence needed by the existing Schwab readers.
        supplied[
            MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT
        ] = schwab_source_path
        supplied[
            MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE
        ] = schwab_source_path

    opening_upload = uploads.get(
        MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
    )
    if opening_upload is not None:
        suffix = Path(opening_upload.name).suffix
        opening_path = upload_dir / f"schwab_opening_position_snapshot{suffix}"
        opening_path.write_bytes(opening_upload.getvalue())

        opening_source_path = opening_path
        if suffix.lower() == ".pdf":
            opening_source_path = extract_pdf_text(
                opening_path,
                upload_dir / "schwab_opening_position_snapshot.txt",
            )

        supplied[
            MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
        ] = opening_source_path

    realized_upload = uploads.get(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS)
    if realized_upload is not None:
        suffix = Path(realized_upload.name).suffix
        realized_path = upload_dir / f"schwab_realized_gain_loss{suffix}"
        realized_path.write_bytes(realized_upload.getvalue())

        realized_source_path = realized_path
        if suffix.lower() == ".pdf":
            realized_source_path = extract_pdf_text(
                realized_path,
                upload_dir / "schwab_realized_gain_loss.txt",
            )

        supplied[
            MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS
        ] = realized_source_path

    forex_upload = uploads.get(MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT)
    if forex_upload is not None:
        suffix = Path(forex_upload.name).suffix
        forex_path = upload_dir / f"schwab_forex_transaction_report{suffix}"
        forex_path.write_bytes(forex_upload.getvalue())
        supplied[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT] = forex_path

    next_upload = uploads.get(MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE)
    if next_upload is not None:
        suffix = Path(next_upload.name).suffix
        next_path = upload_dir / f"next_month_assignment_evidence{suffix}"
        next_path.write_bytes(next_upload.getvalue())
        if suffix.lower() == ".pdf":
            next_path = extract_pdf_text(next_path, upload_dir / "next_month_assignment_evidence.txt")
        supplied[MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE] = next_path

    forex_monthly = uploads.get(MonthlyInputRole.SCHWAB_FOREX_MONTHLY_STATEMENT)
    if forex_monthly is not None:
        suffix = Path(forex_monthly.name).suffix.lower()
        monthly_path = upload_dir / f"schwab_forex_monthly_statement{suffix}"
        monthly_path.write_bytes(forex_monthly.getvalue())
        if suffix == ".pdf":
            monthly_path = extract_pdf_text(monthly_path, upload_dir / "schwab_forex_monthly_statement.txt")
        supplied[MonthlyInputRole.SCHWAB_FOREX_MONTHLY_STATEMENT] = monthly_path

    crypto_upload = uploads.get(MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT)
    if crypto_upload is not None:
        suffix = Path(crypto_upload.name).suffix
        crypto_path = upload_dir / f"schwab_crypto_statement{suffix}"
        crypto_path.write_bytes(crypto_upload.getvalue())
        if suffix.lower() == ".pdf":
            crypto_path = extract_pdf_text(crypto_path, upload_dir / "schwab_crypto_statement.txt")
        supplied[MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT] = crypto_path

    return supplied
