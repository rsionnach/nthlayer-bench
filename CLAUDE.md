# nthlayer-bench

Tier 3 operator TUI for case management and situation awareness. Textual-based terminal UI that communicates with nthlayer-core exclusively via HTTP API — never accesses the SQLite store directly.

## Architecture

```
src/nthlayer_bench/
  __init__.py   # Package marker
  cli.py        # Entry point: nthlayer-bench [-V/--version] [--core-url http://localhost:8000]; deferred import of BenchApp
  app.py        # BenchApp (Textual App): ConnectionStatus widget polls core /health every 5s; states: CONNECTED (200), DEGRADED (non-200), DISCONNECTED (httpx.RequestError/OSError); immediate check on mount via call_later; Header, Footer, status bar, main container
  sre/
    __init__.py
    brief.py    # Pure async logic: build_paging_brief(client, case_id) → PagingBrief; render_brief(brief) → str (tests + future CLI wrappers). No Textual import. PagingBrief fields: case_id, service, severity, summary, likely_cause, cause_confidence, blast_radius, recommended_action, recommended_target, state, awaiting. BriefError hierarchy: CaseNotFoundError, AnchorVerdictMissingError, CoreUnreachableError.
  widgets/
    __init__.py
    case_brief.py  # CaseBriefPanel (Textual Static): mounts in case-detail right pane; calls build_paging_brief; polls every 5s; renders state-aware content per BriefState; BriefError renders inline, never crashes app.
tests/
  test_app.py          # TestConnectionStatus (initial_state, connected, degraded, disconnected), TestBenchApp (creates)
  test_sre_brief.py    # 15 logic-module cases + renderer tests (mock CoreAPIClient, dict payloads not dataclasses)
  test_widgets_case_brief.py  # Widget state rendering per BriefState; BriefError inline error states via App.run_test()
```

### `brief` command (opensrm-81rn.4) — P4 SRE operator surface

Spec: `docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md`

Migrated from `nthlayer-respond/feat/opensrm-0rg-cli` (sre/brief.py). Highest-priority SRE command.

**Key design decisions:**
- Input: `case_id` (not incident_id or verdict_id) — cases are the Tier 1 first-class concept
- Lineage anchor: `case.underlying_verdict`; walks **descendants** to find the response chain
- Brief shape: current-state snapshot — latest verdict per role (triage / correlation / remediation), filtered on `subject.type`
- `BriefState`: `minimal` → `triage_complete` → `investigation_complete` → `remediation_proposed`
- `severity` is `None` when missing — never fabricated (legacy defaulted to 3)
- Tie-breaking on identical `created_at`: by `verdict_id` (deterministic)
- Logic module (`sre/brief.py`) has no Textual import; widget (`widgets/case_brief.py`) has no logic beyond calling `build_paging_brief`

**Migration receipt (legacy → v1.5):**
- Sync + SQLiteVerdictStore + incident_id → async + CoreAPIClient + case_id
- `by_lineage(direction="both")` → `get_descendants` only
- No explicit BriefState field → explicit `BriefState` + `awaiting` list
- `severity` defaults to 3 → `None` when missing (never fabricated)

**Blocking dependency (Bead 1):** “Respond — structured remediation emission” must land and soak 24h before this bead. Bead 1 adds `proposed_action` and `target` to `metadata.custom` at 4 emission sites in nthlayer-workers respond module. The brief reads those fields for `recommended_action` / `recommended_target`.

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
