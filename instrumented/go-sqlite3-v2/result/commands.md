# Commands Used

```bash
# Instrument go-sqlite3 with the current CGOProf tool
python3 -m cgoprof instrument real_projects/eval/go-sqlite3-baseline --out instrumented/go-sqlite3-v2 --force

# Collect runtime profile from the instrumented project
cd instrumented/go-sqlite3-v2
CGOPROF_OUT=/Users/ban/Documents/Projects/drpy/cgoprof/instrumented/go-sqlite3-v2/result/cgoprof.jsonl \
  GOCACHE=/private/tmp/go-build-cache \
  GOMODCACHE=/private/tmp/go-mod-cache \
  go test ./...
# ok github.com/mattn/go-sqlite3 22.696s

# Generate detailed analysis outputs
cd /Users/ban/Documents/Projects/drpy/cgoprof
python3 -m cgoprof scan instrumented/go-sqlite3-v2 --json > instrumented/go-sqlite3-v2/result/scan.json
python3 -m cgoprof analyze instrumented/go-sqlite3-v2/result/cgoprof.jsonl \
  --root instrumented/go-sqlite3-v2 \
  --graph-out instrumented/go-sqlite3-v2/result/interaction_graph.json \
  --json > instrumented/go-sqlite3-v2/result/findings.json
python3 -m cgoprof analyze instrumented/go-sqlite3-v2/result/cgoprof.jsonl \
  --root instrumented/go-sqlite3-v2 > instrumented/go-sqlite3-v2/result/report.txt
```
