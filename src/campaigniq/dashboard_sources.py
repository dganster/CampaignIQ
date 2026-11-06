"""Pure published report loading; no authentication, runtime construction or UI."""
from datetime import date
import re

from campaigniq.analytics.multi_month_analytics_summary import summarize_multi_month_analytics
from campaigniq.persistence.monthly_publication import publication_cutover_from_storage, finalized_month_marker_key
from campaigniq.persistence.persisted_multi_month_analytics import load_persisted_monthly_campaign_attributions_from_storage


class ReadOnlyReportStorage:
    def __init__(self,storage):self._storage=storage
    def exists(self,key):return self._storage.exists(key)
    def read_text(self,key):return self._storage.read_text(key)
    def list_keys(self,*,prefix='',suffix=''):return self._storage.list_keys(prefix=prefix,suffix=suffix)
    def write_text(self,*args,**kwargs):raise PermissionError('Report loading cannot write financial artifacts')
    def delete(self,*args,**kwargs):raise PermissionError('Report loading cannot delete financial artifacts')
    def initialize(self):raise PermissionError('Report loading cannot initialize storage')


def published_artifact_keys(storage,*,suffix):
    storage=ReadOnlyReportStorage(storage)
    keys=[]
    cutover=publication_cutover_from_storage(storage)
    for key in storage.list_keys(suffix=suffix):
        month_text=key.removesuffix(suffix)
        try:month_start=date.fromisoformat(f'{month_text}-01')
        except ValueError:continue
        next_month=(date(month_start.year+1,1,1) if month_start.month==12
                    else date(month_start.year,month_start.month+1,1))
        period_end=next_month-date.resolution
        if cutover is None or period_end<cutover or storage.exists(finalized_month_marker_key(period_end=period_end)):
            keys.append(key)
    return tuple(keys)


def load_dashboard_sources(storage,*,source_description='selected account'):
    """Return the same persisted periods and analytics used by the main dashboard.

    Caller owns authentication, account selection and snapshot lifetime. This
    function neither creates a runtime nor opens its own database connection.
    """
    income_evidence=getattr(storage,'income',())
    storage=ReadOnlyReportStorage(storage)
    realized_keys=published_artifact_keys(storage,suffix='-realized-attributions.json')
    forex_keys=published_artifact_keys(storage,suffix='-forex-settlement-attributions.json')
    if not realized_keys:raise FileNotFoundError(f'No persisted attribution files found in {source_description}')
    monthly,forex=load_persisted_monthly_campaign_attributions_from_storage(storage,
        realized_keys=realized_keys,forex_keys=forex_keys)
    from campaigniq.persistence.futures_month import load_published
    for key in storage.list_keys(suffix='-futures.json'):
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-futures.json',key):raise ValueError('Unexpected futures artifact key')
    futures={}
    periods_by_month={start.strftime('%Y-%m'):(start,end) for start,end in monthly}
    for key in published_artifact_keys(storage,suffix='-futures.json'):
        if key.removesuffix('-futures.json') not in periods_by_month:
            raise ValueError('Published futures record has no matching ordinary monthly generation')
    for start,end in monthly:
        record=load_published(storage,period_start=start,period_end=end)
        if record is not None:futures[start,end]=record.net_realized_usd
    summaries=summarize_multi_month_analytics(monthly_attributions=monthly,monthly_forex_attributions=forex,monthly_futures_pnl=futures)
    from campaigniq.persistence.approved_forex_month import load_published as approved_forex
    from dataclasses import replace
    from decimal import Decimal
    approved=[]
    for summary in summaries:
        record=approved_forex(storage,period_start=summary.period_start,period_end=summary.period_end)
        approved.append(replace(summary,forex_policy_approved=True,
            forex_financing_pnl=Decimal(record['financials']['financing_usd'])) if record is not None else summary)
    from campaigniq.persistence.no_trading_month import load_income
    summaries=[]
    for summary in approved:
        income=load_income(storage,period_end=summary.period_end)
        summaries.append(replace(summary,no_trading_month=True,statement_cash_income=income) if income is not None else summary)
    from campaigniq.account_income_evidence import IncomeEvidence
    from hashlib import sha256
    evidence={}
    for item in income_evidence:
        if not isinstance(item,IncomeEvidence) or item.month in evidence:raise ValueError('Unique typed statement income per account month required')
        evidence[item.month]=item
    completed=[]
    for summary in summaries:
        item=evidence.get(summary.period_start.strftime('%Y-%m'))
        if item is not None and item.status=='verified':
            key=item.month+'-account-publication-receipt.json'
            if not storage.exists(key):key=item.month+'-import-provenance.json'
            if (item.income is None or (item.income.period_start,item.income.period_end)!=(summary.period_start,summary.period_end) or
                not storage.exists(key) or sha256(storage.read_text(key).encode()).hexdigest()!=item.published_binding):
                raise ValueError('Statement income metadata differs from this published snapshot')
            completed.append(replace(summary,statement_income=item.income,statement_income_status='verified'))
        else:completed.append(replace(summary,statement_income_status='unverified'))
    summaries=tuple(completed)
    return summaries,monthly,forex
