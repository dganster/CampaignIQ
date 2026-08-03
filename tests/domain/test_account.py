from campaigniq.domain.account import Account


def test_account():
    account = Account(name="Dennis Brokerage")

    assert account.name == "Dennis Brokerage"

