from pathlib import Path

DASHBOARD = Path("src/campaigniq/ui/dashboard.py")


def source() -> str:
    return DASHBOARD.read_text()


def test_dashboard_exposes_monthly_import_view() -> None:
    text = source()
    assert "st.sidebar.radio(" in text
    assert '"Monthly Import"' in text
    assert "render_monthly_import_wizard()" in text


def test_wizard_exposes_real_world_monthly_documents() -> None:
    text = source()
    assert '"Thinkorswim trade history"' in text
    assert '"Schwab Brokerage Statement"' in text
    assert '"Schwab Realized Gain/Loss Report"' in text
    assert '"Thinkorswim Forex Transaction Report"' in text
    assert '"FOREX Settled P&L"' in text
    assert '"Combined Realized P&L"' in text
    assert '"Equity/Options Realized P&L"' in text
    assert "FOREX financing/interest is excluded" in text
    assert '"Schwab closing position snapshot"' not in text
    assert '"Schwab assignment/exercise evidence (optional)"' not in text


def test_brokerage_statement_feeds_statement_roles_only() -> None:
    text = source()
    assert (
        "supplied[MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT] = schwab_path"
        in text
    )
    assert (
        "supplied[MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE] = schwab_path"
        in text
    )
    assert (
        "supplied[MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS] = schwab_path"
        not in text
    )


def test_realized_gain_loss_uses_its_own_uploaded_report() -> None:
    text = source()
    assert (
        "realized_upload = uploads.get(MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS)"
        in text
    )
    assert (
        "supplied[MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS] = realized_path"
        in text
    )


def test_validation_signature_covers_month_and_upload_contents() -> None:
    text = source()
    assert "def _monthly_import_signature(" in text
    assert 'digest.update(f"{int(year):04d}-{int(month):02d}".encode())' in text
    assert "digest.update(upload.getvalue())" in text


def test_validated_state_survives_streamlit_reruns() -> None:
    text = source()
    assert 'st.session_state["monthly_import_validated_signature"] = signature' in text
    assert 'validated_signature = st.session_state.get(' in text
    assert "if validated_signature != signature:" in text


def test_preflight_is_recreated_on_each_validated_rerun() -> None:
    text = source()
    temp = text.index("with tempfile.TemporaryDirectory(")
    preflight = text.index("preflight = prepare_monthly_import(", temp)
    confirm = text.index("confirm = st.checkbox(", preflight)
    execute = text.index("execution = execute_monthly_import(", confirm)
    assert temp < preflight < confirm < execute


def test_execution_refuses_inputs_changed_after_validation() -> None:
    text = source()
    assert "execution_signature = _monthly_import_signature(" in text
    assert "if execution_signature != st.session_state.get(" in text
    assert '"The import inputs changed after validation. "' in text


def test_wizard_requires_explicit_execution_confirmation() -> None:
    text = source()
    assert "confirm = st.checkbox(" in text
    assert "if not confirm:" in text
    assert '"Run reconciled import"' in text


def test_monthly_import_view_stops_before_analytics_loading() -> None:
    text = source()
    import_view = text.index('if view == "Monthly Import":')
    stop = text.index("st.stop()", import_view)
    load = text.index("summaries, monthly_attributions, monthly_forex_attributions = load_summaries()")
    assert import_view < stop < load


def test_monthly_import_runtime_state_is_separate_from_test_fixtures() -> None:
    text = source()
    assert 'RUNTIME_DATA_DIR = PROJECT_ROOT / ".campaigniq"' in text
    assert (
        'AUTHORITATIVE_STATE_DIR = RUNTIME_DATA_DIR / "authoritative_state"'
        in text
    )
    assert (
        'HISTORICAL_SOURCE_ROOT = RUNTIME_DATA_DIR / "thinkorswim_history"'
        in text
    )
    assert "AUTHORITATIVE_STATE_DIR = RECONCILED_DIR" not in text
    assert (
        'HISTORICAL_SOURCE_ROOT = PROJECT_ROOT / "tests" / "data" / "thinkorswim"'
        not in text
    )


def test_forex_transaction_report_uses_its_own_uploaded_report() -> None:
    text = source()
    assert "forex_upload = uploads.get(" in text
    assert "supplied[MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT] = forex_path" in text

def test_wizard_copy_describes_four_monthly_documents() -> None:
    text = source()
    assert "supply the four monthly brokerage documents" in text
    assert "supply the three documents" not in text


def test_wizard_reports_forex_settlement_control_after_execution() -> None:
    text = source()
    execute = text.index("execution = execute_monthly_import(")
    control = text.index('st.markdown("#### FOREX Settlement Control")', execute)
    final_success = text.index(
        '"Closing inventory reconciled and authoritative month-end state "',
        control,
    )

    assert execute < control < final_success
    assert '"Broker MTD Settled P&L: $' in text
    assert '"Parsed settlement-row P&L: $' in text
    assert '"FOREX settlement control reconciled exactly."' in text
    assert '"FOREX settlement control difference: "' in text
    assert '"monthly finalization."' in text
    assert "execution.forex_settlement_control_delta_usd" in text
    assert "execution.forex_settlement_control_reconciled" in text


def test_wizard_reports_additional_forex_controls_as_nonfatal() -> None:
    text = source()
    assert '"FOREX PL Total control reconciled exactly."' in text
    assert '"FOREX commission control reconciled exactly."' in text
    assert '"FOREX financing controls reconciled exactly."' in text
    assert "execution.forex_pl_total_control_reconciled" in text
    assert "execution.forex_commission_control_reconciled" in text
    assert "execution.forex_financing_control_reconciled" in text
    assert "did not block monthly finalization." in text
