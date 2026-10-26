"""Consistent read-only, account-separated month comparison for local synthetic users."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.account_reconciliation_preview import AccountReconciliationPreview,_reconcile_session
from campaigniq.persistence.account_data_session import AccountDataSession,LocalAccountDataGateway


@dataclass(frozen=True)
class SelectedDraft:
    account_id:str
    draft_id:UUID
    revision:int
    forex_applicable:bool=True
    crypto_applicable:bool=True

    def __post_init__(self):
        if (not isinstance(self.account_id,str) or str(UUID(self.account_id))!=self.account_id
                or type(self.draft_id) is not UUID or type(self.revision) is not int or self.revision<0
                or type(self.forex_applicable) is not bool or type(self.crypto_applicable) is not bool):
            raise ValueError('Canonical account, saved draft, revision and explicit market choices required')


@dataclass(frozen=True)
class AccountSelectionPreview:
    year:int
    month:int
    accounts:tuple[AccountReconciliationPreview,...]
    all_inventory_reconciled:bool
    quantity_totals:tuple[tuple[str,str],...]
    publication_authorized:bool=False


@contextmanager
def selected_sessions(gateway,identity,account_ids):
    if type(gateway) is not LocalAccountDataGateway:
        raise ValueError('Guarded local account gateway required')
    if type(account_ids) is not tuple or not account_ids:
        raise ValueError('Nonempty immutable account selection required')
    sessions=()
    # The outer transaction holds the directory share lock through the entire
    # read, blocking grant/identity/binding changes. It reads no financial rows.
    with gateway.transaction(identity,account_ids[0]) as guard:
        directory=gateway._directory._load(guard._connection)
        authorized=directory.select(identity,account_ids)
        # A second connection gives all selected accounts ONE repeatable-read,
        # PostgreSQL READ ONLY snapshot. Source edits may commit but cannot mix
        # old and new revisions within the returned report.
        with gateway._directory._connection(readonly=True) as conn:
            conn.execute('SET LOCAL search_path = campaigniq_accounts_dev, pg_catalog')
            try:
                sessions=tuple(AccountDataSession(conn,account,False,gateway._directory.registry_id)
                               for account in authorized)
                yield sessions
                for session in sessions:session._check()
            finally:
                for session in sessions:session._active=False


def compare_selected(service,identity,year,month,selection,*,include_totals=False):
    if type(service) is not BoundAccountImportPreviewService:
        raise ValueError('Bound local account service required')
    if type(year) is not int or type(month) is not int or not 1900<=year<=2200:
        raise ValueError('Supported integer reporting month required')
    date(year,month,1)
    if (type(selection) is not tuple or not selection or any(type(item) is not SelectedDraft for item in selection)
            or type(include_totals) is not bool):
        raise ValueError('Nonempty typed selection and explicit totals choice required')
    account_ids=tuple(item.account_id for item in selection)
    if len(set(account_ids))!=len(account_ids):raise ValueError('Duplicate selected account')
    with selected_sessions(service.gateway,identity,account_ids) as sessions:
        drafts=[]
        for session,item in zip(sessions,selection,strict=True):
            draft=session.drafts.get(item.draft_id)
            if (draft.year,draft.month)!=(year,month) or draft.revision!=item.revision:
                raise ValueError('Selected month or draft revision changed; reload the comparison')
            drafts.append(draft)
        results=tuple(_reconcile_session(service,session,draft,
            forex_applicable=item.forex_applicable,crypto_applicable=item.crypto_applicable)
            for session,draft,item in zip(sessions,drafts,selection,strict=True))
        verified=all(result.inventory_reconciled for result in results)
        totals={}
        if include_totals and verified:
            for result in results:
                for instrument,quantity in result.closing_quantities:
                    totals[instrument]=totals.get(instrument,Decimal(0))+Decimal(quantity)
        return AccountSelectionPreview(year,month,results,verified,
            tuple((key,str(value)) for key,value in sorted(totals.items())))
