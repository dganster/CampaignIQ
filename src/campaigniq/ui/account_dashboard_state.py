"""Per-user/account Streamlit widget, filter and session preference namespaces."""
from collections.abc import MutableMapping
from contextlib import AbstractContextManager
from hashlib import sha256
from streamlit.delta_generator import DeltaGenerator


class ScopedState(MutableMapping):
    # Streamlit removes keys belonging to widgets that are not rendered on a
    # later run. Keep account screen values in a non-widget dictionary as well;
    # another account's widgets must never be used as their backing store.
    def __init__(self, state, prefix):
        self.state, self.prefix = state, prefix
        self.saved_key = prefix + '_retained_screen_values'
        if self.saved_key not in state:
            state[self.saved_key] = {}
        self.saved = state[self.saved_key]
    def __getitem__(self, key):
        try:
            value = self.state[self.prefix + str(key)]
        except KeyError:
            return self.saved[str(key)]
        self.saved[str(key)] = value
        return value
    def __setitem__(self, key, value):
        self.state[self.prefix + str(key)] = value
        self.saved[str(key)] = value
    def __delitem__(self, key):
        found = False
        physical = self.prefix + str(key)
        if physical in self.state:
            del self.state[physical]
            found = True
        if str(key) in self.saved:
            del self.saved[str(key)]
            found = True
        if not found:
            raise KeyError(key)
    def __iter__(self):
        physical = {k[len(self.prefix):] for k in self.state
                    if k.startswith(self.prefix) and k != self.saved_key}
        return iter(physical | set(self.saved))
    def __len__(self): return sum(1 for _ in self)


# Preserve screen controls, never button trigger state or uploaded file bytes.
RETAINED_WIDGETS = {'checkbox', 'toggle', 'radio', 'selectbox', 'multiselect',
    'number_input', 'date_input', 'time_input', 'slider', 'select_slider'}


# Assign keys also to previously implicit widgets. All controls within an
# account use this prefix; switching accounts cannot reuse client widget state.
WIDGETS = {'button','checkbox','toggle','radio','selectbox','multiselect','text_input',
    'text_area','number_input','date_input','time_input','slider','select_slider',
    'file_uploader','data_editor','download_button','form','dataframe','plotly_chart'}


class ScopedUI(AbstractContextManager):
    def __init__(self, ui, state, prefix, counter=None, routes=None):
        self._ui, self._prefix = ui, prefix
        self.session_state = ScopedState(state, prefix)
        self._state = state
        self._counter = counter if counter is not None else {}
        # Reporting routes are visit-local and account-specific in this first
        # integration. A browser URL cannot carry another account's filters.
        self.query_params = routes if routes is not None else SessionRoutes(self.session_state)
    def __enter__(self): self._ui.__enter__(); return self
    def __exit__(self, *args): return self._ui.__exit__(*args)
    def __getattr__(self, name):
        value = getattr(self._ui, name)
        if isinstance(value, DeltaGenerator): return self._wrap(value)
        if not callable(value): return value
        def call(*args, **kwargs):
            if name == 'form' and args:
                kwargs['key'] = args[0]
                args = args[1:]
            if name in WIDGETS:
                key = kwargs.get('key')
                if key is None:
                    label = repr(args[:1])
                    token = name + ':' + sha256(label.encode()).hexdigest()[:12]
                    number = self._counter.get(token, 0)
                    self._counter[token] = number + 1
                    key = token + ':' + str(number)
                kwargs['key'] = self._prefix + str(key)
                if name in RETAINED_WIDGETS:
                    physical = kwargs['key']
                    if physical not in self._state and str(key) in self.session_state.saved:
                        self._state[physical] = self.session_state.saved[str(key)]
            result = value(*args, **kwargs)
            if name in RETAINED_WIDGETS:
                # Writing session_state[key] after widget creation is forbidden;
                # update only the separate non-widget backing dictionary here.
                self.session_state.saved[str(key)] = result
            if isinstance(result, DeltaGenerator): return self._wrap(result)
            if isinstance(result, list) and all(isinstance(v, DeltaGenerator) for v in result):
                return [self._wrap(v) for v in result]
            return result
        return call
    def _wrap(self, value): return ScopedUI(value, self._state, self._prefix, self._counter, self.query_params)


class SessionRoutes(MutableMapping):
    def __init__(self, state): self.state = state
    def to_dict(self): return dict(self.state.get('_routes', {}))
    def from_dict(self, value): self.state['_routes'] = dict(value)
    def __getitem__(self, key): return self.to_dict()[key]
    def __setitem__(self, key, value):
        values = self.to_dict(); values[key] = value; self.from_dict(values)
    def __delitem__(self, key):
        values = self.to_dict(); del values[key]; self.from_dict(values)
    def __iter__(self): return iter(self.to_dict())
    def __len__(self): return len(self.to_dict())
