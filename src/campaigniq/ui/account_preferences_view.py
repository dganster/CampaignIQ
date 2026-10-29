"""Adapt personal preference controls without granting financial artifact writes."""
import json
from campaigniq.access import AccessContext
from campaigniq.account_preferences import PreferenceIntegrityError
from campaigniq.ui.preferences import DEFAULT_PREFERENCES, preference_key, render_preferences, validate_preferences


class PreferenceStorage:
    def __init__(self,service,user,account_id,identity,snapshot):
        self.service,self.user,self.account_id=service,user,account_id
        self.key=preference_key(identity)
        self.saved=service.load(user,account_id)
        self.revision=self.saved.revision
        self.snapshot=snapshot
    def _key(self,key):
        if key!=self.key:raise PermissionError('Only this user display preference key is accessible')
    def exists(self,key):
        self._key(key)
        return bool(self.saved.values) or self.snapshot.exists(key)
    def read_text(self,key):
        self._key(key)
        if self.saved.values:return json.dumps({'version':1,**dict(self.saved.values)})
        # Existing financial-table preference bytes are read as a fallback;
        # they are never copied, overwritten or removed by this adapter.
        return self.snapshot.read_text(key)
    def write_text(self,key,text):
        self._key(key)
        payload=json.loads(text)
        if type(payload) is not dict or set(payload)!={'version',*DEFAULT_PREFERENCES}:
            raise ValueError('Unsupported display preference fields')
        values=validate_preferences(payload)
        self.revision=self.service.save(self.user,self.account_id,values,expected_revision=self.revision)
    def delete(self,*a,**k):raise PermissionError('Display preference adapter cannot delete artifacts')
    def initialize(self):raise PermissionError('Explicit administrator action required')


def render(ui,service,user,account_id,identity,snapshot,*,administrator=False):
    try:
        adapter=PreferenceStorage(service,user,account_id,identity,snapshot)
    except PreferenceIntegrityError:
        ui.sidebar.warning('Saved display preferences could not be verified. Changes apply for this visit.')
        render_preferences(ui,snapshot,AccessContext(identity,account_id),persist=False)
        return
    if not adapter.saved.storage_ready and administrator:
        if ui.sidebar.button('Enable local preference storage',key='enable-preference-storage'):
            service.initialize(user)
            ui.rerun()
    if ui.sidebar.button('Reload saved preferences',key='reload-saved-preferences'):
        ui.session_state.pop('campaigniq_preferences_owner',None)
    render_preferences(ui,adapter,AccessContext(identity,account_id),persist=adapter.saved.storage_ready)
    if not adapter.saved.storage_ready:
        ui.sidebar.caption('Preferences apply for this visit until an administrator enables local preference storage.')
