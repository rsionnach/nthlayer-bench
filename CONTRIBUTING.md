# Contributing to nthlayer-bench

Thank you for considering contributing to **nthlayer-bench** — the Tier 3
operator TUI (Textual-based) for case management and situation awareness.
We're in active v1.5 development and welcome feedback from the SRE/DevOps
community.

Bench talks to `nthlayer-core` **exclusively over HTTP** — never the SQLite
store directly.

## Ways to Contribute

- **Report bugs / request features** — [open an issue](https://github.com/rsionnach/nthlayer-bench/issues).
- **Discuss** — [GitHub Discussions](https://github.com/rsionnach/nthlayer/discussions) for the wider ecosystem.
- **Code & docs** — pull requests welcome (see below).

## Development Setup

```bash
# Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone alongside nthlayer-common (bench depends on it via a sibling path)
git clone https://github.com/rsionnach/nthlayer-common.git
git clone https://github.com/rsionnach/nthlayer-bench.git
cd nthlayer-bench
uv sync --extra dev                  # creates .venv with test/lint tools

# Run the TUI against a local core
# (optional; requires a running nthlayer-core — see its CONTRIBUTING.md.
#  Not needed for tests/lint: sre/ logic is tested without the TUI.)
uv run nthlayer-bench --core-url http://localhost:8000

# Run the test suite
uv run pytest -q                     # full suite
uv run pytest tests/test_<name>.py -v  # a single file
uv run pytest -k "<expr>" -v         # by name

# Lint
uv run ruff check src/ tests/
```

> **Sibling dependency & Python.** `pyproject.toml` declares
> `nthlayer-common = { path = "../nthlayer-common", editable = true }`, so
> `nthlayer-common` must sit next to this repo on disk or `uv sync` fails to
> resolve it. Requires Python 3.11+ (`uv` will provision it via
> `uv python install` if needed).

A clean clone to a green `uv run pytest -q` should take well under five
minutes.

## Pull Request Process

1. Fork the repository and create a feature branch off `main`
   (`git checkout -b feat/your-change`).
2. Make your change with tests.
3. Ensure tests pass: `uv run pytest -q`.
4. Ensure lint passes: `uv run ruff check src/ tests/`.
5. Commit using Conventional Commits (see below).
6. Push to your fork and open a PR against `main`.

Commits land on `main`; `release-please` maintains the release PR. A
Docker-based smoke gate (`tests/smoke/`) runs between `twine check` and PyPI
publish.

## Development Guidelines

### Code Style

- Python 3.11+, type hints encouraged.
- Ruff for linting: `uv run ruff check src/ tests/`.
- **Keep logic Textual-free.** Business logic in `sre/` must not import
  Textual so it stays unit-testable; the TUI layer consumes it.
- Set `markup=False` on every data-bearing `Static` / `Label` so arbitrary
  content can't be interpreted as Textual markup.

### Commit Messages

```
<type>: <description>

<optional body>
```

`feat` / `fix` / `perf` / `deps` / `refactor` / `docs` surface in the
changelog; `chore` / `test` / `ci` / `build` / `style` are hidden.

### Testing

- Add tests for new behaviour; exercise `sre/` logic directly (no TUI needed).
- Run a single file with `-v` while iterating; run the full `-q` suite before
  opening a PR.

## Finding Something to Work On

Browse [open issues](https://github.com/rsionnach/nthlayer-bench/issues) and
look for `good-first-issue` / `help-wanted` labels. Maintainers track detailed
work in **Beads**, a Dolt-backed board in the `opensrm` repo
(`cd ../opensrm && bd ready --json`) — you don't need it to contribute.

## Code of Conduct

Be respectful and constructive — we're all here to build better reliability
tooling.

## Questions?

- [GitHub Issues](https://github.com/rsionnach/nthlayer-bench/issues) — bugs and features.
- [GitHub Discussions](https://github.com/rsionnach/nthlayer/discussions) — general questions.

## License

By contributing, you agree that your contributions will be licensed under
this repository's license (see `LICENSE`).

---

**Thank you for helping make NthLayer better!**
