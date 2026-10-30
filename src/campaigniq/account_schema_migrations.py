"""Versioned account schema foundation. Explicit caller-owned transaction only.

No connection, bootstrap, commit, role grant or application activation on import.
Legacy public tables and development schemas are never migration targets.
"""
from hashlib import sha256
import json

SCHEMA = 'campaigniq_accounts'
VERSION = 1
SQL = """
CREATE SCHEMA campaigniq_accounts;
REVOKE ALL ON SCHEMA campaigniq_accounts FROM PUBLIC;
CREATE TABLE campaigniq_accounts.directories (
 registry_id uuid PRIMARY KEY, revision bigint NOT NULL CHECK(revision>=0),
 document jsonb NOT NULL, document_sha256 text NOT NULL CHECK(length(document_sha256)=64),
 updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE campaigniq_accounts.audit_events (
 registry_id uuid NOT NULL REFERENCES campaigniq_accounts.directories(registry_id),
 sequence bigint NOT NULL CHECK(sequence>0), event jsonb NOT NULL,
 event_sha256 text NOT NULL CHECK(length(event_sha256)=64),
 recorded_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(registry_id,sequence));
CREATE TABLE campaigniq_accounts.account_preferences (
 registry_id uuid NOT NULL REFERENCES campaigniq_accounts.directories(registry_id),
 user_id uuid NOT NULL, account_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), document jsonb NOT NULL,
 document_sha256 text NOT NULL CHECK(length(document_sha256)=64),
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(registry_id,user_id,account_id));
CREATE TABLE campaigniq_accounts.artifact_storage_dev (
 namespace text NOT NULL, artifact_key text NOT NULL, content text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(namespace,artifact_key));
CREATE TABLE campaigniq_accounts.import_drafts (
 id uuid PRIMARY KEY, workspace text NOT NULL,
 year integer NOT NULL CHECK(year BETWEEN 1900 AND 2200),
 month integer NOT NULL CHECK(month BETWEEN 1 AND 12),
 state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','abandoned')),
 revision integer NOT NULL DEFAULT 0 CHECK(revision>=0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE UNIQUE INDEX import_drafts_active_month ON campaigniq_accounts.import_drafts(workspace,year,month)
 WHERE state='draft';
CREATE TABLE campaigniq_accounts.import_draft_documents (
 id uuid PRIMARY KEY, draft_id uuid NOT NULL REFERENCES campaigniq_accounts.import_drafts(id) ON DELETE CASCADE,
 role text NOT NULL, filename text NOT NULL, media_type text NOT NULL,
 sha256 text NOT NULL, content bytea NOT NULL, revision integer NOT NULL,
 uploaded_at timestamptz NOT NULL DEFAULT now(), superseded boolean NOT NULL DEFAULT false,
 CHECK(octet_length(content)<=20971520));
CREATE UNIQUE INDEX import_draft_documents_current_role
 ON campaigniq_accounts.import_draft_documents(draft_id,role) WHERE superseded=false;
CREATE TABLE campaigniq_accounts.schema_migrations (
 version integer PRIMARY KEY, source_sha256 text NOT NULL CHECK(length(source_sha256)=64),
 catalog_sha256 text NOT NULL CHECK(length(catalog_sha256)=64),
 applied_at timestamptz NOT NULL DEFAULT now());
REVOKE ALL ON ALL TABLES IN SCHEMA campaigniq_accounts FROM PUBLIC;
"""
SOURCE_SHA256 = sha256(SQL.encode()).hexdigest()
TABLES = frozenset(('directories','audit_events','account_preferences','artifact_storage_dev',
                   'import_drafts','import_draft_documents','schema_migrations'))


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def catalog_digest(conn):
    """Detect changes to columns, constraints, indexes, owners or privileges."""
    statements = (
      "SELECT nspname,pg_get_userbyid(nspowner),nspacl::text FROM pg_namespace WHERE nspname=%s",
      "SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner),c.relacl::text,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname",
      "SELECT c.relname,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
      "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,k.conname",
      "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=%s ORDER BY indexname",
      "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
      "SELECT p.proname,pg_get_function_identity_arguments(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY 1,2",
      "SELECT tablename,policyname,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname=%s ORDER BY tablename,policyname",
    )
    return digest([conn.execute(sql,(SCHEMA,)).fetchall() for sql in statements])


def apply_migrations(conn):
    """Apply inside caller transaction; never commits. Caller must propagate failure.

    Existing untracked schemas are rejected, never adopted with IF NOT EXISTS.
    No runtime grant is issued: deployment role privileges are an activation task.
    """
    from psycopg.pq import TransactionStatus
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('Explicit active migration transaction required')
    conn.execute("SET LOCAL search_path=pg_catalog")
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('campaigniq_accounts:migrations',0))")
    exists=conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone()
    if exists:
        verify_schema(conn)
        return False
    for statement in SQL.split(';'):
        if statement.strip():conn.execute(statement)
    signature=catalog_digest(conn)
    conn.execute('INSERT INTO campaigniq_accounts.schema_migrations(version,source_sha256,catalog_sha256) VALUES(%s,%s,%s)',
                 (VERSION,SOURCE_SHA256,signature))
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('Migration transaction failed')
    return True


def verify_schema(conn):
    """Read-only compatibility check. Never initializes or migrates a schema."""
    conn.execute("SET LOCAL search_path=pg_catalog")
    tables={row[0] for row in conn.execute(
      "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p')",(SCHEMA,)).fetchall()}
    if tables != TABLES:raise ValueError('Untracked or incomplete account schema; no adoption')
    rows=conn.execute('SELECT version,source_sha256,catalog_sha256 FROM campaigniq_accounts.schema_migrations ORDER BY version').fetchall()
    if rows != [(VERSION,SOURCE_SHA256,catalog_digest(conn))]:
        raise ValueError('Migration source, version or schema integrity differs')
