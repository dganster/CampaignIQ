#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
python - <<'PY'
from pathlib import Path
p = Path('src/campaigniq/import_pipeline.py')
s = p.read_text()
s = s.replace('from datetime import date, datetime\n', 'from datetime import date, datetime\nfrom decimal import Decimal\n', 1)
s = s.replace('from campaigniq.domain.lot_book import LotBook\n', 'from campaigniq.domain.lot import Lot\nfrom campaigniq.domain.lot_book import LotBook\nfrom campaigniq.domain.option_contract import OptionContract\nfrom campaigniq.domain.position_effect import PositionEffect\nfrom campaigniq.domain.side import Side\n', 1)
needle = '''        historical_trades = tuple(\n            trade\n            for filename in historical_trade_histories\n            for trade in self._read_trades(\n                filename,\n                start=historical_period_start,\n                end=period_start - date.resolution,\n            )\n        )\n\n'''
if needle not in s: raise SystemExit('historical_trades insertion point not found')
s = s.replace(needle, needle + '''        self._seed_missing_historical_option_lots(\n            opening_lot_book,\n            historical_trades,\n        )\n\n''', 1)
needle = '    def _read_trades(\n'
helper = '''    @staticmethod\n    def _seed_missing_historical_option_lots(\n        opening_lot_book: LotBook,\n        historical_trades: tuple[Trade, ...],\n    ) -> None:\n        """Seed option lots missing from the opening snapshot when history is sufficient."""\n        pending: dict[object, list[list[object]]] = {}\n        invalid: set[object] = set()\n\n        for trade in historical_trades:\n            for leg in trade.legs:\n                if not isinstance(leg.instrument, OptionContract):\n                    continue\n                instrument = leg.instrument\n                if instrument in invalid:\n                    continue\n                quantity = sum(\n                    (abs(execution.quantity) for execution in leg.executions),\n                    Decimal("0"),\n                )\n                if quantity == 0:\n                    continue\n                lots = pending.setdefault(instrument, [])\n                if leg.position_effect == PositionEffect.OPEN:\n                    signed = quantity if leg.side == Side.BUY else -quantity\n                    opened_at = min(\n                        execution.executed_at for execution in leg.executions\n                    )\n                    lots.append([signed, opened_at])\n                    continue\n                target_sign = 1 if leg.side == Side.SELL else -1\n                remaining = quantity\n                for lot in lots:\n                    if remaining <= 0:\n                        break\n                    lot_quantity = lot[0]\n                    if (lot_quantity > 0) != (target_sign > 0):\n                        continue\n                    consumed = min(abs(lot_quantity), remaining)\n                    lot[0] = (\n                        lot_quantity - consumed\n                        if lot_quantity > 0\n                        else lot_quantity + consumed\n                    )\n                    remaining -= consumed\n                if remaining:\n                    invalid.add(instrument)\n\n        for instrument, lots in pending.items():\n            if instrument in invalid or opening_lot_book.lots(instrument):\n                continue\n            remaining_lots = [\n                (quantity, opened_at)\n                for quantity, opened_at in lots\n                if quantity != 0\n            ]\n            for index, (quantity, opened_at) in enumerate(remaining_lots, 1):\n                opening_lot_book.seed(\n                    Lot(\n                        lot_id=(\n                            f"HISTORICAL-TRADE:{opened_at.isoformat()}"\n                            f":{instrument}:{index}"\n                        ),\n                        instrument=instrument,\n                        quantity=quantity,\n                        opened_at=opened_at,\n                        basis_total=None,\n                        basis_source="HISTORICAL_TRADE_RECONSTRUCTION",\n                    )\n                )\n\n'''
if needle not in s: raise SystemExit('_read_trades insertion point not found')
s = s.replace(needle, helper + needle, 1)
p.write_text(s)
PY

cat > tests/test_march_cmi_historical_option_reconstruction.py <<'PY'
from datetime import date, datetime
from decimal import Decimal

from campaigniq.domain.execution import Execution
from campaigniq.domain.leg import Leg
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType
from campaigniq.domain.position_effect import PositionEffect
from campaigniq.domain.side import Side
from campaigniq.domain.trade import Trade
from campaigniq.import_pipeline import PeriodImportPipeline


def test_missing_historical_option_lot_is_reconstructed_for_cmi() -> None:
    instrument = OptionContract(
        underlying="CMI",
        expiration=date(2026, 3, 20),
        strike=Decimal("540"),
        option_type=OptionType.CALL,
    )
    trade = Trade(
        legs=(
            Leg(
                instrument=instrument,
                side=Side.SELL,
                position_effect=PositionEffect.OPEN,
                executions=(
                    Execution(
                        quantity=Decimal("-1"),
                        execution_price=Decimal("44.56"),
                        executed_at=datetime(2026, 2, 27, 8, 26, 19),
                    ),
                ),
            ),
        )
    )

    book = LotBook()
    PeriodImportPipeline._seed_missing_historical_option_lots(book, (trade,))

    lots = book.lots(instrument)
    assert len(lots) == 1
    assert lots[0].quantity == Decimal("-1")
    assert lots[0].opened_at == datetime(2026, 2, 27, 8, 26, 19)
    assert lots[0].basis_total is None
    assert lots[0].basis_source == "HISTORICAL_TRADE_RECONSTRUCTION"
PY

pytest -q tests/test_march_cmi_historical_option_reconstruction.py
pytest -q
