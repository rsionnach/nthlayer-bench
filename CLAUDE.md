# nthlayer-bench

Tier 3 operator TUI for case management and situation awareness.
Textual-based terminal UI; talks to nthlayer-core exclusively via HTTP
— **never accesses the SQLite store directly**.

## Stack

Python ≥3.11, `uv`-managed. Textual TUI + httpx.

## Build / test / lint / run commands

→ See `AGENTS.md`.

## Hard rules

These are load-bearing — wrong-side mistakes either crash the TUI on
malformed core payloads, deadlock the refresh loop, or silently drop
operator work.

1. **HTTP-only to nthlayer-core.** Never import or instantiate a
   SQLite store, verdict store, or any direct-DB primitive in this
   repo. The architectural separation is the point of Tier 3: the
   TUI is observable, replaceable, and decouplable. If you need data
   not exposed by the core API, add a core endpoint first.

2. **Logic modules in `sre/` are Textual-free.** Each `sre/<feature>.py`
   is pure async logic returning dataclasses or strings. No `from
   textual import` lines. Widgets call `sre/` functions; they don't
   reach into Textual internals from `sre/`. This is what makes the
   logic unit-testable without a Textual app instance.

3. **`markup=False` on every data-bearing Static / Label.** Breach
   summaries, operator notes, brief content, and LLM agent text all
   come back from core unsanitised. Rich markup will reformat the
   dashboard if you forget. Pin this in widget tests.

4. **`asyncio.Lock` skip-if-locked on every poller.** Refresh timers
   fire on a 5s cadence; a slow core can let the next tick fire while
   a previous one is in flight. Every widget that polls owns its own
   lock and returns immediately if the lock is held. Do not block on
   the lock.

5. **`severity` is `int` only, never fabricated.** When extracting
   severity from `metadata.custom.severity`, return None on any
   non-int value (incl. missing). The legacy default of 3 was wrong —
   it lets the brief / dashboard render with severity that no agent
   actually emitted. Pinned by R5 defensive tests in
   `test_sre_brief.py` and `test_sre_post_incident.py`.

6. **`_validate_case_id` rejects path-altering characters.** `/`,
   `?`, `#`, and a leading `..` all reject. CLI takes `case_id`
   straight into URL paths; the validator is the only line of
   defence. Pinned in `test_cli.py`.

7. **`_age_minutes` and `duration_minutes` fail-closed to None on
   malformed ISO 8601 or mixed naive/aware datetimes.** Surface
   "—" or skip the row; do not fabricate a number. This mirrors the
   same pattern in `case_bench.py` and `post_incident.py`.

8. **Write queue: stable verdict IDs at enqueue time.**
   `build_operator_note_verdict()` is called once before queuing, so
   every replay submits the same ID → core returns 409 on duplicate →
   `WriteQueue.drain()` drops it cleanly. **Do not** regenerate IDs
   at drain-time; that would create a duplicate audit-trail entry.

9. **Cold-start semantics for the escalation poller and situation
   dashboard.** First successful poll establishes baseline and
   returns []. Cold-start portfolio → `None`, renderer shows
   "Waiting for portfolio data." — not an error. Both prevent
   relaunch spam.

10. **Test discipline: don't assert on rendered text.** Tests use
    structured data primitives (exit codes, enum values, dataclass
    fields, ListView item counts). Captured-string assertions on
    Static/Label content break under any rendering change and miss
    real regressions. Same rule as the rest of the ecosystem
    (see `feedback_test_assertions`).

## Where to find detail

- Source layout, per-feature design decisions, test-suite
  cross-reference: `docs/architecture.md`.
- Build / test / lint / run / CI / release: `AGENTS.md`.
- Operator-facing brief design spec:
  `nthlayer/docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md`.
- SRE CLI inventory:
  `nthlayer/docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md`.
- nthlayer-common public API the TUI consumes:
  `nthlayer-common/docs/architecture.md`.
- README: project-level overview (`README.md`).
- Beads: `cd opensrm && bd ready --json`.
