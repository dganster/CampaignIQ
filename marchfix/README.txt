CampaignIQ March CMI historical option reconstruction fix

Apply from the CampaignIQ repository root:

  bash apply_march_cmi_fix.sh

The script:
- seeds unambiguous historical option lots missing from the opening snapshot;
- leaves snapshot positions authoritative;
- does not reconstruct equities;
- does not invent broker cost basis;
- adds a CMI regression test;
- runs that regression and the full pytest suite.
