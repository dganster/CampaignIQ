CampaignIQ February TMUS fix

1. Copy these two files to a convenient location, or use the provided sandbox files directly.
2. From the CampaignIQ checkout run:

   bash /mnt/data/apply_february_tmus_fix.sh

The script:
- adds an assignment-only prior-business-day match for broker realized records;
- keeps ordinary closing activity on exact-date matching;
- preserves the assignment flag when an activity is partially consumed;
- removes temporary "DEBUG AFTER" lines from src/campaigniq/import_pipeline.py;
- runs the focused TMUS February regression test.

Regression test:
tests/test_february_campaign_realized_pnl.py::test_tmus_assignment_matches_february_realized_stock_sale
