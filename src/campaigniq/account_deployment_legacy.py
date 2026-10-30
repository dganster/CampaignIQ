"""Retained public workspace reads through the deployment account gateway."""
from datetime import date
from campaigniq.account_legacy_access import LocalLegacyAccountAccess
from campaigniq.account_legacy_results import _results,published_months
from campaigniq.persistence.deployment_account_data import PostgresAccountDataGateway


class DeploymentLegacyAccountAccess:
    def __init__(self,gateway):
        if type(gateway) is not PostgresAccountDataGateway:raise ValueError('Deployment gateway required')
        self.gateway=gateway

    def _session(self,user,account_id):return self.gateway.legacy_transaction(user,account_id)

    def choices(self,user):return tuple(a for a in self.gateway.overview(user) if a.legacy)

    def inventory(self,user,account_id):
        # This existing implementation needs only an authorized _session and
        # preserves all original versions and exact adapter/source fingerprints.
        return LocalLegacyAccountAccess.inventory(self,user,account_id)

    def published_months(self,user,account_id):
        with self._session(user,account_id) as (_,session,_):return published_months(session.artifacts)

    def published_results(self,user,account_id,year,month):
        if type(year) is not int or type(month) is not int or not 1900<=year<=2200:
            raise ValueError('Supported integer reporting month required')
        date(year,month,1)
        with self._session(user,account_id) as (conn,session,_):
            prefix=f'{year:04d}-{month:02d}-'
            direct=conn.execute('''SELECT artifact_key,content FROM public.artifact_storage_dev
                WHERE namespace=%s AND starts_with(artifact_key,%s) ORDER BY artifact_key''',
                (session.account.storage_scope,prefix)).fetchall()
            adapter=[(key,session.artifacts.read_text(key)) for key in session.artifacts.list_keys(prefix=prefix)]
            if direct!=adapter:raise ValueError('Retained source adapter parity differs')
            return _results(session.artifacts,account_id,year,month,direct)
