"""Smoke tests for CampaignIQ."""


from campaigniq.cli import main


def test_cli_exists() -> None:
    """Verify the CLI entry point is importable."""
    assert callable(main)
