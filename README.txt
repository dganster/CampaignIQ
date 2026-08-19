CampaignIQ February TMUS assignment fix

This replacement updates:
  src/campaigniq/domain/realized_lot_attributor.py

It preserves assignment campaign provenance and adds a narrowly scoped rule:
when Schwab reports the stock realization from an option assignment one
business day before the assignment position event, assignment-derived stock
activity may match that prior business day. Ordinary trade closures still
require an exact realized date.

From ~/CampaignIQ after downloading this ZIP to ~/Downloads:
  unzip -o ~/Downloads/CampaignIQ-february-tmus-fixed.zip

Then run:
  pytest -q tests/test_february_tmus_assignment_regression.py
