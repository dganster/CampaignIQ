"""CampaignIQ read-only analytics dashboard."""

from __future__ import annotations

import hashlib
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
from campaigniq.monthly_import_execution import execute_monthly_import
from campaigniq.pdf_text import extract_pdf_text
from campaigniq.persistence.lifecycle_history import (
    load_lifecycle_history_from_storage,
)
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.persistence.persisted_multi_month_analytics import (
    load_persisted_monthly_campaign_attributions_from_storage,
)
from campaigniq.runtime import build_local_runtime
from campaigniq.ui.access_audit import audit_unauthorized_oidc_identity
from campaigniq.ui.access_gate import (
    AUTH_MODE_OIDC,
    access_password,
    authentication_mode,
    password_matches,
)
from campaigniq.ui.dashboard_campaigns import (
    aggregate_period_qualified_campaigns,
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
                "Qty": quantity if quantity is not None else "",
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
    st.markdown("#### Preflight")

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

    if assignment_validation.valid:
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


def render_monthly_import_wizard():
    st.subheader("Monthly Import")
    st.caption(
        "Choose the month and supply the four monthly brokerage documents. "
        "For your first CampaignIQ import, also supply the prior month-end "
        "Schwab Brokerage Statement so CampaignIQ can establish opening "
        "inventory. Later months use the preceding authoritative lot state. "
        "CampaignIQ reconciles closing inventory before persisting the new "
        "month-end state."
    )

    today = date.today()
    year_col, month_col = st.columns(2)
    year = year_col.number_input(
        "Year",
        min_value=2020,
        max_value=2100,
        value=today.year,
        step=1,
        key="monthly_import_year",
    )
    month = month_col.selectbox(
        "Month",
        options=range(1, 13),
        index=today.month - 1,
        format_func=lambda value: date(2000, value, 1).strftime("%B"),
        key="monthly_import_month",
    )

    uploads = {}
    for role, label, file_types in MONTHLY_UPLOAD_ROLES:
        uploads[role] = st.file_uploader(
            label,
            type=list(file_types),
            key=f"monthly_import_{role}",
        )

    signature = _monthly_import_signature(
        year=year,
        month=month,
        uploads=uploads,
    )

    if st.button(
        "Validate monthly import",
        type="primary",
        key="monthly_import_validate",
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
            st.info(
                "Choose the month and the four monthly documents, then validate "
                "before import. For your first CampaignIQ import, also supply "
                "the prior month-end Schwab Brokerage Statement."
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

        if not preflight.ready:
            st.error(
                "Monthly import is not ready. Resolve the required items above "
                "before running the import."
            )
            return

        st.success(
            f"Preflight ready for {preflight.contract.period_start:%B %Y}."
        )

        confirm = st.checkbox(
            "Run the import and persist the month-end state only if closing "
            "inventory reconciles.",
            key="monthly_import_confirm",
        )
        if not confirm:
            return

        if not st.button(
            "Run reconciled import",
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
                "No authoritative month-end state was persisted."
            )
            st.dataframe(
                [
                    {
                        "Instrument": repr(item.instrument),
                        "Computed": str(item.computed_quantity),
                        "Snapshot": str(item.snapshot_quantity),
                        "Difference": str(item.difference),
                    }
                    for item in execution.closing_reconciliation.mismatches
                ],
                use_container_width=True,
            )
            return

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

        st.success(
            "Closing inventory reconciled and authoritative month-end state "
            f"was persisted to {execution.authoritative_state_path}."
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

view = st.sidebar.radio(
    "View",
    ("Realized Campaign Analytics", "Monthly Import"),
    key="campaigniq_view",
)

if view == "Monthly Import":
    render_monthly_import_wizard()
    st.stop()

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

st.subheader(f"{first_period} – {last_period}")

metric1, metric2, metric3, metric4 = st.columns(4)

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

st.subheader("Position Lifecycle")

if lifecycle_summary.transition_count == 0:
    st.info(
        "No published lifecycle transitions are available yet."
    )
else:
    lifecycle_metric_columns = st.columns(5)

    lifecycle_metric_columns[0].metric(
        "Transitions",
        f"{lifecycle_summary.transition_count:,}",
    )
    lifecycle_metric_columns[1].metric(
        "Symbols",
        f"{lifecycle_summary.symbol_count:,}",
    )
    lifecycle_metric_columns[2].metric(
        "Rolls",
        f"{lifecycle_summary.roll_count:,}",
    )
    lifecycle_metric_columns[3].metric(
        "Exits",
        f"{lifecycle_summary.exit_count:,}",
    )
    lifecycle_metric_columns[4].metric(
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

st.subheader("Campaign Drill-Down")

campaign_rows = [
    {
        "Campaign": summary.campaign_id,
        "Symbols": ", ".join(summary.symbols),
        "Realized P&L": float(summary.realized_pnl),
        "Records": summary.record_count,
        "Allocations": summary.allocation_count,
        "First Close": summary.first_closed_date,
        "Last Close": summary.last_closed_date,
        "Reconciled": "Yes" if summary.fully_reconciled else "No",
    }
    for summary in campaign_drilldowns
]

campaign_df = pd.DataFrame(campaign_rows)
if not campaign_df.empty:
    campaign_df = campaign_df.sort_values(
        "Realized P&L",
        ascending=True,
    ).reset_index(drop=True)

st.dataframe(
    campaign_df,
    use_container_width=True,
    hide_index=True,
    height=420,
    column_config={
        "Realized P&L": st.column_config.NumberColumn(
            "Realized P&L",
            format="$%0,.2f",
        ),
        "First Close": st.column_config.DateColumn(
            "First Close",
            format="MMM D, YYYY",
        ),
        "Last Close": st.column_config.DateColumn(
            "Last Close",
            format="MMM D, YYYY",
        ),
    },
)

st.caption(
    "Campaigns are sorted by realized P&L, worst first. "
    "Click column headers to re-sort the table."
)

if total_unattributed == 0:
    st.success("All broker realized records are attributed to campaigns.")
else:
    st.warning(
        f"{total_unattributed} broker realized record(s) remain unattributed."
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
