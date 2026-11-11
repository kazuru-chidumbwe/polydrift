"""CLI entrypoint."""

from __future__ import annotations

import argparse
import sys

from polydrift import __version__
from polydrift.drivers.registry import get_driver, list_driver_names
from polydrift.fixtures import list_fixtures
from polydrift.run import (
    compare_exit_code,
    format_report,
    format_suite_report,
    run_compare,
    run_suite,
    suite_exit_code,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="polydrift",
        description="Differential testing of independent protocol implementations.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"polydrift {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show harness version and status.")
    subparsers.add_parser("fixtures", help="List pinned fixture corpus.")

    drivers_parser = subparsers.add_parser("drivers", help="List registered stack drivers.")
    drivers_parser.set_defaults(command="drivers")

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare a fixture across two stack drivers.",
    )
    compare_parser.add_argument("fixture_id", help="Stable fixture ID (e.g. F-200-MIN).")
    compare_parser.add_argument("--left", default="builtin", help="Left driver name.")
    compare_parser.add_argument("--right", default="pjsip-stub", help="Right driver name.")
    compare_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Report format (default: text).",
    )

    suite_parser = subparsers.add_parser(
        "suite",
        help="Compare all fixtures across two stack drivers.",
    )
    suite_parser.add_argument("--left", default="builtin", help="Left driver name.")
    suite_parser.add_argument("--right", default="pjsip-stub", help="Right driver name.")
    suite_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Report format (default: text).",
    )

    cell_run = subparsers.add_parser(
        "cell-run",
        help="Run every fixture of a cell against one implementation, N clean restarts.",
    )
    cell_run.add_argument("--cell", choices=("sip",), default="sip")
    cell_run.add_argument("--impl", required=True, help="Driver name (e.g. pjsip-lab).")
    cell_run.add_argument("--impl-version", default="", help="Implementation version under test.")
    cell_run.add_argument("--repeats", type=int, default=3, help="Clean-restart repeats (default 3).")
    cell_run.add_argument("--restart-cmd", default="", help="Shell command that restarts the implementation.")
    cell_run.add_argument("--fixture", action="append", help="Limit to these fixture IDs (repeatable).")
    cell_run.add_argument("--out", required=True, help="Output JSONL of observation records.")

    cell_an = subparsers.add_parser(
        "cell-analyse",
        help="Compare records of all implementations in a cell under the shared oracle.",
    )
    cell_an.add_argument("--cell", choices=("sip",), default="sip")
    cell_an.add_argument("records", nargs="+", help="JSONL record files.")
    cell_an.add_argument("--deposits", default="deposits.json", help="Deposit register (default deposits.json).")
    cell_an.add_argument(
        "--provisional",
        action="store_true",
        help="Run without a deposited oracle spec; output is marked provisional and is not a study result.",
    )
    cell_an.add_argument("--min-repeats", type=int, default=3)

    cell_rep = subparsers.add_parser(
        "cell-report",
        help="Full cell report: peers, fault classes, consequences, baselines, drift (any cell).",
    )
    cell_rep.add_argument("--cell", required=True, choices=("sip", "dns", "mqtt", "ldap", "jmap", "sieve"))
    cell_rep.add_argument("records", nargs="+", help="JSONL record files.")
    cell_rep.add_argument("--current", default="", help="Comma list of impl keys that are the current peers "
                          "(default: highest version of each family).")
    cell_rep.add_argument("--min-repeats", type=int, default=3)
    cell_rep.add_argument("--out", required=True, help="Output JSON.")
    cell_an.add_argument("--out", required=True, help="Output JSON.")

    args = parser.parse_args(argv)

    if args.command == "cell-run":
        from polydrift.cells.sip.runner import run_impl, shell_restart
        from polydrift.core.record import write_jsonl

        try:
            driver = get_driver(args.impl)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        restart = shell_restart(args.restart_cmd) if args.restart_cmd else None
        records = run_impl(
            driver,
            repeats=args.repeats,
            fixture_ids=args.fixture,
            impl_version=args.impl_version,
            restart=restart,
            log=lambda m: print(m, file=sys.stderr),
        )
        write_jsonl(args.out, records)
        print(f"{len(records)} records -> {args.out}")
        return 0

    if args.command == "cell-report":
        import json

        from polydrift.core.record import read_jsonl
        from polydrift.report import report

        records = [r for path in args.records for r in read_jsonl(path)]
        cur = set(args.current.split(",")) if args.current else None
        result = report(records, args.cell, current=cur, min_repeats=args.min_repeats)
        nl = chr(10)
        with open(args.out, "w", encoding="utf-8", newline=nl) as handle:
            json.dump(result, handle, indent=2, sort_keys=True, ensure_ascii=False, default=str)
            handle.write(nl)
        print(json.dumps({k: result[k] for k in ("summary", "fault_classes", "consequences")}, sort_keys=True))
        return 0

    if args.command == "cell-analyse":
        import json

        from polydrift.analyse import analyse_cell
        from polydrift.cells.sip.adapter import CELL_DIR, load_spec
        from polydrift.core.freeze import SpecNotDeposited
        from polydrift.core.record import read_jsonl

        records = [r for path in args.records for r in read_jsonl(path)]
        try:
            result = analyse_cell(
                records,
                load_spec(),
                CELL_DIR,
                deposits=args.deposits,
                provisional=args.provisional,
                min_repeats=args.min_repeats,
            )
        except (SpecNotDeposited, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        print(json.dumps({"summary": result["summary"], "provisional": result["provisional"]}, sort_keys=True))
        return 0

    if args.command is None:
        print(f"polydrift {__version__}: compare harness ready.")
        return 0

    if args.command == "status":
        print(f"polydrift {__version__}: compare harness ready.")
        return 0

    if args.command == "fixtures":
        rows = list_fixtures()
        for fixture_id, path in rows:
            mark = "ok" if path.is_file() else "MISSING"
            print(f"{fixture_id}\t{mark}\t{path.name}")
        return 0 if all(p.is_file() for _, p in rows) else 1

    if args.command == "drivers":
        for name in list_driver_names():
            print(name)
        return 0

    if args.command in ("compare", "suite"):
        try:
            left = get_driver(args.left)
            right = get_driver(args.right)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        if args.command == "compare":
            try:
                case = run_compare(args.fixture_id, left, right)
            except (KeyError, FileNotFoundError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            sys.stdout.write(format_report(case, fmt=args.format))
            return compare_exit_code(case.status)

        cases = run_suite(left, right)
        sys.stdout.write(format_suite_report(cases, fmt=args.format))
        return suite_exit_code(cases)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
