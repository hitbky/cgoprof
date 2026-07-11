from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from .graph import InteractionGraph, iter_events
from .report import render_text_report
from .rules import run_rules
from .scanner import scan_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgoprof")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", aliases=["s"], help="Scan a Go project for cgo call sites")
    scan_parser.add_argument("root", nargs="?", default=".", help="Go project root; defaults to current directory")
    scan_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    instrument_parser = subparsers.add_parser(
        "instrument",
        aliases=["inst", "i"],
        help="Create an automatically instrumented copy of a Go/cgo project",
    )
    instrument_parser.add_argument("root", nargs="?", default=".", help="Input Go project root; defaults to current directory")
    instrument_parser.add_argument(
        "-o",
        "--out",
        help="Output directory; defaults to a sibling directory named <project>.cgoprof",
    )
    instrument_parser.add_argument("--force", action="store_true", help="Overwrite the output directory if it already exists")
    instrument_parser.add_argument(
        "--runtime",
        help="Path to runtime_go/cgoprof; defaults to the runtime bundled with this checkout",
    )

    analyze_parser = subparsers.add_parser("analyze", aliases=["a"], help="Analyze a CGOProf JSON/JSONL profile")
    analyze_parser.add_argument("profile", nargs="?", help="Profile JSON or JSONL file; defaults to cgoprof.jsonl")
    analyze_parser.add_argument("--root", default=".", help="Go project root used to annotate call sites; defaults to current directory")
    analyze_parser.add_argument("--profile", dest="profile_flag", help="Deprecated spelling; use positional profile instead")
    analyze_parser.add_argument("--graph-out", "--graph", dest="graph_out", help="Write CGO Interaction Graph as JSON")
    analyze_parser.add_argument("--json", action="store_true", help="Emit findings as JSON")

    args = parser.parse_args(argv)
    if args.command == "scan":
        return _cmd_scan(Path(args.root), args.json)
    if args.command == "instrument":
        return _cmd_instrument(args)
    if args.command == "analyze":
        return _cmd_analyze(args)
    raise AssertionError(args.command)


def _cmd_scan(root: Path, emit_json: bool) -> int:
    callsites, directives = scan_project(root)
    if emit_json:
        print(
            json.dumps(
                {
                    "callsites": [callsite.__dict__ for callsite in callsites],
                    "directives": directives,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for callsite in callsites:
        print(
            f"{callsite.site_id} {callsite.file}:{callsite.line} "
            f"{callsite.function} -> C.{callsite.c_symbol}"
        )
    for directive, symbols in directives.items():
        if symbols:
            print(f"#cgo {directive}: {', '.join(sorted(symbols))}")
    return 0


def _cmd_instrument(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parents[1]
    tool_dir = project_root / "instrumenter" / "cgoprof-instrument"
    input_root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve() if args.out else _default_instrumented_out(input_root)
    runtime_dir = Path(args.runtime).resolve() if args.runtime else project_root / "runtime_go" / "cgoprof"
    cmd = [
        "go",
        "run",
        ".",
        "-in",
        str(input_root),
        "-out",
        str(out_dir),
        "-runtime",
        str(runtime_dir.resolve()),
    ]
    if args.force:
        cmd.append("-force")
    env = os.environ.copy()
    env.setdefault("GOCACHE", "/private/tmp/go-build-cache")
    subprocess.run(cmd, cwd=tool_dir, env=env, check=True)
    print(f"instrumented copy written to {out_dir}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    graph = InteractionGraph()
    if args.root:
        callsites, _ = scan_project(args.root)
        for callsite in callsites:
            graph.add_callsite(callsite)
    profile = args.profile_flag or args.profile or "cgoprof.jsonl"
    graph.add_events(iter_events(profile))
    findings = run_rules(graph)
    if args.graph_out:
        Path(args.graph_out).write_text(json.dumps(graph.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps([finding.__dict__ for finding in findings], indent=2, sort_keys=True))
    else:
        print(render_text_report(graph, findings))
    return 0


def _default_instrumented_out(input_root: Path) -> Path:
    if input_root.name:
        return input_root.parent / f"{input_root.name}.cgoprof"
    return input_root / ".cgoprof"


if __name__ == "__main__":
    raise SystemExit(main())
