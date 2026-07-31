from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Account:
    """A brokerage or retirement account that holds positions."""

    name: str
