from datetime import date, datetime
from decimal import Decimal as D

from campaigniq.domain.position_event import PositionChange, PositionEvent
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_event_applier import PositionEventApplier
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.realized_lot_attributor import RealizedLotAttributor
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
from campaigniq.importers.thinkorswim.translator import to_trade
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

DATA_FILE = "tests/data/thinkorswim/Account Trading History 2026.csv"


def opt(symbol: str, expiration: str, strike: str, kind: str) -> OptionContract:
    return OptionContract(
        symbol,
        date.fromisoformat(expiration),
        D(strike),
        OptionType.CALL if kind == "C" else OptionType.PUT,
    )


def seed(book: LotBook, symbol: str, quantity: str, basis: str) -> None:
    book.seed(
        Lot(
            lot_id=f"DEC-{symbol}",
            instrument=Instrument(symbol),
            quantity=D(quantity),
            opened_at=datetime(2025, 12, 31, 16),
            basis_total=D(basis),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )


def seed_option(book: LotBook, symbol: str, expiration: str, strike: str,
                kind: str, quantity: str, basis: str) -> None:
    contract = opt(symbol, expiration, strike, kind)
    book.seed(
        Lot(
            lot_id=f"DEC-{symbol}-{expiration}-{strike}-{kind}",
            instrument=contract,
            quantity=D(quantity),
            opened_at=datetime(2025, 12, 31, 16),
            basis_total=D(basis),
            basis_source="DECEMBER_SNAPSHOT",
        )
    )


def record(date_value, instrument, quantity, price, proceeds, basis, gain_loss):
    return RealizedGainLossRecord(
        closed_date=date.fromisoformat(date_value),
        instrument=instrument,
        quantity=D(quantity),
        closing_price=D(price),
        proceeds=D(proceeds),
        cost_basis=D(basis),
        gain_loss=D(gain_loss),
        basis_method="FIFO",
        term="SHORT TERM",
    )


def january_records():
    return [
        record("2026-01-06", opt("COIN", "2026-01-16", "220", "C"), "5", "28.44", "14221.69", "18113.30", "-3891.61"),
        record("2026-01-06", opt("COST", "2026-01-16", "840", "C"), "5", "44.67", "22336.69", "20228.30", "2108.39"),
        record("2026-01-06", opt("COST", "2026-02-20", "945", "C"), "1", "5.30", "530.34", "531.66", "-1.32"),
        record("2026-01-06", opt("HD", "2026-01-16", "335", "C"), "5", "23.99", "11996.69", "6138.30", "5858.39"),
        record("2026-01-06", opt("CRWD", "2026-01-16", "500", "P"), "5", "24.88", "12441.69", "21353.30", "-8911.61"),
        record("2026-01-06", opt("META", "2026-01-16", "690", "P"), "5", "60.66", "30331.69", "15658.30", "14673.39"),
        record("2026-01-06", opt("MSFT", "2026-01-16", "495", "P"), "5", "18.06", "9031.69", "10288.30", "-1256.61"),
        record("2026-01-09", Instrument("DXCM"), "500", "69.96", "34981.59", "34835.00", "146.59"),
        record("2026-01-09", Instrument("EL"), "500", "110.61", "55305.58", "54746.00", "559.58"),
        record("2026-01-12", opt("AMZN", "2026-01-16", "240", "P"), "5", "14.14", "7071.69", "578.30", "6493.39"),
        record("2026-01-13", opt("NFLX", "2026-02-20", "86", "C"), "50", "10.46", "52316.81", "37033.05", "15283.76"),
        record("2026-01-16", opt("LMT", "2026-01-16", "480", "P"), "4", "31.39", "12557.35", "20.04", "12537.31"),
        record("2026-01-16", opt("LMT", "2026-01-16", "480", "P"), "1", "31.39", "3139.34", "2.01", "3137.33"),
        record("2026-01-16", opt("NVDA", "2026-01-16", "184", "P"), "5", "10.78", "5391.69", "113.30", "5278.39"),
        record("2026-01-20", opt("LLY", "2026-01-16", "1040", "P"), "5", "61.76", "30881.69", "113.30", "30768.39"),
        record("2026-01-22", opt("COIN", "2026-02-20", "220", "C"), "5", "42.88", "21441.69", "8698.30", "12743.39"),
        record("2026-01-22", opt("NFLX", "2026-02-20", "82", "C"), "50", "10.06", "50316.79", "19883.05", "30433.74"),
        record("2026-01-22", opt("ORCL", "2026-02-20", "175", "C"), "5", "27.02", "13511.69", "4432.30", "9079.39"),
        record("2026-01-22", opt("TMUS", "2026-02-20", "175", "C"), "5", "26.88", "13441.69", "5768.30", "7673.39"),
        record("2026-01-23", Instrument("COIN"), "500", "217.44", "108719.90", "128703.31", "-19983.41"),
        record("2026-01-23", Instrument("COST"), "500", "983.94", "491969.90", "452378.31", "39591.59"),
        record("2026-01-23", Instrument("VRT"), "500", "181.77", "90884.90", "93810.00", "-2925.10"),
        record("2026-01-26", opt("COIN", "2026-02-20", "200", "C"), "5", "29.88", "14941.68", "12153.30", "2788.38"),
        record("2026-01-26", opt("COST", "2026-02-20", "840", "C"), "5", "50.09", "25046.69", "73243.30", "-48196.61"),
        record("2026-01-26", opt("VRT", "2026-02-20", "145", "C"), "5", "28.71", "14356.69", "19578.30", "-5221.61"),
        record("2026-01-28", opt("UNH", "2026-02-20", "300", "C"), "5", "41.92", "20961.68", "2928.30", "18033.38"),
        record("2026-01-30", Instrument("LIN"), "500", "452.89", "226444.95", "213921.31", "12523.64"),
        record("2026-02-02", opt("LIN", "2026-02-20", "365", "C"), "5", "64.09", "32046.69", "44728.30", "-12681.61"),
    ]


def test_actual_january_realized_report_all_28_records_reconcile():
    book = LotBook()
    for row in [
        ("COIN", "500", "128703.31"), ("COST", "500", "452378.31"),
        ("HD", "500", "175299.07"), ("LIN", "500", "213921.31"),
        ("NFLX", "5000", "534533.19"), ("ORCL", "500", "101558.31"),
        ("TMUS", "500", "105494.31"), ("VRT", "500", "93810.00"),
    ]:
        seed(book, *row)
    for row in [
        ("COIN", "2026-01-16", "220", "C", "-5", "-14221.69"),
        ("COST", "2026-01-16", "840", "C", "-5", "-22336.69"),
        ("HD", "2026-01-16", "335", "C", "-5", "-11996.69"),
        ("LIN", "2026-02-20", "365", "C", "-5", "-32046.69"),
        ("NFLX", "2026-02-20", "86", "C", "-50", "-52316.81"),
        ("ORCL", "2026-02-20", "175", "C", "-5", "-13511.69"),
        ("TMUS", "2026-02-20", "175", "C", "-5", "-13441.69"),
        ("VRT", "2026-02-20", "145", "C", "-5", "-14356.69"),
        ("AMZN", "2026-01-16", "240", "P", "-5", "-7071.69"),
        ("CRWD", "2026-01-16", "500", "P", "-5", "-12441.69"),
        ("LLY", "2026-01-16", "1040", "P", "-5", "-30881.69"),
        ("LMT", "2026-01-16", "480", "P", "-5", "-15696.69"),
        ("META", "2026-01-16", "690", "P", "-5", "-30331.69"),
        ("MSFT", "2026-01-16", "495", "P", "-5", "-9031.69"),
        ("NVDA", "2026-01-16", "184", "P", "-5", "-5391.69"),
    ]:
        seed_option(book, *row)

    statement = ThinkorswimSourceReader().read(DATA_FILE)
    orders = ThinkorswimTradeHistoryReader().read(statement.section("Account Trade History"))
    trades = []
    for order in orders:
        if any(row.option_type.upper() == "FOREX" for row in order.legs):
            continue
        trade = to_trade(order)
        if min(ex.executed_at for leg in trade.legs for ex in leg.executions).date() <= date(2026, 1, 31):
            trades.append(trade)

    records = january_records()
    events = read_option_assignment_events([
        "01/09",
        "Other Activity",
        "Option Assignment",
        "DXCM",
        "01/16/2026 70.00 C",
        "DEXCOM INC",
        "5.0000",
        "01/09",
        "Other Activity",
        "Option Assignment",
        "EL",
        "01/16/2026 110.00 C",
        "ESTEE LAUDER COS",
        "5.0000",
    ])
    results = RealizedLotAttributor(book).attribute(trades, records, events)

    assert len(results) == 28
    assert all(result.allocated_quantity == result.record.quantity for result in results)
    assert all(result.basis_reconciled for result in results)
    assert all(result.gain_loss_reconciled for result in results)
    assert sum((result.record.gain_loss for result in results), D("0")) == D("126642.32")


def test_actual_january_assignment_events_close_dxcm_and_el_positions() -> None:
    statement = ThinkorswimSourceReader().read(DATA_FILE)
    orders = ThinkorswimTradeHistoryReader().read(
        statement.section("Account Trade History")
    )
    history = PositionHistory()
    events = read_option_assignment_events([
        "01/09",
        "Other Activity",
        "Option Assignment",
        "DXCM",
        "01/16/2026 70.00 C",
        "DEXCOM INC",
        "5.0000",
        "01/09",
        "Other Activity",
        "Option Assignment",
        "EL",
        "01/16/2026 110.00 C",
        "ESTEE LAUDER COS",
        "5.0000",
    ])

    for order in orders:
        if any(row.option_type.upper() == "FOREX" for row in order.legs):
            continue
        trade = to_trade(order)
        if not any(
            getattr(leg.instrument, "symbol", None) in {"DXCM", "EL"}
            or getattr(leg.instrument, "underlying", None) in {"DXCM", "EL"}
            for leg in trade.legs
        ):
            continue
        history.add_trade(trade)

    for event in events:
        history.add_event(event)

    applier = PositionEventApplier()
    history.apply(applier)

    assert applier.quantity(Instrument("DXCM")) == D("0")
    assert applier.quantity(Instrument("EL")) == D("0")
    # The assigned option contracts were opened before the January TOS
    # trade-history boundary, so their option positions are intentionally
    # unresolved here. The January stock positions are fully resolved.

def test_assignment_created_equity_lot_inherits_campaign_provenance():
    opening = LotBook()
    campaign_id = "CAMP-HISTORICAL-COIN"

    opening._append_lot(
        OptionContract(
            underlying="COIN",
            expiration=date(2026, 1, 16),
            strike=D("360"),
            option_type=OptionType.PUT,
        ),
        D("-5"),
        datetime(2025, 12, 19),
        campaign_id=campaign_id,
    )

    attributor = RealizedLotAttributor(opening)

    events = [
        PositionEvent(
            kind=PositionEventKind.ASSIGNMENT,
            occurred_at=datetime(2026, 1, 16),
            changes=(
                PositionChange(
                    instrument=OptionContract(
                        underlying="COIN",
                        expiration=date(2026, 1, 16),
                        strike=D("360"),
                        option_type=OptionType.PUT,
                    ),
                    quantity=D("5"),
                ),
                PositionChange(
                    instrument=Instrument("COIN"),
                    quantity=D("-500"),
                ),
            ),
        )
    ]

    attributor.attribute([], [], events)

    equity_lots = opening.lots(Instrument("COIN"))

    assert len(equity_lots) == 1
    assert equity_lots[0].campaign_id == campaign_id
