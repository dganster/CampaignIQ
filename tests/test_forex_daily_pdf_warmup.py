"""Daily extraction reuse cannot replace source or financial validation."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from campaigniq import account_forex_daily_inventory as daily
from campaigniq import pdf_text
from campaigniq.broker_account_binding import BrokerBinding
from campaigniq.forex_daily_rehearsal_sources import upload
from tests.test_account_forex_inventory_preview import context


@pytest.fixture(autouse=True)
def empty_cache():
    pdf_text.clear_extraction_cache()
    yield
    pdf_text.clear_extraction_cache()


def run(c, raw=None):
    return daily.evaluate(c.service,c.session,c.draft,upload(complete=True).content if raw is None else raw)


def test_real_pdf_warmup_matches_sequential_full_evidence_without_row_changes(context):
    before=deepcopy((context.docs,context.session.artifacts.rows))
    with patch.object(daily,'warm_extraction_cache',return_value=None):sequential=run(context)
    pdf_text.clear_extraction_cache()
    with patch.object(daily,'warm_extraction_cache',wraps=pdf_text.warm_extraction_cache) as warm:
        warmed=run(context)
    assert warmed==sequential
    assert warmed['full_independent_daily_coverage'] and warmed['combined_inventory_evidence_reconciled']
    assert warm.call_count==1 and len(warm.call_args.args[0])==22
    assert (context.docs,context.session.artifacts.rows)==before


def test_previously_warmed_text_cannot_bypass_current_broker_binding(context):
    run(context)
    known=dict(context.service._known(context.session))
    known['forex']=BrokerBinding.create('forex','8642098')
    context.service._known=lambda session:known
    with pytest.raises(ValueError):run(context)


def test_warmup_return_value_cannot_replace_source_and_changed_pdf_is_rejected(context):
    run(context)
    with patch.object(daily,'warm_extraction_cache',return_value={'combined_inventory_evidence_reconciled':True}):
        with pytest.raises(ValueError):run(context,upload(number='8642098',complete=True).content)


def test_worker_start_failure_falls_back_to_complete_sequential_validation(context):
    with patch.object(daily,'warm_extraction_cache',return_value=None):expected=run(context)
    pdf_text.clear_extraction_cache()
    with patch('concurrent.futures.ThreadPoolExecutor',side_effect=RuntimeError('workers unavailable')):
        assert run(context)==expected


def test_warm_pdf_text_does_not_bypass_changed_canonical_predecessor(context):
    run(context)
    del context.session.artifacts.rows['2026-09-finalized.json']
    result=run(context)
    assert not result['authoritative_opening_matches']
    assert not result['combined_inventory_evidence_reconciled']
