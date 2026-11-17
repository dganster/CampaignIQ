"""The actual published-result reader must prime trusted continuation adapters."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from campaigniq.account_continuation import ContinuationDrafts
from campaigniq import account_deployment_results as results
from campaigniq.persistence.deployment_account_data import _SnapshotDrafts
from campaigniq.persistence.account_data_session import ClosedAccountSession,AccountTransactionFailed
from psycopg.pq import TransactionStatus
from tests.test_snapshot_document_batch import opened


class StoppedAfterPrime(Exception):pass


def prepared(monkeypatch,*,year=2026,wrapped=True,writable=False):
    session,conn=opened(writable=writable);reader=session.drafts
    draft=SimpleNamespace(id=conn.a,year=year,month=10,revision=2)
    monkeypatch.setattr(reader,'get',lambda draft_id:draft)
    if wrapped:session.drafts=ContinuationDrafts(session,reader)
    return session,conn,reader,draft


@pytest.mark.parametrize('wrapped',[False,True])
def test_real_result_reader_primes_unwrapped_and_continuation_originals(monkeypatch,wrapped):
    session,conn,reader,draft=prepared(monkeypatch,wrapped=wrapped)
    def restore(repository,draft_id):
        assert reader._primed_scope==(reader.workspace,draft.id)
        assert repository.document(draft_id,'one')[2]==b'one'
        with pytest.raises(FileNotFoundError):repository.document(draft_id,'absent')
        raise StoppedAfterPrime
    monkeypatch.setattr(results,'restore_uploads',restore)
    with pytest.raises(StoppedAfterPrime):results._deployment_results_session(None,session,draft,'account',draft.revision)
    assert sum('count(*)' in query for query in conn.queries)==1
    assert sum('SELECT d.role,d.filename' in query for query in conn.queries)==1


def test_retained_month_is_rejected_before_any_prefetch_or_source_restore(monkeypatch):
    session,conn,reader,draft=prepared(monkeypatch,year=2025)
    restore=Mock();monkeypatch.setattr(results,'restore_uploads',restore)
    with pytest.raises(ValueError,match='Retained published history is immutable'):
        results._deployment_results_session(None,session,draft,'account',draft.revision)
    restore.assert_not_called();assert reader._primed_scope is None and not reader._documents
    assert not any('count(*)' in query for query in conn.queries)


@pytest.mark.parametrize('failure,error',[('closed',ClosedAccountSession),('failed',AccountTransactionFailed)])
def test_wrapper_rechecks_failure_even_when_underlying_batch_is_present(monkeypatch,failure,error):
    session,conn,reader,draft=prepared(monkeypatch)
    assert session.drafts.prime_documents(draft.id)
    if failure=='closed':session._active=False
    else:conn.info.transaction_status=TransactionStatus.INERROR
    with pytest.raises(error):session.drafts.prime_documents(draft.id)


def test_writable_continuation_preserves_scalar_fallback(monkeypatch):
    session,conn,reader,draft=prepared(monkeypatch,writable=True)
    assert session.drafts.prime_documents(draft.id) is False
    assert session.drafts.document(draft.id,'one')[2]==b'one'
    assert reader._primed_scope is None and not any('count(*)' in query for query in conn.queries)


def test_continuation_without_optional_optimization_keeps_reader_contract():
    session=SimpleNamespace(_check=lambda **kw:None)
    draft=SimpleNamespace(year=2026,month=10)
    reader=SimpleNamespace(get=lambda draft_id:draft)
    assert ContinuationDrafts(session,reader).prime_documents('draft') is False


def test_borrowed_reader_from_another_session_is_not_prefetched(monkeypatch):
    session,conn,reader,draft=prepared(monkeypatch)
    other,_,_,_=prepared(monkeypatch)
    other.drafts=ContinuationDrafts(other,reader)
    prime=Mock();monkeypatch.setattr(reader,'prime_documents',prime)
    monkeypatch.setattr(results,'restore_uploads',Mock(side_effect=StoppedAfterPrime))
    with pytest.raises(StoppedAfterPrime):results._deployment_results_session(None,other,draft,'account',2)
    prime.assert_not_called()
