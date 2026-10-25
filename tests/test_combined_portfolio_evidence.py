import json
from copy import deepcopy
import pytest

from campaigniq.historical_backfill_portfolio_evidence import (
    MARKET_KEY,BASIS_KEY,CONSUMPTION_KEY,CHECK_KEYS,validate_check_payloads,require_reviewed_checks,
)
from campaigniq.historical_backfill_combined_rehearsal import stage,InjectedFailure,CONTINUITY_KEY,PROTOCOL_FILE,TARGETS
from tests.test_historical_combined_rehearsal import Connection,originals
from tests.test_historical_publication_rehearsal import complete_generation


@pytest.fixture
def checks():
    return {key:json.dumps(dict(format=fmt,version=1,publication_authorized=False)) for key,fmt in
        ((MARKET_KEY,'campaigniq.market_continuity_review'),(BASIS_KEY,'campaigniq.aggregate_basis_provenance'),
         (CONSUMPTION_KEY,'campaigniq.downstream_basis_consumption'))}


def test_combined_insert_scope_contains_all_three_proofs_before_protocol(complete_generation,checks):
    connection=Connection()
    stage(connection,originals(),complete_generation,{k:'correction' for k in TARGETS},'graph','audit','archive',checks=checks)
    inserts=[params[1] for query,params in connection.calls if query.startswith('INSERT')]
    assert set(inserts)==set(complete_generation)|CHECK_KEYS|{CONTINUITY_KEY,'audit'}
    assert connection.calls[-1][0].startswith('UPDATE') and connection.calls[-1][1][2]==PROTOCOL_FILE
    assert all('ON CONFLICT' not in query for query,_ in connection.calls)


def test_injected_failure_occurs_after_a_proof_is_inserted(complete_generation,checks):
    connection=Connection()
    with pytest.raises(InjectedFailure,match='evidence insertion'):
        stage(connection,originals(),complete_generation,{k:'correction' for k in TARGETS},'graph','audit','archive',checks=checks,inject=True)
    inserted={params[1] for query,params in connection.calls if query.startswith('INSERT')}
    assert set(complete_generation)<=inserted and CONTINUITY_KEY in inserted
    assert len(inserted&CHECK_KEYS)==1
    assert not any(query.startswith('UPDATE') and params[2]==PROTOCOL_FILE for query,params in connection.calls)


@pytest.mark.parametrize('change',['missing','extra','format','version','authorization'])
def test_invalid_proof_scope_is_refused_before_any_sql(complete_generation,checks,change):
    if change=='missing':del checks[BASIS_KEY]
    if change=='extra':checks['2026-03-lot-book.json']='overwrite'
    if change in ('format','version','authorization'):
        obj=json.loads(checks[BASIS_KEY]);obj[{'format':'format','version':'version','authorization':'publication_authorized'}[change]]={'format':'other','version':2,'authorization':True}[change];checks[BASIS_KEY]=json.dumps(obj)
    connection=Connection()
    with pytest.raises(ValueError):stage(connection,originals(),complete_generation,{k:'correction' for k in TARGETS},'graph','audit','archive',checks=checks)
    assert not connection.calls


def test_review_comparison_is_semantic_and_does_not_mutate_sources(checks):
    before=deepcopy(checks)
    supplied={key:json.dumps(json.loads(text),indent=2) for key,text in checks.items()}
    require_reviewed_checks(checks,supplied)
    assert checks==before


@pytest.mark.parametrize('key',sorted(CHECK_KEYS))
def test_changed_prior_review_is_not_silently_replaced(checks,key):
    supplied=deepcopy(checks);obj=json.loads(supplied[key]);obj['changed_fact']='different';supplied[key]=json.dumps(obj)
    with pytest.raises(ValueError,match='reviewed|Reviewed'):require_reviewed_checks(checks,supplied)


def test_incomplete_prior_review_is_rejected(checks):
    supplied=deepcopy(checks);del supplied[MARKET_KEY]
    with pytest.raises(ValueError):require_reviewed_checks(checks,supplied)


def test_empty_checks_cannot_downgrade_an_extended_rehearsal(complete_generation):
    connection=Connection()
    with pytest.raises(ValueError):stage(connection,originals(),complete_generation,{k:'correction' for k in TARGETS},'graph','audit','archive',checks={})
    assert not connection.calls
