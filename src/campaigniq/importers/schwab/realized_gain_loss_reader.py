"""Read Schwab Realized Gain/Loss report text."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument


_OPTION_RE = re.compile(
    r"^\s*(?P<symbol>[A-Z.]+)\s+"
    r"(?P<expiration>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<strike>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<option_type>[CP])\s+"
    r"(?P<closed_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<quantity>\d+)\s+"
    r"\$(?P<closing_price>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<basis_method>\S+)\s+"
    r"\$(?P<proceeds>[\d,]+(?:\.\d+)?)\s+"
    r"\$(?P<cost_basis>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<gain_loss>[+-]?\$[\d,]+(?:\.\d+)?)\s+"
    r"(?P<term_amount>[+-]?\$?[\d,]+(?:\.\d+)?)"
)

_EQUITY_RE = re.compile(
    r"^\s*(?P<symbol>[A-Z.]+)\s+"
    r"(?P<closed_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<quantity>\d+)\s+"
    r"\$(?P<closing_price>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<basis_method>\S+)\s+"
    r"\$(?P<proceeds>[\d,]+(?:\.\d+)?)\s+"
    r"\$(?P<cost_basis>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<gain_loss>[+-]?\$[\d,]+(?:\.\d+)?)\s+"
    r"(?P<term_amount>[+-]?\$?[\d,]+(?:\.\d+)?)"
)


def read_realized_gain_loss_section(
    lines: list[str],
) -> tuple[RealizedGainLossRecord, ...]:
    """Parse Schwab's rendered Realized Gain/Loss detail rows.

    The reader intentionally returns every detail row in the supplied report.
    The report's selected period is authoritative; settlement-date spillover
    rows, such as the February 2 LIN assignment settlement in the January
    report, must not be silently discarded.
    """
    records: list[RealizedGainLossRecord] = []
    for line in lines:
        match = _OPTION_RE.match(line)
        if match:
            records.append(_to_record(match, option=True))
            continue

        match = _EQUITY_RE.match(line)
        if match:
            records.append(_to_record(match, option=False))

    return tuple(records)


def _to_record(match: re.Match[str], *, option: bool) -> RealizedGainLossRecord:
    expiration = (
        datetime.strptime(match.group("expiration"), "%m/%d/%Y").date()
        if option
        else None
    )
    if option:
        instrument = OptionContract(
            underlying=match.group("symbol"),
            expiration=expiration,
            strike=_decimal(match.group("strike")),
            option_type=(
                OptionType.CALL
                if match.group("option_type") == "C"
                else OptionType.PUT
            ),
        )
    else:
        instrument = Instrument(match.group("symbol"))

    return RealizedGainLossRecord(
        closed_date=datetime.strptime(
            match.group("closed_date"), "%m/%d/%Y"
        ).date(),
        instrument=instrument,
        quantity=Decimal(match.group("quantity")),
        closing_price=_decimal(match.group("closing_price")),
        proceeds=_decimal(match.group("proceeds")),
        cost_basis=_decimal(match.group("cost_basis")),
        gain_loss=_decimal(match.group("gain_loss")),
        basis_method=match.group("basis_method"),
        term=_term(match),
    )


def _decimal(value: str) -> Decimal:
    value = value.replace("$", "").replace(",", "")
    return Decimal(value)


def _term(match: re.Match[str]) -> str:
    # Schwab renders the inactive term column as blank. The final numeric
    # amount therefore identifies the active tax term for ordinary rows.
    return "SHORT" if _decimal(match.group("term_amount")) != 0 else "UNKNOWN"
