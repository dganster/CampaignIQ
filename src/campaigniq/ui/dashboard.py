"""CampaignIQ read-only analytics dashboard."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from campaigniq.access import AccessContext
from campaigniq.analytics.campaign_outcome_distribution import (
    summarize_campaign_outcomes,
)
from campaigniq.analytics.lifecycle_analytics import (
    SymbolLifecycleSummary,
    summarize_lifecycle_history,
)
from campaigniq.analytics.drawdown_contribution import (
    summarize_maximum_drawdown_contributions,
)
from campaigniq.analytics.multi_month_analytics_summary import (
    summarize_multi_month_analytics,
)
from campaigniq.analytics.multi_month_campaign_performance import (
    summarize_multi_month_campaign_performance,
)
from campaigniq.analytics.multi_month_performance_summary import (
    summarize_multi_month_performance,
)
from campaigniq.analytics.realized_drawdown import (
    summarize_period_qualified_realized_drawdown,
)
from campaigniq.analytics.repeated_campaign_performance import (
    summarize_repeated_campaign_performance,
)
from campaigniq.analytics.underlying_performance import (
    summarize_underlying_performance,
)
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.import_contract import MonthlyInputRole
from campaigniq.import_preflight import prepare_monthly_import
from campaigniq.importers.thinkorswim.crypto_reader import read_crypto_report
from campaigniq.persistence.crypto_month import build_crypto_month, load_preceding_crypto
from campaigniq.ui.crypto_view import render_crypto_report
from campaigniq.monthly_import_execution import execute_monthly_import
from campaigniq.persistence.reconciliation_decision import (
    ACCEPT_TRANSACTION_DERIVED_STATE,
    BOUNDARY_TIMING_EXCEPTION,
    ReconciliationDecision,
    capture_reconciliation_mismatches,
)
from campaigniq.pdf_text import extract_pdf_text
from campaigniq.persistence.lifecycle_history import (
    load_lifecycle_history_from_storage,
)
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.persistence.persisted_multi_month_analytics import (
    load_persisted_monthly_campaign_attributions_from_storage,
)
from campaigniq.runtime import build_local_runtime
from campaigniq.persistence.authoritative_lot_state import (
    load_preceding_authoritative_state_from_storage,
    lot_state_key,
)
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from campaigniq.ui.access_history import (
    access_history_storage,
    is_access_admin,
    load_access_history,
    record_session_access,
)
from campaigniq.ui.access_audit import audit_unauthorized_oidc_identity
from campaigniq.ui.access_gate import (
    AUTH_MODE_OIDC,
    access_password,
    authentication_mode,
    password_matches,
)
from campaigniq.ui.dashboard_campaigns import (
    aggregate_period_qualified_campaigns,
    campaign_realized_attributions,
)
from campaigniq.ui.oidc_access_gate import authorized_streamlit_access
from campaigniq.ui.workspace_authorization import workspace_authorization

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RECONCILED_DIR = PROJECT_ROOT / "tests" / "data" / "reconciled"


def require_dashboard_access() -> AccessContext | None:
    """Require dashboard access and return authorized workspace context."""
    if authentication_mode() == AUTH_MODE_OIDC:
        access = authorized_streamlit_access(
            st.user,
            workspace_authorization(),
        )
        if access is not None:
            return access

        st.title("CampaignIQ")
        st.caption("Private access")

        claims = st.user.to_dict()
        is_logged_in = claims.get("is_logged_in") is True

        if not is_logged_in:
            st.info("Google authentication is not active for this session.")
            if st.button("Sign in with Google", type="primary"):
                st.login()
        else:
            audit_unauthorized_oidc_identity(claims)
            st.error("This Google account is not authorized for CampaignIQ.")

            if st.button("Sign out"):
                st.logout()

        st.stop()

    expected_password = access_password()

    # Preserve normal local development when no password is configured.
    if expected_password is None:
        return

    if st.session_state.get("campaigniq_authenticated") is True:
        return

    st.title("CampaignIQ")
    st.caption("Private access")

    candidate = st.text_input(
        "Password",
        type="password",
        autocomplete="current-password",
        key="campaigniq_access_password",
    )

    if st.button("Sign in", type="primary"):
        if password_matches(candidate, expected_password):
            st.session_state["campaigniq_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()


ACCESS = require_dashboard_access()

RUNTIME = build_local_runtime(
    project_root=PROJECT_ROOT,
    workspace_id=ACCESS.workspace_id if ACCESS is not None else None,
)
AUTHORITATIVE_STATE_DIR = RUNTIME.authoritative_state_root
HISTORICAL_SOURCE_ROOT = RUNTIME.historical_source_root
ARTIFACT_STORAGE = RUNTIME.artifact_storage

# CAMPAIGNIQ_ACCESS_HISTORY
ACCESS_HISTORY_STORAGE = access_history_storage(PROJECT_ROOT)
ACCESS_HISTORY_ERROR = record_session_access(
    ACCESS,
    st.user.to_dict() if ACCESS is not None else {},
    st.session_state,
    ACCESS_HISTORY_STORAGE,
)

MONTHLY_UPLOAD_ROLES = (
    (
        MonthlyInputRole.THINKORSWIM_TRADE_HISTORY,
        "Thinkorswim trade history",
        ("csv",),
    ),
    (
        "schwab_brokerage_statement",
        "Schwab Brokerage Statement",
        ("pdf", "txt", "csv"),
    ),
    (
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS,
        "Schwab Realized Gain/Loss Report",
        ("pdf", "txt", "csv"),
    ),
    (
        MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT,
        "Thinkorswim Forex Transaction Report",
        ("csv",),
    ),
    (
        MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT,
        "Prior month-end Schwab Brokerage Statement (first import only)",
        ("pdf", "txt", "csv"),
    ),
)


def money(value: Decimal) -> str:
    """Format a Decimal as signed currency."""
    amount = float(value)
    if amount > 0:
        return f"+${amount:,.2f}"
    if amount < 0:
        return f"-${abs(amount):,.2f}"
    return "$0.00"


def _display_decimal(value) -> str:
    """Format a domain Decimal without unnecessary fractional zeros."""

    text = format(value, "f")

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text


def _display_option_contract(contract: OptionContract) -> str:
    """Format an option contract for lifecycle presentation."""

    return (
        f"{contract.expiration:%b %d, %Y} "
        f"${_display_decimal(contract.strike)} "
        f"{contract.option_type.value.title()}"
    )


def _display_position(instrument, quantity) -> str:
    """Format one authoritative position snapshot entry."""

    absolute_quantity = abs(quantity)
    quantity_text = f"{absolute_quantity:,f}"

    if "." in quantity_text:
        quantity_text = quantity_text.rstrip("0").rstrip(".")

    if isinstance(instrument, OptionContract):
        direction = "short" if quantity < 0 else "long"
        noun = "contract" if absolute_quantity == 1 else "contracts"
        return (
            f"{quantity_text} {direction} "
            f"{_display_option_contract(instrument)} {noun}"
        )

    if isinstance(instrument, Instrument):
        direction = "short" if quantity < 0 else ""
        noun = "share" if absolute_quantity == 1 else "shares"
        prefix = f"{direction} " if direction else ""
        return f"{quantity_text} {prefix}{instrument.symbol} {noun}"

    return f"{quantity_text} {instrument}"


def _lifecycle_transition_details(transition) -> tuple[str, object]:
    """Describe existing lifecycle evidence without inferring intent."""

    if transition.corporate_action is not None:
        action = transition.corporate_action
        action_name = action.action_type.value.replace("_", " ").title()
        ratio = f"{_display_decimal(action.new_units)}-for-{_display_decimal(action.old_units)}"
        source = f"Source: {action.source}"
        if action.source_reference:
            source += f" ({action.source_reference})"
        return f"{action_name} {ratio}. {source}", None

    if transition.position_event is not None:
        changes = transition.position_event.changes
        return ", ".join(
            f"{'+' if change.quantity > 0 else '-'}"
            f"{_display_decimal(abs(change.quantity))} "
            f"{_display_instrument(change.instrument)}"
            for change in changes
        ), None

    if transition.option_roll is not None:
        roll = transition.option_roll
        return (
            (
                f"{_display_option_contract(roll.closed_contract)} "
                f"→ {_display_option_contract(roll.opened_contract)}"
            ),
            roll.quantity,
        )

    if transition.position_exit is not None:
        before = transition.position_exit.before_positions

        if before:
            positions = " + ".join(
                _display_position(instrument, quantity)
                for instrument, quantity in before
            )
            return f"{positions} → zero exposure", None

        return "Position → zero exposure", None

    if transition.covered_position is not None:
        covered = transition.covered_position
        shares = _display_decimal(covered.share_quantity)
        calls = _display_decimal(covered.covered_call_quantity)

        return (
            f"{shares} {covered.underlying} shares + "
            f"{calls} covered calls",
            None,
        )

    return "", None


def lifecycle_timeline_rows(
    summary: SymbolLifecycleSummary,
) -> list[dict[str, object]]:
    """Build display rows from authoritative lifecycle transitions."""

    rows = []

    for transition in summary.transitions:
        details, quantity = _lifecycle_transition_details(transition)

        rows.append(
            {
                "Date": transition.occurred_at.date(),
                "Time": transition.occurred_at.time(),
                "Transition": transition.kind.value.replace("_", " "),
                "Details": details,
                "Qty": float(quantity) if quantity is not None else None,
            }
        )

    return rows


def _save_uploaded_monthly_inputs(*, upload_dir, uploads):
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

    return supplied


def _monthly_import_signature(*, year, month, uploads):
    digest = hashlib.sha256()
    digest.update(f"{int(year):04d}-{int(month):02d}".encode())

    for role, _, _ in MONTHLY_UPLOAD_ROLES:
        upload = uploads.get(role)
        digest.update(str(role).encode())
        if upload is None:
            digest.update(b"<missing>")
            continue
        digest.update(upload.name.encode())
        digest.update(upload.getvalue())

    return digest.hexdigest()


def _show_validation(validation, *, label, required=True):
    message = f"**{label}:** {validation.message}"
    if validation.valid:
        st.success(message)
    elif required:
        st.error(message)
    else:
        st.info(message)


def _show_monthly_preflight(preflight):
    st.markdown("#### 3. Review")
    st.caption(
        "Review what CampaignIQ validated before finalization. "
        "Nothing has been published yet."
    )

    _show_validation(
        preflight.validation_for(
            MonthlyInputRole.THINKORSWIM_TRADE_HISTORY
        ),
        label="Thinkorswim trade history",
    )

    realized_validation = preflight.validation_for(
        MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS
    )
    closing_validation = preflight.validation_for(
        MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT
    )
    assignment_validation = preflight.validation_for(
        MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE
    )

    if closing_validation.valid:
        st.success(
            "**Schwab Brokerage Statement:** recognized for the requested "
            "month, including closing positions."
        )
    else:
        st.error(
            "**Schwab Brokerage Statement:** CampaignIQ could not validate "
            "the required closing-position evidence."
        )
        st.error(f"Closing positions: {closing_validation.message}")

    _show_validation(
        realized_validation,
        label="Schwab Realized Gain/Loss Report",
    )

    _show_validation(
        preflight.validation_for(MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT),
        label="Thinkorswim Forex Transaction Report",
    )

    if assignment_validation.record_count > 0:
        st.success(
            "**Assignment/exercise evidence:** detected in the Schwab "
            "Brokerage Statement."
        )
    else:
        st.info(
            "**Assignment/exercise evidence:** none detected for this "
            "month. This evidence is optional."
        )

    opening_snapshot_validation = preflight.validation_for(
        MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
    )
    _show_validation(
        opening_snapshot_validation,
        label="Prior month-end Schwab Brokerage Statement",
        required=False,
    )

    _show_validation(
        preflight.validation_for(MonthlyInputRole.OPENING_STATE),
        label="Opening inventory",
    )



def _month_start(year: int, month: int) -> date:
    return date(int(year), int(month), 1)


def _next_month(period_end: date) -> date:
    if period_end.month == 12:
        return date(period_end.year + 1, 1, 1)
    return date(period_end.year, period_end.month + 1, 1)


def _authoritative_lot_period_ends() -> tuple[date, ...]:
    """Return published authoritative lot-state period ends."""

    period_ends = []

    for key in ARTIFACT_STORAGE.list_keys(suffix="-lot-book.json"):
        name = Path(key).name

        try:
            period_start = date.fromisoformat(name[:7] + "-01")
        except (TypeError, ValueError):
            continue

        if name != f"{period_start:%Y-%m}-lot-book.json":
            continue

        if period_start.month == 12:
            next_month = date(period_start.year + 1, 1, 1)
        else:
            next_month = date(period_start.year, period_start.month + 1, 1)

        period_end = next_month - date.resolution

        if is_month_published_in_storage(
            ARTIFACT_STORAGE,
            period_end=period_end,
        ):
            period_ends.append(period_end)

    return tuple(sorted(period_ends))


def _render_analytics_data_status(*, analytics_period_end: date) -> None:
    """Render operator-facing analytics and authoritative-state currency."""

    authoritative_periods = _authoritative_lot_period_ends()
    latest_authoritative = (
        authoritative_periods[-1] if authoritative_periods else None
    )

    st.markdown("#### Data Status")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Analytics through",
        analytics_period_end.strftime("%B %Y"),
    )

    col2.metric(
        "Latest authoritative month",
        (
            latest_authoritative.strftime("%B %Y")
            if latest_authoritative is not None
            else "Not available"
        ),
    )

    col3.metric(
        "Next expected monthly import",
        (
            _next_month(latest_authoritative).strftime("%B %Y")
            if latest_authoritative is not None
            else "Bootstrap required"
        ),
    )


def _monthly_import_operator_state(
    *,
    year: int,
    month: int,
):
    """Describe authoritative readiness for the selected import month."""

    selected_start = _month_start(year, month)
    predecessor = load_preceding_authoritative_state_from_storage(
        ARTIFACT_STORAGE,
        period_start=selected_start,
    )

    authoritative_periods = _authoritative_lot_period_ends()
    latest = authoritative_periods[-1] if authoritative_periods else None
    expected_start = _next_month(latest) if latest is not None else None

    return latest, expected_start, predecessor


def _render_monthly_import_operator_state(
    *,
    year: int,
    month: int,
):
    """Render operator-facing authoritative-state readiness."""

    selected_start = _month_start(year, month)
    latest, expected_start, predecessor = _monthly_import_operator_state(
        year=year,
        month=month,
    )

    st.markdown("#### Current authoritative state")

    if latest is None:
        st.info(
            "No authoritative month-end position state is available yet. "
            "This will be a bootstrap import."
        )
    else:
        st.write(f"Latest authoritative month: **{latest:%B %Y}**")

        if expected_start is not None:
            st.write(
                f"Next month to process: **{expected_start:%B %Y}**"
            )

    if predecessor is not None:
        predecessor_end, _, _ = predecessor
        st.success(
            f"Opening position state available from "
            f"{predecessor_end:%B %Y}."
        )
    else:
        st.warning(
            f"No authoritative opening position state is available for "
            f"{selected_start:%B %Y}."
        )

    if expected_start is not None and selected_start == expected_start:
        st.success(
            f"{selected_start:%B %Y} is the next expected monthly import."
        )
    elif expected_start is not None:
        st.warning(
            f"You selected {selected_start:%B %Y}; the next expected "
            f"monthly import is {expected_start:%B %Y}. "
            "Historical backfill should be handled separately from the normal monthly workflow."
        )

    return predecessor


def _display_date_range(start: date, end: date) -> str:
    """Format a monthly document date range for an operator."""

    if start.year == end.year and start.month == end.month:
        return f"{start:%B} {start.day}–{end.day}, {end.year}"

    if start.year == end.year:
        return (
            f"{start:%B} {start.day}–"
            f"{end:%B} {end.day}, {end.year}"
        )

    return (
        f"{start:%B} {start.day}, {start.year}–"
        f"{end:%B} {end.day}, {end.year}"
    )


def _monthly_document_guidance(
    role,
    *,
    period_start: date,
    period_end: date,
) -> str:
    """Describe the period CampaignIQ expects for one monthly document."""

    if role == MonthlyInputRole.THINKORSWIM_TRADE_HISTORY:
        return (
            "Required • "
            + _display_date_range(period_start, period_end)
        )

    if role == "schwab_brokerage_statement":
        return (
            "Required • Schwab statement for "
            + _display_date_range(period_start, period_end)
            + "; used to reconcile month-end positions"
        )

    if role == MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS:
        return (
            "Required • "
            + _display_date_range(period_start, period_end)
        )

    if role == MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT:
        forex_start = period_start - date.resolution
        return (
            "Required • "
            + _display_date_range(forex_start, period_end)
        )

    if role == MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT:
        previous_day = period_start - date.resolution
        opening_start = previous_day.replace(day=1)
        return (
            "Required for bootstrap • "
            + _display_date_range(opening_start, previous_day)
        )

    return ""

def _display_instrument(instrument) -> str:
    """Format an instrument for operator-facing reconciliation output."""

    if isinstance(instrument, OptionContract):
        strike = format(instrument.strike, "f").rstrip("0").rstrip(".")
        option_type = instrument.option_type.value.title()
        return (
            f"{instrument.underlying} "
            f"{instrument.expiration:%b %d %Y} "
            f"${strike} {option_type}"
        )

    if isinstance(instrument, Instrument):
        return instrument.symbol

    return str(instrument)


def render_monthly_import_wizard():
    st.subheader("Monthly Import")
    st.caption(
        "Choose the month and supply the four monthly brokerage documents. "
        "When opening inventory is not yet available, also supply the prior "
        "month-end Schwab Brokerage Statement (five files in total). "
        "CampaignIQ processes them against the authoritative position state. "
        "Validation is read-only; authoritative data is persisted only after "
        "successful preflight, explicit confirmation, and execution. Closing "
        "inventory must reconcile unless the operator separately documents "
        "and approves an exact boundary-timing exception."
    )

    authoritative_periods = _authoritative_lot_period_ends()

    if authoritative_periods:
        default_start = _next_month(authoritative_periods[-1])
    else:
        default_start = date.today().replace(day=1)

    year_col, month_col = st.columns(2)
    year = year_col.number_input(
        "Year",
        min_value=2020,
        max_value=2100,
        value=default_start.year,
        step=1,
        key="monthly_import_year",
    )
    month = month_col.selectbox(
        "Month",
        options=range(1, 13),
        index=default_start.month - 1,
        format_func=lambda value: date(2000, value, 1).strftime("%B"),
        key="monthly_import_month",
    )

    predecessor = _render_monthly_import_operator_state(
        year=int(year),
        month=int(month),
    )

    st.markdown("#### 1. Supply monthly documents")
    if predecessor is None:
        prior_month_end = _month_start(int(year), int(month)) - date.resolution
        st.caption(
            f"Supply five required files: the four monthly documents for "
            f"{_month_start(int(year), int(month)):%B %Y}, plus the "
            f"{prior_month_end:%B %Y} Schwab Brokerage Statement to establish "
            "opening inventory."
        )
    else:
        st.caption("Supply the four brokerage documents for the selected month.")

    selected_period_start = _month_start(int(year), int(month))
    if selected_period_start.month == 12:
        selected_next_month = date(
            selected_period_start.year + 1,
            1,
            1,
        )
    else:
        selected_next_month = date(
            selected_period_start.year,
            selected_period_start.month + 1,
            1,
        )
    selected_period_end = selected_next_month - date.resolution

    uploads = {}

    for role, label, file_types in MONTHLY_UPLOAD_ROLES:
        if (
            role == MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
            and predecessor is not None
        ):
            continue

        st.markdown(f"**{label}**")
        guidance = _monthly_document_guidance(
            role,
            period_start=selected_period_start,
            period_end=selected_period_end,
        )
        if guidance:
            st.caption(guidance)

        uploads[role] = st.file_uploader(
            "Upload",
            type=list(file_types),
            key=f"monthly_import_{role}",
            label_visibility="collapsed",
        )

    if predecessor is not None:
        predecessor_end, _, _ = predecessor
        st.info(
            "Prior month-end Schwab Brokerage Statement: not required. "
            f"CampaignIQ will use the authoritative "
            f"{predecessor_end:%B %Y} opening state."
        )
    else:
        st.info(
            "A prior month-end Schwab Brokerage Statement is required when "
            "CampaignIQ cannot load an authoritative predecessor state."
        )

    st.markdown("#### 2. Validate")
    st.caption(
        "Validation checks the supplied documents and opening state. "
        "It does not persist authoritative monthly data."
    )

    signature = _monthly_import_signature(
        year=year,
        month=month,
        uploads=uploads,
    )

    required_upload_roles = tuple(
        role
        for role, _, _ in MONTHLY_UPLOAD_ROLES
        if role != MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT
    )
    required_uploads_supplied = all(
        uploads.get(role) is not None
        for role in required_upload_roles
    )

    if st.button(
        "Validate monthly import",
        type="primary",
        key="monthly_import_validate",
        disabled=not required_uploads_supplied,
    ):
        st.session_state["monthly_import_validated_signature"] = signature
        st.session_state["monthly_import_confirm"] = False

    validated_signature = st.session_state.get(
        "monthly_import_validated_signature"
    )
    if validated_signature != signature:
        if validated_signature is not None:
            st.info(
                "The month or an uploaded document changed. "
                "Validate the monthly import again."
            )
        else:
            if predecessor is None:
                prior_month_end = selected_period_start - date.resolution
                st.info(
                    f"Supply all five required files above: four monthly documents "
                    f"for {selected_period_start:%B %Y} and the "
                    f"{prior_month_end:%B %Y} Schwab Brokerage Statement. "
                    "Then select Validate monthly import."
                )
            else:
                st.info(
                    "Supply the four monthly documents above, then validate. "
                    "CampaignIQ will use the existing opening inventory."
                )
        return

    # Streamlit reruns the script for checkbox/button interactions. Recreate
    # the temporary files and preflight on every validated rerun so Confirm
    # and Execute always operate on exactly the documents that were validated.
    with tempfile.TemporaryDirectory(prefix="campaigniq-monthly-import-") as tmp:
        supplied = _save_uploaded_monthly_inputs(
            upload_dir=Path(tmp),
            uploads=uploads,
        )

        try:
            preflight = prepare_monthly_import(
                int(year),
                int(month),
                authoritative_state_root=AUTHORITATIVE_STATE_DIR,
                supplied_inputs=supplied,
                artifact_storage=ARTIFACT_STORAGE,
            )
        except Exception as exc:
            st.error(f"Unable to validate monthly import: {exc}")
            return

        _show_monthly_preflight(preflight)
        if preflight.validation_for(MonthlyInputRole.THINKORSWIM_TRADE_HISTORY).valid:
            try:
                crypto_preview = read_crypto_report(
                    supplied[MonthlyInputRole.THINKORSWIM_TRADE_HISTORY],
                    period_start=preflight.contract.period_start,
                    period_end=preflight.contract.period_end,
                )
                if crypto_preview is not None:
                    with st.expander("Review crypto activity from the same export", expanded=True):
                        render_crypto_report(build_crypto_month(
                            crypto_preview,
                            load_preceding_crypto(ARTIFACT_STORAGE, preflight.contract.period_start),
                        ), st)
            except (ValueError, OSError) as exc:
                st.error(f"Unable to review crypto evidence: {exc}")
                return

        if not preflight.ready:
            st.error(
                "Monthly import is not ready. Resolve the required items above "
                "before running the import."
            )
            return

        st.success(
            f"{preflight.contract.period_start:%B %Y} is ready to process."
        )

        st.markdown("##### What happens during finalization")
        st.write(
            "CampaignIQ will reconstruct the selected month's activity and "
            "compare the computed month-end positions with the Schwab "
            "closing-position snapshot. Normally, the month is published "
            "only when closing inventory reconciles."
        )
        st.caption(
            "If reconciliation fails because independently reviewed evidence "
            "shows a statement-boundary timing difference, CampaignIQ can "
            "record a documented boundary exception in a separate second "
            "step. An unexplained or missing economic event must be corrected "
            "instead of overridden."
        )
        st.info(
            "Nothing has been published yet. Validation and review are "
            "read-only."
        )

        st.markdown(
            f"#### 4. Finalize {preflight.contract.period_start:%B %Y}"
        )
        st.caption(
            "Finalization runs the monthly import and may publish a new "
            "authoritative CampaignIQ month. If closing inventory does not "
            "reconcile, the ordinary finalization attempt remains blocked "
            "and CampaignIQ will show the exact discrepancies for review."
        )

        confirm = st.checkbox(
            f"I understand that finalizing "
            f"{preflight.contract.period_start:%B %Y} may publish it as "
            "authoritative CampaignIQ data.",
            key="monthly_import_confirm",
        )
        if not confirm:
            return

        if not st.button(
            f"Finalize {preflight.contract.period_start:%B %Y}",
            type="primary",
            key="monthly_import_execute",
        ):
            return

        execution_signature = _monthly_import_signature(
            year=year,
            month=month,
            uploads=uploads,
        )
        if execution_signature != st.session_state.get(
            "monthly_import_validated_signature"
        ):
            st.error(
                "The import inputs changed after validation. "
                "Validate the monthly import again."
            )
            return

        try:
            execution = execute_monthly_import(
                preflight,
                authoritative_state_root=AUTHORITATIVE_STATE_DIR,
                supplied_inputs=supplied,
                artifact_storage=ARTIFACT_STORAGE,
                historical_source_root=HISTORICAL_SOURCE_ROOT,
            )
        except Exception as exc:
            st.error(f"Monthly import failed: {exc}")
            return

        if not execution.closing_reconciliation.reconciled:
            st.error(
                "Closing inventory does not reconcile. "
                "The ordinary finalization attempt did not publish the month."
            )
            st.caption(
                "Brokerage positions are checked against the closing statement. "
                "FOREX positions are checked against the monthly FOREX report's "
                "Total Position; pairs without activity retain the published opening quantity."
            )
            st.dataframe(
                [
                    {
                        "Instrument": _display_instrument(item.instrument),
                        "Computed": str(item.computed_quantity),
                        "Snapshot": str(item.snapshot_quantity),
                        "Difference": str(item.difference),
                    }
                    for item in execution.closing_reconciliation.mismatches
                ],
                use_container_width=True,
            )

            st.markdown("##### Document a boundary timing exception")
            st.warning(
                "Use this only when independent evidence shows that every "
                "discrepancy above is caused by statement-boundary timing and "
                "the transaction-derived closing state is the state that "
                "should carry forward. Do not use this for missing trades, "
                "assignments, IPO allocations, transfers, or other economic "
                "events that still need to be reconstructed."
            )

            use_exception = st.checkbox(
                "I have reviewed every mismatch above and believe all of them "
                "are explained by statement-boundary timing.",
                key="monthly_import_boundary_exception_requested",
            )

            if not use_exception:
                return

            exception_reason = st.text_area(
                "Why is this a boundary timing difference?",
                key="monthly_import_boundary_exception_reason",
                help=(
                    "Explain why the transaction-derived closing state should "
                    "carry forward even though the supplied month-end statement "
                    "shows different positions."
                ),
            )

            exception_evidence = st.text_area(
                "Supporting evidence",
                key="monthly_import_boundary_exception_evidence",
                help=(
                    "Identify the independent evidence you reviewed, such as "
                    "dated broker transaction history. Enter one item per line."
                ),
            )

            evidence_items = tuple(
                line.strip()
                for line in exception_evidence.splitlines()
                if line.strip()
            )

            exception_approved = st.checkbox(
                "I approve carrying forward the transaction-derived closing "
                "state for these exact mismatches and understand that the "
                "closing reconciliation itself remains failed.",
                key="monthly_import_boundary_exception_approved",
            )

            exception_ready = (
                bool(exception_reason.strip())
                and bool(evidence_items)
                and exception_approved
            )

            if not exception_ready:
                st.info(
                    "A reason, supporting evidence, and explicit approval are "
                    "required before exceptional finalization is available."
                )
                return

            if not st.button(
                "Finalize with documented boundary exception",
                type="primary",
                key="monthly_import_execute_boundary_exception",
            ):
                return

            decision = ReconciliationDecision(
                period_start=preflight.contract.period_start,
                period_end=preflight.contract.period_end,
                decision_type=BOUNDARY_TIMING_EXCEPTION,
                resolution=ACCEPT_TRANSACTION_DERIVED_STATE,
                reason=exception_reason.strip(),
                evidence=evidence_items,
                mismatches=capture_reconciliation_mismatches(
                    execution.closing_reconciliation.mismatches
                ),
                approved=True,
            )

            try:
                execution = execute_monthly_import(
                    preflight,
                    authoritative_state_root=AUTHORITATIVE_STATE_DIR,
                    supplied_inputs=supplied,
                    artifact_storage=ARTIFACT_STORAGE,
                    historical_source_root=HISTORICAL_SOURCE_ROOT,
                    reconciliation_decision=decision,
                )
            except Exception as exc:
                st.error(
                    f"Exceptional monthly finalization failed: {exc}"
                )
                return

            if not execution.finalized:
                st.error(
                    "CampaignIQ did not publish the month. The reconciliation "
                    "result changed or the documented exception no longer "
                    "matches the exact current discrepancies. Review the "
                    "current inputs and run finalization again."
                )
                return

            if execution.reconciliation_decision is None:
                st.error(
                    "CampaignIQ did not record the required reconciliation "
                    "decision. The month was not accepted as an exceptional "
                    "finalization."
                )
                return

            st.warning(
                "This month was finalized with a documented boundary timing "
                "exception. Closing inventory did not reconcile; the "
                "transaction-derived closing state was carried forward under "
                "the persisted operator decision."
            )

        boundary = execution.result.boundary_reconstruction
        if (
            boundary.unresolved_positions
            or boundary.unresolved_campaigns
            or boundary.historical_requirements
        ):
            st.warning(
                "This month was finalized with incomplete historical ancestry. "
                "The positions remain tracked, but unsupported campaign "
                "attribution is excluded from campaign analytics."
            )

            if boundary.unresolved_positions:
                st.write(
                    "Unresolved positions: "
                    + ", ".join(boundary.unresolved_positions)
                )

            if boundary.unresolved_campaigns:
                st.write(
                    f"Unresolved campaigns: {len(boundary.unresolved_campaigns)}"
                )

            if boundary.historical_requirements:
                st.write(
                    "Additional historical documents could improve these results:"
                )
                for requirement in boundary.historical_requirements:
                    months = ", ".join(requirement.months)
                    documents = ", ".join(requirement.document_types)
                    st.write(
                        f"- {requirement.case_id}: {months} — "
                        f"{documents}. {requirement.reason}"
                    )

        if execution.crypto_report is not None:
            render_crypto_report(execution.crypto_report, st)

        forex_report = execution.result.forex_transaction_report
        if forex_report is not None:
            st.markdown("#### FOREX Settlement Control")
            st.write(
                f"Broker MTD Settled P&L: ${forex_report.mtd_settled_pl_usd:,.2f}"
            )
            st.write(
                f"Parsed settlement-row P&L: ${forex_report.settlement_pl_usd:,.2f}"
            )
            if execution.forex_settlement_control_reconciled:
                st.success("FOREX settlement control reconciled exactly.")
            else:
                st.warning(
                    "FOREX settlement control difference: "
                    f"${execution.forex_settlement_control_delta_usd:,.2f}. "
                    "This difference is reported for review and did not block "
                    "monthly finalization."
                )

            if execution.forex_pl_total_control_reconciled is not None:
                st.write(f"Report PL Total: ${forex_report.pl_total_usd:,.2f}")
                if execution.forex_pl_total_control_reconciled:
                    st.success("FOREX PL Total control reconciled exactly.")
                else:
                    st.warning(
                        "FOREX PL Total control difference: "
                        f"${execution.forex_pl_total_control_delta_usd:,.2f}. "
                        "This difference is reported for review and did not block monthly finalization."
                    )

            if execution.forex_commission_control_reconciled is not None:
                st.write(f"Report Commission Total: ${forex_report.commission_total_usd:,.2f}")
                st.write(f"Parsed transaction fees: ${forex_report.transaction_fee_usd:,.2f}")
                if execution.forex_commission_control_reconciled:
                    st.success("FOREX commission control reconciled exactly.")
                else:
                    st.warning(
                        "FOREX commission control difference: "
                        f"${execution.forex_commission_control_delta_usd:,.2f}. "
                        "This difference is reported for review and did not block monthly finalization."
                    )

            if execution.forex_financing_control_reconciled is not None:
                if execution.forex_financing_control_reconciled:
                    st.success("FOREX financing controls reconciled exactly.")
                else:
                    differences = ", ".join(
                        f"{instrument}: ${delta:,.2f}"
                        for instrument, delta in execution.forex_financing_control_deltas_usd
                        if delta != 0
                    )
                    st.warning(
                        "FOREX financing control differences: "
                        f"{differences}. This difference is reported for review and did not block monthly finalization."
                    )

        if execution.reconciliation_decision is None:
            st.success(
                f"{preflight.contract.period_start:%B %Y} finalized successfully. "
                "Closing inventory reconciled and the authoritative month was "
                "published."
            )
        else:
            st.success(
                f"{preflight.contract.period_start:%B %Y} finalized with a "
                "documented boundary timing exception and the authoritative "
                "month was published."
            )
        st.caption(
            f"Authoritative state: {execution.authoritative_state_path}"
        )



def _published_artifact_keys(*, suffix: str):
    keys = []
    for key in ARTIFACT_STORAGE.list_keys(suffix=suffix):
        month_text = key.removesuffix(suffix)
        try:
            month_start = date.fromisoformat(f"{month_text}-01")
        except ValueError:
            continue
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        period_end = next_month - date.resolution
        if is_month_published_in_storage(ARTIFACT_STORAGE, period_end=period_end):
            keys.append(key)
    return tuple(keys)


def load_summaries():
    """Load authoritative persisted periods and calculate monthly analytics."""
    realized_keys = _published_artifact_keys(suffix="-realized-attributions.json")
    forex_keys = _published_artifact_keys(suffix="-forex-settlement-attributions.json")

    if not realized_keys:
        raise FileNotFoundError(
            f"No persisted attribution files found in {AUTHORITATIVE_STATE_DIR}"
        )

    monthly_attributions, monthly_forex_attributions = (
        load_persisted_monthly_campaign_attributions_from_storage(
            ARTIFACT_STORAGE,
            realized_keys=realized_keys,
            forex_keys=forex_keys,
        )
    )

    summaries = summarize_multi_month_analytics(
        monthly_attributions=monthly_attributions,
        monthly_forex_attributions=monthly_forex_attributions,
    )

    return summaries, monthly_attributions, monthly_forex_attributions


st.set_page_config(
    page_title="CampaignIQ",
    page_icon="📈",
    layout="wide",
)

st.title("CampaignIQ")

if authentication_mode() == AUTH_MODE_OIDC:
    if st.sidebar.button("Sign out", key="campaigniq_sign_out"):
        st.logout()

# CAMPAIGNIQ_UI_STAGE1
navigation_views = ("Overview", "Campaigns", "Performance", "Positions", "Data", "Crypto")
if is_access_admin(ACCESS):
    navigation_views += ("Access History",)
view = st.sidebar.radio(
    "Navigate",
    navigation_views,
    key="campaigniq_primary_view",
)

if view == "Access History":
    if not is_access_admin(ACCESS):
        st.error("Administrator access is required.")
        st.stop()
    st.subheader("Access History")
    st.caption("Authorized browser sessions recorded since access logging was enabled. Times are UTC.")
    st.caption("Opening a new browser session counts as a visit; changing filters does not.")
    if ACCESS_HISTORY_ERROR:
        st.warning(ACCESS_HISTORY_ERROR)
    st.button("Refresh history", key="campaigniq_refresh_access_history")
    try:
        access_rows = load_access_history(ACCESS, ACCESS_HISTORY_STORAGE)
    except Exception:
        st.error("Access history could not be loaded. Check the service storage configuration.")
    else:
        if access_rows:
            access_df = pd.DataFrame(access_rows)
            st.metric("Recorded visits", len(access_df))
            st.markdown("#### Last visit by user")
            st.dataframe(access_df.drop_duplicates(subset=["Identity"]),
                         use_container_width=True, hide_index=True)
            st.markdown("#### Recent visits")
            st.dataframe(access_df.head(500), use_container_width=True, hide_index=True)
            st.caption("The recent visits table shows up to 500 sessions.")
        else:
            st.info("No visits have been recorded yet.")
    st.stop()

if view == "Crypto":
    crypto_keys = _published_artifact_keys(suffix="-crypto.json")
    if not crypto_keys:
        st.subheader("Crypto")
        st.info("Crypto records will appear after a monthly import containing crypto evidence is finalized. Upload your original Account Trade History through Data; no extra crypto file is required.")
    else:
        crypto_key = st.selectbox(
            "Published crypto month", options=tuple(reversed(crypto_keys)),
            format_func=lambda key: Path(key).name[:7], key="crypto_month",
        )
        report = json.loads(ARTIFACT_STORAGE.read_text(crypto_key))
        if report.get("format") != "campaigniq.crypto_month" or report.get("version") != 1:
            st.error("Unsupported crypto artifact format.")
        else:
            render_crypto_report(report, st)
    st.stop()

if view == "Data":
    st.subheader("Data")
    st.caption("Published periods and monthly import")
    try:
        realized_keys = _published_artifact_keys(suffix="-realized-attributions.json")
        forex_keys = _published_artifact_keys(suffix="-forex-settlement-attributions.json")
        lot_periods = _authoritative_lot_period_ends()
    except Exception as exc:
        st.warning(f"Published data status is unavailable: {exc}")
    else:
        realized_months = {Path(key).name[:7] for key in realized_keys}
        forex_months = {Path(key).name[:7] for key in forex_keys}
        lot_months = {f"{period:%Y-%m}" for period in lot_periods}
        all_months = sorted(realized_months | forex_months | lot_months, reverse=True)
        latest_analytics = max(realized_months) if realized_months else None
        latest_lots = lot_periods[-1] if lot_periods else None
        status1, status2, status3 = st.columns(3)
        status1.metric("Realized analytics through", latest_analytics or "Not available")
        status2.metric("Authoritative lots through", f"{latest_lots:%B %Y}" if latest_lots else "Not available")
        status3.metric("Next expected import", _next_month(latest_lots).strftime("%B %Y") if latest_lots else "Bootstrap required")
        with st.expander("Published data by month"):
            if all_months:
                st.dataframe(pd.DataFrame([{
                    "Month": month,
                    "Realized attributions": "Published" if month in realized_months else "—",
                    "FOREX settlements": "Published" if month in forex_months else "—",
                    "Authoritative lots": "Published" if month in lot_months else "—",
                } for month in all_months]), use_container_width=True, hide_index=True)
            else:
                st.info("No published periods are available yet.")
            st.caption("A blank FOREX cell means no published FOREX attribution artifact for that month; it does not by itself indicate an import failure.")
    st.subheader("Attribution Status")
    try:
        published_summaries, _, _ = load_summaries()
    except FileNotFoundError:
        st.info("Attribution status will appear after the first published import.")
    except Exception as exc:
        st.warning(f"Unable to load attribution status: {exc}")
    else:
        total_unattributed_data = sum(
            summary.realized_pnl.unattributed_record_count
            for summary in published_summaries
        )
        st.metric("Unattributed broker records", f"{total_unattributed_data:,}")
        if total_unattributed_data == 0:
            st.success("All published broker realized records are attributed to campaigns.")
        else:
            st.warning("Review months with unattributed records before interpreting campaign results as complete.")
        with st.expander("Broker attribution by month"):
            st.dataframe(pd.DataFrame([{
                "Month": summary.period_start.strftime("%B %Y"),
                "Broker records": summary.realized_pnl.broker_record_count,
                "Unattributed": summary.realized_pnl.unattributed_record_count,
            } for summary in published_summaries]), use_container_width=True, hide_index=True)
    st.divider()
    render_monthly_import_wizard()
    st.stop()

if view == "Performance":
    st.caption("Realized campaign analytics")

try:
    summaries, monthly_attributions, monthly_forex_attributions = load_summaries()
    lifecycle_history = load_lifecycle_history_from_storage(ARTIFACT_STORAGE)
    lifecycle_summary = summarize_lifecycle_history(lifecycle_history)
except Exception as exc:
    st.error(f"Unable to load CampaignIQ analytics: {exc}")
    st.stop()

if not summaries:
    st.warning("No analytics periods are available.")
    st.stop()

multi_month_performance = summarize_multi_month_performance(summaries)
multi_month_campaign_performance = summarize_multi_month_campaign_performance(
    summary.campaign_performance
    for summary in summaries
)

campaign_results, campaign_drilldowns = (
    aggregate_period_qualified_campaigns(
        monthly_attributions,
        monthly_forex_attributions,
    )
)
campaign_outcomes = summarize_campaign_outcomes(campaign_results)
underlying_performance = summarize_underlying_performance(monthly_attributions)
repeated_campaign_performance = summarize_repeated_campaign_performance(
    monthly_attributions
)
realized_drawdown = summarize_period_qualified_realized_drawdown(
    monthly_attributions
)
maximum_drawdown_contributions = summarize_maximum_drawdown_contributions(
    monthly_attributions
)

rows = []

for summary in summaries:
    pnl = summary.realized_pnl
    performance = summary.campaign_performance

    rows.append(
        {
            "period_start": summary.period_start,
            "period_end": summary.period_end,
            "month": summary.period_start.strftime("%B"),
            "equity_options_realized_pnl": float(summary.equity_options_realized_pnl),
            "forex_settled_pnl": float(summary.forex_settled_pnl),
            "realized_pnl": float(summary.combined_realized_pnl),
            "campaigns": performance.campaign_count,
            "wins": performance.winning_campaign_count,
            "losses": performance.losing_campaign_count,
            "breakeven": performance.breakeven_campaign_count,
            "win_rate": (
                float(performance.win_rate)
                if performance.win_rate is not None
                else 0.0
            ),
            "records": pnl.broker_record_count,
            "unattributed_records": pnl.unattributed_record_count,
        }
    )

df = pd.DataFrame(rows)

total_pnl = sum(
    (summary.realized_pnl.broker_realized_pnl for summary in summaries),
    Decimal("0"),
)
total_campaigns = sum(
    summary.campaign_performance.campaign_count
    for summary in summaries
)
total_wins = sum(
    summary.campaign_performance.winning_campaign_count
    for summary in summaries
)
total_records = sum(
    summary.realized_pnl.broker_record_count
    for summary in summaries
)
total_unattributed = sum(
    summary.realized_pnl.unattributed_record_count
    for summary in summaries
)

overall_win_rate = (
    Decimal(total_wins) / Decimal(total_campaigns)
    if total_campaigns
    else Decimal("0")
)

first_period = summaries[0].period_start.strftime("%B %Y")
last_period = summaries[-1].period_end.strftime("%B %Y")

if view == "Performance":
    st.subheader(f"{first_period} – {last_period}")
    _render_analytics_data_status(
        analytics_period_end=summaries[-1].period_end,
    )

forex_settled_pnl = sum(
    (
        attribution.gain_loss
        for attributions in monthly_forex_attributions.values()
        for attribution in attributions
    ),
    Decimal("0"),
)
equity_options_realized_pnl = sum(
    (summary.equity_options_realized_pnl for summary in summaries),
    Decimal("0"),
)
combined_realized_pnl = equity_options_realized_pnl + forex_settled_pnl


if view == "Overview":
    st.subheader("Portfolio Overview")
    st.caption(f"{first_period} – {last_period} · Published analytics through {summaries[-1].period_end:%B %Y}")
    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Realized P&L", money(combined_realized_pnl))
    o2.metric("Campaigns", f"{multi_month_campaign_performance.campaign_count:,}")
    win_rate = multi_month_campaign_performance.win_rate
    o3.metric("Campaign Win Rate", f"{float(win_rate):.1%}" if win_rate is not None else "—")
    o4.metric(
        "Profitable Months",
        f"{multi_month_performance.profitable_month_count:,} / {multi_month_performance.month_count:,}",
    )

    st.subheader("Monthly Realized P&L")
    monthly_chart_data = df[["period_start", "realized_pnl"]].copy()
    monthly_chart_data["month_label"] = monthly_chart_data["period_start"].map(
        lambda value: value.strftime("%b %Y")
    )
    monthly_chart_data["result"] = monthly_chart_data["realized_pnl"].map(
        lambda value: "Gain" if value >= 0 else "Loss"
    )
    monthly_chart = (
        alt.Chart(monthly_chart_data)
        .mark_bar()
        .encode(
            x=alt.X(
                "month_label:N",
                sort=monthly_chart_data["month_label"].tolist(),
                title="Reporting month",
                axis=alt.Axis(labelAngle=0),
            ),
            y=alt.Y("realized_pnl:Q", title="Realized P&L (USD)"),
            color=alt.Color(
                "result:N",
                scale=alt.Scale(domain=["Gain", "Loss"], range=["#287d59", "#c44949"]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("month_label:N", title="Month"),
                alt.Tooltip("realized_pnl:Q", title="Realized P&L", format="$,.2f"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(monthly_chart, use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader("Campaign Results")
        st.metric("Average Winner", money(multi_month_campaign_performance.average_win) if multi_month_campaign_performance.average_win is not None else "—")
        st.metric("Average Loser", money(multi_month_campaign_performance.average_loss) if multi_month_campaign_performance.average_loss is not None else "—")
        ratio = multi_month_campaign_performance.payoff_ratio
        st.metric("Payoff Ratio", f"{ratio:.2f}×" if ratio is not None else "—")
    with right:
        st.subheader("Attention")
        st.metric("Unattributed Broker Records", f"{total_unattributed:,}")
        st.metric("Worst Campaign", money(campaign_outcomes.worst_campaign_pnl) if campaign_outcomes.worst_campaign_pnl is not None else "—")
        st.metric("Data Through", f"{summaries[-1].period_end:%B %Y}")

    st.subheader("Recent Campaigns")
    recent = sorted(campaign_drilldowns, key=lambda item: item.last_closed_date, reverse=True)[:10]
    st.dataframe(pd.DataFrame([{
        "Symbols": ", ".join(item.symbols), "Campaign": item.campaign_id,
        "Realized P&L": float(item.realized_pnl), "Last Close": item.last_closed_date,
    } for item in recent]).style.format({"Realized P&L": "{:,.2f}"}), use_container_width=True, hide_index=True)
    if recent:
        recent_id = st.selectbox(
            "Open a recent campaign",
            [item.campaign_id for item in recent],
            format_func=lambda campaign_id: next(
                f"{', '.join(item.symbols)} · {campaign_id} · {money(item.realized_pnl)}"
                for item in recent if item.campaign_id == campaign_id
            ),
            key="campaigniq_overview_recent_campaign",
        )

        def open_recent_campaign(campaign_id):
            # Callbacks run before the next Streamlit rerun creates the widgets.
            st.session_state["campaigniq_campaign_symbol"] = "All symbols"
            st.session_state["campaigniq_campaign_result"] = "All results"
            st.session_state["campaigniq_campaign_detail"] = campaign_id
            st.session_state["campaigniq_primary_view"] = "Campaigns"

        st.button(
            "View campaign detail",
            on_click=open_recent_campaign,
            args=(recent_id,),
            key="campaigniq_open_recent_campaign",
        )
    st.caption("Realized P&L combines equity/options realized results and settled FOREX; financing and interest are excluded. Campaign counts, win rate, and economics follow the existing campaign performance summary.")
    st.stop()

if view == "Campaigns":
    st.subheader("Campaigns")
    st.caption(f"Realized campaigns · {first_period} – {last_period}")
    if not campaign_drilldowns:
        st.info("No realized campaigns are available for these periods.")
        st.stop()

    symbol_options = sorted({symbol for item in campaign_drilldowns for symbol in item.symbols})
    filter1, filter2 = st.columns(2)
    selected_symbol = filter1.selectbox("Symbol", ["All symbols", *symbol_options], key="campaigniq_campaign_symbol")
    selected_result = filter2.selectbox("Result", ["All results", "Profit", "Loss", "Breakeven"], key="campaigniq_campaign_result")

    def matches_result(item):
        if selected_result == "Profit":
            return item.realized_pnl > 0
        if selected_result == "Loss":
            return item.realized_pnl < 0
        if selected_result == "Breakeven":
            return item.realized_pnl == 0
        return True

    visible = [
        item for item in campaign_drilldowns
        if (selected_symbol == "All symbols" or selected_symbol in item.symbols)
        and matches_result(item)
    ]
    visible.sort(key=lambda item: (item.last_closed_date, item.campaign_id), reverse=True)
    st.caption(f"{len(visible):,} of {len(campaign_drilldowns):,} realized campaigns")
    campaign_table = pd.DataFrame([{
        "Campaign": item.campaign_id,
        "Symbols": ", ".join(item.symbols),
        "Realized P&L": float(item.realized_pnl),
        "First Close": item.first_closed_date,
        "Last Close": item.last_closed_date,
        "Reconciled": "Yes" if item.fully_reconciled else "No",
    } for item in visible])
    st.dataframe(
        campaign_table.style.format({"Realized P&L": "{:,.2f}"}), use_container_width=True, hide_index=True,
        column_config={
            "First Close": st.column_config.DateColumn("First Close", format="MMM D, YYYY"),
            "Last Close": st.column_config.DateColumn("Last Close", format="MMM D, YYYY"),
        },
    )

    if visible:
        selected_id = st.selectbox(
            "Inspect campaign",
            [item.campaign_id for item in visible],
            format_func=lambda campaign_id: next(
                f"{', '.join(item.symbols)} · {campaign_id} · {money(item.realized_pnl)}"
                for item in visible if item.campaign_id == campaign_id
            ),
            key="campaigniq_campaign_detail",
        )
        selected = next(item for item in visible if item.campaign_id == selected_id)
        st.subheader("Campaign Detail")
        st.caption(f"{selected.campaign_id} · {', '.join(selected.symbols)}")
        d1, d2, d3 = st.columns(3)
        d1.metric("Realized P&L", money(selected.realized_pnl))
        d2.metric("Broker Records", f"{selected.record_count:,}")
        d3.metric("Lot Allocations", f"{selected.allocation_count:,}")
        st.write(f"**Realized close dates:** {selected.first_closed_date:%b %d, %Y} – {selected.last_closed_date:%b %d, %Y}")
        st.write(f"**Reconciliation:** {'Fully reconciled' if selected.fully_reconciled else 'Needs review'}")
        realized_records = campaign_realized_attributions(monthly_attributions, selected_id)
        if len(realized_records) != selected.record_count:
            st.warning("Campaign summary and underlying record counts differ; review the published data.")
        else:
            st.markdown("#### Broker Realized Records")
            st.dataframe(pd.DataFrame([{
                "Record": number,
                "Close Date": attribution.record.closed_date,
                "Instrument": _display_instrument(attribution.record.instrument),
                "Quantity": float(attribution.record.quantity),
                "Realized P&L": float(attribution.record.gain_loss),
                "Status": (
                    "OK" if attribution.basis_reconciled and attribution.gain_loss_reconciled
                    else "Review basis and P&L" if not attribution.basis_reconciled and not attribution.gain_loss_reconciled
                    else "Review basis" if not attribution.basis_reconciled
                    else "Review P&L"
                ),
            } for number, attribution in enumerate(realized_records, 1)]),
                use_container_width=True, hide_index=True,
                column_config={
                    "Record": st.column_config.NumberColumn("Record", width=80),
                    "Close Date": st.column_config.DateColumn("Close Date", format="MMM D, YYYY", width=125),
                    "Instrument": st.column_config.TextColumn("Instrument", width=330),
                    "Quantity": st.column_config.NumberColumn("Quantity", width=95),
                    "Realized P&L": st.column_config.NumberColumn("Realized P&L", format="$%0,.2f", width=140),
                    "Status": st.column_config.TextColumn("Status", width=150),
                },
            )
            with st.expander("Broker proceeds and cost basis"):
                st.dataframe(pd.DataFrame([{
                    "Record": number,
                    "Proceeds": float(attribution.record.proceeds),
                    "Cost Basis": float(attribution.record.cost_basis),
                    "Disallowed Loss": float(attribution.record.disallowed_loss),
                    "Basis Method": attribution.record.basis_method,
                    "Term": attribution.record.term,
                } for number, attribution in enumerate(realized_records, 1)]),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Proceeds": st.column_config.NumberColumn("Proceeds", format="$%0,.2f"),
                        "Cost Basis": st.column_config.NumberColumn("Cost Basis", format="$%0,.2f"),
                        "Disallowed Loss": st.column_config.NumberColumn("Disallowed Loss", format="$%0,.2f"),
                    },
                )
            with st.expander("Lot allocations for these closes"):
                st.dataframe(pd.DataFrame([{
                    "Record": number,
                    "Lot ID": allocation.lot_id,
                    "Quantity": float(allocation.quantity),
                    "Broker Basis": float(allocation.broker_basis) if allocation.broker_basis is not None else None,
                    "Basis Source": allocation.basis_source or "—",
                } for number, attribution in enumerate(realized_records, 1)
                    for allocation in attribution.allocations]),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Broker Basis": st.column_config.NumberColumn("Broker Basis", format="$%0,.2f"),
                    },
                )
        st.caption("This is broker realized close evidence, not a complete opening-trade or position history. Record numbers link the results, broker amounts, and lot allocations.")
        if len(selected.symbols) == 1:
            symbol = selected.symbols[0]
            lifecycle_symbols = {item.symbol for item in lifecycle_summary.symbols}
            if symbol in lifecycle_symbols:
                st.caption("The position timeline covers all published activity for this symbol, including other campaigns.")

                def open_symbol_lifecycle(underlying):
                    st.session_state["campaigniq_lifecycle_symbol"] = underlying
                    st.session_state["campaigniq_primary_view"] = "Positions"

                st.button(
                    f"View {symbol} position lifecycle",
                    on_click=open_symbol_lifecycle,
                    args=(symbol,),
                    key="campaigniq_campaign_to_positions",
                )
    st.stop()

if view == "Positions":
    st.subheader("Positions")
    st.caption("Published month-end open lots and observed lifecycle transitions")
    st.subheader("Published Month-End Positions")
    try:
        published_lot_periods = _authoritative_lot_period_ends()
        if published_lot_periods:
            selected_lot_period = st.selectbox(
                "Snapshot month",
                options=tuple(reversed(published_lot_periods)),
                format_func=lambda period: period.strftime("%B %Y"),
                key="campaigniq_position_snapshot_month",
            )
            persisted_lots = load_lot_book_from_storage(
                ARTIFACT_STORAGE,
                lot_state_key(period_end=selected_lot_period),
            )
            if persisted_lots.period_end != selected_lot_period:
                raise ValueError("Published lot-state period does not match the artifact date.")
        else:
            persisted_lots = None
    except Exception as exc:
        st.warning(f"Unable to load published open-lot inventory: {exc}")
    else:
        if persisted_lots is None:
            st.info("No published authoritative month-end lot inventory is available.")
        else:
            lot_book = persisted_lots.lot_book
            open_lots = [lot for instrument in lot_book.instruments() for lot in lot_book.lots(instrument)]
            groups = {}
            for lot in open_lots:
                groups.setdefault((lot.instrument, lot.side), []).append(lot)
            st.caption(f"As of {selected_lot_period:%B %d, %Y}. This is saved month-end lot state, not a live broker position feed.")
            inv1, inv2 = st.columns(2)
            inv1.metric("Open instruments", len(lot_book.instruments()))
            inv2.metric("Open lots", len(open_lots))
            if groups:
                position_rows = [{
                    "Instrument": _display_instrument(instrument),
                    "Side": side,
                    "Quantity": float(sum((abs(lot.quantity) for lot in lots), Decimal("0"))),
                    "Lots": len(lots),
                } for (instrument, side), lots in groups.items()]
                st.dataframe(
                    pd.DataFrame(position_rows).sort_values(["Instrument", "Side"]),
                    use_container_width=True, hide_index=True,
                )
                with st.expander("Open lot details"):
                    st.dataframe(pd.DataFrame([{
                        "Instrument": _display_instrument(lot.instrument),
                        "Side": lot.side,
                        "Quantity": float(lot.absolute_quantity),
                        "Lot ID": lot.lot_id,
                        "Opened": lot.opened_at.date(),
                        "Stored Basis": float(lot.basis_total) if lot.basis_total is not None else None,
                        "Campaign": lot.campaign_id or "Unassigned",
                    } for lot in open_lots]), use_container_width=True, hide_index=True)
            else:
                st.info("No open lots are present in the selected published state.")

            previous_index = published_lot_periods.index(selected_lot_period) - 1
            if previous_index >= 0:
                previous_period = published_lot_periods[previous_index]
                with st.expander(f"Position changes since {previous_period:%B %Y}"):
                    try:
                        previous = load_lot_book_from_storage(
                            ARTIFACT_STORAGE,
                            lot_state_key(period_end=previous_period),
                        )
                        if previous.period_end != previous_period:
                            raise ValueError("Previous lot-state period does not match its artifact date.")
                    except Exception as exc:
                        st.warning(f"Unable to compare published snapshots: {exc}")
                    else:
                        def quantities_by_instrument_and_side(book):
                            quantities = {}
                            for instrument in book.instruments():
                                for lot in book.lots(instrument):
                                    key = (instrument, lot.side)
                                    quantities[key] = quantities.get(key, Decimal("0")) + lot.absolute_quantity
                            return quantities

                        before = quantities_by_instrument_and_side(previous.lot_book)
                        after = quantities_by_instrument_and_side(lot_book)
                        changed = sorted(
                            (key for key in before.keys() | after.keys()
                             if before.get(key, Decimal("0")) != after.get(key, Decimal("0"))),
                            key=lambda key: (_display_instrument(key[0]), key[1]),
                        )
                        if changed:
                            st.dataframe(pd.DataFrame([{
                                "Instrument": _display_instrument(instrument),
                                "Side": side,
                                "Prior Quantity": float(before.get((instrument, side), Decimal("0"))),
                                "Selected Quantity": float(after.get((instrument, side), Decimal("0"))),
                                "Change": float(after.get((instrument, side), Decimal("0")) - before.get((instrument, side), Decimal("0"))),
                            } for instrument, side in changed]), use_container_width=True, hide_index=True)
                        else:
                            st.info("No open-quantity differences between these published snapshots.")
                        st.caption("These are differences between saved month-end quantities. They do not identify trades, splits, or other causes.")
    st.divider()
    st.subheader("Published Lifecycle Events")

    if lifecycle_summary.transition_count == 0:
        st.info(
            "No published lifecycle transitions are available yet."
        )
    else:
        lifecycle_metric_columns = st.columns(6)

        lifecycle_metric_columns[0].metric(
            "Transitions",
            f"{lifecycle_summary.transition_count:,}",
        )
        lifecycle_metric_columns[1].metric(
            "Symbols",
            f"{lifecycle_summary.symbol_count:,}",
        )
        lifecycle_metric_columns[2].metric(
            "Corporate Actions",
            f"{lifecycle_summary.corporate_action_count:,}",
        )
        lifecycle_metric_columns[3].metric(
            "Rolls",
            f"{lifecycle_summary.roll_count:,}",
        )
        lifecycle_metric_columns[4].metric(
            "Exits",
            f"{lifecycle_summary.exit_count:,}",
        )
        lifecycle_metric_columns[5].metric(
            "Assignments",
            f"{lifecycle_summary.assignment_count:,}",
        )

        lifecycle_by_symbol = {
            summary.symbol: summary
            for summary in lifecycle_summary.symbols
        }

        lifecycle_symbol = st.selectbox(
            "Underlying",
            options=tuple(lifecycle_by_symbol),
            key="campaigniq_lifecycle_symbol",
        )
        selected_lifecycle = lifecycle_by_symbol[lifecycle_symbol]

        st.markdown(f"#### {selected_lifecycle.symbol}")
        if lifecycle_symbol == "NFLX":
            with st.expander("Historical context: NFLX 10-for-1 split", expanded=True):
                st.write(
                    "Netflix announced a 10-for-1 forward stock split. "
                    "Split-adjusted trading was scheduled for November 17, 2025. "
                    "The supplied NFLX Transaction History shows an "
                    "'Options Frwd Split' entry dated November 17, 2025, "
                    "with a -45 quantity adjustment to the December $114 put."
                )
                st.link_button(
                    "Netflix split announcement",
                    "https://ir.netflix.net/investor-news-and-events/financial-releases/press-release-details/2025/Netflix-Announces-Ten-For-One-Stock-Split/default.aspx",
                )
                st.caption(
                    "Broker source: NFLX Transaction History.pdf, transaction "
                    "dated November 17, 2025. This historical context predates "
                    "the published 2026 lifecycle periods. It is not counted "
                    "as a published transition or used to change positions or P&L."
                )

        symbol_metric_columns = st.columns(4)
        symbol_metric_columns[0].metric(
            "Transitions",
            f"{selected_lifecycle.transition_count:,}",
        )
        symbol_metric_columns[1].metric(
            "Covered Positions",
            f"{selected_lifecycle.covered_position_count:,}",
        )
        symbol_metric_columns[2].metric(
            "Rolls",
            f"{selected_lifecycle.roll_count:,}",
        )
        symbol_metric_columns[3].metric(
            "Exits",
            f"{selected_lifecycle.exit_count:,}",
        )

        lifecycle_timeline_df = pd.DataFrame(
            lifecycle_timeline_rows(selected_lifecycle)
        )

        st.dataframe(
            lifecycle_timeline_df,
            hide_index=True,
            width="stretch",
            column_config={
                "Date": st.column_config.DateColumn(
                    "Date",
                    format="MMM D, YYYY",
                ),
                "Time": st.column_config.TimeColumn(
                    "Time",
                    format="HH:mm:ss",
                ),
            },
        )

        st.caption(
            "Authoritative lifecycle evidence from published monthly artifacts. "
            "This view summarizes observed corporate actions, assignments, "
            "covered positions, option rolls, and exits; it does not infer "
            "strategy intent or realized P&L."
        )

        related_campaigns = sorted(
            (item for item in campaign_drilldowns if lifecycle_symbol in item.symbols),
            key=lambda item: (item.last_closed_date, item.campaign_id),
            reverse=True,
        )
        st.subheader("Realized Campaigns for This Symbol")
        if not related_campaigns:
            st.info("No realized campaign summaries are available for this symbol in the selected published periods.")
        else:
            related_id = st.selectbox(
                "Open a campaign",
                [item.campaign_id for item in related_campaigns],
                format_func=lambda campaign_id: next(
                    f"{campaign_id} · {money(item.realized_pnl)} · {item.last_closed_date:%b %d, %Y}"
                    for item in related_campaigns if item.campaign_id == campaign_id
                ),
                key="campaigniq_positions_related_campaign",
            )

            def open_related_campaign(campaign_id, symbol):
                st.session_state["campaigniq_campaign_symbol"] = symbol
                st.session_state["campaigniq_campaign_result"] = "All results"
                st.session_state["campaigniq_campaign_detail"] = campaign_id
                st.session_state["campaigniq_primary_view"] = "Campaigns"

            st.button(
                "View campaign detail",
                on_click=open_related_campaign,
                args=(related_id, lifecycle_symbol),
                key="campaigniq_positions_to_campaign",
            )
        st.caption("Campaign summaries are keyed by realized closes; the lifecycle timeline above covers all published activity for this symbol.")
    st.stop()

summary_tab, campaigns_tab, risk_tab = st.tabs(
    ["Monthly Results", "Campaign Analysis", "Drawdown"]
)

with summary_tab:
    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric(
        "Combined Realized P&L",
        money(combined_realized_pnl),
    )
    metric2.metric(
        "Campaigns",
        f"{multi_month_campaign_performance.campaign_count:,}",
    )
    metric3.metric(
        "Campaign Win Rate",
        f"{float(multi_month_campaign_performance.win_rate):.1%}",
    )
    metric4.metric(
        "Broker Records",
        f"{total_records:,}",
    )

    pnl1, pnl2, pnl3 = st.columns(3)
    pnl1.metric("Equity/Options Realized P&L", money(equity_options_realized_pnl))
    pnl2.metric("FOREX Settled P&L", money(forex_settled_pnl))
    pnl3.metric("Combined Realized P&L", money(combined_realized_pnl))
    st.caption(
        "Combined realized P&L includes equity/options realized P&L and FOREX settled P&L. "
        "FOREX financing/interest is excluded."
    )

    perf1, perf2, perf3, perf4 = st.columns(4)

    profitable_month_rate_delta = (
        f"{float(multi_month_performance.profitable_month_rate):.1%} profitable"
        if multi_month_performance.profitable_month_rate is not None
        else None
    )

    perf1.metric(
        "Profitable Months",
        f"{multi_month_performance.profitable_month_count:,} / "
        f"{multi_month_performance.month_count:,}",
        profitable_month_rate_delta,
    )

    perf2.metric(
        "Average Monthly P&L",
        (
            money(multi_month_performance.average_monthly_pnl)
            if multi_month_performance.average_monthly_pnl is not None
            else "—"
        ),
    )

    best_month_label = (
        multi_month_performance.best_month_start.strftime("%B")
        if multi_month_performance.best_month_start is not None
        else "—"
    )

    perf3.metric(
        "Best Month",
        best_month_label,
        (
            money(multi_month_performance.best_month_pnl)
            if multi_month_performance.best_month_pnl is not None
            else None
        ),
    )

    worst_month_label = (
        multi_month_performance.worst_month_start.strftime("%B")
        if multi_month_performance.worst_month_start is not None
        else "—"
    )

    perf4.metric(
        "Worst Month",
        worst_month_label,
        (
            money(multi_month_performance.worst_month_pnl)
            if multi_month_performance.worst_month_pnl is not None
            else None
        ),
        delta_color="inverse",
    )

    st.divider()

    left, right = st.columns(2)

    with left:
        st.subheader("Monthly Realized P&L")

        pnl_chart = df[["period_start", "realized_pnl"]].copy()
        pnl_chart["period_start"] = pd.to_datetime(pnl_chart["period_start"])
        pnl_chart = pnl_chart.sort_values("period_start")

        monthly_pnl_base = alt.Chart(pnl_chart).encode(
            x=alt.X(
                "period_start:T",
                title="Month",
                axis=alt.Axis(format="%B"),
            ),
            y=alt.Y(
                "realized_pnl:Q",
                title="Realized P&L ($)",
            ),
            tooltip=[
                alt.Tooltip(
                    "period_start:T",
                    title="Month",
                    format="%B %Y",
                ),
                alt.Tooltip(
                    "realized_pnl:Q",
                    title="Realized P&L",
                    format="$,.2f",
                ),
            ],
        )

        monthly_pnl_chart = (
            monthly_pnl_base.mark_bar()
            + monthly_pnl_base.mark_point(
                filled=True,
                size=70,
            )
        )

        st.altair_chart(
            monthly_pnl_chart,
            width="stretch",
        )

    with right:
        st.subheader("Cumulative Realized P&L")

        cumulative = df[["period_start", "realized_pnl"]].copy()
        cumulative["period_start"] = pd.to_datetime(cumulative["period_start"])
        cumulative = cumulative.sort_values("period_start")
        cumulative["cumulative_pnl"] = cumulative["realized_pnl"].cumsum()

        cumulative_pnl_chart = (
            alt.Chart(cumulative)
            .mark_line(point=True)
            .encode(
                x=alt.X(
                    "period_start:T",
                    title="Month",
                    axis=alt.Axis(format="%B"),
                ),
                y=alt.Y(
                    "cumulative_pnl:Q",
                    title="Cumulative P&L ($)",
                ),
                tooltip=[
                    alt.Tooltip(
                        "period_start:T",
                        title="Month",
                        format="%B %Y",
                    ),
                    alt.Tooltip(
                        "cumulative_pnl:Q",
                        title="Cumulative P&L",
                        format="$,.2f",
                    ),
                ],
            )
        )

        st.altair_chart(
            cumulative_pnl_chart,
            width="stretch",
        )

    st.divider()

    st.divider()

    st.subheader("Monthly Performance")

    display_df = df[
        [
            "month",
            "equity_options_realized_pnl",
            "forex_settled_pnl",
            "realized_pnl",
            "campaigns",
            "wins",
            "losses",
            "breakeven",
            "win_rate",
            "records",
        ]
    ].copy()

    display_df.columns = [
        "Month",
        "Equity/Options P&L",
        "FOREX Settled P&L",
        "Combined Realized P&L",
        "Campaigns",
        "Wins",
        "Losses",
        "Breakeven",
        "Win Rate",
        "Broker Records",
    ]

    st.dataframe(
        display_df,
        hide_index=True,
        width="stretch",
        column_config={
            "Equity/Options P&L": st.column_config.NumberColumn(
                "Equity/Options P&L",
                format="$%,.2f",
            ),
            "FOREX Settled P&L": st.column_config.NumberColumn(
                "FOREX Settled P&L",
                format="$%,.2f",
            ),
            "Combined Realized P&L": st.column_config.NumberColumn(
                "Combined Realized P&L",
                format="$%,.2f",
            ),
            "Win Rate": st.column_config.NumberColumn(
                "Win Rate",
                format="percent",
            ),
        },
    )

    st.caption(
        "Monthly realized P&L combines equity/options realized P&L and FOREX "
        "settled P&L. FOREX financing/interest is excluded."
    )

with campaigns_tab:
    st.subheader("Campaign Economics")

    econ1, econ2, econ3, econ4 = st.columns(4)

    econ1.metric(
        "Average Winning Campaign",
        (
            money(multi_month_campaign_performance.average_win)
            if multi_month_campaign_performance.average_win is not None
            else "—"
        ),
    )

    econ2.metric(
        "Average Losing Campaign",
        (
            money(multi_month_campaign_performance.average_loss)
            if multi_month_campaign_performance.average_loss is not None
            else "—"
        ),
    )

    econ3.metric(
        "Win/Loss Payoff Ratio",
        (
            f"{multi_month_campaign_performance.payoff_ratio:.2f}×"
            if multi_month_campaign_performance.payoff_ratio is not None
            else "—"
        ),
    )

    econ4.metric(
        "Breakeven Campaigns",
        f"{multi_month_campaign_performance.breakeven_campaign_count:,}",
    )

    st.subheader("Campaign Outcome Distribution")

    dist1, dist2, dist3, dist4 = st.columns(4)

    dist1.metric(
        "Median Campaign P&L",
        (
            money(campaign_outcomes.median_campaign_pnl)
            if campaign_outcomes.median_campaign_pnl is not None
            else "—"
        ),
    )

    dist2.metric(
        "Median Winner",
        (
            money(campaign_outcomes.median_win)
            if campaign_outcomes.median_win is not None
            else "—"
        ),
    )

    dist3.metric(
        "Median Loser",
        (
            money(campaign_outcomes.median_loss)
            if campaign_outcomes.median_loss is not None
            else "—"
        ),
    )

    dist4.metric(
        "Excluded Campaigns",
        f"{campaign_outcomes.excluded_campaign_count:,}",
    )

    tail1, tail2, tail3, tail4 = st.columns(4)

    tail1.metric(
        "Best Campaign",
        campaign_outcomes.best_campaign_id or "—",
        (
            money(campaign_outcomes.best_campaign_pnl)
            if campaign_outcomes.best_campaign_pnl is not None
            else None
        ),
    )

    tail2.metric(
        "Worst Campaign",
        campaign_outcomes.worst_campaign_id or "—",
        (
            money(campaign_outcomes.worst_campaign_pnl)
            if campaign_outcomes.worst_campaign_pnl is not None
            else None
        ),
        delta_color="inverse",
    )

    tail3.metric(
        "Top 3 Winners",
        money(campaign_outcomes.top_3_winner_pnl),
    )

    tail4.metric(
        "Bottom 3 Losers",
        money(campaign_outcomes.bottom_3_loser_pnl),
    )

    st.subheader("Underlying Performance")

    underlying_rows = [
        {
            "Underlying": summary.underlying,
            "Realized P&L": float(summary.realized_pnl),
            "Campaigns": summary.campaign_count,
            "Wins": summary.winning_campaign_count,
            "Losses": summary.losing_campaign_count,
            "Breakeven": summary.breakeven_campaign_count,
            "Win Rate": float(summary.win_rate) * 100 if summary.win_rate is not None else None,
            "Average Campaign P&L": float(summary.average_campaign_pnl) if summary.average_campaign_pnl is not None else None,
            "Median Campaign P&L": float(summary.median_campaign_pnl) if summary.median_campaign_pnl is not None else None,
            "Best Campaign": summary.best_campaign_id,
            "Best Campaign P&L": float(summary.best_campaign_pnl) if summary.best_campaign_pnl is not None else None,
            "Worst Campaign": summary.worst_campaign_id,
            "Worst Campaign P&L": float(summary.worst_campaign_pnl) if summary.worst_campaign_pnl is not None else None,
        }
        for summary in underlying_performance
    ]
    underlying_df = pd.DataFrame(underlying_rows)
    if not underlying_df.empty:
        underlying_df = underlying_df.sort_values("Realized P&L", ascending=True).reset_index(drop=True)

    st.dataframe(
        underlying_df,
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config={
            "Realized P&L": st.column_config.NumberColumn("Realized P&L", format="$%0,.2f"),
            "Win Rate": st.column_config.NumberColumn("Win Rate", format="%.1f%%"),
            "Average Campaign P&L": st.column_config.NumberColumn("Average Campaign P&L", format="$%0,.2f"),
            "Median Campaign P&L": st.column_config.NumberColumn("Median Campaign P&L", format="$%0,.2f"),
            "Best Campaign P&L": st.column_config.NumberColumn("Best Campaign P&L", format="$%0,.2f"),
            "Worst Campaign P&L": st.column_config.NumberColumn("Worst Campaign P&L", format="$%0,.2f"),
        },
    )
    st.caption(
        "Equity/options only. FOREX is excluded from Underlying Performance v1. "
        "Only fully reconciled, unambiguous realized records are included. "
        "Underlyings are sorted by realized P&L, worst first; click column headers to re-sort the table."
    )

    st.subheader("Repeated-Campaign Performance")

    repeated_campaign_rows = [
        {
            "Underlying": summary.underlying,
            "Campaigns": summary.campaign_count,
            "First Realized Campaign": summary.first_campaign_id,
            "First Realized Date": summary.first_realized_date,
            "First Campaign P&L": float(summary.first_campaign_pnl),
            "Subsequent Campaigns": summary.subsequent_campaign_count,
            "Subsequent Realized P&L": float(summary.subsequent_realized_pnl),
            "Subsequent Wins": summary.subsequent_winning_campaign_count,
            "Subsequent Losses": summary.subsequent_losing_campaign_count,
            "Subsequent Breakeven": summary.subsequent_breakeven_campaign_count,
            "Subsequent Win Rate": float(summary.subsequent_win_rate) * 100 if summary.subsequent_win_rate is not None else None,
            "Subsequent Average Campaign P&L": float(summary.subsequent_average_campaign_pnl) if summary.subsequent_average_campaign_pnl is not None else None,
            "Subsequent Median Campaign P&L": float(summary.subsequent_median_campaign_pnl) if summary.subsequent_median_campaign_pnl is not None else None,
        }
        for summary in repeated_campaign_performance
    ]
    repeated_campaign_df = pd.DataFrame(repeated_campaign_rows)
    if not repeated_campaign_df.empty:
        repeated_campaign_df = repeated_campaign_df.sort_values("Subsequent Realized P&L", ascending=True).reset_index(drop=True)

    st.dataframe(
        repeated_campaign_df,
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config={
            "First Realized Date": st.column_config.DateColumn("First Realized Date", format="MMM D, YYYY"),
            "First Campaign P&L": st.column_config.NumberColumn("First Campaign P&L", format="$%0,.2f"),
            "Subsequent Realized P&L": st.column_config.NumberColumn("Subsequent Realized P&L", format="$%0,.2f"),
            "Subsequent Win Rate": st.column_config.NumberColumn("Subsequent Win Rate", format="%.1f%%"),
            "Subsequent Average Campaign P&L": st.column_config.NumberColumn("Subsequent Average Campaign P&L", format="$%0,.2f"),
            "Subsequent Median Campaign P&L": st.column_config.NumberColumn("Subsequent Median Campaign P&L", format="$%0,.2f"),
        },
    )
    st.caption(
        "Equity/options only; underlyings with at least two qualifying campaigns are shown. "
        "This is realized-campaign chronology, not true campaign-start chronology: campaigns "
        "are ordered by earliest realized close date, with period-qualified campaign ID as a "
        "same-date tie-breaker. Only fully reconciled, unambiguous realized records are included. "
        "Rows are sorted by subsequent realized P&L, worst first; click column headers to re-sort."
    )

with risk_tab:
    st.subheader("Realized Drawdown")

    drawdown1, drawdown2, drawdown3, drawdown4 = st.columns(4)

    drawdown1.metric(
        "Maximum Drawdown",
        money(realized_drawdown.maximum_drawdown),
    )

    drawdown2.metric(
        "Current Drawdown",
        money(realized_drawdown.current_drawdown),
    )

    drawdown3.metric(
        "Drawdown Peak",
        (
            realized_drawdown.maximum_drawdown_peak_date.strftime("%b %d, %Y")
            if realized_drawdown.maximum_drawdown_peak_date is not None
            else "Starting baseline"
        ),
    )

    drawdown4.metric(
        "Drawdown Trough",
        (
            realized_drawdown.maximum_drawdown_trough_date.strftime("%b %d, %Y")
            if realized_drawdown.maximum_drawdown_trough_date is not None
            else "—"
        ),
    )

    if realized_drawdown.maximum_drawdown < Decimal("0"):
        if realized_drawdown.recovery_date is not None:
            st.caption(
                "Maximum drawdown recovered on "
                f"{realized_drawdown.recovery_date:%b %d, %Y}. "
                "Equity/options realized P&L only."
            )
        else:
            st.caption(
                "Maximum drawdown has not recovered through the latest "
                "period-qualified realized close. Equity/options realized P&L only."
            )
    else:
        st.caption(
            "No realized drawdown is present in the available "
            "period-qualified equity/options history."
        )

    drawdown_rows = [
        {
            "closed_date": point.closed_date,
            "daily_realized_pnl": float(point.realized_pnl),
            "cumulative_pnl": float(point.cumulative_pnl),
            "running_peak_pnl": float(point.running_peak_pnl),
            "drawdown": float(point.drawdown),
        }
        for point in realized_drawdown.points
    ]

    drawdown_df = pd.DataFrame(drawdown_rows)

    if not drawdown_df.empty:
        drawdown_df["closed_date"] = pd.to_datetime(
            drawdown_df["closed_date"]
        )

        equity_base = alt.Chart(drawdown_df).encode(
            x=alt.X(
                "closed_date:T",
                title="Realized Close Date",
            ),
        )

        cumulative_line = equity_base.mark_line().encode(
            y=alt.Y(
                "cumulative_pnl:Q",
                title="Cumulative Realized P&L ($)",
            ),
            tooltip=[
                alt.Tooltip(
                    "closed_date:T",
                    title="Date",
                    format="%b %d, %Y",
                ),
                alt.Tooltip(
                    "cumulative_pnl:Q",
                    title="Cumulative P&L",
                    format="$,.2f",
                ),
            ],
        )

        peak_line = equity_base.mark_line(
            strokeDash=[6, 4],
        ).encode(
            y=alt.Y(
                "running_peak_pnl:Q",
                title="Cumulative Realized P&L ($)",
            ),
            tooltip=[
                alt.Tooltip(
                    "closed_date:T",
                    title="Date",
                    format="%b %d, %Y",
                ),
                alt.Tooltip(
                    "running_peak_pnl:Q",
                    title="High-Water Mark",
                    format="$,.2f",
                ),
            ],
        )

        drawdown_line = (
            alt.Chart(drawdown_df)
            .mark_line(point=True)
            .encode(
                x=alt.X(
                    "closed_date:T",
                    title="Realized Close Date",
                ),
                y=alt.Y(
                    "drawdown:Q",
                    title="Drawdown ($)",
                ),
                tooltip=[
                    alt.Tooltip(
                        "closed_date:T",
                        title="Date",
                        format="%b %d, %Y",
                    ),
                    alt.Tooltip(
                        "drawdown:Q",
                        title="Drawdown",
                        format="$,.2f",
                    ),
                    alt.Tooltip(
                        "daily_realized_pnl:Q",
                        title="Daily Realized P&L",
                        format="$,.2f",
                    ),
                ],
            )
        )

        trough_df = drawdown_df[
            drawdown_df["drawdown"]
            == float(realized_drawdown.maximum_drawdown)
        ]

        trough_point = (
            alt.Chart(trough_df)
            .mark_point(
                filled=True,
                size=120,
            )
            .encode(
                x=alt.X("closed_date:T"),
                y=alt.Y("drawdown:Q"),
                tooltip=[
                    alt.Tooltip(
                        "closed_date:T",
                        title="Maximum Drawdown Trough",
                        format="%b %d, %Y",
                    ),
                    alt.Tooltip(
                        "drawdown:Q",
                        title="Maximum Drawdown",
                        format="$,.2f",
                    ),
                ],
            )
        )

        drawdown_chart = drawdown_line + trough_point

        equity_col, drawdown_col = st.columns(2)

        with equity_col:
            st.caption("Realized equity curve and high-water mark")
            st.altair_chart(
                cumulative_line + peak_line,
                width="stretch",
            )

        with drawdown_col:
            st.caption("Realized drawdown from high-water mark")
            st.altair_chart(
                drawdown_chart,
                width="stretch",
            )

    st.subheader("Maximum Drawdown Contribution")

    contribution_rows = [
        {
            "Underlying": item.underlying,
            "Records": item.record_count,
            "Wins": item.winning_record_count,
            "Losses": item.losing_record_count,
            "Breakeven": item.breakeven_record_count,
            "Gross Gain": float(item.gross_gain),
            "Gross Loss": float(item.gross_loss),
            "Net P&L": float(item.net_realized_pnl),
            "Largest Gain": (
                float(item.largest_gain)
                if item.largest_gain is not None
                else None
            ),
            "Largest Loss": (
                float(item.largest_loss)
                if item.largest_loss is not None
                else None
            ),
            "Contribution": float(item.drawdown_contribution) * 100,
        }
        for item in maximum_drawdown_contributions.contributions
    ]

    contribution_df = pd.DataFrame(contribution_rows)

    if not contribution_df.empty:
        contribution_df = contribution_df.sort_values(
            ["Net P&L", "Underlying"],
            ascending=[True, True],
        ).reset_index(drop=True)

    st.dataframe(
        contribution_df,
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config={
            "Gross Gain": st.column_config.NumberColumn(
                "Gross Gain",
                format="$%0,.2f",
            ),
            "Gross Loss": st.column_config.NumberColumn(
                "Gross Loss",
                format="$%0,.2f",
            ),
            "Net P&L": st.column_config.NumberColumn(
                "Net P&L",
                format="$%0,.2f",
            ),
            "Largest Gain": st.column_config.NumberColumn(
                "Largest Gain",
                format="$%0,.2f",
            ),
            "Largest Loss": st.column_config.NumberColumn(
                "Largest Loss",
                format="$%0,.2f",
            ),
            "Contribution": st.column_config.NumberColumn(
                "Contribution",
                format="%.1f%%",
            ),
        },
    )

    if maximum_drawdown_contributions.maximum_drawdown < Decimal("0"):
        st.caption(
            "Equity/options broker realized facts from the maximum-drawdown "
            "interval only. Negative contribution increased drawdown; positive "
            "contribution offset losses elsewhere. Unassigned, ambiguous, and "
            "unreconciled campaign provenance remains included. FOREX is excluded."
        )
    else:
        st.caption(
            "No maximum-drawdown contribution interval is present in the "
            "available equity/options history."
        )
