# nthlayer-bench architecture

Tier 3 operator TUI. Source layout, per-feature design decisions, and
the test-suite cross-reference. The hard rules in `CLAUDE.md` are
canonical for the runtime invariants — this file is the "what lives
where" reference.

## Source layout

```
src/nthlayer_bench/
  __init__.py
  cli.py        # Entry point: nthlayer-bench [-V/--version]
                #              [--core-url URL] [--case-id ID]
                # _validate_case_id() rejects path-altering chars
                #   (/, ?, #) and leading ..; deferred import of BenchApp.
  app.py        # BenchApp (Textual App):
                # - ConnectionStatus widget polls core /health with
                #   exponential backoff (base=5s, doubles per failure,
                #   cap=60s). _compute_next_interval() pure helper;
                #   _consecutive_failures counter reset on success or
                #   non-200. States: CONNECTED (200), DEGRADED (non-200),
                #   DISCONNECTED (httpx.RequestError/OSError).
                # - on_mount pushes CaseBenchScreen (home view), starts
                #   escalation poller (ESCALATION_POLL_SECONDS=5.0) +
                #   write-queue drainer (WRITE_QUEUE_DRAIN_SECONDS=5.0),
                #   then pushes CaseDetailScreen on top if
                #   initial_case_id set (deferred imports).
                # - Lazy CoreAPIClient property (shared pool, single
                #   instance per app run).
                # - _on_exit_app closes client in try/finally so Textual
                #   super cleanup always runs.
                # - _escalation_monitor (EscalationMonitor, one-per-session).
                # - _escalation_lock (asyncio.Lock, skip-if-locked guard
                #   against duplicate toasts).
                # - _write_queue (WriteQueue, in-memory FIFO);
                #   write_queue property (public accessor for screens).
                # - _poll_escalations() dispatches toasts via
                #   _notify_escalation.
                # - _drain_write_queue() replays pending notes, notifies
                #   operator on recovery.
                # - _notify_escalation() maps severity via
                #   _SEVERITY_TO_TEXTUAL ("critical"→"error"/red
                #   persistent, "high"→"warning"/orange).
  screens/      # Textual Screens — see "Per-feature design" below
  sre/          # Pure async logic (no Textual imports), one file per
                # operator command
  widgets/      # Textual widgets — pull from sre/, no logic of their own
```

### `screens/`

- `case_bench.py` — CaseBenchScreen: operator home view (no escape/q —
  app-level quit is the exit path); mounts CaseBenchPanel; handles
  `CaseBenchPanel.CaseSelected` → pushes CaseDetailScreen; double-push
  guard skips push if topmost screen is already CaseDetailScreen for
  same case_id; deferred import of CaseDetailScreen; 's' binding →
  `action_open_situation_board` → pushes SituationBoardScreen
  (deferred import).
- `case_detail.py` — CaseDetailScreen: two-pane layout (36-col context
  pane + CaseBriefPanel right pane). Left pane: Static "Case Context",
  Static `case_id`, live `ReasoningCapturePanel(client, case_id,
  write_queue=app.write_queue)`. Passes `write_queue` via
  `getattr(self.app, "write_queue", None)` — falls back to None for
  legacy inline-error behaviour if app doesn't expose one. escape/q →
  `pop_screen`; r → push CaseReviewScreen (deferred import). `case_id`
  property used by double-push guard. Never constructs its own
  CoreAPIClient (uses `app.client`).
- `case_review.py` — CaseReviewScreen: single-pane with
  CaseReviewPanel; escape/q → `pop_screen`; pushed from
  CaseDetailScreen via `r` key.
- `situation_board.py` — SituationBoardScreen: system-state-at-a-glance
  dashboard; pushed from CaseBenchScreen via `s`; escape/q →
  `pop_screen`; composes SituationBoardPanel. Never constructs its own
  CoreAPIClient.

### `sre/` (pure async, no Textual)

- `brief.py` — `build_paging_brief(client, case_id) -> PagingBrief`;
  `render_brief(brief) -> str`. PagingBrief fields: `case_id`,
  `service`, `severity`, `summary`, `likely_cause`, `cause_confidence`,
  `blast_radius`, `recommended_action`, `recommended_target`, `state`,
  `awaiting`. BriefError hierarchy: CaseNotFoundError,
  AnchorVerdictMissingError, CoreUnreachableError.
- `case_bench.py` — `fetch_case_bench(client, state, limit, now) ->
  CaseBenchView`. `CaseSummary(case_id, priority, service, state,
  created_at, age_minutes, briefing)`. `CaseBenchView(ordered_priorities,
  cases_by_priority, flat)`. `PRIORITY_ORDER=("P0","P1","P2","P3")`;
  unrecognised priority → "Other" bucket. `_age_minutes` fail-closed to
  None on malformed ISO/mixed tz. `_group_and_sort`: known priorities in
  canonical order then extras alphabetically. `render_case_bench(view)
  -> str`. CaseBenchError(BriefError).
- `post_incident.py` — `build_post_incident_review(client, case_id) ->
  PostIncidentReview`; `render_post_incident_review(review) -> str`.
  Chronological timeline (asc by `created_at`, ties by `verdict_id`).
  Accuracy via `outcome_resolution` verdicts (parent_ids match,
  latest-wins) + mutation-style `outcome.override` on original verdict
  (jmy.19). worked/to_improve: confirmed→worked, overridden→to_improve.
  PostIncidentError(BriefError). ReviewState: "in_progress" |
  "resolved". `severity` from latest triage
  `metadata.custom.severity` (int only, never fabricated).
  `_is_outcome_resolution()` checks both `subject.type` and
  `verdict_type`. `VerdictAccuracy`: 5 `override_*` fields
  (override_by, override_at, override_action, override_reasoning,
  override_original_action); all-None when no override. render emits
  `## Overrides` section when any accuracy record has `override_by`
  set.
- `reasoning_capture.py` — `fetch_case(client, case_id) -> dict`
  (public case fetch, used to prime write-queue cache);
  `fetch_operator_notes(client, case_id) -> list[OperatorNote]`;
  `submit_operator_note(client, case_id, text, *, author) ->
  OperatorNote`; `build_operator_note_verdict(case, text, *, author)
  -> Verdict` (pure factory, no I/O, stable ID at build time for
  409-idempotent queue replay; raises ValueError empty text /
  AnchorVerdictMissingError missing anchor);
  `submit_operator_note_verdict(client, verdict) -> APIResult`
  (submits pre-built Verdict directly);
  `operator_note_from_verdict(verdict, case_id) -> OperatorNote`.
  OperatorNote(verdict_id, case_id, author, text, created_at).
  DEFAULT_AUTHOR="operator". Verdict shape: `subject.type="custom"`,
  `verdict_type="operator_note"`,
  `parent_ids=[case.underlying_verdict]`, `judgment.action="flag"`,
  `confidence=1.0`, `metadata.custom.author`. `_is_operator_note()`
  dual-path detection: `subject.type` OR `verdict_type` (supports
  legacy producers). submit normalises empty/whitespace author →
  DEFAULT_AUTHOR. ReasoningCaptureError(BriefError). Spec:
  opensrm-81rn.4.
- `situation_board.py` — `fetch_situation_board(client,
  breach_limit=10) -> SituationBoardView`;
  `render_situation_board(view) -> str`. Dataclasses:
  `PortfolioSnapshot(total_services, healthy, warning, critical,
  exhausted, captured_at)`, `BreachEvent(verdict_id, service, summary,
  created_at, severity str|None)`, `SituationBoardView(portfolio:
  PortfolioSnapshot|None, recent_breaches: list[BreachEvent], queue:
  CaseBenchView)`. SituationBoardError(BriefError). Three
  `asyncio.gather` parallel fetches: `_fetch_portfolio` (GET
  /assessments kind=portfolio_status limit=1),
  `_fetch_recent_breaches` (GET /verdicts
  verdict_type=quality_breach), `fetch_case_bench`. Cold start (empty
  portfolio rows) → None, not an error. `_project_portfolio`: non-dict
  guard → None; `total_services` not int → None; missing counts via
  `_int_or_zero` → 0. `_to_breach_event`: `_safe_dict()` guards nested
  subject/judgment/metadata/custom; severity only if str. Breaches
  sorted newest-first by `(created_at, id)`. Spec: opensrm-81rn.2.
- `escalation.py` — Stateful poller: `EscalationMonitor.poll(client)
  -> list[EscalationEvent]`. ESCALATION_SEVERITIES=
  frozenset{"high","critical"}, DEFAULT_POLL_LIMIT=20. Cold-start:
  first successful poll records baseline (`_seen_ids` populated) and
  returns [] — no replay spam on relaunch. Delta detection: `_seen_ids`
  set grows unbounded (acceptable at v1.5 demo scale). Connection
  failures swallowed (best-effort toasts, `_baseline_done` NOT set on
  failure so a failed first poll doesn't suppress future events).
  `_to_escalation_event()` returns None for low-severity or
  malformed. Sorted newest-first by `(created_at, id)`. EscalationEvent
  dataclass (verdict_id, service, severity, summary, created_at).
  Spec: opensrm-81rn.5.
- `write_queue.py` — In-memory FIFO for operator-note submissions
  during core outages. `WriteQueue` class: `enqueue(verdict, case_id)`
  appends `PendingNote`; `pending()` returns defensive copy;
  `drain(client)` skip-if-locked (`_drain_lock`), replays all pending,
  201→submitted/drop, 409→duplicates/drop, other errors keep queued,
  unexpected exceptions keep queued (BLE001 broad catch, logs
  warning). Stable verdict IDs at enqueue time
  (build_operator_note_verdict called once before queuing) make 409
  reply idempotent. In-memory only — no persistence across restarts
  (v2 deferred). `PendingNote(verdict, case_id, enqueued_at)`,
  `DrainResult(submitted, duplicates, remaining)`. Spec:
  opensrm-81rn.1 acceptance gap.

### `widgets/`

- `case_bench.py` — CaseBenchPanel (Vertical): mounts in
  CaseBenchScreen; polls `fetch_case_bench` every 5s
  (REFRESH_SECONDS=5.0); ListView with priority-header ListItems
  interleaved with case-row ListItems; `_items_by_id` dict maps
  `item_id` → CaseSummary; `on_list_view_selected` skips
  priority-header rows silently; posts `CaseSelected(summary)`
  message; `markup=False` on all data-bearing widgets; asyncio.Lock
  skip-if-locked reentrant refresh guard; `on_unmount` stops timer;
  `_format_row()` helper.
- `case_brief.py` — CaseBriefPanel (Vertical): mounts in case-detail
  right pane; calls `build_paging_brief`; polls every 5s; asyncio.Lock
  reentrant guard; `markup=False`; `_CAUSE_PLACEHOLDER` dict keyed by
  BriefState; `on_unmount` stops timer; BriefError renders inline,
  never crashes app.
- `case_review.py` — CaseReviewPanel (VerticalScroll): calls
  `build_post_incident_review`; polls every 5s; asyncio.Lock
  reentrant guard; `markup=False`; inline BriefError; `on_unmount`
  stops timer.
- `reasoning_capture.py` — ReasoningCapturePanel (Vertical): mounts in
  case-detail left context pane; REFRESH_SECONDS=5.0; params: client,
  case_id, author, refresh_seconds, write_queue (WriteQueue|None).
  compose: Static #notes-title, VerticalScroll #notes-list, Input
  #note-input, Static #status, Static #error. `call_later(_refresh)`
  on mount for immediate first render; each refresh calls
  `fetch_case()` (populates `_cached_case`) +
  `fetch_operator_notes()`. asyncio.Lock reentrant guard.
  `_render_notes` removes/remounts note Labels (markup=False), shows
  "(no notes yet)" when empty. `_render_error` updates `#error`
  without clearing input or notes list. `on_input_submitted`: guards
  empty/whitespace, `_submit_in_flight` bool guard against
  double-submit, spawns `asyncio.create_task(_do_submit)`.
  `_do_submit`: try/finally always clears `_submit_in_flight`; clears
  `input.value` on success, keeps input text on failure; on
  CoreUnreachableError: if write_queue and `_cached_case` available,
  builds verdict via `build_operator_note_verdict` and enqueues
  (clears input, shows "queued" status), else legacy inline-error
  path. `on_unmount` stops timer. Bench's first write path.
- `situation_board.py` — SituationBoardPanel (VerticalScroll): mounts
  in SituationBoardScreen; polls `fetch_situation_board` every 5s;
  `call_later(_refresh)` for immediate first render; two Static
  children: `#body` and `#error` (both `markup=False` — breach
  summaries embed LLM agent text); asyncio.Lock skip-if-locked
  reentrant refresh guard; CoreUnreachableError → inline error;
  SituationBoardError → inline error; `on_unmount` stops timer.

## Test suite

- `conftest.py` — autouse `_quiet_escalation_monitor` fixture: patches
  `EscalationMonitor.poll` → async no-op for all tests EXCEPT
  `test_sre_escalation.py` (exempted by `request.node.path.name`
  check); prevents unmocked `get_verdicts` round-trips from doubling
  suite runtime when escalation poller fires during `BenchApp.run_test`.
- `smoke/test_imports.py` — walks every module under `nthlayer_bench`
  via `pkgutil`; asserts every `__all__` symbol resolves via
  `getattr`.
- `smoke/test_cli.py` — asserts the `nthlayer-bench` console script is
  on PATH and `--help` exits 0 with non-empty stdout.
- `test_app.py` — `_empty_case_bench()` context manager;
  TestConnectionStatus, TestReconnectBackoff (pure
  `_compute_next_interval` tests), TestConnectionStatusBackoffState,
  TestBenchApp (creates, stores initial_case_id, closes client on
  exit, chains super on close error, exit without client, pushes
  CaseBenchScreen when no initial_case_id, pushes CaseDetailScreen on
  mount when initial_case_id set, deep-link pop returns to case bench,
  write queue + escalation integration).
- `test_cli.py` — `_validate_case_id`: unset/empty/whitespace → None,
  strips whitespace, passes normal IDs, rejects /, ?, #, leading `..`.
- `test_screens_*.py` — one file per screen.
- `test_sre_*.py` — one file per SRE logic module; defensive guards
  pinned.
- `test_widgets_*.py` — one file per widget; lifecycle, error states,
  `markup=False`, reentrant guard, double-submit guard, timer
  unmount.

## Per-feature design

### `case_bench` — P4 operator queue (opensrm-81rn.3)

Priority-grouped list of active cases. Operator home view.

- `PRIORITY_ORDER = ("P0", "P1", "P2", "P3")` — single source of truth
  for display order and membership test.
- Unrecognised priority strings land in "Other" bucket at end —
  operators never lose visibility on malformed cases.
- Bucket sort: oldest `created_at` first within each priority
  (longest-waiting cases top of bucket).
- `_age_minutes` fail-closed to None on malformed ISO 8601 / mixed
  naive/aware datetimes (mirrors `post_incident.py` pattern).
- `CaseBenchError` inherits `BriefError` — single widget-level catch
  covers all SRE surfaces.
- Logic module (`sre/case_bench.py`) has no Textual import; widget
  (`widgets/case_bench.py`) has no logic beyond calling
  `fetch_case_bench`.
- Widget double-push guard: `CaseBenchScreen` skips pushing
  `CaseDetailScreen` if topmost screen is already that case —
  operator can mash Enter without stacking duplicates.
- `_items_by_id` dict rebuilt on each refresh; priority-header
  `ListItem`s are silently skipped in selection handler.

### `brief` (opensrm-81rn.4) — P4 SRE operator surface

Spec:
`nthlayer/docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md`.

Migrated from `nthlayer-respond/feat/opensrm-0rg-cli` (sre/brief.py).
Highest-priority SRE command.

- Input: `case_id` (not incident_id or verdict_id) — cases are the
  Tier 1 first-class concept.
- Lineage anchor: `case.underlying_verdict`; walks **descendants** to
  find the response chain.
- Brief shape: current-state snapshot — latest verdict per role
  (triage / correlation / remediation), filtered on `subject.type`.
- `BriefState`: minimal → triage_complete → investigation_complete →
  remediation_proposed.
- `severity` is None when missing — never fabricated (legacy
  defaulted to 3).
- Tie-breaking on identical `created_at`: by `verdict_id`
  (deterministic).
- State derivation uses `_STATE_TABLE` lookup dict keyed by
  `(triage_present, correlation_present, remediation_present)` — no
  regex, no conditional chains.
- Logic module (`sre/brief.py`) has no Textual import; widget
  (`widgets/case_brief.py`) has no logic beyond calling
  `build_paging_brief`.

Migration receipt (legacy → v1.5):

- Sync + SQLiteVerdictStore + incident_id → async + CoreAPIClient +
  case_id.
- `by_lineage(direction="both")` → `get_descendants` only.
- No explicit BriefState field → explicit `BriefState` + `awaiting`
  list.
- `severity` defaults to 3 → None when missing (never fabricated).

Blocking dependency (Bead 1): "Respond — structured remediation
emission" must land and soak 24h before this bead. Bead 1 adds
`proposed_action` and `target` to `metadata.custom` at 4 emission
sites in nthlayer-workers respond module. The brief reads those
fields for `recommended_action` / `recommended_target`.

### `post_incident` — P4 SRE operator surface

Spec:
`nthlayer/docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md`
(section 5).

Migrated from `nthlayer-respond/feat/opensrm-0rg-cli`
(sre/post_incident.py). Second-highest-priority SRE command.

- Input: `case_id` (same anchor as brief — `case.underlying_verdict`).
- Timeline: chronological ascending by `created_at`; ties broken by
  `verdict_id` (deterministic).
- Accuracy: `outcome_resolution` verdicts matched via `parent_ids`;
  multiple resolutions for same original → latest wins; outcomes
  without `created_at` skipped (unorderable).
- `_is_outcome_resolution()` checks both `subject.type` and
  `verdict_type` — tolerates core variants.
- `worked`/`to_improve` classification: confirmed → worked,
  overridden → to_improve; `partial`/`superseded`/`expired`/None
  silently skipped (surface only clear signals).
- `severity` from latest triage verdict's
  `metadata.custom.severity` — must be `int`, never fabricated.
- `duration_minutes`: `case.created_at` → latest verdict
  `created_at`; fail-closed (None) on malformed ISO 8601, mixed
  naive/aware datetimes, or empty chain.
- `PostIncidentError` inherits `BriefError` — widget-level catch
  covers both brief and review surfaces.
- Partial review for in-progress cases: `state="in_progress"`, DRAFT
  banner rendered; no blocking wait for resolution.
- `render_post_incident_review()` produces markdown-shaped output;
  `[bold]` markers NOT parsed (markup=False in widget).
- `VerdictAccuracy` carries 5 `override_*` fields (jmy.19):
  `override_by`, `override_at`, `override_action`,
  `override_reasoning`, `override_original_action` — populated from
  `outcome.override` on the original verdict (mutation-style); all
  None when no override. When both lineage-style `outcome_resolution`
  child AND mutation-style `outcome.override` exist, lineage child
  wins for `outcome_status` (preserves existing semantics) but
  `override_*` fields are populated either way. Mutation-style
  `outcome_status` surfaced only when no lineage child exists AND
  `outcome.override.by` is non-empty (prevents empty-override
  fabrication). `render_post_incident_review()` emits `## Overrides`
  section showing attribution (by, at), action transition
  (`original_action → action`), and reasoning; section omitted
  entirely when no accuracy record has `override_by` set.

Navigation: accessed via `r` key on CaseDetailScreen → pushes
CaseReviewScreen; escape returns to case detail.

### `reasoning_capture` — P4 operator note feed (opensrm-81rn.4)

Bench's **first write path**. Operator notes attached to a case,
persisted to core as `operator_note` verdicts.

- Input: `case_id`; verdict hangs off `case.underlying_verdict`
  (parallel sibling to triage/correlation verdicts — not chained
  serially).
- Verdict shape: `subject.type="custom"` (operator_note not in
  VALID_SUBJECT_TYPES), `verdict_type="operator_note"` (typed column
  for filtering), `judgment.action="flag"`, `confidence=1.0`,
  `metadata.custom.author`.
- Dual-path detection in `_is_operator_note()`: matches `subject.type`
  OR `verdict_type` — supports legacy producers that stored role on
  subject.type before typed column existed.
- Empty/whitespace author normalised to `DEFAULT_AUTHOR="operator"` at
  submit boundary — prevents round-trip mismatch (submit writes `""`,
  fetch reads `"unknown"`).
- `ReasoningCaptureError(BriefError)` — single widget-level catch
  covers all SRE error paths.
- Widget `_submit_in_flight` bool guard: set synchronously before task
  spawn, cleared in try/finally — prevents double-POST on rapid Enter
  presses.
- On submit failure: input text retained, inline error shown — never
  silently lose operator work.
- `_render_error()` does not clear the notes list: stale notes are
  more useful than a blank pane during a network glitch.
- Logic module (`sre/reasoning_capture.py`) has no Textual import;
  widget has no logic beyond calling `fetch_operator_notes` /
  `submit_operator_note` / `build_operator_note_verdict`.
- `fetch_case()` public companion exposes the private `_get_case()` so
  the panel can cache the case dict for offline queuing without a
  fresh fetch (which would itself fail during an outage).
- `build_operator_note_verdict()` pure factory: called once at
  submit-time or at enqueue-time, never at drain-time — stable ID is
  the contract that makes 409-idempotent replay correct.

### `escalation` — P4 toast notifications (opensrm-81rn.5)

App-level breach notification surface. `EscalationMonitor` lives on
`BenchApp` so toasts fire regardless of which screen the operator is
viewing.

- Stateful class (not stateless function): delta-detection across
  polls requires per-session memory of seen verdict IDs — `_seen_ids`
  set.
- Cold-start semantics: first successful poll establishes baseline and
  returns [] — operator is not spammed with every breach in history on
  relaunch.
- Connection failures swallowed entirely: toasts are best-effort;
  operator already has inline-error UX on primary panels;
  `_baseline_done` NOT set on failure so a failed first poll doesn't
  suppress real future events.
- `ESCALATION_SEVERITIES = frozenset{"high", "critical"}` —
  low-severity breaches stay off the toast queue; only "wake up and
  look" events surface.
- `_seen_ids` grows unbounded at v1.5 scale (demo/single-operator
  sessions); prune-to-last-N deferred for large deployments.
- `_SEVERITY_TO_TEXTUAL` dict in `app.py` maps severity → Textual
  toast severity ("critical"→"error" red/persistent, "high"→"warning"
  orange) — hoisted to module scope so mapping is greppable in one
  place.
- `_escalation_lock` on `BenchApp`: skip-if-locked prevents duplicate
  toast dispatch when a slow core lets the 5s interval fire mid-poll.

### `write_queue` — P4 offline note submission (opensrm-81rn.1)

Deferred write path for operator notes during core outages. Notes
typed during an outage are queued in memory and replayed automatically
when core recovers.

- Verdict ID is stable at enqueue time:
  `build_operator_note_verdict()` is called once before queuing, so
  every replay submits the same ID → core returns 409 on duplicate →
  dropped cleanly.
- 409 is **not** an error: queue drops the entry on 409 so a
  partial-success replay (note accepted on first attempt, core died
  before ACK reached panel) never duplicates entries in the audit
  trail.
- In-memory only (v1.5): no persistence across app restarts; operator
  must re-type notes lost in a hard crash. Persistent queue deferred
  to v2 (requires local storage + replay-on-startup).
- `WriteQueue.drain()` owns its own `asyncio.Lock` (skip-if-locked):
  app-level 5s timer can fire while a previous drain is in progress on
  a slow core; second caller returns `DrainResult(remaining=N)`
  immediately rather than racing the queue.
- `_drain_write_queue()` on `BenchApp` skips entirely when queue is
  empty (`len == 0`) to avoid unnecessary core round-trips.
- Recovery toast: `_drain_write_queue` emits one `app.notify` when
  `result.submitted > 0` so operator sees confirmation; 409s
  ("already submitted") drop silently to avoid toast spam during the
  recovery window.
- `ReasoningCapturePanel` falls back to legacy inline-error path when
  `write_queue is None` or `_cached_case is None` (first-submit-
  before-first-refresh) — never silently lose operator work.

### `situation_board` — P4 operator dashboard (opensrm-81rn.2)

System-state-at-a-glance view composing three live signals: portfolio
health roll-up, recent quality breaches, and active case queue.

- Three parallel `asyncio.gather` fetches per cycle — portfolio,
  breaches, queue are independent core round-trips, no reason to
  serialise.
- Fail-closed: `status_code==0` on any fetch → CoreUnreachableError
  (single inline error rather than half-rendered dashboard).
- Cold start (worker hasn't produced a `portfolio_status` assessment
  yet) → `portfolio=None`, renderer shows "Waiting for portfolio
  data." — not an error.
- `_project_portfolio`: non-dict row → None; `total_services` not int
  → None (no fabricated counts).
- `_int_or_zero`: missing per-status count fields default to 0
  (partial-payload tolerance for older schema).
- `_to_breach_event`: `_safe_dict()` guards all nested dicts
  (subject, judgment, metadata, custom) — malformed payload never
  raises AttributeError.
- Breach `severity` only set when value is a str — non-string values
  (int, None) silently drop.
- Breaches sorted newest-first by `(created_at, id)` — deterministic
  tie-breaking.
- `SituationBoardError(BriefError)` — single widget-level catch
  covers all SRE surfaces.
- `markup=False` end-to-end on `#body` and `#error`: breach summaries
  embed LLM agent verdict text; stray Rich markup must not reformat
  the dashboard.
- `call_later(_refresh)` on mount for immediate first render —
  operator doesn't wait 5s for the dashboard to populate.
- Navigation: CaseBenchScreen 's' → push SituationBoardScreen; escape
  → pop back to bench.

## Runtime dependencies

- `nthlayer-common>=0.1.8` (editable local) — shared utilities.
- `textual>=1.0` — Textual TUI framework.
- `httpx>=0.27` — HTTP client for core API.

Dev: `pytest>=8.2`, `pytest-asyncio>=0.23`, `ruff>=0.8`.

`pyproject.toml` is authoritative.
