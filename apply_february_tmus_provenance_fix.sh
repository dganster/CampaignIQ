#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f "pyproject.toml" || ! -f "src/campaigniq/domain/realized_lot_attributor.py" ]]; then
    echo "ERROR: Run this script from the CampaignIQ repository root."
    echo "Current directory: $PWD"
    exit 1
fi

python - <<'PY'
from pathlib import Path

path = Path("src/campaigniq/domain/lot_book.py")
text = path.read_text()

start = text.index("    def assign_unassigned_lots_to_campaign(")
end = text.index("    def assign_campaign(", start)

new = """    def assign_unassigned_lots_to_campaign(
        self,
        instrument: Instrument,
        *,
        quantity: Decimal,
        campaign_id: str,
    ) -> None:
        \"\"\"Assign an existing unassigned positive lot to an assignment campaign.

        If no existing positive lot is present, the assignment is creating a
        new equity position; apply_signed_change() will create that lot with
        the supplied campaign_id.
        \"\"\"
        remaining = quantity

        lots = self._lots.get(instrument, [])
        matching = [
            lot
            for lot in lots
            if lot.campaign_id is None and lot.quantity > 0
        ]

        if not matching:
            return

        for lot in list(matching):
            if remaining <= 0:
                break

            take = min(lot.quantity, remaining)
            replacement = Lot(
                lot_id=lot.lot_id,
                instrument=lot.instrument,
                quantity=lot.quantity,
                opened_at=lot.opened_at,
                basis_total=lot.basis_total,
                basis_source=lot.basis_source,
                campaign_id=campaign_id,
            )
            lots[lots.index(lot)] = replacement
            remaining -= take

        if remaining:
            raise ValueError(
                f"Insufficient unassigned {instrument} lots to assign "
                f"{quantity}; {remaining} remains unmatched."
            )

"""

path.write_text(text[:start] + new + text[end:])
PY

pytest -q tests/test_february_tmus_assignment_regression.py
pytest -q
