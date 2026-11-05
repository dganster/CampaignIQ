# Monthly source-control messages

The combined monthly preview now identifies whether each quantity control comes
from the brokerage closing snapshot or the Forex Transaction Report CSV. The
overall label includes Forex because a Forex disagreement can block the same
check even when every securities quantity agrees.

The CSV control retains published opening quantities for pairs with no CSV
transaction rows. An omitted close can therefore leave a carried quantity that
disagrees with complete execution evidence. This does not mean the brokerage
statement reports that position. Verified monthly Forex evidence remains labelled
separately when that existing path is used.

The outdated message about unfinished futures integration/rehearsal is replaced
with a message about the actual source/inventory findings. Source checks that
pass still require the enabled application workflow and explicit month approval.

This change does not alter quantity calculations, financial source policies,
receipts, saved originals, publication eligibility, database roles or production
configuration. In particular, saved daily Forex reviews do not yet substitute
for the financial closing control. October's missing USD/JPY CSV close and
monetary differences remain blocked until that integration is implemented and
verified. There is no requirement to supply an October Forex monthly statement.
