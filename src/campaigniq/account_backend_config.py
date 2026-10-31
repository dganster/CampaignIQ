"""Explicit trusted server configuration; no environment discovery or connections."""
from dataclasses import dataclass
import ipaddress
import os
import re
from types import MappingProxyType
from uuid import UUID
from campaigniq.account_administration import Identity, LegacyOwner, propose_legacy_directory
from campaigniq.persistence.account_directory_codec import checksum


@dataclass(frozen=True)
class AccountDirectorySettings:
    registry_id: str
    trusted_issuer: str
    legacy_authorization: tuple[tuple[str,str], ...]

    def __post_init__(self):
        if not isinstance(self.registry_id,str) or str(UUID(self.registry_id))!=self.registry_id:
            raise ValueError('Explicit canonical registry UUID required')
        Identity(self.trusted_issuer,'configuration-validation')
        pairs=self.legacy_authorization
        if type(pairs) is not tuple or not pairs or any(type(p) is not tuple or len(p)!=2 for p in pairs):
            raise ValueError('Immutable complete legacy authorization required')
        subjects=set();scopes=set()
        for subject,scope in pairs:
            Identity(self.trusted_issuer,subject)
            if not isinstance(scope,str) or not re.fullmatch(r'workspace-[A-Za-z0-9][A-Za-z0-9_-]{0,95}',scope):
                raise ValueError('Invalid legacy storage namespace')
            if subject in subjects or scope in scopes:raise ValueError('Ambiguous legacy ownership')
            subjects.add(subject);scopes.add(scope)
        if pairs!=tuple(sorted(pairs)):raise ValueError('Canonical sorted legacy mapping required')

    @property
    def authorization(self):return MappingProxyType(dict(self.legacy_authorization))

    @property
    def sha256(self):
        return checksum(dict(registry_id=self.registry_id,trusted_issuer=self.trusted_issuer,
                             legacy_authorization=self.legacy_authorization))


@dataclass(frozen=True)
class AccountDatabaseTarget:
    """Reviewed remote endpoint and least-privilege runtime role, never UI input."""
    host: str
    database: str
    user: str
    port: int = 5432
    transport: str = "verified-tls"

    def __post_init__(self):
        if (not isinstance(self.host,str) or len(self.host)>253 or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*',self.host) or
            self.host.lower() in ('localhost','localhost.localdomain') or self.host.endswith('.localhost')):
            raise ValueError('Explicit single remote database hostname required')
        try:
            address=ipaddress.ip_address(self.host)
        except ValueError:pass
        else:
            if address.is_loopback or address.is_unspecified or address.is_multicast:
                raise ValueError('Remote deployment endpoint required')
        for value in (self.database,self.user):
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}',value):
                raise ValueError('Explicit database and runtime role required')
        if self.transport not in ('verified-tls','render-private-tls'):
            raise ValueError('Explicit reviewed database transport required')
        if self.transport=='render-private-tls' and not re.fullmatch(r'dpg-[a-z0-9]+-[a-z]',self.host):
            raise ValueError('Render private TLS requires an exact internal database hostname')
        if type(self.port) is not int or not 1<=self.port<=65535:raise ValueError('Invalid database port')

    def conninfo(self,dsn):
        """Reject libpq routing overrides and ambiguous or differently pinned DSNs."""
        if any(os.environ.get(key) for key in ('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGOPTIONS')):
            raise ValueError('PostgreSQL routing overrides are blocked')
        from psycopg.conninfo import conninfo_to_dict,make_conninfo
        try:values=conninfo_to_dict(dsn)
        except Exception:raise ValueError('Invalid account database settings') from None
        allowed={'host','port','dbname','user','password','sslmode','sslrootcert','connect_timeout','application_name'}
        mode='require' if self.transport=='render-private-tls' else 'verify-full'
        expected=dict(host=self.host,port=str(self.port),dbname=self.database,user=self.user,sslmode=mode)
        if set(values)-allowed or any(values.get(k)!=v for k,v in expected.items()):
            raise ValueError('Account database endpoint, role or verified TLS differs')
        values.update(connect_timeout='5',application_name='campaigniq_accounts')
        return make_conninfo(**values)
