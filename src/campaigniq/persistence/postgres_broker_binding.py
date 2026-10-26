"""Guarded local broker metadata commands in the existing audited directory."""
from campaigniq.broker_account_binding import bind
from campaigniq.persistence.account_directory_codec import checksum,encode
from campaigniq.persistence.postgres_account_directory import LocalPostgresAccountDirectory
from campaigniq.ui.account_administration_view import validate_demo_directory


class LocalBrokerBindingStore:
    def __init__(self,store):
        if type(store) is not LocalPostgresAccountDirectory:
            raise ValueError('Guarded local directory required')
        self.store=store

    def bind(self,identity,account_id,kind,number,*,expected_revision,confirm_replace=False):
        from psycopg.types.json import Jsonb
        with self.store._connection() as conn:
            current=self.store._load(conn,lock=True)
            validate_demo_directory(current,self.store.registry_id)
            updated=bind(current,identity,account_id,kind,number,expected_revision=expected_revision,
                         confirm_replace=confirm_replace)
            if updated==current: return current.revision
            result=conn.execute('''UPDATE campaigniq_admin_dev.directories
                SET revision=%s,document=%s,document_sha256=%s,updated_at=now()
                WHERE registry_id=%s AND revision=%s''',
                (updated.revision,Jsonb(encode(updated)),checksum(encode(updated)),self.store.registry_id,current.revision))
            if result.rowcount!=1: raise ValueError('Directory changed; reload before binding')
            self.store._append_event(conn,updated.events[-1])
            if self.store._load(conn)!=updated:
                raise ValueError('Broker binding write verification differs')
        return updated.revision
