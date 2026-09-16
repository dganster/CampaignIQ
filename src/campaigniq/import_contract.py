"""Describe the broker inputs required for one CampaignIQ monthly import."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from enum import Enum


class MonthlyInputRole(str, Enum):
    """Semantic role played by an input to a monthly import."""

    THINKORSWIM_TRADE_HISTORY = "thinkorswim_trade_history"
    SCHWAB_REALIZED_GAIN_LOSS = "schwab_realized_gain_loss"
    SCHWAB_FOREX_TRANSACTION_REPORT = "schwab_forex_transaction_report"
    SCHWAB_CLOSING_POSITION_SNAPSHOT = "schwab_closing_position_snapshot"
    OPENING_STATE = "opening_state"
    SCHWAB_ASSIGNMENT_EVIDENCE = "schwab_assignment_evidence"
    HISTORICAL_TRADE_EVIDENCE = "historical_trade_evidence"


@dataclass(frozen=True, slots=True)
class MonthlyInputRequirement:
    """One input role and how CampaignIQ expects to obtain it."""

    role: MonthlyInputRole
    required: bool
    user_supplied: bool
    description: str


@dataclass(frozen=True, slots=True)
class MonthlyImportContract:
    """Input contract for a calendar-month CampaignIQ import."""

    period_start: date
    period_end: date
    requirements: tuple[MonthlyInputRequirement, ...]

    @property
    def user_supplied_requirements(self) -> tuple[MonthlyInputRequirement, ...]:
        return tuple(
            requirement
            for requirement in self.requirements
            if requirement.user_supplied
        )


def monthly_import_contract(year: int, month: int) -> MonthlyImportContract:
    """Return the semantic input contract for one calendar month.

    Filenames are intentionally absent.  CampaignIQ cares about what a file
    contains and the role it plays, not what the user happened to name it.
    """

    period_start = date(year, month, 1)
    period_end = date(year, month, calendar.monthrange(year, month)[1])

    requirements = (
        MonthlyInputRequirement(
            role=MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
            required=True,
            user_supplied=True,
            description=(
                "Thinkorswim trade history containing activity for the "
                "requested month."
            ),
        ),
        MonthlyInputRequirement(
            role=MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
            required=True,
            user_supplied=True,
            description="Thinkorswim Forex Transaction Report for the requested month.",
        ),
        MonthlyInputRequirement(
            role=MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
            required=True,
            user_supplied=True,
            description=(
                "Schwab realized gain/loss report for the requested month."
            ),
        ),
        MonthlyInputRequirement(
            role=MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
            required=True,
            user_supplied=True,
            description=(
                "Schwab position snapshot for the requested month-end, used "
                "to reconcile CampaignIQ's computed ending inventory."
            ),
        ),
        MonthlyInputRequirement(
            role=MonthlyInputRole.OPENING_STATE,
            required=True,
            user_supplied=False,
            description=(
                "Opening lot state carried from CampaignIQ's preceding "
                "authoritative period, or reconstructed from a validated "
                "opening snapshot when no authoritative predecessor exists."
            ),
        ),
        MonthlyInputRequirement(
            role=MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE,
            required=False,
            user_supplied=True,
            description=(
                "Schwab assignment/exercise evidence when such events "
                "occurred during the month or on the relevant boundary date."
            ),
        ),
        MonthlyInputRequirement(
            role=MonthlyInputRole.HISTORICAL_TRADE_EVIDENCE,
            required=False,
            user_supplied=False,
            description=(
                "Earlier trade history discovered by CampaignIQ only when "
                "boundary reconstruction identifies missing historical "
                "evidence."
            ),
        ),
    )

    return MonthlyImportContract(
        period_start=period_start,
        period_end=period_end,
        requirements=requirements,
    )
