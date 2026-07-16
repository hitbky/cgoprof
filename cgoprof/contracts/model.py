from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from .conditions import ArgumentCondition
from .evidence import Evidence, FactStatus, evidence_sort_key


CONTRACT_SCHEMA_VERSION = 1
T = TypeVar("T")


class ContractAttribute(str, Enum):
    MEMORY_ACCESS = "memory_access"
    OWNERSHIP = "ownership"
    LIFETIME = "lifetime"
    ESCAPE = "escape"
    CALLBACK = "callback"
    MUTABILITY = "mutability"
    REPRESENTATION = "representation"


class MemoryAccess(str, Enum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"
    UNKNOWN = "unknown"


class Ownership(str, Enum):
    BORROWED = "borrowed"
    TRANSFERRED_TO_CALLEE = "transferred_to_callee"
    TRANSFERRED_TO_CALLER = "transferred_to_caller"
    CALLEE_OWNED = "callee_owned"
    CALLER_OWNED = "caller_owned"
    SHARED = "shared"
    REFERENCE_COUNTED = "reference_counted"
    COPIED_BY_CALLEE = "copied_by_callee"
    UNKNOWN = "unknown"


class Lifetime(str, Enum):
    CALL_SCOPED = "call_scoped"
    UNTIL_NEXT_CALL = "until_next_call"
    UNTIL_REBIND = "until_rebind"
    OWNER_SCOPED = "owner_scoped"
    UNTIL_EXPLICIT_FREE = "until_explicit_free"
    PROCESS_LIFETIME = "process_lifetime"
    UNKNOWN = "unknown"


class Escape(str, Enum):
    NO_ESCAPE = "no_escape"
    MAY_ESCAPE = "may_escape"
    ESCAPES = "escapes"
    UNKNOWN = "unknown"


class Callback(str, Enum):
    NO_CALLBACK = "no_callback"
    MAY_CALLBACK = "may_callback"
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    OBSERVED_CALLBACK = "observed_callback"
    UNKNOWN = "unknown"


class Mutability(str, Enum):
    STABLE = "stable"
    MAY_MUTATE = "may_mutate"
    CALLEE_MUTATES = "callee_mutates"
    EXTERNALLY_MUTABLE = "externally_mutable"
    CONDITIONALLY_STABLE = "conditionally_stable"
    UNKNOWN = "unknown"


class RepresentationKind(str, Enum):
    SCALAR = "scalar"
    C_STRING = "c_string"
    POINTER_LENGTH = "pointer_length"
    FIXED_ARRAY = "fixed_array"
    STRUCT = "struct"
    OPAQUE_HANDLE = "opaque_handle"
    FUNCTION_POINTER = "function_pointer"
    GO_STRING = "go_string"
    GO_SLICE = "go_slice"
    RAW_BYTES = "raw_bytes"
    UNKNOWN = "unknown"


class Encoding(str, Enum):
    UTF8 = "utf8"
    UTF16 = "utf16"
    BYTES = "bytes"
    NATIVE = "native"
    UNKNOWN = "unknown"


class TriState(str, Enum):
    YES = "yes"
    NO = "no"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Representation:
    kind: RepresentationKind = RepresentationKind.UNKNOWN
    encoding: Encoding = Encoding.UNKNOWN
    nul_terminated: TriState = TriState.UNKNOWN
    length_argument: int | None = None
    alignment: int | None = None
    element_type: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.length_argument is not None and self.length_argument < 0:
            raise ValueError("representation length argument must be non-negative")
        if self.alignment is not None and self.alignment <= 0:
            raise ValueError("representation alignment must be positive")

    @classmethod
    def unknown(cls) -> "Representation":
        return cls()


@dataclass(frozen=True)
class ContractFact(Generic[T]):
    value: T
    status: FactStatus = FactStatus.UNKNOWN
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if self.status != FactStatus.UNKNOWN and not self.evidence:
            raise ValueError(f"{self.status.value} contract facts require evidence")
        semantically_unknown = _value_is_unknown(self.value)
        if self.status == FactStatus.UNKNOWN and not semantically_unknown:
            raise ValueError("unknown contract facts must use the attribute's unknown value")
        if semantically_unknown and self.status not in {FactStatus.UNKNOWN, FactStatus.CONFLICT}:
            raise ValueError(
                f"{self.status.value} contract facts must provide a non-unknown value"
            )
        object.__setattr__(self, "evidence", tuple(sorted(set(self.evidence), key=evidence_sort_key)))


def _unknown_fact(value: T) -> ContractFact[T]:
    return ContractFact(value=value)


def _value_is_unknown(value: Any) -> bool:
    if isinstance(value, Representation):
        return value == Representation.unknown()
    return getattr(value, "value", None) == "unknown"


@dataclass(frozen=True)
class ValueContract:
    memory_access: ContractFact[MemoryAccess] = field(
        default_factory=lambda: _unknown_fact(MemoryAccess.UNKNOWN)
    )
    ownership: ContractFact[Ownership] = field(
        default_factory=lambda: _unknown_fact(Ownership.UNKNOWN)
    )
    lifetime: ContractFact[Lifetime] = field(
        default_factory=lambda: _unknown_fact(Lifetime.UNKNOWN)
    )
    escape: ContractFact[Escape] = field(
        default_factory=lambda: _unknown_fact(Escape.UNKNOWN)
    )
    mutability: ContractFact[Mutability] = field(
        default_factory=lambda: _unknown_fact(Mutability.UNKNOWN)
    )
    representation: ContractFact[Representation] = field(
        default_factory=lambda: _unknown_fact(Representation.unknown())
    )

    def __post_init__(self) -> None:
        expected = (
            ("memory_access", self.memory_access, MemoryAccess),
            ("ownership", self.ownership, Ownership),
            ("lifetime", self.lifetime, Lifetime),
            ("escape", self.escape, Escape),
            ("mutability", self.mutability, Mutability),
            ("representation", self.representation, Representation),
        )
        for name, fact, fact_type in expected:
            if not isinstance(fact.value, fact_type):
                raise TypeError(
                    f"{name} facts require {fact_type.__name__}, "
                    f"got {type(fact.value).__name__}"
                )


@dataclass(frozen=True)
class ParameterContract:
    index: int
    name: str
    c_type: str
    contract: ValueContract = field(default_factory=ValueContract)

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("parameter index must be non-negative")


@dataclass(frozen=True)
class ResultContract:
    c_type: str
    contract: ValueContract = field(default_factory=ValueContract)


class ContractTargetKind(str, Enum):
    PARAMETER = "parameter"
    RESULT = "result"
    FUNCTION = "function"


@dataclass(frozen=True)
class ContractTarget:
    kind: ContractTargetKind
    index: int | None = None

    def __post_init__(self) -> None:
        if self.kind == ContractTargetKind.PARAMETER:
            if self.index is None or self.index < 0:
                raise ValueError("parameter targets require a non-negative index")
        elif self.index is not None:
            raise ValueError(f"{self.kind.value} targets must not have an index")


_ATTRIBUTE_TYPES: dict[ContractAttribute, type[Any]] = {
    ContractAttribute.MEMORY_ACCESS: MemoryAccess,
    ContractAttribute.OWNERSHIP: Ownership,
    ContractAttribute.LIFETIME: Lifetime,
    ContractAttribute.ESCAPE: Escape,
    ContractAttribute.CALLBACK: Callback,
    ContractAttribute.MUTABILITY: Mutability,
    ContractAttribute.REPRESENTATION: Representation,
}


@dataclass(frozen=True)
class ContractAssignment:
    target: ContractTarget
    attribute: ContractAttribute
    fact: ContractFact[Any]

    def __post_init__(self) -> None:
        expected = _ATTRIBUTE_TYPES[self.attribute]
        if not isinstance(self.fact.value, expected):
            raise TypeError(
                f"{self.attribute.value} assignments require {expected.__name__}, "
                f"got {type(self.fact.value).__name__}"
            )
        if self.attribute == ContractAttribute.CALLBACK:
            if self.target.kind != ContractTargetKind.FUNCTION:
                raise ValueError("callback assignments must target the function")
        elif self.target.kind == ContractTargetKind.FUNCTION:
            raise ValueError(
                f"{self.attribute.value} is a value-level attribute and cannot target the function"
            )


@dataclass(frozen=True)
class ConditionalClause:
    conditions: tuple[ArgumentCondition, ...]
    assignments: tuple[ContractAssignment, ...]

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError("conditional clauses require at least one condition")
        if not self.assignments:
            raise ValueError("conditional clauses require at least one assignment")


@dataclass(frozen=True)
class BuildScope:
    go_package: str = ""
    goos: str = ""
    goarch: str = ""
    build_tags: tuple[str, ...] = ()
    c_macros_fingerprint: str = ""
    library_version: str | None = None

    def __post_init__(self) -> None:
        if len(set(self.build_tags)) != len(self.build_tags):
            raise ValueError("build tags must be unique")
        object.__setattr__(self, "build_tags", tuple(sorted(self.build_tags)))


@dataclass(frozen=True)
class APIContract:
    api_id: str
    c_symbol: str
    scope: BuildScope = field(default_factory=BuildScope)
    parameters: tuple[ParameterContract, ...] = ()
    result: ResultContract | None = None
    callback: ContractFact[Callback] = field(
        default_factory=lambda: _unknown_fact(Callback.UNKNOWN)
    )
    clauses: tuple[ConditionalClause, ...] = ()
    diagnostics: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ValueError(f"unsupported contract schema version: {self.schema_version}")
        if not self.api_id.strip():
            raise ValueError("api_id must not be empty")
        if not self.c_symbol.strip():
            raise ValueError("c_symbol must not be empty")
        if not isinstance(self.callback.value, Callback):
            raise TypeError(
                f"callback facts require Callback, got {type(self.callback.value).__name__}"
            )
        object.__setattr__(self, "parameters", tuple(sorted(self.parameters, key=lambda item: item.index)))
        object.__setattr__(self, "metadata", tuple(sorted(self.metadata)))
        indices = [parameter.index for parameter in self.parameters]
        if len(indices) != len(set(indices)):
            raise ValueError("parameter indices must be unique")
        if len(self.metadata) != len({key for key, _ in self.metadata}):
            raise ValueError("metadata keys must be unique")
        parameter_indices = set(indices)
        for clause in self.clauses:
            for assignment in clause.assignments:
                if assignment.target.kind == ContractTargetKind.PARAMETER:
                    if assignment.target.index not in parameter_indices:
                        raise ValueError(
                            f"conditional assignment references unknown parameter "
                            f"{assignment.target.index}"
                        )
                elif assignment.target.kind == ContractTargetKind.RESULT and self.result is None:
                    raise ValueError("conditional assignment references a missing result")


@dataclass(frozen=True)
class ContractCatalog:
    contracts: tuple[APIContract, ...] = ()
    generated_by: str = "cgoprof"
    metadata: tuple[tuple[str, str], ...] = ()
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ValueError(f"unsupported catalog schema version: {self.schema_version}")
        object.__setattr__(self, "contracts", tuple(sorted(self.contracts, key=lambda item: item.api_id)))
        object.__setattr__(self, "metadata", tuple(sorted(self.metadata)))
        identifiers = [contract.api_id for contract in self.contracts]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("contract catalog contains duplicate api_id values")
        if len(self.metadata) != len({key for key, _ in self.metadata}):
            raise ValueError("catalog metadata keys must be unique")
