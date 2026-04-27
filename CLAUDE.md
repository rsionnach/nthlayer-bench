# nthlayer-bench

Tier 3 operator TUI for case management and situation awareness. Textual-based terminal UI that communicates with nthlayer-core exclusively via HTTP API — never accesses the SQLite store directly.

## Architecture

```
src/nthlayer_bench/
  __init__.py   # Package marker
  cli.py        # Entry point: nthlayer-bench [-V/--version] [--core-url http://localhost:8000]; deferred import of BenchApp
  app.py        # BenchApp (Textual App): ConnectionStatus widget polls core /health every 5s; states: CONNECTED (200), DEGRADED (non-200), DISCONNECTED (httpx.RequestError/OSError); immediate check on mount via call_later; Header, Footer, status bar, main container
tests/
  test_app.py   # TestConnectionStatus (initial_state, connected, degraded, disconnected), TestBenchApp (creates)
```

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
