"""A completed trading decision."""

from __future__ import annotations

from dataclasses import dataclass

from campaigniq.domain.leg import Leg


@dataclass(frozen=True, slots=True)
class Trade:
    """One completed trade consisting of one or more option legs."""

    legs: tuple[Leg, ...]
