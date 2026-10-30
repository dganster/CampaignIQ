"""Explicit LOCAL publication for new accounts using fresh server sign-in.

The writable artifact handle exists only inside this publisher. Ordinary upload
and preview transactions retain their read-only artifact wrappers. No Render
or production connection is accepted by the underlying directory store.
"""
from calendar import monthrange
from contextlib import contextmanager
from datetime import date
import json
from uuid import UUID

from campaigniq.account_local_publication import _publish_in_session
from campaigniq.account_signedin_inputs import SignedInMonthlyInputs
from campaigniq.persistence.account_data_session import AccountDataSession
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway, _new_schwab_account


@contextmanager
def _publication_transaction(gateway, user, account_id):
    if type(gateway) is not LocalSignedInInputGateway:
        raise ValueError('Guarded signed-in local gateway required')
    session = None
    with gateway._directory._connection() as conn:
        try:
            conn.execute('''SELECT registry_id FROM campaigniq_admin_dev.directories
                WHERE registry_id=%s FOR SHARE''', (gateway._directory.registry_id,))
            directory, actor = gateway._current(conn, user)
            access = directory.authorize(actor.identity, account_id, write=True)
            _new_schwab_account(directory, account_id)
            if not gateway._ready(conn):
                raise ValueError('Dedicated local input storage is not initialized')
            conn.execute('SET LOCAL search_path=campaigniq_signedin_accounts_dev,pg_catalog')
            session = AccountDataSession(conn, access, True, gateway._directory.registry_id)
            session._physical_scope = 'signedin-' + UUID(gateway._directory.registry_id).hex + '-' + UUID(account_id).hex
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (session.artifacts.namespace,))
            yield session, actor.user_id
            session._check()
        finally:
            if session is not None:
                session._active = False


def publish_signedin(service, user, account_id, draft_id, revision, *, authorize_local_publication=False,
                     forex_applicable=True, crypto_applicable=True):
    if authorize_local_publication is not True:
        raise PermissionError('Confirm publication to the LOCAL development database')
    if type(service) is not SignedInMonthlyInputs:
        raise ValueError('Signed-in local monthly input service required')
    if type(revision) is not int or any(type(flag) is not bool for flag in (forex_applicable, crypto_applicable)):
        raise ValueError('Displayed revision and explicit boolean market choices required')
    with _publication_transaction(service.gateway, user, account_id) as (session, actor_id):
        return _publish_in_session(service, session, actor_id, account_id, draft_id, revision,
            forex_applicable=forex_applicable, crypto_applicable=crypto_applicable,
            receipt_format='campaigniq.signedin_local_account_publication')


def published_signedin(service, user, account_id, draft_id, revision):
    if type(service) is not SignedInMonthlyInputs or type(revision) is not int:
        raise ValueError('Signed-in service and displayed revision required')
    with service.gateway.transaction(user, account_id) as session:
        draft = service._lock(session, draft_id)
        if draft.revision != revision:
            raise ValueError('Draft changed; reload before checking publication')
        return _published_session(service, session, account_id, draft, revision)


def _published_session(service, session, account_id, draft, revision, *, receipt_format='campaigniq.signedin_local_account_publication'):
    """Verify the selected generation on an already scoped session."""
    end = date(draft.year, draft.month, monthrange(draft.year, draft.month)[1])
    prefix = f'{draft.year:04d}-{draft.month:02d}-'
    if not session.artifacts.list_keys(prefix=prefix):
        return False
    required = (prefix+'finalized.json', prefix+'lot-book.json', prefix+'account-publication-receipt.json')
    if not all(session.artifacts.exists(key) for key in required):
        raise ValueError('Incomplete local generation; preserve existing records and investigate')
    marker = json.loads(session.artifacts.read_text(required[0]))
    receipt = json.loads(session.artifacts.read_text(required[2]))
    from campaigniq.persistence.lot_book_store import load_lot_book_from_storage
    if (marker != dict(format='campaigniq.finalized_month', version=1, period_end=end.isoformat())
        or receipt.get('format') != receipt_format
        or receipt.get('registry_id') != service.gateway._directory.registry_id
        or receipt.get('account_id') != account_id or receipt.get('draft_id') != str(draft.id)
        or receipt.get('draft_revision') != revision or receipt.get('period_end') != end.isoformat()
        or load_lot_book_from_storage(session.artifacts, required[1]).period_end != end
        or not is_month_published_in_storage(session.artifacts, period_end=end)):
        raise ValueError('Local generation and source receipt differ; publication status unverified')
    return True
