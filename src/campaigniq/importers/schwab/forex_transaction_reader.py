from __future__ import annotations
import csv,re
from dataclasses import dataclass
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
M=re.compile(r"^\s*([+-]?)\$?([\d,]+(?:\.\d+)?)\s*(?:USD)?\s*$",re.I)
@dataclass(frozen=True,slots=True)
class SchwabForexSettlement:
    order_id:str; trade_at:datetime; settlement_at:datetime; instrument:str; side:str
    rate:Decimal; amount:Decimal; settlement_pl_usd:Decimal; total_position:Decimal
@dataclass(frozen=True,slots=True)
class SchwabForexFinancing:
    order_id:str; occurred_at:datetime; instrument:str; financing_usd:Decimal
@dataclass(frozen=True,slots=True)
class SchwabForexTransactionReport:
    period_text:str; period_start:date; period_end:date
    mtd_settled_pl_usd:Decimal; mtd_fee_usd:Decimal
    settlements:tuple[SchwabForexSettlement,...]; financing:tuple[SchwabForexFinancing,...]
    @property
    def settlement_pl_usd(self): return sum((x.settlement_pl_usd for x in self.settlements),Decimal("0"))
    @property
    def settlement_control_delta_usd(self):
        return self.mtd_settled_pl_usd - self.settlement_pl_usd
    @property
    def settlement_control_reconciled(self):
        return self.settlement_control_delta_usd == Decimal("0")
    @property
    def financing_usd(self): return sum((x.financing_usd for x in self.financing),Decimal("0"))
def dec(s): return Decimal(s.strip().replace(",","").lstrip("+"))
def usd(s):
    m=M.match(s)
    if not m: raise ValueError(f"Not a USD amount: {s!r}")
    sign,n=m.groups(); v=Decimal(n.replace(",","")); return -v if sign=="-" else v
def dt(s): return datetime.strptime(s.strip(),"%b %d, %Y %H:%M:%S")
PERIOD_RE=re.compile(
    r"^Transaction Report since (?P<start>[A-Z][a-z]{2} \d{1,2}, \d{4})"
    r"(?: \d{2}:\d{2}:\d{2} \([^)]+\))?\s+through "
    r"(?P<end>[A-Z][a-z]{2} \d{1,2}, \d{4})"
    r"(?: \d{2}:\d{2}:\d{2} \([^)]+\))?\s*$"
)
def report_period(s):
    normalized=s.replace(" | ", ", ")
    m=PERIOD_RE.match(normalized.strip())
    if not m: raise ValueError(f"FOREX report period could not be determined: {s!r}")
    return (datetime.strptime(m.group("start"),"%b %d, %Y").date(),
            datetime.strptime(m.group("end"),"%b %d, %Y").date())
def oid(s):
    s=s.strip(); return s[2:-1] if s.startswith('="') and s.endswith('"') else s.strip('"=')
def read_forex_transaction_report(source:str|Path):
    with Path(source).open(newline="",encoding="utf-8-sig",errors="replace") as f: rows=list(csv.reader(f))
    period=""; pl=fee=None; settlements=[]; financing=[]
    for row in rows:
        if not row: continue
        c=[x.strip() for x in row]; first=c[0] if c else ""
        if first.lower().startswith("transaction report since"): period=" | ".join(x for x in c if x)
        elif first.upper().startswith("MTD SETTLED PL"): pl=usd(c[1])
        elif first.upper().startswith("MTD FEE"): fee=usd(c[1])
        if len(c)<4 or not(first.startswith('="') or first.strip('"=').isdigit()): continue
        kind=c[3].lower()
        if kind=="settlement":
            if len(c)<13 or not c[11]: raise ValueError(f"Incomplete settlement: {row!r}")
            settlements.append(SchwabForexSettlement(oid(c[0]),dt(c[1]),dt(c[2]),c[4],c[5],dec(c[6]),dec(c[7]),usd(c[11]),dec(c[12])))
        elif kind=="financing":
            usd_cells=[x for x in c[4:] if "USD" in x.upper()]
            if not usd_cells: raise ValueError(f"Financing row lacks USD amount: {row!r}")
            financing.append(SchwabForexFinancing(oid(c[0]),dt(c[1]),c[4],usd(usd_cells[-1])))
    if not period: raise ValueError("Not a Thinkorswim FOREX Transaction Report: period missing")
    period_start,period_end=report_period(period)
    if pl is None: raise ValueError("FOREX report lacks MTD Settled PL")
    if fee is None: raise ValueError("FOREX report lacks MTD fee")
    return SchwabForexTransactionReport(period,period_start,period_end,pl,fee,tuple(settlements),tuple(financing))
