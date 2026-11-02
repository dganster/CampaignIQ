"""Scoped user assertion of an empty futures opening book; never broker evidence."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from campaigniq.broker_account_binding import verify_document
from campaigniq.futures_accounting import read_fills
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader

ROLE = 'futures_empty_opening_confirmation_v1'
HISTORY = 'thinkorswim_trade_history'
FORMAT = 'campaigniq.user_confirmed_empty_futures_opening'


def source_context(service, session, draft):
    try:
        _, media, content = session.drafts.document(draft.id, HISTORY)
    except FileNotFoundError as error:
        raise ValueError('Save this month’s Trade History before confirming futures opening positions') from error
    if media not in ('text/plain', 'text/csv', 'application/vnd.ms-excel'):
        raise ValueError('A saved Trade History CSV is required')
    text = content.decode('utf-8-sig')
    known = service._known(session)
    verify_document(known, HISTORY, text)
    heading = text.splitlines()[0]
    period = re.search(r'since (\d{1,2}/\d{1,2}/\d{2}) through (\d{1,2}/\d{1,2}/\d{2})', heading)
    if not period:
        raise ValueError('Trade History must declare the complete selected month')
    start, end = (datetime.strptime(value, '%m/%d/%y').date() for value in period.groups())
    first = date(draft.year, draft.month, 1)
    last = date(draft.year, draft.month, monthrange(draft.year, draft.month)[1])
    if start > first or end < last:
        raise ValueError('Trade History does not cover the complete selected month')
    with TemporaryDirectory(prefix='campaigniq-futures-opening-') as temporary:
        path = Path(temporary)/'history.csv'
        path.write_bytes(content)
        source = ThinkorswimSourceReader().read(str(path))
        if sum(section.name == 'Account Trade History' for section in source.sections) != 1:
            raise ValueError('Missing or ambiguous Trade History table')
        if not read_fills(source, draft.year, draft.month):
            raise ValueError('No supported futures-option executions for this month')
    return dict(history_sha256=sha256(content).hexdigest(), binding_digest=known['securities'].digest)


def make_receipt(session, draft, actor_id, context):
    if not isinstance(actor_id, str) or not actor_id:
        raise ValueError('Current server-authenticated actor required')
    return dict(format=FORMAT, version=1, account_id=session.account.account_id,
        draft_id=str(draft.id), year=draft.year, month=draft.month,
        opening_as_of=(date(draft.year,draft.month,1)-timedelta(days=1)).isoformat(),
        empty_futures=True, empty_futures_options=True, evidence_type='user_confirmation',
        broker_verified=False, confirmed_by=actor_id,
        confirmed_at=datetime.now(timezone.utc).isoformat(), confirmed_source_revision=draft.revision, **context)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field in futures opening confirmation')
        result[key] = value
    return result


def load_receipt(session, draft):
    try:
        _, media, content = session.drafts.document(draft.id, ROLE)
    except FileNotFoundError:
        return None
    if media != 'text/plain':
        raise ValueError('Invalid futures opening confirmation media type')
    result = json.loads(content, object_pairs_hook=_unique_object)
    fields = {'format','version','account_id','draft_id','year','month','opening_as_of',
              'empty_futures','empty_futures_options','evidence_type','broker_verified',
              'confirmed_by','confirmed_at','confirmed_source_revision','history_sha256','binding_digest'}
    expected_date = (date(draft.year,draft.month,1)-timedelta(days=1)).isoformat()
    if (not isinstance(result,dict) or set(result) != fields or result['format'] != FORMAT or
            type(result['version']) is not int or result['version'] != 1 or
            result['account_id'] != session.account.account_id or result['draft_id'] != str(draft.id) or
            type(result['year']) is not int or type(result['month']) is not int or
            (result['year'],result['month']) != (draft.year,draft.month) or
            result['opening_as_of'] != expected_date or result['empty_futures'] is not True or
            result['empty_futures_options'] is not True or result['broker_verified'] is not False or
            result['evidence_type'] != 'user_confirmation' or
            type(result['confirmed_source_revision']) is not int or
            not 0 <= result['confirmed_source_revision'] < draft.revision or
            not isinstance(result['confirmed_by'],str) or not result['confirmed_by']):
        raise ValueError('Futures opening confirmation scope or evidence type differs')
    for key in ('history_sha256','binding_digest'):
        if not isinstance(result[key],str) or not re.fullmatch(r'[0-9a-f]{64}',result[key]):
            raise ValueError('Invalid futures opening confirmation fingerprint')
    if not isinstance(result['confirmed_at'],str):
        raise ValueError('Invalid futures opening confirmation timestamp')
    when = datetime.fromisoformat(result['confirmed_at'])
    if when.tzinfo is None:
        raise ValueError('Futures opening confirmation timestamp must include timezone')
    return result


def validate_receipt(service, session, draft):
    receipt = load_receipt(session,draft)
    if receipt is None:
        raise ValueError('No saved confirmation of empty futures opening positions')
    context = source_context(service,session,draft)
    if any(receipt[key] != value for key,value in context.items()):
        raise ValueError('Trade History or account binding changed; reconfirm futures opening positions')
    return receipt
