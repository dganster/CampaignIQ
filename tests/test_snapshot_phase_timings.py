from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import navigation_timing as timing
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway
from tests.test_navigation_timing import UI
from tests.test_snapshot_binding_reuse import opened
from tests.test_snapshot_artifact_batch import opened as artifact_session

@pytest.mark.parametrize('failure',[None,'validation','body'])
def test_connection_timing_preserves_exit_order_and_original_errors(failure):
    order=[];expected=ValueError('private error text')
    @contextmanager
    def connection(*,readonly):
        order.append(('enter',readonly))
        try:yield object()
        finally:order.append('exit')
    gateway=object.__new__(PostgresAccountDataGateway);gateway._directory=SimpleNamespace(_connection=connection)
    def validate(conn):
        order.append('validate')
        if failure=='validation':raise expected
    with patch('campaigniq.persistence.deployment_account_data.validate_data_role',side_effect=validate):
        with timing.navigation_run(UI(False)) as trace:
            try:
                with gateway._connection(readonly=True):
                    order.append('body')
                    if failure=='body':raise expected
            except ValueError as error:assert error is expected and failure is not None
            else:assert failure is None
    assert order==[('enter',True),'validate']+([] if failure=='validation' else ['body'])+['exit']
    assert trace.stages['account_data_connection_setup']['calls']==1
    assert 'private error text' not in repr(trace.stages)

def test_session_phases_time_authorization_schema_and_proof_without_extra_sql():
    with timing.navigation_run(UI(False)) as trace:
        session,_=opened()
    assert trace.stages['account_data_session_authorization']['calls']==1
    assert trace.stages['account_data_session_schema']['calls']==1
    assert session._known_directory_snapshot is not None

def test_batch_phase_records_only_first_load_and_preserves_exact_query_count():
    with timing.navigation_run(UI(False)) as trace:
        session,conn=artifact_session()
        for _ in range(3):assert session.artifacts.read_text('first')=='one'
    assert len(conn.queries)==3
    assert trace.stages['snapshot_artifact_batch_read']['calls']==1

def test_all_new_stage_labels_remain_fixed_allowlisted_metadata():
    names={'account_data_connection_setup','account_data_guard_authorization','account_data_session_authorization',
        'account_data_session_schema','snapshot_account_selection','snapshot_continuation_setup','snapshot_draft_listing',
        'snapshot_publication_checks','snapshot_artifact_assembly','snapshot_artifact_batch_read','snapshot_history_source_reads'}
    assert names<=timing._STAGES
    with timing.navigation_run(UI(False)) as trace:
        for name in names:
            with timing.stage(name):pass
        with timing.stage('PRIVATE-ACCOUNT-OR-SQL'):pass
    assert set(trace.stages)==names
