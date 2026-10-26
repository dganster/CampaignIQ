"""Full account identity, audit policy and bound-preview regression tests."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import pytest

from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.broker_account_binding import BrokerBinding,bind,bindings,document_identity,normalize,verify_document
from campaigniq.import_draft_bridge import RestoredUpload
from tests.test_account_import_preview import context


@pytest.mark.parametrize('kind,number,expected',[
    ('securities','1234-5678','12345678'),('securities','00123456','00123456'),
    ('forex','001234567','001234567')])
def test_full_identifier_normalization(kind,number,expected):
    assert normalize(kind,number)==expected
    b=BrokerBinding.create(kind,number)
    assert b.suffix==expected[-4:]
    with pytest.raises(FrozenInstanceError): b.suffix='0000'


@pytest.mark.parametrize('kind,number',[
    ('securities','****5678'),('securities','1234 5678'),('securities','１２３４５６７８'),
    ('securities','123456789'),('forex','1234'),('forex','1234567junk'),('crypto','12345678'),
    ('securities',12345678)])
def test_masked_malformed_and_unsupported_identifiers_fail_closed(kind,number):
    with pytest.raises(ValueError): normalize(kind,number)


def test_binding_is_admin_only_revision_checked_and_retains_history(context):
    c=context
    with pytest.raises(PermissionError):
        bind(c.directory,c.member,c.other,'securities','12345678',expected_revision=0)
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    first=c.directory
    assert first.revision==1 and bindings(first,c.account)['securities'].suffix=='5678'
    assert '12345678' not in repr(first.events)
    assert bind(first,c.admin,c.account,'securities','1234-5678',expected_revision=1)==first
    with pytest.raises(ValueError):
        bind(first,c.admin,c.account,'securities','87654321',expected_revision=0,confirm_replace=True)
    with pytest.raises(ValueError):
        bind(first,c.admin,c.account,'securities','87654321',expected_revision=1)
    updated=bind(first,c.admin,c.account,'securities','87654321',expected_revision=1,confirm_replace=True)
    assert updated.events[:-1]==first.events and bindings(updated,c.account)['securities'].suffix=='4321'


def test_duplicate_binding_cannot_be_assigned_to_different_account_even_if_archived(context):
    c=context
    d=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    d=d.configure_account(c.admin,c.account,archived=True,expected_revision=1)
    with pytest.raises(ValueError): bind(d,c.admin,c.other,'securities','12345678',expected_revision=2)
    with pytest.raises(ValueError): bind(d,c.admin,c.account,'forex','1234567',expected_revision=2)


def test_securities_and_forex_are_independent(context):
    c=context
    d=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    d=bind(d,c.admin,c.account,'forex','7654321',expected_revision=1)
    known=bindings(d,c.account)
    assert set(known)=={'securities','forex'}
    verify_document(known,'schwab_forex_transaction_report','Account (USD): 7654321, Base currency: USD')
    with pytest.raises(ValueError): verify_document(known,'schwab_forex_transaction_report','Account (USD): 12345678,')


@pytest.mark.parametrize('role,text,kind',[
    ('schwab_brokerage_statement','Account Number    Statement Period\nOWNER\n1234-5678   January 2025','securities'),
    ('schwab_opening_position_snapshot','Account Number\n1234-5678','securities'),
    ('schwab_realized_gain_loss','Account Number: 1234-5678','securities'),
    ('schwab_next_month_assignment_evidence','Account Number\n1234-5678','securities'),
    ('thinkorswim_trade_history','Account Statement for 12345678 since 1/1/25 through 1/31/25','securities'),
    ('schwab_forex_transaction_report','Account (USD): 7654321, Base currency: USD','forex'),
    ('schwab_forex_monthly_statement','Account Account Currency\n7654321 USD','forex'),
])
def test_supported_explicit_headers(role,text,kind):
    assert document_identity(role,text).kind==kind


@pytest.mark.parametrize('role,text',[
    ('schwab_brokerage_statement','Account Number\n****-5678'),
    ('schwab_brokerage_statement','Account Number\n1234-5678\nAccount Number\n8765-4321'),
    ('schwab_brokerage_statement','Account Number\n****-5678\nOWNER\nOWNER\nAccount Number\n1234-5678'),
    ('thinkorswim_trade_history','Account Statement synthetic\nTRADE 12345678'),
    ('thinkorswim_trade_history','Account Statement for ****5678 since 1/1/25'),
    ('schwab_forex_transaction_report','Account (USD): 7654321,\nAccount (USD): ****4321,'),
    ('schwab_forex_monthly_statement','Account Account Currency\n7654321 USD\nAccount Account Currency\n1234567 USD'),
    ('schwab_crypto_statement','Schwab Crypto Account\n****-****1234'),
])
def test_missing_masked_ambiguous_and_unsupported_headers_are_blocked(role,text):
    with pytest.raises(ValueError): document_identity(role,text)


@pytest.fixture
def bound_context(context):
    c=context
    c.service.gateway._directory=SimpleNamespace(_load=lambda connection:c.directory)
    c.bound=BoundAccountImportPreviewService(c.service.gateway)
    return c


def test_wrong_account_upload_does_not_create_or_replace_documents(bound_context):
    c=bound_context
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    d=c.bound.create(c.admin,c.account,2025,1)
    matching=RestoredUpload('matching.csv','text/csv',b'Account Statement for 12345678 since 1/1/25 through 1/31/25')
    wrong=RestoredUpload('wrong.csv','text/csv',b'Account Statement for 87654321 since 1/1/25 through 1/31/25')
    with pytest.raises(ValueError,match='different broker account'):
        c.bound.save(c.admin,c.account,d.id,0,'thinkorswim_trade_history',wrong)
    assert c.repositories[c.account].get(d.id).revision==0 and not c.repositories[c.account].docs
    c.bound.save(c.admin,c.account,d.id,0,'thinkorswim_trade_history',matching)
    with pytest.raises(ValueError): c.bound.save(c.admin,c.account,d.id,1,'thinkorswim_trade_history',wrong,replace=True)
    assert c.repositories[c.account].document(d.id,'thinkorswim_trade_history')[2]==matching.content
    assert not c.repositories[c.account].originals
    result=c.bound.preview(c.admin,c.account,d.id,1)
    assert any(v.role=='account:thinkorswim_trade_history' and v.valid for v in result.validations)
    assert not result.inputs_ready # identity does not waive missing financial evidence


def test_preexisting_wrong_document_and_binding_change_invalidate_preview(bound_context):
    c=bound_context
    c.directory=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    d=c.bound.create(c.admin,c.account,2025,1)
    upload=RestoredUpload('old.csv','text/csv',b'Account Statement for 87654321 since 1/1/25 through 1/31/25')
    c.service.save(c.admin,c.account,d.id,0,'thinkorswim_trade_history',upload)
    result=c.bound.preview(c.admin,c.account,d.id,1)
    assert not result.inputs_ready
    assert any(not v.valid and 'different broker account' in v.message for v in result.validations)
    c.directory=bind(c.directory,c.admin,c.account,'securities','87654321',expected_revision=1,confirm_replace=True)
    assert any(v.role.startswith('account:') and v.valid for v in c.bound.preview(c.admin,c.account,d.id,1).validations)


def test_unbound_account_cannot_save_and_preview_is_blocked(bound_context):
    c=bound_context;d=c.bound.create(c.admin,c.account,2025,1)
    upload=RestoredUpload('x.csv','text/csv',b'Account Statement for 12345678 since 1/1/25 through 1/31/25')
    with pytest.raises(ValueError,match='Bind the securities'):
        c.bound.save(c.admin,c.account,d.id,0,'thinkorswim_trade_history',upload)
    result=c.bound.preview(c.admin,c.account,d.id,0)
    assert not result.inputs_ready and any(v.role=='broker_account_binding' for v in result.validations)


def test_binding_event_round_trips_through_existing_directory_codec(context):
    from campaigniq.persistence.account_directory_codec import encode,decode
    c=context
    changed=bind(c.directory,c.admin,c.account,'securities','12345678',expected_revision=0)
    assert decode(encode(changed))==changed


def test_browser_binding_form_uses_admin_command_and_masks_display(context,monkeypatch):
    from tests.test_account_import_preview import _app
    from campaigniq.persistence.postgres_broker_binding import LocalBrokerBindingStore
    c=context;calls=[]
    def saved(store,identity,account,kind,number,**kwargs):
        calls.append((identity,account,kind,number,kwargs))
        c.directory=bind(c.directory,identity,account,kind,number,**kwargs)
        return c.directory.revision
    monkeypatch.setattr(LocalBrokerBindingStore,'bind',saved)
    app=_app(c,monkeypatch)
    field=next(t for t in app.text_input if t.label=='Full synthetic broker account identifier')
    field.set_value('12345678')
    next(b for b in app.button if b.label=='Save broker binding').click().run()
    assert not app.exception and len(calls)==1
    assert calls[0][4]=={'expected_revision':0,'confirm_replace':False}
    assert any('securities ending 5678' in caption.value for caption in app.caption)
    assert not any('12345678' in caption.value for caption in app.caption)
    next(s for s in app.selectbox if s.label=='Demo user').select(c.directory.user(c.member).id).run()
    assert not app.exception
    assert not any(b.label=='Save broker binding' for b in app.button)
