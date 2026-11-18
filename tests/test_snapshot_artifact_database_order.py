"""The bounded cache must keep exactly the database order, not Python's order."""
from types import SimpleNamespace
import pytest
from tests.test_snapshot_artifact_batch import Connection,opened

class DatabaseOrder(Connection):
    def __init__(self):
        super().__init__()
        self.rows['scope-a']={'z-result':'Z','a-result':'A','a!detail':'punctuation','é-result':'unicode'}
    def execute(self,sql,args=None):
        values=self.rows.get(args[0],{}) if args else {}
        if sql.startswith('SELECT artifact_key,content'):
            self.queries.append(sql);return SimpleNamespace(fetchall=lambda:list(values.items()))
        if sql.startswith('SELECT artifact_key FROM'):
            self.queries.append(sql);return SimpleNamespace(fetchall=lambda:[(k,) for k in values])
        return super().execute(sql,args)

def test_cached_and_fallback_keys_keep_identical_sql_order():
    s,c=opened(DatabaseOrder());expected=tuple(c.rows['scope-a'])
    assert expected!=tuple(sorted(expected))
    assert s.artifacts.list_keys()==expected
    cached_queries=len(c.queries)
    assert s.artifacts.list_keys()==expected and len(c.queries)==cached_queries
    fallback,_=opened(DatabaseOrder());fallback.artifacts.MAX_ENTRIES=0
    assert fallback.artifacts.list_keys()==expected
    assert [(k,s.artifacts.read_text(k)) for k in expected]==list(c.rows['scope-a'].items())

@pytest.mark.parametrize('prefix,suffix',[('a',''),('','result'),('a','result'),('absent','')])
def test_filters_keep_database_order_as_a_subsequence(prefix,suffix):
    s,c=opened(DatabaseOrder())
    assert s.artifacts.list_keys(prefix=prefix,suffix=suffix)==tuple(k for k in c.rows['scope-a'] if k.startswith(prefix) and k.endswith(suffix))

def test_order_fix_does_not_accept_different_source_bytes():
    s,c=opened(DatabaseOrder());c.inconsistent=True
    with pytest.raises(ValueError,match='inventory differs'):s.artifacts.list_keys()
    assert s.artifacts._snapshot_rows is None
