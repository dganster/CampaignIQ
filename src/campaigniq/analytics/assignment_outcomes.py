"""Conservative assignment delivery outcomes from resolved journal lots."""
from datetime import datetime
from decimal import Decimal
from campaigniq.persistence.lot_book_store import _deserialize_instrument
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.ui.roll_evidence import closed_side_from_journal

VERIFIED = {'Published position journal', 'Verified retained trade history'}
CLOSES = {'Close / reduce', 'Roll: close', 'Assignment', 'Exercise', 'Expiration'}
ZERO = Decimal('0')


def assignment_outcomes(entries, selected_entries, attributions=()):
    """Link only unique standard-option deliveries; never pair by nearby dates.

    Subsequent closes follow acquired lots through explicit carried opening
    references. Broker results require complete matching consumed allocations.
    Cash at strike excludes premiums/fees and is not profit or adjusted basis.
    """
    entries = tuple(r for r in entries if r.get('source') in VERIFIED)
    selected = {id(r) for r in selected_entries}
    results = []
    for option_row in entries:
        contract = _deserialize_instrument(option_row['instrument'])
        if (option_row['action'] != 'Assignment'
                or not isinstance(contract, OptionContract)):
            continue
        quantity = Decimal(option_row['quantity_change'])
        if quantity <= ZERO or quantity != quantity.to_integral_value() or contract.underlying.startswith('/'):
            continue
        at = option_row['occurred_at']
        shares = quantity * 100 * (1 if contract.option_type is OptionType.PUT else -1)
        same_event = [r for r in entries if r['action'] == 'Assignment'
                      and r['occurred_at'] == at and r['period_start'] == option_row['period_start']
                      and underlying_symbol(_deserialize_instrument(r['instrument'])) == contract.underlying]
        option_legs = [r for r in same_event if isinstance(_deserialize_instrument(r['instrument']), OptionContract)]
        stocks = [r for r in same_event if not isinstance(_deserialize_instrument(r['instrument']), OptionContract)
                  and Decimal(r['quantity_change']) == shares and r['lots']]
        if id(option_row) not in selected and not any(id(r) in selected for r in stocks):
            continue
        stock = stocks[0] if len(stocks) == 1 and len(option_legs) == 1 else None
        row = dict(date=datetime.fromisoformat(at).date(), contract=contract, contracts=quantity,
                   shares=shares, strike_cash=-shares * contract.strike, linked=stock is not None,
                   remaining=None, closed_shares=ZERO, realized=None, broker_records=0)
        if stock is not None:
            after = Decimal(stock['position_after']) if stock.get('position_after') is not None else None
            if after is None or after < ZERO or after - shares < ZERO:
                row['linked'] = False
                results.append(row)
                continue
            affected = {lot['lot_id'] for lot in stock['lots']}
            if sum((Decimal(lot['quantity']) for lot in stock['lots']), ZERO) != abs(shares):
                row['linked'] = False
                results.append(row)
                continue
            if shares < ZERO:
                closes = [stock]
            else:
                cid = stock['campaign_id']
                closes = []
                uncertain = False
                for candidate in entries:
                    if (candidate['action'] not in CLOSES or candidate['occurred_at'] <= at
                            or candidate['campaign_id'] != cid or candidate['instrument'] != stock['instrument']
                            or Decimal(candidate['quantity_change']) >= ZERO):
                        continue
                    proven = candidate['period_start'] == stock['period_start']
                    if not proven and cid and cid[:7].count('-') == 1 and ':' in cid:
                        carry = [r for r in entries if r['action'] == 'Carried position'
                                 and r['period_start'] == candidate['period_start'] and r['campaign_id'] == cid
                                 and r['instrument'] == stock['instrument']]
                        refs = {(ref['lot_id'], ref['opened_at']) for r in carry for ref in r.get('opening_refs', ())}
                        proven = all((lot['lot_id'], at) in refs for lot in candidate['lots'] if lot['lot_id'] in affected)
                    touches = any(lot['lot_id'] in affected for lot in candidate['lots'])
                    if proven and touches:
                        closes.append(candidate)
                    elif touches:
                        uncertain = True
                row['closed_shares'] = sum((Decimal(lot['quantity']) for r in closes for lot in r['lots'] if lot['lot_id'] in affected), ZERO)
                if row['closed_shares'] <= shares and not uncertain:
                    row['remaining'] = shares - row['closed_shares']
                else:
                    row['linked'] = False
                    closes = []
            # Whole-record P&L only when every consumed allocation belongs to
            # these delivery lots and its close is supported by these rows.
            matched = []
            for attribution in attributions:
                if attribution.record.instrument != _deserialize_instrument(stock['instrument']):
                    continue
                if not attribution.allocations or not all(a.lot_id in affected for a in attribution.allocations):
                    continue
                if closed_side_from_journal(attribution, closes) != 'Unavailable':
                    matched.append(attribution)
            row['broker_records'] = len(matched)
            if matched:
                row['realized'] = sum((a.record.gain_loss for a in matched), ZERO)
        results.append(row)
    return tuple(sorted(results, key=lambda r: (r['date'], str(r['contract']))))
