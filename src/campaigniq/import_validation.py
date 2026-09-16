"""Content-based validation for monthly CampaignIQ broker inputs."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section
from campaigniq.importers.schwab.realized_gain_loss_reader import read_realized_gain_loss_section
from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

@dataclass(frozen=True, slots=True)
class MonthlyInputValidation:
    role: MonthlyInputRole
    valid: bool
    message: str
    record_count: int = 0

_DATE_RANGE_RE = re.compile(
    r"From mm/dd/yyyy\s+(?P<start>\d{2}/\d{2}/\d{4}).*?"
    r"To mm/dd/yyyy\s+(?P<end>\d{2}/\d{2}/\d{4})",
    re.DOTALL,
)

def _mmddyyyy(value: str) -> date:
    month, day, year = (int(part) for part in value.split("/"))
    return date(year, month, day)

def validate_monthly_input(
    role: MonthlyInputRole,
    path: str | Path,
    *,
    period_start: date,
    period_end: date,
) -> MonthlyInputValidation:
    """Validate a user input by contents rather than filename."""
    source = Path(path)
    if not source.is_file():
        return MonthlyInputValidation(role, False, "File does not exist.")

    try:
        if role is MonthlyInputRole.THINKORSWIM_TRADE_HISTORY:
            source_reader = ThinkorswimSourceReader()
            trade_history_reader = ThinkorswimTradeHistoryReader()

            # Parse the Account Trade History section first so recognition does
            # not depend on the filename.
            statement = source_reader.read(str(source))
            orders = trade_history_reader.read(
                statement.section("Account Trade History")
            )

            # Then use the production trade reader for date-scoped validation.
            # It deliberately handles regular securities and FOREX through
            # their respective import paths.
            trades = ThinkorswimTradeReader(
                source_reader,
                trade_history_reader,
            ).read(
                source,
                start=period_start,
                end=period_end,
            )

            if not trades:
                return MonthlyInputValidation(
                    role, False,
                    "Recognized Thinkorswim trade history, but it contains no trades in the requested month.",
                    len(orders),
                )

            return MonthlyInputValidation(
                role, True,
                f"Recognized Thinkorswim trade history with {len(trades)} in-period trades.",
                len(trades),
            )

        text = source.read_text()
        lines = text.splitlines()

        if role is MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT:
            period_label = (
                f"{period_start.strftime('%B')} {period_start.day}-"
                f"{period_end.day}, {period_end.year}"
            )
            if period_label not in text:
                return MonthlyInputValidation(
                    role, False,
                    "Recognized file does not declare the requested Schwab "
                    f"statement period {period_label}.",
                )
            rows = read_position_snapshot_section(
                lines,
                snapshot_at=datetime.combine(period_end, datetime.max.time()),
            )
            if not rows:
                return MonthlyInputValidation(
                    role, False,
                    "Requested-month Schwab statement contains no recognized ending positions.",
                )
            return MonthlyInputValidation(
                role, True,
                f"Recognized Schwab month-end position snapshot with {len(rows)} positions.",
                len(rows),
            )

        if role is MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT:
            report = read_forex_transaction_report(source)
            period_text = report.period_text.lower()
            if period_start.strftime("%b").lower() not in period_text or str(period_start.year) not in period_text:
                return MonthlyInputValidation(role, False, "Recognized Thinkorswim FOREX Transaction Report, but its report period does not match the requested month.")
            count = len(report.settlements) + len(report.financing)
            return MonthlyInputValidation(role, True, f"Recognized Thinkorswim FOREX Transaction Report with {len(report.settlements)} settlement(s) and {len(report.financing)} financing record(s).", count)

        if role is MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS:
            if "Realized Gain / Loss" not in text:
                return MonthlyInputValidation(
                    role, False, "Not a recognized Schwab realized gain/loss report."
                )
            match = _DATE_RANGE_RE.search(text)
            if match is None:
                return MonthlyInputValidation(
                    role, False,
                    "Recognized Schwab realized gain/loss report, but its date range could not be determined.",
                )
            report_start = _mmddyyyy(match.group("start"))
            report_end = _mmddyyyy(match.group("end"))
            if (report_start, report_end) != (period_start, period_end):
                return MonthlyInputValidation(
                    role, False,
                    f"Schwab report covers {report_start} through {report_end}; requested period is {period_start} through {period_end}.",
                )
            records = read_realized_gain_loss_section(lines)
            return MonthlyInputValidation(
                role, True,
                f"Recognized Schwab realized gain/loss report for the requested month with {len(records)} records.",
                len(records),
            )

        if role is MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE:
            events = read_option_assignment_events(lines)
            boundary_date = period_end + timedelta(days=1)
            while boundary_date.weekday() >= 5:
                boundary_date += timedelta(days=1)
            relevant = [
                event for event in events
                if (
                    period_start <= event.occurred_at.date() <= period_end
                    or event.occurred_at.date() == boundary_date
                )
            ]
            in_period = [
                event for event in relevant
                if event.occurred_at.date() <= period_end
            ]
            boundary = [
                event for event in relevant
                if event.occurred_at.date() == boundary_date
            ]
            if not events:
                return MonthlyInputValidation(
                    role, False, "No Schwab option assignment events were recognized."
                )
            if not relevant:
                return MonthlyInputValidation(
                    role, False,
                    "Recognized Schwab assignment evidence, but it contains "
                    "no assignment events in the requested month or on the "
                    f"relevant boundary date {boundary_date}.",
                    len(events),
                )
            parts = []
            if in_period:
                parts.append(f"{len(in_period)} in-period")
            if boundary:
                parts.append(f"{len(boundary)} boundary-date")
            return MonthlyInputValidation(
                role, True,
                "Recognized Schwab assignment evidence with "
                + " and ".join(parts)
                + " assignment event(s).",
                len(relevant),
            )

        return MonthlyInputValidation(
            role, False, "This input role is not user-file validated."
        )
    except (AttributeError, KeyError, ValueError, IndexError) as exc:
        return MonthlyInputValidation(
            role, False,
            f"File contents do not match the expected {role.value} format: {exc}",
        )
