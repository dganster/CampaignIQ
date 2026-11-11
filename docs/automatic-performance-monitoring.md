# Automatic server performance monitoring

Every deployed application run records elapsed server time automatically, including startup, allowlisted pages, named stages, successful stops, reruns and errors. Detailed cProfile remains optional; profiled samples are marked and summarized separately because profiling adds overhead.

Monthly input calls are timed for allowlisted reviews, saves, confirmations, recovery, results and publication. The transparent wrapper preserves arguments, returned objects, exceptions, access checks and transactions. It introduces no database calls or financial writes, and never establishes publication readiness.

Administrators open Navigation diagnostics and click Show performance statistics for counts, errors, last duration, median, 95th percentile and maximum by page/operation, stage summaries and 50 recent events. Access requires the current administrator boolean. Another click refreshes the summary; no polling or scheduled reruns are added.

The window holds at most 2,000 events per Python worker and resets on restart/deployment. It is not a cross-instance durable database. Timestamped JSON events named campaigniq.performance.v1 are emitted to stdout for Render logs. Longer retention or alerting depends on hosting log retention or a configured external destination. This patch does not provision a log sink, environment setting, schema or credentials.

Collected fields are fixed page/operation/stage labels, numeric durations/call counts, outcome, UTC timestamp and profiling flag. No user/account/session identifiers, upload names, SQL, parameters, arguments, results, exception text or financial values are recorded. Errors include expected validation failures. Samples count server runs and Streamlit reruns, not unique visits. Nearest-rank percentiles use the retained window. Nested times overlap and cannot be added; browser rendering and network delivery are excluded.

Collection uses perf_counter, a bounded deque and a short lock. Synchronous logging adds some overhead. Logging failures are suppressed so they cannot change a completed financial operation into an error. Instrumentation itself does not improve latency. Use production measurements to identify bottlenecks and optional profiling for deeper investigation.

Tests cover profiling-off capture, administrator visibility, metadata allowlisting, call/result/exception preservation, bounded concurrent samples, percentiles, logging failure isolation, actual Streamlit stop, profiler separation and existing review flows. Rollout requires an exact candidate release review and paired runtime source checksums.

## Readable statistics panel

Show performance statistics now opens a full-width main-page panel, outside the sidebar. Pages/operations and nested stages appear in separate tables sorted by descending 95th-percentile duration. Column labels identify seconds, sample counts, errors and profiled samples. Low-sample and overlapping-stage caveats remain visible. Download performance statistics (JSON) exports exactly the sanitized snapshot shown, including the last 50 retained events. The snapshot excludes its own in-progress page run. No financial or access behavior changes; current administrator authorization is still required before both display and export.
