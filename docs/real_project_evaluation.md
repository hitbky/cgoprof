# Real Project Evaluation Plan

CGOProf's synthetic examples prove that the four rules can fire. The next paper
step is to test whether the rules expose useful signals in real cgo projects.

## Project Selection Criteria

Use projects that satisfy most of these conditions:

- The project contains `import "C"` in production code.
- The cgo boundary is in a repeated hot path, not only initialization.
- The project can be scanned without extra services.
- At least one benchmark or test can run locally.
- Dependencies are manageable on macOS/Linux.
- The code has clear candidate rewrites: batching, conversion reuse, callback
  aggregation, or reduced pointer passing.

## Candidate Tiers

Tier 1 should be attempted first:

- `mattn/go-sqlite3`: database driver; likely buildable because it includes a
  SQLite C path.
- `confluentinc/confluent-kafka-go`: real industrial cgo client; useful for
  conversion/callback patterns, but build setup may be harder.

Tier 2:

- `google/gopacket`: pcap subpackage uses native libpcap.
- `chai2010/webp`: image codec binding; good for buffer conversion patterns.

Tier 3 stress cases:

- `veandco/go-sdl2`: large binding with many cgo calls and external deps.
- `hybridgroup/gocv`: OpenCV binding; likely useful but dependency-heavy.

The machine-readable candidate list is in `real_projects/projects.json`.

## Evaluation Workflow

1. Clone candidates into `real_projects/`.
2. Run static scanning to count cgo call sites and `#cgo` directives.
3. Select packages with repeated cgo calls and feasible local tests.
4. Add manual CGOProf instrumentation to one or two hot paths.
5. Run the project's tests/benchmarks to collect JSONL profiles.
6. Run `cgoprof analyze` and record findings.
7. Build an optimized variant or local patch.
8. Benchmark baseline vs optimized and report speedup.

## Commands

Clone and scan all priority-1 projects:

```bash
PYTHONPATH=. python3 scripts/evaluate_real_projects.py --clone --priority 1
```

Scan already cloned projects:

```bash
PYTHONPATH=. python3 scripts/evaluate_real_projects.py --scan-only
```

Outputs are written under `real_projects/results/`.

## What Counts as Evidence

For a paper-quality case study, record:

- project commit hash,
- scanned cgo call-site count,
- selected hot-path source location,
- CGOProf finding,
- optimization patch,
- baseline median time,
- optimized median time,
- speedup,
- any semantic validation test.

Static scan results alone are useful for project triage, but they are not enough
to claim CGOProf found a real performance bug.
