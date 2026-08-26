from decimal import Decimal

from campaigniq.domain.position_effect import PositionEffect

from campaigniq.importers.thinkorswim.forex_position_effect_resolver import (
    ForexPositionEffect,
    ForexPositionEffectResolver,
)

def test_flat_position_buy_opens() -> None:
    resolver = ForexPositionEffectResolver()

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("50000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("50000"),
        ),
    )


def test_flat_position_sell_opens() -> None:
    resolver = ForexPositionEffectResolver()

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("-50000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("-50000"),
        ),
    )


def test_long_position_sell_closes() -> None:
    resolver = ForexPositionEffectResolver(
        initial_positions={"EUR/USD": Decimal("50000")}
    )

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("-50000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-50000"),
        ),
    )


def test_short_position_buy_closes() -> None:
    resolver = ForexPositionEffectResolver(
        initial_positions={"EUR/USD": Decimal("-50000")}
    )

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("50000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("50000"),
        ),
    )


def test_same_direction_adds_to_position() -> None:
    resolver = ForexPositionEffectResolver(
        initial_positions={"EUR/USD": Decimal("50000")}
    )

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("50000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("50000"),
        ),
    )


def test_partial_close_reduces_existing_position() -> None:
    resolver = ForexPositionEffectResolver(
        initial_positions={"EUR/USD": Decimal("100000")}
    )

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("-40000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-40000"),
        ),
    )


def test_reversal_splits_close_and_open() -> None:
    resolver = ForexPositionEffectResolver(
        initial_positions={"EUR/USD": Decimal("50000")}
    )

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("-100000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-50000"),
        ),
        ForexPositionEffect(
            position_effect=PositionEffect.OPEN,
            quantity=Decimal("-50000"),
        ),
    )
def test_resolver_tracks_resulting_position() -> None:
    resolver = ForexPositionEffectResolver()

    resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("50000"),
    )

    resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("-20000"),
    )

    effects = resolver.resolve(
        pair="EUR/USD",
        quantity=Decimal("-30000"),
    )

    assert effects == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-30000"),
        ),
    )
def test_resolver_can_seed_multiple_opening_positions() -> None:
    resolver = ForexPositionEffectResolver(
        initial_positions={
            "USD/JPY": Decimal("20000"),
            "USD/MXN": Decimal("-60000"),
        }
    )

    usd_jpy = resolver.resolve(
        pair="USD/JPY",
        quantity=Decimal("-10000"),
    )

    usd_mxn = resolver.resolve(
        pair="USD/MXN",
        quantity=Decimal("50000"),
    )

    assert usd_jpy == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("-10000"),
        ),
    )

    assert usd_mxn == (
        ForexPositionEffect(
            position_effect=PositionEffect.CLOSE,
            quantity=Decimal("50000"),
        ),
    )