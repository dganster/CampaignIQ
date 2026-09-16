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
    fee_usd:Decimal=Decimal("0")
@dataclass(frozen=True,slots=True)
class SchwabForexFinancing:
    order_id:str; occurred_at:datetime; instrument:str; financing_usd:Decimal
@dataclass(frozen=True,slots=True)
class SchwabForexNewTransaction:
    order_id:str; trade_at:datetime; settlement_at:datetime; instrument:str; side:str
    rate:Decimal; amount:Decimal; counter_amount:Decimal; fee_usd:Decimal
    average_acquisition_fx_rate:Decimal; total_position:Decimal
@dataclass(frozen=True,slots=True)
class SchwabForexTransactionReport:
    period_text:str; period_start:date; period_end:date
    mtd_settled_pl_usd:Decimal; mtd_fee_usd:Decimal
    settlements:tuple[SchwabForexSettlement,...]; financing:tuple[SchwabForexFinancing,...]
    new_transactions:tuple[SchwabForexNewTransaction,...]=()
    ytd_settled_pl_usd:Decimal|None=None; ytd_fee_usd:Decimal|None=None
    pl_total_usd:Decimal|None=None; commission_total_usd:Decimal|None=None
    financing_totals_usd:tuple[tuple[str,Decimal],...]=()
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
    @property
    def financing_usd_by_instrument(self):
        totals={}
        for x in self.financing:
            totals[x.instrument]=totals.get(x.instrument,Decimal("0"))+x.financing_usd
        return totals
    @property
    def financing_control_deltas_usd(self):
        rows=self.financing_usd_by_instrument
        return tuple((instrument,control-rows.get(instrument,Decimal("0"))) for instrument,control in self.financing_totals_usd)
    @property
    def financing_control_reconciled(self):
        return None if not self.financing_totals_usd else all(delta == Decimal("0") for _,delta in self.financing_control_deltas_usd)
    @property
    def pl_total_control_delta_usd(self):
        return None if self.pl_total_usd is None else self.pl_total_usd - self.settlement_pl_usd
    @property
    def pl_total_control_reconciled(self):
        delta = self.pl_total_control_delta_usd
        return None if delta is None else delta == Decimal("0")
    @property
    def transaction_fee_usd(self):
        return (
            sum((x.fee_usd for x in self.new_transactions),Decimal("0"))
            + sum((x.fee_usd for x in self.settlements),Decimal("0"))
        )
    @property
    def commission_control_delta_usd(self):
        return None if self.commission_total_usd is None else self.commission_total_usd - self.transaction_fee_usd
    @property
    def commission_control_reconciled(self):
        delta = self.commission_control_delta_usd
        return None if delta is None else delta == Decimal("0")
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
    period=""; pl=fee=ytd_pl=ytd_fee=pl_total=commission_total=None; settlements=[]; financing=[]; new_transactions=[]; financing_totals=[]
    for row in rows:
        if not row: continue
        c=[x.strip() for x in row]; first=c[0] if c else ""
        if first.lower().startswith("transaction report since"): period=" | ".join(x for x in c if x)
        elif first.upper().startswith("MTD SETTLED PL"): pl=usd(c[1])
        elif first.upper().startswith("YTD SETTLED PL"): ytd_pl=usd(c[1])
        elif first.upper().startswith("MTD FEE"): fee=usd(c[1])
        elif first.upper().startswith("YTD FEE"): ytd_fee=usd(c[1])
        elif first.upper().startswith("TOTAL FINANCING"):
            financing_totals.append((c[1],usd(c[2])))
        elif not first and len(c)>=3 and c[1] and c[2] and financing_totals:
            financing_totals.append((c[1],usd(c[2])))
        elif first.upper().startswith("PL TOTAL"): pl_total=usd(c[2])
        elif first.upper().startswith("COMMISSION TOTAL"): commission_total=usd(c[2])
        if len(c)<4 or not(first.startswith('="') or first.strip('"=').isdigit()): continue
        kind=c[3].lower()
        if kind=="settlement":
            if len(c)<13 or not c[11]: raise ValueError(f"Incomplete settlement: {row!r}")
            settlements.append(SchwabForexSettlement(oid(c[0]),dt(c[1]),dt(c[2]),c[4],c[5],dec(c[6]),dec(c[7]),usd(c[11]),dec(c[12]),usd(c[9]) if len(c)>9 and c[9] else Decimal("0")))
        elif kind=="new":
            if len(c)<13 or not all(c[i] for i in (1,2,4,5,6,7,8,9,10,12)):
                raise ValueError(f"Incomplete new FOREX transaction: {row!r}")
            new_transactions.append(SchwabForexNewTransaction(
                oid(c[0]),dt(c[1]),dt(c[2]),c[4],c[5],dec(c[6]),dec(c[7]),
                dec(c[8]),usd(c[9]),dec(c[10]),dec(c[12])
            ))
        elif kind=="financing":
            usd_cells=[x for x in c[4:] if "USD" in x.upper()]
            if not usd_cells: raise ValueError(f"Financing row lacks USD amount: {row!r}")
            financing.append(SchwabForexFinancing(oid(c[0]),dt(c[1]),c[4],usd(usd_cells[-1])))
        else:
            raise ValueError(f"Unrecognized FOREX transaction type {c[3]!r}: {row!r}")
    if not period: raise ValueError("Not a Thinkorswim FOREX Transaction Report: period missing")
    period_start,period_end=report_period(period)
    if pl is None: raise ValueError("FOREX report lacks MTD Settled PL")
    if fee is None: raise ValueError("FOREX report lacks MTD fee")
    return SchwabForexTransactionReport(period,period_start,period_end,pl,fee,tuple(settlements),tuple(financing),tuple(new_transactions),ytd_pl,ytd_fee,pl_total,commission_total,tuple(financing_totals))
