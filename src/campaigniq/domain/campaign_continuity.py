"""Plan carried campaign identity from chronological FIFO evidence, without writes.

A matching ID is not proof of source authenticity or full downstream continuity.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side

from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_event_kind import PositionEventKind


@dataclass(frozen=True)
class CampaignContinuityPlan:
    _campaigns: tuple
    bindings: tuple[tuple[str, str], ...]
    blocking_findings: tuple[str, ...]
    trade_bindings: tuple = ()

    @property
    def campaigns(self):
        if self.blocking_findings:
            raise ValueError('A blocked continuity plan cannot supply replay campaigns.')
        return self._campaigns


@dataclass(frozen=True)
class TradeCampaignBinding:
    trade_ordinal: int
    occurred_at: str
    source_campaign_id: str
    target_campaign_id: str
    supported_by_fifo: bool
    consumed_lot_ids: tuple[str, ...]
    consumed_lot_quantities: tuple[tuple[str, str], ...] = ()
    evidence_kind: str = 'fifo_consumption'
    documented_opening_lot_ids: tuple[str, ...] = ()
    covered_equity_lot_ids: tuple[str, ...] = ()


class CampaignContinuityError(ValueError):
    """Retain partial trade evidence while refusing a blocked replay."""
    def __init__(self, plan):
        super().__init__('Campaign continuity blocked: ' + '; '.join(plan.blocking_findings))
        self.trade_bindings = plan.trade_bindings


def plan_campaign_continuity(*, opening_lot_book, position_history, campaigns, documented_opening_lots=(), link_assignment_calls=False):
    """Link reconstructed campaigns to carried IDs using actual FIFO closures.

    Speculative replay occurs on a clone. Exact trade nodes, not monthly grouping,
    connect new openings and rolls to consumed FIFO lots. Distinct trades in one
    monthly campaign may inherit different carried identities. An atomic connected
    component still cannot merge carried IDs or invent unknown ancestry. Original
    opening lots and source campaign objects are never rewritten. Optional
    assignment call linkage attaches only a newly observed single-leg short call
    to one unambiguous, sufficiently covered share delivery in this replay. It
    cannot merge existing carried IDs or infer coverage from a symbol alone.
    """
    documented_opening_lots = frozenset(documented_opening_lots)
    documented_consumption = {}
    if type(link_assignment_calls) is not bool:
        raise ValueError('Assignment call linkage must be an explicit boolean.')
    assigned_equity = {}
    coverage = {}
    coverage_lots = {}
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

    owner = {}; ambiguous_owner = set()
    symbols = dict(carried)
    for campaign in campaigns:
        symbols[campaign.campaign_id] = {
            underlying_symbol(leg.instrument) for trade in campaign.trades for leg in trade.legs}
        if len(symbols[campaign.campaign_id]) != 1:
            findings.add(f'Campaign {campaign.campaign_id} does not contain exactly one underlying.')
        for trade in campaign.trades:
            if id(trade) in owner:
                findings.add('A trade belongs to more than one reconstructed campaign.')
                ambiguous_owner.add(id(trade))
            owner[id(trade)] = campaign.campaign_id

    # A monthly builder's grouping is not evidence that separate trades merge
    # carried campaigns. Give each actual trade its own temporary replay owner;
    # connect these nodes only through consumed FIFO lots or propagated events.
    nodes = {}; records = {}; reserved = set(carried) | set(identifiers)
    for item in position_history.items():
        if item.trade is None or id(item.trade) in nodes or id(item.trade) not in owner:
            continue
        ordinal = len(nodes) + 1
        token = f'__CONTINUITY_TRADE_{ordinal:06d}__'
        while token in reserved:
            token += '_'
        reserved.add(token)
        nodes[id(item.trade)] = token
        records[token] = (ordinal, item.occurred_at.isoformat(), owner[id(item.trade)], item.trade)
    graph = {cid: set() for cid in (*carried, *records)}
    symbols.update({token: symbols[data[2]] for token, data in records.items()})
    book = opening_lot_book.clone()
    visited_trades = set()
    tainted = {token for tid, token in nodes.items() if tid in ambiguous_owner or records[token][2] in carried}
    consumed = {token: [] for token in records}; consumed_quantities = {token: [] for token in records}
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
                if item.event.kind is PositionEventKind.ASSIGNMENT:
                    # One delivery cannot silently combine distinct option
                    # ancestries. Connect only the FIFO option lots actually
                    # consumed; expiry alone does not merge campaigns.
                    event_node = f'__CONTINUITY_EVENT_{len(graph):06d}__'
                    while event_node in reserved:
                        event_node += '_'
                    reserved.add(event_node)
                    graph[event_node] = set()
                    symbols[event_node] = {underlying_symbol(change.instrument) for change in item.event.changes}
                    probe = book.clone()
                    for change in item.event.changes:
                        if isinstance(change.instrument, OptionContract):
                            allocations = probe.apply_signed_change(instrument=change.instrument, quantity=change.quantity,
                                occurred_at=item.event.occurred_at)
                            for allocation in allocations:
                                previous = allocation.campaign_id
                                if previous is not None:
                                    if previous not in graph:
                                        raise ValueError('Assignment has an unrecognized campaign ID.')
                                    graph[event_node].add(previous)
                                    graph[previous].add(event_node)
                before_ids = {lot.lot_id for instrument in book.instruments() for lot in book.lots(instrument)}
                LotBookPeriodApplier()._apply_event(book, item.event)
                if item.event.kind is PositionEventKind.SPLIT:
                    changed_symbols = {underlying_symbol(change.instrument) for change in item.event.changes}
                    for instrument in book.instruments():
                        if underlying_symbol(instrument) in changed_symbols:
                            for lot in book.lots(instrument):
                                assigned_equity.pop(lot.lot_id, None)
                if link_assignment_calls and item.event.kind is PositionEventKind.ASSIGNMENT:
                    for instrument in book.instruments():
                        if isinstance(instrument, OptionContract):
                            continue
                        for lot in book.lots(instrument):
                            if lot.lot_id not in before_ids and lot.quantity > 0 and lot.campaign_id in graph:
                                assigned_equity[lot.lot_id] = item.occurred_at
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
            token = nodes[id(trade)]
            if link_assignment_calls and len(trade.legs) == 1:
                leg = trade.legs[0]
                contract = leg.instrument
                if (isinstance(contract, OptionContract) and contract.option_type is OptionType.CALL
                        and leg.side is Side.SELL and leg.position_effect is PositionEffect.OPEN):
                    equity = [lot for instrument in book.instruments()
                              if not isinstance(instrument, OptionContract) and underlying_symbol(instrument) == contract.underlying
                              for lot in book.lots(instrument)]
                    owners = {lot.campaign_id for lot in equity}
                    quantity = sum((abs(e.quantity) for e in leg.executions), Decimal(0))
                    existing = [lot for instrument in book.instruments()
                                if isinstance(instrument, OptionContract) and instrument.underlying == contract.underlying
                                and instrument.option_type is OptionType.CALL for lot in book.lots(instrument)]
                    collateral = sum((lot.quantity for lot in equity), Decimal(0))
                    if (equity and len(owners) == 1 and next(iter(owners)) in graph
                            and all(lot.quantity > 0 and lot.lot_id in assigned_equity
                                    and assigned_equity[lot.lot_id] < item.occurred_at for lot in equity)
                            and quantity > 0 and quantity == quantity.to_integral_value()
                            and all(lot.quantity < 0 and coverage.get(lot.campaign_id) == next(iter(owners)) for lot in existing)
                            and (quantity + sum((abs(lot.quantity) for lot in existing), Decimal(0))) * 100 <= collateral):
                        anchor = next(iter(owners))
                        graph[token].add(anchor); graph[anchor].add(token)
                        coverage[token] = anchor
                        coverage_lots[token] = tuple(sorted(lot.lot_id for lot in equity))
            allocations = book.apply_trade(trade, campaign_id=token)
            consumed[token].extend(allocation.lot_id for allocation in allocations)
            consumed_quantities[token].extend((allocation.lot_id, str(allocation.quantity)) for allocation in allocations)
            after = {lot.lot_id: abs(lot.quantity)
                     for instrument in book.instruments() for lot in book.lots(instrument)}
            for lot_id, quantity in unknown_before.items():
                if after.get(lot_id, 0) < quantity and lot_id not in documented_opening_lots:
                    findings.add(f'Campaign {cid} consumes lot {lot_id} with unknown campaign ancestry.')
                    tainted.add(token)
            for allocation in allocations:
                previous = None if allocation.lot_id in unknown_before else allocation.campaign_id
                if previous is None and allocation.lot_id in documented_opening_lots:
                    documented_consumption.setdefault(token, set()).add(allocation.lot_id)
                    continue
                if previous is None:
                    findings.add(f'Campaign {cid} closes lot {allocation.lot_id} with unknown campaign ancestry.')
                    tainted.add(token)
                    continue
                if previous not in graph:
                    raise ValueError('Closing allocation has an unrecognized campaign ID.')
                if previous in tainted:
                    tainted.add(token)
                graph[token].add(previous)
                graph[previous].add(token)
    except ValueError as error:
        findings.add('Chronological continuity replay failed: ' + str(error))
        if item.trade is not None and id(item.trade) in nodes:
            tainted.add(nodes[id(item.trade)])
    if set(owner) != visited_trades:
        findings.add('Campaign trades and position history do not match completely.')

    targets = {}; supported = {}; seen = set()
    for first in graph:
        if first in seen:
            continue
        component, pending = set(), [first]
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(graph[node] - component)
        seen.update(component)
        old = component & set(carried)
        current = component & set(records)
        source_ids = {records[node][2] for node in current}
        all_symbols = set().union(*(symbols[node] for node in component))
        valid = True
        if len(all_symbols) != 1:
            findings.add('Connected campaign activity spans multiple underlyings.')
            valid = False
        if current & documented_consumption.keys() and len(old) != 1:
            findings.add('Documented opening lots require exactly one observed carried campaign connection.')
            valid = False
        if len(old) > 1:
            findings.add('Connected activity would merge distinct carried campaigns: ' + ', '.join(sorted(old)))
            valid = False
        if not old and len(source_ids) > 1:
            findings.add('Connected activity would merge distinct new campaigns: ' + ', '.join(sorted(source_ids)))
            valid = False
        for node in current:
            # Keep normal source grouping for new, unanchored activity. Only
            # proven consumption links may transfer it to a carried identity.
            targets[node] = next(iter(old)) if len(old) == 1 else records[node][2]
            supported[node] = valid and node not in tainted and id(records[node][3]) in visited_trades
    trade_bindings = tuple(TradeCampaignBinding(
        data[0], data[1], data[2], targets.get(node, data[2]),
        supported.get(node, False), tuple(consumed[node]), tuple(consumed_quantities[node]),
        'assignment_backed_covered_call' if node in coverage else 'documented_opening_fifo' if node in documented_consumption else 'carried_fifo' if targets.get(node, data[2]) in carried else 'new_source_campaign',
        tuple(sorted(documented_consumption.get(node, ()))), coverage_lots.get(node, ()))
        for node, data in sorted(records.items(), key=lambda pair: pair[1][0]))
    relation = tuple(sorted({(link.source_campaign_id, link.target_campaign_id)
                             for link in trade_bindings if link.supported_by_fifo
                             and link.source_campaign_id != link.target_campaign_id}))
    by_id = {campaign.campaign_id: campaign for campaign in campaigns}
    grouped = {}; origins = {}
    for node, data in sorted(records.items(), key=lambda pair: pair[1][0]):
        target = targets.get(node, data[2])
        grouped.setdefault(target, []).append(data[3])
        origins.setdefault(target, []).append(by_id[data[2]])
    resolved = tuple(replace(origins[target][0], campaign_id=target, trades=tuple(trades),
                             started_before_data=target in carried or any(c.started_before_data for c in origins[target]))
                     for target, trades in grouped.items())
    return CampaignContinuityPlan(resolved, relation, tuple(sorted(findings)), trade_bindings)
