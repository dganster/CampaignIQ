# Render only the selected Performance tab

Production measurements showed report_rendering taking about 2.82 seconds on two Performance visits. Streamlit executes hidden tab bodies unless the application explicitly guards them. This page previously built Monthly Results, Campaign Analysis and Drawdown on every run, including strategy evidence in a hidden tab.

All three existing tabs remain. Only the selected tab now builds its UI. Underlying analysis and drawdown calculations run when their tab is selected. Existing financial formulas, source verification, reporting filters, campaign drilldowns and route callback are unchanged. Tab keys now use the same user/account prefix as other widgets; the selected tab is read from that same scoped state, so lazy rendering follows the actual control. Existing account-specific backing state and default restore remain in use.

Three automatic timing stages identify performance_monthly_render, performance_campaign_render and performance_drawdown_render. Timers nest within report_rendering and must not be added to the parent duration. No source or authorization cache, database/schema change, or production write is introduced. Tab selection can now require a server run to load content; that is the intended tradeoff for avoiding unselected work.

Tests render the actual account dashboard through Streamlit with synthetic authorized snapshots: each tab's content remains available, unselected headings and tab-specific analytics are absent, selection changes work on later runs, and tab widget keys differ by account. Existing navigation, drawdown, underlying, account-state and timing tests remain. The real latency gain must be measured after the exact release review and deployment; no reduction is claimed from synthetic tests alone.
