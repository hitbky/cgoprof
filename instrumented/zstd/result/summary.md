# zstd CGOProf Experiment Results

## Project

- Project: `github.com/DataDog/zstd`
- Source: `real_projects/eval/zstd`
- Instrumented project: `instrumented/zstd`
- Original static cgo call sites: 48
- Instrumented-project static cgo call sites used for report annotation: 41
- Current CGOProf rules: 5 (`small-call`, `conversion-copy`, `pointer-check`, `callback-pingpong`, `inbound-copy`)

## Test Workload

- Profile: `cgoprof_test.jsonl`
- Profile size: 2260609851 bytes
- Approx runtime window from events: 126.904 seconds
- Runtime events: 14026009
- Runtime metric sites: 46
- Findings: 7

### Events By Kind

- `cgo_call`: 7016212
- `conversion`: 1006
- `pointer_check`: 7008729
- `scheduler`: 62

### Findings By Rule

- `conversion-copy-detector`: 1
- `pointer-check-overhead-detector`: 2
- `small-call-detector`: 4

### Top Findings

1. `small-call-detector` (high) at zstd_stream.go:192 C.ZSTD_compressStream2_wrapper
   - Location source: scan
   - Evidence: avg_cgo_ns=173.1, boundary_ratio=unknown, call_count=7005114, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
2. `small-call-detector` (medium) at errors.go:16 C.ZSTD_getErrorName
   - Location source: scan
   - Evidence: avg_cgo_ns=57.3, boundary_ratio=unknown, call_count=1006, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
3. `pointer-check-overhead-detector` (medium) at zstd.go:77 C.ZSTD_getFrameContentSize
   - Location source: scan
   - Evidence: estimated_ratio=0.34, pointer_check_count=388, pointer_check_ms=0.019
   - Recommendation: Audit whether the C function stores Go pointers or calls back into Go. If the cgo safety contract holds, consider #cgo noescape or reducing pointer passing.
4. `pointer-check-overhead-detector` (medium) at zstd_stream.go:192 C.ZSTD_compressStream2_wrapper
   - Location source: scan
   - Evidence: estimated_ratio=0.224, pointer_check_count=7005114, pointer_check_ms=350.256
   - Recommendation: Audit whether the C function stores Go pointers or calls back into Go. If the cgo safety contract holds, consider #cgo noescape or reducing pointer passing.
5. `small-call-detector` (medium) at zstd_stream.go:594 C.ZSTD_decompressStream_wrapper
   - Location source: scan
   - Evidence: avg_cgo_ns=21446.3, boundary_ratio=unknown, call_count=1610, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
6. `small-call-detector` (medium) at errors.go:19
   - Location source: instrumented-source fallback
   - Evidence: avg_cgo_ns=49.1, boundary_ratio=unknown, call_count=1006, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
7. `conversion-copy-detector` (medium) at errors.go:19
   - Location source: instrumented-source fallback
   - Evidence: call_count=1006, conversion_bytes=0, conversion_count=1006, free_count=0, malloc_count=0, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.

## Bench Workload

- Profile: `cgoprof_bench.jsonl`
- Profile size: 111749 bytes
- Approx runtime window from events: 0.023 seconds
- Runtime events: 687
- Runtime metric sites: 44
- Findings: 0

### Events By Kind

- `cgo_call`: 409
- `conversion`: 2
- `pointer_check`: 276

### Findings By Rule

- No findings

### Top Findings

No rule fired for this workload.

## Benchmark Command Output

- `benchmark_stdout.txt`: stdout from the successful benchmark run with `PAYLOAD=result/payload_1m.txt` and `-benchtime=10x -benchmem`.

## Targeted Benchmarks

### Stream Compression 1000x

- Command output: `benchmark_stream_1000x_stdout.txt`
- Profile: `cgoprof_bench_stream_1000x.jsonl`
- Events: 2,022
- Findings: 0
- Interpretation: each iteration writes a 1MiB payload, so the main cgo call is not small. The dominant site had 1,001 calls but average cgo time was about 124,624 ns, above the `small-call-detector` threshold.

### Small Write Stream Compression 1000x

- Command output: `benchmark_smallwrite_1000x_stdout.txt`
- Profile: `cgoprof_bench_smallwrite_1000x.jsonl`
- Events: 6,046
- Findings: 2
- Findings by rule:
  - `small-call-detector`: 1
  - `pointer-check-overhead-detector`: 1

Top finding:

1. `small-call-detector` at `zstd_stream.go:192 C.ZSTD_compressStream2_wrapper`
   - Evidence: `call_count=3003`, `avg_cgo_ns=141.2`
   - Interpretation: this benchmark writes 1 byte at a time, which creates the same kind of high-frequency tiny cgo calls seen in the test workload.

2. `pointer-check-overhead-detector` at `zstd_stream.go:192 C.ZSTD_compressStream2_wrapper`
   - Evidence: `pointer_check_count=3003`, `pointer_check_ms=0.15`, `estimated_ratio=0.262`
   - Interpretation: the same call site repeatedly passes Go buffer pointers into C.

## Files

- `scan_original.json`: static cgo call-site scan of the original project.
- `scan.json`: static cgo call-site scan of the instrumented project, used for report annotation.
- `payload_1m.txt`: deterministic benchmark payload used for PAYLOAD-dependent benchmarks.
- `benchmark_stdout.txt`: successful benchmark output.
- `benchmark_stream_1000x_stdout.txt`, `benchmark_smallwrite_1000x_stdout.txt`: targeted benchmark outputs.
- `cgoprof_test.jsonl`, `cgoprof_bench.jsonl`: raw runtime profiles.
- `cgoprof_bench_stream_1000x.jsonl`, `cgoprof_bench_smallwrite_1000x.jsonl`: targeted benchmark profiles.
- `findings_test.json`, `findings_bench.json`: structured findings.
- `findings_bench_stream_1000x.json`, `findings_bench_smallwrite_1000x.json`: targeted benchmark findings.
- `interaction_graph_test.json`, `interaction_graph_bench.json`: CGO Interaction Graphs.
- `report_test.txt`, `report_bench.txt`: text reports.
- `event_summary_test.json`, `event_summary_bench.json`: per-workload event summaries.
- `event_summary_bench_stream_1000x.json`, `event_summary_bench_smallwrite_1000x.json`: targeted benchmark summaries.
- `top_findings_test.csv`, `top_findings_bench.csv`: finding tables with columns reserved for manual labels.
- `top_findings_bench_stream_1000x.csv`, `top_findings_bench_smallwrite_1000x.csv`: targeted benchmark finding tables.
- `top_sites_by_*_test.csv`, `top_sites_by_*_bench.csv`: site ranking tables.
