"""Keep campaign identity distinct from a month-local reconstruction number."""
from dataclasses import replace
import re
from urllib.parse import quote
from campaigniq.domain.lot_book import LotBook


def underlying_symbol(instrument) -> str:
    return str(getattr(instrument, 'underlying', None) or instrument.symbol).upper()


def scope_import_campaigns(campaigns, namespace: str):
    """Use import month, underlying, and local number for newly opened lots."""
    result = []
    for campaign in campaigns:
        symbols = {underlying_symbol(leg.instrument)
                   for trade in campaign.trades for leg in trade.legs}
        if len(symbols) != 1:
            raise ValueError('A reconstructed campaign must contain one underlying.')
        symbol = quote(next(iter(symbols)), safe='')
        result.append(replace(campaign, campaign_id=f'{namespace}:{symbol}:{campaign.campaign_id}'))
    return tuple(result)


def scope_legacy_opening_lots(book: LotBook) -> LotBook:
    """Retain legacy ancestry without inventing its original import month."""
    result = book.clone()
    for instrument, lots in result._lots.items():
        symbol = quote(underlying_symbol(instrument), safe='')
        result._lots[instrument] = [
            replace(lot, campaign_id=f'LEGACY:{symbol}:{lot.campaign_id}')
            if lot.campaign_id and re.fullmatch(r'(?:FX-)?CAMP-\d+', lot.campaign_id)
            else lot for lot in lots
        ]
    return result


def separate_campaign_collisions(attributions):
    """Separate proven cross-underlying ID collisions in existing results.

    This is a read-only compatibility view. Broker facts, allocation quantities,
    basis, unknown provenance, and genuinely shared records are preserved.
    Uncollided IDs stay unchanged. No original import month is guessed.
    """
    items = tuple(attributions)
    symbols = {}
    for attribution in items:
        symbol = underlying_symbol(attribution.record.instrument)
        for allocation in attribution.allocations:
            if allocation.campaign_id:
                symbols.setdefault(allocation.campaign_id, set()).add(symbol)
    collisions = {cid for cid, values in symbols.items() if len(values) > 1}
    return tuple(
        replace(attribution, allocations=tuple(
            replace(allocation, campaign_id=(
                f'{allocation.campaign_id}@{quote(underlying_symbol(attribution.record.instrument), safe="")}'
                if allocation.campaign_id in collisions else allocation.campaign_id
            )) for allocation in attribution.allocations
        )) for attribution in items
    )
