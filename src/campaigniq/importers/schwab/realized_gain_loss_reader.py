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

_DISALLOWED_LOSS_RE = re.compile(
    r"Disallowed Loss:\s*\$(?P<amount>[\d,]+(?:\.\d+)?)"
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
    for index, line in enumerate(lines):
        disallowed_loss = _disallowed_loss_for_row(
            lines,
            index,
        )

        match = _OPTION_RE.match(line)
        if match:
            records.append(
                _to_record(
                    match,
                    option=True,
                    disallowed_loss=disallowed_loss,
                )
            )
            continue

        match = _EQUITY_RE.match(line)
        if match:
            records.append(
                _to_record(
                    match,
                    option=False,
                    disallowed_loss=disallowed_loss,
                )
            )

    return tuple(records)


def _to_record(
    match: re.Match[str],
    *,
    option: bool,
    disallowed_loss: Decimal,
) -> RealizedGainLossRecord:
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
        disallowed_loss=disallowed_loss,
    )


def _disallowed_loss_for_row(
    lines: list[str],
    row_index: int,
) -> Decimal:
    """Return Schwab's disallowed loss attached to one realized detail row.

    Schwab may render the "Disallowed Loss:" label on the detail row or on a
    continuation line, while rendering the amount on that line or a later
    continuation line. Only lines belonging to this detail row are searched;
    the next realized detail row ends the search.
    """
    label = "Disallowed Loss:"

    for index in range(row_index, len(lines)):
        candidate = lines[index]

        if index > row_index and (
            _OPTION_RE.match(candidate)
            or _EQUITY_RE.match(candidate)
        ):
            break

        label_column = candidate.find(label)
        if label_column < 0:
            continue

        same_line_match = _DISALLOWED_LOSS_RE.search(candidate)
        if same_line_match:
            return _decimal(same_line_match.group("amount"))

        for continuation in lines[index + 1 :]:
            if (
                _OPTION_RE.match(continuation)
                or _EQUITY_RE.match(continuation)
            ):
                break

            amount_match = re.search(
                r"\$(?P<amount>[\d,]+(?:\.\d+)?)",
                continuation[label_column:],
            )
            if amount_match:
                return _decimal(amount_match.group("amount"))

        break

    return Decimal("0")


def _decimal(value: str) -> Decimal:
    value = value.replace("$", "").replace(",", "")
    return Decimal(value)


def _term(match: re.Match[str]) -> str:
    # Schwab renders the inactive term column as blank. The final numeric
    # amount therefore identifies the active tax term for ordinary rows.
    return "SHORT" if _decimal(match.group("term_amount")) != 0 else "UNKNOWN"
