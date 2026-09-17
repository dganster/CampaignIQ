"""Execute and finalize one preflighted monthly CampaignIQ import."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Mapping

from campaigniq.closing_inventory_reconciliation import (
    ClosingInventoryReconciliation,
    reconcile_closing_inventory,
)
from campaigniq.analytics.period_realized_attributions import (
    attribute_period_realized_pnl,
)
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_pipeline import PeriodImportPipeline, PeriodImportResult
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.importers.schwab.pending_activity_reader import (
    read_pending_option_activity,
)
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.persistence.authoritative_lot_state import (
    save_authoritative_lot_state,
)
from campaigniq.persistence.forex_settlement_attribution_store import (
    save_forex_settlement_attributions,
)
from campaigniq.persistence.realized_attribution_store import (
    save_realized_attributions,
)
from campaigniq.persistence.monthly_publication import (
    ensure_publication_protocol,
    finalized_month_marker_path,
    publish_finalized_month_marker,
)


@dataclass(frozen=True, slots=True)
class MonthlyImportExecution:
    """Result of a pipeline run plus its closing-state verification."""

    result: PeriodImportResult
    closing_reconciliation: ClosingInventoryReconciliation
    authoritative_state_path: Path | None

    @property
    def finalized(self) -> bool:
        return self.authoritative_state_path is not None

    @property
    def forex_settlement_control_delta_usd(self):
        report = self.result.forex_transaction_report
        return report.settlement_control_delta_usd if report is not None else None

    @property
    def forex_settlement_control_reconciled(self):
        report = self.result.forex_transaction_report
        return report.settlement_control_reconciled if report is not None else None

    @property
    def forex_pl_total_control_delta_usd(self):
        report = self.result.forex_transaction_report
        return report.pl_total_control_delta_usd if report is not None else None

    @property
    def forex_pl_total_control_reconciled(self):
        report = self.result.forex_transaction_report
        return report.pl_total_control_reconciled if report is not None else None

    @property
    def forex_commission_control_delta_usd(self):
        report = self.result.forex_transaction_report
        return report.commission_control_delta_usd if report is not None else None

    @property
    def forex_commission_control_reconciled(self):
        report = self.result.forex_transaction_report
        return report.commission_control_reconciled if report is not None else None

    @property
    def forex_financing_control_deltas_usd(self):
        report = self.result.forex_transaction_report
        return report.financing_control_deltas_usd if report is not None else ()

    @property
    def forex_financing_control_reconciled(self):
        report = self.result.forex_transaction_report
        return report.financing_control_reconciled if report is not None else None


def execute_monthly_import(
    preflight: MonthlyImportPreflight,
    *,
    authoritative_state_root: str | Path,
    supplied_inputs: Mapping[MonthlyInputRole, str | Path],
    historical_source_root: str | Path | None = None,
) -> MonthlyImportExecution:
    """Run a ready import and persist ending state only after reconciliation."""
    if not preflight.ready:
        raise ValueError("Monthly import preflight is not ready.")

    opening_lot_book = preflight.opening_lot_book
    if opening_lot_book is None:
        raise ValueError("Monthly import has no authoritative opening lot state.")

    contract = preflight.contract

    tos_path = _required_input(
        supplied_inputs,
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
    )
    realized_path = _required_input(
        supplied_inputs,
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
    )
    forex_path = _required_input(
        supplied_inputs,
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
    )
    closing_path = _required_input(
        supplied_inputs,
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT,
    )

    assignment_path = supplied_inputs.get(
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE
    )
    assignment_lines: tuple[list[str], ...] = ()
    boundary_assignment_lines: tuple[list[str], ...] = ()
    if assignment_path is not None:
        lines = Path(assignment_path).read_text(encoding="utf-8").splitlines()
        # The production pipeline performs its own in-period/boundary filtering.
        # Supplying the same recognized evidence to both channels preserves that
        # existing distinction without duplicating broker parsing here.
        assignment_lines = (lines,)
        boundary_assignment_lines = (lines,)

    result = PeriodImportPipeline().run(
        period_start=contract.period_start,
        period_end=contract.period_end,
        thinkorswim_trade_history=tos_path,
        carried_opening_lot_book=opening_lot_book,
        assignment_lines=assignment_lines,
        boundary_assignment_lines=boundary_assignment_lines,
        realized_gain_loss_report=realized_path,
        forex_transaction_report=forex_path,
        historical_source_root=historical_source_root,
    )

    closing_lines = Path(closing_path).read_text(encoding="utf-8").splitlines()
    closing_rows = read_position_snapshot_section(
        closing_lines,
        snapshot_at=datetime.combine(contract.period_end, datetime.max.time()),
    )
    pending_activity = read_pending_option_activity(closing_lines)
    reconciliation = reconcile_closing_inventory(
        ending_lot_book=result.ending_lot_book,
        snapshot_rows=closing_rows,
        pending_activity=pending_activity,
        period_end=contract.period_end,
    )

    if not reconciliation.reconciled:
        return MonthlyImportExecution(
            result=result,
            closing_reconciliation=reconciliation,
            authoritative_state_path=None,
        )

    state_root = Path(authoritative_state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    realized_attributions = attribute_period_realized_pnl(result)

    realized_name = f"{contract.period_end:%Y-%m}-realized-attributions.json"
    forex_name = f"{contract.period_end:%Y-%m}-forex-settlement-attributions.json"
    lot_name = f"{contract.period_end:%Y-%m}-lot-book.json"

    # Establish the marker protocol before any protected canonical payload can
    # become visible. Older months retain their pre-marker discovery semantics.
    ensure_publication_protocol(
        state_root,
        first_period_end=contract.period_end,
    )

    # Build the complete finalized artifact set away from its canonical names.
    # A serialization/write failure therefore cannot expose a partially
    # finalized month to predecessor discovery or dashboard globbing.
    with tempfile.TemporaryDirectory(
        prefix=".campaigniq-finalize-",
        dir=state_root,
    ) as staging_dir:
        staging_root = Path(staging_dir)
        save_realized_attributions(
            staging_root / realized_name,
            period_start=contract.period_start,
            period_end=contract.period_end,
            attributions=realized_attributions,
        )
        save_forex_settlement_attributions(
            staging_root / forex_name,
            period_start=contract.period_start,
            period_end=contract.period_end,
            attributions=result.forex_settlement_attributions,
        )
        staged_state_path = save_authoritative_lot_state(
            staging_root,
            period_end=contract.period_end,
            lot_book=result.ending_lot_book,
        )

        # A rerun may be replacing an already-published month. Unpublish the
        # old generation only after the complete replacement generation has
        # staged successfully, then republish only after every payload rename.
        finalized_month_marker_path(
            state_root,
            period_end=contract.period_end,
        ).unlink(missing_ok=True)

        (staging_root / realized_name).replace(state_root / realized_name)
        (staging_root / forex_name).replace(state_root / forex_name)
        state_path = staged_state_path.replace(state_root / lot_name)

        # This marker is the publication boundary and must be published last.
        publish_finalized_month_marker(
            state_root,
            period_end=contract.period_end,
        )
    return MonthlyImportExecution(
        result=result,
        closing_reconciliation=reconciliation,
        authoritative_state_path=state_path,
    )


def _required_input(
    supplied_inputs: Mapping[MonthlyInputRole, str | Path],
    role: MonthlyInputRole,
) -> str | Path:
    try:
        return supplied_inputs[role]
    except KeyError as exc:
        raise ValueError(
            f"Required monthly input is missing at execution time: {role.value}."
        ) from exc
