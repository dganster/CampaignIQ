"""Exercise every rehearsal call without requiring PostgreSQL.

SQL locking/rollback is covered by the separately opt-in PostgreSQL contract.
This catches orchestration errors before asking the user to run that contract.
"""
from unittest.mock import Mock

from campaigniq import account_signedin_admin_rehearsal as rehearsal
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory


def test_all_signedin_admin_rehearsal_commands_reach_the_policy(tmp_path, monkeypatch):
    state = []
    store = object.__new__(LocalPostgresAccountDirectory)
    store.registry_id = '00000000-0000-0000-0000-000000000001'
    store.initialize = Mock()
    store.bootstrap = lambda initial: state.append(initial)
    store._snapshot = lambda: state[0]
    store._append_event = Mock()

    def execute(actor, revision, command, **values):
        updated = getattr(state[0], command)(actor, expected_revision=revision, **values)
        store._append_event(None, updated.events[-1])
        state[0] = updated
        return updated.revision

    store.execute = execute
    monkeypatch.setattr(rehearsal, 'LocalPostgresAccountDirectory', lambda *a, **k: store)
    monkeypatch.setattr(rehearsal, 'protected_rows', lambda *a, **k: {'prior_rows': 'unchanged'})
    report = rehearsal.run_rehearsal(tmp_path / 'report')
    assert report['local_rehearsal_passed']
    assert len(report['checks']) == 8 and all(report['checks'].values())
