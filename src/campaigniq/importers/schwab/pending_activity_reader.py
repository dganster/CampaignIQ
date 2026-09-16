"""Read option position changes from Schwab Pending / Open Activity."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from campaigniq.domain.option_contract import OptionContract
from campaigniq.domain.option_type import OptionType

ACTION=re.compile(r"\b(?P<action>Cover Short|Short Sale)\s+(?P<symbol>[A-Z][A-Z0-9.]*)\s+(?P<type>CALL|PUT)\b.*?(?P<qty>[\d,]+\.\d{4})\b")
EXP=re.compile(r"(?P<x>\d{2}/\d{2}/\d{4})")
STRIKE=re.compile(r"(?P<x>[\d,]+(?:\.\d+)?)\s+[CP]\b")

@dataclass(frozen=True, slots=True)
class SchwabPendingOptionActivity:
    instrument: OptionContract
    quantity_change: Decimal
    activity_date: date | None

def read_pending_option_activity(lines: list[str]) -> tuple[SchwabPendingOptionActivity,...]:
    out=[]; inside=False; current_date=None
    for i,line in enumerate(lines):
        text=line.strip()
        if text.startswith("Pending / Open Activity"):
            inside=True; continue
        if inside and text.startswith("Endnotes For Your Account"):
            break
        if not inside: continue
        m=ACTION.search(line)
        if not m: continue
        em=sm=None
        for detail in lines[i+1:i+4]:
            em=em or EXP.search(detail)
            sm=sm or STRIKE.search(detail)
        if not em or not sm:
            raise ValueError(f"Missing pending option details for {m.group('symbol')}")
        expiration=datetime.strptime(em.group("x"),"%m/%d/%Y").date()
        # Only text before the action can contain the Activity Date.
        # This prevents the 09/01 payable date from becoming the trade date.
        prefix = line[: line.find(m.group("action"))]
        dm=re.search(r"\b(\d{2})/(\d{2})\b",prefix)
        if dm:
            current_date=date(expiration.year,int(dm.group(1)),int(dm.group(2)))
        qty=Decimal(m.group("qty").replace(",",""))
        change=qty if m.group("action")=="Cover Short" else -qty
        out.append(SchwabPendingOptionActivity(
            OptionContract(m.group("symbol"),expiration,Decimal(sm.group("x").replace(",","")),OptionType[m.group("type")]),
            change,current_date))
    return tuple(out)
