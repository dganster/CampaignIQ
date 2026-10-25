"""Development-only, read-only ancestry replay and snapshot binding preview.

Diagnostic IDs are evidence anchors, NOT canonical campaign identities. Neither
quantity reconciliation nor these links authorize publication. Parsed API inputs
must come from trusted parsers; file hashes establish byte identity, not semantic
authenticity. The CLI supplies those inputs using existing broker parsers.
"""
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
import re
from pathlib import Path

from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_roll import detect_option_rolls
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.side import Side
from campaigniq.persistence.lot_book_store import serialize_lot_book, deserialize_lot_book


def _development(flag):
    if flag is not True or os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID'):
        raise ValueError('Explicit development-only execution outside Render is required.')


def _digest(book, day):
    return serialize_lot_book(period_end=day, lot_book=book)


def _quantities(book):
    result = {}
    ids = set()
    for instrument in book.instruments():
        lots = book.lots(instrument)
        signs = set()
        for lot in lots:
            if lot.lot_id in ids or not lot.quantity.is_finite() or lot.quantity == 0:
                raise ValueError('Invalid quantity or duplicate lot ID.')
            ids.add(lot.lot_id)
            signs.add(lot.quantity > 0)
        if len(signs) != 1:
            raise ValueError('Offsetting lots cannot establish ancestry by net quantity.')
        result[instrument] = sum((lot.quantity for lot in lots), Decimal(0))
    return result


def _receipts(paths):
    paths = tuple(Path(p).resolve() for p in paths)
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('Distinct source evidence files are required.')
    return tuple((str(p), sha256(p.read_bytes()).hexdigest()) for p in paths)


@dataclass(frozen=True)
class AncestryAudit:
    """An in-memory diagnostic; no database/storage writes are implemented."""
    _ending: str
    evidence_sha256: tuple[tuple[str, str], ...]
    origins: tuple[tuple[str, str, str], ...]
    transitions: tuple[tuple[str, str, str], ...]
    scope: tuple[str, ...]
    publication_authorized: bool = field(default=False, init=False)

    def preview_bindings(self, snapshot_book, *, development_only=False):
        """Return a separate clone, never overwrite known campaign identities.

        Snapshot lots retain ID, basis, dates and quantities. The complete target
        scope must match; multiple ancestry IDs for one contract are ambiguous.
        These provisional links cannot be persisted through this module.
        """
        _development(development_only)
        if self.evidence_sha256 != _receipts(p for p, _ in self.evidence_sha256):
            raise ValueError('Source evidence changed after ancestry replay.')
        ending = deserialize_lot_book(self._ending)
        scope = set(self.scope)
        source_quantities = _quantities(snapshot_book)
        target_quantities = {i: q for i, q in source_quantities.items()
                             if underlying_symbol(i) in scope}
        if target_quantities != _quantities(ending.lot_book):
            raise ValueError('Complete target closing inventory does not match ancestry replay.')
        preview = snapshot_book.clone()
        bindings = []
        for instrument in target_quantities:
            cids = {l.campaign_id for l in ending.lot_book.lots(instrument)}
            if len(cids) != 1 or None in cids:
                raise ValueError('Ambiguous ancestry for target contract.')
            cid = next(iter(cids))
            for lot in snapshot_book.lots(instrument):
                if lot.campaign_id is not None:
                    raise ValueError('Refusing to replace an existing campaign identity.')
                preview.assign_campaign(lot.lot_id, cid)
                bindings.append((lot.lot_id, cid))
        return preview, tuple(sorted(bindings))


def replay_ancestry(*, opening_lot_book, position_history, closing_lot_book,
                    period_start, period_end, evidence_paths, development_only=False):
    """Replay a targeted history with conservative, provisional identity links.

    Same-trade option rolls inherit actual FIFO closing provenance. An opening
    covered call may inherit exactly one sufficiently large equity holding's
    identity. Assignment links require the exact option/share ratio and a single
    known consumed identity. Flat reentry creates a new diagnostic anchor.
    Unsupported/mixed activity fails closed; original books are never mutated.
    """
    _development(development_only)
    if not isinstance(period_start, date) or not isinstance(period_end, date) or period_start > period_end:
        raise ValueError('A valid explicit replay period is required.')
    receipts = _receipts(evidence_paths)
    opening_before = _digest(opening_lot_book, period_start)
    closing_before = _digest(closing_lot_book, period_end)
    _quantities(opening_lot_book)
    expected = _quantities(closing_lot_book)
    scope = {underlying_symbol(i) for i in (*opening_lot_book.instruments(), *closing_lot_book.instruments())}
    if not scope:
        raise ValueError('An explicit nonempty inventory scope is required.')
    book = opening_lot_book.clone()
    origins, transitions = [], []
    sequence = 0

    def new_id(symbol, when, kind):
        nonlocal sequence
        sequence += 1
        cid = f'DIAGNOSTIC-ANCESTRY:{symbol}:{when.isoformat()}:{sequence}'
        origins.append((cid, kind, when.isoformat()))
        return cid

    for instrument in book.instruments():
        for lot in book.lots(instrument):
            if lot.campaign_id is not None:
                raise ValueError('This diagnostic requires unassigned boundary lots; known IDs are preserved elsewhere.')
            if lot.opened_at.date() >= period_start:
                raise ValueError('Opening boundary lots must precede the replay period.')
            cid = new_id(underlying_symbol(instrument), lot.opened_at, 'started_before_data')
            book.assign_campaign(lot.lot_id, cid)
    seen = set()
    items = position_history.items()
    by_time = {}
    for item in items:
        by_time.setdefault(item.occurred_at, []).append(item)
    for group in by_time.values():
        if len(group) > 1:
            # Identical-contract split fills commute. Other ties require broker
            # ordering evidence; never guess an assignment/open/close sequence.
            pairs = []
            for item in group:
                rolls = detect_option_rolls(item.trade) if item.trade is not None else ()
                if len(rolls) != 1 or len(item.trade.legs) != 2:
                    raise ValueError('Equal timestamps require exact split-roll evidence.')
                roll = rolls[0]
                pairs.append((roll.closed_contract, roll.opened_contract,
                              roll.closed_leg.side, roll.opened_leg.side))
            if len(set(pairs)) != 1:
                raise ValueError('Equal timestamps contain different roll contracts.')
    for item in items:
        if not period_start <= item.occurred_at.date() <= period_end:
            raise ValueError('History item is outside the replay period.')
        obj = item.trade if item.trade is not None else item.event
        # Equal broker fills may be distinct executions, so compare object
        # identity, not economic equality. Source completeness remains separate.
        fingerprint = id(obj)
        if fingerprint in seen:
            raise ValueError('Duplicate history item.')
        seen.add(fingerprint)
        if item.trade is not None:
            trade = item.trade
            legs = trade.legs
            if not legs or len({underlying_symbol(l.instrument) for l in legs}) != 1:
                raise ValueError('Mixed or empty trade cannot establish ancestry.')
            symbol = underlying_symbol(legs[0].instrument)
            if symbol not in scope:
                raise ValueError('History underlying is outside the target inventory scope.')
            for leg in legs:
                if (not leg.executions or any(not e.quantity.is_finite() or e.quantity == 0
                        or e.executed_at != item.occurred_at for e in leg.executions)):
                    raise ValueError('Trade executions require nonzero quantities and one explicit timestamp.')
                if leg.position_effect not in {PositionEffect.OPEN, PositionEffect.CLOSE}:
                    raise ValueError('Unsupported position effect.')
                if leg.side not in {Side.BUY, Side.SELL}:
                    raise ValueError('Unsupported trade side.')
            opens = [l for l in legs if l.position_effect is PositionEffect.OPEN]
            closes = [l for l in legs if l.position_effect is PositionEffect.CLOSE]
            # Preview closes in broker leg order; no opening may cover an overclose.
            probe = book.clone()
            allocations = []
            for leg in closes:
                allocations.extend(probe.apply_trade(replace(trade, legs=(leg,))))
            cids = {a.campaign_id for a in allocations}
            if None in cids or len(cids) > 1:
                raise ValueError('A close would consume unknown or multiple ancestry identities.')
            if opens and closes:
                rolls = detect_option_rolls(trade)
                if len(rolls) != 1 or len(opens) != 1 or len(closes) != 1:
                    raise ValueError('Only an exact same-trade option roll may transfer closing ancestry.')
                cid = next(iter(cids))
                kind = 'option_roll'
            elif opens:
                if len(opens) != 1:
                    raise ValueError('Multiple new opening legs require separate ancestry evidence.')
                leg = opens[0]
                quantity = sum((abs(e.quantity) for e in leg.executions), Decimal(0))
                equities = [l for i in book.instruments() if not isinstance(i, OptionContract)
                            and underlying_symbol(i) == symbol for l in book.lots(i)]
                if (isinstance(leg.instrument, OptionContract)
                        and leg.instrument.option_type is OptionType.CALL
                        and leg.side is Side.SELL and equities):
                    equity_ids = {l.campaign_id for l in equities}
                    if (len(equity_ids) != 1 or None in equity_ids
                            or sum((l.quantity for l in equities), Decimal(0)) < quantity * 100):
                        raise ValueError('Covered-call equity ancestry is ambiguous or insufficient.')
                    # Existing calls also consume cover; never over-allocate shares.
                    covered = sum((abs(l.quantity) for i in book.instruments()
                                   if isinstance(i, OptionContract) and i.underlying == symbol
                                   and i.option_type is OptionType.CALL for l in book.lots(i)
                                   if l.quantity < 0), Decimal(0))
                    if sum((l.quantity for l in equities), Decimal(0)) < (covered + quantity) * 100:
                        raise ValueError('Insufficient uncommitted covered-call shares.')
                    cid = next(iter(equity_ids))
                    kind = 'covered_call'
                else:
                    if any(underlying_symbol(i) == symbol for i in book.instruments()):
                        raise ValueError('New exposure alongside existing positions needs explicit ancestry evidence.')
                    cid = new_id(symbol, item.occurred_at, 'observed_open')
                    kind = 'observed_open'
            else:
                cid = next(iter(cids))
                kind = 'close'
            book.apply_trade(trade, campaign_id=cid)
        else:
            event = item.event
            if event is None or event.kind not in {PositionEventKind.ASSIGNMENT, PositionEventKind.EXPIRATION}:
                raise ValueError('Unsupported ancestry event.')
            options = [c for c in event.changes if isinstance(c.instrument, OptionContract)]
            if len(options) != 1 or any(underlying_symbol(c.instrument) not in scope for c in event.changes):
                raise ValueError('Event requires exactly one option within the target scope.')
            option = options[0]
            if not option.quantity.is_finite() or option.quantity == 0:
                raise ValueError('Invalid event quantity.')
            probe = book.clone()
            available = sum((abs(l.quantity) for l in probe.lots(option.instrument)
                             if l.quantity * option.quantity < 0), Decimal(0))
            if available < abs(option.quantity):
                raise ValueError('Event lacks matching option inventory.')
            allocations = probe.apply_signed_change(instrument=option.instrument,
                quantity=option.quantity, occurred_at=event.occurred_at)
            cids = {a.campaign_id for a in allocations}
            if len(cids) != 1 or None in cids:
                raise ValueError('Event consumes ambiguous ancestry.')
            cid = next(iter(cids))
            if event.kind is PositionEventKind.ASSIGNMENT:
                # This preview supports short options with standard 100-share deliverables.
                shares = [c for c in event.changes if not isinstance(c.instrument, OptionContract)]
                expected_shares = option.quantity * (100 if option.instrument.option_type is OptionType.PUT else -100)
                if (option.quantity <= 0 or len(event.changes) != 2 or len(shares) != 1
                        or event.changes[0] is not option
                        or underlying_symbol(shares[0].instrument) != option.instrument.underlying
                        or shares[0].quantity != expected_shares):
                    raise ValueError('Assignment requires ordered short-option/standard-share evidence.')
                if expected_shares < 0:
                    equity_lots = book.lots(shares[0].instrument)
                    if (sum((l.quantity for l in equity_lots), Decimal(0)) < abs(expected_shares)
                            or any(l.campaign_id != cid for l in equity_lots)):
                        raise ValueError('Call assignment lacks matching equity ancestry.')
            elif len(event.changes) != 1:
                raise ValueError('Expiration must contain only the consumed option.')
            LotBookPeriodApplier()._apply_event(book, event)
            kind = event.kind.value.lower()
        transitions.append((item.occurred_at.isoformat(), kind, cid))
    if not transitions or _quantities(book) != expected:
        raise ValueError('Ancestry replay does not reconcile the complete target closing inventory.')
    if receipts != _receipts(p for p, _ in receipts):
        raise ValueError('Source evidence changed during ancestry replay.')
    if opening_before != _digest(opening_lot_book, period_start) or closing_before != _digest(closing_lot_book, period_end):
        raise ValueError('Input lot state changed during ancestry replay.')
    return AncestryAudit(_digest(book, period_end), receipts, tuple(origins), tuple(transitions), tuple(sorted(scope)))


def _validate_history_header(header, account, month):
    match = re.search(r'Account Statement for (\d{8})SCHW.* since (\d+/\d+/\d+) through (\d+/\d+/\d+)', header)
    if not match or match.group(1) != account:
        raise ValueError('Trade history account does not match the statements.')
    first, last = (datetime.strptime(s, '%m/%d/%y').date() for s in match.groups()[1:])
    start, end = date(2025, month, 1), date(2025, month + 1, 1) - date.resolution
    if first != start - date.resolution or last not in {end, end - date.resolution}:
        raise ValueError('Trade history header does not match its monthly evidence role.')


def main():
    """Read the ten explicitly supplied broker files; print a diagnostic JSON."""
    import argparse
    import subprocess
    import tempfile
    from campaigniq.domain.lot_book import LotBook
    from campaigniq.domain.position_history import PositionHistory
    from campaigniq.importers.schwab.position_snapshot import to_lots
    from campaigniq.importers.schwab.position_snapshot_reader import read_position_snapshot_section
    from campaigniq.importers.schwab.option_assignment_flow import read_option_assignment_events
    from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
    from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
    from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-only', action='store_true', required=True)
    parser.add_argument('--evidence-manifest', type=Path, required=True,
                        help='JSON mapping opening/february/march/closing and 2025-01 through 2025-06 to file paths')
    args = parser.parse_args()
    try:
        _development(args.development_only)
        manifest = json.loads(args.evidence_manifest.read_text())
        keys = {'opening', 'february', 'march', 'closing', *(f'2025-{m:02}' for m in range(1, 7))}
        if not isinstance(manifest, dict) or set(manifest) != keys or any(not isinstance(v, str) for v in manifest.values()):
            raise ValueError('Exactly four statements and six monthly histories are required.')
        paths = {k: (args.evidence_manifest.resolve().parent / v).resolve() for k, v in manifest.items()}
        initial_receipts = _receipts(paths.values())
        texts = {}
        with tempfile.TemporaryDirectory(prefix='campaigniq-ancestry-') as temporary:
            for key, period in [('opening', 'December 1-31, 2024'), ('february', 'February 1-28, 2025'),
                                ('march', 'March 1-31, 2025'), ('closing', 'June 1-30, 2025')]:
                output = Path(temporary) / (key + '.txt')
                subprocess.run(['pdftotext', '-layout', str(paths[key]), str(output)], check=True, capture_output=True)
                texts[key] = output.read_text()
                if period not in texts[key].split('\f')[0]:
                    raise ValueError('Statement period does not match its evidence role.')
        accounts = []
        for text in texts.values():
            match = re.search(r'\b(\d{4})-(\d{4})\b', text.split('\f')[0])
            if not match:
                raise ValueError('Statement account identity could not be verified.')
            accounts.append(''.join(match.groups()))
        reader = ThinkorswimTradeReader(ThinkorswimSourceReader(), ThinkorswimTradeHistoryReader())
        history = PositionHistory()
        for month in range(1, 7):
            path = paths[f'2025-{month:02}']
            header = path.read_text(encoding='utf-8-sig').splitlines()[0]
            _validate_history_header(header, accounts[0], month)
            start = date(2025, month, 1)
            end = date(2025, month + 1, 1) - date.resolution
            # The broker header range may differ from its actual filled rows.
            for trade in reader.read(str(path), start=start, end=end):
                symbols = {underlying_symbol(l.instrument) for l in trade.legs}
                if symbols & {'ISRG', 'URI'}:
                    if len(symbols) != 1:
                        raise ValueError('Mixed target/non-target trade.')
                    history.add_trade(trade)
        if len(set(accounts)) != 1:
            raise ValueError('Statements belong to different accounts.')
        for key in ('february', 'march'):
            for event in read_option_assignment_events(texts[key].splitlines()):
                if any(underlying_symbol(c.instrument) in {'ISRG', 'URI'} for c in event.changes):
                    history.add_event(event)
        books = []
        for key, when in [('opening', datetime(2024, 12, 31, 23, 59, 59)), ('closing', datetime(2025, 6, 30, 23, 59, 59))]:
            book = LotBook()
            rows = [r for r in read_position_snapshot_section(texts[key].splitlines(), snapshot_at=when)
                    if r.symbol in {'ISRG', 'URI'}]
            for lot in to_lots(rows):
                book.seed(lot)
            books.append(book)
        if initial_receipts != _receipts(paths.values()):
            raise ValueError('Evidence changed while parsing.')
        audit = replay_ancestry(opening_lot_book=books[0], position_history=history,
            closing_lot_book=books[1], period_start=date(2025, 1, 1), period_end=date(2025, 6, 30),
            evidence_paths=paths.values(), development_only=True)
        _, bindings = audit.preview_bindings(books[1], development_only=True)
        print(json.dumps({'targeted_inventory_reconciled': True, 'diagnostic_only': True,
            'origins': audit.origins, 'transitions': audit.transitions, 'preview_bindings': bindings,
            'evidence_sha256': audit.evidence_sha256, 'publication_authorized': False}, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': type(error).__name__, 'message': str(error), 'publication_authorized': False}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
