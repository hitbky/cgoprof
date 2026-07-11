# CGOProf go-sqlite3-v2 Results

## Run

- Source: `real_projects/eval/go-sqlite3-baseline`
- Instrumented project: `instrumented/go-sqlite3-v2`
- Profile: `instrumented/go-sqlite3-v2/result/cgoprof.jsonl`
- Workload: `go test ./...` from the instrumented project

## Counts

- Static cgo call sites: 239
- Runtime metric sites: 254
- Runtime events: 2204007
- Findings: 44

## Events By Kind

- `callback`: 90
- `cgo_call`: 1282078
- `conversion`: 277547, bytes=4022826
- `memory`: 475973, bytes=6424638
- `pointer_check`: 168279
- `scheduler`: 40

## Findings By Rule

- `blocking-cgo-detector`: 1
- `conversion-copy-detector`: 7
- `inbound-copy-detector`: 3
- `pointer-check-overhead-detector`: 1
- `small-call-detector`: 32

## Findings By Severity

- `medium`: 44

## Top Findings

1. `small-call-detector` (medium) at callback.go:193 C.malloc
   - Evidence: avg_cgo_ns=59.7, boundary_ratio=unknown, call_count=51531, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
2. `small-call-detector` (medium) at callback.go:224 C.free
   - Evidence: avg_cgo_ns=67.9, boundary_ratio=unknown, call_count=51456, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
3. `small-call-detector` (medium) at sqlite3.go:826 C.CString
   - Evidence: avg_cgo_ns=70.1, boundary_ratio=unknown, call_count=51523, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
4. `conversion-copy-detector` (medium) at sqlite3.go:826 C.CString
   - Evidence: call_count=51523, conversion_bytes=762457, conversion_count=51523, free_count=51523, malloc_count=51523, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.
5. `small-call-detector` (medium) at sqlite3.go:832 C.free
   - Evidence: avg_cgo_ns=80.1, boundary_ratio=unknown, call_count=51523, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
6. `small-call-detector` (medium) at sqlite3.go:848 C._sqlite3_create_function
   - Evidence: avg_cgo_ns=183.3, boundary_ratio=unknown, call_count=51525, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
7. `small-call-detector` (medium) at sqlite3.go:1105 C.CString
   - Evidence: avg_cgo_ns=110.8, boundary_ratio=unknown, call_count=1197, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
8. `conversion-copy-detector` (medium) at sqlite3.go:1105 C.CString
   - Evidence: call_count=1197, conversion_bytes=17530, conversion_count=1197, free_count=1197, malloc_count=1197, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.
9. `small-call-detector` (medium) at sqlite3.go:1110 C._sqlite3_exec_no_args
   - Evidence: avg_cgo_ns=17806.1, boundary_ratio=unknown, call_count=1197, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
10. `small-call-detector` (medium) at sqlite3.go:1124 C.free
   - Evidence: avg_cgo_ns=84.3, boundary_ratio=unknown, call_count=1197, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
11. `small-call-detector` (medium) at sqlite3.go:1302 C.sqlite3_threadsafe
   - Evidence: avg_cgo_ns=79.0, boundary_ratio=unknown, call_count=10306, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
12. `small-call-detector` (medium) at sqlite3.go:1699 C.CString
   - Evidence: avg_cgo_ns=120.9, boundary_ratio=unknown, call_count=10305, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
13. `conversion-copy-detector` (medium) at sqlite3.go:1699 C.CString
   - Evidence: call_count=10305, conversion_bytes=781454, conversion_count=10305, free_count=10305, malloc_count=10305, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.
14. `small-call-detector` (medium) at sqlite3.go:1705 C.free
   - Evidence: avg_cgo_ns=89.2, boundary_ratio=unknown, call_count=10305, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
15. `small-call-detector` (medium) at sqlite3.go:1725 C._sqlite3_open_v2
   - Evidence: avg_cgo_ns=27787.1, boundary_ratio=unknown, call_count=10305, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
16. `small-call-detector` (medium) at sqlite3.go:2090 C.sqlite3_close_v2
   - Evidence: avg_cgo_ns=5232.2, boundary_ratio=unknown, call_count=10287, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
17. `small-call-detector` (medium) at sqlite3.go:2206 C.CString
   - Evidence: avg_cgo_ns=105.5, boundary_ratio=unknown, call_count=63070, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
18. `conversion-copy-detector` (medium) at sqlite3.go:2206 C.CString
   - Evidence: call_count=63070, conversion_bytes=2395022, conversion_count=63070, free_count=63070, malloc_count=63070, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.
19. `small-call-detector` (medium) at sqlite3.go:2212 C.free
   - Evidence: avg_cgo_ns=108.2, boundary_ratio=unknown, call_count=63070, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
20. `small-call-detector` (medium) at sqlite3.go:2219 C._sqlite3_prepare_v2_internal
   - Evidence: avg_cgo_ns=1328.2, boundary_ratio=unknown, call_count=63070, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.

## Detailed Files

- `cgoprof.jsonl`
- `scan.json`
- `findings.json`
- `interaction_graph.json`
- `report.txt`
- `event_summary.json`
- `top_findings.csv`
- `top_sites_by_total_cgo.csv`
- `top_sites_by_conversion.csv`
- `top_sites_by_allocation.csv`
- `top_sites_by_pointer_check.csv`
