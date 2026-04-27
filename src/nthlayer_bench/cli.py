"""NthLayer Bench CLI — start the operator TUI."""

import argparse


def main():
    parser = argparse.ArgumentParser(description="NthLayer Bench — operator TUI")
    parser.add_argument("-V", "--version", action="version", version="%(prog)s 1.5.0a1")
    parser.add_argument("--core-url", default="http://localhost:8000",
                        help="Core API URL (default: http://localhost:8000)")

    args = parser.parse_args()

    from nthlayer_bench.app import BenchApp

    app = BenchApp(core_url=args.core_url)
    app.run()


if __name__ == "__main__":
    main()
