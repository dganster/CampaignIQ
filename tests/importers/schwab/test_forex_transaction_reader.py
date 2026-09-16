from decimal import Decimal
from pathlib import Path
import pytest
from campaigniq.importers.schwab.forex_transaction_reader import read_forex_transaction_report
def put(tmp_path,text):
    p=tmp_path/"Forex Transaction Report.csv"; p.write_text(text); return p
def test_reads_settlement_and_financing(tmp_path):
    text='"Transaction Report since Jul 31, 2026 16:00:00 (EDT) through Aug 31, 2026 16:00:00 (EDT)"\n"MTD Settled PL, USD:",+3.00\n"MTD fee, USD:",0.00\n="129305452144","Aug 27, 2026 17:12:31","Aug 27, 2026 17:12:31",financing,USD$FX,1.1652,-6.67,-6.67 USD\n="1007745626983","Aug 27, 2026 20:19:57","Aug 28, 2026 17:00:00",settlement,EUR/USD,Sell,1.16508,"-100,000","+116,508",0.00,,"+3.00 USD",0,,"+3.00 USD"\n'
    r=read_forex_transaction_report(put(tmp_path,text))
    assert r.mtd_settled_pl_usd==Decimal("3.00")
    assert r.settlement_pl_usd==Decimal("3.00")
    assert r.financing_usd==Decimal("-6.67")
    assert r.settlements[0].instrument=="EUR/USD"
def test_zero_activity(tmp_path):
    text='"Transaction Report since Jun 30, 2026 through Jul 31, 2026"\n"MTD Settled PL, USD:",0.00\n"MTD fee, USD:",0.00\n'
    r=read_forex_transaction_report(put(tmp_path,text)); assert r.settlements==() and r.financing==()
def test_rejects_other(tmp_path):
    with pytest.raises(ValueError): read_forex_transaction_report(put(tmp_path,"other,report\n"))

def test_financing_with_conversion_rate_uses_usd_amount(tmp_path):
    text='"Transaction Report since Mar 31, 2026 through Apr 30, 2026"\n"MTD Settled PL, USD:",0.00\n"MTD fee, USD:",0.00\n="116469481550","Apr 13, 2026 17:12:21","Apr 13, 2026 17:12:21",financing,USD$FX,.78479,+6.37,+6.37 USD\n'
    r=read_forex_transaction_report(put(tmp_path,text))
    assert r.financing_usd == Decimal("6.37")
