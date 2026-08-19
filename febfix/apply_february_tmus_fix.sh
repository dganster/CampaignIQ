#!/usr/bin/env bash
set -euo pipefail

cd "${CAMPAIGNIQ_ROOT:-$HOME/CampaignIQ}"

python - <<'PY'
from pathlib import Path

path = Path("src/campaigniq/domain/realized_lot_attributor.py")
text = path.read_text()

old = """@dataclass(frozen=True, slots=True)
class _ClosingActivity:
    closed_date: date
    instrument: Instrument
    quantity: Decimal
    allocations: tuple[LotAllocation, ...]
"""
new = """@dataclass(frozen=True, slots=True)
class _ClosingActivity:
    closed_date: date
    instrument: Instrument
    quantity: Decimal
    allocations: tuple[LotAllocation, ...]
    allow_prior_business_day_match: bool = False
"""
if old not in text:
    raise SystemExit("Could not find _ClosingActivity definition; file may already be changed or differ from the expected version.")
text = text.replace(old, new, 1)

old = """                if allocations and change.quantity < 0:
                    activities.append(
                        _ClosingActivity(
                            closed_date=event.occurred_at.date(),
                            instrument=change.instrument,
                            quantity=abs(quantity),
                            allocations=allocations,
                        )
                    )
"""
new = """                if allocations and change.quantity < 0:
                    activities.append(
                        _ClosingActivity(
                            closed_date=event.occurred_at.date(),
                            instrument=change.instrument,
                            quantity=abs(quantity),
                            allocations=allocations,
                            allow_prior_business_day_match=(
                                event.kind == PositionEventKind.ASSIGNMENT
                            ),
                        )
                    )
"""
if old not in text:
    raise SystemExit("Could not find assignment closing-activity block; file may already be changed or differ from the expected version.")
text = text.replace(old, new, 1)

old = """    @classmethod
    def _consume_matching_activity(
        cls,
        activities: list[_ClosingActivity],
        record: RealizedGainLossRecord,
    ) -> tuple[LotAllocation, ...]:
        \"\"\"Consume closing activity matching one broker realized record.\"\"\"
        remaining = record.quantity
        matched: list[LotAllocation] = []

        index = 0
        while index < len(activities) and remaining > 0:
            activity = activities[index]
            if (
                activity.closed_date != record.closed_date
                or activity.instrument != record.instrument
            ):
                index += 1
                continue

            take = min(activity.quantity, remaining)
            matched.extend(
                cls._take_allocations(activity.allocations, take)
            )

            if take == activity.quantity:
                activities.pop(index)
            else:
                activities[index] = _ClosingActivity(
                    closed_date=activity.closed_date,
                    instrument=activity.instrument,
                    quantity=activity.quantity - take,
                    allocations=cls._remaining_allocations(
                        activity.allocations,
                        take,
                    ),
                )
                index += 1

            remaining -= take

        if remaining:
            raise ValueError(
                \"No CampaignIQ closing activity matches broker realized \"
                f\"record: {record.instrument} {record.closed_date} \"
                f\"{record.quantity}; {remaining} remains unmatched.\"
            )

        return tuple(matched)
"""
new = """    @classmethod
    def _consume_matching_activity(
        cls,
        activities: list[_ClosingActivity],
        record: RealizedGainLossRecord,
    ) -> tuple[LotAllocation, ...]:
        \"\"\"Consume closing activity matching one broker realized record.

        Ordinary closing activity requires an exact date match. Assignment-
        derived stock dispositions may match the broker realization on the
        immediately preceding business day, reflecting Schwab's assignment
        settlement/reporting date without weakening ordinary trade matching.
        Exact matches are always consumed before this assignment-only fallback.
        \"\"\"
        remaining = record.quantity
        matched: list[LotAllocation] = []

        for assignment_fallback in (False, True):
            index = 0
            while index < len(activities) and remaining > 0:
                activity = activities[index]
                if activity.instrument != record.instrument:
                    index += 1
                    continue

                exact_match = activity.closed_date == record.closed_date
                assignment_match = (
                    assignment_fallback
                    and activity.allow_prior_business_day_match
                    and cls._next_business_day(record.closed_date)
                    == activity.closed_date
                )

                if (
                    assignment_fallback and not assignment_match
                ) or (
                    not assignment_fallback and not exact_match
                ):
                    index += 1
                    continue

                take = min(activity.quantity, remaining)
                matched.extend(
                    cls._take_allocations(activity.allocations, take)
                )

                if take == activity.quantity:
                    activities.pop(index)
                else:
                    activities[index] = _ClosingActivity(
                        closed_date=activity.closed_date,
                        instrument=activity.instrument,
                        quantity=activity.quantity - take,
                        allocations=cls._remaining_allocations(
                            activity.allocations,
                            take,
                        ),
                        allow_prior_business_day_match=(
                            activity.allow_prior_business_day_match
                        ),
                    )
                    index += 1

                remaining -= take

        if remaining:
            raise ValueError(
                \"No CampaignIQ closing activity matches broker realized \"
                f\"record: {record.instrument} {record.closed_date} \"
                f\"{record.quantity}; {remaining} remains unmatched.\"
            )

        return tuple(matched)
"""
if old not in text:
    raise SystemExit("Could not find _consume_matching_activity implementation; file may already be changed or differ from the expected version.")
text = text.replace(old, new, 1)
path.write_text(text)

pipeline = Path("src/campaigniq/import_pipeline.py")
lines = pipeline.read_text().splitlines(keepends=True)
filtered = [line for line in lines if "DEBUG AFTER " not in line]
if len(filtered) != len(lines):
    pipeline.write_text("".join(filtered))

print("Updated", path)
print("Removed temporary DEBUG AFTER lines from", pipeline)
PY

pytest -q tests/test_february_campaign_realized_pnl.py::test_tmus_assignment_matches_february_realized_stock_sale
