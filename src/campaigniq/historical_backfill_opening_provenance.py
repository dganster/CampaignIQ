"""Read-only source receipts for opening lots; never assign campaign identities.

A documented opening balance is not an observed campaign start. These receipts
explain that distinction without inventing pre-period trades, dates or basis.
"""
from datetime import timedelta
from decimal import Decimal

from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.persistence.lot_book_store import _serialize_instrument, serialize_lot_book


def _lots(book):
    lots = [lot for instrument in book.instruments() for lot in book.lots(instrument)]
    if len({lot.lot_id for lot in lots}) != len(lots):
        raise ValueError('Provenance requires unique lot IDs.')
    return {lot.lot_id: lot for lot in lots}


def _identity(lot):
    # Quantity/basis may reduce on a partial close. This fingerprint deliberately
    # excludes those two changing values, but preserves origin and campaign ID.
    return dict(instrument=_serialize_instrument(lot.instrument), opened_at=lot.opened_at.isoformat(),
                campaign_id=lot.campaign_id, basis_source=lot.basis_source)


def trace_opening_provenance(result, *, period_start, period_end, documents, previous=None):
    """Replay source activity and return JSON-safe receipts on a separate book.

    Previous receipts are only retained for the same lot identity and direction,
    at no more than its previous closing quantity. An identity change is recorded
    as unverified, never repaired. Source hashes bind receipts to saved bytes;
    their existence does not certify broker authenticity or full continuity.
    """
    previous = previous or {}
    receipts = [{k: doc[k] for k in ('id', 'role', 'sha256')} for doc in documents]
    if any(len(doc['sha256']) != 64 or any(c not in '0123456789abcdef' for c in doc['sha256']) for doc in receipts):
        raise ValueError('Invalid source checksum.')
    source_roles = {doc['role'] for doc in receipts}
    from campaigniq.import_contract import MonthlyInputRole as Role
    has_sources = {Role.THINKORSWIM_TRADE_HISTORY.value, 'schwab_brokerage_statement'} <= source_roles
    original = serialize_lot_book(period_end=period_start - timedelta(days=1), lot_book=result.opening_lot_book)
    book = result.opening_lot_book.clone()
    ledger = {}; births = []; opening = []
    for lid, lot in _lots(book).items():
        prior = previous.get(lid)
        retain = (prior is not None and prior['identity'] == _identity(lot)
                  and Decimal(prior['quantity']) * lot.quantity > 0
                  and abs(lot.quantity) <= abs(Decimal(prior['quantity'])))
        if retain:
            receipt = dict(prior)
        else:
            receipt = dict(lot_id=lid, identity=_identity(lot), quantity=str(lot.quantity),
                           origin_kind='opening_balance' if prior is None else 'changed_carried_identity',
                           actual_campaign_start=None, acquisition_date=None,
                           snapshot_observed_at=(period_start - timedelta(days=1)).isoformat(),
                           basis_total=None if lot.basis_total is None else str(lot.basis_total),
                           campaign_identity_verified=False, parent_lot_ids=[], source_documents=receipts,
                           observed_open_verified=False)
        if prior is not None and not retain:
            receipt['previous_receipt'] = {key: prior.get(key) for key in ('lot_id', 'identity', 'quantity', 'origin_kind', 'source_documents')}
        ledger[lid] = receipt
        opening.append(dict(receipt))
    source_trades = [(id(trade), campaign.campaign_id) for campaign in result.campaigns for trade in campaign.trades]
    if len({tid for tid, _ in source_trades}) != len(source_trades):
        raise ValueError('Repeated campaign trade cannot establish provenance.')
    owners = dict(source_trades)
    for ordinal, item in enumerate(result.position_history.items(), 1):
        before = _lots(book)
        parents = []
        consumed_quantities = {}
        direct = False
        if item.trade is not None:
            if id(item.trade) not in owners:
                raise ValueError('Unowned source trade cannot establish provenance.')
            allocations = book.apply_trade(item.trade, campaign_id=owners[id(item.trade)])
            parents = list(dict.fromkeys(allocation.lot_id for allocation in allocations))
            for allocation in allocations:
                consumed_quantities[allocation.lot_id] = consumed_quantities.get(allocation.lot_id, Decimal(0)) + abs(allocation.quantity)
            direct = bool(item.trade.legs) and all(leg.position_effect is PositionEffect.OPEN for leg in item.trade.legs)
            kind = 'observed_open' if direct else 'observed_trade_with_prior_inventory'
        else:
            event = item.event
            if event is None:
                raise ValueError('Missing position event.')
            if event.kind is PositionEventKind.ASSIGNMENT:
                probe = book.clone()
                for change in event.changes:
                    if isinstance(change.instrument, OptionContract):
                        allocations = probe.apply_signed_change(instrument=change.instrument,
                            quantity=change.quantity, occurred_at=event.occurred_at)
                        parents.extend(allocation.lot_id for allocation in allocations)
                        for allocation in allocations:
                            consumed_quantities[allocation.lot_id] = consumed_quantities.get(allocation.lot_id, Decimal(0)) + abs(allocation.quantity)
            LotBookPeriodApplier()._apply_event(book, event)
            kind = 'observed_' + event.kind.value.lower()
        for lid, lot in _lots(book).items():
            if lid in before:
                continue
            parent_receipts = [{key: ledger[parent].get(key) for key in ('lot_id', 'origin_kind', 'identity', 'actual_campaign_start', 'snapshot_observed_at', 'quantity', 'source_documents')}
                               for parent in parents if parent in ledger]
            receipt = dict(lot_id=lid, identity=_identity(lot), quantity=str(lot.quantity),
                           origin_kind=kind, observed_at=item.occurred_at.isoformat(),
                           actual_campaign_start=None, acquisition_date=item.occurred_at.isoformat() if direct else None,
                           basis_total=None if lot.basis_total is None else str(lot.basis_total),
                           campaign_identity_verified=False,
                           parent_lot_ids=parents, parent_origins=parent_receipts,
                           parent_consumed_quantities={lid: str(qty) for lid, qty in consumed_quantities.items()},
                           source_documents=receipts, source_item_ordinal=ordinal,
                           observed_open_verified=direct and has_sources and lot.campaign_id is not None)
            ledger[lid] = receipt
            births.append(dict(receipt))
    expected = serialize_lot_book(period_end=period_end, lot_book=result.ending_lot_book)
    if serialize_lot_book(period_end=period_end, lot_book=book) != expected:
        raise ValueError('Opening provenance replay differs from ending state.')
    if serialize_lot_book(period_end=period_start - timedelta(days=1), lot_book=result.opening_lot_book) != original:
        raise RuntimeError('Opening provenance mutated source state.')
    closing = {}
    for lid, lot in _lots(book).items():
        receipt = dict(ledger[lid])
        receipt.update(identity=_identity(lot), quantity=str(lot.quantity))
        closing[lid] = receipt
    return dict(opening=opening, observed_origins=births, closing=closing,
                campaign_identities_assigned=False, publication_authorized=False)

