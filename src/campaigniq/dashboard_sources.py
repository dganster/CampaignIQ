"""Pure published report loading; no authentication, runtime construction or UI."""
from datetime import date

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
    storage=ReadOnlyReportStorage(storage)
    realized_keys=published_artifact_keys(storage,suffix='-realized-attributions.json')
    forex_keys=published_artifact_keys(storage,suffix='-forex-settlement-attributions.json')
    if not realized_keys:raise FileNotFoundError(f'No persisted attribution files found in {source_description}')
    monthly,forex=load_persisted_monthly_campaign_attributions_from_storage(storage,
        realized_keys=realized_keys,forex_keys=forex_keys)
    summaries=summarize_multi_month_analytics(monthly_attributions=monthly,monthly_forex_attributions=forex)
    return summaries,monthly,forex
