"""Read-only lot continuity replay. Does not approve evidence or publication."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json

from campaigniq.domain.lot_book_period_applier import LotBookPeriodApplier
from campaigniq.historical_backfill_impact import month_end
from campaigniq.persistence.lot_book_store import deserialize_lot_book, serialize_lot_book


@dataclass(frozen=True)
class ReplayPeriod:
    period_end: date
    opening_text: str
    closing_text: str
    position_history: object
    campaigns: tuple


def _book(text, expected):
    # Reject duplicate JSON fields rather than silently selecting one.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    payload = json.loads(text, object_pairs_hook=pairs)
    if (not isinstance(payload, dict)
            or set(payload) != {"format", "version", "period_end", "next_id", "lots"}
            or type(payload["version"]) is not int
            or type(payload["next_id"]) is not int):
        raise ValueError("invalid lot book envelope")
    persisted = deserialize_lot_book(text)
    if persisted.period_end != expected:
        raise ValueError("lot book boundary date mismatch")
    ids = set()
    for lot in payload["lots"]:
        if set(lot) != {"lot_id", "instrument", "quantity", "opened_at",
                        "basis_total", "basis_source", "campaign_id"}:
            raise ValueError("invalid lot fields")
        if not isinstance(lot["lot_id"], str) or not lot["lot_id"] or lot["lot_id"] in ids:
            raise ValueError("missing or duplicate lot identity")
        ids.add(lot["lot_id"])
        if (not isinstance(lot["opened_at"], str)
                or datetime.fromisoformat(lot["opened_at"]).date() > expected):
            raise ValueError("invalid lot opening date")
        for field in ("basis_source", "campaign_id"):
            if lot[field] is not None and not isinstance(lot[field], str):
                raise ValueError("invalid lot provenance")
        for field in ("quantity", "basis_total"):
            if field == "basis_total" and lot[field] is None:
                continue
            if not isinstance(lot[field], str) or not Decimal(lot[field]).is_finite():
                raise ValueError("non-finite or invalid lot value")
        instrument = lot["instrument"]
        if not isinstance(instrument, dict):
            raise ValueError("invalid instrument")
        identity = "symbol" if instrument.get("type") == "instrument" else "underlying"
        if not isinstance(instrument.get(identity), str) or not instrument[identity]:
            raise ValueError("invalid instrument identity")
        fields = ({"type", "symbol"} if instrument.get("type") == "instrument"
                  else {"type", "underlying", "expiration", "strike", "option_type"})
        if set(instrument) != fields:
            raise ValueError("invalid instrument fields")
        if "strike" in instrument and not Decimal(instrument["strike"]).is_finite():
            raise ValueError("non-finite strike")
    return persisted.lot_book


def _canonical(book, end):
    # Includes lot identity, FIFO order, quantities, basis, campaign provenance
    # and allocator state. Conservative decimal/text differences remain blockers.
    return serialize_lot_book(period_end=end, lot_book=book)


def audit_replay(*, workspace, target_period_end, target_closing_text,
                 downstream_period_end, periods):
    """Replay every intervening month and compare independently supplied states.

    Callers must obtain complete histories and opening/closing evidence through
    existing verified import paths. Matching supplied inputs is not proof that
    those inputs are authentic or complete. This function never authorizes
    publication, approves reconciliation exceptions, or writes artifacts.
    """
    if not isinstance(workspace, str) or not workspace:
        raise ValueError("explicit workspace required")
    for end in (target_period_end, downstream_period_end):
        if end != month_end(end.year, end.month):
            raise ValueError("month-end date required")
    if downstream_period_end <= target_period_end:
        raise ValueError("downstream month must follow target")
    rows = tuple(periods)
    expected = []
    cursor = target_period_end
    while cursor < downstream_period_end:
        year, month = (cursor.year + 1, 1) if cursor.month == 12 else (cursor.year, cursor.month + 1)
        cursor = month_end(year, month)
        expected.append(cursor)
    if [row.period_end for row in rows] != expected:
        raise ValueError("missing, duplicate, unordered, or extra replay months")
    carried = _book(target_closing_text, target_period_end)
    previous = target_period_end
    findings = []
    fingerprints = [sha256(target_closing_text.encode()).hexdigest()]
    for row in rows:
        opening = _book(row.opening_text, previous)
        closing = _book(row.closing_text, row.period_end)
        fingerprints.extend(sha256(text.encode()).hexdigest()
                            for text in (row.opening_text, row.closing_text))
        if _canonical(carried, previous) != _canonical(opening, previous):
            findings.append(f"{row.period_end:%Y-%m}: opening lot state differs from preceding close")
        start = row.period_end.replace(day=1)
        for item in row.position_history.items():
            instants = [item.occurred_at]
            if item.trade is not None:
                instants.extend(execution.executed_at for leg in item.trade.legs
                                for execution in leg.executions)
            if any(not start <= instant.date() <= row.period_end for instant in instants):
                raise ValueError("replay history contains out-of-period activity")
        # Replay the carried state, never reset it to the supplied opening.
        carried = LotBookPeriodApplier().apply(
            opening_lot_book=carried, position_history=row.position_history,
            campaigns=row.campaigns)
        if _canonical(carried, row.period_end) != _canonical(closing, row.period_end):
            findings.append(f"{row.period_end:%Y-%m}: replayed closing lot state differs")
        previous = row.period_end
    return {"format": "campaigniq.historical_backfill_replay", "version": 1,
            "workspace": workspace, "target_period_end": target_period_end.isoformat(),
            "downstream_period_end": downstream_period_end.isoformat(),
            "months_checked": len(rows), "supplied_states_match_replay": not findings,
            "blocking_findings": findings, "state_input_sha256": fingerprints,
            "evidence_completeness_verified": False, "publication_authorized": False,
            "limitation": "Supplied states and histories require independent evidence verification; reconciliation, backup, authorization and transactional safeguards remain required."}
