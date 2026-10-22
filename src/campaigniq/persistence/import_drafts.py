"""Persistent, non-authoritative monthly import drafts.

All operations are scoped to a workspace supplied by the authenticated service
layer. Never expose this repository directly to untrusted clients.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from campaigniq.persistence.postgres_artifact_storage import _pool_for_dsn

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_drafts (
    id uuid PRIMARY KEY,
    workspace text NOT NULL,
    year integer NOT NULL CHECK (year BETWEEN 1900 AND 2200),
    month integer NOT NULL CHECK (month BETWEEN 1 AND 12),
    state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'abandoned')),
    revision integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS import_drafts_active_month
    ON import_drafts(workspace, year, month) WHERE state='draft';
CREATE TABLE IF NOT EXISTS import_draft_documents (
    id uuid PRIMARY KEY,
    draft_id uuid NOT NULL REFERENCES import_drafts(id) ON DELETE CASCADE,
    role text NOT NULL,
    filename text NOT NULL,
    media_type text NOT NULL,
    sha256 text NOT NULL,
    content bytea NOT NULL,
    revision integer NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    superseded boolean NOT NULL DEFAULT false,
    CHECK (octet_length(content) <= 20971520)
);
CREATE UNIQUE INDEX IF NOT EXISTS import_draft_documents_current_role
    ON import_draft_documents(draft_id, role) WHERE superseded=false;
"""

@dataclass(frozen=True)
class Draft:
    id: UUID
    workspace: str
    year: int
    month: int
    revision: int

class DraftConflict(ValueError):
    """Explicit user confirmation is required to replace a different file."""

class ImportDraftRepository:
    def __init__(self, dsn: str, workspace: str):
        if not workspace or len(workspace) > 128:
            raise ValueError('invalid workspace')
        self.dsn, self.workspace = dsn, workspace

    def _connect(self):
        return _pool_for_dsn(self.dsn).connection()

    def initialize(self):
        with self._connect() as conn:
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    conn.execute(statement)

    def create_or_resume(self, year: int, month: int) -> Draft:
        if not (1900 <= year <= 2200 and 1 <= month <= 12):
            raise ValueError('invalid reporting month')
        with self._connect() as conn:
            row = conn.execute("""INSERT INTO import_drafts(id,workspace,year,month)
                VALUES (%s,%s,%s,%s) ON CONFLICT (workspace,year,month)
                WHERE state='draft' DO UPDATE SET updated_at=import_drafts.updated_at
                RETURNING id,workspace,year,month,revision""",
                (uuid4(), self.workspace, year, month)).fetchone()
        return Draft(*row)

    def get(self, draft_id: UUID) -> Draft:
        with self._connect() as conn:
            row = conn.execute("""SELECT id,workspace,year,month,revision FROM import_drafts
                WHERE id=%s AND workspace=%s AND state='draft'""",
                (draft_id, self.workspace)).fetchone()
        if row is None:
            raise FileNotFoundError('draft not found')
        return Draft(*row)

    def list_drafts(self) -> tuple[Draft, ...]:
        with self._connect() as conn:
            rows = conn.execute("""SELECT id,workspace,year,month,revision FROM import_drafts
                WHERE workspace=%s AND state='draft' ORDER BY year,month""",
                (self.workspace,)).fetchall()
        return tuple(Draft(*row) for row in rows)

    def upload(self, draft_id: UUID, role: str, filename: str, media_type: str,
               content: bytes, *, replace: bool = False) -> int:
        if not role or len(role) > 128 or not filename or len(filename) > 255:
            raise ValueError('invalid role or filename')
        if media_type not in ('application/pdf', 'text/csv', 'text/plain', 'application/vnd.ms-excel'):
            raise ValueError('unsupported file type')
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError('document must be nonempty and at most 20 MiB')
        digest = sha256(content).hexdigest()
        with self._connect() as conn:
            row = conn.execute("""SELECT revision FROM import_drafts
                WHERE id=%s AND workspace=%s AND state='draft' FOR UPDATE""",
                (draft_id, self.workspace)).fetchone()
            if row is None:
                raise FileNotFoundError('draft not found')
            current = conn.execute("""SELECT sha256 FROM import_draft_documents
                WHERE draft_id=%s AND role=%s AND superseded=false""",
                (draft_id, role)).fetchone()
            if current and current[0] == digest:
                return row[0]
            if current and not replace:
                raise DraftConflict('Different document already exists for this role; confirm replacement')
            next_revision = row[0] + 1
            if current:
                conn.execute("""UPDATE import_draft_documents SET superseded=true
                    WHERE draft_id=%s AND role=%s AND superseded=false""", (draft_id, role))
            conn.execute("""INSERT INTO import_draft_documents
                (id,draft_id,role,filename,media_type,sha256,content,revision)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), draft_id, role, filename, media_type, digest, content, next_revision))
            conn.execute("""UPDATE import_drafts SET revision=%s,updated_at=now()
                WHERE id=%s AND workspace=%s""", (next_revision, draft_id, self.workspace))
        return next_revision

    def document(self, draft_id: UUID, role: str) -> tuple[str, str, bytes]:
        with self._connect() as conn:
            row = conn.execute("""SELECT d.filename,d.media_type,d.content
                FROM import_draft_documents d JOIN import_drafts p ON p.id=d.draft_id
                WHERE p.id=%s AND p.workspace=%s AND p.state='draft'
                  AND d.role=%s AND d.superseded=false""",
                (draft_id, self.workspace, role)).fetchone()
        if row is None:
            raise FileNotFoundError('document not found')
        return (row[0], row[1], bytes(row[2]))

    def abandon(self, draft_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute("""UPDATE import_drafts SET state='abandoned',updated_at=now()
                WHERE id=%s AND workspace=%s AND state='draft'""", (draft_id, self.workspace))
