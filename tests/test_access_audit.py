import logging

from campaigniq.ui.access_audit import audit_unauthorized_oidc_identity


def test_audit_records_only_unauthorized_oidc_subject(caplog) -> None:
    claims = {
        "sub": "google-subject-andrew",
        "email": "andrew@example.com",
        "name": "Andrew Example",
        "access_token": "must-not-be-logged",
        "id_token": "must-not-be-logged-either",
    }

    with caplog.at_level(
        logging.WARNING,
        logger="campaigniq.ui.access_audit",
    ):
        audit_unauthorized_oidc_identity(claims)

    assert len(caplog.records) == 1

    message = caplog.records[0].getMessage()

    assert "google-subject-andrew" in message
    assert "andrew@example.com" not in message
    assert "Andrew Example" not in message
    assert "must-not-be-logged" not in message
    assert "must-not-be-logged-either" not in message


def test_audit_does_nothing_without_oidc_subject(caplog) -> None:
    with caplog.at_level(
        logging.WARNING,
        logger="campaigniq.ui.access_audit",
    ):
        audit_unauthorized_oidc_identity(
            {
                "email": "unknown@example.com",
                "access_token": "must-not-be-logged",
            }
        )

    assert caplog.records == []
