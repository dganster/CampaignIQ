"""Separate, provisional crypto inventory and FIFO economics from TOS evidence."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal

from campaigniq.persistence.artifact_storage import ArtifactStorage
from campaigniq.persistence.monthly_publication import is_month_published_in_storage


def crypto_month_key(period_end: date) -> str:
    return f"{period_end:%Y-%m}-crypto.json"


def load_preceding_crypto(storage: ArtifactStorage, period_start: date) -> dict | None:
    prior_end = period_start - date.resolution
    key = crypto_month_key(prior_end)
    if not storage.exists(key) or not is_month_published_in_storage(storage, period_end=prior_end):
        return None
    result = json.loads(storage.read_text(key))
    if result.get("format") != "campaigniq.crypto_month" or result.get("version") != 1:
        raise ValueError("Unsupported crypto history format.")
    if result.get("period_end") != prior_end.isoformat():
        raise ValueError("Crypto history period does not match its key.")
    return result


def build_crypto_month(report: dict, previous: dict | None = None) -> dict:
    """Build exact quantities and ledger-based costs; never invent opening basis.

    Without prior history, opening units are inferred from the export snapshot
    and net fills. Inferred units have unknown cost. All crypto state remains
    provisional because the snapshot has no independently confirmed as-of date.
    """
    result = deepcopy(report)
    if previous and previous.get("account") != report.get("account"):
        raise ValueError("Crypto account changed; separate opening evidence is required.")
    if previous and report.get("opening_cash_usd") is not None and previous.get("last_reported_cash_usd") is not None:
        if Decimal(report["opening_cash_usd"]) != Decimal(previous["last_reported_cash_usd"]):
            result["warnings"].append("Opening crypto cash differs from the previous published export; review missing cash activity.")
    quantities = {}
    for fill in report["fills"]:
        sign = 1 if fill["side"] == "BUY" else -1
        quantities[fill["pair"]] = quantities.get(fill["pair"], Decimal(0)) + sign * Decimal(fill["quantity"])
    lots = deepcopy(previous.get("ending_lots", {})) if previous else {}
    opening = {}
    if previous:
        opening = {pair: str(sum((Decimal(lot["quantity"]) for lot in rows), Decimal(0)))
                   for pair, rows in lots.items()}
    else:
        result["warnings"].append("Opening crypto quantities are inferred from this export; no previous published crypto state is available.")
        for pair in set(quantities) | set(report["holdings_snapshot"]):
            snapshot = report["holdings_snapshot"].get(pair)
            if snapshot is None:
                result["warnings"].append(f"Opening quantity for {pair} is unknown because its snapshot is missing.")
                continue
            initial = Decimal(snapshot["quantity"]) - quantities.get(pair, Decimal(0))
            # Avoid inventing sub-display-precision opening units from rounding.
            if abs(initial) <= Decimal(snapshot["quantity_precision"]) / 2:
                initial = Decimal(0)
            opening[pair] = str(initial)
            if initial > 0:
                lots[pair] = [{"quantity": str(initial), "cost_usd": None,
                               "source": "inferred opening inventory"}]
            elif initial < 0:
                result["warnings"].append(f"Implied opening quantity for {pair} is negative; review history coverage.")
    result["opening_quantities"] = opening
    result["opening_source"] = "previous published crypto evidence" if previous else "inferred from export snapshot"
    realized = []
    for fill in sorted(result["fills"], key=lambda row: (row["executed_at"], int(row["fill_id"].rsplit("-", 1)[1]))):
        pair, quantity = fill["pair"], Decimal(fill["quantity"])
        rows = lots.setdefault(pair, [])
        principal = Decimal(fill["principal_usd"]) if fill["principal_usd"] is not None else None
        fee = Decimal(fill["fee_usd"]) if fill["fee_usd"] is not None else None
        if fill["side"] == "BUY":
            cost = principal + fee if principal is not None and fee is not None else None
            rows.append({"quantity": str(quantity), "cost_usd": str(cost) if cost is not None else None,
                         "source": fill["fill_id"], "opened_at": fill["executed_at"]})
            continue
        remaining, cost, known = quantity, Decimal(0), True
        while remaining > 0 and rows:
            lot = rows[0]
            lot_quantity = Decimal(lot["quantity"])
            consumed = min(lot_quantity, remaining)
            lot_cost = Decimal(lot["cost_usd"]) if lot["cost_usd"] is not None else None
            allocated = lot_cost * consumed / lot_quantity if lot_cost is not None else None
            if allocated is None:
                known = False
            else:
                cost += allocated
            remaining -= consumed
            lot_quantity -= consumed
            if lot_quantity == 0:
                rows.pop(0)
            else:
                lot["quantity"] = str(lot_quantity)
                lot["cost_usd"] = str(lot_cost - allocated) if allocated is not None else None
        if remaining > 0:
            known = False
            result["warnings"].append(f"Sale of {pair} exceeds available opening/fill history by {remaining} units.")
        net = principal - fee if principal is not None and fee is not None else None
        realized.append({"fill_id": fill["fill_id"], "pair": pair,
                         "net_proceeds_usd": str(net) if net is not None else None,
                         "fifo_cost_usd": str(cost) if known else None,
                         "pnl_usd": str(net - cost) if known and net is not None else None})
    result["ending_lots"] = lots
    result["ending_quantities"] = {pair: str(sum((Decimal(row["quantity"]) for row in rows), Decimal(0)))
                                    for pair, rows in lots.items()}
    result["realized_sales"] = realized
    result["realized_pnl_usd"] = (str(sum((Decimal(row["pnl_usd"]) for row in realized), Decimal(0)))
                                   if all(row["pnl_usd"] is not None for row in realized) else None)
    mismatches = []
    for pair in set(lots) | set(report["holdings_snapshot"]):
        computed = Decimal(result["ending_quantities"].get(pair, "0"))
        snapshot = report["holdings_snapshot"].get(pair)
        if snapshot is None or computed.quantize(Decimal(snapshot["quantity_precision"])) != Decimal(snapshot["quantity"]):
            mismatches.append({"pair": pair, "computed": str(computed),
                               "snapshot": snapshot["quantity"] if snapshot else None})
    result["snapshot_mismatches"] = mismatches
    result["snapshot_quantity_matches"] = not mismatches and bool(report["holdings_snapshot"])
    result["month_end_reconciled"] = False
    result["status"] = "provisional export evidence"
    return result


def serialize_crypto_month(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"
