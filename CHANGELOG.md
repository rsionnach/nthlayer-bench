# Changelog — nthlayer-bench (Tier 3)

This file narrates the build sequence behind the initial state of this repository,
in prose. The repository was created from working code that had been developed
across the ecosystem under the v1.5 epic plan; we did not reconstruct phase-by-phase
git history because that history did not exist as commits at the time the work
was being done. This narrative is the honest substitute.

## Provenance

`nthlayer-bench` is the Tier 3 (operator interface) process in the three-tier
NthLayer architecture decided 2026-04-21
([`docs/superpowers/specs/2026-04-21-spec-revision-summary.md`][spec-revision] in the
`opensrm` repo). It is one of the three new repositories created as part of the
six-repo consolidation
([`docs/superpowers/specs/2026-04-21-repo-consolidation-recommendation.md`][consol]).

A Textual TUI for SREs to interact with NthLayer: situation board, case bench
with paging brief / post-incident review, manual approve/reject for AWAITING_APPROVAL
incidents, on-call status, change-freeze view. Communicates with `nthlayer-core`
via HTTP API only — no direct SQLite access.

## Build sequence (epic-level)

The contents of this initial commit reflect the **Phase 4 scaffolding** of the
v1.5 epic plan
([`docs/superpowers/plans/2026-04-21-nthlayer-v1.5-epic-tree.md`][v15-plan]):
the Textual app skeleton, CLI entry point, and minimum viable structure to
host upcoming P4-B / P4-C / P4-D work.

The bulk of the bench's screens / panels / interaction surfaces is forthcoming
in dedicated P4 beads. The current state is intentionally early — the structural
shape needs to be in place before screens are wired.

## What is in this initial commit

- `src/nthlayer_bench/app.py` — Textual `App` skeleton.
- `src/nthlayer_bench/cli.py` — `nthlayer-bench` entry point.
- `src/nthlayer_bench/__init__.py` — Package marker.
- `pyproject.toml` — depends on `nthlayer-common` (for `CoreAPIClient`),
  `textual`. Console script: `nthlayer-bench = "nthlayer_bench.cli:main"`.
- `tests/` — minimal placeholder; meaningful tests arrive as screens land.

## Things deliberately NOT yet in this repo

- **Situation board.** Live status of all open cases, grouped by service, with
  staleness thresholds (10s case list, 120s situation board, 60s heartbeat).
- **Case bench.** Detailed view per case: paging brief, investigation timeline,
  proposed remediation, approve/reject buttons.
- **Reasoning capture.** When an operator approves/rejects, capture the reason
  in an `operator_note` verdict so post-incident review has the human signal.
- **SRE operator commands** ported from the deprecated `nthlayer-respond` repo:
  `oncall`, `brief`, `shift-report`, `suppress`, `post-incident`, `delegate`.
  Inventory and bench-equivalent shape:
  `opensrm/docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md`.
  Demo-prioritised: `brief` and `post-incident` first.
- **textual-serve SaaS delivery.** v2. v1.5 ships local-terminal only.

## How this repo evolves

- **P4-B** — situation board screen.
- **P4-C** — case bench screen (incident detail).
- **P4-D** — reasoning capture for operator decisions.
- **P4-E** — port operator commands from legacy nthlayer-respond
  (priority order in the inventory doc above).
- **v2** — textual-serve for hosted SaaS delivery.

[spec-revision]: ../opensrm/docs/superpowers/specs/2026-04-21-spec-revision-summary.md
[consol]: ../opensrm/docs/superpowers/specs/2026-04-21-repo-consolidation-recommendation.md
[v15-plan]: ../opensrm/docs/superpowers/plans/2026-04-21-nthlayer-v1.5-epic-tree.md
