"""One read-only snapshot for explicitly selected published LOCAL accounts."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from campaigniq.account_reconciliation_preview import ReadOnlyArtifacts
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.account_signedin_results import PublishedResults, _results_session
from campaigniq.persistence.account_data_session import AccountDataSession
from campaigniq.persistence.signedin_account_data import _new_schwab_account


@dataclass(frozen=True)
class PublishedSelection:
    account_id: str
    draft_id: UUID
    revision: int

    def __post_init__(self):
        if (type(self.account_id) is not str or str(UUID(self.account_id))!=self.account_id
            or type(self.draft_id) is not UUID or type(self.revision) is not int or self.revision<0):
            raise ValueError('Canonical account, saved draft and displayed revision required')


@dataclass(frozen=True)
class PublishedComparison:
    month: str
    accounts: tuple[PublishedResults,...]
    combined_positions: tuple[tuple[str,str],...]
    includes_combined_positions: bool


def compare_published(service,user,year,month,selections,*,include_positions=False):
    if type(service) is not SignedInMonthlyInputs:
        raise ValueError('Signed-in monthly input service required')
    if type(year) is not int or type(month) is not int or not 1900<=year<=2200:
        raise ValueError('Supported integer reporting month required')
    date(year,month,1)
    if (type(selections) is not tuple or not 1<=len(selections)<=20
        or any(type(item) is not PublishedSelection for item in selections)
        or type(include_positions) is not bool):
        raise ValueError('Select one to twenty published accounts and an explicit combined-positions choice')
    ids=tuple(item.account_id for item in selections)
    if len(set(ids))!=len(ids):raise ValueError('Duplicate account selection')
    gateway=service.gateway
    # One outer directory lock prevents permission/binding changes for the
    # entire comparison. All source and artifact reads use ONE SQL snapshot.
    with gateway.transaction(user,ids[0]):
        sessions=[]
        with gateway._directory._connection(readonly=True) as conn:
            directory,actor=gateway._current(conn,user)
            authorized=directory.select(actor.identity,ids)
            for account_id in ids:_new_schwab_account(directory,account_id)
            if not gateway._ready(conn):raise ValueError('Dedicated local input storage is missing')
            conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
            try:
                drafts=[]
                for access,item in zip(authorized,selections,strict=True):
                    session=AccountDataSession(conn,access,False,gateway._directory.registry_id)
                    sessions.append(session)
                    session._physical_scope='signedin-'+UUID(gateway._directory.registry_id).hex+'-'+UUID(item.account_id).hex
                    session.artifacts=ReadOnlyArtifacts(session.artifacts)
                    draft=session.drafts.get(item.draft_id)
                    if (draft.year,draft.month)!=(year,month) or draft.revision!=item.revision:
                        raise ValueError('Selected month or source revision changed; reload comparison sources')
                    drafts.append(draft)
                results=tuple(_results_session(service,session,draft,item.account_id,item.revision)
                    for session,draft,item in zip(sessions,drafts,selections,strict=True))
                totals={}
                if include_positions:
                    for result in results:
                        for instrument,quantity in result.positions:
                            totals[instrument]=totals.get(instrument,Decimal(0))+Decimal(quantity)
                for session in sessions:session._check()
                return PublishedComparison(f'{year:04d}-{month:02d}',results,
                    tuple((key,str(value)) for key,value in sorted(totals.items())),include_positions)
            finally:
                for session in sessions:session._active=False
