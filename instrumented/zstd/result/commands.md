# Commands Used For zstd CGOProf Experiment

```bash
cd /Users/ban/Documents/Projects/drpy/cgoprof

# Baseline check on original project
cd real_projects/eval/zstd
GOCACHE=/private/tmp/go-build-cache \
GOMODCACHE=/private/tmp/go-mod-cache \
go test ./...

# Static scan of the original project
cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof scan real_projects/eval/zstd --json > instrumented/zstd/result/scan_original.json

# Instrumentation command used to create the instrumented copy
python3 -m cgoprof instrument real_projects/eval/zstd --out instrumented/zstd --force

# Static scan of the instrumented project, used to annotate analysis reports
python3 -m cgoprof scan instrumented/zstd --json > instrumented/zstd/result/scan.json

# Test workload profile, already produced
cd instrumented/zstd
CGOPROF_OUT=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/cgoprof_test.jsonl \
GOCACHE=/private/tmp/go-build-cache \
GOMODCACHE=/private/tmp/go-mod-cache \
go test ./...

# Benchmark workload profile. PAYLOAD is required by several zstd benchmarks.
CGOPROF_OUT=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/cgoprof_bench.jsonl \
PAYLOAD=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/payload_1m.txt \
GOCACHE=/private/tmp/go-build-cache \
GOMODCACHE=/private/tmp/go-mod-cache \
go test -bench=. -run=^$ -benchtime=10x -benchmem \
  > /Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/benchmark_stdout.txt

# Analyze test profile
cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_test.jsonl \
  --root instrumented/zstd \
  --graph-out instrumented/zstd/result/interaction_graph_test.json \
  --json > instrumented/zstd/result/findings_test.json
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_test.jsonl \
  --root instrumented/zstd > instrumented/zstd/result/report_test.txt

# Analyze benchmark profile
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_bench.jsonl \
  --root instrumented/zstd \
  --graph-out instrumented/zstd/result/interaction_graph_bench.json \
  --json > instrumented/zstd/result/findings_bench.json
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_bench.jsonl \
  --root instrumented/zstd > instrumented/zstd/result/report_bench.txt

# Targeted benchmark: large stream compression, useful negative control.
cd /Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd
PAYLOAD=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/payload_1m.txt \
CGOPROF_OUT=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/cgoprof_bench_stream_1000x.jsonl \
GOCACHE=/private/tmp/go-build-cache \
GOMODCACHE=/private/tmp/go-mod-cache \
go test -bench='^BenchmarkStreamCompression$' -run=^$ -benchtime=1000x -benchmem \
  > /Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/benchmark_stream_1000x_stdout.txt

cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_bench_stream_1000x.jsonl \
  --root instrumented/zstd \
  --graph-out instrumented/zstd/result/interaction_graph_bench_stream_1000x.json \
  --json > instrumented/zstd/result/findings_bench_stream_1000x.json
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_bench_stream_1000x.jsonl \
  --root instrumented/zstd > instrumented/zstd/result/report_bench_stream_1000x.txt

# Targeted benchmark: 1-byte stream writes, produces benchmark-backed findings.
cd /Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd
PAYLOAD=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/payload_1m.txt \
CGOPROF_OUT=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/cgoprof_bench_smallwrite_1000x.jsonl \
GOCACHE=/private/tmp/go-build-cache \
GOMODCACHE=/private/tmp/go-mod-cache \
go test -bench='^BenchmarkSmallWriteStreamCompression$' -run=^$ -benchtime=1000x -benchmem \
  > /Users/ban/Documents/Projects/drpy/cgoprof/instrumented/zstd/result/benchmark_smallwrite_1000x_stdout.txt

cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_bench_smallwrite_1000x.jsonl \
  --root instrumented/zstd \
  --graph-out instrumented/zstd/result/interaction_graph_bench_smallwrite_1000x.json \
  --json > instrumented/zstd/result/findings_bench_smallwrite_1000x.json
python3 -m cgoprof analyze instrumented/zstd/result/cgoprof_bench_smallwrite_1000x.jsonl \
  --root instrumented/zstd > instrumented/zstd/result/report_bench_smallwrite_1000x.txt
```
