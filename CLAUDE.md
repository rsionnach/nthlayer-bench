# nthlayer-bench

Tier 3 operator TUI for case management and situation awareness. Textual-based terminal UI that communicates with nthlayer-core exclusively via HTTP API — never accesses the SQLite store directly.

## Architecture

```
src/nthlayer_bench/
  __init__.py   # Package marker
  cli.py        # Entry point: nthlayer-bench [-V/--version] [--core-url URL] [--case-id ID]; _validate_case_id() rejects path-altering chars (/, ?, #) and leading ..; deferred import of BenchApp
  app.py        # BenchApp (Textual App): ConnectionStatus widget polls core /health with exponential backoff (base=5s, doubles per failure, cap=60s; _compute_next_interval() pure helper; _consecutive_failures counter reset on success or non-200); states: CONNECTED (200), DEGRADED (non-200), DISCONNECTED (httpx.RequestError/OSError); on_mount pushes CaseBenchScreen (home view), starts escalation poller (ESCALATION_POLL_SECONDS=5.0) + write-queue drainer (WRITE_QUEUE_DRAIN_SECONDS=5.0), then pushes CaseDetailScreen on top if initial_case_id set (deferred imports); lazy CoreAPIClient property (shared pool, single instance per app run); _on_exit_app closes client in try/finally so Textual super cleanup always runs; _escalation_monitor (EscalationMonitor, one-per-session); _escalation_lock (asyncio.Lock, skip-if-locked guard against duplicate toasts); _write_queue (WriteQueue, in-memory FIFO); write_queue property (public accessor for screens); _poll_escalations() dispatches toasts via _notify_escalation; _drain_write_queue() replays pending notes, notifies operator on recovery; _notify_escalation() maps severity via _SEVERITY_TO_TEXTUAL ("critical"→"error"/"red persistent", "high"→"warning"/"orange")
  screens/
    __init__.py          # Exports CaseBenchScreen, CaseDetailScreen, CaseReviewScreen, SituationBoardScreen
    case_bench.py        # CaseBenchScreen (Textual Screen): operator home view (no escape/q — app-level quit is the exit path); mounts CaseBenchPanel; handles CaseBenchPanel.CaseSelected → pushes CaseDetailScreen; double-push guard (skip if topmost screen is already CaseDetailScreen for same case_id); deferred import of CaseDetailScreen; 's' binding → action_open_situation_board → pushes SituationBoardScreen (deferred import)
    case_detail.py       # CaseDetailScreen (Textual Screen): two-pane layout (36-col context pane + CaseBriefPanel right pane); left pane: Static "Case Context", Static case_id, live ReasoningCapturePanel(client, case_id, write_queue=app.write_queue); passes write_queue via getattr(self.app, "write_queue", None) — falls back to None for legacy inline-error behaviour if app doesn't expose one; escape/q → pop_screen; r → push CaseReviewScreen (deferred import); case_id property (used by double-push guard in CaseBenchScreen); never constructs its own CoreAPIClient (uses app.client)
    case_review.py       # CaseReviewScreen (Textual Screen): single-pane with CaseReviewPanel; escape/q → pop_screen; pushed from CaseDetailScreen via r key
    situation_board.py   # SituationBoardScreen (Textual Screen): system-state-at-a-glance dashboard; pushed from CaseBenchScreen via 's'; escape/q → pop_screen; composes SituationBoardPanel; never constructs its own CoreAPIClient
  sre/
    __init__.py          # Exports all brief + case_bench + reasoning_capture + post_incident + situation_board + escalation + write_queue public names
    brief.py             # Pure async logic: build_paging_brief(client, case_id) → PagingBrief; render_brief(brief) → str. No Textual import. PagingBrief fields: case_id, service, severity, summary, likely_cause, cause_confidence, blast_radius, recommended_action, recommended_target, state, awaiting. BriefError hierarchy: CaseNotFoundError, AnchorVerdictMissingError, CoreUnreachableError.
    case_bench.py        # Pure async logic: fetch_case_bench(client, state, limit, now) → CaseBenchView; CaseSummary dataclass (case_id, priority, service, state, created_at, age_minutes, briefing); CaseBenchView dataclass (ordered_priorities, cases_by_priority, flat); PRIORITY_ORDER=("P0","P1","P2","P3"); unrecognised priority → "Other" bucket; _age_minutes fail-closed to None on malformed ISO/mixed tz; _group_and_sort: known priorities in canonical order then extras alphabetically; render_case_bench(view) → str; CaseBenchError(BriefError)
    post_incident.py     # Pure async logic: build_post_incident_review(client, case_id) → PostIncidentReview; render_post_incident_review(review) → str. No Textual import. Chronological timeline (asc by created_at, ties by verdict_id); accuracy via outcome_resolution verdicts (parent_ids match, latest-wins); worked/to_improve classification (confirmed→worked, overridden→to_improve). PostIncidentError(BriefError). ReviewState: "in_progress" | "resolved". severity from latest triage metadata.custom.severity (int only, never fabricated). _is_outcome_resolution() checks both subject.type and verdict_type.
    reasoning_capture.py # Pure async logic: fetch_case(client, case_id) → dict (public case fetch, used to prime write-queue cache); fetch_operator_notes(client, case_id) → list[OperatorNote]; submit_operator_note(client, case_id, text, *, author) → OperatorNote; build_operator_note_verdict(case, text, *, author) → Verdict (pure factory, no I/O, stable ID at build time for 409-idempotent queue replay, raises ValueError empty text / AnchorVerdictMissingError missing anchor); submit_operator_note_verdict(client, verdict) → APIResult (submits pre-built Verdict directly); operator_note_from_verdict(verdict, case_id) → OperatorNote. No Textual import. OperatorNote dataclass (verdict_id, case_id, author, text, created_at). DEFAULT_AUTHOR="operator". Verdict shape: subject.type="custom", verdict_type="operator_note", parent_ids=[case.underlying_verdict], judgment.action="flag", confidence=1.0, metadata.custom.author. _is_operator_note(): dual-path detection — subject.type OR verdict_type (supports legacy producers). submit normalises empty/whitespace author → DEFAULT_AUTHOR. ReasoningCaptureError(BriefError). Spec: opensrm-81rn.4.
    situation_board.py   # Pure async logic: fetch_situation_board(client, breach_limit=10) → SituationBoardView; render_situation_board(view) → str. No Textual import. Dataclasses: PortfolioSnapshot (total_services, healthy, warning, critical, exhausted, captured_at), BreachEvent (verdict_id, service, summary, created_at, severity str|None), SituationBoardView (portfolio: PortfolioSnapshot|None, recent_breaches: list[BreachEvent], queue: CaseBenchView). SituationBoardError(BriefError). Three asyncio.gather parallel fetches: _fetch_portfolio (GET /assessments kind=portfolio_status limit=1), _fetch_recent_breaches (GET /verdicts verdict_type=quality_breach), fetch_case_bench. Cold start (empty portfolio rows) → None, not an error. _project_portfolio: non-dict guard → None; total_services not int → None; missing counts via _int_or_zero → 0. _to_breach_event: _safe_dict() guards nested subject/judgment/metadata/custom; severity only if str. Breaches sorted newest-first by (created_at, id). Spec: opensrm-81rn.2.
    escalation.py        # Stateful poller: EscalationMonitor.poll(client) → list[EscalationEvent]. No Textual import. ESCALATION_SEVERITIES=frozenset{"high","critical"}, DEFAULT_POLL_LIMIT=20. Cold-start: first successful poll records baseline (_seen_ids populated) and returns [] — no replay spam on relaunch. Delta detection: _seen_ids set grows unbounded (acceptable at v1.5 demo scale). Connection failures swallowed (best-effort toasts, _baseline_done NOT set on failure so a failed first poll doesn't suppress future events). _to_escalation_event(): returns None for low-severity or malformed. Sorted newest-first by (created_at, id). EscalationEvent dataclass (verdict_id, service, severity, summary, created_at). Spec: opensrm-81rn.5.
    write_queue.py       # In-memory FIFO for operator-note submissions during core outages. WriteQueue class: enqueue(verdict, case_id) appends PendingNote; pending() returns defensive copy; drain(client) skip-if-locked (_drain_lock), replays all pending, 201→submitted/drop, 409→duplicates/drop, other errors keep queued, unexpected exceptions keep queued (BLE001 broad catch, logs warning). Stable verdict IDs at enqueue time (build_operator_note_verdict called once before queuing) make 409 reply idempotent. In-memory only — no persistence across restarts (v2 deferred). PendingNote dataclass (verdict, case_id, enqueued_at). DrainResult dataclass (submitted, duplicates, remaining). Spec: opensrm-81rn.1 acceptance gap.
  widgets/
    __init__.py          # Exports CaseBenchPanel, CaseBriefPanel, CaseReviewPanel, ReasoningCapturePanel, SituationBoardPanel
    case_bench.py        # CaseBenchPanel (Textual Vertical): mounts in CaseBenchScreen; polls fetch_case_bench every 5s (REFRESH_SECONDS=5.0); ListView with priority-header ListItems interleaved with case-row ListItems; _items_by_id dict maps item_id → CaseSummary; on_list_view_selected skips priority-header rows silently; posts CaseSelected(summary) message; markup=False on all data-bearing widgets; asyncio.Lock skip-if-locked reentrant refresh guard; on_unmount stops timer; _format_row() helper
    case_brief.py        # CaseBriefPanel (Textual Vertical): mounts in case-detail right pane; calls build_paging_brief; polls every 5s (REFRESH_SECONDS constant); asyncio.Lock skip-if-locked reentrant refresh guard; markup=False on all data-bearing Static fields; _CAUSE_PLACEHOLDER dict keyed by BriefState; on_unmount stops timer; BriefError renders inline, never crashes app.
    case_review.py       # CaseReviewPanel (Textual VerticalScroll): calls build_post_incident_review; polls every 5s (REFRESH_SECONDS); asyncio.Lock skip-if-locked guard; markup=False on all Static fields; inline BriefError error states; on_unmount stops timer
    reasoning_capture.py # ReasoningCapturePanel (Textual Vertical): mounts in case-detail left context pane; REFRESH_SECONDS=5.0; params: client, case_id, author, refresh_seconds, write_queue (WriteQueue|None); compose: Static #notes-title, VerticalScroll #notes-list, Input #note-input, Static #status, Static #error; call_later(_refresh) on mount for immediate first render; each refresh calls fetch_case() (populates _cached_case) + fetch_operator_notes(); asyncio.Lock skip-if-locked reentrant refresh guard; _render_notes: removes/remounts note Labels (markup=False), shows "(no notes yet)" when empty; _render_error: updates #error without clearing input or notes list; on_input_submitted: guards empty/whitespace, _submit_in_flight bool guard against double-submit, spawns asyncio.create_task(_do_submit); _do_submit: try/finally always clears _submit_in_flight; clears input.value on success, keeps input text on failure; on CoreUnreachableError: if write_queue and _cached_case available, builds verdict via build_operator_note_verdict and enqueues (clears input, shows "queued" status), else legacy inline-error path; on_unmount stops timer; bench's first write path
    situation_board.py   # SituationBoardPanel (Textual VerticalScroll): mounts in SituationBoardScreen; polls fetch_situation_board every 5s (REFRESH_SECONDS=5.0); call_later(_refresh) for immediate first render; two Static children: #body and #error (both markup=False — breach summaries embed LLM agent text); asyncio.Lock skip-if-locked reentrant refresh guard; CoreUnreachableError → inline error; SituationBoardError → inline error; on_unmount stops timer
tests/
  conftest.py                        # autouse _quiet_escalation_monitor fixture: patches EscalationMonitor.poll → async no-op for all tests EXCEPT test_sre_escalation.py (exempted by request.node.path.name check); prevents unmocked get_verdicts round-trips from doubling suite runtime when escalation poller fires during BenchApp.run_test
  test_app.py                        # _empty_case_bench() context manager patches fetch_case_bench → empty CaseBenchView (guards lifecycle tests from auto-poll); TestConnectionStatus (initial_state, connected, degraded, disconnected); TestReconnectBackoff (pure _compute_next_interval tests: base interval, negative defensive, doubles to 10/20/40, caps at 60, long outage stays capped); TestConnectionStatusBackoffState (failures increment on connection error, resets on success, degraded non-200 also resets, schedules next check at backed-off interval); TestBenchApp (creates, stores initial_case_id, closes client on exit, chains super on close error, exit without client, pushes CaseBenchScreen when no initial_case_id, pushes CaseDetailScreen on mount when initial_case_id set, deep-link pop returns to case bench, write queue + escalation integration)
  test_cli.py                        # _validate_case_id: unset/empty/whitespace → None, strips whitespace, passes normal IDs, rejects /, ?, #, leading ..
  test_screens_case_bench.py         # CaseBenchScreen: mounts CaseBenchPanel, selecting case pushes CaseDetailScreen, double-push guard (re-select same case doesn't stack duplicate screens), pop then reselect pushes freshly
  test_screens_case_detail.py        # CaseDetailScreen: mounts brief panel, renders context pane with case_id, escape pops screen, push/pop/push reuses app client, mounts live ReasoningCapturePanel wired to same case_id
  test_screens_case_review.py        # CaseReviewScreen: mounts review panel, r key pushes review from detail, escape returns to detail
  test_screens_situation_board.py    # SituationBoardScreen: mounts SituationBoardPanel; 's' key from CaseBenchScreen pushes SituationBoardScreen; escape returns to case bench (symmetric navigation)
  test_sre_brief.py                  # build_paging_brief + render_brief: spec cases, R5 defensive guards (missing service→"unknown", malformed descendant dropped, missing metadata safe defaults, string severity fallback), renderer tests
  test_sre_escalation.py             # EscalationMonitor: first poll returns [] (cold start / baseline), empty first poll sets baseline_done, new event after baseline fires, multiple new events in one cycle, duplicate suppression (_seen_ids), low-severity filtered out, missing severity filtered out, non-dict rows dropped, connection failure returns [] without setting baseline_done, baseline_done survives connection error, _to_escalation_event projection
  test_sre_reasoning_capture.py      # fetch_operator_notes: chronological sort, filters to operator notes only, legacy subject.type detection, verdict_type-only detection, extracts author/text/timestamp, CaseNotFoundError (404), AnchorVerdictMissingError, CoreUnreachableError (status_code=0), empty descendants; submit_operator_note: happy path writes correct verdict fields, empty text raises ValueError, whitespace author normalised to DEFAULT_AUTHOR, CoreUnreachableError on network failure, non-201 raises ReasoningCaptureError; build_operator_note_verdict: pure factory, stable ID, author normalisation, raises on empty text/missing anchor; submit_operator_note_verdict: submits pre-built Verdict directly (APIResult ok=True→201, APIResult ok=False→ReasoningCaptureError)
  test_sre_write_queue.py            # WriteQueue: empty queue len/pending, enqueue appends PendingNote, pending() defensive copy, drain empty no-call; drain: 201→submitted/drop, 409→duplicates/drop, transient error keeps queued, unexpected exception keeps queued, multiple items mixed outcomes, skip-if-locked concurrent drain returns immediate DrainResult
  test_sre_case_bench.py             # fetch_case_bench: priority grouping in canonical order, unrecognised priority → Other bucket, age computation, CoreUnreachableError on status_code=0, CaseBenchError on non-200; render_case_bench tests
  test_sre_post_incident.py          # build_post_incident_review: resolved/in-progress state, chronological timeline ordering, summary→reasoning fallback, accuracy matching, worked/to_improve classification, error propagation
  test_sre_situation_board.py        # fetch_situation_board: portfolio projection (full, cold start→None, malformed→None, missing counts→0), breach feed (sorted newest-first, field extraction, no-severity→None, non-dict rows dropped), queue integration, CoreUnreachableError on status_code=0, SituationBoardError on non-2xx; render_situation_board (populated, cold-start placeholders)
  test_widgets_case_bench.py         # CaseBenchPanel: priority count status line, empty view "No active cases.", ListView populated with headers + rows, selection fires CaseSelected, priority-header row not selectable, CoreUnreachableError inline render, asyncio.Lock reentrant guard, timer unmount lifecycle
  test_widgets_reasoning_capture.py  # ReasoningCapturePanel: renders existing notes on mount, placeholder when no notes, submit happy path clears input, empty input dropped silently, keeps input and shows error on CoreUnreachableError, keeps input on ReasoningCaptureError, asyncio.Lock reentrant guard, _submit_in_flight double-submit guard, timer unmount lifecycle, markup=False; write queue integration: enqueues on CoreUnreachableError when write_queue+_cached_case available (clears input, shows "queued" status), falls back to inline error when no write_queue, falls back when no _cached_case; fetch_case called on each refresh to keep cache warm
  test_widgets_case_brief.py         # CaseBriefPanel: state-aware rendering per BriefState, inline BriefError error states, REFRESH_SECONDS pin, markup=False pin, asyncio.Lock reentrant guard, on_unmount timer lifecycle
  test_widgets_case_review.py        # CaseReviewPanel: renders review body, DRAFT banner for in-progress, inline error states, markup=False pin, asyncio.Lock reentrant guard, timer unmount lifecycle
  test_widgets_situation_board.py    # SituationBoardPanel: renders populated dashboard, cold-start placeholders, inline CoreUnreachableError, inline SituationBoardError, markup=False pin (Rich markup in breach summaries not parsed), asyncio.Lock reentrant guard, on_unmount timer lifecycle
```

### `case_bench` — P4 operator queue (opensrm-81rn.3)

Priority-grouped list of active cases. Operator home view.

**Key design decisions:**
- `PRIORITY_ORDER = ("P0", "P1", "P2", "P3")` — single source of truth for display order and membership test
- Unrecognised priority strings land in "Other" bucket at end — operators never lose visibility on malformed cases
- Bucket sort: oldest `created_at` first within each priority (longest-waiting cases top of bucket)
- `_age_minutes` fails closed to `None` on malformed ISO 8601, mixed naive/aware datetimes (mirrors `post_incident.py` pattern)
- `CaseBenchError` inherits `BriefError` — single widget-level catch covers all SRE surfaces
- Logic module (`sre/case_bench.py`) has no Textual import; widget (`widgets/case_bench.py`) has no logic beyond calling `fetch_case_bench`
- Widget double-push guard: `CaseBenchScreen` skips pushing `CaseDetailScreen` if topmost screen is already that case — operator can mash enter without stacking duplicates
- `_items_by_id` dict rebuilt on each refresh; priority-header `ListItem`s are silently skipped in selection handler (not in the map)

### `brief` command (opensrm-81rn.4) — P4 SRE operator surface

Spec: [`docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md`](https://github.com/rsionnach/nthlayer/blob/main/docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md) (in `nthlayer/` ecosystem hub)

Migrated from `nthlayer-respond/feat/opensrm-0rg-cli` (sre/brief.py). Highest-priority SRE command.

**Key design decisions:**
- Input: `case_id` (not incident_id or verdict_id) — cases are the Tier 1 first-class concept
- Lineage anchor: `case.underlying_verdict`; walks **descendants** to find the response chain
- Brief shape: current-state snapshot — latest verdict per role (triage / correlation / remediation), filtered on `subject.type`
- `BriefState`: `minimal` → `triage_complete` → `investigation_complete` → `remediation_proposed`
- `severity` is `None` when missing — never fabricated (legacy defaulted to 3)
- Tie-breaking on identical `created_at`: by `verdict_id` (deterministic)
- State derivation uses `_STATE_TABLE` lookup dict keyed by `(triage_present, correlation_present, remediation_present)` — no regex, no conditional chains
- Logic module (`sre/brief.py`) has no Textual import; widget (`widgets/case_brief.py`) has no logic beyond calling `build_paging_brief`

**Migration receipt (legacy → v1.5):**
- Sync + SQLiteVerdictStore + incident_id → async + CoreAPIClient + case_id
- `by_lineage(direction="both")` → `get_descendants` only
- No explicit BriefState field → explicit `BriefState` + `awaiting` list
- `severity` defaults to 3 → `None` when missing (never fabricated)

**Blocking dependency (Bead 1):** “Respond — structured remediation emission” must land and soak 24h before this bead. Bead 1 adds `proposed_action` and `target` to `metadata.custom` at 4 emission sites in nthlayer-workers respond module. The brief reads those fields for `recommended_action` / `recommended_target`.

### `post_incident` command — P4 SRE operator surface

Spec: [`docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md`](https://github.com/rsionnach/nthlayer/blob/main/docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md) (in `nthlayer/` ecosystem hub, section 5)

Migrated from `nthlayer-respond/feat/opensrm-0rg-cli` (sre/post_incident.py). Second-highest-priority SRE command.

**Key design decisions:**
- Input: `case_id` (same anchor as brief — `case.underlying_verdict`)
- Timeline: chronological ascending by `created_at`; ties broken by `verdict_id` (deterministic)
- Accuracy: `outcome_resolution` verdicts matched via `parent_ids`; multiple resolutions for same original → latest wins; outcomes without `created_at` skipped (unorderable)
- `_is_outcome_resolution()` checks both `subject.type` and `verdict_type` — tolerates core variants
- `worked`/`to_improve` classification: `confirmed` → worked, `overridden` → to_improve; `partial`/`superseded`/`expired`/`None` silently skipped (surface only clear signals)
- `severity` from latest triage verdict's `metadata.custom.severity` — must be `int`, never fabricated
- `duration_minutes`: `case.created_at` → latest verdict `created_at`; fails closed (`None`) on malformed ISO 8601, mixed naive/aware datetimes, or empty chain
- `PostIncidentError` inherits `BriefError` — widget-level catch covers both brief and review surfaces
- Partial review for in-progress cases: state=`”in_progress”`, DRAFT banner rendered; no blocking wait for resolution
- `render_post_incident_review()` produces markdown-shaped output; `[bold]` markers NOT parsed (markup=False in widget)

**Navigation:** Accessed via `r` key on `CaseDetailScreen` → pushes `CaseReviewScreen`; `escape` returns to case detail.

### `reasoning_capture` — P4 operator note feed (opensrm-81rn.4)

Bench's first write path. Operator notes attached to a case, persisted to core as `operator_note` verdicts.

**Key design decisions:**
- Input: `case_id`; verdict hangs off `case.underlying_verdict` (parallel sibling to triage/correlation verdicts — not chained serially)
- Verdict shape: `subject.type="custom"` (operator_note not in VALID_SUBJECT_TYPES), `verdict_type="operator_note"` (typed column for filtering), `judgment.action="flag"`, `confidence=1.0`, `metadata.custom.author`
- Dual-path detection in `_is_operator_note()`: matches `subject.type` OR `verdict_type` — supports legacy producers that stored role on subject.type before typed column existed
- Empty/whitespace author normalised to `DEFAULT_AUTHOR="operator"` at submit boundary — prevents round-trip mismatch (submit writes `""`, fetch reads `"unknown"`)
- `ReasoningCaptureError(BriefError)` — single widget-level catch covers all SRE error paths
- Widget `_submit_in_flight` bool guard: set synchronously before task spawn, cleared in `try/finally` — prevents double-POST on rapid Enter presses
- On submit failure: input text retained, inline error shown — never silently lose operator work
- `_render_error()` does not clear the notes list: stale notes are more useful than a blank pane during a network glitch
- Logic module (`sre/reasoning_capture.py`) has no Textual import; widget (`widgets/reasoning_capture.py`) has no logic beyond calling `fetch_operator_notes` / `submit_operator_note` / `build_operator_note_verdict`
- `fetch_case()` public companion exposes the private `_get_case()` so the panel can cache the case dict for offline queuing without a fresh fetch (which would itself fail during an outage)
- `build_operator_note_verdict()` pure factory: called once at submit-time or at enqueue-time, never at drain-time — stable ID is the contract that makes 409-idempotent replay correct

### `escalation` — P4 toast notifications (opensrm-81rn.5)

App-level breach notification surface. `EscalationMonitor` lives on `BenchApp` so toasts fire regardless of which screen the operator is viewing.

**Key design decisions:**
- Stateful class (not stateless function): delta-detection across polls requires per-session memory of seen verdict IDs — `_seen_ids` set
- Cold-start semantics: first successful poll establishes baseline and returns `[]` — operator is not spammed with every breach in history on relaunch
- Connection failures swallowed entirely: toasts are best-effort; operator already has inline-error UX on primary panels; `_baseline_done` NOT set on failure so a failed first poll doesn't suppress real future events
- `ESCALATION_SEVERITIES = frozenset{"high", "critical"}` — low-severity breaches stay off the toast queue; only "wake up and look" events surface
- `_seen_ids` grows unbounded at v1.5 scale (demo/single-operator sessions); prune-to-last-N deferred for large deployments
- `_SEVERITY_TO_TEXTUAL` dict in `app.py` maps severity → Textual toast severity ("critical"→"error" red/persistent, "high"→"warning" orange) — hoisted to module scope so mapping is greppable in one place
- `_escalation_lock` on `BenchApp`: skip-if-locked prevents duplicate toast dispatch when a slow core lets the 5s interval fire mid-poll

### `write_queue` — P4 offline note submission (opensrm-81rn.1)

Deferred write path for operator notes during core outages. Notes typed during an outage are queued in memory and replayed automatically when core recovers.

**Key design decisions:**
- Verdict ID is stable at enqueue time: `build_operator_note_verdict()` is called once before queuing, so every replay submits the same ID → core returns 409 on duplicate → dropped cleanly
- 409 is not an error: queue drops the entry on 409 so a partial-success replay (note accepted on first attempt, core died before ACK reached panel) never duplicates entries in the audit trail
- In-memory only (v1.5): no persistence across app restarts; operator must re-type notes lost in a hard crash. Persistent queue deferred to v2 (requires local storage + replay-on-startup)
- `WriteQueue.drain()` owns its own `asyncio.Lock` (skip-if-locked): app-level 5s timer can fire while a previous drain is in progress on a slow core; second caller returns `DrainResult(remaining=N)` immediately rather than racing the queue
- `_drain_write_queue()` on `BenchApp` skips entirely when queue is empty (`len == 0`) to avoid unnecessary core round-trips
- Recovery toast: `_drain_write_queue` emits one `app.notify` when `result.submitted > 0` so operator sees confirmation; 409s ("already submitted") drop silently to avoid toast spam during the recovery window
- `ReasoningCapturePanel` falls back to legacy inline-error path when `write_queue is None` or `_cached_case is None` (first-submit-before-first-refresh) — never silently lose operator work

### `situation_board` — P4 operator dashboard (opensrm-81rn.2)

System-state-at-a-glance view composing three live signals: portfolio health roll-up, recent quality breaches, and active case queue.

**Key design decisions:**
- Three parallel `asyncio.gather` fetches per cycle — portfolio, breaches, queue are independent core round-trips, no reason to serialise
- Fail-closed: `status_code==0` on any fetch → `CoreUnreachableError` (single inline error rather than half-rendered dashboard)
- Cold start (worker hasn't produced a `portfolio_status` assessment yet) → `portfolio=None`, renderer shows "Waiting for portfolio data." — not an error
- `_project_portfolio`: non-dict row → `None`; `total_services` not `int` → `None` (no fabricated counts)
- `_int_or_zero`: missing per-status count fields default to `0` (partial-payload tolerance for older schema)
- `_to_breach_event`: `_safe_dict()` guards all nested dicts (subject, judgment, metadata, custom) — malformed payload never raises `AttributeError`
- Breach `severity` only set when value is a `str` — non-string values (int, None) silently drop
- Breaches sorted newest-first by `(created_at, id)` — deterministic tie-breaking
- `SituationBoardError(BriefError)` — single widget-level catch covers all SRE surfaces
- `markup=False` end-to-end on `#body` and `#error`: breach summaries embed LLM agent verdict text; stray Rich markup must not reformat the dashboard
- `call_later(_refresh)` on mount for immediate first render — operator doesn't wait 5s for the dashboard to populate
- Logic module (`sre/situation_board.py`) has no Textual import; widget (`widgets/situation_board.py`) has no logic beyond calling `fetch_situation_board`
- Navigation: `CaseBenchScreen` 's' → push `SituationBoardScreen`; `escape` → pop back to bench

## Commands

```bash
# Run tests
uv run pytest

# Install (editable)
uv pip install -e .

# Start TUI
nthlayer-bench --core-url http://localhost:8000
```

## Dependencies

- `nthlayer-common>=0.1.8` (editable local) — shared utilities
- `textual>=1.0` — Textual TUI framework
- `httpx>=0.27` — HTTP client for core API

Dev: `pytest>=8.2`, `pytest-asyncio>=0.23`, `ruff>=0.8`

## Documentation

- `README.md` — added 2026-04-28; project-level overview for GitHub and contributors

## CI / Release pipeline

nthlayer-bench is the pilot repo for `googleapis/release-please-action@v4`. On every push to `main`, release-please inspects Conventional Commits and maintains a release PR that bumps `pyproject.toml` and appends `CHANGELOG.md`. Config lives in `release-please-config.json` (package type `python`, `changelog-sections` filter) and `.release-please-manifest.json` (current version anchor). Commit taxonomy: `feat`/`fix`/`perf`/`deps`/`refactor`/`docs` surface in the changelog; `chore`/`test`/`ci`/`build`/`style` are hidden. When the release PR is merged, release-please creates the GitHub release tag and the existing `release.yml` (trusted-publishing PyPI flow) fires unchanged. Prerequisite: repo setting "Allow GitHub Actions to create and approve pull requests" must be enabled (Settings → Actions → General → Workflow permissions) — the first run fails without it.
