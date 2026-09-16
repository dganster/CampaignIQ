"""JSON persistence for authoritative FOREX settlement campaign attributions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from campaigniq.domain.forex_settlement_attribution import ForexSettlementAttribution
from campaigniq.importers.schwab.forex_transaction_reader import SchwabForexSettlement

_FORMAT = "campaigniq.forex_settlement_attributions"
_VERSION = 1


@dataclass(frozen=True, slots=True)
class PersistedForexSettlementAttributions:
    """One durable period of FOREX settlement campaign attributions."""

    period_start: date
    period_end: date
    attributions: tuple[ForexSettlementAttribution, ...]

    def __post_init__(self) -> None:
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")


def save_forex_settlement_attributions(
    path: str | Path,
    *,
    period_start: date,
    period_end: date,
    attributions: Iterable[ForexSettlementAttribution],
) -> None:
    persisted = PersistedForexSettlementAttributions(
        period_start=period_start,
        period_end=period_end,
        attributions=tuple(attributions),
    )
    destination = Path(path)
    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "period_start": persisted.period_start.isoformat(),
        "period_end": persisted.period_end.isoformat(),
        "attributions": [
            {
                "campaign_id": item.campaign_id,
                "settlement": {
                    "order_id": item.settlement.order_id,
                    "trade_at": item.settlement.trade_at.isoformat(),
                    "settlement_at": item.settlement.settlement_at.isoformat(),
                    "instrument": item.settlement.instrument,
                    "side": item.settlement.side,
                    "rate": str(item.settlement.rate),
                    "amount": str(item.settlement.amount),
                    "settlement_pl_usd": str(item.settlement.settlement_pl_usd),
                    "total_position": str(item.settlement.total_position),
                },
            }
            for item in persisted.attributions
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_forex_settlement_attributions(
    path: str | Path,
) -> PersistedForexSettlementAttributions:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    if payload.get("format") != _FORMAT:
        raise ValueError("Unsupported FOREX settlement attribution persistence format.")
    if payload.get("version") != _VERSION:
        raise ValueError(
            "Unsupported FOREX settlement attribution persistence version: "
            f"{payload.get('version')!r}"
        )

    period_start_raw = payload.get("period_start")
    period_end_raw = payload.get("period_end")
    items = payload.get("attributions")
    if not isinstance(period_start_raw, str) or not isinstance(period_end_raw, str):
        raise ValueError("FOREX attribution persistence must contain period dates.")
    if not isinstance(items, list):
        raise ValueError("FOREX attribution persistence must contain an attributions list.")

    return PersistedForexSettlementAttributions(
        period_start=date.fromisoformat(period_start_raw),
        period_end=date.fromisoformat(period_end_raw),
        attributions=tuple(_deserialize_attribution(item) for item in items),
    )


def _deserialize_attribution(payload: dict[str, object]) -> ForexSettlementAttribution:
    settlement = payload["settlement"]
    if not isinstance(settlement, dict):
        raise ValueError("FOREX attribution settlement must be an object.")

    return ForexSettlementAttribution(
        campaign_id=str(payload["campaign_id"]),
        settlement=SchwabForexSettlement(
            order_id=str(settlement["order_id"]),
            trade_at=datetime.fromisoformat(str(settlement["trade_at"])),
            settlement_at=datetime.fromisoformat(str(settlement["settlement_at"])),
            instrument=str(settlement["instrument"]),
            side=str(settlement["side"]),
            rate=Decimal(str(settlement["rate"])),
            amount=Decimal(str(settlement["amount"])),
            settlement_pl_usd=Decimal(str(settlement["settlement_pl_usd"])),
            total_position=Decimal(str(settlement["total_position"])),
        ),
    )
