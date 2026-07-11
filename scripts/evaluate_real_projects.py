#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from cgoprof.scanner import scan_project


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "real_projects" / "projects.json"
PROJECT_ROOT = ROOT / "real_projects"
RESULTS = PROJECT_ROOT / "results"


@dataclass
class ScanResult:
    name: str
    url: str
    present: bool
    priority: int
    callsite_count: int = 0
    directive_count: int = 0
    c_symbols: list[str] | None = None
    error: str = ""


def load_projects() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def clone_project(project: dict) -> None:
    dest = PROJECT_ROOT / project["name"]
    if dest.exists():
        return
    proc = run(["git", "clone", "--depth", "1", project["url"], str(dest)], ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"clone failed for {project['name']}: {proc.stderr.strip()}")


def scan_one(project: dict) -> ScanResult:
    dest = project_dir(project["name"])
    result = ScanResult(
        name=project["name"],
        url=project["url"],
        present=dest.exists(),
        priority=int(project["priority"]),
    )
    if not dest.exists():
        result.error = "not cloned"
        return result
    try:
        callsites, directives = scan_project(dest)
        symbols = sorted({site.c_symbol for site in callsites})
        result.callsite_count = len(callsites)
        result.directive_count = sum(len(items) for items in directives.values())
        result.c_symbols = symbols[:50]
    except Exception as exc:  # pragma: no cover - triage script should preserve failures.
        result.error = str(exc)
    return result


def project_dir(name: str) -> Path:
    direct = PROJECT_ROOT / name
    if direct.exists():
        return direct
    nested_in_results = RESULTS / name
    if nested_in_results.exists():
        return nested_in_results
    return direct


def write_results(results: list[ScanResult]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "real_project_scan.json").write_text(
        json.dumps([asdict(result) for result in results], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    lines = [
        "# Real Project CGOProf Scan Results",
        "",
        "| Project | Present | Priority | cgo call sites | #cgo directives | Notes |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for result in results:
        note = result.error or ", ".join(result.c_symbols or [])
        if len(note) > 120:
            note = note[:117] + "..."
        lines.append(
            f"| {result.name} | {str(result.present).lower()} | {result.priority} | "
            f"{result.callsite_count} | {result.directive_count} | {note} |"
        )
    lines.append("")
    (RESULTS / "real_project_scan.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Clone and scan real cgo projects for CGOProf evaluation.")
    parser.add_argument("--clone", action="store_true", help="Clone missing projects before scanning")
    parser.add_argument("--scan-only", action="store_true", help="Only scan already cloned projects")
    parser.add_argument("--priority", type=int, action="append", help="Only include selected priorities")
    parser.add_argument("--project", action="append", help="Only include selected project names")
    args = parser.parse_args()

    projects = load_projects()
    if args.priority:
        wanted = set(args.priority)
        projects = [project for project in projects if int(project["priority"]) in wanted]
    if args.project:
        wanted_names = set(args.project)
        projects = [project for project in projects if project["name"] in wanted_names]

    if args.clone:
        for project in projects:
            clone_project(project)

    results = [scan_one(project) for project in projects]
    write_results(results)
    print((RESULTS / "real_project_scan.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
