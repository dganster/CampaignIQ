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
        "MonthlyInputRole.SCHWAB_CLOSING_POSITION_SNAPSHOT\n        ] = schwab_source_path"
        in text
    )
    assert (
        "MonthlyInputRole.SCHWAB_ASSIGNMENT_EVIDENCE\n        ] = schwab_source_path"
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
        "MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS\n        ] = realized_source_path"
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
    assert '"Finalize {preflight.contract.period_start:%B %Y}"' in text


def test_monthly_import_view_stops_before_analytics_loading() -> None:
    text = source()
    import_view = text.index('if view == "Monthly Import":')
    stop = text.index("st.stop()", import_view)
    load = text.index("summaries, monthly_attributions, monthly_forex_attributions = load_summaries()")
    assert import_view < stop < load


def test_monthly_import_runtime_state_is_separate_from_test_fixtures() -> None:
    text = source()

    assert "from campaigniq.runtime import build_local_runtime" in text
    assert "RUNTIME = build_local_runtime(" in text
    assert (
        "workspace_id=ACCESS.workspace_id if ACCESS is not None else None"
        in text
    )
    assert (
        "AUTHORITATIVE_STATE_DIR = RUNTIME.authoritative_state_root"
        in text
    )
    assert "HISTORICAL_SOURCE_ROOT = RUNTIME.historical_source_root" in text
    assert "ARTIFACT_STORAGE = RUNTIME.artifact_storage" in text

    # The dashboard consumes runtime infrastructure rather than constructing
    # the local filesystem storage adapter itself.
    assert "LocalFilesystemArtifactStorage" not in text

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
    final_success_text = text.index(
        "finalized successfully",
        control,
    )
    final_success = text.rfind(
        "st.success(",
        control,
        final_success_text,
    )
    assert final_success != -1

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


def test_analytics_loading_filters_artifacts_through_publication_boundary() -> None:
    text = source()
    assert "def _published_artifact_keys(" in text
    assert "ARTIFACT_STORAGE.list_keys(" in text
    assert "is_month_published_in_storage(" in text
    assert '"-realized-attributions.json"' in text
    assert '"-forex-settlement-attributions.json"' in text

def test_wizard_accepts_native_schwab_pdfs() -> None:
    text = source()
    assert '"Schwab Brokerage Statement",\n        ("pdf", "txt", "csv"),' in text
    assert '"Schwab Realized Gain/Loss Report",\n        ("pdf", "txt", "csv"),' in text


def test_wizard_extracts_schwab_pdfs_before_existing_readers() -> None:
    text = source()
    assert "from campaigniq.pdf_text import extract_pdf_text" in text
    assert 'if suffix.lower() == ".pdf":' in text
    assert 'upload_dir / "schwab_brokerage_statement.txt"' in text
    assert 'upload_dir / "schwab_realized_gain_loss.txt"' in text
    assert "SCHWAB_CLOSING_POSITION_SNAPSHOT" in text
    assert "SCHWAB_REALIZED_GAIN_LOSS" in text


def test_wizard_exposes_first_import_opening_statement() -> None:
    text = source()
    assert '"Prior month-end Schwab Brokerage Statement (first import only)"' in text
    assert "MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT" in text
    assert (
        '"Prior month-end Schwab Brokerage Statement",\n'
        '        required=False,'
    ) in text


def test_opening_statement_feeds_opening_snapshot_role_only() -> None:
    text = source()
    assert (
        "opening_upload = uploads.get(\n"
        "        MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT"
    ) in text
    assert (
        "MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT\n"
        "        ] = opening_source_path"
    ) in text
    assert 'upload_dir / "schwab_opening_position_snapshot.txt"' in text


def test_opening_statement_participates_in_validation_signature() -> None:
    text = source()
    roles = text[text.index("MONTHLY_UPLOAD_ROLES = ("):text.index(
        "\n\n\ndef money", text.index("MONTHLY_UPLOAD_ROLES = (")
    )]
    assert "MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT" in roles

    signature = text[text.index("def _monthly_import_signature("):text.index(
        "\n\n\ndef _show_validation", text.index("def _monthly_import_signature(")
    )]
    assert "for role, _, _ in MONTHLY_UPLOAD_ROLES:" in signature
    assert "digest.update(upload.getvalue())" in signature


def test_wizard_explains_bootstrap_without_changing_four_document_workflow() -> None:
    text = source()
    assert "supply the four monthly brokerage documents" in text
    assert "No authoritative month-end position state is available yet." in text
    assert "This will be a bootstrap import." in text
    assert (
        "A prior month-end Schwab Brokerage Statement is required when "
        in text
    )
    assert (
        "CampaignIQ cannot load an authoritative predecessor state."
        in text
    )




def test_wizard_reports_authoritative_monthly_readiness() -> None:
    text = source()

    assert "#### Current authoritative state" in text
    assert "Latest authoritative month:" in text
    assert "Next month to process:" in text
    assert "Opening position state available from" in text
    assert "is the next expected monthly import." in text


def test_wizard_uses_exact_authoritative_predecessor_api() -> None:
    text = source()

    assert "load_preceding_authoritative_state_from_storage" in text
    assert "period_start=selected_start" in text


def test_wizard_defaults_to_next_authoritative_month() -> None:
    text = source()

    assert "default_start = _next_month(authoritative_periods[-1])" in text
    assert "value=default_start.year" in text
    assert "index=default_start.month - 1" in text


def test_wizard_hides_bootstrap_statement_when_predecessor_exists() -> None:
    text = source()

    assert (
        "role == MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT"
        in text
    )
    assert "and predecessor is not None" in text
    assert "Prior month-end Schwab Brokerage Statement: not required." in text


def test_wizard_explains_validation_is_read_only() -> None:
    text = source()

    assert "Validation is read-only" in text
    assert "It does not persist authoritative monthly data." in text


def test_wizard_separates_historical_backfill_from_normal_import() -> None:
    text = source()

    assert (
        "Historical backfill should be handled separately from the "
        "normal monthly workflow."
    ) in text



def test_wizard_provides_period_specific_document_guidance() -> None:
    text = source()

    assert "def _monthly_document_guidance(" in text
    assert "selected_period_start = _month_start(int(year), int(month))" in text
    assert "selected_period_end = selected_next_month - date.resolution" in text
    assert "guidance = _monthly_document_guidance(" in text


def test_trade_history_guidance_uses_selected_month() -> None:
    text = source()

    assert "role == MonthlyInputRole.THINKORSWIM_TRADE_HISTORY" in text
    assert "_display_date_range(period_start, period_end)" in text


def test_schwab_statement_guidance_explains_reconciliation_role() -> None:
    text = source()

    assert 'role == "schwab_brokerage_statement"' in text
    assert "used to reconcile month-end positions" in text


def test_realized_gain_loss_guidance_uses_selected_month() -> None:
    text = source()

    start = text.index(
        "if role == MonthlyInputRole.SCHWAB_REALIZED_GAIN_LOSS:"
    )
    end = text.index(
        "if role == MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT:",
        start,
    )
    section = text[start:end]

    assert "_display_date_range(period_start, period_end)" in section


def test_forex_guidance_includes_required_preceding_day() -> None:
    text = source()

    start = text.index(
        "if role == MonthlyInputRole.SCHWAB_FOREX_TRANSACTION_REPORT:"
    )
    end = text.index(
        "if role == MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT:",
        start,
    )
    section = text[start:end]

    assert "forex_start = period_start - date.resolution" in section
    assert "_display_date_range(forex_start, period_end)" in section


def test_bootstrap_statement_guidance_uses_prior_month() -> None:
    text = source()

    start = text.index(
        "if role == MonthlyInputRole.SCHWAB_OPENING_POSITION_SNAPSHOT:"
    )
    end = text.index('    return ""', start)
    section = text[start:end]

    assert "previous_day = period_start - date.resolution" in section
    assert "opening_start = previous_day.replace(day=1)" in section
    assert "_display_date_range(opening_start, previous_day)" in section


def test_monthly_uploaders_use_separate_guidance_labels() -> None:
    text = source()

    assert 'st.markdown(f"**{label}**")' in text
    assert '"Upload",' in text
    assert 'label_visibility="collapsed"' in text



def test_review_explicitly_says_nothing_is_published() -> None:
    text = source()

    assert 'st.markdown("#### 3. Review")' in text
    assert "Nothing has been published yet." in text
    assert "Validation and review are" in text
    assert '"read-only."' in text


def test_review_explains_finalization_reconciliation_gate() -> None:
    text = source()

    assert 'st.markdown("##### What happens during finalization")' in text
    assert "compare the computed month-end positions with the Schwab" in text
    assert "Normally, the month is published" in text
    assert "only when closing inventory reconciles." in text
    assert "statement-boundary timing difference" in text
    assert "missing economic event must be corrected" in text


def test_finalize_action_is_named_for_selected_month() -> None:
    text = source()

    assert (
        'f"#### 4. Finalize '
        '{preflight.contract.period_start:%B %Y}"'
    ) in text
    assert (
        'f"Finalize {preflight.contract.period_start:%B %Y}"'
    ) in text
    assert 'type="primary"' in text


def test_finalize_requires_explicit_publication_acknowledgement() -> None:
    text = source()

    assert "I understand that finalizing " in text
    assert "may publish it as " in text
    assert '"authoritative CampaignIQ data."' in text
    assert 'key="monthly_import_confirm"' in text


def test_failed_closing_reconciliation_still_blocks_ordinary_publication() -> None:
    text = source()

    assert "if not execution.closing_reconciliation.reconciled:" in text
    assert "The ordinary finalization attempt did not publish the month." in text
    assert "Document a boundary timing exception" in text


def test_success_message_says_authoritative_month_was_published() -> None:
    text = source()

    assert "finalized successfully." in text
    assert "Closing inventory reconciled and the authoritative month was " in text
    assert '"published."' in text



def test_reconciliation_table_uses_operator_instrument_display() -> None:
    text = source()

    assert '"Instrument": _display_instrument(item.instrument)' in text
    assert '"Instrument": repr(item.instrument)' not in text


def test_operator_instrument_display_formats_options_and_symbols() -> None:
    text = source()

    assert "def _display_instrument(instrument) -> str:" in text
    assert "if isinstance(instrument, OptionContract):" in text
    assert 'f"{instrument.underlying} "' in text
    assert 'f"{instrument.expiration:%b %d %Y} "' in text
    assert 'f"${strike} {option_type}"' in text
    assert "if isinstance(instrument, Instrument):" in text
    assert "return instrument.symbol" in text



def test_boundary_exception_is_explicit_second_pass_workflow() -> None:
    text = source()

    first_execution = text.index("execution = execute_monthly_import(")
    failure = text.index(
        "if not execution.closing_reconciliation.reconciled:",
        first_execution,
    )
    exception_heading = text.index(
        'st.markdown("##### Document a boundary timing exception")',
        failure,
    )
    second_execution = text.index(
        "execution = execute_monthly_import(",
        exception_heading,
    )

    assert first_execution < failure < exception_heading < second_execution
    assert "reconciliation_decision=decision" in text


def test_boundary_exception_requires_reason_evidence_and_approval() -> None:
    text = source()

    assert '"Why is this a boundary timing difference?"' in text
    assert '"Supporting evidence"' in text
    assert "evidence_items = tuple(" in text
    assert "bool(exception_reason.strip())" in text
    assert "and bool(evidence_items)" in text
    assert "and exception_approved" in text
    assert "monthly_import_boundary_exception_approved" in text


def test_boundary_exception_preserves_failed_reconciliation_semantics() -> None:
    text = source()

    assert "closing reconciliation itself remains failed." in text
    assert "Closing inventory did not reconcile; the" in text
    assert "transaction-derived closing state was carried forward under" in text


def test_boundary_exception_warns_against_missing_economic_events() -> None:
    text = source()

    assert '"assignments, IPO allocations, transfers, or other economic "' in text
    assert '"events that still need to be reconstructed."' in text


def test_boundary_exception_captures_exact_failed_mismatches() -> None:
    text = source()

    assert "decision = ReconciliationDecision(" in text
    assert "decision_type=BOUNDARY_TIMING_EXCEPTION" in text
    assert "resolution=ACCEPT_TRANSACTION_DERIVED_STATE" in text
    assert "mismatches=capture_reconciliation_mismatches(" in text
    assert "execution.closing_reconciliation.mismatches" in text


def test_boundary_exception_backend_revalidates_before_publication() -> None:
    text = source()

    assert "The reconciliation" in text
    assert "result changed or the documented exception no longer" in text
    assert "matches the exact current discrepancies." in text
    assert "if not execution.finalized:" in text
    assert "if execution.reconciliation_decision is None:" in text


def test_success_message_distinguishes_exceptional_finalization() -> None:
    text = source()

    assert "if execution.reconciliation_decision is None:" in text
    assert "Closing inventory reconciled and the authoritative month was" in text
    assert "finalized with a" in text
    assert "documented boundary timing exception and the authoritative" in text



def test_analytics_dashboard_reports_data_currency() -> None:
    text = source()

    assert "def _render_analytics_data_status(" in text
    assert 'st.markdown("#### Data Status")' in text
    assert '"Analytics through"' in text
    assert '"Latest authoritative month"' in text
    assert '"Next expected monthly import"' in text


def test_analytics_data_status_uses_published_authoritative_state() -> None:
    text = source()

    start = text.index("def _render_analytics_data_status(")
    end = text.index(
        "\n\n\ndef _monthly_import_operator_state(",
        start,
    )
    section = text[start:end]

    assert "_authoritative_lot_period_ends()" in section
    assert "authoritative_periods[-1]" in section
    assert "_next_month(latest_authoritative)" in section


def test_analytics_data_status_uses_actual_analytics_period_end() -> None:
    text = source()

    heading = text.index(
        'st.subheader(f"{first_period} – {last_period}")'
    )
    status = text.index(
        "_render_analytics_data_status(",
        heading,
    )
    metrics = text.index(
        "metric1, metric2, metric3, metric4 = st.columns(4)",
        heading,
    )

    assert heading < status < metrics
    assert "analytics_period_end=summaries[-1].period_end" in text[
        status:metrics
    ]
