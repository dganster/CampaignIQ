CampaignIQ — Schwab test fixtures

All Schwab source fixtures belong in this directory.

Canonical naming:
  <period>_positions.txt
  <period>_assignments.txt
  <period>_realized_gain_loss.txt

Do not create Schwab fixture files directly under tests/data/.

Examples:
  tests/data/schwab/january_positions.txt
  tests/data/schwab/february_assignments.txt
  tests/data/schwab/march_positions.txt
  tests/data/schwab/march_realized_gain_loss.txt

March 2026 input files:

  march_positions.txt
      February 28, 2026 ending positions, to be used as the March opening
      snapshot at 2026-02-28 23:59:59.

  march_assignments.txt
      March option assignments: UNH (03/09), CRWD (03/26), IBM (03/27).

  march_realized_gain_loss.txt
      30 March realized-gain/loss records. Schwab total:
      -$27,983.82.

The March trade-history source supplied separately is the user's uploaded
Account Trade History CSV. The existing project annual trade-history file
should not be overwritten until its contents are checked.

Source checkpoints:
  February statement ending account value: $1,483,807.20
  March statement beginning account value: $1,483,807.20
