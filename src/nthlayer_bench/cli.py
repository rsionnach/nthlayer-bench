"""NthLayer Bench CLI — start the operator TUI."""

import argparse
import sys

# Characters that change a URL's path/query/fragment if interpolated as
# a path segment — reject at the CLI boundary so we don't reshape the
# request to core. The right longer-term fix lives in CoreAPIClient
# (URL-encode path params), tracked separately; this is the bench-side
# defensive guard until that lands.
_FORBIDDEN_CASE_ID_CHARS = ("/", "?", "#")


def _validate_case_id(raw: str | None) -> str | None:
    """Normalise and validate ``--case-id`` from argparse.

    Returns ``None`` for unset / empty / whitespace-only inputs (the
    bench then stays on its default screen). Raises ``SystemExit`` for
    inputs that would reshape the request URL — operators see the error
    and a hint at what's wrong, not a confusing 404.
    """
    if raw is None:
        return None
    case_id = raw.strip()
    if not case_id:
        return None
    if any(ch in case_id for ch in _FORBIDDEN_CASE_ID_CHARS) or case_id.startswith(".."):
        sys.exit(
            f"--case-id {raw!r} contains a path-altering character "
            f"(one of {_FORBIDDEN_CASE_ID_CHARS} or leading '..'). "
            "Use the case ID exactly as core emitted it."
        )
    return case_id


def main():
    parser = argparse.ArgumentParser(description="NthLayer Bench — operator TUI")
    parser.add_argument("-V", "--version", action="version", version="%(prog)s 1.5.0a1")
    parser.add_argument("--core-url", default="http://localhost:8000",
                        help="Core API URL (default: http://localhost:8000)")
    parser.add_argument("--case-id", default=None,
                        help="Open the case-detail screen for this case on launch.")

    args = parser.parse_args()

    initial_case_id = _validate_case_id(args.case_id)

    from nthlayer_bench.app import BenchApp

    app = BenchApp(core_url=args.core_url, initial_case_id=initial_case_id)
    app.run()


if __name__ == "__main__":
    main()
