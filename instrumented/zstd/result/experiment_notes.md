# zstd CGOProf Experiment Notes

## Project

- Project: `github.com/DataDog/zstd`
- Source directory: `real_projects/eval/zstd`
- Instrumented directory: `instrumented/zstd`
- Result directory: `instrumented/zstd/result`
- Original static cgo call sites: 48
- Instrumented-project static cgo call sites used for report annotation: 41

## Workloads

### test

- Profile: `cgoprof_test.jsonl`
- Profile size: 2260609851 bytes
- Runtime events: 14026009
- Approx runtime window from event timestamps: 126.904 seconds
- Runtime metric sites: 46
- Findings: 7
- Events by kind: cgo_call=7016212, conversion=1006, pointer_check=7008729, scheduler=62
- Findings by rule: conversion-copy-detector=1, pointer-check-overhead-detector=2, small-call-detector=4

### bench

- Profile: `cgoprof_bench.jsonl`
- Profile size: 111749 bytes
- Runtime events: 687
- Approx runtime window from event timestamps: 0.023 seconds
- Runtime metric sites: 44
- Findings: 0
- Events by kind: cgo_call=409, conversion=2, pointer_check=276
- Findings by rule: none

### bench_stream_1000x

- Profile: `cgoprof_bench_stream_1000x.jsonl`
- Runtime events: 2022
- Findings: 0
- Reason: this benchmark writes a 1MiB payload per iteration, so the main cgo call is not a tiny boundary-amplification case.

### bench_smallwrite_1000x

- Profile: `cgoprof_bench_smallwrite_1000x.jsonl`
- Runtime events: 6046
- Findings: 2
- Findings by rule: `small-call-detector=1`, `pointer-check-overhead-detector=1`
- Main location: `zstd_stream.go:192 C.ZSTD_compressStream2_wrapper`
- Reason: this benchmark writes 1 byte at a time, creating high-frequency tiny cgo calls.

## Initial Interpretation

- The test workload produced a very large profile, which is useful for stress-testing CGOProf on real projects.
- The first benchmark attempt failed because zstd benchmarks require the `PAYLOAD` environment variable. This has been fixed by generating `payload_1m.txt` and rerunning with `PAYLOAD=...`, `-benchtime=10x`, and `-benchmem`. The successful stdout is saved in `benchmark_stdout.txt`.
- A targeted high-iteration benchmark is needed to make benchmark findings meaningful. `BenchmarkStreamCompression` with 1MiB payload does not trigger `small-call-detector`, while `BenchmarkSmallWriteStreamCompression` with 1000x does trigger both small-call and pointer-check findings.
- zstd is expected to be more pointer/buffer oriented than string-conversion oriented. Pay special attention to `pointer-check-overhead-detector` and `small-call-detector` findings.

## Manual Labeling Table

Fill this table after inspecting `top_findings_test.csv` and `top_findings_bench.csv`.

| Rank | Workload | Rule | Location | Evidence | Label | Reason | Optimization idea |
|---:|---|---|---|---|---|---|---|
| 1 |  |  |  |  | TP/FP/Unclear |  |  |
| 2 |  |  |  |  | TP/FP/Unclear |  |  |
| 3 |  |  |  |  | TP/FP/Unclear |  |  |

## Next Actions

1. Inspect `summary.md` and `report_test.txt` first.
2. Label the top 10 findings in `top_findings_test.csv`.
3. Use `top_findings_bench_smallwrite_1000x.csv` as the benchmark-backed finding table.
4. For each likely TP, inspect the source location in `instrumented/zstd` and then map it back to `real_projects/eval/zstd`.
