#!/usr/bin/env bash
set -euo pipefail

: "${PORT:=8501}"

exec python -m streamlit run src/campaigniq/ui/dashboard.py \
    --server.address=0.0.0.0 \
    --server.port="${PORT}" \
    --server.headless=true
