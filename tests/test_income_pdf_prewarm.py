from pathlib import Path
from threading import Barrier,Lock
from types import SimpleNamespace
from unittest.mock import patch
from hashlib import sha256
import pytest
from campaigniq import pdf_text as pdf
from campaigniq import account_income_evidence as income
from tests.test_income_evidence import legacy,statement


@pytest.fixture(autouse=True)
def cache():
    pdf.clear_extraction_cache();yield;pdf.clear_extraction_cache()


def test_two_workers_extract_exact_bytes_once_and_warm_reads_skip_processes(monkeypatch,tmp_path):
    barrier=Barrier(2,timeout=5);lock=Lock();calls=[];active=0;maximum=0
    monkeypatch.setattr(pdf.shutil,'which',lambda _: '/test/pdftotext')
    def run(args,**kwargs):
        nonlocal active,maximum
        with lock:active+=1;maximum=max(maximum,active);calls.append(Path(args[2]).read_bytes())
        barrier.wait()
        Path(args[3]).write_bytes(b'layout:'+Path(args[2]).read_bytes())
        with lock:active-=1
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr(pdf.subprocess,'run',run)
    pdf.warm_extraction_cache([b'one',b'two',b'one'])
    assert sorted(calls)==[b'one',b'two'] and maximum==2
    for raw in (b'one',b'two'):
        source=tmp_path/'source.pdf';source.write_bytes(raw)
        assert pdf.extract_pdf_text(source,tmp_path/'text.txt').read_bytes()==b'layout:'+raw
    pdf.warm_extraction_cache([b'one',b'two'])
    assert len(calls)==2


@pytest.mark.parametrize('documents',([b'one'],[str(n).encode() for n in range(33)],[b'a'*(5*1024*1024),b'b'*(4*1024*1024)]))
def test_unprofitable_or_oversized_batches_stay_sequential(documents):
    with patch.object(pdf,'extract_pdf_text') as extract:
        pdf.warm_extraction_cache(documents)
    extract.assert_not_called()


def test_failed_warmup_is_not_cached_and_regular_read_retries(monkeypatch,tmp_path):
    monkeypatch.setattr(pdf.shutil,'which',lambda _: '/test/pdftotext')
    calls=[]
    def run(args,**kwargs):
        calls.append(1);return SimpleNamespace(returncode=1,stderr='invalid')
    monkeypatch.setattr(pdf.subprocess,'run',run)
    pdf.warm_extraction_cache([b'one',b'two'])
    assert not pdf._cache
    source=tmp_path/'source.pdf';source.write_bytes(b'one')
    with pytest.raises(pdf.PdfTextExtractionError):pdf.extract_pdf_text(source,tmp_path/'text.txt')
    assert len(calls)==3


def test_income_only_warms_current_checksum_valid_pdfs_and_rechecks_binding():
    store,rows,known=legacy();raw=statement().encode()
    pdf_row=('pdf',sha256(raw).hexdigest(),raw,'application/pdf',2025,1)
    other=('other',sha256(b'other').hexdigest(),b'other','application/pdf',2025,2)
    bad=('bad','0'*64,b'bad','application/pdf',2025,1)
    with patch.object(pdf,'warm_extraction_cache') as warm,patch.object(income,'statement_text',return_value=statement()):
        result=income.legacy_statements(store,[pdf_row,other,bad],known,('2025-01',))
        assert result[0].status=='verified' and warm.call_count==0
        from campaigniq.broker_account_binding import BrokerBinding
        foreign={'securities':BrokerBinding.create('securities','87654321')}
        assert income.legacy_statements(store,[pdf_row],foreign,('2025-01',))[0].status=='unverified'


def test_warmup_never_supplies_provenance_or_income_result():
    store,rows,known=legacy()
    with patch.object(pdf,'warm_extraction_cache',return_value='untrusted replacement'):
        good=income.legacy_statements(store,rows,known,('2025-01',))
        store.rows['2025-01-import-provenance.json']='{}'
        bad=income.legacy_statements(store,rows,known,('2025-01',))
    assert good[0].status=='verified' and bad[0].status=='unverified' and bad[0].income is None


def test_worker_startup_failure_leaves_normal_reader_available(monkeypatch):
    monkeypatch.setattr(pdf.shutil,'which',lambda _: '/test/pdftotext')
    with patch('concurrent.futures.ThreadPoolExecutor',side_effect=RuntimeError('no worker')):
        pdf.warm_extraction_cache([b'one',b'two'])
    assert not pdf._cache
