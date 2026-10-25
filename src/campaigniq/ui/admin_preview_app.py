"""Streamlit entry point for the loopback-only synthetic admin preview."""
import streamlit as st

from campaigniq.admin_preview import run_app

st.set_page_config(page_title='CampaignIQ administration preview', layout='wide')
run_app(st)
