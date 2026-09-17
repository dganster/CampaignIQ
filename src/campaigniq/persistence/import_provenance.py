"""Durable provenance for one finalized CampaignIQ monthly import."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from campaigniq.import_contract import MonthlyInputRole
from campaigniq.persistence.artifact_storage import ArtifactStorage


IMPORT_PROVENANCE_FORMAT = "campaigniq.monthly_import_provenance"
IMPORT_PROVENANCE_VERSION = 1


@dataclass(frozen=True, slots=True)
class MonthlyInputProvenance:
    role: MonthlyInputRole
    sha256: str
    byte_size: int


def _fingerprint(path: str | Path) -> tuple[str, int]:
    source = Path(path)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def capture_monthly_input_provenance(
    supplied_inputs: Mapping[MonthlyInputRole, str | Path],
) -> tuple[MonthlyInputProvenance, ...]:
    """Fingerprint the exact user-supplied bytes by semantic input role."""
    records = []
    for role, path in sorted(supplied_inputs.items(), key=lambda item: item[0].value):
        sha256, byte_size = _fingerprint(path)
        records.append(
            MonthlyInputProvenance(
                role=role,
                sha256=sha256,
                byte_size=byte_size,
            )
        )
    return tuple(records)


def serialize_monthly_import_provenance(
    *,
    period_start: date,
    period_end: date,
    inputs: tuple[MonthlyInputProvenance, ...],
) -> str:
    """Serialize the deterministic manifest independently of storage I/O."""
    payload = {
        "format": IMPORT_PROVENANCE_FORMAT,
        "version": IMPORT_PROVENANCE_VERSION,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "inputs": [
            {
                "role": item.role.value,
                "sha256": item.sha256,
                "byte_size": item.byte_size,
            }
            for item in inputs
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def save_monthly_import_provenance_to_storage(
    storage: ArtifactStorage,
    key: str,
    *,
    period_start: date,
    period_end: date,
    inputs: tuple[MonthlyInputProvenance, ...],
) -> None:
    """Persist provenance through the provider-neutral storage boundary."""
    storage.write_text(
        key,
        serialize_monthly_import_provenance(
            period_start=period_start,
            period_end=period_end,
            inputs=inputs,
        ),
    )


def save_monthly_import_provenance(
    path: str | Path,
    *,
    period_start: date,
    period_end: date,
    inputs: tuple[MonthlyInputProvenance, ...],
) -> Path:
    """Persist a deterministic manifest without copying private broker contents."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        serialize_monthly_import_provenance(
            period_start=period_start,
            period_end=period_end,
            inputs=inputs,
        ),
        encoding="utf-8",
    )
    return target
