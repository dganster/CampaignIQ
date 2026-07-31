from campaigniq.domain.value_objects.equity import Equity


def test_equities_with_same_symbol_are_equal():
    ibm1 = Equity("IBM")
    ibm2 = Equity("IBM")

    assert ibm1 == ibm2
