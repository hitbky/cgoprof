#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export PYTHONPATH=.
export GOCACHE="${GOCACHE:-/private/tmp/go-build-cache}"

python3 -m unittest discover -s tests

python3 -m cgoprof contract-infer examples/small_calls \
  --manifest-out /tmp/cgoprof-contract-manifest.json \
  --out /tmp/cgoprof-contracts.json \
  --summary-out /tmp/cgoprof-c-summaries.json \
  --require-complete

python3 -m cgoprof contract-verify \
  /tmp/cgoprof-contracts.json \
  /tmp/cgoprof-contract-manifest.json

python3 - /tmp/cgoprof-c-summaries.json <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
summaries = {
    summary["symbol"]: summary
    for unit in data["translation_units"]
    for summary in unit["summaries"]
}
add_one = summaries.get("add_one")
if add_one is None or not add_one["complete"]:
    raise SystemExit("missing complete C function summary for add_one")
if add_one["callback"] != "no_callback":
    raise SystemExit(f"unexpected add_one callback summary: {add_one['callback']}")
PY

python3 -m cgoprof.cli analyze \
  --profile examples/profiles/synthetic_all_rules.json \
  --json > /tmp/cgoprof-synthetic-findings.json

python3 - /tmp/cgoprof-synthetic-findings.json <<'PY'
import json
import sys

expected = {
    "small-call-detector",
    "conversion-copy-detector",
    "pointer-check-overhead-detector",
    "callback-pingpong-detector",
    "inbound-copy-detector",
}
findings = json.load(open(sys.argv[1], encoding="utf-8"))
rules = {finding["rule"] for finding in findings}
missing = expected - rules
if missing:
    raise SystemExit(f"missing synthetic rules {sorted(missing)}; observed {sorted(rules)}")
PY

run_example() {
  local name="$1"
  local rule="$2"
  local profile="examples/${name}/${name}.jsonl"

  (
    cd "examples/${name}"
    CGOPROF_OUT="${name}.jsonl" go run .
  ) >/tmp/cgoprof-${name}.stdout

  python3 -m cgoprof.cli analyze \
    --root "examples/${name}" \
    --profile "${profile}" \
    --json > "/tmp/cgoprof-${name}-findings.json"

  python3 - "$rule" "/tmp/cgoprof-${name}-findings.json" <<'PY'
import json
import sys

expected = sys.argv[1]
path = sys.argv[2]
findings = json.load(open(path, encoding="utf-8"))
rules = {finding["rule"] for finding in findings}
if expected not in rules:
    raise SystemExit(f"missing {expected}; observed {sorted(rules)}")
PY
}

run_example small_calls small-call-detector
run_example conversion_copy conversion-copy-detector
run_example pointer_check pointer-check-overhead-detector
run_example callback_pingpong callback-pingpong-detector

python3 benchmarks/run_benchmarks.py --runs 3 --warmups 1 >/tmp/cgoprof-benchmark-results.md

echo "CGOProf full verification passed."
