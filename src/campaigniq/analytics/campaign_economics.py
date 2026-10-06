"""Broker-realized campaign results and separately labeled observed trading cash flows."""
from dataclasses import dataclass, replace
from decimal import Decimal
import re
from urllib.parse import quote
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.campaign_identity import separate_campaign_collisions, underlying_symbol

ZERO = Decimal('0')


@dataclass(frozen=True, slots=True)
class CampaignEconomics:
    stock_realized: Decimal
    option_realized: Decimal
    other_realized: Decimal
    total_realized: Decimal
    reported_proceeds: Decimal
    reported_basis: Decimal
    reported_adjustments: Decimal
    record_count: int
    reconciled: bool
    short_premiums_received: Decimal
    short_option_buybacks: Decimal
    long_option_purchases: Decimal
    long_option_sale_receipts: Decimal
    missing_option_trade_prices: int
    observed_option_trade_count: int
    carried_position_count: int

    @property
    def observed_option_cash_flow(self):
        return (self.short_premiums_received + self.long_option_sale_receipts
                - self.short_option_buybacks - self.long_option_purchases)


def campaign_record_scope(monthly_attributions, selected_campaign_id):
    """Extend only immutable import-scoped identities across published periods.

    Reporting-month qualification is kept for legacy local IDs; matching a
    local number alone cannot prove that two historical campaigns are one.
    Shared or unassigned broker closes stay excluded, as in the drilldown.
    """
    reporting_month, campaign_id = selected_campaign_id.split('/', 1)
    identity = re.match(r'^\d{4}-\d{2}:([^:]+):', campaign_id)
    lifetime = identity is not None
    qualified = []
    for (start, end), attributions in sorted(monthly_attributions.items()):
        for attribution in attributions:
            if not start <= attribution.record.closed_date <= end:
                continue
            qualified.append(replace(attribution, allocations=tuple(
                replace(a, campaign_id=f'{start:%Y-%m}/{a.campaign_id}' if a.campaign_id else None)
                for a in attribution.allocations)))
    result = []
    for attribution in separate_campaign_collisions(qualified):
        if attribution.has_unassigned_campaign_allocation or len(attribution.campaign_ids) != 1:
            continue
        month, cid = attribution.campaign_ids[0].split('/', 1)
        if lifetime and quote(underlying_symbol(attribution.record.instrument), safe='') != identity[1]:
            continue
        if cid == campaign_id and (lifetime or month == reporting_month):
            result.append(attribution)
    return tuple(sorted(result,key=lambda a:a.record.closed_date)), lifetime


def summarize_campaign_economics(attributions, entries=()):
    totals = {'stock': ZERO, 'option': ZERO, 'other': ZERO}
    proceeds = basis = adjustments = ZERO
    records = tuple(attributions)
    for attribution in records:
        record = attribution.record
        kind = ('option' if isinstance(record.instrument, OptionContract) else
                'other' if '/' in underlying_symbol(record.instrument) else 'stock')
        totals[kind] += record.gain_loss
        proceeds += record.proceeds
        basis += record.cost_basis
        adjustments += record.disallowed_loss
    cash = {'short_open': ZERO, 'short_close': ZERO, 'long_open': ZERO, 'long_close': ZERO}
    entries = tuple(entries)
    openings = {(repr(row['instrument']), row.get('occurred_at')) for row in entries
                if row['action'] in {'Open / add', 'Roll: open'}}
    missing = observed = carried = 0
    for row in entries:
        if row['action'] == 'Carried position':
            if row['instrument'].get('type') != 'option':
                continue
            refs = row.get('opening_refs', [])
            refs = refs or ([dict(opened_at=row['opened_at'])] if row.get('opened_at') else [])
            if not refs or not all((repr(row['instrument']), ref['opened_at']) in openings for ref in refs):
                carried += 1
            continue
        instrument = row['instrument']
        if instrument.get('type') != 'option':
            continue
        action = row['action']
        if action not in {'Open / add', 'Roll: open', 'Close / reduce', 'Roll: close'}:
            continue
        if row['price'] is None or instrument.get('underlying','').startswith('/'):
            missing += 1
            continue
        quantity, price = Decimal(row['quantity_change']), Decimal(row['price'])
        if not quantity.is_finite() or not price.is_finite() or price < ZERO:
            raise ValueError('Invalid option trade cash-flow evidence.')
        opened = action in {'Open / add', 'Roll: open'}
        category = ('short_open' if quantity < ZERO else 'long_open') if opened else ('short_close' if quantity > ZERO else 'long_close')
        cash[category] += abs(quantity) * price * Decimal('100')
        observed += 1
    return CampaignEconomics(
        stock_realized=totals['stock'], option_realized=totals['option'], other_realized=totals['other'],
        total_realized=sum(totals.values(),ZERO), reported_proceeds=proceeds, reported_basis=basis,
        reported_adjustments=adjustments, record_count=len(records),
        reconciled=all(a.basis_reconciled and a.gain_loss_reconciled for a in records),
        short_premiums_received=cash['short_open'], short_option_buybacks=cash['short_close'],
        long_option_purchases=cash['long_open'], long_option_sale_receipts=cash['long_close'],
        missing_option_trade_prices=missing, observed_option_trade_count=observed, carried_position_count=carried)
