"""Read-only position history from published journals and retained source evidence."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from campaigniq.domain.campaign_identity import underlying_symbol
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.position_history import PositionHistory
from campaigniq.import_pipeline import PeriodImportPipeline
from campaigniq.importers.thinkorswim.trade_reader import ThinkorswimTradeReader
from campaigniq.importers.thinkorswim.trade_history_reader import ThinkorswimTradeHistoryReader
from campaigniq.sources.thinkorswim.source_reader import ThinkorswimSourceReader
from campaigniq.persistence.authoritative_lot_state import load_preceding_authoritative_state_from_storage
from campaigniq.persistence.lot_book_store import load_lot_book_from_storage, _deserialize_instrument, _serialize_instrument
from campaigniq.persistence.monthly_publication import is_month_published_in_storage
from campaigniq.persistence.position_journal import capture_position_journal, deserialize_position_journal, lot_signature
from campaigniq.ui.campaign_interactions import _next_weekday


def _close_totals(entries):
    totals = defaultdict(Decimal)
    for row in entries:
        if row["action"] not in {"Close / reduce", "Roll: close", "Assignment", "Expiration", "Exercise"}:
            continue
        instrument = _deserialize_instrument(row["instrument"])
        for lot in row["lots"]:
            totals[(repr(instrument), lot["lot_id"], row["campaign_id"])] += Decimal(lot["quantity"])
    return totals


def _published_totals(attributions):
    totals = defaultdict(Decimal)
    for attribution in attributions:
        for allocation in attribution.allocations:
            totals[(repr(attribution.record.instrument), allocation.lot_id, allocation.campaign_id)] += allocation.quantity
    return totals



def _source_matches_manifest(storage, root, start, end):
    key = f"{end:%Y-%m}-import-provenance.json"
    if not storage.exists(key):
        return True
    manifest = json.loads(storage.read_text(key))
    if (manifest.get("format") != "campaigniq.monthly_import_provenance" or manifest.get("version") != 1
            or manifest.get("period_start") != start.isoformat() or manifest.get("period_end") != end.isoformat()):
        return False
    from campaigniq.import_contract import MonthlyInputRole
    matches = [item for item in manifest["inputs"]
               if item["role"] == MonthlyInputRole.THINKORSWIM_TRADE_HISTORY.value]
    if len(matches) != 1:
        return False
    path = Path(root) / f"Account Trade History {start:%B %Y}.csv"
    contents = path.read_bytes()
    return len(contents) == matches[0]["byte_size"] and sha256(contents).hexdigest() == matches[0]["sha256"]

def _reconstruct_period(storage, root, start, end, attributions, transitions):
    """Accept old-source replay only when both saved ending lots and closes agree."""
    predecessor = load_preceding_authoritative_state_from_storage(storage, period_start=start)
    ending = load_lot_book_from_storage(storage, f"{end:%Y-%m}-lot-book.json")
    if ending.period_end != end:
        raise ValueError("Saved lot period does not match its filename.")
    path = Path(root) / f"Account Trade History {start:%B %Y}.csv"
    if not _source_matches_manifest(storage, root, start, end):
        return None
    pipeline = PeriodImportPipeline()
    # Imports before campaign scoping used local IDs; imports after it used a
    # month namespace. Neither scheme is inferred from a coincidentally equal ID.
    for namespace in (f"{start:%Y-%m}", None):
        try:
            result = pipeline.run(period_start=start, period_end=end,
                                  thinkorswim_trade_history=path,
                                  carried_opening_lot_book=predecessor[2] if predecessor else None,
                                  historical_source_root=root, campaign_namespace=namespace)
            # Assignment evidence is retained in the published lifecycle store.
            history = PositionHistory()
            for trade in result.trades:
                history.add_trade(trade)
            events = list(result.position_events)
            for transition in transitions:
                event = transition.position_event
                if event is not None and start <= event.occurred_at.date() <= end and event not in events:
                    events.append(event)
            for event in events:
                history.add_event(event)
            from dataclasses import replace
            from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
            result = replace(result, position_history=history,
                             ending_lot_book=LotBookPeriodApplier().apply(
                                 opening_lot_book=result.opening_lot_book, position_history=history,
                                 campaigns=result.campaigns))
            if lot_signature(result.ending_lot_book) != lot_signature(ending.lot_book):
                continue
            entries = capture_position_journal(result, start)
            # Unreported closes (including non-realized expirations) may exist;
            # every reported consumed lot must nevertheless match exactly.
            observed = _close_totals(entries)
            expected = _published_totals(attributions)
            if any(observed.get(key) != value for key, value in expected.items()):
                continue
            return entries
        except (ValueError, KeyError, FileNotFoundError, TypeError):
            continue
    return None


def _raw_entries(root, start, end):
    reader = ThinkorswimTradeReader(ThinkorswimSourceReader(), ThinkorswimTradeHistoryReader())
    trades = reader.read(Path(root) / f"Account Trade History {start:%B %Y}.csv", start=start, end=end)
    rows = []
    for trade in trades:
        rolling = {leg.position_effect for leg in trade.legs} == {PositionEffect.OPEN, PositionEffect.CLOSE}
        for leg in trade.legs:
            quantity = sum((abs(e.quantity) for e in leg.executions), Decimal("0"))
            if not quantity:
                continue
            opened = leg.position_effect is PositionEffect.OPEN
            action = ("Roll: open" if opened else "Roll: close") if rolling else ("Open / add" if opened else "Close / reduce")
            rows.append(dict(occurred_at=min(e.executed_at for e in leg.executions).isoformat(),
                             instrument=_serialize_instrument(leg.instrument), action=action,
                             quantity_change=str(quantity if leg.side.value == "BUY" else -quantity),
                             price=str(sum((abs(e.quantity) * e.execution_price for e in leg.executions), Decimal("0")) / quantity),
                             campaign_id=None, position_after=None, lots=[], fills=len(leg.executions), opened_at=None))
    return rows


def load_position_evidence(storage, root, monthly_attributions, lifecycle_history):
    """Return published history without rewriting any existing monthly artifacts."""
    entries, gaps = [], []
    for (start, end), attrs in sorted(monthly_attributions.items()):
        if not is_month_published_in_storage(storage, period_end=end):
            continue
        key = f"{end:%Y-%m}-position-journal.json"
        if storage.exists(key):
            try:
                rows = deserialize_position_journal(storage.read_text(key), period_start=start, period_end=end)
            except (ValueError, KeyError, TypeError):
                gaps.append(f"{start:%b %Y}: the saved position journal could not be read.")
                continue
            source = "Published position journal"
        else:
            try:
                rows = _reconstruct_period(storage, root, start, end, attrs, lifecycle_history.transitions)
            except (ValueError, KeyError, FileNotFoundError, TypeError):
                rows = None
            source = "Verified retained trade history"
            if rows is None:
                try:
                    rows = _raw_entries(root, start, end) if _source_matches_manifest(storage, root, start, end) else []
                except (ValueError, KeyError, FileNotFoundError, TypeError):
                    rows = []
                source = "Retained trades; campaign link unavailable"
                gaps.append(f"{start:%b %Y}: full campaign provenance could not be verified from retained evidence.")
        for row in rows:
            entries.append(dict(row, period_start=start.isoformat(), source=source))
    return entries, gaps


def evidence_fingerprint(storage, root, monthly_attributions):
    """Invalidate session evidence after publication, source replacement, or rerun."""
    digest = sha256(str(Path(root).resolve()).encode())
    for (start, end), attrs in sorted(monthly_attributions.items()):
        digest.update(repr(attrs).encode())
        digest.update(str(is_month_published_in_storage(storage, period_end=end)).encode())
        for suffix in ("position-journal", "lot-book", "lifecycle-transitions", "import-provenance"):
            key = f"{end:%Y-%m}-{suffix}.json"
            digest.update(storage.read_text(key).encode() if storage.exists(key) else b"missing")
        path = Path(root) / f"Account Trade History {start:%B %Y}.csv"
        digest.update(repr((path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None).encode())
    return digest.hexdigest()


def campaign_entries(entries, realized_records):
    """Follow exact consumed-lot ancestry; never select by underlying alone.

    Legacy local IDs are restricted to the reporting month. Across months,
    only namespaced IDs or a retained carried-lot opening reference can link
    history. Reused LOT-000001 values alone cannot establish an association.
    """
    if not realized_records:
        return []
    symbols = {underlying_symbol(a.record.instrument) for a in realized_records}
    cids = {x.campaign_id for a in realized_records for x in a.allocations if x.campaign_id}
    # Realized records passed here are UI-qualified. Strip only that prefix
    # and the verified underlying suffix used for historical ID collisions.
    cids = {cid.split("/", 1)[1] if re.match(r"^\d{4}-\d{2}/", cid) else cid for cid in cids}
    cids = {cid.rsplit("@", 1)[0] if cid.rsplit("@", 1)[-1] in symbols else cid for cid in cids}
    reporting = {a.record.closed_date.strftime("%Y-%m") for a in realized_records}
    selected = []
    for row in entries:
        instrument = _deserialize_instrument(row["instrument"])
        cid = row["campaign_id"]
        scoped = bool(cid and re.match(r"^\d{4}-\d{2}:", cid))
        if underlying_symbol(instrument) in symbols and cid in cids and (scoped or row["period_start"][:7] in reporting):
            selected.append(row)
    # Carried lots explicitly retain instrument, original opening timestamp,
    # lot quantity and campaign assignment from the resolved import boundary.
    # Link only unique executions, rather than assuming all symbol activity is related.
    for row in tuple(selected):
        if row["action"] != "Carried position":
            continue
        refs = row.get("opening_refs", [])
        if not refs and row.get("opened_at"):
            refs = [dict(lot_id=lot["lot_id"], opened_at=row["opened_at"], quantity=row["quantity_change"])
                    for lot in row["lots"]]
        for ref in refs:
            candidates = [candidate for candidate in entries
                          if candidate["action"] in {"Open / add", "Roll: open"}
                          and candidate["instrument"] == row["instrument"]
                          and candidate["occurred_at"] == ref["opened_at"]
                          and (any(lot["lot_id"] == ref["lot_id"] for lot in candidate["lots"])
                               or (not candidate["lots"] and
                                   abs(Decimal(candidate["quantity_change"])) == abs(Decimal(ref["quantity"]))))]
            if len(candidates) != 1:
                continue
            opening = candidates[0]
            if opening not in selected:
                selected.append(opening)
            # Preserve intervening partial reductions of this proven opening
            # lot. A row spanning unrelated lots cannot be pulled into scope.
            if opening["campaign_id"] is not None:
                for earlier in entries:
                    if (earlier["instrument"] == opening["instrument"]
                            and earlier["campaign_id"] == opening["campaign_id"]
                            and opening["occurred_at"] <= earlier["occurred_at"] < row["occurred_at"]
                            and earlier["action"] in {"Close / reduce", "Roll: close", "Assignment", "Expiration", "Exercise"}
                            and earlier["lots"] and all(lot["lot_id"] == ref["lot_id"] for lot in earlier["lots"])
                            and earlier not in selected):
                        selected.append(earlier)
    # When replay is unavailable, expose only an unambiguous observed close;
    # do not manufacture its opening or running balance.
    for attribution in realized_records:
        record = attribution.record
        candidates = [row for row in entries if row["campaign_id"] is None
                      and row["action"] in {"Close / reduce", "Roll: close"}
                      and repr(_deserialize_instrument(row["instrument"])) == repr(record.instrument)
                      and (datetime.fromisoformat(row["occurred_at"]).date() == record.closed_date
                           or (row["instrument"]["type"] == "option" and
                               _next_weekday(datetime.fromisoformat(row["occurred_at"]).date()) == record.closed_date))
                      and abs(Decimal(row["quantity_change"])) == record.quantity]
        if len(candidates) == 1 and candidates[0] not in selected:
            selected.append(candidates[0])
    return sorted(selected, key=lambda row: row["occurred_at"])


def history_rows(entries, display_instrument, *, include_campaign=False, realized_records=()):
    rows = []
    realized_matches = {}
    for attribution in realized_records:
        record = attribution.record
        candidates = [row for row in entries
                      if row["action"] in {"Close / reduce", "Roll: close", "Assignment", "Expiration", "Exercise"}
                      and _deserialize_instrument(row["instrument"]) == record.instrument
                      and abs(Decimal(row["quantity_change"])) == record.quantity
                      and (datetime.fromisoformat(row["occurred_at"]).date() == record.closed_date
                           or (isinstance(record.instrument, OptionContract) and
                               _next_weekday(datetime.fromisoformat(row["occurred_at"]).date()) == record.closed_date))]
        if len(candidates) == 1:
            realized_matches.setdefault(id(candidates[0]), []).append(record.gain_loss)
    # Monthly carry entries mark a starting balance, not another execution.
    # Suppress them only when its actual original opening is visible.
    opens = {(repr(row["instrument"]), row["occurred_at"]) for row in entries
             if row["action"] in {"Open / add", "Roll: open"}}
    for row in sorted(entries, key=lambda row: row["occurred_at"]):
        refs = row.get("opening_refs", [])
        if row["action"] == "Carried position" and (
                (refs and all((repr(row["instrument"]), ref["opened_at"]) in opens for ref in refs))
                or (not refs and (repr(row["instrument"]), row.get("opened_at")) in opens)):
            continue
        instrument = _deserialize_instrument(row["instrument"])
        rows.append({"Date": datetime.fromisoformat(row["occurred_at"]).date(),
                     "Time": datetime.fromisoformat(row["occurred_at"]).time(),
                     "Action": row["action"], "Instrument": display_instrument(instrument),
                     "Quantity change": float(Decimal(row["quantity_change"])),
                     "Units": "Contracts" if isinstance(instrument, OptionContract) else "Base currency" if re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", instrument.symbol) else "Shares",
                     "Position after": None if row["position_after"] is None else float(Decimal(row["position_after"])),
                     "Price": None if row["price"] is None else float(Decimal(row["price"])),
                     "Fills": row["fills"], "Evidence": row["source"],
                     **({"Realized P&L": float(realized_matches[id(row)][0]) if len(realized_matches.get(id(row), [])) == 1 else None} if realized_records else {}),
                     **({"Campaign": row["campaign_id"] or "Unavailable"} if include_campaign else {})})
    return rows
