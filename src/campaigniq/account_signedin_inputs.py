"""Bound monthly input service for real server-authenticated LOCAL sessions.

Reuses the existing account-scoped draft/preflight/document-identity behavior.
Only the exact new signed-in gateway is accepted; synthetic gateway guards and
publication entry-point type checks remain unchanged.
"""
from campaigniq.account_import_preview import BoundAccountImportPreviewService
from campaigniq.persistence.signedin_account_data import LocalSignedInInputGateway


class SignedInMonthlyInputs(BoundAccountImportPreviewService):
    def __init__(self, gateway):
        if type(gateway) is not LocalSignedInInputGateway:
            raise ValueError('Real-sign-in local input gateway required')
        self.gateway = gateway

    # Inherited methods' first identity argument must be st.user here. The
    # gateway accepts only server-session claims and authorizes every call;
    # passing an Identity object or caller-supplied claims dict is denied.
