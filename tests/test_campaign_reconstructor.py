from campaigniq.campaign_reconstructor import CampaignReconstructor


def test_empty_trade_list_returns_no_campaigns():
    reconstructor = CampaignReconstructor()

    campaigns = reconstructor.reconstruct([])

    assert campaigns == []