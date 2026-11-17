from hashlib import sha256
from unittest.mock import patch,Mock
import json
from campaigniq import account_income_evidence as income
from campaigniq.statement_text_cache import using_text_cache
from campaigniq.broker_account_binding import BrokerBinding
from tests.test_income_evidence import legacy,statement

def pdf_inputs():
    store,_,known=legacy();raw=b'%PDF synthetic';text=statement()
    row=('id',sha256(raw).hexdigest(),raw,'application/pdf',2025,1)
    return store,[row],known,text

def test_cached_and_fresh_income_equal_and_no_writes_on_hit(tmp_path):
    store,rows,known,text=pdf_inputs();root=tmp_path/'cache'
    with patch.object(income,'statement_text',return_value=text) as extract,using_text_cache(root):
        first=income.legacy_statements(store,rows,known,('2025-01',))
    assert first[0].status=='verified' and extract.call_count==1
    with patch.object(income,'statement_text',side_effect=AssertionError('unexpected extraction')),patch('campaigniq.statement_text_cache.StatementTextCache.store') as write,using_text_cache(root):
        second=income.legacy_statements(store,rows,known,('2025-01',))
    assert first==second;write.assert_not_called()

def test_wrong_binding_and_changed_provenance_still_withhold_income(tmp_path):
    store,rows,known,text=pdf_inputs();root=tmp_path/'cache'
    with patch.object(income,'statement_text',return_value=text),using_text_cache(root):
        assert income.legacy_statements(store,rows,known,('2025-01',))[0].status=='verified'
    with patch.object(income,'statement_text',return_value=text),using_text_cache(root):
        assert income.legacy_statements(store,rows,{'securities':BrokerBinding.create('securities','87654321')},('2025-01',))[0].status=='unverified'
        manifest=json.loads(store.rows['2025-01-import-provenance.json']);manifest['inputs'][0]['sha256']='0'*64
        store.rows['2025-01-import-provenance.json']=json.dumps(manifest)
        assert income.legacy_statements(store,rows,known,('2025-01',))[0].status=='unverified'

def test_corrupt_cache_falls_back_to_exact_original_validation(tmp_path):
    store,rows,known,text=pdf_inputs();root=tmp_path/'cache'
    with patch.object(income,'statement_text',return_value=text),using_text_cache(root):first=income.legacy_statements(store,rows,known,('2025-01',))
    next(root.glob('*.txt')).write_text('corruption')
    with patch.object(income,'statement_text',return_value=text) as extract,using_text_cache(root):second=income.legacy_statements(store,rows,known,('2025-01',))
    assert first==second and extract.call_count==1

def test_failed_identity_check_cannot_populate_cache(tmp_path):
    store,rows,_,text=pdf_inputs();root=tmp_path/'cache'
    with patch.object(income,'statement_text',return_value=text),using_text_cache(root):
        result=income.legacy_statements(store,rows,{'securities':BrokerBinding.create('securities','87654321')},('2025-01',))
    assert result[0].status=='unverified' and not root.exists()
