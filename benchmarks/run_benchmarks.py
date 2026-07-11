#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CASES = [
    "small_calls",
    "conversion_copy",
    "pointer_check",
    "callback_pingpong",
]


@dataclass
class Timing:
    case: str
    baseline_median_ms: float
    optimized_median_ms: float
    speedup: float
    baseline_runs_ms: list[float]
    optimized_runs_ms: list[float]
    baseline_output: str
    optimized_output: str


def build_binary(path: Path, out: Path, env: dict[str, str]) -> None:
    if out.exists():
        out.unlink()
    proc = subprocess.run(
        ["go", "build", "-o", str(out), "."],
        cwd=path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"go build failed in {path}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def run_once(binary: Path, env: dict[str, str]) -> tuple[float, str]:
    start = time.perf_counter_ns()
    proc = subprocess.run(
        [str(binary)],
        cwd=binary.parent,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    if proc.returncode != 0:
        raise RuntimeError(
            f"benchmark failed for {binary}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return elapsed_ms, proc.stdout.strip()


def measure(case: str, runs: int, warmups: int) -> Timing:
    env = dict(os.environ)
    env.setdefault("GOCACHE", "/private/tmp/go-build-cache")

    baseline_path = ROOT / "baseline" / case
    optimized_path = ROOT / "optimized" / case
    bin_dir = ROOT / "results" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    baseline_bin = bin_dir / f"{case}_baseline"
    optimized_bin = bin_dir / f"{case}_optimized"

    build_binary(baseline_path, baseline_bin, env)
    build_binary(optimized_path, optimized_bin, env)

    for _ in range(warmups):
        run_once(baseline_bin, env)
        run_once(optimized_bin, env)

    baseline_runs: list[float] = []
    optimized_runs: list[float] = []
    baseline_output = ""
    optimized_output = ""
    for _ in range(runs):
        elapsed, baseline_output = run_once(baseline_bin, env)
        baseline_runs.append(elapsed)
        elapsed, optimized_output = run_once(optimized_bin, env)
        optimized_runs.append(elapsed)

    if baseline_output != optimized_output:
        raise RuntimeError(
            f"{case}: baseline and optimized outputs differ: "
            f"{baseline_output!r} != {optimized_output!r}"
        )

    baseline_median = statistics.median(baseline_runs)
    optimized_median = statistics.median(optimized_runs)
    speedup = baseline_median / optimized_median if optimized_median else 0.0
    return Timing(
        case=case,
        baseline_median_ms=baseline_median,
        optimized_median_ms=optimized_median,
        speedup=speedup,
        baseline_runs_ms=baseline_runs,
        optimized_runs_ms=optimized_runs,
        baseline_output=baseline_output,
        optimized_output=optimized_output,
    )


def write_markdown(results: list[Timing], path: Path) -> None:
    lines = [
        "# CGOProf Optimization Benchmark Results",
        "",
        "| Case | Baseline median (ms) | Optimized median (ms) | Speedup |",
        "|---|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            f"| {result.case} | {result.baseline_median_ms:.3f} | "
            f"{result.optimized_median_ms:.3f} | {result.speedup:.2f}x |"
        )
    lines.append("")
    lines.append("Measured from prebuilt binaries; medians exclude Go build time.")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CGOProf baseline/optimized benchmarks.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args()

    selected = args.case or CASES
    results = [measure(case, args.runs, args.warmups) for case in selected]

    out_dir = ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "speedups.json"
    md_path = out_dir / "speedups.md"
    json_path.write_text(
        json.dumps([asdict(result) for result in results], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_markdown(results, md_path)

    print(md_path.read_text(encoding="utf-8"))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
