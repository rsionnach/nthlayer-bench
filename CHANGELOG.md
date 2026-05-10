# Changelog — nthlayer-bench (Tier 3)

This file narrates the build sequence behind the initial state of this repository,
in prose. The repository was created from working code that had been developed
across the ecosystem under the v1.5 epic plan; we did not reconstruct phase-by-phase
git history because that history did not exist as commits at the time the work
was being done. This narrative is the honest substitute.

## [1.6.0](https://github.com/rsionnach/nthlayer-bench/compare/v1.5.0...v1.6.0) (2026-05-10)


### Features

* smoke-test gate before PyPI publish ([95cff44](https://github.com/rsionnach/nthlayer-bench/commit/95cff44f1a7d03169bf862d1db092f20efa602e2))


### Documentation

* add README — Tier 3 operator TUI overview ([3390e36](https://github.com/rsionnach/nthlayer-bench/commit/3390e36648387bacd97fe197a418d430ba6e875d))
* **CLAUDE.md:** document release-please integration ([36abe2b](https://github.com/rsionnach/nthlayer-bench/commit/36abe2b7e92b91c911b339b6551f9fd2544bff54))
* **CLAUDE.md:** document Renovate config (Phase 4) ([e41bb56](https://github.com/rsionnach/nthlayer-bench/commit/e41bb56d5c4fec32ea5e4d1e624d991df4190ade))
* **CLAUDE.md:** document smoke-test gate (Phase 2) ([009249e](https://github.com/rsionnach/nthlayer-bench/commit/009249e971e4b171a80a1582b06005b217f75e82))
* **claude:** repoint stale ecosystem refs to nthlayer/docs/superpowers/ ([ca43255](https://github.com/rsionnach/nthlayer-bench/commit/ca432550cc962171789ee31a5c97c767b1f137ba))

## v1.5.0 — 2026-05-03

First lockstep release with the rest of the v1.5 ecosystem. Phase 5
landed minor changes here:

**`verdict_type=operator_note` on operator-note submissions**
(opensrm-saun.1.2). `sre/reasoning_capture.py:build_operator_note_verdict`
now sets `verdict.verdict_type = "operator_note"` (matching the RBAC §10
canonical verdict-type taxonomy). The verdict's `subject.type` remains
`"custom"` because `operator_note` is not in `VALID_SUBJECT_TYPES`; the
typed column is what core's `GET /verdicts?type=operator_note` filter
reads.

**Test fixture compat with `to_dict(Verdict)`'s wire-canonical rename**
(opensrm-saun.1.2). `tests/test_sre_reasoning_capture.py` previously
asserted `payload["verdict_type"]`; updated to `payload["type"]`
following the `to_dict()` rename in `nthlayer-common`. The verdict
dataclass keeps its internal field name; the wire format uses the
HTTP-canonical name.

The bench logic layer was not otherwise touched in Phase 5 — the saun.1
three-tier integration test verified bench's read path (via
`sre.case_bench.fetch_case_bench`) works through core's HTTP API
end-to-end. The widget rendering layer is unit-tested separately.

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
