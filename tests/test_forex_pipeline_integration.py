from datetime import date
from decimal import Decimal
from pathlib import Path

from campaigniq.domain.lot_book import LotBook
from campaigniq.import_pipeline import PeriodImportPipeline


def write_empty_tos_statement(path: Path) -> Path:
    path.write_text(
        "Account Statement test\n"
        "\n"
        "Cash Balance\n"
        "DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE\n"
        "\n"
        "Account Trade History\n"
        "Notes,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type\n"
        "\n"
        "Forex Statements\n"
        ",Date,Time,Type,Ref #,Description,Commissions & Fees,Amount,Amount(USD),Balance\n"
        "\n",
        encoding="utf-8",
    )
    return path


def test_pipeline_accepts_forex_report_and_exposes_parsed_economics(tmp_path):
    report = tmp_path / "forex.csv"
    report.write_text(
        "Transaction Report since Jul 31, 2026 through Aug 31, 2026\n"
        "MTD Settled PL,$3.00\n"
        "MTD Fee,$0.00\n",
        encoding="utf-8",
    )
    tos = write_empty_tos_statement(tmp_path / "tos.csv")

    result = PeriodImportPipeline().run(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        thinkorswim_trade_history=tos,
        carried_opening_lot_book=LotBook(),
        forex_transaction_report=report,
    )

    assert result.forex_transaction_report is not None
    assert result.forex_transaction_report.mtd_settled_pl_usd == Decimal("3.00")
    assert result.forex_settlement_attributions == ()


def test_pipeline_without_forex_report_remains_backward_compatible(tmp_path):
    tos = write_empty_tos_statement(tmp_path / "tos.csv")

    result = PeriodImportPipeline().run(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        thinkorswim_trade_history=tos,
        carried_opening_lot_book=LotBook(),
    )

    assert result.forex_transaction_report is None
    assert result.forex_settlement_attributions == ()
