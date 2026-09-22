from campaigniq.ui.streamlit_identity import oidc_claims_from_streamlit_user


class FakeStreamlitUser:
    def __init__(self, claims):
        self._claims = claims

    def to_dict(self):
        return self._claims


def test_returns_streamlit_oidc_claims() -> None:
    claims = {
        "sub": "google-subject-123",
        "email": "person@example.com",
        "name": "Example Person",
    }

    assert oidc_claims_from_streamlit_user(FakeStreamlitUser(claims)) == claims


def test_missing_to_dict_fails_closed() -> None:
    assert oidc_claims_from_streamlit_user(object()) == {}


def test_non_mapping_claims_fail_closed() -> None:
    assert oidc_claims_from_streamlit_user(FakeStreamlitUser("not-claims")) == {}
