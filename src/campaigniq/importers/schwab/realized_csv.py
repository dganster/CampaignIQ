"""Strict Schwab lot-detail CSV reader. Never infer account identity from a nickname."""
import csv
import io
import re
from datetime import datetime
from decimal import Decimal
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.realized_gain_loss import RealizedGainLossRecord
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.broker_report_period import validate_broker_report_date

PREFIX='Realized Gain/Loss - Lot Details for '
HEADERS=('Symbol','Name','Closed Date','Opened Date','Quantity','Proceeds Per Share','Cost Per Share','Proceeds','Cost Basis (CB)','Gain/Loss ($)','Gain/Loss (%)','Long Term Gain/Loss','Short Term Gain/Loss','Term','Unadjusted Cost Basis','Wash Sale?','Disallowed Loss','Transaction Closed Date','Transaction Cost Basis','Total Transaction Gain/Loss ($)','Total Transaction Gain/Loss (%)','LT Transaction Gain/Loss ($)','LT Transaction Gain/Loss (%)','ST Transaction Gain/Loss ($)','ST Transaction Gain/Loss (%)')

def csv_rows(text):
    try:return list(csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True))
    except csv.Error as error:raise ValueError('Malformed realized CSV quoting') from error

def is_csv(text):
    # Schwab quotes the title field as well as the detail fields. Detect the
    # decoded CSV field, not raw bytes; a quoted header is still the same format.
    try:
        first=next(csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True),[])
    except csv.Error:
        return False
    return bool(first) and first[0].startswith(PREFIX)

def period(text):
    rows=csv_rows(text)
    if len(rows)<2 or not rows[0] or not rows[0][0].startswith(PREFIX):raise ValueError('Not a Schwab realized lot-detail CSV')
    m=re.search(r' from ([0-9]{2}/[0-9]{2}/[0-9]{4}) to ([0-9]{2}/[0-9]{2}/[0-9]{4})$',rows[0][0])
    if not m:raise ValueError('CSV reporting period is missing or ambiguous')
    start,end=(datetime.strptime(x,'%m/%d/%Y').date() for x in m.groups())
    if end<start:raise ValueError('Invalid CSV reporting period')
    return start,end

def number(value,*,blank=False):
    if value=='' and blank:return Decimal(0)
    if not re.fullmatch(r'-?\$?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',value):raise ValueError('Invalid CSV numeric value')
    result=Decimal(value.replace('$','').replace(',',''))
    if not result.is_finite():raise ValueError('Non-finite CSV value')
    return result

def read(text):
    start,end=period(text)
    rows=csv_rows(text)
    if tuple(rows[1])!=HEADERS:raise ValueError('Unsupported realized CSV columns')
    records=[]
    for row in rows[2:]:
        if not row or not any(row):continue
        if len(row)!=len(HEADERS):raise ValueError('Malformed realized CSV row')
        r=dict(zip(HEADERS,row));symbol=r['Symbol']
        option=re.fullmatch(r'([A-Z.]+) ([0-9]{2}/[0-9]{2}/[0-9]{4}) ([0-9]+(?:\.[0-9]+)?) ([CP])',symbol)
        if option:
            underlying,expiry,strike,kind=option.groups()
            instrument=OptionContract(underlying,datetime.strptime(expiry,'%m/%d/%Y').date(),number(strike),OptionType.CALL if kind=='C' else OptionType.PUT)
        elif re.fullmatch(r'[A-Z][A-Z.]*',symbol):instrument=Instrument(symbol)
        else:raise ValueError('Unsupported realized CSV instrument')
        closed=datetime.strptime(r['Closed Date'],'%m/%d/%Y').date()
        opened=datetime.strptime(r['Opened Date'],'%m/%d/%Y').date()
        if opened>closed:raise ValueError('CSV opening date follows closing date')
        validate_broker_report_date(closed,period_start=start,period_end=end)
        if r['Term'] not in ('Short Term','Long Term'):raise ValueError('Unsupported CSV tax term')
        if r['Wash Sale?'] not in ('Yes','No'):raise ValueError('Unsupported CSV wash-sale indicator')
        record=RealizedGainLossRecord(closed,instrument,number(r['Quantity']),number(r['Proceeds Per Share']),number(r['Proceeds']),number(r['Cost Basis (CB)']),number(r['Gain/Loss ($)']),'BROKER_REPORTED','SHORT' if r['Term']=='Short Term' else 'LONG',number(r['Disallowed Loss'],blank=True))
        if abs(record.expected_gain_loss-record.gain_loss)>Decimal('0.01'):raise ValueError('CSV proceeds, basis and gain/loss do not reconcile')
        records.append(record)
    return tuple(records)
