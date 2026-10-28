from decimal import Decimal
import json
import pytest
from campaigniq.account_administration import Permission
from campaigniq.account_legacy_access_rehearsal import RehearsalSession
from tests.test_account_legacy_access import harness,Result,A,B
from tests.test_account_local_publication import local,month,bound_context,context,publish


@pytest.fixture
def retained(harness,local):
    h=harness;publish(local);h.rows=dict(local.artifacts[local.account].rows)
    original=h.conn.execute
    def execute(sql,params=()):
        q=' '.join(sql.split())
        if 'artifact_storage_dev' not in q or 'FROM pg_class' in q:return original(sql,params)
        h.conn.queries.append((q,params))
        assert params[0]=='workspace-a'
        if q.startswith('SELECT artifact_key,content'):
            return Result([(k,v) for k,v in sorted(h.rows.items()) if k.startswith(params[1])])
        if q.startswith('SELECT artifact_key FROM'):
            return Result([(k,) for k in sorted(h.rows)])
        if q.startswith('SELECT 1 FROM'):
            return Result([(1,)] if params[1] in h.rows else [])
        if q.startswith('SELECT content FROM'):
            text=h.rows.get(params[1]);return Result([] if text is None else [(text+' ' if h.conn.bad_artifact else text,)])
        raise AssertionError(q)
    h.conn.execute=execute
    return h


def read(h):return h.reader.published_results(h.user,h.account,2025,1)


def test_legacy_results_use_existing_namespace_and_canonical_loaders_without_writes(retained):
    h=retained;before=dict(h.rows)
    assert h.reader.published_months(h.user,h.account)==('2025-01',)
    result=read(h)
    assert result.account_id==h.account and result.month=='2025-01'
    assert result.positions[0][0]=='SPY' and Decimal(result.positions[0][1])==110
    assert result.brokerage_realized_usd=='0' and result.forex_settled_usd=='0'
    assert result.source_artifacts==10 and len(result.source_sha256)==64
    assert h.rows==before and all(h.modes)
    assert not any(q.startswith(('CREATE','INSERT','UPDATE','DELETE')) for q,_ in h.conn.queries)


def test_implicit_pre_cutover_publication_semantics_retained_but_empty_month_not_published(retained):
    h=retained;h.rows.pop('.campaigniq-monthly-publication-v1.json');h.rows.pop('2025-01-finalized.json')
    assert h.reader.published_months(h.user,h.account)==('2025-01',)
    assert Decimal(read(h).positions[0][1])==110
    with pytest.raises(ValueError):h.reader.published_results(h.user,h.account,2025,2)


def test_missing_protected_marker_is_not_listed_or_read(retained):
    h=retained;h.rows.pop('2025-01-finalized.json')
    assert h.reader.published_months(h.user,h.account)==()
    with pytest.raises(ValueError):read(h)


def test_forex_missing_is_unknown_coverage_not_zero(retained):
    h=retained;h.rows.pop('2025-01-forex-settlement-attributions.json')
    result=read(h)
    assert result.forex_settled_usd is None and result.forex_records is None
    assert any('coverage is unknown' in warning for warning in result.warnings)


def test_direct_source_and_adapter_difference_blocks_entire_result(retained):
    h=retained;h.conn.bad_artifact=True
    with pytest.raises(ValueError,match='source adapter parity'):read(h)


@pytest.mark.parametrize('key',['lot-book.json','realized-attributions.json','finalized.json'])
def test_wrong_embedded_period_or_marker_blocks_results(retained,key):
    h=retained;name='2025-01-'+key;value=json.loads(h.rows[name]);value['period_end']='2025-02-28'
    h.rows[name]=json.dumps(value)
    with pytest.raises(ValueError):read(h)


def test_realized_artifact_missing_does_not_return_partial_positions(retained):
    h=retained;h.rows.pop('2025-01-realized-attributions.json')
    with pytest.raises(FileNotFoundError):read(h)


def test_current_signin_owner_view_revocation_and_archiving_are_rechecked(retained):
    h=retained
    with pytest.raises(PermissionError):h.reader.published_results(h.user,h.other,2025,1)
    h.current[0]=h.current[0].set_grant(A,h.initial.users[1].id,h.account,Permission.VIEW,expected_revision=0)
    assert h.reader.published_results(RehearsalSession(B),h.account,2025,1)==read(h)
    h.current[0]=h.current[0].set_grant(A,h.initial.users[1].id,h.account,None,expected_revision=1)
    with pytest.raises(PermissionError):h.reader.published_results(RehearsalSession(B),h.account,2025,1)
    h.current[0]=h.current[0].configure_account(A,h.account,archived=True,expected_revision=2)
    assert Decimal(read(h).positions[0][1])==110


@pytest.mark.parametrize('reported_year,reported_month',[(True,1),(2025,True),(2025,0),(2025,13),('2025',1)])
def test_strict_month_rejected_before_financial_queries(retained,reported_year,reported_month):
    h=retained
    with pytest.raises(ValueError):h.reader.published_results(h.user,h.account,reported_year,reported_month)
    assert not h.conn.queries
