# Session responsiveness — 2026-10-23

Large MCP inventories and frequent App polling were feeding a conversation refresh
loop: every read request and response could fetch both the chat snapshot and the
session list, then rebuild the React conversation. Replaying all projection
changes on each snapshot made long chats progressively more expensive.

## Changes

- Share MCP inventory reads across App bridges and panels within a page (30 seconds),
  and across browser tabs in the Host (3 seconds). Cache keys include the thread;
  configuration and MCP lifecycle changes invalidate cached inventory. Actual tool
  calls still execute through the original native permission and gateway checks.
- Separate the conversation event stream from background catalog/status reads.
  Raw diagnostic history remains available with its original cursors. Background
  reads no longer occupy the execution-step pages or trigger chat refreshes.
- Coalesce relevant notifications and refresh the session list only for changes
  that affect it. Incrementally apply projection changes to bounded snapshot
  caches; fixed-watermark pagination, rollback, bindings and retention still apply.
- Retry transient GET failures, coalesce simultaneous reads and cancel history
  requests when switching chats. Recover failed session metadata reads so the
  composer cannot remain disabled. Preserve the current draft during read recovery.
  Mutation requests keep their idempotency keys and are never replayed automatically.
- Compress large finite JSON responses when the client accepts gzip. SSE remains
  uncompressed and incremental. Keep one previous build's assets for lazy imports
  from already-open tabs; publish deployment assets before the entry HTML.

## Validation

149 frontend tests passed. Python discovery ran 280 tests successfully with 20
native/environment-dependent skips. The TypeScript check and production build
passed. Browser checks covered MCP App permissions and state, history pagination,
streaming scroll, reconnect/replay, retention and a high-volume polling stream.
The polling test processed 84 distinct 100 KB background frames without additional
chat snapshots; entering seven characters took about 172 ms including typing delay.

A 10-second sample against the same running production chat, with the original
backend in both samples, gave the following observations. The running task was
not deterministic, so these are diagnostic samples rather than a controlled benchmark.

| Measurement | Previous frontend | Updated frontend |
| --- | ---: | ---: |
| Snapshot requests | 6 | 2 |
| Session-list requests | 6 | 1 |
| Browser main-thread time | 2,907 ms | 542 ms |
| Data received | 1,291,674 bytes | 917,628 bytes |

After backend deployment, repeated production snapshot requests took 21–26 ms
(first request 147 ms). One 589 KB JSON snapshot transferred as 126 KB with gzip.
Twelve concurrent inventory API reads produced one native request and one response.
A 12-second idle sample contained 21 background reads in raw diagnostics and none
in the conversation stream. A browser with the production App open observed zero
snapshot/inventory refreshes during ten idle seconds, input operations of 12–21 ms,
and no page or request errors.

A separate synthetic replay of 60,000 events reduced median warm snapshot time
from 242 ms to 0.02 ms and removed catalog reads from conversation pages while
preserving message content. These synthetic timings are not production latency.

## Deployment and limits

The primary service on port 18880 was updated after its chat turn finished and
checks found no outstanding delivery, unresolved intent or external task operation.
The existing chat was resumed to `ready` without starting a model turn. Static
assets were backed up and existing hashed chunks retained. The task service's stale
PID record was corrected only after matching the live listener's configuration.

The approximately 2 GB persisted diagnostic history still takes about a minute to
replay during a cold restart. These changes reduce steady-state work; they do not
delete history or change model inference speed. A network outage can still occur;
read recovery and explicit uncertain-write messages make it recoverable.

Detailed test, benchmark and deployment evidence is preserved in
`platform-data/galatea-app/production-18880/evidence/session-performance-20261023/`
from the workspace root. Working copies remain in `/tmp/galatea-session-performance/`.
