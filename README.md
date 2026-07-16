# CGOProf Prototype

CGOProf is a first-pass prototype for a cgo-aware dynamic cross-layer profiler.
It is intentionally independent from DrPy. The current implementation focuses on
the end-to-end research workflow:

1. scan Go source for cgo call sites,
2. create an instrumented source copy with a Go AST rewriter,
3. collect runtime JSONL events from lightweight Go wrappers,
4. build a CGO Interaction Graph,
5. run diagnosis rules for common Go/C boundary inefficiencies.

## Detected Inefficiency Classes

- Boundary amplification: many tiny cgo calls where boundary cost dominates.
- Conversion/copy overhead: repeated `C.CString`, `C.GoString`, `malloc`,
  `free`, or `memcpy` on a hot cgo path.
- Pointer-check overhead: visible `cgoCheckPointer`-style cost on hot paths.
- Callback ping-pong: C repeatedly calls back into Go with tiny work per call.
- Inbound native copy overhead: repeated `C.GoString`, `C.GoStringN`, or
  `C.GoBytes` copies from C memory into Go memory.

## Repository Layout

- `cgoprof/`: Python analyzer and CLI.
- `cgoprof/contracts/`: versioned Contract IR, content-addressed API identity,
  exact build Manifest, evidence model, conservative fact lattice, and codecs.
- `docs/contract_model.md`: normative definition of the seven cgo contract
  attributes and their relationship to later cost analysis.
- `docs/api_identity_manifest.md`: normative API/provider/signature identity,
  binding, build snapshot, resolution, and integrity rules.
- `instrumenter/cgoprof-instrument/`: Go AST source-to-source instrumenter.
- `runtime_go/cgoprof/`: lightweight Go event recorder.
- `examples/`: four cgo examples, one per rule.
- `examples/profiles/synthetic_all_rules.json`: profile fixture for local tests.
- `tests/`: Python unit tests.

## Full Verification

From this directory:

```bash
./run_all.sh
```

This runs:

1. Python unit tests,
2. the synthetic all-rule profile,
3. all four real Go/cgo examples for the original rules,
4. analyzer checks that each example fires its expected rule,
5. baseline/optimized benchmark pairs that calculate speedups.

If the Go build cache is not writable in the default location, the script uses
`/private/tmp/go-build-cache` by default.

## Python Usage

From this directory:

```bash
python3 -m cgoprof scan examples/small_calls
python3 -m cgoprof manifest examples/conversion_copy --out api-manifest.json
python3 -m cgoprof manifest-verify api-manifest.json
python3 -m cgoprof instrument path/to/go-project
python3 -m cgoprof analyze examples/profiles/synthetic_all_rules.json
```

After `pip install -e .`, the same commands can be written even shorter:

```bash
cgoprof scan examples/small_calls
cgoprof instrument path/to/go-project
cgoprof analyze examples/profiles/synthetic_all_rules.json
```

Short aliases are also available: `s` for `scan`, `m` for `manifest`, `i` for
`instrument`, and `a` for `analyze`.

To write the interaction graph:

```bash
python3 -m cgoprof analyze examples/profiles/synthetic_all_rules.json \
  --graph-out /tmp/cgoprof-graph.json
```

## Go Example Usage

On a machine with Go and a C compiler:

```bash
cd examples/small_calls
CGOPROF_OUT=small_calls.jsonl go run .
cd ../..
python3 -m cgoprof analyze examples/small_calls/small_calls.jsonl --root examples/small_calls
```

Repeat with:

- `examples/conversion_copy`
- `examples/pointer_check`
- `examples/callback_pingpong`

## Automatic Source Instrumentation

The `instrument` command copies a Go project to a new output directory and
rewrites the copy using `go/ast`. It does not modify the original project.

```bash
python3 -m cgoprof instrument ./real_projects/results/go-sqlite3
```

The rewriter currently recognizes:

- `C.xxx(...)` call expressions, including nested calls inside larger Go
  expressions. Calls are hoisted to temporary variables and surrounded with
  `prof.BeginCall(...)` so the result type does not need to be known in advance.
- `C.CString(...)`, which emits conversion and malloc events before the real
  cgo call.
- `C.CBytes(...)`, `C.GoString(...)`, `C.GoStringN(...)`, and `C.GoBytes(...)`,
  which emit conversion events for Go/C data movement. Conversion events are
  tagged as Go-to-C or C-to-Go so inbound copy overhead can be separated from
  outbound temporary C string/buffer creation.
- `C.malloc(...)`, `C.calloc(...)`, and `C.realloc(...)`, which emit native
  allocation events for graph metrics and memory-context evidence.
- `C.free(...)`, which emits a free event and records the boundary crossing.
- direct and simple intraprocedural indirect `unsafe.Pointer(...)` values
  flowing into a C call, such as `ptr := unsafe.Pointer(&buf[0]); C.foo(ptr)`,
  which emit a pointer-check event.
- lightweight interprocedural pointer summaries for same-package helper
  functions. For example, `C.foo(bufferPtr(buf))` is recognized when
  `bufferPtr` returns `unsafe.Pointer(&buf[0])`, while helpers that return
  `C.CString`, `C.CBytes`, or `C.malloc` are treated as C-owned pointers to
  avoid unnecessary pointer-check events.
- `defer C.free(...)` and other deferred C calls, which are wrapped so the event
  is recorded when the deferred call actually executes.
- `//export` Go callbacks, which are wrapped with a deferred callback event.

If the input has a `go.mod`, the output module is updated with a local
`replace cgoprof/runtime_go/cgoprof => ...` directive pointing to the bundled
runtime recorder.

The current instrumenter is intentionally conservative for transformations that
would require non-trivial control-flow lowering. In particular, it does not
rewrite cgo calls in `for` loop conditions because hoisting such calls outside
the loop would change per-iteration evaluation semantics.

## Design Notes

The Go runtime package records explicit events instead of claiming transparent
runtime interception. This keeps the MVP small and testable. The backend can be
replaced later by source rewriting, eBPF uprobes, LD_PRELOAD hooks, or Go runtime
trace integration without changing the graph and rule layer.

## Contract IR and API Manifest

The repository contains the Phase 0–2 foundation for contract-aware cgo
analysis. The Contract IR represents memory access, ownership, lifetime,
escape, callback behavior, mutability, and physical representation at API,
parameter, and result granularity. It also preserves build scope, evidence,
fact status, argument-dependent clauses, and conservative merge conflicts.

Phase 2 adds three separate identity levels:

- provider/signature-based, content-addressed `APIIdentity`;
- package-local `C.name` to API bindings;
- an immutable `APIManifest` for one exact target, toolchain, flag, macro,
  package, and provider configuration.

Manifest readers verify content IDs and referential integrity. Symbol-only
lookups never become exact resolution; missing provider or ABI-canonical
signature information remains explicitly unresolved. Contract schema v2 links
catalogs to exact `manifest_id` and `build_id` values before facts may be used
as proof; provider releases and canonical parameter types must also match.

The Contract IR is deliberately independent from the current profiler event
and rule models. Project discovery already records authoritative
`go env`/`go list` build/package data, source digests, call sites, directives, and exact cgo
intrinsics. Later declaration and semantic frontends will resolve external C
APIs and populate contracts from C/Go static analysis, curated annotations, and
positive dynamic evidence. The current implementation does not claim complete
contract inference for arbitrary C libraries.

See [`docs/contract_model.md`](docs/contract_model.md) for the normative
contract semantics and [`docs/api_identity_manifest.md`](docs/api_identity_manifest.md)
for identity, Manifest, resolution, and integrity rules.

## Optimization Benchmarks

The profiler examples under `examples/` detect low-efficiency behavior. The
paired programs under `benchmarks/` measure the corresponding rewrite benefit
without profiler logging overhead:

```bash
python3 benchmarks/run_benchmarks.py --runs 7 --warmups 2
```

The script writes:

- `benchmarks/results/speedups.json`
- `benchmarks/results/speedups.md`

The latest local benchmark table is written to
`benchmarks/results/speedups.md` each time the script runs.

## Current Scope

This is a first tool framework, not a production transparent profiler. The core
research path is already represented:

- event schema and recorder,
- source scanner and Go AST instrumenter for cgo call sites,
- CGO Interaction Graph,
- five cross-layer inefficiency rules,
- reproducible examples and synthetic fixtures proving the rules fire on cgo
  programs.

The current implementation does not yet perform full SSA, points-to, escape, or
C-body side-effect analysis. Rules such as automatically proving safe
`#cgo noescape`/`#cgo nocallback` opportunities or detecting repeated semantic
serialization across Go and C would require those analyses or API-specific
models, so they are treated as follow-up research extensions rather than
implemented rules in this prototype.
