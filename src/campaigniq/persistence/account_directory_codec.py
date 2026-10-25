"""Strict versioned serialization for the immutable administration directory."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from campaigniq.account_administration import (
    Account, AuditEvent, Directory, Grant, Identity, Permission, User,
)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checksum(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def encode(directory: Directory) -> dict:
    if not isinstance(directory, Directory):
        raise ValueError('Expected a validated Directory')
    # JSON normalization converts tuples and enum values to their wire forms.
    return json.loads(canonical(dict(format='campaigniq.account_directory', version=1,
                                    **asdict(directory))))


def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected.split()):
        raise ValueError('Unexpected directory fields')
    return value


def array(value):
    if type(value) is not list:
        raise ValueError('Expected a directory array')
    return value


def decode(value: dict) -> Directory:
    fields(value, 'format version users accounts grants events')
    if value['format'] != 'campaigniq.account_directory' or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported directory format')
    users = []
    for row in array(value['users']):
        fields(row, 'id identity name workspace_id admin enabled')
        identity = Identity(**fields(row['identity'], 'issuer subject'))
        users.append(User(**(row | dict(identity=identity))))
    accounts = tuple(Account(**fields(row, 'id owner_id workspace_id name broker archived legacy'))
                     for row in array(value['accounts']))
    grants = []
    for row in array(value['grants']):
        fields(row, 'user_id account_id permission')
        grants.append(Grant(**(row | dict(permission=Permission(row['permission'])))))
    events = []
    for row in array(value['events']):
        fields(row, 'sequence actor_id action target_id details')
        details = array(row['details'])
        if any(type(pair) is not list or len(pair) != 2 for pair in details):
            raise ValueError('Invalid audit details')
        events.append(AuditEvent(**(row | dict(details=tuple(tuple(pair) for pair in details)))))
    return Directory(tuple(users), accounts, tuple(grants), tuple(events))


def loads(raw: str) -> Directory:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate directory JSON field')
            result[key] = value
        return result

    return decode(json.loads(raw, object_pairs_hook=unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON'))))


def event_document(event: AuditEvent) -> dict:
    return json.loads(canonical(asdict(event)))
