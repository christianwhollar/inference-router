# Reservations before inference

Cost control must happen before the provider request. This service reserves integer USD micro-units using a conservative upper estimate. Successful calls refund the difference between the reservation and provider-reported token usage. Timeouts and malformed responses retain the entire reservation because the provider may have completed and billed the request. This is an accounting bound under the configured rates and token contract, not a reconciliation with a provider invoice.

PostgreSQL uses a conditional UPDATE inside a transaction, so concurrent replicas cannot both consume the last budget. SQLite uses BEGIN IMMEDIATE. The day is captured with the reservation so refunds crossing midnight apply to the correct accounting period. A process crash leaves a conservative reservation; automatic reconciliation of abandoned reservations is not implemented.

Candidate ordering is deterministic by estimated cost and model name after policy filtering. The quality number is a configured qualification score. A real deployment should populate it from relevant held-out evaluations. Confidential requests only reach explicitly approved model configurations. The caller is responsible for the data classification.

Context packing uses UTF-8 bytes plus a framing allowance as a conservative proxy for byte-based tokenizers. It preserves the question and selects whole, high-priority evidence blocks. Provider-reported usage outside the reserved contract is rejected. This does not support every possible tokenizer or inference billing scheme. Cached responses contain sensitive text in process memory, expire after 60 seconds, and are not shared across identities.

The breaker opens after two failures for 30 seconds. It is process-local and does not implement a distributed half-open probe. The semaphore bounds concurrent provider calls; it does not replace a gateway-level tenant rate limiter. Provider endpoints come only from server configuration, not request bodies.

## Walkthrough

Show cheap versus high-quality routing, a confidential request that has no eligible model, a timed-out provider, and the retained quota reservation. Race 20 small reservations against a 100-micro-unit daily budget. Then explain why a cache miss on another replica does not compromise the shared quota.

References: [Ollama generation API](https://docs.ollama.com/api/generate), [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/).
