"""Administrator-only readable view of sanitized server timing snapshots."""
import json
from campaigniq.performance_monitor import snapshot

_LABELS={'kind':'Type','name':'Page or operation','profiled':'Detailed profiler','count':'Samples','errors':'Errors',
    'p50_seconds':'Median (s)','p95_seconds':'95th percentile (s)','max_seconds':'Maximum (s)','last_seconds':'Last (s)',
    'view':'Page','stage':'Stage'}

def rows(values):
    return [{_LABELS.get(k,k):v for k,v in row.items()} for row in sorted(values,key=lambda x:x.get('p95_seconds',0),reverse=True)]

def render(st,*,administrator):
    report=snapshot(administrator=administrator)
    with st.expander('Performance statistics',expanded=True):
        st.caption(report['scope'])
        st.caption(f"{report['events_retained']:,} retained samples. Oldest: {report['oldest_timestamp'] or 'none'}. This snapshot excludes the current page run until it finishes.")
        if not report['events_retained']:
            st.info('No completed measurements yet. Visit a few pages, then show performance statistics again.')
        else:
            st.write('Pages and operations — slowest 95th percentile first')
            st.dataframe(rows(report['summary']),hide_index=True,use_container_width=True)
            st.write('Stages — nested times overlap; do not add them together')
            st.dataframe(rows(report['stage_summary']),hide_index=True,use_container_width=True)
            st.caption('With few samples, the 95th percentile may equal the maximum. Detailed profiler samples are listed separately.')
        st.download_button('Download performance statistics (JSON)',json.dumps(report,indent=2),
            file_name='campaigniq-performance.json',mime='application/json',key='performance-statistics-download',on_click='ignore')
