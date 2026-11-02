"""Owner-confirmed flat ending futures book, bound to the entire preview evidence."""
from calendar import monthrange
from datetime import date,datetime,timezone
from hashlib import sha256
import json
import re

from campaigniq import account_futures_opening as opening
from campaigniq import account_futures_preview as statement

ROLE='futures_empty_closing_confirmation_v1'
FORMAT='campaigniq.user_confirmed_empty_futures_closing'


def graph_digest(session,draft,result):
    # Draft revisions and display-only closing annotations change when saving this
    # record. Source documents and financial calculations must remain identical.
    calculation={k:v for k,v in result.items() if k not in ('source_revision','blockers',
        'closing_positions_confirmed_by_owner','closing_evidence_type')}
    evidence={role:sha256(session.drafts.document(draft.id,role)[2]).hexdigest()
              for role in (opening.HISTORY,opening.ROLE,statement.PDF_ROLE,statement.RECEIPT_ROLE)}
    return sha256(json.dumps(dict(calculation=calculation,evidence=evidence),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load_receipt(session,draft):
    try:_,media,content=session.drafts.document(draft.id,ROLE)
    except FileNotFoundError:return None
    if media!='text/plain':raise ValueError('Invalid futures closing confirmation media type')
    result=json.loads(content,object_pairs_hook=opening._unique_object)
    fields={'format','version','account_id','draft_id','year','month','closing_as_of','empty_futures',
        'empty_futures_options','evidence_type','broker_verified','confirmed_by','confirmed_at',
        'confirmed_source_revision','evidence_graph_sha256'}
    end=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1]).isoformat()
    if (not isinstance(result,dict) or set(result)!=fields or result['format']!=FORMAT or
            type(result['version']) is not int or result['version']!=1 or
            result['account_id']!=session.account.account_id or result['draft_id']!=str(draft.id) or
            type(result['year']) is not int or type(result['month']) is not int or
            (result['year'],result['month'])!=(draft.year,draft.month) or result['closing_as_of']!=end or
            result['empty_futures'] is not True or result['empty_futures_options'] is not True or
            result['evidence_type']!='user_confirmation' or result['broker_verified'] is not False or
            type(result['confirmed_source_revision']) is not int or
            not 0<=result['confirmed_source_revision']<draft.revision or
            not isinstance(result['confirmed_by'],str) or not result['confirmed_by'] or
            not isinstance(result['evidence_graph_sha256'],str) or
            not re.fullmatch('[0-9a-f]{64}',result['evidence_graph_sha256'])):
        raise ValueError('Futures closing confirmation scope or evidence type differs')
    if not isinstance(result['confirmed_at'],str) or datetime.fromisoformat(result['confirmed_at']).tzinfo is None:
        raise ValueError('Futures closing confirmation requires a server timestamp with timezone')
    return result


def attach_status(session,draft,result):
    result=dict(result)
    result.update(closing_positions_confirmed_by_owner=False,closing_evidence_type='missing')
    receipt=load_receipt(session,draft)
    if receipt is None:return result
    if receipt['evidence_graph_sha256']!=graph_digest(session,draft,result):
        raise ValueError('Futures evidence changed; reconfirm ending positions')
    if result['calculated_ending_options'] or result['calculated_ending_futures']:
        raise ValueError('An empty ending confirmation conflicts with calculated positions')
    result.update(closing_positions_confirmed_by_owner=True,closing_evidence_type='user_confirmation')
    result['blockers']=['Opening and ending positions are owner-confirmed, not independently broker-verified.',
        'Futures publication integration remains pending.','Crypto remains separate and requires its own statement.']
    return result


def make_receipt(session,draft,actor_id,result):
    if result['calculated_ending_options'] or result['calculated_ending_futures']:
        raise ValueError('Calculated futures positions are not empty; confirmation rejected')
    return dict(format=FORMAT,version=1,account_id=session.account.account_id,draft_id=str(draft.id),
        year=draft.year,month=draft.month,closing_as_of=date(draft.year,draft.month,monthrange(draft.year,draft.month)[1]).isoformat(),
        empty_futures=True,empty_futures_options=True,evidence_type='user_confirmation',broker_verified=False,
        confirmed_by=actor_id,confirmed_at=datetime.now(timezone.utc).isoformat(),confirmed_source_revision=draft.revision,
        evidence_graph_sha256=graph_digest(session,draft,result))
