"""Verified daily-session Forex input and exact canonical FIFO generation."""
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from campaigniq import account_forex_financial_policy as policy
from campaigniq import account_forex_cash_review as cash
from campaigniq.forex_daily_financial_candidate import canonical, POLICY
from campaigniq.domain.value_objects.forex_pair import ForexPair
from campaigniq.domain.position_history import PositionHistory
from campaigniq.domain.campaign_continuity import plan_campaign_continuity
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.importers.schwab.forex_transaction_reader import (
    read_forex_transaction_report, SchwabForexSettlement, SchwabForexFinancing)
from campaigniq.importers.thinkorswim.forex_trade_reader import read_forex_trades
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.persistence.lot_book_store import serialize_lot_book

START,END=date(2026,10,1),date(2026,10,31)


def _fx(trade):
    flags=[isinstance(leg.instrument,ForexPair) for leg in trade.legs]
    if any(flags) and not all(flags):raise ValueError('Mixed Forex/securities trade cannot use this policy')
    return all(flags)


def _fx_book(book):
    result=book.clone();result._lots={i:rows for i,rows in result._lots.items() if isinstance(i,ForexPair)}
    return result


def _facts(rows):
    return Counter((r.reference,r.executed_at,r.pair,r.quantity,r.price) for r in rows)


def project_history(original, cash_original):
    """Replace only the Forex ledger in a labelled derivative; retain all originals."""
    lines=original.decode('utf-8').splitlines(keepends=True)
    starts=[i for i,line in enumerate(lines) if line.strip()=='Forex Statements']
    if len(starts)!=1:raise ValueError('One original Forex ledger section required')
    start=starts[0]+1;end=start
    while end<len(lines) and lines[end].strip():end+=1
    with TemporaryDirectory(prefix='campaigniq-forex-source-projection-') as folder:
        root=Path(folder);path=root/'cash.csv';path.write_bytes(cash_original)
        section=ThinkorswimSourceReader().read(str(path)).section('Forex Statements')
        header=section.header();selected=[]
        for row in section.rows():
            if len(row)!=len(header) or row[3].strip()!='TRD':continue
            at=datetime.strptime(row[1]+' '+row[2],'%m/%d/%y %H:%M:%S')
            if START<=at.date()<=END:selected.append(row)
        if not selected:raise ValueError('Approved Forex source has no selected-month executions')
        output=io.StringIO();csv.writer(output,lineterminator='\n').writerows([header,*selected])
        derived=(''.join(lines[:start])+output.getvalue()+''.join(lines[end:])).encode()
    return derived,dict(format='campaigniq.approved_forex_history_projection',version=1,
        base_history_sha256=sha256(original).hexdigest(),cash_original_sha256=sha256(cash_original).hexdigest(),
        derived_history_sha256=sha256(derived).hexdigest(),selected_references=[r[4] for r in selected],
        original_preserved=True,other_sections_preserved=True)


@dataclass(frozen=True)
class ApprovedForex:
    approved_json:str
    cash_original:bytes
    report_original:bytes
    derived_sha256:str
    lineage_json:str
    canonical_opening_json:str

    @property
    def approved(self):return json.loads(self.approved_json)
    @property
    def calculation(self):return self.approved['candidate']['artifact']['calculation']

    def apply(self,result,canonical_opening,history):
        if serialize_lot_book(period_end=START-date.resolution,lot_book=canonical_opening)!=self.canonical_opening_json:
            raise ValueError('Monthly Forex opening differs from the approved published predecessor')
        if sha256(Path(history).read_bytes()).hexdigest()!=self.derived_sha256:
            raise ValueError('Approved Forex derivative differs from reviewed execution input')
        approved=self.approved;calculation=self.calculation
        if (calculation['period_start'],calculation['period_end'])!=(str(START),str(END)):
            raise ValueError('Approved Forex profile is limited to the exact October month')
        raw=read_forex_transaction_report_bytes(self.report_original)
        rows=read_forex_trades(ThinkorswimSourceReader().read(str(history)).section('Forex Statements'))
        rows=[r for r in rows if START<=r.executed_at.date()<=END]
        with TemporaryDirectory(prefix='campaigniq-approved-forex-facts-') as folder:
            p=Path(folder)/'cash.csv';p.write_bytes(self.cash_original)
            expected=[r for r in read_forex_trades(ThinkorswimSourceReader().read(str(p)).section('Forex Statements'))
                if START<=r.executed_at.date()<=END]
        if _facts(rows)!=_facts(expected):raise ValueError('Approved Forex execution facts differ')
        fx_opening=_fx_book(canonical_opening)
        initial=serialize_lot_book(period_end=START-date.resolution,lot_book=fx_opening)
        fx_history=PositionHistory()
        for item in result.position_history.items():
            if item.trade is not None and _fx(item.trade):fx_history.add_trade(item.trade)
            if item.event is not None and any(isinstance(c.instrument,ForexPair) for c in item.event.changes):
                raise ValueError('Forex position events require a separate approved profile')
        fx_campaigns=tuple(c for c in result.campaigns if any(_fx(t) for t in c.trades))
        if any(not all(_fx(t) for t in c.trades) for c in fx_campaigns):raise ValueError('Mixed Forex campaign')
        plan=plan_campaign_continuity(opening_lot_book=fx_opening,position_history=fx_history,campaigns=fx_campaigns)
        if plan.blocking_findings:raise ValueError('Approved Forex FIFO identity blocked: '+'; '.join(plan.blocking_findings))
        campaigns=tuple(c for c in result.campaigns if c not in fx_campaigns)+plan.campaigns
        if len({c.campaign_id for c in campaigns})!=len(campaigns):raise ValueError('Forex campaign identity collides with securities')
        opening=result.opening_lot_book.clone()
        opening._lots={i:lots for i,lots in opening._lots.items() if not isinstance(i,ForexPair)}
        opening._lots.update({i:list(lots) for i,lots in fx_opening._lots.items()})
        opening._next_id=max(opening._next_id,fx_opening._next_id)
        ending,transitions=LotBookPeriodApplier().apply_with_lifecycle(opening_lot_book=opening,
            position_history=result.position_history,campaigns=campaigns)
        if any(ending.lots(i) for i in ending.instruments() if isinstance(i,ForexPair)):
            raise ValueError('Approved daily evidence and generated Forex ending lots differ')
        if serialize_lot_book(period_end=START-date.resolution,lot_book=fx_opening)!=initial:
            raise ValueError('Approved Forex replay mutated the canonical opening')
        attrs=[];close_lots=[]
        replay=opening.clone();campaign_ids={id(t):c.campaign_id for c in campaigns for t in c.trades}
        closes={r['reference']:r for r in calculation['closes']}
        matched=set()
        for item in result.position_history.items():
            if item.trade is None:
                LotBookPeriodApplier()._apply_event(replay,item.event);continue
            trade=item.trade;allocations=replay.apply_trade(trade,campaign_id=campaign_ids.get(id(trade)))
            if not _fx(trade):continue
            leg=trade.legs[0]
            if len(trade.legs)!=1 or len(leg.executions)!=1:raise ValueError('Exact single Forex execution required')
            execution=leg.executions[0]
            matches=[r for r in rows if (r.pair,r.executed_at,abs(r.quantity),r.price)==(
                leg.instrument.symbol,execution.executed_at,abs(execution.quantity),execution.execution_price)]
            if len(matches)!=1:raise ValueError('Forex trade lacks one exact saved reference')
            row=matches[0]
            if leg.position_effect.value!='CLOSE':continue
            source=closes.get(row.reference)
            if source is None or row.reference in matched:raise ValueError('Generated close differs from approved reference set')
            matched.add(row.reference)
            ids={a.campaign_id for a in allocations}
            if len(ids)!=1 or None in ids or sum((abs(a.quantity) for a in allocations),Decimal(0))!=abs(row.quantity):
                raise ValueError('Approved close requires one exact FIFO campaign and complete lot allocation')
            if campaign_ids[id(trade)]!=next(iter(ids)):raise ValueError('Closing campaign differs from consumed FIFO ancestry')
            settlement=SchwabForexSettlement(row.reference,
                datetime.fromisoformat(source['csv_trade_at'] or source['daily_observed_at']),
                datetime.fromisoformat(source['csv_settlement_at']) if source['csv_settlement_at'] is not None else None,
                row.pair,'Buy' if row.quantity>0 else 'Sell',row.price,row.quantity,
                Decimal(source['candidate_pl_usd']),sum((v.quantity for v in replay.lots(leg.instrument)),Decimal(0)),Decimal(0),
                'owner-approved-daily:'+approved['approval_receipt_sha256'])
            attrs.append(ForexSettlementAttribution(settlement,next(iter(ids))))
            close_lots.extend(dict(reference=row.reference,lot_id=a.lot_id,campaign_id=a.campaign_id,quantity=str(a.quantity)) for a in allocations)
        if matched!=set(closes):raise ValueError('Approved closes were omitted from generation')
        report=replace(raw,settlements=tuple(a.settlement for a in attrs),
            financing=tuple(SchwabForexFinancing(r['reference'],datetime.fromisoformat(r['daily_observed_at']),
                r['pair'],Decimal(r['amount_usd'])) for r in calculation['financing']),
            mtd_settled_pl_usd=Decimal(calculation['totals']['trading_usd']),
            pl_total_usd=Decimal(calculation['totals']['trading_usd']),commission_total_usd=Decimal(0),
            financing_totals_usd=tuple((pair,sum((Decimal(r['amount_usd']) for r in calculation['financing'] if r['pair']==pair),Decimal(0)))
                for pair in sorted({r['pair'] for r in calculation['financing']})),source_policy=POLICY)
        if not report.settlement_control_reconciled or not report.pl_total_control_reconciled:
            raise ValueError('Approved generated Forex controls differ')
        audit=dict(format='campaigniq.forex_source_resolution',version=3,policy=POLICY,
            period_end=str(END),financial_policy_approved=True,original_discrepancies_resolved=False,
            approval_receipt_sha256=approved['approval_receipt_sha256'],
            original_candidate=approved['candidate']['artifact'],actual_close_allocations=close_lots,
            source_lineage=json.loads(self.lineage_json),fifo_identity_links=[
                dict(occurred_at=v.occurred_at,source_campaign_id=v.source_campaign_id,
                    target_campaign_id=v.target_campaign_id,supported_by_fifo=v.supported_by_fifo) for v in plan.trade_bindings])
        return replace(result,opening_lot_book=opening,ending_lot_book=ending,campaigns=campaigns,
            lifecycle_transitions=transitions,forex_transaction_report=report,forex_settlement_attributions=tuple(attrs)),audit


def read_forex_transaction_report_bytes(raw):
    with TemporaryDirectory(prefix='campaigniq-original-forex-report-') as folder:
        path=Path(folder)/'report.csv';path.write_bytes(raw)
        return read_forex_transaction_report(path)


def prepare(service,session,draft,base_history,destination):
    approved=policy.saved_preview(service,session,draft)
    raw=session.drafts.document(draft.id,cash.CASH)[2]
    derived,lineage=project_history(base_history,raw)
    # Both the original and any separately labelled futures derivative are bound.
    lineage['original_history_sha256']=sha256(session.drafts.document(draft.id,cash.HISTORY)[2]).hexdigest()
    destination.write_bytes(derived)
    from campaigniq.account_forex_inventory_preview import preceding_state
    book,_=preceding_state(session.artifacts,start=START)
    if book is None:raise ValueError('Canonical Forex predecessor required')
    return ApprovedForex(canonical(approved),raw,session.drafts.document(draft.id,cash.REPORT)[2],
        sha256(derived).hexdigest(),canonical(lineage),serialize_lot_book(period_end=START-date.resolution,lot_book=book))
