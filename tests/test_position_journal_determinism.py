"""Publication checksums must survive separate Python process hash seeds."""
import json
import os
import subprocess
import sys


SCRIPT = '''
from datetime import datetime
from decimal import Decimal as D
from tests.test_position_journal import imported, START, END
from campaigniq.domain.lot import Lot
from campaigniq.domain.lot_book import LotBook
from campaigniq.domain.position_event import PositionEvent, PositionChange
from campaigniq.domain.position_event_kind import PositionEventKind
from campaigniq.domain.value_objects.instrument import Instrument
from campaigniq.persistence.position_journal import serialize_position_journal
book=LotBook();symbol=Instrument("ABC")
for n in range(12):
    book.seed(Lot("lot-"+str(n),symbol,D(-1),datetime(2026,8,n+1),None,campaign_id="campaign-"+str(n%3)))
event=PositionEvent(PositionEventKind.EXPIRATION,(PositionChange(symbol,D(12)),),datetime(2026,9,20))
print(serialize_position_journal(imported([],opening=book,events=(event,)),period_start=START,period_end=END),end="")
'''


def test_event_journal_bytes_are_stable_across_process_hash_seeds():
    outputs = [subprocess.check_output([sys.executable,'-c',SCRIPT],
        env=dict(os.environ,PYTHONHASHSEED=str(seed))) for seed in (1,2,3,42,101)]
    assert len(set(outputs)) == 1
    rows=json.loads(outputs[0])['entries']
    expired=[r for r in rows if r['action']=='Expiration']
    assert len(expired)==3
    assert sum(int(r['quantity_change']) for r in expired)==12
    assert {r['campaign_id'] for r in expired}=={'campaign-0','campaign-1','campaign-2'}
    assert all(r['position_after']=='0' for r in expired)
    assert {lot['lot_id'] for r in expired for lot in r['lots']}=={'lot-'+str(n) for n in range(12)}
