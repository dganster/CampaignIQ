#!/usr/bin/env bash
set -euo pipefail
python scripts/configure_streamlit_auth.py
exec python scripts/start_accounts.py
