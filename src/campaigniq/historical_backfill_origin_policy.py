"""Local preview receipts for observed closure of documented opening balances.

An observed connection to one carried campaign is not a claim that the stock
was acquired when that campaign began. Unknown original dates remain unknown.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import os

from campaigniq.persistence.lot_book_store import serialize_lot_book


@dataclass(frozen=True)
class OpeningOriginEvidence:
    period_end: date
    state_sha256: str
    documented_unknown_lot_ids: tuple[str, ...]
    source_sha256: tuple[str, ...]


def _local():
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Opening origin evidence is for local development previews only.')


def bind_opening_origins(book, *, period_end, receipts, documents):
    """Bind exact unknown opening lots to source-validated provenance receipts.

    Documents are the verified saved capture records, not a filename whitelist.
    A receipt must trace to an actual saved opening snapshot, or to the observed
    assignment of such a snapshot option. Changed carried identities do not qualify.
    """
    _local()
    from campaigniq.historical_backfill_opening_provenance import _identity
    from decimal import Decimal
    from base64 import b64decode
    docs = {}
    for doc in documents:
        raw = b64decode(doc['content_base64'], validate=True)
        if sha256(raw).hexdigest() != doc['sha256']:
            raise ValueError('Opening origin source bytes do not match the capture checksum.')
        docs[doc['id']] = doc

    def sources(row, role):
        matches = []
        for ref in row.get('source_documents', ()):
            doc = docs.get(ref.get('id'))
            if doc is None or doc['sha256'] != ref.get('sha256') or doc['role'] != ref.get('role'):
                raise ValueError('Opening origin source receipt is absent or changed.')
            if doc['role'] == role:
                matches.append(doc['sha256'])
        return matches

    allowed = []; hashes = set()
    for instrument in book.instruments():
        for lot in book.lots(instrument):
            if lot.campaign_id is not None:
                continue
            row = receipts.get(lot.lot_id)
            if row is None or row['identity'] != _identity(lot) or Decimal(row['quantity']) != lot.quantity:
                continue
            if row['origin_kind'] == 'opening_balance':
                proof = sources(row, 'schwab_opening_position_snapshot')
                # Snapshot-only evidence is explicitly not an acquisition date.
                if (not lot.lot_id.startswith('SCHWAB-SNAPSHOT:2024-12-31T')
                        or row.get('snapshot_observed_at') != '2024-12-31' or not proof):
                    continue
            elif row['origin_kind'] == 'observed_assignment':
                proof = sources(row, 'schwab_brokerage_statement')
                parents = row.get('parent_origins', ())
                if len(parents) != 1 or parents[0].get('origin_kind') != 'opening_balance':
                    continue
                parent = parents[0]
                inst = parent['identity']['instrument']
                if (inst.get('type') != 'option' or inst.get('option_type') != 'PUT'
                        or inst.get('underlying') != getattr(instrument, 'symbol', None)
                        or lot.quantity <= 0 or not proof
                        or parent.get('snapshot_observed_at') != '2024-12-31'
                        or not parent['lot_id'].startswith('SCHWAB-SNAPSHOT:2024-12-31T')
                        or Decimal(parent.get('quantity', '0')) >= 0
                        or Decimal(row.get('parent_consumed_quantities', {}).get(parent['lot_id'], '0')) > abs(Decimal(parent.get('quantity', '0')))
                        or Decimal(row.get('parent_consumed_quantities', {}).get(parent['lot_id'], '0')) * 100 != lot.quantity):
                    continue
                proof += sources(parent, 'schwab_opening_position_snapshot')
                if not proof or not sources(parent, 'schwab_opening_position_snapshot'):
                    continue
            else:
                continue
            allowed.append(lot.lot_id); hashes.update(proof)
    state = serialize_lot_book(period_end=period_end, lot_book=book)
    return OpeningOriginEvidence(period_end, sha256(state.encode()).hexdigest(), tuple(sorted(allowed)), tuple(sorted(hashes)))


def verify_opening_origins(evidence, book, *, period_end):
    _local()
    if not isinstance(evidence, OpeningOriginEvidence) or evidence.period_end != period_end:
        raise ValueError('Opening origin evidence has the wrong boundary.')
    actual = sha256(serialize_lot_book(period_end=period_end, lot_book=book).encode()).hexdigest()
    if actual != evidence.state_sha256:
        raise ValueError('Opening state differs from source-bound origin evidence.')
    return evidence.documented_unknown_lot_ids
