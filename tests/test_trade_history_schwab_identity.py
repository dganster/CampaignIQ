import pytest
from campaigniq.broker_account_binding import BrokerBinding, document_identity, verify_document

ROLE = 'thinkorswim_trade_history'

@pytest.mark.parametrize('identifier', ['12345678SCHW (My Brokerage)', '1234-5678SCHW (My Brokerage)', '12345678SCHW'])
def test_explicit_schwab_header(identifier):
    text = f'Account Statement for {identifier} since 9/30/26 through 10/31/26'
    assert document_identity(ROLE, text) == BrokerBinding.create('securities', '12345678')

@pytest.mark.parametrize('identifier', ['****5678SCHW (My Brokerage)', '123456789SCHW (My Brokerage)', '12345678OTHER (My Brokerage)', '12345678SCHW (Other 87654321)', '12345678 junk'])
def test_malformed_headers_rejected(identifier):
    text = f'Account Statement for {identifier} since 9/30/26 through 10/31/26'
    if identifier == '12345678SCHW (Other 87654321)':
        assert document_identity(ROLE, text) == BrokerBinding.create('securities', '12345678')
    else:
        with pytest.raises(ValueError): document_identity(ROLE, text)

def test_wrong_and_conflicting_accounts_rejected():
    known = {'securities': BrokerBinding.create('securities', '87654321')}
    text = 'Account Statement for 12345678SCHW (My Brokerage) since 9/30/26'
    with pytest.raises(ValueError, match='different broker account'): verify_document(known, ROLE, text)
    with pytest.raises(ValueError): document_identity(ROLE, text + '\nAccount Statement for 87654321SCHW (Other) since 9/30/26')

def test_masked_gain_loss_stays_blocked():
    with pytest.raises(ValueError): document_identity('schwab_realized_gain_loss', 'Realized Gain / Loss\nMy Brokerage …678')
