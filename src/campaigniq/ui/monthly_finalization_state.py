"""Retain failed reconciliation review across Streamlit widget reruns."""


def run_or_resume_finalization(state, *, signature, clicked, execute):
    key = "monthly_import_blocked_execution"
    cached = state.get(key)
    if cached is not None and cached[0] != signature:
        state.pop(key, None)
        cached = None
    if not clicked:
        return cached[1] if cached is not None else None
    state.pop(key, None)
    result = execute()
    if not result.closing_reconciliation.reconciled:
        state[key] = (signature, result)
    return result
