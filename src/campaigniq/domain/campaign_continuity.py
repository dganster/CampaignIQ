"""Plan carried campaign identity from chronological FIFO evidence, without writes.

A matching ID is not proof of source authenticity or full downstream continuity.
"""
from dataclasses import dataclass, replace

from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_event_kind import PositionEventKind


@dataclass(frozen=True)
class CampaignContinuityPlan:
    _campaigns: tuple
    bindings: tuple[tuple[str, str], ...]
    blocking_findings: tuple[str, ...]

    @property
    def campaigns(self):
        if self.blocking_findings:
            raise ValueError('A blocked continuity plan cannot supply replay campaigns.')
        return self._campaigns


def plan_campaign_continuity(*, opening_lot_book, position_history, campaigns):
    """Link reconstructed campaigns to carried IDs using actual FIFO closures.

    Speculative replay occurs on a clone. New openings can precede a roll/close
    that links their month-local campaign to an existing one. Connected activity
    may inherit exactly one carried ID. Unknown ancestry and multiple carried
    identities are blockers; original lots and campaigns are never rewritten.
    """
    campaigns = tuple(campaigns)
    identifiers = [c.campaign_id for c in campaigns]
    if any(not isinstance(cid, str) or not cid for cid in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError('Current campaigns require unique nonempty IDs.')
    carried = {}
    findings = set()
    for instrument in opening_lot_book.instruments():
        for lot in opening_lot_book.lots(instrument):
            if lot.campaign_id is not None:
                if not isinstance(lot.campaign_id, str) or not lot.campaign_id:
                    raise ValueError('Carried campaign IDs must be nonempty strings.')
                carried.setdefault(lot.campaign_id, set()).add(underlying_symbol(instrument))
    for cid, symbols in carried.items():
        if len(symbols) > 1:
            findings.add(f'Carried campaign {cid} spans multiple underlyings.')
    if set(identifiers) & set(carried):
        findings.add('Reconstructed IDs collide with carried campaign IDs.')

    owner = {}
    symbols = dict(carried)
    for campaign in campaigns:
        symbols[campaign.campaign_id] = {
            underlying_symbol(leg.instrument) for trade in campaign.trades for leg in trade.legs}
        if len(symbols[campaign.campaign_id]) != 1:
            findings.add(f'Campaign {campaign.campaign_id} does not contain exactly one underlying.')
        for trade in campaign.trades:
            if id(trade) in owner:
                findings.add('A trade belongs to more than one reconstructed campaign.')
            owner[id(trade)] = campaign.campaign_id

    graph = {cid: set() for cid in (*carried, *identifiers)}
    book = opening_lot_book.clone()
    visited_trades = set()
    try:
        for item in position_history.items():
            if item.trade is None:
                if item.event is None:
                    raise ValueError('History item has no trade or event.')
                if item.event.kind in {PositionEventKind.ASSIGNMENT, PositionEventKind.EXPIRATION}:
                    for change in item.event.changes:
                        if isinstance(change.instrument, OptionContract):
                            available = sum(abs(lot.quantity) for lot in book.lots(change.instrument)
                                            if lot.quantity * change.quantity < 0)
                            if change.quantity == 0 or available < abs(change.quantity):
                                raise ValueError('Option event lacks sufficient carried/opened inventory.')
                unknown_before = {
                    lot.lot_id: abs(lot.quantity)
                    for instrument in book.instruments() for lot in book.lots(instrument)
                    if lot.campaign_id is None
                }
                LotBookPeriodApplier()._apply_event(book, item.event)
                after = {lot.lot_id: abs(lot.quantity)
                         for instrument in book.instruments() for lot in book.lots(instrument)}
                for lot_id, quantity in unknown_before.items():
                    if after.get(lot_id, 0) < quantity:
                        findings.add(f'A position event consumes lot {lot_id} with unknown campaign ancestry.')
                continue
            trade = item.trade
            cid = owner.get(id(trade))
            if cid is None or id(trade) in visited_trades:
                raise ValueError('History trade is unowned or repeated.')
            visited_trades.add(id(trade))
            unknown_before = {
                lot.lot_id: abs(lot.quantity)
                for instrument in book.instruments() for lot in book.lots(instrument)
                if lot.campaign_id is None
            }
            allocations = book.apply_trade(trade, campaign_id=cid)
            after = {lot.lot_id: abs(lot.quantity)
                     for instrument in book.instruments() for lot in book.lots(instrument)}
            for lot_id, quantity in unknown_before.items():
                if after.get(lot_id, 0) < quantity:
                    findings.add(f'Campaign {cid} consumes lot {lot_id} with unknown campaign ancestry.')
            for allocation in allocations:
                previous = allocation.campaign_id
                if previous is None:
                    findings.add(f'Campaign {cid} closes lot {allocation.lot_id} with unknown campaign ancestry.')
                    continue
                if previous not in graph:
                    raise ValueError('Closing allocation has an unrecognized campaign ID.')
                graph[cid].add(previous)
                graph[previous].add(cid)
    except ValueError as error:
        findings.add('Chronological continuity replay failed: ' + str(error))
    if set(owner) != visited_trades:
        findings.add('Campaign trades and position history do not match completely.')

    bindings = {}
    seen = set()
    for first in graph:
        if first in seen:
            continue
        component, pending = set(), [first]
        while pending:
            cid = pending.pop()
            if cid in component:
                continue
            component.add(cid)
            pending.extend(graph[cid] - component)
        seen.update(component)
        old = component & set(carried)
        current = component & set(identifiers)
        if not current:
            continue
        all_symbols = set().union(*(symbols[cid] for cid in component))
        if len(all_symbols) != 1:
            findings.add('Connected campaign activity spans multiple underlyings.')
            continue
        if len(old) > 1:
            findings.add('Connected activity would merge distinct carried campaigns: ' + ', '.join(sorted(old)))
            continue
        if len(old) == 1:
            target = next(iter(old))
            bindings.update((cid, target) for cid in current)
        elif len(current) > 1:
            findings.add('Connected activity would merge distinct new campaigns: ' + ', '.join(sorted(current)))
    return CampaignContinuityPlan(
        tuple(replace(c, campaign_id=bindings.get(c.campaign_id, c.campaign_id)) for c in campaigns),
        tuple(sorted(bindings.items())), tuple(sorted(findings)))
