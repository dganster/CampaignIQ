# Account chart drill-down

Altair selection events must use the same account-scoped physical key as their callbacks. The account UI wrapper previously scoped dataframe and Plotly widgets but omitted Altair charts. A click was stored under an unscoped key while the callback read its account-prefixed key, so Overview month bars did not navigate.

The fix adds `altair_chart` to the existing widget-key scoping mechanism. It does not retain selection events as preferences or change calculations, authorization, documents or publication. Both Overview and Performance use the shared chart renderer.

Regression coverage reproduces the old mismatch, verifies month navigation and filter resets, reopening the same month, independent account state, and empty or stale selections. Browser confirmation after deployment remains required.
