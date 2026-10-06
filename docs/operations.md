# Operating guide

## Start, stop, and inspect

Start the loopback demo with `python -m router.serve --demo` and open port 8104. Stop with Ctrl-C. Persistent data lives under the configured runtime directory. Restarting does not reset edited demo data. Remove or choose a fresh runtime directory only when you deliberately want a fresh synthetic workspace.

For hosted use, configure `API_KEYS_JSON` with strong random keys and server-owned tenant/user/role claims; omit `APP_DEMO`. Terminate TLS, restrict network access and persist application state. Do not publish the built-in demo credentials on an externally reachable service. The included Compose configurations publish to loopback only.

`/health` checks application availability. The three operational services expose authenticated `/metrics` and optional OpenTelemetry export through `OTEL_EXPORTER_OTLP_ENDPOINT`. These metrics do not log raw prompts, API keys, or document bodies. The evaluation and model services expose health and their explicit run/inference results.

## Accounting and failures

Every attempted provider call creates a reservation inside the same transaction as the quota increment. Known successful usage settles the reservation. Unknown usage is retained, not refunded speculatively. Reviewers can reconcile uncertain reservations from provider evidence. An active reservation becomes manually eligible after 120 seconds; provider calls are capped at 30 seconds.

Settlement locks the reservation, validates its bound, refunds only the difference, and records the actor and note. An identical replay is harmless; a contradictory replay is rejected. The PostgreSQL adapter runs the same state machine with row locks across replicas.

The response `charged_usd` includes held estimates from failed attempts. It is an accounting amount under configured tariffs, not proof of a provider invoice. For local zero-tariff models it is zero; electricity/hardware costs are not included. Cached responses incur no new charge and preserve the original provider latency.

## Deployment and configuration

Use `MODELS_CONFIG` for the model JSON path, `DAILY_BUDGET_USD` for the per-tenant daily limit and `ROUTER_DATABASE_URL` for a shared PostgreSQL ledger. Without the database URL, SQLite uses `DATA_DIR/router.db`. Do not put provider secrets in the model file; use `api_key_env` references.

Task calibration is checked against the local model digest at startup. If the digest differs, recalibrate and review the resulting configuration. Do not silently reuse a profile after replacing a model tag. General quality is an operator rating and is labeled as such in the API.

Circuit state and response cache are local to each process. The first eligible half-open request becomes the probe; another request must use a different permitted route or receive 503. Multiple replicas coordinate quota accounting, not circuit state. Terraform validation is recorded separately from an actual cloud rollout.

## Useful failure demonstrations

- Request confidential data from an unapproved model: no route is eligible.
- Raise the quality floor above all task profiles: receive 503 without a provider call.
- Inject a timeout and observe the uncertain reservation plus failover.
- Reconcile the same unknown charge concurrently from two instances: refund occurs once.
- Cancel a client while a provider runs: retain the unknown charge.
- Return JSON that violates the requested schema: reject it and preserve uncertain usage.


## Five-minute technical walkthrough

Explain the central data flow in the README, run one successful user workflow, deliberately trigger one failure above, inspect its recorded evidence, and explain one limitation you would address before a broader deployment. Describe measured results as results of this repository's experiments rather than as prior employer production outcomes.
