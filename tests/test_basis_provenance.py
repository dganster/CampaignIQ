from copy import deepcopy
from decimal import Decimal as D

import pytest

from campaigniq.historical_backfill_basis_provenance import short_sale, purchase, verify_quote, verify_opening_sale, number


@pytest.fixture
def sale():
    quote='12/01 Sale Short Sale XYZ 01/16/2026 PUT COMPANY $100 (5.0000) 10.0000 3.31 4,996.69\n100.00 P EXP 01/16/26\nCommission $3.25; Industry Fee $0.06'
    source=dict(statement_pdf_sha256='source',text_line=2,quote=quote,quantity='5',price='10',fees='3.31',net_proceeds='4996.69')
    instrument=dict(type='option',underlying='XYZ',expiration='2026-01-16',strike='100',option_type='PUT')
    return source,{'source':'Header\n'+quote},instrument


def test_exact_net_proceeds_and_no_source_mutation(sale):
    before=deepcopy(sale)
    assert short_sale(*sale)==(D(5),D('4996.69'))
    assert sale==before


@pytest.mark.parametrize('field,value', [('underlying','ABC'),('expiration','2026-02-20'),('strike','101'),('option_type','CALL')])
def test_other_contract_cannot_supply_basis(sale,field,value):
    source,texts,instrument=sale;instrument[field]=value
    with pytest.raises(ValueError):short_sale(source,texts,instrument)


@pytest.mark.parametrize('field,value', [('quantity','4'),('price','9'),('fees','0'),('net_proceeds','5000')])
def test_manifest_columns_cannot_override_source(sale,field,value):
    source,texts,instrument=sale;source[field]=value
    with pytest.raises(ValueError):short_sale(source,texts,instrument)


def test_incorrect_source_arithmetic_is_rejected_even_if_manifest_agrees(sale):
    source,texts,instrument=sale
    source['quote']=source['quote'].replace('4,996.69','4,996.68');source['net_proceeds']='4996.68'
    texts['source']='Header\n'+source['quote']
    with pytest.raises(ValueError,match='arithmetic'):short_sale(source,texts,instrument)


@pytest.mark.parametrize('change',['quote','line','missing_pdf'])
def test_quotation_requires_exact_retained_text(sale,change):
    source,texts,_=sale
    if change=='quote':source['quote']=source['quote'].replace('10.0000','11.0000')
    if change=='line':source['text_line']=1
    if change=='missing_pdf':texts={}
    with pytest.raises((ValueError,KeyError)):verify_quote(source,texts)


def test_page_break_delimiter_does_not_change_quoted_line_contents(sale):
    source,texts,_=sale
    source['quote']=source['quote'].replace('\n','\f',1)
    assert verify_quote(source,texts)==source['quote']


def test_purchase_cash_is_exact_without_invented_fees():
    quote='Purchase XYZ COMPANY 500.0000 100.0000 (50,000.00)'
    source=dict(statement_pdf_sha256='source',text_line=1,quote=quote)
    assert purchase(source,{'source':quote},'XYZ')==(D(500),D(100),D(50000))
    with pytest.raises(ValueError):purchase(source,{'source':quote},'ABC')
    source['quote']=quote.replace('50,000.00','49,999.00')
    with pytest.raises(ValueError):purchase(source,{'source':source['quote']},'XYZ')


def test_matching_lot_openings_must_have_exact_gross_cash(sale):
    source,_,instrument=sale
    rows=[dict(instrument=instrument,action='Roll: open',quantity_change='-2',price='10',lots=[dict(lot_id='A')]),
        dict(instrument=instrument,action='Roll: open',quantity_change='-3',price='10',lots=[dict(lot_id='B')])]
    assert verify_opening_sale(rows,instrument,{'A','B'},source)==rows
    with pytest.raises(ValueError):verify_opening_sale(rows,instrument,{'A'},source)
    rows[1]['price']='10.01'
    with pytest.raises(ValueError):verify_opening_sale(rows,instrument,{'A','B'},source)


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity'])
def test_nonfinite_basis_is_never_accepted(value):
    with pytest.raises(ValueError):number(value)
