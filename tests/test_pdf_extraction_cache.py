from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import pdf_text as pdf

@pytest.fixture(autouse=True)
def reset_cache():
    pdf.clear_extraction_cache();yield;pdf.clear_extraction_cache()

def runner(monkeypatch,callback=None):
    calls=[]
    def run(args,**kwargs):
        data=Path(args[-2]).read_bytes();calls.append(data)
        if callback:callback(data)
        Path(args[-1]).write_bytes(b'layout:'+data)
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr(pdf.shutil,'which',lambda name:'/fake/pdftotext')
    monkeypatch.setattr(pdf.subprocess,'run',run)
    return calls

def test_identical_bytes_reuse_across_source_paths_and_keep_destination_independent(monkeypatch,tmp_path):
    calls=runner(monkeypatch);a=tmp_path/'a.pdf';b=tmp_path/'b.pdf';a.write_bytes(b'one');b.write_bytes(b'one')
    first=pdf.extract_pdf_text(a,tmp_path/'one.txt');first.write_text('changed destination')
    second=pdf.extract_pdf_text(b,tmp_path/'two.txt')
    assert calls==[b'one'] and second.read_bytes()==b'layout:one'

def test_same_path_changed_bytes_never_reuses_old_output(monkeypatch,tmp_path):
    calls=runner(monkeypatch);source=tmp_path/'same.pdf';source.write_bytes(b'one')
    pdf.extract_pdf_text(source,tmp_path/'out.txt');source.write_bytes(b'two')
    result=pdf.extract_pdf_text(source,tmp_path/'out.txt')
    assert calls==[b'one',b'two'] and result.read_bytes()==b'layout:two'

def test_private_snapshot_prevents_hash_extract_race(monkeypatch,tmp_path):
    source=tmp_path/'same.pdf';source.write_bytes(b'one')
    calls=runner(monkeypatch,lambda data:source.write_bytes(b'two'))
    result=pdf.extract_pdf_text(source,tmp_path/'out.txt')
    assert result.read_bytes()==b'layout:one'
    result=pdf.extract_pdf_text(source,tmp_path/'out2.txt')
    assert result.read_bytes()==b'layout:two' and calls==[b'one',b'two']

def test_extractor_identity_change_invalidates_cache(monkeypatch,tmp_path):
    calls=runner(monkeypatch);source=tmp_path/'same.pdf';source.write_bytes(b'one')
    pdf.extract_pdf_text(source,tmp_path/'one.txt')
    monkeypatch.setattr(pdf,'_tool_identity',lambda x:(x,'new version'))
    pdf.extract_pdf_text(source,tmp_path/'two.txt');assert len(calls)==2

def test_failure_is_not_cached_and_old_destination_is_not_adopted(monkeypatch,tmp_path):
    source=tmp_path/'same.pdf';source.write_bytes(b'one');out=tmp_path/'out.txt';out.write_text('stale')
    monkeypatch.setattr(pdf.shutil,'which',lambda name:'/fake/pdftotext')
    monkeypatch.setattr(pdf.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stderr='broken'))
    with pytest.raises(pdf.PdfTextExtractionError):pdf.extract_pdf_text(source,out)
    calls=runner(monkeypatch);assert pdf.extract_pdf_text(source,out).read_bytes()==b'layout:one' and len(calls)==1

def test_entry_limit_evicts_lru_and_memory_stays_bounded(monkeypatch,tmp_path):
    monkeypatch.setattr(pdf,'_CACHE_MAX_ENTRIES',1);calls=runner(monkeypatch)
    for i,value in enumerate((b'one',b'two',b'one')):
        source=tmp_path/'same.pdf';source.write_bytes(value);pdf.extract_pdf_text(source,tmp_path/f'{i}.txt')
    assert len(calls)==3 and len(pdf._cache)==1 and pdf._cache_bytes<=pdf._CACHE_MAX_BYTES

def test_oversized_entry_is_extracted_but_not_retained(monkeypatch,tmp_path):
    monkeypatch.setattr(pdf,'_CACHE_MAX_BYTES',3);calls=runner(monkeypatch);source=tmp_path/'same.pdf';source.write_bytes(b'large')
    pdf.extract_pdf_text(source,tmp_path/'one.txt');pdf.extract_pdf_text(source,tmp_path/'two.txt')
    assert len(calls)==2 and not pdf._cache and pdf._cache_bytes==0

def test_cached_text_does_not_skip_account_identity_or_statement_validation(monkeypatch):
    from campaigniq import account_income_evidence as inc
    from campaigniq.income_rehearsal_sources import controls
    text='January 1-31, 2025\n'+controls()
    monkeypatch.setattr(inc,'statement_text',lambda *a:text)
    with patch.object(inc,'verify_document') as identity:
        for _ in range(2):assert inc.read_bound_statement('2025-01',b'original','application/pdf',{},published_binding='receipt').status=='verified'
        assert identity.call_count==2
    with patch.object(inc,'verify_document',side_effect=ValueError('Wrong account')):
        with pytest.raises(ValueError):inc.read_bound_statement('2025-01',b'original','application/pdf',{},published_binding='receipt')
