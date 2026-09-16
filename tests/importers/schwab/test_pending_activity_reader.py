from datetime import date
from decimal import Decimal
from pathlib import Path
from campaigniq.importers.schwab.pending_activity_reader import read_pending_option_activity

def test_reads_august_pending_rolls_exactly():
    rows=read_pending_option_activity(Path("tests/data/schwab/august_positions.txt").read_text().splitlines())
    rows=[r for r in rows if r.instrument.underlying in {"AAPL","CVX","IBM"}]
    assert len(rows)==6
    got={(r.instrument.underlying,r.instrument.expiration,r.instrument.strike,r.quantity_change,r.activity_date) for r in rows}
    assert got=={
      ("AAPL",date(2026,10,2),Decimal("290"),Decimal("2"),date(2026,8,31)),
      ("AAPL",date(2026,10,16),Decimal("300"),Decimal("-2"),date(2026,8,31)),
      ("CVX",date(2026,10,2),Decimal("190"),Decimal("2"),date(2026,8,31)),
      ("CVX",date(2026,10,16),Decimal("195"),Decimal("-2"),date(2026,8,31)),
      ("IBM",date(2026,10,2),Decimal("210"),Decimal("2"),date(2026,8,31)),
      ("IBM",date(2026,10,16),Decimal("220"),Decimal("-2"),date(2026,8,31)),
    }
