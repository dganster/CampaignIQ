from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from campaigniq import account_committed_publication_rehearsal as r

@pytest.mark.parametrize('name',['postgres',r.DATABASE,'campaigniq_pub_rehearsal_123','campaigniq_pub_rehearsal_'+'A'*32,None])
def test_only_generated_clone_names_are_cleanup_candidates(name):
    with pytest.raises(ValueError):r.clone_name(name)


def test_database_replacement_or_owner_change_stops_cleanup_before_drop():
    class Control:
        def __init__(self,row):self.row=row;self.calls=[]
        def execute(self,query,args=None):self.calls.append(str(query));return SimpleNamespace(fetchone=lambda:self.row)
    for row in (None,(999,'postgres'),(123,'different_owner')):
        conn=Control(row)
        with pytest.raises(ValueError,match='identity differs'):r.cleanup_clone(conn,'campaigniq_pub_rehearsal_'+'a'*32,123,{}, {})
        assert not any(q.startswith('DROP') for q in conn.calls)


def test_changed_copied_rows_or_role_oid_prevent_database_removal():
    class Control:
        def __init__(self):self.calls=[]
        def execute(self,query,args=None):
            self.calls.append(str(query));return SimpleNamespace(fetchone=lambda:(123,'postgres') if 'pg_database' in str(query) else (999,))
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
    control=Control()
    with patch('psycopg.connect',return_value=Conn()),patch.object(r,'inventory',return_value={'public.original':'different'}),pytest.raises(ValueError,match='Copied source'):
        r.cleanup_clone(control,'campaigniq_pub_rehearsal_'+'a'*32,123,{}, {'public.original':'old'})
    with patch('psycopg.connect',return_value=Conn()),patch.object(r,'inventory',return_value={'public.original':'old'}),pytest.raises(ValueError,match='role identity'):
        r.cleanup_clone(control,'campaigniq_pub_rehearsal_'+'a'*32,123,{'ciq_commit_input_'+'a'*16:456},{'public.original':'old'})
    assert not any(q.startswith('DROP') for q in control.calls)


def test_successful_cleanup_drops_only_exact_clone_and_matching_roles_without_force():
    class Control:
        def __init__(self):self.calls=[]
        def execute(self,query,args=None):
            text=query.as_string() if hasattr(query,'as_string') else query
            self.calls.append(text)
            return SimpleNamespace(fetchone=lambda:(123,'postgres') if 'pg_database' in text else (456,))
        @contextmanager
        def transaction(self):yield
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
    conn=Control();name='campaigniq_pub_rehearsal_'+'a'*32;role='ciq_commit_input_'+'a'*16
    with patch('psycopg.connect',return_value=Conn()),patch.object(r,'inventory',return_value={'public.original':'same'}):
        r.cleanup_clone(conn,name,123,{role:456},{'public.original':'same'})
    drops=[q for q in conn.calls if q.startswith('DROP')]
    assert drops==['DROP DATABASE "'+name+'"','DROP ROLE "'+role+'"']
    assert all('FORCE' not in q and 'CASCADE' not in q and r.DATABASE not in q for q in drops)
