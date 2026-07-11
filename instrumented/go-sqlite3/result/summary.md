# CGOProf go-sqlite3 Result Summary

## Workload

- Source project: `real_projects/eval/go-sqlite3-baseline`
- Instrumented project: `instrumented/go-sqlite3`
- Workload command: `go test ./...`
- Workload result: `ok github.com/mattn/go-sqlite3 22.153s`
- Raw profile: `result/cgoprof.jsonl`

## Scale

- Profile size: 316.1 MiB
- Runtime events: 2,092,899
- Static/instrumented callsites: 239
- Graph nodes: 324
- Graph edges: 208
- Findings: 41

## Event Counts

- `cgo_call`: 1,282,544
- `memory`: 364,329
- `conversion`: 277,641
- `pointer_check`: 168,295
- `callback`: 90

## Findings by Rule

- `conversion-copy-detector`: 7
- `pointer-check-overhead-detector`: 2
- `small-call-detector`: 32

## Findings by Severity

- `medium`: 41

## Top Findings

1. **small-call-detector** `medium` at `sqlite3.go:2911 (_sqlite3_step_internal, Go func isInterruptErr)`
   - Evidence: avg_cgo_ns=4472.7, boundary_ratio=unknown, call_count=90517, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
2. **small-call-detector** `medium` at `sqlite3.go:2435 (sqlite3_bind_parameter_count, Go func finalizeCachedStmt)`
   - Evidence: avg_cgo_ns=49.7, boundary_ratio=unknown, call_count=63234, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
3. **small-call-detector** `medium` at `sqlite3.go:2580 (_sqlite3_reset_clear, Go func stmtArgs)`
   - Evidence: avg_cgo_ns=89.5, boundary_ratio=unknown, call_count=63234, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
4. **small-call-detector** `medium` at `sqlite3.go:2206 (CString, Go func finalizeCachedStmt)`
   - Evidence: avg_cgo_ns=116.4, boundary_ratio=unknown, call_count=63069, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
5. **conversion-copy-detector** `medium` at `sqlite3.go:2206 (CString, Go func finalizeCachedStmt)`
   - Evidence: call_count=63069, conversion_bytes=2394989, conversion_count=63069, free_count=63069, malloc_count=63069, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.
6. **small-call-detector** `medium` at `sqlite3.go:2212 (free, Go func finalizeCachedStmt)`
   - Evidence: avg_cgo_ns=99.8, boundary_ratio=unknown, call_count=63069, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
7. **small-call-detector** `medium` at `sqlite3.go:2219 (_sqlite3_prepare_v2_internal, Go func finalizeCachedStmt)`
   - Evidence: avg_cgo_ns=1264.6, boundary_ratio=unknown, call_count=63069, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
8. **small-call-detector** `medium` at `sqlite3.go:2423 (sqlite3_finalize, Go func finalizeCachedStmt)`
   - Evidence: avg_cgo_ns=403.4, boundary_ratio=unknown, call_count=63052, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
9. **small-call-detector** `medium` at `sqlite3.go:2933 (_sqlite3_column_values, Go func isInterruptErr)`
   - Evidence: avg_cgo_ns=109.7, boundary_ratio=unknown, call_count=60499, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
10. **small-call-detector** `medium` at `sqlite3.go:2825 (sqlite3_column_name, Go func isInterruptErr)`
   - Evidence: avg_cgo_ns=81.1, boundary_ratio=unknown, call_count=60401, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
11. **small-call-detector** `medium` at `sqlite3.go:2829 (GoString, Go func isInterruptErr)`
   - Evidence: avg_cgo_ns=115.8, boundary_ratio=unknown, call_count=60401, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
12. **conversion-copy-detector** `medium` at `sqlite3.go:2829 (GoString, Go func isInterruptErr)`
   - Evidence: call_count=60401, conversion_bytes=0, conversion_count=60401, free_count=0, malloc_count=0, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.
13. **small-call-detector** `medium` at `sqlite3.go:2842 (sqlite3_column_decltype, Go func isInterruptErr)`
   - Evidence: avg_cgo_ns=82.8, boundary_ratio=unknown, call_count=60401, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
14. **small-call-detector** `medium` at `sqlite3.go:2849 (GoString, Go func isInterruptErr)`
   - Evidence: avg_cgo_ns=179.2, boundary_ratio=unknown, call_count=60401, reason=high-frequency cgo calls with very small measured total latency
   - Recommendation: Batch work across the cgo boundary or move the loop entirely to Go or C.
15. **conversion-copy-detector** `medium` at `sqlite3.go:2849 (GoString, Go func isInterruptErr)`
   - Evidence: call_count=60401, conversion_bytes=0, conversion_count=60401, free_count=0, malloc_count=0, memcpy_bytes=0, memcpy_count=0
   - Recommendation: Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.

## Result Files

- `cgoprof.jsonl`: 316.07 MiB
- `scan.json`: 0.06 MiB
- `findings.json`: 0.02 MiB
- `interaction_graph.json`: 0.20 MiB
- `report.txt`: 0.02 MiB
- `top_findings.csv`: 0.01 MiB
