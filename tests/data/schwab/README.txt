CampaignIQ — March 2026 input files

These files were prepared from the February 2026 Schwab statement and the
March 2026 Schwab transaction/realized-gain sources supplied for the March
validation.

Files:
  march_positions.txt
      February 28, 2026 ending positions, to be used as the March opening
      snapshot at 2026-02-28 23:59:59.

  march_assignments.txt
      March option assignments: UNH (03/09), CRWD (03/26), IBM (03/27).

  march_realized_gain_loss.txt
      30 March realized-gain/loss records. Schwab total:
      -$27,983.82.

Recommended destination:
  tests/data/schwab/

The March trade-history source supplied separately is the user's uploaded
Account Trade History CSV. The existing project annual trade-history file
should not be overwritten until its contents are checked.

Source checkpoints:
  February statement ending account value: $1,483,807.20
  March statement beginning account value: $1,483,807.20
  March realized net loss: -$27,983.82
