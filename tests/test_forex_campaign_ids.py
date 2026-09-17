from campaigniq.domain.campaign import Campaign
from campaigniq.import_pipeline import PeriodImportPipeline


def _raw_campaign(campaign_id: str) -> Campaign:
    return Campaign(campaign_id=campaign_id, trades=())


def test_forex_campaign_ids_use_dedicated_namespace() -> None:
    raw = (
        _raw_campaign("CAMP-000001"),
        _raw_campaign("CAMP-000002"),
    )

    result = PeriodImportPipeline._namespace_forex_campaigns(raw)

    assert tuple(campaign.campaign_id for campaign in result) == (
        "FX-CAMP-000001",
        "FX-CAMP-000002",
    )


def test_forex_campaign_ids_do_not_depend_on_non_forex_campaign_count() -> None:
    raw = (_raw_campaign("CAMP-000001"),)

    # The helper deliberately has no non-FOREX count/start-ID input. The same
    # reconstructed FOREX campaign therefore receives the same namespace
    # regardless of how many non-FOREX campaigns exist in the period.
    first = PeriodImportPipeline._namespace_forex_campaigns(raw)
    second = PeriodImportPipeline._namespace_forex_campaigns(raw)

    assert first[0].campaign_id == "FX-CAMP-000001"
    assert second[0].campaign_id == first[0].campaign_id
