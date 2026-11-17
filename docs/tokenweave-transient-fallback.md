# Transient-error-only fallback policy

## 1.91.4+tokenweave.20261117

The optional router setting `fallback_on_transient_errors_only: true` restricts retries and fallbacks to HTTP 408, 429 and 5xx responses, transport connection failures and temporary router deployment unavailability. All other errors return to the caller, including invalid parameters, context overflow, authentication, permission and unknown application errors

The guard runs before order-based and weighted failover or context-window/content-policy fallback selection. It also takes precedence over explicit retry policies. Chat Completions and Responses share the guard, including mid-stream wrappers, which are classified by their original exception. A transient failure followed by a permanent error stops immediately

The default is false for existing SDK/router users. Enable the setting in `router_settings` to apply the production policy. The setting is visible and editable through router configuration APIs. Existing fallback destinations can stay configured; they are only used for eligible failures

OpenAI-compatible errors containing `Input is too long` are now recognized as `ContextWindowExceededError`, with HTTP 400 and `code: context_length_exceeded`. Clients can use that signal to compact context and resubmit. The proxy does not truncate or rewrite conversation history. Post-header streaming errors remain SSE errors because an HTTP status cannot change after streaming starts

Deployment requires the new image before enabling the setting. Deploy a committed image containing this change, then upload the committed configuration. Do not upload the setting to an older image. No production deployment is included in this code change

## Validation

The targeted router, fallback-handler, exception-mapping and proxy suites passed 154 tests; request-processing error regressions passed another 53. This includes native and bridged Responses streams, stopping a retry or multi-hop fallback chain on a permanent error, and retaining recovery from rate limits, server failures, transport failures and temporary deployment unavailability

A real local proxy against a local fault-injection HTTP server passed 12 combinations: Chat Completions and Responses, streaming and non-streaming, and upstream 400/429/503. Every 400 returned `context_length_exceeded` with one primary call and zero backup calls. Every 429/503 recovered with exactly one backup call. The upstream was simulated; this did not send production traffic or prove a client's automatic compaction behavior

Python syntax and incremental Ruff checks passed with no added findings against the parent commit. Offline lock validation passed for 412 packages. Existing lint findings outside the changed lines were retained
