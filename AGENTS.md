# nthlayer-bench — agent-facing commands

Tier 3 operator TUI (Textual-based) for case management and situation
awareness. Talks to nthlayer-core exclusively via HTTP; never touches
the SQLite store directly.

## Stack

- Python ≥3.11, `uv`-managed.
- Runtime: Textual (TUI framework) + httpx (HTTP).
- Tests: `pytest`, `pytest-asyncio`.
- Lint: `ruff`.
- Typecheck: **not configured** (no `mypy.ini`, no `pyrightconfig.json`,
  no `[tool.mypy]`/`[tool.pyright]` in `pyproject.toml`). TODO: wire
  one when the rest of the ecosystem standardises.

## Build / test / lint / run commands

```bash
uv sync                                # set up .venv
uv pip install -e .                    # editable install (one-shot dev setup)
uv run pytest -q                       # full suite
uv run pytest tests/test_<name>.py -v  # single file
uv run pytest -k "<expr>" -v           # single test by name
uv run ruff check src/ tests/          # lint
nthlayer-bench --core-url http://localhost:8000        # start the TUI
nthlayer-bench --case-id <id> --core-url <url>         # deep-link to a case
```

Ecosystem testing conventions: [../nthlayer/docs/testing.md](../nthlayer/docs/testing.md).

## CI / release

- Pilot repo for `googleapis/release-please-action@v4`. Push to `main`
  inspects Conventional Commits and maintains a release PR bumping
  `pyproject.toml` and `CHANGELOG.md`. Config:
  `release-please-config.json` + `.release-please-manifest.json`.
- Conventional Commit taxonomy: `feat`/`fix`/`perf`/`deps`/`refactor`/
  `docs` surface in the changelog; `chore`/`test`/`ci`/`build`/`style`
  are hidden.
- Release PR merge → release-please cuts the GitHub release tag →
  `release.yml` runs the trusted-publishing PyPI flow.
- **Prerequisite (one-time):** repo setting "Allow GitHub Actions to
  create and approve pull requests" must be enabled
  (Settings → Actions → General → Workflow permissions). First run
  fails without it.
- **Docker smoke gate (Phase 2):** between `twine check` and the PyPI
  publish action, `release.yml` spins a `python:3.11-slim` container,
  installs the freshly-built wheel + pytest, and runs `tests/smoke/`.
  Catches stale `__all__` exports, missing runtime deps, broken entry
  points. Failure blocks publish.
- **Dependabot (Phase 4):** `.github/dependabot.yml` declares two
  ecosystems — `uv` (`pyproject.toml` + `uv.lock`) and `github-actions`
  — on Monday-morning Europe/Dublin schedule. Sibling packages
  (`nthlayer-*`) and dev deps grouped into single weekly PRs.
  Auto-merge policy in `.github/workflows/dependabot-automerge.yml`:
  - External runtime deps: auto-merge on patch only.
  - Dev deps: auto-merge on patch + minor.
  - nthlayer-ecosystem siblings + any major bump: always require
    review (workflow inspects `dependabot/fetch-metadata@v2` outputs
    and gates `gh pr merge --auto`).
- Vulnerability alerts arrive via GitHub native security advisories.
