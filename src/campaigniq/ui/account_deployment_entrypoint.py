"""Separate unactivated Streamlit entry point for a reviewed account release."""
from pathlib import Path

def main():
    import streamlit as st
    from campaigniq.account_deployment_startup import run
    st.set_page_config(page_title='CampaignIQ',page_icon='📈',layout='wide')
    run(st,repo=Path(__file__).resolve().parents[3])

if __name__=='__main__':main()
