from campaigniq.domain.value_objects.instrument import Instrument


def test_equities_with_same_symbol_are_equal():
    ibm1 = Instrument("IBM")
    ibm2 = Instrument("IBM")

    assert ibm1 == ibm2


def test_different_symbols_are_not_equal():
    assert Instrument("IBM") != Instrument("AAPL")
