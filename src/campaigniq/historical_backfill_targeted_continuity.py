"""Read-only development check of ISRG/URI ancestry through July-August 2025.

Broker snapshots verify quantities. Continuous and month-by-month replay verify
the same derived lot/campaign state. These diagnostic IDs and derived states are
not existing authoritative campaign records; full-portfolio continuity,
publication, cost basis, Forex and backup verification remain separate gates.
"""
from datetime import date, datetime
from pathlib import Path
import json
import re
import subprocess
import tempfile

from campaigniq.domain.campaign import Campaign
from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_history import PositionHistory
from campaigniq.historical_backfill_ancestry import (
    _development, _receipts, _validate_history_header, replay_ancestry,
)
from campaigniq.historical_backfill_replay import ReplayPeriod, audit_replay
from campaigniq.persistence.lot_book_store import deserialize_lot_book, serialize_lot_book

SCOPE = frozenset({'ISRG', 'URI'})
ENDS = (date(2025, 6, 30), date(2025, 7, 31), date(2025, 8, 31))
EVIDENCE_NAMES = {
    'opening': 'Brokerage Statement (20261018-184629).pdf',
    'february': 'Brokerage Statement(20261023-135014).PDF',
    'march': 'Brokerage Statement(20261023-135026).PDF',
    '2025-06-statement': 'Brokerage Statement(20261023-115949).PDF',
    '2025-07-statement': 'Brokerage Statement(20261023-115802).PDF',
    '2025-08-statement': 'Brokerage Statement(20261023-123321).PDF',
    **{f'2025-{m:02}-trades': f'Account Trade History(20261023-{suffix}).csv'
       for m, suffix in enumerate(['134755', '134701', '134608', '134528',
                                  '134425', '115954', '122405', '123326'], 1)},
}


def _history(items):
    history = PositionHistory()
    for item in items:
        if item.trade is not None:
            history.add_trade(item.trade)
        else:
            history.add_event(item.event)
    return history


def verify_targeted_continuity(*, opening_lot_book, position_history,
        closing_lot_books, evidence_paths, development_only=False):
    """Verify each snapshot and a separate sequential replay, without writes.

    The parsed-input API trusts its caller's source-to-domain parsing. The CLI
    below enforces the concrete file roles and account identity. Every evidence
    file is rehashed before and after the complete check.
    """
    _development(development_only)
    if set(closing_lot_books) != set(ENDS):
        raise ValueError('Exactly June, July and August closing snapshots are required.')
    paths = tuple(evidence_paths)
    receipts = _receipts(paths)
    all_books = (opening_lot_book, *(closing_lot_books[e] for e in ENDS))
    if any(underlying_symbol(i) not in SCOPE for b in all_books for i in b.instruments()):
        raise ValueError('Only ISRG and URI inventories are supported by this targeted check.')
    before = tuple(serialize_lot_book(period_end=ENDS[0], lot_book=b) for b in all_books)
    items = position_history.items()
    if any(not date(2025, 1, 1) <= item.occurred_at.date() <= ENDS[-1] for item in items):
        raise ValueError('Targeted history must be within January-August 2025.')
    for item in items:
        instruments = ([leg.instrument for leg in item.trade.legs] if item.trade is not None
                       else [change.instrument for change in item.event.changes])
        if not instruments or any(underlying_symbol(i) not in SCOPE for i in instruments):
            raise ValueError('History contains activity outside the target scope.')
    audits = {}
    histories = {}
    for end in ENDS:
        histories[end] = _history(item for item in items if item.occurred_at.date() <= end)
        audits[end] = replay_ancestry(opening_lot_book=opening_lot_book,
            position_history=histories[end], closing_lot_book=closing_lot_books[end],
            period_start=date(2025, 1, 1), period_end=end, evidence_paths=paths,
            development_only=True)
    rows = []
    previous = ENDS[0]
    for end in ENDS[1:]:
        # Trace entries come from the conservative ancestry replay, including
        # exact FIFO rolls, assignments and covered-share links. No symbol-only
        # campaign inference is performed here.
        full_items = histories[end].items()
        trace = audits[end].transitions
        if len(full_items) != len(trace):
            raise ValueError('Ancestry trace is incomplete.')
        if trace[:len(audits[previous].transitions)] != audits[previous].transitions:
            raise ValueError('Later replay changes earlier ancestry evidence.')
        owners = {}
        monthly_items = []
        for item, (timestamp, _, cid) in zip(full_items, trace):
            if timestamp != item.occurred_at.isoformat():
                raise ValueError('Ancestry trace is not aligned with history.')
            if item.occurred_at.date() <= previous:
                continue
            monthly_items.append(item)
            if item.trade is not None:
                owners.setdefault(cid, []).append(item.trade)
        campaigns = tuple(Campaign(cid, tuple(trades)) for cid, trades in owners.items())
        rows.append(ReplayPeriod(end, audits[previous]._ending, audits[end]._ending,
                                 _history(monthly_items), campaigns))
        previous = end
    continuity = audit_replay(workspace='diagnostic-only:ISRG-URI',
        target_period_end=ENDS[0], target_closing_text=audits[ENDS[0]]._ending,
        downstream_period_end=ENDS[-1], periods=rows)
    if not continuity['supplied_states_match_replay']:
        raise ValueError('Month-by-month replay differs from continuous ancestry replay: ' +
                         '; '.join(continuity['blocking_findings']))
    if receipts != _receipts(paths):
        raise ValueError('Evidence changed during continuity verification.')
    if before != tuple(serialize_lot_book(period_end=ENDS[0], lot_book=b) for b in all_books):
        raise ValueError('An input lot book changed during continuity verification.')
    month_summaries = []
    for end in ENDS:
        b = deserialize_lot_book(audits[end]._ending).lot_book
        month_summaries.append({'month': end.strftime('%Y-%m'),
            'target_inventory_reconciled': True,
            'cumulative_transitions': len(audits[end].transitions),
            'positions': [{'instrument': repr(i),
                           'quantity': str(sum(l.quantity for l in b.lots(i))),
                           'diagnostic_ancestry_ids': sorted({l.campaign_id for l in b.lots(i)})}
                          for i in b.instruments()]})
    return {'format': 'campaigniq.targeted_continuity', 'version': 1,
        'diagnostic_only': True, 'scope': sorted(SCOPE),
        'months': month_summaries,
        'july_august_exact_replay_match': True,
        'origins': audits[ENDS[-1]].origins,
        'state_input_sha256': continuity['state_input_sha256'],
        'evidence_sha256': receipts,
        'closing_state_source': 'Derived continuous replay; quantities checked against broker snapshots.',
        'existing_authoritative_campaigns_verified': False,
        'full_portfolio_continuity_verified': False,
        'forex_verified': False, 'publication_authorized': False}


def _load_evidence(folder):
    from campaigniq.importers.schwab.position_snapshot import to_lots
    from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section
    from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
    from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
    from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
    from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
    paths = {k: (folder / name).resolve() for k, name in EVIDENCE_NAMES.items()}
    missing = [EVIDENCE_NAMES[k] for k, p in paths.items() if not p.is_file()]
    if missing:
        raise ValueError('Required evidence files are missing: ' + ', '.join(missing))
    receipts = _receipts(paths.values())
    periods = {'opening': 'December 1-31, 2024', 'february': 'February 1-28, 2025',
        'march': 'March 1-31, 2025', '2025-06-statement': 'June 1-30, 2025',
        '2025-07-statement': 'July 1-31, 2025', '2025-08-statement': 'August 1-31, 2025'}
    texts, accounts = {}, set()
    with tempfile.TemporaryDirectory(prefix='campaigniq-targeted-') as temporary:
        for key, period in periods.items():
            output = Path(temporary) / (key + '.txt')
            subprocess.run(['pdftotext', '-layout', str(paths[key]), str(output)], check=True, capture_output=True)
            text = output.read_text()
            first_page = text.split('\f')[0]
            if period not in first_page:
                raise ValueError('Statement period does not match its evidence role.')
            account = re.search(r'\b(\d{4})-(\d{4})\b', first_page)
            if not account:
                raise ValueError('Statement account identity could not be verified.')
            accounts.add(''.join(account.groups()))
            texts[key] = text.splitlines()
    if len(accounts) != 1:
        raise ValueError('Statements belong to different accounts.')
    account = next(iter(accounts))
    reader = ThinkorswimTradeReader(ThinkorswimSourceReader(), ThinkorswimTradeHistoryReader())
    history = PositionHistory()
    for month in range(1, 9):
        path = paths[f'2025-{month:02}-trades']
        _validate_history_header(path.read_text(encoding='utf-8-sig').splitlines()[0], account, month)
        start, end = date(2025, month, 1), date(2025, month + 1, 1) - date.resolution
        for trade in reader.read(str(path), start=start, end=end):
            symbols = {underlying_symbol(l.instrument) for l in trade.legs}
            if symbols & SCOPE:
                if not symbols <= SCOPE:
                    raise ValueError('Trade mixes target and non-target underlyings.')
                history.add_trade(trade)
    for key, lines in texts.items():
        if key == 'opening':
            continue
        for event in read_option_assignment_events(lines):
            symbols = {underlying_symbol(c.instrument) for c in event.changes}
            if symbols & SCOPE:
                if not symbols <= SCOPE:
                    raise ValueError('Assignment mixes target and non-target underlyings.')
                history.add_event(event)
    books = {}
    for key, end in [('opening', date(2024, 12, 31)),
                     *((f'{e:%Y-%m}-statement', e) for e in ENDS)]:
        book = LotBook()
        when = datetime.combine(end, datetime.max.time()).replace(microsecond=0)
        rows = [r for r in read_position_snapshot_section(texts[key], snapshot_at=when) if r.symbol in SCOPE]
        for lot in to_lots(rows):
            book.seed(lot)
        books[key] = book
    if receipts != _receipts(paths.values()):
        raise ValueError('Evidence changed while parsing.')
    return books['opening'], history, {e: books[f'{e:%Y-%m}-statement'] for e in ENDS}, tuple(paths.values())


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', action='store_true', required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        _development(args.development_only)
        opening, history, closings, paths = _load_evidence(args.evidence_dir.expanduser().resolve())
        result = verify_targeted_continuity(opening_lot_book=opening, position_history=history,
            closing_lot_books=closings, evidence_paths=paths, development_only=True)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error), 'publication_authorized': False}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
