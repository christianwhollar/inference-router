# Verification record · 0.2.0

The combined five-project suite completed with **77 passing tests**, including PostgreSQL accounting/vector integration, actual local transformer embeddings, and Solidity execution on a local EVM. The portable per-repository GitHub Actions workflows run separately on Linux; their live-provider tests are opt-in.

All five browser interfaces were exercised in Chromium. The recorded walkthroughs covered every navigation tab and the core workflows: case import/investigation/review, document edit/revocation/feedback, queued experiments and trace inspection, provider request/accounting, and actual student-model inference. No JavaScript errors were observed. Desktop and mobile captures are saved beside this record.

The code was checked with Ruff and the browser JavaScript was parsed with Node.js. Container images run as UID 10001; health, packaged browser assets and the Linux student-model inference path were checked. Final release images were rebuilt and checked after packaging.

See [machine-readable record](verification-v2.json), [operating guide](operations.md), and the repository's `reports/` directory for experiment-specific evidence. Initial v1 verification remains in `verification.json` as historical evidence; it does not describe the larger release by itself.

Benchmarks and tests answer different questions. Public-data studies report held-out quality and local timing; fault studies inject controlled failures. These checks do not establish a cloud SLA, production security certification, public-chain deployment, or prior work experience.

Docker images were built and started on Linux as UID 10001. Compose configurations and application commands passed; seeded services, packaged reports, fixture routing and released-model prediction were exercised as applicable. The per-project machine record includes browser and container outcomes.
