"""Read saved financial results from the exact retained legacy workspace."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
import re

from campaigniq.account_signedin_results import _instrument, _decimal
from campaigniq.persistence.account_directory_codec import checksum
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.persistence.realized_attribution_store import load_realized_attributions_from_storage
from campaigniq.persistence.forex_settlement_attribution_store import load_forex_settlement_attributions_from_storage


@dataclass(frozen=True)
class LegacyPublishedResults:
    account_id: str
    month: str
    positions: tuple[tuple[str,str],...]
    lots: tuple[tuple[str,str,str,str,str],...]
    realized: tuple[tuple[str,str,str,str,str,str],...]
    brokerage_realized_usd: str
    forex_settled_usd: str | None
    forex_records: int | None
    crypto_positions: tuple[tuple[str,str],...]
    crypto_status: str
    warnings: tuple[str,...]
    source_artifacts: int
    source_sha256: str


def _visible(storage,end):
    if not is_month_published_in_storage(storage,period_end=end):return False
    key=f'{end:%Y-%m}-finalized.json'
    if storage.exists(key):
        marker=json.loads(storage.read_text(key))
        if marker!=dict(format='campaigniq.finalized_month',version=1,period_end=end.isoformat()):
            raise ValueError('Retained publication marker differs from its month')
    return True


def published_months(storage):
    months=[]
    for key in storage.list_keys(suffix='-lot-book.json'):
        match=re.fullmatch(r'([0-9]{4})-([0-9]{2})-lot-book.json',key)
        if match is None:raise ValueError('Unsupported retained lot-state key')
        year,month=map(int,match.groups());end=date(year,month,monthrange(year,month)[1])
        if _visible(storage,end):months.append(f'{year:04d}-{month:02d}')
    return tuple(sorted(months))


def _results(storage,account_id,year,month,source_rows):
    start=date(year,month,1);end=date(year,month,monthrange(year,month)[1]);prefix=f'{year:04d}-{month:02d}-'
    if prefix+'lot-book.json' not in dict(source_rows) or not _visible(storage,end):
        raise ValueError('No published retained lot state for the selected month')
    state=load_lot_book_from_storage(storage,prefix+'lot-book.json')
    realized=load_realized_attributions_from_storage(storage,prefix+'realized-attributions.json')
    if state.period_end!=end or (realized.period_start,realized.period_end)!=(start,end):
        raise ValueError('Retained financial artifact period differs from selected month')
    quantities={};lots=[]
    for group in state.lot_book._lots.values():
        for lot in group:
            name=_instrument(lot.instrument)
            quantities[name]=quantities.get(name,Decimal(0))+_decimal(lot.quantity)
            if lot.basis_total is not None:_decimal(lot.basis_total)
            lots.append((name,str(lot.quantity),str(lot.basis_total) if lot.basis_total is not None else 'Unknown',
                lot.basis_source or 'Unknown',lot.campaign_id or 'Unassigned'))
    rows=[];total=Decimal(0)
    for item in realized.attributions:
        row=item.record
        if not start<=row.closed_date<=end:raise ValueError('Retained realized record falls outside selected month')
        for value in (row.quantity,row.proceeds,row.cost_basis,row.gain_loss):_decimal(value)
        rows.append((row.closed_date.isoformat(),_instrument(row.instrument),str(row.quantity),
            str(row.proceeds),str(row.cost_basis),str(row.gain_loss)))
        total+=row.gain_loss
    warnings=[];fx_total=None;fx_count=None
    if storage.exists(prefix+'forex-settlement-attributions.json'):
        forex=load_forex_settlement_attributions_from_storage(storage,prefix+'forex-settlement-attributions.json')
        if (forex.period_start,forex.period_end)!=(start,end):raise ValueError('Retained Forex period differs')
        fx_total=str(sum((_decimal(a.settlement.settlement_pl_usd) for a in forex.attributions),Decimal(0)))
        fx_count=len(forex.attributions)
    else:
        warnings.append('No retained Forex settlement artifact for this month; coverage is unknown.')
    crypto_positions=();crypto_status='No retained crypto artifact'
    if storage.exists(prefix+'crypto.json'):
        crypto=json.loads(storage.read_text(prefix+'crypto.json'))
        if (crypto.get('format')!='campaigniq.crypto_month' or crypto.get('version')!=1
            or crypto.get('period_end')!=end.isoformat()):raise ValueError('Retained crypto format or period differs')
        crypto_positions=tuple(sorted((pair,str(_decimal(quantity))) for pair,quantity in crypto['ending_quantities'].items()))
        crypto_status=str(crypto['status'])
        if not isinstance(crypto.get('warnings'),list) or any(not isinstance(w,str) for w in crypto['warnings']):
            raise ValueError('Retained crypto warnings are invalid')
        warnings.extend(crypto['warnings'])
    return LegacyPublishedResults(account_id,f'{year:04d}-{month:02d}',
        tuple(sorted((name,str(quantity)) for name,quantity in quantities.items())),tuple(sorted(lots)),
        tuple(rows),str(total),fx_total,fx_count,crypto_positions,crypto_status,tuple(warnings),
        len(source_rows),checksum(source_rows))


def load_existing_results(service,user,account_id,year,month):
    from campaigniq.account_legacy_access import LocalLegacyAccountAccess
    if type(service) is not LocalLegacyAccountAccess:
        raise ValueError('Guarded retained-workspace reader required')
    if type(year) is not int or type(month) is not int or not 1900<=year<=2200:
        raise ValueError('Supported integer reporting month required')
    date(year,month,1)
    with service._session(user,account_id) as (conn,session,_):
        prefix=f'{year:04d}-{month:02d}-'
        direct=conn.execute('''SELECT artifact_key,content FROM public.artifact_storage_dev
            WHERE namespace=%s AND starts_with(artifact_key,%s) ORDER BY artifact_key''',
            (session.account.storage_scope,prefix)).fetchall()
        adapter=[(key,session.artifacts.read_text(key)) for key in session.artifacts.list_keys(prefix=prefix)]
        if direct!=adapter:raise ValueError('Retained financial source adapter parity differs')
        return _results(session.artifacts,account_id,year,month,direct)
