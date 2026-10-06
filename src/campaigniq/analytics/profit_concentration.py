"""Profit concentration from reconciled, unambiguous equity/option closes."""
from dataclasses import dataclass
from decimal import Decimal
import re
from urllib.parse import quote
from campaigniq.domain.campaign_identity import underlying_symbol

ZERO = Decimal('0')


@dataclass(frozen=True, slots=True)
class ConcentrationCampaign:
    campaign_id: str
    underlying: str
    realized_pnl: Decimal
    drilldown_id: str


@dataclass(frozen=True, slots=True)
class ProfitConcentration:
    campaigns: tuple[ConcentrationCampaign, ...]
    gross_profits: Decimal
    gross_losses: Decimal
    net_realized: Decimal
    excluded_records: int
    excluded_pnl: Decimal

    @property
    def winners(self):
        return tuple(sorted((r for r in self.campaigns if r.realized_pnl > ZERO),
                            key=lambda r: (-r.realized_pnl, r.underlying, r.campaign_id)))

    @property
    def losers(self):
        return tuple(sorted((r for r in self.campaigns if r.realized_pnl < ZERO),
                            key=lambda r: (r.realized_pnl, r.underlying, r.campaign_id)))

    def top(self, count):
        winners = self.winners[:count]
        pnl = sum((r.realized_pnl for r in winners), ZERO)
        share = pnl / self.gross_profits if self.gross_profits else None
        return len(winners), pnl, share, self.net_realized - pnl


def summarize_profit_concentration(monthly):
    """Combine immutable campaign ancestry; keep legacy local IDs month-scoped.

    Positive and negative amounts are classified AFTER aggregating campaign
    closes within the selected months. Gross profit shares use winning campaign
    P&L, never net P&L as their denominator. No record is split or duplicated.
    """
    totals = {}; links = {}; excluded = 0; excluded_pnl = ZERO
    identities = {}
    for (start, end), records in monthly.items():
        for attribution in records:
            if start <= attribution.record.closed_date <= end:
                for allocation in attribution.allocations:
                    if allocation.campaign_id:
                        identities.setdefault((start, allocation.campaign_id), set()).add(underlying_symbol(attribution.record.instrument))
    for (start, end), records in sorted(monthly.items()):
        for attribution in records:
            record = attribution.record
            if not start <= record.closed_date <= end:
                continue
            symbol = underlying_symbol(record.instrument)
            cids = attribution.campaign_ids
            if (attribution.has_unassigned_campaign_allocation or len(cids) != 1
                    or not attribution.basis_reconciled or not attribution.gain_loss_reconciled
                    or symbol.startswith('/')):
                excluded += 1; excluded_pnl += record.gain_loss
                continue
            cid = cids[0]
            identity = re.match(r'^\d{4}-\d{2}:([^:]+):', cid)
            if identity and identity[1] != quote(symbol, safe=''):
                excluded += 1; excluded_pnl += record.gain_loss
                continue
            key = (symbol, cid if identity else f'{start:%Y-%m}/{cid}')
            totals[key] = totals.get(key, ZERO) + record.gain_loss
            suffix = '@'+quote(symbol, safe='') if len(identities[(start,cid)]) > 1 else ''
            links[key] = f'{start:%Y-%m}/{cid}{suffix}'
    campaigns = tuple(ConcentrationCampaign(cid, symbol, pnl, links[(symbol,cid)])
                      for (symbol,cid),pnl in sorted(totals.items()))
    profits = sum((r.realized_pnl for r in campaigns if r.realized_pnl > ZERO), ZERO)
    losses = sum((-r.realized_pnl for r in campaigns if r.realized_pnl < ZERO), ZERO)
    return ProfitConcentration(campaigns, profits, losses, profits-losses, excluded, excluded_pnl)


def underlying_contributions(summary):
    totals = {}
    for campaign in summary.campaigns:
        row = totals.setdefault(campaign.underlying, dict(profits=ZERO, losses=ZERO, campaigns=0))
        row['campaigns'] += 1
        if campaign.realized_pnl > ZERO:
            row['profits'] += campaign.realized_pnl
        else:
            row['losses'] -= campaign.realized_pnl
    return tuple(dict(underlying=symbol, **row, net=row['profits']-row['losses'])
                 for symbol,row in sorted(totals.items(),key=lambda item:(-(item[1]['profits']-item[1]['losses']),item[0])))
