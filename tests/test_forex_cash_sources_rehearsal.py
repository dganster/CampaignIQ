import base64
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import zipfile
import pytest
from campaigniq import account_runtime_roles_admin as admin
from campaigniq import account_committed_publication_rehearsal as runner
from campaigniq import account_forex_cash_review as cash
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.forex_cash_rehearsal_sources import sources


def test_real_synthetic_identity_and_cash_calculation():
    uploads, ledger, supplement = sources()
    service = SimpleNamespace(_known=lambda session: {
        'securities': BrokerBinding.create('securities', '24681357'),
        'forex': BrokerBinding.create('forex', '8642097')})
    session = SimpleNamespace(account=SimpleNamespace(account_id='synthetic'), drafts=SimpleNamespace(
        document=lambda draft, role: (uploads[role].name, uploads[role].media_type, uploads[role].content)))
    draft = SimpleNamespace(id='synthetic-draft', year=2026, month=10, revision=2)
    from decimal import Decimal
    review = cash.evaluate(service, session, draft, ledger.content, supplement.content)
    assert review['calculation']['candidate_net_cash_change_usd'] == Decimal('-11')
    assert review['calculation']['csv_mtd_minus_candidate_usd'] == Decimal('1')
    assert review['calculation']['omitted_execution_references'] == ['1001']
    assert review['publication_authorized'] is False
    assert review['broker_verified_cross_account_link'] is False


def test_bundle_imports_without_site_packages_and_only_named_fixtures(tmp_path):
    destination = tmp_path / 'forex.zip'
    admin.build_bundle(destination, job='forex_cash_sources')
    with zipfile.ZipFile(destination) as archive:
        assert json.loads(archive.read('bundle.json'))['job'] == 'forex_cash_sources'
        from campaigniq.account_reconciliation_rehearsal import ROLES
        assert {n for n in archive.namelist() if n.startswith('tests/')} == {'tests/fixtures/' + n for n in ROLES.values()}
        archive.extractall(tmp_path / 'isolated')
    code = ('import sys;sys.path[:0]=sys.argv[1:];'
        'from campaigniq.account_forex_cash_sources_rehearsal import verify_forex_cash_sources;'
        'from campaigniq.account_forex_financial_candidate import preview;'
        'from campaigniq.account_forex_financial_policy import saved_preview;'
        'from campaigniq.account_forex_publication_rehearsal import verify_forex_publication;'
        'from campaigniq.account_no_trading_rehearsal import verify_no_trading;'
        'from campaigniq.account_income_rehearsal import verify_income;'
        'from campaigniq.forex_publication_rehearsal_sources import sources as publication_sources;'
        'u,c,s,d,p,n,f=publication_sources();assert len(u)==4;'
        'from campaigniq.forex_cash_rehearsal_sources import sources;'
        'u,c,s=sources();assert len(u)==2 and c.content==s.content')
    result = subprocess.run([sys._base_executable, '-I', '-S', '-c', code,
        str(tmp_path / 'isolated/code'), str(tmp_path / 'isolated/deps')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('other', ['publication', 'committed', 'dashboard', 'preferences', 'application', 'futures', 'crypto_sources'])
def test_admin_mode_is_exclusive_before_output_or_launch(tmp_path, other):
    with patch.object(admin, 'administrator_command', side_effect=AssertionError('Must not launch')):
        with pytest.raises(ValueError):
            admin.run_admin_rehearsal(tmp_path / 'absent', development_only=True, forex_cash_sources=True, **{other: True})
    assert not (tmp_path / 'absent').exists()


@pytest.mark.parametrize('other', ['verify_dashboard', 'verify_preferences', 'verify_application', 'verify_futures', 'verify_crypto'])
def test_runner_mixed_modes_cannot_connect(tmp_path, other):
    with patch('psycopg.connect', side_effect=AssertionError('Must not connect')):
        with pytest.raises(ValueError):
            runner.run_rehearsal(tmp_path / 'absent', development_only=True, verify_forex_cash=True, **{other: True})
    assert not (tmp_path / 'absent').exists()


def test_admin_transport_requires_complete_private_receipts(tmp_path):
    names = ['prewrite-recovery-receipt.json', 'reviewed-grant-plan.json',
        'reviewed-publication-grant-plan.json', 'committed-publication-resources.json',
        'forex-cash-source-receipts-report.json']
    def line(name):
        return admin.PREFIX + json.dumps(dict(name=name, data=base64.b64encode(b'{"passed":true}').decode())) + '\n'
    def bundle(path, *, job):
        assert job == 'forex_cash_sources'
        path.write_bytes(b'synthetic')
        return 'a' * 64
    for complete in (True, False):
        output = tmp_path / str(complete)
        process = SimpleNamespace(stdout=io.StringIO(''.join(line(n) for n in (names if complete else names[1:]))), wait=lambda: 0)
        with patch.object(admin, 'restore_conninfo'), patch.object(admin, 'administrator_command', return_value=['synthetic']), \
             patch.object(admin, 'build_bundle', side_effect=bundle), patch.object(admin.subprocess, 'Popen', return_value=process):
            if complete:
                assert admin.run_admin_rehearsal(output, development_only=True, forex_cash_sources=True)['passed']
                assert (output / names[-1]).stat().st_mode & 0o777 == 0o600
            else:
                with pytest.raises(RuntimeError, match='receipts incomplete'):
                    admin.run_admin_rehearsal(output, development_only=True, forex_cash_sources=True)


@pytest.mark.skipif(os.environ.get('CAMPAIGNIQ_FOREX_CASH_SOURCES_TEST') != '1',
    reason='Explicit postgres disposable-clone Forex source-save rehearsal required')
def test_actual_restricted_forex_source_save_on_disposable_database(tmp_path):
    report = runner.run_rehearsal(tmp_path / 'forex', development_only=True, verify_forex_cash=True)
    assert report['passed'] and report['disposable_database_and_roles_removed']
    assert report['checks']['caught_forbidden_sql_cannot_commit_partial_forex_sources']
    assert report['checks']['explicit_forex_replacement_preserves_prior_original_and_receipt_and_financial_artifacts_are_unchanged']

    assert report['checks']['approved_forex_independent_reader_rebuilds_exact_money_original_sources_fifo_identity_and_dashboard_without_writes']
    assert report['checks']['combined_forex_futures_crypto_concurrent_publications_have_one_winner_and_repeatable_reader_sees_no_partial_generation']
    assert report['checks']['approved_forex_following_month_commits_from_published_opening_and_previous_generation_is_identical']

    assert report['checks']['no_trading_independent_reader_verifies_dividend_separate_from_zero_trading_and_november_opening_preserves_exact_lots_and_campaigns']

    assert report['checks']['income_active_month_reader_and_dashboard_include_dividends_interest_and_costs_without_changing_trade_totals_or_rows']
    assert report['checks']['income_reader_database_transaction_rejects_raw_sql_writes_and_preserves_every_row']
