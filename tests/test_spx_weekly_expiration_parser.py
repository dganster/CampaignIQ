from campaigniq.importers.thinkorswim.cash_balance_event import (
    _EXP_DESCRIPTION_RE,
)


def test_spx_weekly_put_expiration_description():
    description = (
        "BOT 10.0 SPX 100 (Weeklys) 26 MAR 25 5760 PUT "
        "UPON PUT S & P 500 INDEX $5760 EXP 03/26/25"
    )

    match = _EXP_DESCRIPTION_RE.match(description)

    assert match is not None
    assert match.group("action") == "BOT"
    assert match.group("quantity") == "10.0"
    assert match.group("symbol") == "SPX"


def test_original_expiration_description_still_supported():
    match = _EXP_DESCRIPTION_RE.match("SOLD 5.0 SPY UPON EXPIRATION")

    assert match is not None
    assert match.group("symbol") == "SPY"


def test_unrecognized_expiration_description_rejected():
    assert _EXP_DESCRIPTION_RE.match(
        "BOT 10.0 SPX UNRECOGNIZED DESCRIPTION"
    ) is None
