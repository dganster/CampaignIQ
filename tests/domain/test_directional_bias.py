from campaigniq.domain.directional_bias import DirectionalBias


def test_directional_bias_values():
    assert DirectionalBias.BULLISH.value == "BULLISH"
    assert DirectionalBias.BEARISH.value == "BEARISH"
    assert DirectionalBias.NEUTRAL.value == "NEUTRAL"
    