"""Execute and finalize one preflighted monthly CampaignIQ import."""

from __future__ import annotations

from campaigniq.persistence.position_journal import serialize_position_journal

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from shutil import copyfile
from typing import Mapping

from campaigniq.closing_inventory_reconciliation import (
    ClosingInventoryReconciliation,
    reconcile_closing_inventory,
)
from campaigniq.analytics.period_realized_attributions import (
    attribute_period_realized_pnl,
)
from campaigniq.persistence.crypto_month import (
    build_crypto_month, crypto_month_key, load_preceding_crypto, serialize_crypto_month,
)
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_pipeline import PeriodImportPipeline, PeriodImportResult
from campaigniq.import_preflight import MonthlyImportPreflight
from campaigniq.importers.schwab.pending_activity_reader import (
    read_pending_position_activity,
)
from campaigniq.importers.schwab.position_snapshot_reader import (
    read_position_snapshot_section,
)
from campaigniq.persistence.artifact_storage import ArtifactStorage, LocalFilesystemArtifactStorage
from campaigniq.persistence.authoritative_lot_state import lot_state_key
from campaigniq.persistence.lot_book_store import serialize_lot_book
from campaigniq.persistence.forex_settlement_attribution_store import (
    serialize_forex_settlement_attributions,
)
from campaigniq.persistence.realized_attribution_store import (
    serialize_realized_attributions,
)
from campaigniq.persistence.lifecycle_transition_store import (
    serialize_lifecycle_transitions,
)
from campaigniq.persistence.import_provenance import (
    capture_monthly_input_provenance,
    serialize_monthly_import_provenance,
)
from campaigniq.persistence.boundary_completeness_store import (
    serialize_boundary_completeness,
)
from campaigniq.persistence.reconciliation_decision import (
    ReconciliationDecision,
    decision_exactly_matches_reconciliation,
    reconciliation_decision_key,
    serialize_reconciliation_decision,
    deserialize_reconciliation_decision,
)
from campaigniq.persistence.monthly_publication import (
    ensure_publication_protocol_in_storage,
    publish_finalized_month_marker_to_storage,
    unpublish_finalized_month_marker_from_storage,
)


@dataclass(frozen=True, slots=True)
class MonthlyImportExecution:
    """Result of a pipeline run plus its closing-state verification."""

    result: PeriodImportResult
    closing_reconciliation: ClosingInventoryReconciliation
    authoritative_state_path: Path | None
    reconciliation_decision: ReconciliationDecision | None = None
    crypto_report: dict | None = None

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


def _archive_thinkorswim_trade_history(
    source: str | Path,
    *,
    historical_source_root: str | Path,
    period_end,
) -> Path:
    """Archive finalized monthly Thinkorswim evidence for future reconstruction."""
    root = Path(historical_source_root)
    root.mkdir(parents=True, exist_ok=True)

    destination = root / (
        f"Account Trade History {period_end:%B %Y}.csv"
    )
    copyfile(Path(source), destination)
    return destination


def execute_monthly_import(
    preflight: MonthlyImportPreflight,
    *,
    authoritative_state_root: str | Path,
    supplied_inputs: Mapping[MonthlyInputRole, str | Path],
    historical_source_root: str | Path | None = None,
    artifact_storage: ArtifactStorage | None = None,
    reconciliation_decision: ReconciliationDecision | None = None,
) -> MonthlyImportExecution:
    """Run and finalize after reconciliation or an exact reviewed exception."""
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

    next_statement = supplied_inputs.get(MonthlyInputRole.SCHWAB_NEXT_MONTH_ASSIGNMENT_EVIDENCE)
    if next_statement is not None:
        boundary_assignment_lines = (*boundary_assignment_lines,
            _validated_next_month_assignment_lines(next_statement, period_end=contract.period_end))

    previously_applied_deliveries = _verified_prior_deliveries(
        tos_path, period_start=contract.period_start, opening_lot_book=opening_lot_book,
        storage=artifact_storage or LocalFilesystemArtifactStorage(Path(authoritative_state_root)),
        historical_source_root=historical_source_root,
    )

    result = PeriodImportPipeline().run(
        campaign_namespace=f"{contract.period_start:%Y-%m}",
        previously_applied_deliveries=previously_applied_deliveries,
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
    pending_activity = read_pending_position_activity(
        closing_lines, period_end=contract.period_end,
    )
    reconciliation = reconcile_closing_inventory(
        ending_lot_book=result.ending_lot_book,
        snapshot_rows=closing_rows,
        pending_activity=pending_activity,
        period_end=contract.period_end,
        forex_report=result.forex_transaction_report,
        opening_lot_book=opening_lot_book,
    )

    accepted_decision: ReconciliationDecision | None = None

    if not reconciliation.reconciled:
        if reconciliation_decision is None:
            return MonthlyImportExecution(
                result=result,
                closing_reconciliation=reconciliation,
                authoritative_state_path=None,
            )

        if not decision_exactly_matches_reconciliation(
            decision=reconciliation_decision,
            reconciliation=reconciliation,
            period_start=contract.period_start,
            period_end=contract.period_end,
        ):
            return MonthlyImportExecution(
                result=result,
                closing_reconciliation=reconciliation,
                authoritative_state_path=None,
            )

        accepted_decision = reconciliation_decision

    state_root = Path(authoritative_state_root)
    storage = artifact_storage or LocalFilesystemArtifactStorage(state_root)

    if historical_source_root is not None:
        _archive_thinkorswim_trade_history(
            tos_path,
            historical_source_root=historical_source_root,
            period_end=contract.period_end,
        )

    realized_attributions = attribute_period_realized_pnl(result)
    input_provenance = capture_monthly_input_provenance(supplied_inputs)

    realized_name = f"{contract.period_end:%Y-%m}-realized-attributions.json"
    forex_name = f"{contract.period_end:%Y-%m}-forex-settlement-attributions.json"
    lifecycle_name = f"{contract.period_end:%Y-%m}-lifecycle-transitions.json"
    lot_name = lot_state_key(period_end=contract.period_end)
    provenance_name = f"{contract.period_end:%Y-%m}-import-provenance.json"
    completeness_name = f"{contract.period_end:%Y-%m}-boundary-completeness.json"
    decision_name = reconciliation_decision_key(period_end=contract.period_end)

    ensure_publication_protocol_in_storage(
        storage, first_period_end=contract.period_end
    )

    # Build the complete replacement generation in memory before unpublishing.
    realized_text = serialize_realized_attributions(
        period_start=contract.period_start,
        period_end=contract.period_end,
        attributions=realized_attributions,
    )
    forex_text = serialize_forex_settlement_attributions(
        period_start=contract.period_start,
        period_end=contract.period_end,
        attributions=result.forex_settlement_attributions,
    )
    lifecycle_text = serialize_lifecycle_transitions(
        period_start=contract.period_start,
        period_end=contract.period_end,
        transitions=result.lifecycle_transitions,
    )
    journal_name = f"{contract.period_end:%Y-%m}-position-journal.json"
    journal_text = serialize_position_journal(
        result, period_start=contract.period_start, period_end=contract.period_end
    )
    lot_text = serialize_lot_book(
        period_end=contract.period_end, lot_book=result.ending_lot_book
    )
    provenance_text = serialize_monthly_import_provenance(
        period_start=contract.period_start,
        period_end=contract.period_end,
        inputs=input_provenance,
    )
    completeness_text = serialize_boundary_completeness(
        period_start=contract.period_start,
        period_end=contract.period_end,
        reconstruction=result.boundary_reconstruction,
    )
    decision_text = (
        serialize_reconciliation_decision(accepted_decision)
        if accepted_decision is not None
        else None
    )

    crypto_report = (
        build_crypto_month(result.crypto_report, load_preceding_crypto(storage, contract.period_start))
        if result.crypto_report is not None else None
    )
    crypto_text = serialize_crypto_month(crypto_report) if crypto_report is not None else None
    crypto_name = crypto_month_key(contract.period_end)

    # A rerun becomes invisible before any canonical payload is replaced.
    unpublish_finalized_month_marker_from_storage(
        storage, period_end=contract.period_end
    )
    storage.write_text(realized_name, realized_text)
    storage.write_text(forex_name, forex_text)
    storage.write_text(lifecycle_name, lifecycle_text)
    storage.write_text(lot_name, lot_text)
    storage.write_text(journal_name, journal_text)
    storage.write_text(provenance_name, provenance_text)
    storage.write_text(completeness_name, completeness_text)
    if crypto_text is not None:
        storage.write_text(crypto_name, crypto_text)
    else:
        storage.delete(crypto_name)

    if decision_text is not None:
        storage.write_text(decision_name, decision_text)
    else:
        storage.delete(decision_name)

    # The marker is the multi-object visibility boundary and is written last.
    publish_finalized_month_marker_to_storage(
        storage, period_end=contract.period_end
    )
    state_path = state_root / lot_name

    return MonthlyImportExecution(
        result=result,
        closing_reconciliation=reconciliation,
        authoritative_state_path=state_path,
        reconciliation_decision=accepted_decision,
        crypto_report=crypto_report,
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


def _verified_prior_deliveries(filename, *, period_start, opening_lot_book, storage, historical_source_root=None):
    """Require a published, approved predecessor exception and matching cash evidence."""
    from campaigniq.domain.option_contract import OptionContract
    from campaigniq.importers.thinkorswim.expiration_event_reader import ThinkorswimExpirationEventReader
    from campaigniq.importers.thinkorswim.cash_balance_reader import ThinkorswimCashBalanceReader
    from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
    from campaigniq.persistence.monthly_publication import is_month_published_in_storage
    previous_end = period_start - timedelta(days=1)
    key = reconciliation_decision_key(period_end=previous_end)
    if not is_month_published_in_storage(storage, period_end=previous_end) or not storage.exists(key):
        return ()
    decision = deserialize_reconciliation_decision(storage.read_text(key))
    if decision.period_end != previous_end or not decision.permits_exceptional_finalization:
        return ()
    sources = [Path(filename)]
    if historical_source_root is not None:
        import hashlib
        import json
        archived = Path(historical_source_root) / f"Account Trade History {previous_end:%B %Y}.csv"
        provenance_key = f"{previous_end:%Y-%m}-import-provenance.json"
        if archived.is_file() and storage.exists(provenance_key):
            manifest = json.loads(storage.read_text(provenance_key))
            if (manifest.get("format") != "campaigniq.monthly_import_provenance"
                    or manifest.get("version") != 1
                    or manifest.get("period_end") != previous_end.isoformat()):
                raise ValueError("Prior delivery source provenance is invalid.")
            matches = [item for item in manifest.get("inputs", [])
                       if item.get("role") == MonthlyInputRole.THINKORSWIM_TRADE_HISTORY.value]
            content = archived.read_bytes()
            if (len(matches) != 1 or matches[0].get("sha256") != hashlib.sha256(content).hexdigest()
                    or matches[0].get("byte_size") != len(content)):
                raise ValueError("Archived predecessor Trade History differs from its published source evidence.")
            sources.append(archived)
    events = tuple(dict.fromkeys(
        event for source in sources
        for event in ThinkorswimExpirationEventReader(ThinkorswimCashBalanceReader()).read(
            ThinkorswimSourceReader().read(str(source)).section("Cash Balance"),
            start=previous_end - timedelta(days=3), end=previous_end,
        )
    ))
    verified = []
    for event in events:
        if len(event.changes) != 1:
            continue
        change, = event.changes
        if isinstance(change.instrument, OptionContract):
            continue
        quantity = sum((lot.quantity for lot in opening_lot_book.lots(change.instrument)), 0)
        matches = [m for m in decision.mismatches if m.instrument == change.instrument
                   and m.computed_quantity == quantity
                   and m.computed_quantity - m.snapshot_quantity == change.quantity]
        if len(matches) == 1:
            verified.append(event)
    return tuple(verified)


def _validated_next_month_assignment_lines(path, *, period_end):
    """Use a validated next-month statement only as boundary assignment evidence."""
    import calendar
    from campaigniq.import_validation import validate_monthly_input
    from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
    next_start = period_end + timedelta(days=1)
    next_end = next_start.replace(day=calendar.monthrange(next_start.year, next_start.month)[1])
    validation = validate_monthly_input(
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT, path,
        period_start=next_start, period_end=next_end,
    )
    if not validation.valid:
        raise ValueError("Next-month assignment statement: " + validation.message)
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    boundary = next_start
    while boundary.weekday() >= 5:
        boundary += timedelta(days=1)
    if not any(e.occurred_at.date() == boundary for e in read_option_assignment_events(lines)):
        raise ValueError("Next-month statement contains no first-business-day assignment evidence.")
    return lines
