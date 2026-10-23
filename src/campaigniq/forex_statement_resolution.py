"""Resolve August/September 2025 Forex financial records from broker evidence.

The monthly statement defines the reporting scope and cash controls. History
supplies per-close USD P/L, corroborated by monthly pair totals. CSV differences
are retained in an audit, never edited away. Missing settlement dates stay null.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import re
import csv
import io

from campaigniq.forex_opening_statement import read_forex_opening_statement, _TRADE, _amount
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.importers.schwab.forex_transaction_reader import (
    read_forex_transaction_report, SchwabForexSettlement, SchwabForexFinancing,
)

POLICY='schwab_monthly_history_2025_v1'
MONEY=r'\(?[+-]?\$[\d,]+(?:\.\d+)?\)?'


def summary(text,label):
    values=re.findall(rf'^\s*{re.escape(label)}\s+({MONEY})(?:\s{{2,}}.*)?\s*$',text,re.M)
    if len(values)!=1:raise ValueError('Missing/ambiguous monthly monetary control: '+label)
    return _amount(values[0].replace('$',''))


def resolve_forex_statement(text,*,trade_history,transaction_report,period_end):
    if period_end not in (date(2025,8,31),date(2025,9,30)):
        raise ValueError('Financial source resolution is limited to August/September 2025.')
    paths=(Path(trade_history),Path(transaction_report));originals=tuple(p.read_bytes() for p in paths)
    for fields in csv.reader(io.StringIO(originals[1].decode('utf-8-sig'))):
        if len(fields)>14 and fields[3].strip().lower()=='financing':
            amounts=[_amount(v.strip().replace(' USD','').replace('$','')) for v in (fields[13],fields[14]) if v.strip()]
            if len(amounts)!=2 or amounts[0]!=amounts[1]:
                raise ValueError('CSV financing amount and delivery amount disagree.')
    proof=read_forex_opening_statement(text,trade_history=paths[0],transaction_report=paths[1],period_end=period_end)
    raw=read_forex_transaction_report(paths[1])
    if raw.period_start != period_end.replace(day=1)-timedelta(days=1) or raw.period_end != period_end:
        raise ValueError('Resolution requires the exact standard monthly CSV period.')
    statement=ThinkorswimSourceReader().read(str(paths[0]))
    rows=read_forex_trades(statement.section('Forex Statements'))
    details={};fin={};inside=False
    for line in text.splitlines():
        s=line.strip()
        if s=='Financing Report':inside=True;continue
        if s.startswith(('Conversion Report','Customer Account Summary Report')):inside=False
        m=_TRADE.fullmatch(line)
        if m:
            if m[7] in details:raise ValueError('Duplicate monthly execution.')
            details[m[7]]=(m,datetime.fromisoformat(' '.join(s.split()[:2])))
        if inside and re.match(r'^\d{4}-',s):
            parts=s.split()
            if len(parts)!=8 or not re.fullmatch(r'[A-Z]{3}/[A-Z]{3}',parts[2]):raise ValueError('Unsupported financing detail.')
            when=datetime.fromisoformat(' '.join(parts[:2]));identifier=parts[5]
            if not identifier.isdigit() or identifier in fin:raise ValueError('Duplicate/invalid monthly financing reference.')
            if not period_end.replace(day=1)-timedelta(days=3)<=when.date()<=period_end:raise ValueError('Financing outside monthly statement scope.')
            fin[identifier]=SchwabForexFinancing(identifier,when,'USD$FX',_amount(parts[7].replace('$', '')))
    first=text.split('\f')[0]
    pair_section=first.split('Monthly Profit/Loss Summary')[1].split('Open Position(s) at End of Month')[0]
    pair_totals={}
    for line in pair_section.splitlines():
        if re.match(r'^\s*[A-Z]{3}/[A-Z]{3}',line):
            m=re.fullmatch(rf'\s*([A-Z]{{3}}/[A-Z]{{3}})\s+({MONEY})(?:\s+{MONEY})?\s*',line)
            if m is None or m[1] in pair_totals:raise ValueError('Invalid monthly pair summary.')
            pair_totals[m[1]]=_amount(m[2].replace('$',''))
    raw_events=(*raw.new_transactions,*raw.settlements)
    byid={x.order_id:x for x in raw_events}
    if len(byid)!=len(raw_events) or set(byid)-set(details):raise ValueError('Unknown/duplicate CSV execution references.')
    positions=dict(proof.opening);settlements=[];news=[];totals=defaultdict(Decimal);changes=[]
    source='monthly:'+proof.source_sha256+';history:'+proof.history_sha256+';csv:'+proof.report_sha256
    for row in sorted(rows,key=lambda x:x.executed_at):
        m,when=details[row.reference]
        old=byid.get(row.reference)
        if old is None and when != row.executed_at+timedelta(hours=2):raise ValueError('Missing close lacks verified monthly/history clock alignment.')
        if old is not None and (old.instrument!=row.pair or old.amount!=row.quantity or old.rate!=row.price or old.side not in (('B','Buy') if row.quantity>0 else ('S','Sell'))):
            raise ValueError('CSV execution facts differ; quantities/rates cannot be repaired.')
        positions[row.pair]=positions.get(row.pair,Decimal(0))+row.quantity
        if old is not None and old.total_position!=positions[row.pair]:raise ValueError('Explicit CSV Total Position differs from exact continuous replay.')
        if m[4]=='OPEN':
            if old not in raw.new_transactions:raise ValueError('Monthly opening execution lacks its original CSV financial record.')
            if row.broker_pnl_usd not in (None,Decimal(0)):raise ValueError('Opening execution has unexplained P/L.')
            news.append(old)
        else:
            if row.broker_pnl_usd is None or not row.broker_pnl_usd.is_finite():raise ValueError('Close lacks explicit history USD P/L.')
            totals[row.pair]+=row.broker_pnl_usd
            if old is not None and old not in raw.settlements:raise ValueError('CSV execution effect conflicts with monthly close.')
            if old is None and (raw.mtd_fee_usd != 0 or raw.commission_total_usd != 0):
                raise ValueError('Missing close fees cannot be established from the source controls.')
            item=SchwabForexSettlement(row.reference,old.trade_at if old else when,old.settlement_at if old else None,row.pair,'Buy' if row.quantity>0 else 'Sell',row.price,row.quantity,row.broker_pnl_usd,positions[row.pair],old.fee_usd if old else Decimal(0),source)
            settlements.append(item)
            if old is None or old.settlement_pl_usd!=item.settlement_pl_usd:
                changes.append({'reference':row.reference,'csv_usd':None if old is None else str(old.settlement_pl_usd),'resolved_usd':str(item.settlement_pl_usd),'settlement_date_reported':item.settlement_at is not None})
    reported_pl=summary(first,'Settled Profit/Loss');reported_fin=summary(first,'Financing')
    checks={'pair_sum':reported_pl-sum(pair_totals.values(),Decimal(0)),
        'history_sum':reported_pl-sum(totals.values(),Decimal(0)),
        'financing':reported_fin-sum((f.financing_usd for f in fin.values()),Decimal(0)),
        'cash_bridge':summary(first,'Ending Cash Balance')-(summary(first,'Beginning Cash Balance')+summary(first,'Deposits')-summary(first,'Withdrawals')+summary(first,'Adjustments')+reported_fin+reported_pl)}
    if set(totals)!=set(pair_totals):raise ValueError('Monthly/history realized pair sets differ.')
    checks.update({'pair:'+pair:pair_totals[pair]-totals[pair] for pair in pair_totals})
    if any(abs(v)>Decimal('.01') for v in checks.values()):raise ValueError('Monthly financial controls exceed the approved one-cent aggregate tolerance.')
    original_fin={f.order_id:f for f in raw.financing}
    if len(original_fin)!=len(raw.financing):raise ValueError('Duplicate CSV financing reference.')
    if any(identifier in original_fin and original_fin[identifier].financing_usd!=f.financing_usd for identifier,f in fin.items()):raise ValueError('Individual financing amounts differ between broker sources.')
    audit={'format':'campaigniq.forex_source_resolution','version':1,'policy':POLICY,'period_end':period_end.isoformat(),
        'source_sha256':{'monthly_text':proof.source_sha256,'history':proof.history_sha256,'original_csv':proof.report_sha256},
        'settlement_changes':changes,'monthly_financing_ids':sorted(fin),
        'monthly_financing_records':[{'reference':f.order_id,'observed_at':f.occurred_at.isoformat(),'amount_usd':str(f.financing_usd)} for f in fin.values()],
        'csv_financing_outside_monthly_scope':[{'reference':f.order_id,'instrument':f.instrument,'amount_usd':str(f.financing_usd)} for f in raw.financing if f.order_id not in fin],
        'monthly_financing_absent_from_csv':sorted(set(fin)-set(original_fin)),
        'original_csv_controls':{'mtd_pl_usd':str(raw.mtd_settled_pl_usd),'pl_total_usd':str(raw.pl_total_usd),'settlement_row_total_usd':str(raw.settlement_pl_usd),'mtd_fee_usd':str(raw.mtd_fee_usd),'commission_total_usd':str(raw.commission_total_usd)},
        'resolved_pl_usd':str(sum(totals.values(),Decimal(0))),'reported_monthly_pl_usd':str(reported_pl),'financing_usd':str(reported_fin),
        'ending_cash_usd':str(summary(first,'Ending Cash Balance')),'aggregate_differences_usd':{k:str(v) for k,v in checks.items()},
        'settlement_dates_not_invented':True,'original_sources_unchanged':True}
    resolved=replace(raw,settlements=tuple(settlements),new_transactions=tuple(news),financing=tuple(fin.values()),
        mtd_settled_pl_usd=reported_pl,pl_total_usd=reported_pl,
        financing_totals_usd=(('USD$FX',reported_fin),),source_policy=POLICY)
    if not resolved.commission_control_reconciled and resolved.commission_control_reconciled is not None:raise ValueError('Commission control failed.')
    if originals!=tuple(p.read_bytes() for p in paths):raise ValueError('Source changed during resolution.')
    return resolved,audit
