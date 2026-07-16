# CGOProf Contract Model

## 1. Purpose

CGOProf uses contracts to connect three different kinds of information:

```text
API contract       What the C API permits, requires, or guarantees.
Call-site facts    What a particular Go call site passes to that API.
Observed evidence  What happened under a profiled workload.
```

The contract model does not assign performance costs by itself. It constrains
cost attribution and optimization legality. In particular, a high-frequency
copy, pointer transfer, or callback is only a hotspot until the contract and
call-site facts show that a semantics-preserving alternative may exist.

The Contract IR has four design goals:

1. represent the seven core contract attributes without defaulting unknown
   behavior to safe behavior;
2. preserve the source, scope, and strength of every fact;
3. support argument-dependent contracts;
4. provide a stable, versioned JSON interchange format for later static
   analysis, dynamic profiling, graph construction, and rewrite checking.

## 2. Analysis Units

Contracts are not flat labels attached only to a C symbol. The IR distinguishes:

- **API scope**: a content-addressed API identity linked through a build
  Manifest to the Go package, target, flags, macros, and library release;
- **function effects**: currently the callback contract, with room for later
  blocking, thread-affinity, ordering, and global-side-effect summaries;
- **parameter contracts**: memory access, ownership, lifetime, escape,
  mutability, and representation for each argument;
- **result contract**: the same value-level properties for the return value;
- **conditional clauses**: facts that apply only when call arguments satisfy
  specified predicates.

The same C symbol in different providers or build configurations may have
different contracts. Phase 2 defines `api_id` from provider and ABI-canonical
signature, then records package-local `C.name` bindings in an exact build
Manifest. See `docs/api_identity_manifest.md`.

## 3. The Seven Core Attributes

### 3.1 Memory Access

Memory access describes what the callee may do to the memory region reachable
through a parameter during the call.

| Value | Meaning |
|---|---|
| `none` | The analyzed implementation does not access the referenced region. |
| `read` | The callee may read but does not write the region. |
| `write` | The callee may write without requiring the previous contents. |
| `read_write` | The callee may both read and write the region. |
| `unknown` | No usable information is available. |

Later analyses may attach a region or size expression, such as
`read(arg[0], bytes=arg[1])`. In the initial IR, those expressions can be kept
in evidence details or metadata.

`unknown` means absence of information. If a parameter is passed to an
unresolved external callee, the analyzer must emit a conservative effect such
as `read_write` or a separate `may` diagnostic rather than treating the
absence of a summary as harmless.

### 3.2 Ownership

Ownership describes responsibility for retaining and releasing a resource.

| Value | Meaning |
|---|---|
| `borrowed` | The callee may use the object but does not take ownership. |
| `transferred_to_callee` | Ownership moves from the caller to the callee. |
| `transferred_to_caller` | Ownership moves from the callee to the caller. |
| `callee_owned` | The callee owns the object; the caller must not release it. |
| `caller_owned` | The caller owns the object and remains responsible for release. |
| `shared` | Multiple parties may retain the object under an external protocol. |
| `reference_counted` | Lifetime is governed by a retain/release protocol. |
| `copied_by_callee` | The callee creates and owns an independent copy. |
| `unknown` | Ownership responsibility is not known. |

Ownership is distinct from lifetime. `borrowed` says who does not own the
object; lifetime says how long the borrowed reference remains valid.

### 3.3 Lifetime

Lifetime describes the validity interval of an object or reference.

| Value | Meaning |
|---|---|
| `call_scoped` | Valid only for the dynamic call. |
| `until_next_call` | Valid until a relevant subsequent API call. |
| `until_rebind` | Valid until a parameter or resource is rebound. |
| `owner_scoped` | Valid while an associated owner object remains alive. |
| `until_explicit_free` | Valid until an explicit release operation. |
| `process_lifetime` | Valid for the lifetime of the process. |
| `unknown` | No validity bound is known. |

Lifetime may depend on argument values or another object. Such dependencies
belong in conditional clauses and evidence until the IR gains symbolic owner
references.

### 3.4 Escape

Escape describes whether a reference may remain reachable by C after the
dynamic call returns.

| Value | Meaning |
|---|---|
| `no_escape` | Complete evidence says the reference is not retained past the call. |
| `may_escape` | Some path or unresolved callee may retain the reference. |
| `escapes` | A store, return, registration, or other retaining path is known. |
| `unknown` | Escape behavior has not been analyzed. |

`no_escape` is a safety-sensitive fact. It requires intrinsic semantics,
complete static proof, or an explicitly trusted declaration. Absence of a
dynamic escape observation is never sufficient evidence.

### 3.5 Callback

Callback describes whether and how C may transfer control back into Go.

| Value | Meaning |
|---|---|
| `no_callback` | Complete evidence says the call cannot callback into Go. |
| `may_callback` | A callback path may exist or cannot be ruled out. |
| `synchronous` | Callback occurs before the outer C call returns. |
| `asynchronous` | Callback may occur after the outer C call returns. |
| `observed_callback` | A callback was observed dynamically; its general mode is not proven. |
| `unknown` | Callback behavior has not been analyzed. |

The callback contract is a function-level effect, although parameters may
identify callback targets or context values. Dynamic observation can establish
`may_callback`, but lack of an observed callback cannot establish
`no_callback`.

### 3.6 Mutability

Mutability describes whether the logical contents remain stable during a reuse
or caching interval. It is deliberately separate from memory access, which
describes one callee invocation.

| Value | Meaning |
|---|---|
| `stable` | Contents are proven stable for the stated scope. |
| `may_mutate` | Contents may change, but the mutating agent is not resolved. |
| `callee_mutates` | The callee is known to modify the contents. |
| `externally_mutable` | Another actor or alias may modify the contents. |
| `conditionally_stable` | Stability holds only under a recorded condition. |
| `unknown` | No stability information is available. |

A C `const` qualifier may support a declared read-only access fact, but it does
not by itself prove no escape, stable lifetime, global immutability, or absence
of mutation through another alias.

### 3.7 Representation

Representation describes the physical form required or produced at the
language boundary.

The initial IR represents:

- `kind`: `scalar`, `c_string`, `pointer_length`, `fixed_array`, `struct`,
  `opaque_handle`, `function_pointer`, `go_string`, `go_slice`, `raw_bytes`, or
  `unknown`;
- `encoding`: `utf8`, `utf16`, `bytes`, `native`, or `unknown`;
- `nul_terminated`: `yes`, `no`, `conditional`, or `unknown`;
- optional length-argument index;
- optional alignment;
- optional element type and notes.

Representation requirements determine whether conversion, allocation,
termination, scanning, transcoding, or layout adaptation is necessary.

## 4. Conditional Contracts

Some APIs change ownership, lifetime, representation, or callback behavior
based on argument values. The initial condition language intentionally supports
only auditable predicates:

| Operator | Meaning |
|---|---|
| `eq` | Argument equals a constant. |
| `ne` | Argument does not equal a constant. |
| `bit_set` | All bits in an integer mask are set. |
| `is_null` | Argument is null. |
| `not_null` | Argument is non-null. |

A clause is a conjunction of conditions and a list of typed assignments:

```json
{
  "when": [
    {"argument": 4, "operator": "eq", "value": "SQLITE_TRANSIENT"}
  ],
  "assign": [
    {
      "target": {"kind": "parameter", "index": 2},
      "attribute": "ownership",
      "fact": {
        "value": "copied_by_callee",
        "status": "declared",
        "evidence": [
          {
            "kind": "api_documentation",
            "source": "SQLite bind API documentation",
            "detail": "SQLITE_TRANSIENT causes SQLite to copy the input",
            "location": null
          }
        ]
      }
    },
    {
      "target": {"kind": "parameter", "index": 2},
      "attribute": "lifetime",
      "fact": {
        "value": "call_scoped",
        "status": "declared",
        "evidence": [
          {
            "kind": "api_documentation",
            "source": "SQLite bind API documentation",
            "detail": "input is copied before the bind call returns",
            "location": null
          }
        ]
      }
    }
  ]
}
```

Condition evaluation returns `match`, `no_match`, or `unknown`. A clause with
an unknown condition must not be applied as a proven fact.

## 5. Evidence and Fact Status

Every non-unknown contract fact carries provenance. The IR recognizes these
evidence kinds:

- `cgo_intrinsic`
- `cgo_directive`
- `c_signature`
- `c_body_analysis`
- `go_analysis`
- `api_documentation`
- `user_annotation`
- `dynamic_observation`
- `heuristic`

Each fact also has a status:

| Status | Meaning |
|---|---|
| `unknown` | No information is available. |
| `heuristic` | A naming or structural heuristic suggests the value. |
| `observed` | The behavior occurred in a dynamic workload. |
| `declared` | A directive, annotation, signature, or document declares it. |
| `proven` | Static analysis proves it within the recorded scope. |
| `intrinsic` | The semantics are built into cgo or another trusted intrinsic. |
| `conflict` | Available evidence is contradictory or incompatible. |

Status is not a probability. A stronger declaration cannot silently overwrite
contradictory positive evidence. For example, an observed callback combined
with a `no_callback` directive yields a conservative callback value and a
`conflict` status.

## 6. Conservative Merge Semantics

The merge lattice follows these rules:

1. an `unknown` fact with no evidence acts as missing information and may be
   filled by another source;
2. compatible effects are joined, for example `read + write = read_write`;
3. positive `may` or observed behavior dominates a negative safety claim;
4. incompatible ownership, lifetime, or representation facts become
   `unknown` with `conflict` status;
5. evidence from all sources is preserved and deduplicated;
6. merge results are deterministic and independent of source order.

An unresolved external C call is not modeled as a harmless unknown fact. The C
analyzer must explicitly emit conservative `may_escape`, `may_callback`, or
`read_write` effects as appropriate.

## 7. Build Scope

Contracts are valid only in a recorded build scope:

```text
Go package import path
GOOS / GOARCH
build tags
C preprocessor macro fingerprint
linked library version, when known
exact content-addressed provider release id
exact content-addressed build id
```

The same C symbol may resolve to different implementations under different
build tags or macros. A contract inferred for one scope must not be reused as a
proof in another scope unless compatibility is established.

## 8. Relationship to Boundary Cost Analysis

The contract model constrains cost classification:

| Contract fact | Relevant cost component |
|---|---|
| memory access | copy-in, copy-out, touched bytes, memory bandwidth |
| ownership | allocation, free, destructor, transfer, reuse |
| lifetime | required copying, cache validity, pin duration |
| escape | heap promotion, pinning, GC/liveness |
| callback | callback preparation, reverse transition, callback work |
| mutability | cache invalidation, copy-back, hoisting legality |
| representation | conversion, copy, scan, transcoding, alignment |

The planned cost model should keep these components distinct:

```text
T_interaction = T_transition_base
              + T_callback_prepare
              + T_c_work
              + T_representation_copy
              + T_allocation_free
              + T_escape_pin_gc
              + T_pointer_check
              + T_dynamic_callback
              + epsilon
```

In particular, `#cgo noescape` concerns heap escape and liveness; it must not be
treated as proof that runtime pointer checks disappear. `#cgo nocallback`
concerns callback preparation. The Contract IR records these semantic facts but
does not invent timing values.

## 9. Serialization and Compatibility

Contract JSON schema version is `2`. A contract catalog contains:

```json
{
  "schema_version": 2,
  "generated_by": "cgoprof",
  "manifest_id": "cgomanifest:v1:<sha256>",
  "contracts": []
}
```

Serialization must be deterministic. Readers reject unsupported schema
versions, free-form or malformed API identifiers, duplicate API identifiers,
malformed target indices, and assignments whose values do not match their
attribute type. Proof-grade consumers additionally require exact
`manifest_id`/`build_id` linkage.

Schema v1 catalogs used free-form API labels and cannot be upgraded without
provider/signature evidence. Readers therefore reject v1 rather than inventing
content-addressed identities; producers must regenerate them with a Manifest.

The Contract IR is separate from the current runtime profile schema. Existing
profiles remain readable without a contract catalog and produce only baseline
hotspot findings.

## 10. Initial Non-Goals

The first Contract IR does not attempt to provide:

- full C/C++ points-to or alias analysis;
- a proof from absence of dynamic behavior;
- automatic insertion of `#cgo noescape` or `#cgo nocallback`;
- a numeric confidence score;
- exact timing or avoidable-cost estimates;
- automatic rewrite legality decisions.

Those capabilities consume the IR after its semantics and provenance are
stable.

## 11. Phase 0 and Phase 1 Completion Criteria

The model is ready for later inference work when:

- all seven attributes have typed domains and explicit unknown values;
- contracts are represented per API, parameter, and result;
- conditional clauses are representable and tri-state evaluable;
- every fact can carry status, evidence, and build scope;
- conservative merge operations are deterministic and preserve conflicts;
- catalogs round-trip through versioned JSON without losing information;
- malformed or duplicate structures fail validation;
- the package has unit tests independent of profiler events and rules.
