# nthlayer-bench

**Tier 3 of the [NthLayer](https://github.com/rsionnach/nthlayer) ecosystem.** Operator TUI for case management, situation awareness, and incident response. [Textual](https://textual.textualize.io/)-based terminal interface that communicates with [`nthlayer-core`](https://github.com/rsionnach/nthlayer-core) (Tier 1) exclusively via HTTP API — never touches the SQLite store directly.

```bash
pip install nthlayer-bench
nthlayer-bench --core-url http://localhost:8000
```

## What it is

The operator's window into the NthLayer runtime. SREs use it to triage cases, follow situation snapshots, approve or reject remediation actions, and capture reasoning during incidents. Bench is read-mostly against core — every write goes through the core HTTP API and is subject to the same RBAC + change-freeze rules as worker writes.

- **Stateless** beyond local UI state. All durable state lives in core.
- **Read-mostly.** Lists are polled with staleness thresholds (case list 10 s, situation board 120 s, heartbeats 60 s).
- **Apache 2.0** licensed.

## CLI

```bash
nthlayer-bench --core-url http://localhost:8000   # launch TUI
nthlayer-bench -V                                  # print version
```

## What's in v1.5

The first cut establishes the connection model and the shell:

- `BenchApp` — Textual app; Header / Footer / status bar / main container.
- `ConnectionStatus` widget — polls `GET /health` every 5 s.
  - **CONNECTED** — core returned 200.
  - **DEGRADED** — core returned a non-200 (still reachable, but unhealthy).
  - **DISCONNECTED** — `httpx.RequestError` / `OSError` (network-level failure).
- Initial check fires immediately on mount via `call_later` so operators don't see a "checking..." flash.

Subsequent v1.5 work (Phase 4 of the [v1.5 epic plan](https://github.com/rsionnach/opensrm/blob/main/docs/superpowers/plans/2026-04-21-nthlayer-v1.5-epic-tree.md)) adds:

- **Situation board** — live correlation_snapshot rollup with staleness highlighting.
- **Case bench** — case list with lease ownership, priority, and team filtering (with toggle to show all teams).
- **Case detail** — verdict chain walk, reasoning capture, approve / reject buttons that POST to core.
- **Notification escalation** — staleness-driven warnings when core or workers stop heartbeating.

The SRE CLI commands previously stranded in `nthlayer-respond` (`brief`, `post-incident`, `suppress`, `shift-report`, `oncall`, `delegate`) will land here in priority order — `brief` and `post-incident` first, since they map directly onto case-detail and retrospective views. Inventory: [respond SRE CLI inventory spec](https://github.com/rsionnach/opensrm/blob/main/docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md).

## How it talks to core

Bench is a pure HTTP consumer of core. No direct SQLite access — that boundary is structural, not just convention. The contract is:

| Direction | Endpoint(s) |
|---|---|
| Read state | `GET /cases`, `GET /verdicts/{id}`, `GET /verdicts/{id}/ancestors`, `GET /assessments`, `GET /heartbeats` |
| Operator write | `PUT /cases/{id}/lease`, `DELETE /cases/{id}/lease`, `PUT /cases/{id}/resolve`, `POST /verdicts/{id}/outcome` |
| Approval flow | `POST /verdicts` (operator-note + approval verdicts) |

Write operations queued during disconnection are replayed on reconnect; conflicts return 409 and surface in the UI.

## Why a separate Tier 3 process

Operator UX has different cadence and risk than worker computation. Pulling bench into its own process means:

1. **Deploy independently.** A bench rebuild doesn't affect the runtime; a worker rebuild doesn't drop the operator's session.
2. **Run multiple instances.** Several SREs can attach simultaneously; each instance is a stateless client.
3. **Future-proof for SaaS delivery.** v2 introduces `textual-serve` so bench can be served over the network without changing the underlying app.

## NthLayer ecosystem

| Repo | Tier | Role |
|---|---|---|
| [`opensrm`](https://github.com/rsionnach/opensrm) | — | The OpenSRM specification |
| [`nthlayer-common`](https://github.com/rsionnach/nthlayer-common) | — | Shared library |
| [`nthlayer-generate`](https://github.com/rsionnach/nthlayer-generate) | — | Build-time compiler |
| [`nthlayer-core`](https://github.com/rsionnach/nthlayer-core) | **1** | HTTP API + state |
| [`nthlayer-workers`](https://github.com/rsionnach/nthlayer-workers) | **2** | Five worker modules |
| [`nthlayer-bench`](https://github.com/rsionnach/nthlayer-bench) | **3** | This repo — operator TUI |
| [`nthlayer`](https://github.com/rsionnach/nthlayer) | — | Project front door + meta-package |

## Licence

Apache 2.0
