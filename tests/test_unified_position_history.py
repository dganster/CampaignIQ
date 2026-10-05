from datetime import date
from campaigniq.ui.history_scope import scope_history, history_coverage

def test_selected_period_uses_activity_date_and_preserves_balance():
 rows=[dict(occurred_at='2025-12-31T12:00:00',position_after='100'),dict(occurred_at='2026-01-02T12:00:00',position_after='80'),dict(occurred_at='2026-04-01T12:00:00',position_after='0')]
 lifecycle=[{'Date':date(2026,3,31)},{'Date':date(2026,4,1)}]
 selected, events=scope_history(rows,lifecycle,'2026-01','2026-03')
 assert selected == [rows[1]] and selected[0] is rows[1]
 assert selected[0]['position_after']=='80'
 assert events==[lifecycle[0]]

def test_all_history_includes_other_years():
 rows=[dict(occurred_at='2025-12-31T00:00:00'),dict(occurred_at='2027-01-01T00:00:00')]
 assert scope_history(rows,[],'2026-01','2026-09',all_history=True)[0]==rows
 assert history_coverage(rows,[])=='Displayed activity: Dec 31, 2025 – Jan 01, 2027.'

def test_empty_coverage():
 assert history_coverage([],[])=='No retained position activity in this history range.'

def test_campaign_highlights_use_selected_evidence_rows():
 from campaigniq.ui.position_history_view import history_rows
 row=dict(instrument={'type':'instrument','symbol':'SPOT'},occurred_at='2026-01-02T00:00:00',action='Open / add',quantity_change='100',position_after='100',price='420',fills=1,source='Trade history',campaign_id='C1')
 other=dict(row,campaign_id='C2',occurred_at='2026-02-01T00:00:00')
 display=history_rows([row,other],lambda i:i.symbol,include_campaign=True,selected_entries=[row])
 assert [r['Selected campaign'] for r in display]==[True,False]
 assert [r['Campaign'] for r in display]==['C1','C2']

def test_campaign_detail_uses_unified_history_and_removes_extra_route():
 from pathlib import Path
 source=Path('src/campaigniq/ui/dashboard.py').read_text()
 assert 'View all {symbol} position history' not in source
 assert 'selected_entries=selected_history' in source
 assert 'frame.style.apply' in source

def test_highlighted_history_renders_as_styled_table():
 import ast
 from pathlib import Path
 from types import SimpleNamespace
 import pandas as pd
 from campaigniq.ui.position_history_view import history_rows
 source=ast.parse(Path('src/campaigniq/ui/dashboard.py').read_text())
 node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='_render_position_history')
 shown=[]
 column=SimpleNamespace(DateColumn=lambda *a,**k:None,TimeColumn=lambda *a,**k:None,NumberColumn=lambda *a,**k:None)
 scope={'pd':pd,'history_rows':history_rows,'_display_instrument':lambda i:i.symbol,
        'st':SimpleNamespace(column_config=column,caption=lambda *a:None,info=lambda *a:None),
        'render_dataframe':lambda st,frame,**k:shown.append(frame)}
 exec(compile(ast.Module(body=[node],type_ignores=[]),'renderer','exec'),scope)
 first=dict(instrument={'type':'instrument','symbol':'SPOT'},occurred_at='2026-01-02T00:00:00',action='Open / add',quantity_change='100',position_after='100',price='420',fills=1,source='Trade history',campaign_id='C1')
 other=dict(first,campaign_id='C2',occurred_at='2026-02-01T00:00:00')
 scope['_render_position_history']([first,other],include_campaign=True,selected_entries=[first])
 assert shown[0].data['Selected campaign'].tolist()==[True,False]
 assert 'background-color: #dbeafe' in shown[0].to_html()
