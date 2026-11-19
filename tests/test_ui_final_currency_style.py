from streamlit.testing.v1 import AppTest
from tests.test_ui_visual_cleanup import app_code
import io
import pyarrow as pa


def test_campaign_loss_uses_minus_before_dollar_without_numeric_config_override():
    app=AppTest.from_string(app_code()).run(timeout=25)
    next(r for r in app.radio if r.label=='Navigate').set_value('Campaigns').run(timeout=25)
    assert not app.exception
    table=next(d for d in app.dataframe if 'Campaign' in d.value.columns)
    displayed=pa.ipc.open_stream(io.BytesIO(getattr(table.proto, 'arrow_data', table.proto).styler.display_values)).read_all().to_pandas()
    assert displayed['Realized P&L'].tolist()==['$1,250.25','-$400.10']
    import json
    assert "format" not in json.loads(table.proto.columns).get("Realized P&L", {}).get("type_config", {})
    # Numeric source remains available for table sorting and row selection.
    assert table.value['Realized P&L'].tolist()==[1250.25,-400.10]
