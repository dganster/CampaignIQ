"""Collect complete monthly artifact candidates in memory, never finalize imports."""
import json
from campaigniq import monthly_import_execution as execution
from campaigniq.persistence.monthly_publication import _serialize_finalized_month_marker


def validate_generation(artifacts):
    """Require all twelve complete periods and exercise the retained-data readers."""
    import calendar
    from datetime import date
    from campaigniq.persistence.lot_book_store import deserialize_lot_book
    from campaigniq.persistence.position_journal import deserialize_position_journal
    from campaigniq.persistence.realized_attribution_store import deserialize_realized_attributions
    from campaigniq.persistence.forex_settlement_attribution_store import deserialize_forex_settlement_attributions
    from campaigniq.persistence.lifecycle_transition_store import deserialize_lifecycle_transitions
    from campaigniq.persistence.boundary_completeness_store import deserialize_boundary_completeness
    readers={'lot-book':deserialize_lot_book,'realized-attributions':deserialize_realized_attributions,
             'forex-settlement-attributions':deserialize_forex_settlement_attributions,
             'lifecycle-transitions':deserialize_lifecycle_transitions,'boundary-completeness':deserialize_boundary_completeness}
    formats={'lot-book':'campaigniq.lot_book','realized-attributions':'campaigniq.realized_attributions',
        'forex-settlement-attributions':'campaigniq.forex_settlement_attributions','lifecycle-transitions':'campaigniq.lifecycle_transitions',
        'position-journal':'campaigniq.position_journal','import-provenance':'campaigniq.monthly_import_provenance',
        'boundary-completeness':'campaigniq.boundary_completeness','market-applicability':'campaigniq.market_applicability',
        'finalized':'campaigniq.finalized_month','crypto':'campaigniq.crypto_month','forex-source-resolution':'campaigniq.forex_source_resolution'}
    required=set(formats)-{'crypto','forex-source-resolution'}
    seen=set()
    for month in range(1,13):
        key=f'2025-{month:02}';start=date(2025,month,1);end=date(2025,month,calendar.monthrange(2025,month)[1])
        found=set()
        for name,text in artifacts.items():
            if not name.startswith(key+'-'):continue
            suffix=name[len(key)+1:-5] if name.endswith('.json') else ''
            if suffix not in formats:raise ValueError('Unexpected candidate artifact family.')
            obj=json.loads(text,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Non-finite JSON number.')))
            if obj.get('format')!=formats[suffix] or obj.get('period_end')!=end.isoformat():
                raise ValueError('Candidate format or period differs from its key.')
            if 'period_start' in obj and obj['period_start']!=start.isoformat():
                raise ValueError('Candidate start period differs.')
            if suffix=='position-journal':deserialize_position_journal(text,period_start=start,period_end=end)
            elif suffix in readers:
                if readers[suffix](text).period_end!=end:raise ValueError('Candidate reader period differs.')
            seen.add(name);found.add(suffix)
        if not required<=found:raise ValueError('A monthly candidate generation is incomplete.')
    if seen!=set(artifacts):raise ValueError('Candidate generation contains out-of-scope artifacts.')
    return True


class CandidateCollector:
    def __init__(self, baseline):
        self.baseline = baseline
        self.artifacts = {}
        self.periods = []
        self.contract_mapping_candidates = {}

    def exists(self,key):
        return key in self.artifacts or key in self.baseline

    def read_text(self,key):
        return self.artifacts[key] if key in self.artifacts else self.baseline[key]

    def list_keys(self,*,prefix='',suffix=''):
        return tuple(sorted(k for k in self.baseline.keys() | self.artifacts.keys() if k.startswith(prefix) and k.endswith(suffix)))

    def collect(self,preflight,preview,supplied):
        if preview.finalized or not preview.closing_reconciliation.reconciled or preview.closing_reconciliation.forex_source_findings:
            raise ValueError('Only reconciled, unfinalized previews may supply candidates.')
        for name in ('settlement','pl_total','commission','financing'):
            if getattr(preview,'forex_'+name+'_control_reconciled') is False:
                raise ValueError('Financial controls block candidate preparation.')
        contract=preflight.contract;start=contract.period_start;end=contract.period_end;month=f'{end:%Y-%m}'
        if end.year!=2025 or month in self.periods:
            raise ValueError('Candidates require unique 2025 periods.')
        result=preview.result
        from dataclasses import replace
        from campaigniq.domain.option_contract import OptionContract
        from campaigniq.historical_backfill_realized_replay import ChronologicalRealizedLotAttributor
        represented={leg.instrument for campaign in result.campaigns for trade in campaign.trades for leg in trade.legs}
        aliases={}
        for record in result.realized_gain_loss:
            source=record.instrument
            if isinstance(source,OptionContract) and source.underlying=='SPXW':
                target=replace(source,underlying='SPX')
                if target in represented and source not in represented:
                    aliases[source]=target
        self.contract_mapping_candidates[month]=[dict(source=repr(a),target=repr(b),approved=False) for a,b in aliases.items()]
        realized=ChronologicalRealizedLotAttributor(result.opening_lot_book.clone(),contract_aliases=aliases).attribute_campaigns(
            list(result.campaigns),list(result.realized_gain_loss),list(result.attribution_events))
        if len(realized)!=len(result.realized_gain_loss) or any(a.record!=record for a,record in zip(realized,result.realized_gain_loss)):
            raise ValueError('Original broker realized records were changed or omitted.')
        from decimal import Decimal
        for attribution in realized:
            if sum((a.quantity for a in attribution.allocations),Decimal(0))!=attribution.record.quantity:
                raise ValueError('Realized allocation quantities differ from the broker record.')
            if sum((a.broker_basis for a in attribution.allocations),Decimal(0))!=attribution.record.cost_basis:
                raise ValueError('Realized allocations do not preserve total broker basis.')
        payloads={
            'realized-attributions':execution.serialize_realized_attributions(period_start=start,period_end=end,attributions=realized),
            'forex-settlement-attributions':execution.serialize_forex_settlement_attributions(period_start=start,period_end=end,attributions=result.forex_settlement_attributions),
            'lifecycle-transitions':execution.serialize_lifecycle_transitions(period_start=start,period_end=end,transitions=result.lifecycle_transitions),
            'lot-book':execution.serialize_lot_book(period_end=end,lot_book=result.ending_lot_book),
            'position-journal':execution.serialize_position_journal(result,period_start=start,period_end=end),
            'import-provenance':execution.serialize_monthly_import_provenance(period_start=start,period_end=end,inputs=execution.capture_monthly_input_provenance(supplied)),
            'boundary-completeness':execution.serialize_boundary_completeness(period_start=start,period_end=end,reconstruction=result.boundary_reconstruction),
            'market-applicability':json.dumps(dict(format='campaigniq.market_applicability',version=1,period_start=start.isoformat(),period_end=end.isoformat(),forex_applicable=preflight.forex_applicable,crypto_applicable=preflight.crypto_applicable),sort_keys=True),
            'finalized':_serialize_finalized_month_marker(period_end=end),
        }
        if preview.forex_resolution is not None:
            payloads['forex-source-resolution']=json.dumps(preview.forex_resolution,indent=2,sort_keys=True)+'\n'
        crypto=(execution.build_crypto_month(result.crypto_report,execution.load_preceding_crypto(self,start))
                if preflight.crypto_applicable and result.crypto_report is not None else None)
        path=supplied.get(execution.MonthlyInputRole.SCHWAB_CRYPTO_STATEMENT)
        if path is not None:
            from campaigniq.importers.schwab.crypto_statement_reader import read_crypto_statement,attach_crypto_statement
            crypto=attach_crypto_statement(crypto,read_crypto_statement(path,period_start=start,period_end=end))
        if crypto is not None:
            payloads['crypto']=execution.serialize_crypto_month(crypto)
        candidates={month+'-'+suffix+'.json':text for suffix,text in payloads.items()}
        if any(key in self.baseline or key in self.artifacts for key in candidates):
            raise ValueError('Refusing to replace any existing candidate or captured artifact.')
        self.artifacts.update(candidates);self.periods.append(month)
