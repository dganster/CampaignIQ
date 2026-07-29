import pytest

from campaigniq.domain.option_type import OptionType
from campaigniq.domain.side import Side
from campaigniq.importers.thinkorswim.parsers import (
    parse_option_type,
    parse_side,
)


def test_parse_option_type_call() -> None:
    assert parse_option_type("CALL") is OptionType.CALL


def test_parse_option_type_put() -> None:
    assert parse_option_type("PUT") is OptionType.PUT


def test_parse_side_buy() -> None:
    assert parse_side("BUY") is Side.BUY


def test_parse_side_sell() -> None:
    assert parse_side("SELL") is Side.SELL


def test_parse_option_type_invalid() -> None:
    with pytest.raises(ValueError):
        parse_option_type("INVALID")


def test_parse_side_invalid() -> None:
    with pytest.raises(ValueError):
        parse_side("INVALID")
