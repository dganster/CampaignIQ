from datetime import date

from campaigniq.persistence.monthly_publication import (
    ensure_publication_protocol,
    finalized_month_marker_path,
    is_month_published,
    publication_cutover,
    publish_finalized_month_marker,
)


def test_protocol_cutover_preserves_legacy_months_and_protects_new_months(
    tmp_path,
) -> None:
    ensure_publication_protocol(
        tmp_path,
        first_period_end=date(2026, 8, 31),
    )

    assert publication_cutover(tmp_path) == date(2026, 8, 31)
    assert is_month_published(tmp_path, period_end=date(2026, 7, 31))
    assert not is_month_published(tmp_path, period_end=date(2026, 8, 31))

    marker = publish_finalized_month_marker(
        tmp_path,
        period_end=date(2026, 8, 31),
    )
    assert marker == finalized_month_marker_path(
        tmp_path,
        period_end=date(2026, 8, 31),
    )
    assert is_month_published(tmp_path, period_end=date(2026, 8, 31))


def test_existing_protocol_cutover_is_not_moved_forward(tmp_path) -> None:
    ensure_publication_protocol(
        tmp_path,
        first_period_end=date(2026, 8, 31),
    )
    ensure_publication_protocol(
        tmp_path,
        first_period_end=date(2026, 9, 30),
    )

    assert publication_cutover(tmp_path) == date(2026, 8, 31)
    assert not is_month_published(tmp_path, period_end=date(2026, 9, 30))
