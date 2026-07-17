# CGOProf API Identity and Manifest

## 1. Purpose

Contract-aware analysis is unsafe if a contract is selected by `C.symbol`
alone. The same spelling can denote:

- unrelated functions from different libraries;
- a package-local `static` function;
- a function-like macro lowered to a generated wrapper;
- different declarations under build tags or preprocessor macros;
- an ABI-incompatible function in another library or target;
- a cgo pseudo-function such as `C.CString`.

Phase 2 therefore establishes a proof-grade identity layer between source
discovery and Contract IR:

```text
library API identity
        |
        v
Go-package C.name binding
        |
        v
one exact build manifest
        |
        v
contract catalog linked by manifest_id + api_id + build_id
```

An identity is exact only when the provider and ABI-canonical function
signature are known. Missing information is represented by an unresolved
binding. The implementation never converts a unique-looking symbol match into
proof.

## 2. Three Separate Identities

### 2.1 Library API identity

`APIIdentity` names the callable API independently of any particular Go use
site. Its content-addressed `api_id` includes:

| Field | Reason |
|---|---|
| provider kind, namespace, and name | distinguishes the same symbol in different libraries |
| API kind | distinguishes a function, function-like macro, and cgo intrinsic |
| logical symbol | provides stable human-facing API membership |
| linkage name | distinguishes a generated or alternate linker symbol |
| canonical result and parameter types | distinguishes ABI-incompatible declarations |
| variadic bit | distinguishes fixed and variadic entry points |
| calling convention | distinguishes incompatible call sequences |
| ABI tag | prevents reuse across incompatible ABI canonicalization domains |

Parameter names, declaration locations, header paths, use sites, library
release versions, and source spellings are not part of `api_id`. They can
change without changing the callable ABI. Library version and declaration
evidence remain in the Manifest and contract build scope.

`family_id` excludes the function signature. It groups ABI variants for
diagnostics and evolution analysis, but it is never sufficient for selecting a
contract.

### 2.2 Go-package binding identity

Every Go package has its own pseudo-package `C`. `APIBinding` therefore records:

```text
(Go package id, C selector, exact api_id, binding kind, linkage)
```

The resulting `binding_id` distinguishes two packages that both call
`C.open`, even when they resolve to different providers or generated wrappers.
Bindings retain declaration sites, use sites, aliases, and
`#cgo noescape`/`#cgo nocallback` directives, but those observations do not
change the binding identity.

### 2.3 Build Manifest identity

`APIManifest` is an immutable snapshot of one exact build context. It records
packages, provider releases, exact APIs, bindings, unresolved bindings, and
diagnostics. `manifest_id` hashes the complete semantic payload, including the
build identity. Any modification invalidates the ID.

This is intentionally not a global symbol database. Separate GOOS, GOARCH,
toolchain, flag, macro, or source configurations produce separate Manifests.

## 3. Canonical C Function Signatures

`CTypeIdentity.canonical` is required to be typedef-resolved and
target-ABI-canonical before it is used in `APIIdentity`. The identity layer
normalizes irrelevant whitespace, but it does not pretend to perform C type
resolution.

A declaration frontend is responsible for resolving:

- typedef chains;
- signedness and integer width;
- pointer target qualifiers;
- array/function decay where applicable;
- structure/union identity and layout domain;
- target-dependent calling convention;
- variadic status;
- ABI size and alignment when available.

For example, a frontend may establish that `size_t` is the same canonical type
as its target-specific unsigned integer representation. A source scanner that
only sees the token `size_t` has not established that fact.

`source_spelling` can be retained for audit output but is excluded from the
signature hash. Two source spellings with identical ABI-canonical types produce
the same ID; changing a parameter type, result type, order, variadic bit,
calling convention, provider, or ABI tag changes the ID.

Optional measured size/alignment fields are also Manifest audit data rather
than identity inputs. This prevents an ID from changing merely because one
frontend knows more layout detail. Manifest assembly fills missing measurements
but rejects conflicting non-missing measurements.

cgo pseudo-functions use an explicit `cgo-pseudo-v1` ABI tag and canonical
Go/C pseudo-types. They cannot collide with external C functions.

Phase 4 implements the declaration frontend with Clang JSON AST. It resolves
typedefs, extracts calling shape and target ABI, probes type size/alignment,
preserves declarations, and qualifies package-local static linkage by
translation-unit content. Exact binding still requires an explicit provider
(or the content-addressed package-local source provider); the frontend does not
guess providers from names.

## 4. Content-Addressed IDs

All stable identifiers use:

```text
<kind>:v<schema>:<64 lowercase SHA-256 hex digits>
```

Implemented kinds are:

| Prefix | Payload |
|---|---|
| `cgoapi` | exact provider, symbol, API kind, and signature |
| `cgofamily` | provider, symbol, and API kind without signature |
| `cgosig` | canonical function signature |
| `cgoprov` | provider namespace |
| `cgorelease` | exact provider version/ABI/artifact record |
| `cgopkg` | Go module and package import path |
| `cgobind` | package-local `C.name` to API mapping |
| `cgoref` | unresolved package-local selector |
| `cgobuild` | exact target/toolchain/flag/macro context |
| `cgomanifest` | complete Manifest semantic payload |

Hash input is UTF-8 canonical JSON with sorted keys and no insignificant
whitespace. IDs are typed and versioned: a well-formed `cgoapi` ID is not
accepted where a `cgopkg` or `cgomanifest` ID is required.

Content addressing provides deterministic joins and tamper detection. It does
not prove that a producer supplied a correct canonical signature; that claim
must remain attributable to a declaration frontend and its evidence.

## 5. Exact Build Context

`BuildContext` contains:

- GOOS and GOARCH;
- target triple, pointer width, endianness, and data model;
- Go version, C compiler identity, and compiler version when available;
- cgo enabled state;
- sorted Go build tags;
- ordered global C, preprocessor, and linker flags;
- normalized C macro definitions.

Flag order is preserved because it may change include, link, or macro
resolution. Macro names are unique, and conflicting definitions are rejected.
The normalized macro set also has a SHA-256 fingerprint for the existing
Contract `BuildScope`.

Package-specific cgo flags and macros are stored on `GoPackageRecord`; they are
not incorrectly flattened into a global unordered set.

The build ID is exact, not a compatibility claim. A later compatibility
analysis may prove that two build contexts are contract-equivalent, but it
must not be inferred from a matching symbol.

## 6. Manifest Records and Invariants

### 6.1 Package records

A package record includes:

- module path and import path;
- package name and module release/sum when known;
- workspace-relative source file list;
- a deterministic digest over file paths and bytes;
- package-local cgo flags and macro definitions.

Absolute workspace paths are excluded so the same checkout produces the same
Manifest in another directory.

### 6.2 Provider records

A provider has a stable namespace identity plus release-specific information:

- version and ABI version;
- pkg-config, shared-library, archive, framework, or source artifacts;
- optional artifact digest;
- auditable metadata.

Provider version is excluded from `api_id` but included in the Manifest and
checked against a contract's `library_version` when present. Every provider
record also has a content-addressed `release_id` over provider ID, version, ABI
version, and artifacts; a record must supply at least one release discriminator.

### 6.3 API and binding records

Every exact API references a provider present in the same Manifest. Every
binding references both a present package and API. Within one build, a
`(package_id, C.name)` selector has at most one exact binding.

Declaration and use locations use positive line/column values and
workspace-relative paths. Content digests allow a consumer to determine
whether a location still refers to the recorded source bytes.

### 6.4 Unresolved records

When an exact ID cannot be established, `UnresolvedBinding` records:

- package and `C.name`;
- missing-identity-components, missing-provider, missing-signature, ambiguous,
  unsupported, or build-error reason;
- all use sites and cgo directives;
- candidate API IDs when ambiguity is known;
- a human-readable explanation.

A selector cannot be both resolved and unresolved. Ambiguous records require
at least two candidates, and every candidate must be present in the Manifest.

### 6.5 Completeness

A Manifest is `complete` only when it has no unresolved bindings and no error
diagnostics. Otherwise it is `partial`.

`require_complete()` is the explicit proof gate. Partial Manifests remain
useful for discovery and diagnostics, but downstream analysis must not use
them to claim full API coverage.

## 7. Resolution Rules

`ManifestIndex` exposes two different operations:

1. `resolve_binding(import_path, cgo_name)` follows a recorded package binding.
   It returns `exact`, `unresolved`, or `not_found`.
2. `resolve_symbol(symbol, provider_id, signature_id)` searches the API
   inventory.

A symbol-only lookup returns `candidate` even when there is only one current
match. Multiple matches return `ambiguous`. Only provider plus signature can
produce exact symbol resolution. `require_exact()` fails for every non-exact
result.

This distinction prevents accidental code such as:

```text
if one API named "open" exists:
    attach its contract
```

from becoming a proof path.

## 8. Contract IR Integration

Contract schema version 2 replaces free-form API labels with typed
`cgoapi:v1:...` identifiers and adds:

- `ContractCatalog.manifest_id`;
- `BuildScope.build_id`;
- `BuildScope.provider_release_id`;
- exact Manifest linkage validation.

`validate_contract_catalog()` checks:

- catalog `manifest_id` equality;
- presence of every contract API in the Manifest;
- exact build ID;
- GOOS, GOARCH, build tags, macro fingerprint, and provider version;
- exact provider release ID and, when recorded, human-readable library version;
- existence of the API binding in the contract's Go package;
- contract/signature symbol, parameter arity, and canonical parameter/result
  types.

`ContractStore(..., manifest, require_linked=True)` refuses construction if any
link error exists. Binding lookup then resolves:

```text
(Go import path, C.name) -> API binding -> api_id -> APIContract
```

This gate is required before a contract fact can be used as proof for cost
avoidability or rewrite legality.

## 9. Serialization and Integrity

Manifest JSON schema version is `1`. Serialization is deterministic and
contains both `build_id` and `manifest_id`.

The reader rejects:

- unsupported schema versions;
- duplicate JSON object keys;
- unknown or missing fields;
- non-finite JSON numbers;
- malformed typed IDs;
- content IDs that do not match their payload;
- duplicate packages, providers, APIs, bindings, or selectors;
- dangling package/provider/API references;
- resolved/unresolved overlap;
- absolute or parent-traversing source paths;
- malformed flags, macro conflicts, or metadata conflicts.

Strict unknown-field rejection is deliberate. Schema evolution requires a
schema version change rather than silently ignoring a misspelled safety field.

## 10. Project Discovery

The built-in discovery frontend uses authoritative Go commands:

```text
go env -json ...
go list -json ./...
```

It records actual module/package identities, selected cgo files, flags,
compiler identity, target, source digests, call sites, and cgo directives.
The reference scanner lexically masks Go comments, interpreted/raw strings,
and rune literals before matching `C.name` calls, and extracts directives only
from real comments. If a file may shadow the imported pseudo-package name `C`,
the frontend emits an error diagnostic and refuses to resolve that file.

The frontend currently establishes exact identities for the well-defined cgo
pseudo-functions:

- `C.CString`
- `C.CBytes`
- `C.GoString`
- `C.GoStringN`
- `C.GoBytes`

For arbitrary C functions, source regexes are not treated as an ABI parser.
Until a C declaration frontend supplies provider and ABI-canonical signature,
the selector is emitted as unresolved. `ManifestAssembler` is the
conflict-detecting integration interface for future Clang/C AST summaries,
generated cgo type information, curated library manifests, and audited
annotations.

This is a reliability boundary, not a missing-value default: a partial
Manifest explicitly prevents proof-grade lookup while preserving all
discovery evidence.

## 11. CLI

Generate a Manifest:

```bash
python3 -m cgoprof manifest ./path/to/module \
  --tags sqlite,fts5 \
  --out api-manifest.json
```

Require full resolution:

```bash
python3 -m cgoprof manifest ./path/to/module \
  --out api-manifest.json \
  --require-complete
```

Verify schema, referential integrity, and the content ID:

```bash
python3 -m cgoprof manifest-verify api-manifest.json
```

Without `--out`, Manifest JSON is written to stdout. Discovery failures and
`--require-complete` failures return exit status 2.

## 12. Phase 2 Completion Criteria

Phase 2 is complete when:

- exact API identity includes provider and ABI-canonical signature;
- package bindings are independent from library identity;
- build context is content-addressed and includes target/toolchain/flags/macros;
- package and provider releases remain auditable without polluting stable API
  identity;
- unresolved and ambiguous selectors cannot be consumed as exact;
- Manifests are deterministic, strict, self-verifying, and path-independent;
- Contract catalogs can be bound to one exact Manifest/build and fail closed;
- project discovery produces real package/build/use-site records;
- exact cgo intrinsics and unresolved external APIs coexist honestly;
- unit and CLI integration tests cover collisions, tampering, build mismatch,
  partial completeness, linking, and deterministic discovery.
