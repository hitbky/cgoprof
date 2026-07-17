# CGOProf Contract Inference: Intrinsics, Annotations, C Signatures, and Function Summaries

## 1. Scope and Safety Contract

Phases 3 and 4 turn the Contract IR and API Manifest into an executable
analysis pipeline with four independent fact sources:

```text
cgo intrinsic semantics -----------+
                                     |
versioned user annotations ---------+--> conservative Contract merge
                                     |              |
Clang C signature analysis ---------+              v
                                     |       linked ContractCatalog
Clang C body summaries -------------+       (manifest_id + api_id)
```

The implementation has three non-negotiable safety properties:

1. A fact is attached only after exact Manifest, build, provider release, API,
   and Go-package binding checks.
2. A stronger-looking source never overwrites contradictory evidence. Every
   source enters the attribute lattice; contradictions produce a conservative
   value and `conflict` status.
3. Missing C bodies, unknown callees, unsupported constructs, and incomplete
   call graphs do not imply safety. They emit explicit conservative effects and
   completeness diagnostics.

The result constrains cost decomposition, ownership-aware graph construction,
and rewrite proof obligations. It is not a performance estimate by itself.

## 2. Phase 3: Intrinsic Contracts

### 2.1 One Registry for Identity and Semantics

`cgoprof/contracts/intrinsics.py` is the single registry for the five cgo
pseudo-functions:

| Intrinsic | Input representation | Result representation | Allocation/ownership |
|---|---|---|---|
| `C.CString` | immutable Go UTF-8 string | NUL-terminated C string | new caller-owned C allocation, explicit `C.free` |
| `C.CBytes` | Go byte slice | raw C byte allocation | new caller-owned C allocation, explicit `C.free` |
| `C.GoString` | NUL-terminated C string | immutable Go string | independent Go-owned copy |
| `C.GoStringN` | C pointer plus explicit length | immutable Go string | independent Go-owned copy |
| `C.GoBytes` | C pointer plus explicit length | mutable Go byte slice | independent Go-owned copy |

Manifest discovery and Contract generation call the same registry. A signature
used to calculate `api_id` therefore cannot drift away from the signature whose
semantics are modeled.

Each intrinsic emits all six value-level dimensions and the function-level
callback dimension. Every fact has:

- `EvidenceKind.CGO_INTRINSIC`;
- `FactStatus.INTRINSIC`;
- the exact Go toolchain release and `cgo-pseudo-v1` ABI in its evidence;
- an exact `BuildScope`, provider release, and package binding.

The registry does not treat libc `malloc`, `free`, or `memcpy` as cgo
intrinsics. Those are C APIs handled by C summaries or annotations.

## 3. Phase 3: Versioned Annotation Contracts

### 3.1 Exact Targeting

An `AnnotationScope` requires:

```text
manifest_id
build_id
provider_release_id
api_id
go_package
```

Application fails if any field disagrees with the loaded Manifest or if the Go
package has no exact binding to the API. There is no symbol-only fallback and
no implicit reuse across library versions.

### 3.2 Provenance and Trust

Every annotation records author, optional organization, source URI or explicit
documentation path, revision, trust level, reviewers, and a justification for
every assignment. Trust is `untrusted`, `reviewed`, or `trusted`.

`AnnotationPolicy` can reject material below a required trust level. Trust
controls admission only. Even a trusted annotation becomes
`FactStatus.DECLARED` with `EvidenceKind.USER_ANNOTATION`; it cannot manufacture
`PROVEN` or `INTRINSIC` evidence.

### 3.3 Typed Facts and Conditional Clauses

Assignments use the Contract IR's typed targets and attribute domains. The
loader rejects:

- callback facts on a parameter or result;
- value-level facts on the function;
- unknown values masquerading as declarations;
- missing/extra fields, duplicate JSON keys, invalid enums, and invalid
  parameter indices;
- result facts for a `void` function;
- clauses referencing parameters outside the canonical signature.

Annotations support the same `eq`, `ne`, `bit_set`, `is_null`, and `not_null`
conditional clauses as Contract IR.

### 3.4 Content Addressing and Merge Behavior

Each annotation has a `cgoannotation:v1:<sha256>` ID over its complete semantic
payload. A bundle has a `cgoannotations:v1:<sha256>` ID over its sorted member
IDs and generator identity. Loading verifies both layers, so changing a
justification, target, value, trust record, or scope invalidates the ID.

Repeated assignments and cross-source facts enter `merge_facts()`. For example,
an intrinsic `caller_owned` result plus an annotation claiming `callee_owned`
yields `ownership=unknown`, `status=conflict`, and retains both evidence records.

## 4. Phase 4: Clang C Signature Analysis

### 4.1 Translation-Unit Fidelity

`analyze_package_translation_units()` analyzes:

- build-selected `.c`, `.cc`, `.cpp`, `.cxx`, `.m`, and `.mm` files;
- each C documentation comment attached to `import "C"` as a separate
  translation unit;
- global and package-local `CFLAGS`/`CPPFLAGS`, with Manifest placeholders
  expanded;
- the Manifest target triple and package include directory.

Keeping preambles separate matches cgo compilation semantics. A failed unit is
reported as `translation_unit_failed` or `preamble_failed`; it is never replaced
with an empty safe summary. The preamble extractor preserves C preprocessing
lines and declarations while removing Go-only `#cgo` directive lines before
Clang parsing.

### 4.2 Canonical Function Identity

The frontend consumes Clang JSON AST and extracts:

- result and ordered parameter types;
- parameter names and source spellings;
- typedef chains and normalized builtin spellings;
- variadic status and calling convention;
- storage class and definition availability;
- declaration and definition locations;
- target ABI tag;
- size/alignment from Clang constant-expression layout probes.

Canonical types are prefixed with `c:` before entering `CTypeIdentity`. Source
spellings and measured layout remain audit data; typedef-resolved canonical
spelling drives the signature ID as defined in Phase 2.

Package-local `static` functions receive a translation-unit-qualified linkage
name derived from the source content digest. Two same-named static functions in
different cgo preambles therefore do not collapse into one API. If more than
one ABI/linkage candidate can satisfy a selector, the Manifest remains
unresolved.

### 4.3 Provider Discipline

The analyzer never guesses a library provider from a function name.

- For package-defined bodies, `local_package_provider()` creates an exact
  `go_package_local` release from the package source digest.
- For external libraries, the CLI requires explicit provider kind, namespace,
  name, and version or ABI. Declaration-only binding additionally requires
  `--allow-declaration-provider`.

`augment_manifest_with_c_analysis()` replaces an unresolved selector only when
one exact provider/signature/linkage candidate exists. Other unresolved records
and diagnostics remain isolated.

## 5. Phase 4: C Function Effect Summaries

### 5.1 Direct Effects

The AST analysis builds a conservative local may-alias set (including aliases
created by assignments and casts) and recognizes:

| Construct | Summary effect |
|---|---|
| pointee/array/field rvalue | parameter memory `read` |
| pointee/array/field assignment | `write`, or `read_write` for compound updates |
| store to global/static object | `escapes` |
| return an input pointer | input `escapes`; borrowed result aliases owner |
| return global/static pointer | callee-owned, process-lifetime result |
| `free`/`realloc` input | ownership transferred to callee |
| `malloc`/`calloc`/`realloc`/`strdup` result | caller-owned until explicit free |
| call through function-pointer parameter | synchronous callback |
| retain function-pointer parameter | possible asynchronous callback |
| inline assembly/unsupported atomic construct | incomplete conservative summary |

Local aliases, casts, pointer arithmetic, array/member access, and common
wrapper expressions preserve the originating parameter set.

### 5.2 Known C Library Summaries

Effect seeds cover `memcpy`, `memmove`, `memcmp`, `memset`, `strlen`, `strnlen`,
string copy/compare operations, `malloc`, `calloc`, `realloc`, `free`, `strdup`,
`qsort`, `bsearch`, and `pthread_create`.

These are C analysis seeds, not cgo intrinsic contracts. Their effects are
propagated into an analyzed wrapper; the library API still needs an exact
provider if it is itself bound as `C.name`.

### 5.3 Interprocedural Fixed Point

The frontend includes body-available functions reachable from requested cgo
entry points. It records argument-to-parameter alias maps at direct calls and
iterates summaries to a fixed point, including recursive components.
Propagation covers read/write, escape, ownership consumption, callback mode,
completeness, allocator results, and returned-parameter aliases for directly
returned calls.

The iteration is monotone and guarded by a deterministic safety limit. A limit
failure emits `summary_fixed_point_limit` and invalidates completeness.

### 5.4 Conservative Unknowns

An unresolved external or indirect call produces:

- `read_write` and `may_escape` for every pointer-like actual argument that
  aliases an input parameter;
- function-level `may_callback`, because a callback may use global state
  without an explicit callback argument;
- `analysis_complete=false` and an `unresolved_callee` diagnostic.

A declaration-only entry point receives the same conservative pointer effects.
Thus `no_escape` and `no_callback` arise only from a complete analyzed body and
call graph, an intrinsic, or an admitted declaration/directive.

### 5.5 Contract Generation

`contracts_from_c_analysis()` maps summaries into Contract IR:

- canonical type/representation uses `C_SIGNATURE + DECLARED`;
- body-derived effects use `C_BODY_ANALYSIS + PROVEN` within the recorded
  translation units and build scope;
- `#cgo noescape` and `#cgo nocallback` use `CGO_DIRECTIVE + DECLARED`;
- directive/body contradictions become lattice conflicts;
- raw completeness, digests, call edges, diagnostics, and proof reasons remain
  available in summary JSON.

`infer_contract_catalog()` merges intrinsic, C analysis, directive, and
annotation sources, then validates the catalog against the enriched Manifest.

## 6. Command-Line Workflow

### 6.1 Package-Local Implementations

```bash
python3 -m cgoprof contract-infer path/to/module \
  --package example.com/module/pkg \
  --manifest-out result/api-manifest.json \
  --out result/contracts.json \
  --summary-out result/c-summaries.json \
  --require-complete
```

If the Manifest contains one cgo package, `--package` may be omitted. Without
explicit provider options, only body-defined functions resolve against an
exact package-local source provider.

### 6.2 External Declarations

```bash
python3 -m cgoprof contract-infer path/to/module \
  --manifest input-manifest.json \
  --package example.com/wrapper \
  --provider-kind pkg_config \
  --provider-namespace sqlite.org \
  --provider-name sqlite3 \
  --provider-version 3.46.0 \
  --provider-abi sqlite3 \
  --provider-artifact /usr/lib/libsqlite3.dylib \
  --allow-declaration-provider \
  --manifest-out result/api-manifest.json \
  --out result/contracts.json \
  --summary-out result/c-summaries.json
```

Declaration-only contracts stay conservative until annotations or an available
implementation provide stronger facts.

One inference invocation names one provider. Mixed local and external selectors
can be resolved in successive invocations: first persist the package-local
Manifest, then reload it with `--manifest` and resolve the remaining library
provider. This preserves one explicit provider decision per resolution step.

### 6.3 Annotation and Link Verification

Annotation IDs are derived, not placeholders to edit by hand. A bundle can be
created through the typed API after the signature-enriched Manifest exists:

```python
from cgoprof.contracts import (
    AnnotationAssignment, AnnotationBundle, AnnotationProvenance,
    AnnotationScope, AnnotationTrust, ContractAnnotation,
    ContractAttribute, ContractTarget, ContractTargetKind,
    Escape, dump_annotation_bundle, load_manifest,
)

manifest = load_manifest("result/api-manifest.json")
api = next(item for item in manifest.apis if item.identity.symbol == "consume")
provider = next(
    item for item in manifest.providers
    if item.provider_id == api.identity.provider.provider_id
)
annotation = ContractAnnotation(
    scope=AnnotationScope(
        manifest.manifest_id,
        manifest.build.build_id,
        provider.release_id,
        api.api_id,
        "example.com/wrapper",
    ),
    provenance=AnnotationProvenance(
        author="Wrapper maintainers",
        source="docs/native-api.md",
        revision="git:4f17a2c",
        trust=AnnotationTrust.REVIEWED,
        reviewed_by=("Reviewer A",),
    ),
    assignments=(
        AnnotationAssignment(
            ContractTarget(ContractTargetKind.PARAMETER, 0),
            ContractAttribute.ESCAPE,
            Escape.NO_ESCAPE,
            "the implementation consumes the buffer synchronously",
        ),
    ),
)
dump_annotation_bundle(AnnotationBundle((annotation,)), "annotations.json")
```

The resulting JSON includes both verified `annotation_id` and `bundle_id`.

```bash
python3 -m cgoprof annotation-verify annotations.json

python3 -m cgoprof contract-infer path/to/module \
  --manifest input-manifest.json \
  --annotation annotations.json \
  --minimum-annotation-trust reviewed \
  --manifest-out result/api-manifest.json \
  --out result/contracts.json

python3 -m cgoprof contract-verify \
  result/contracts.json result/api-manifest.json
```

When C signatures enrich the Manifest, `--manifest-out` is mandatory. The CLI
will not emit a catalog whose new `manifest_id` is unavailable to consumers.

## 7. Output Artifacts

The workflow produces three auditable artifacts:

1. **API Manifest**: provider/signature/linkage bindings and unresolved
   isolation.
2. **Contract catalog**: merged seven-dimensional facts with provenance,
   status, conflicts, and exact BuildScope.
3. **C summary JSON**: per-unit signatures, layout, direct calls, completeness,
   raw parameter/result effects, proof reasons, and diagnostics.

Summary JSON is separate from stable Contract IR so analysis internals remain
debuggable without becoming part of the interchange schema.

## 8. Deliberate Proof Boundaries

The implementation does not make unsound guesses beyond its C-AST abstraction:

- Function-like macros still require a generated-wrapper identity frontend.
- An unresolved indirect call remains conservative.
- External providers are never inferred from header or symbol spelling.
- Pointer/length coupling, NUL termination, reference-count protocols, and
  API-specific lifetime conditions need body evidence or typed annotation; a
  `char *` signature alone does not prove a C string.
- Inline assembly and unmodeled compiler extensions invalidate completeness.
- Dynamically loaded behavior requires provider summaries or annotations.

These boundaries are visible in diagnostics and status, allowing later rewrite
logic to require explicit proof obligations instead of hidden optimism.
