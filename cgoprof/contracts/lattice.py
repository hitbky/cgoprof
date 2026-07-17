from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .evidence import Evidence, FactStatus, evidence_sort_key
from .model import (
    Callback,
    ContractAttribute,
    ContractFact,
    Escape,
    MemoryAccess,
    Mutability,
    Representation,
    RepresentationKind,
)


@dataclass(frozen=True)
class MergeOutcome:
    fact: ContractFact[Any]
    diagnostics: tuple[str, ...] = ()


_STATUS_RANK = {
    FactStatus.UNKNOWN: 0,
    FactStatus.HEURISTIC: 1,
    FactStatus.OBSERVED: 2,
    FactStatus.DECLARED: 3,
    FactStatus.PROVEN: 4,
    FactStatus.INTRINSIC: 5,
    FactStatus.CONFLICT: -1,
}


def merge_facts(
    attribute: ContractAttribute,
    left: ContractFact[Any],
    right: ContractFact[Any],
) -> MergeOutcome:
    if _is_missing(attribute, left):
        return MergeOutcome(right)
    if _is_missing(attribute, right):
        return MergeOutcome(left)

    joiner = _JOINERS[attribute]
    value, conflict = joiner(left.value, right.value)
    evidence = _merge_evidence(left.evidence, right.evidence)
    same_value = left.value == right.value
    status = _merge_status(left.status, right.status, same_value, conflict)
    diagnostics: tuple[str, ...] = ()
    if conflict:
        diagnostics = (
            f"conflicting {attribute.value} facts: {_display(left.value)} vs {_display(right.value)}",
        )
    return MergeOutcome(
        fact=ContractFact(value=value, status=status, evidence=evidence),
        diagnostics=diagnostics,
    )


def _merge_status(
    left: FactStatus,
    right: FactStatus,
    same_value: bool,
    conflict: bool,
) -> FactStatus:
    if conflict or left == FactStatus.CONFLICT or right == FactStatus.CONFLICT:
        return FactStatus.CONFLICT
    if same_value:
        return max((left, right), key=lambda status: _STATUS_RANK[status])
    return min((left, right), key=lambda status: _STATUS_RANK[status])


def _merge_evidence(left: tuple[Evidence, ...], right: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    return tuple(sorted(set(left + right), key=evidence_sort_key))


def _is_missing(attribute: ContractAttribute, fact: ContractFact[Any]) -> bool:
    if fact.status != FactStatus.UNKNOWN or fact.evidence:
        return False
    value = fact.value
    if attribute == ContractAttribute.REPRESENTATION:
        return value == Representation.unknown()
    return getattr(value, "value", None) == "unknown"


def _join_memory_access(left: MemoryAccess, right: MemoryAccess) -> tuple[MemoryAccess, bool]:
    if left == right:
        return left, False
    effects = {
        MemoryAccess.NONE: frozenset(),
        MemoryAccess.READ: frozenset({"read"}),
        MemoryAccess.WRITE: frozenset({"write"}),
        MemoryAccess.READ_WRITE: frozenset({"read", "write"}),
    }
    joined = effects[left] | effects[right]
    result = {
        frozenset(): MemoryAccess.NONE,
        frozenset({"read"}): MemoryAccess.READ,
        frozenset({"write"}): MemoryAccess.WRITE,
        frozenset({"read", "write"}): MemoryAccess.READ_WRITE,
    }[joined]
    conflict = MemoryAccess.NONE in {left, right} and result != MemoryAccess.NONE
    return result, conflict


def _join_escape(left: Escape, right: Escape) -> tuple[Escape, bool]:
    if left == right:
        return left, False
    if Escape.ESCAPES in {left, right}:
        return Escape.ESCAPES, Escape.NO_ESCAPE in {left, right}
    if Escape.MAY_ESCAPE in {left, right}:
        return Escape.MAY_ESCAPE, Escape.NO_ESCAPE in {left, right}
    return Escape.MAY_ESCAPE, True


def _join_callback(left: Callback, right: Callback) -> tuple[Callback, bool]:
    if left == right:
        return left, False
    values = {left, right}
    if Callback.NO_CALLBACK in values:
        other = right if left == Callback.NO_CALLBACK else left
        return other, True
    if values == {Callback.SYNCHRONOUS, Callback.ASYNCHRONOUS}:
        return Callback.MAY_CALLBACK, True
    if Callback.MAY_CALLBACK in values:
        other = right if left == Callback.MAY_CALLBACK else left
        return other, False
    if Callback.OBSERVED_CALLBACK in values:
        other = right if left == Callback.OBSERVED_CALLBACK else left
        if other in {Callback.SYNCHRONOUS, Callback.ASYNCHRONOUS}:
            return other, False
        return Callback.OBSERVED_CALLBACK, False
    return Callback.MAY_CALLBACK, True


def _join_mutability(left: Mutability, right: Mutability) -> tuple[Mutability, bool]:
    if left == right:
        return left, False
    values = {left, right}
    mutating = {
        Mutability.MAY_MUTATE,
        Mutability.CALLEE_MUTATES,
        Mutability.EXTERNALLY_MUTABLE,
    }
    if Mutability.STABLE in values:
        other = right if left == Mutability.STABLE else left
        if other == Mutability.CONDITIONALLY_STABLE:
            return Mutability.CONDITIONALLY_STABLE, False
        return other if other in mutating else Mutability.MAY_MUTATE, True
    if Mutability.MAY_MUTATE in values:
        other = right if left == Mutability.MAY_MUTATE else left
        return other if other in mutating else Mutability.MAY_MUTATE, False
    if values == {Mutability.CALLEE_MUTATES, Mutability.EXTERNALLY_MUTABLE}:
        return Mutability.MAY_MUTATE, False
    if Mutability.CONDITIONALLY_STABLE in values:
        other = right if left == Mutability.CONDITIONALLY_STABLE else left
        return other if other in mutating else Mutability.CONDITIONALLY_STABLE, False
    return Mutability.MAY_MUTATE, True


def _join_exact_or_unknown(left: Any, right: Any) -> tuple[Any, bool]:
    if left == right:
        return left, False
    enum_type = type(left)
    return enum_type.UNKNOWN, True


def _join_representation(
    left: Representation, right: Representation
) -> tuple[Representation, bool]:
    if left == right:
        return left, False
    if left.kind == RepresentationKind.UNKNOWN:
        return right, False
    if right.kind == RepresentationKind.UNKNOWN:
        return left, False
    refinements = {
        RepresentationKind.C_STRING,
        RepresentationKind.POINTER_LENGTH,
        RepresentationKind.FIXED_ARRAY,
    }
    if left.kind == RepresentationKind.RAW_BYTES and right.kind in refinements:
        return right, False
    if right.kind == RepresentationKind.RAW_BYTES and left.kind in refinements:
        return left, False
    return Representation.unknown(), True


def _display(value: Any) -> str:
    if isinstance(value, Representation):
        return value.kind.value
    return str(getattr(value, "value", value))


Joiner = Callable[[Any, Any], tuple[Any, bool]]
_JOINERS: dict[ContractAttribute, Joiner] = {
    ContractAttribute.MEMORY_ACCESS: _join_memory_access,
    ContractAttribute.OWNERSHIP: _join_exact_or_unknown,
    ContractAttribute.LIFETIME: _join_exact_or_unknown,
    ContractAttribute.ESCAPE: _join_escape,
    ContractAttribute.CALLBACK: _join_callback,
    ContractAttribute.MUTABILITY: _join_mutability,
    ContractAttribute.REPRESENTATION: _join_representation,
}
