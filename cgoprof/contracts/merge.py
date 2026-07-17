from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .lattice import merge_facts
from .model import (
    APIContract,
    Callback,
    ConditionalClause,
    ContractAttribute,
    ContractFact,
    ParameterContract,
    ResultContract,
    ValueContract,
)


@dataclass(frozen=True)
class ContractMergeResult:
    """The deterministic result of combining independent contract sources."""

    contract: APIContract
    diagnostics: tuple[str, ...] = ()


def merge_contracts(left: APIContract, right: APIContract) -> ContractMergeResult:
    """Merge two summaries for one exact API/build scope.

    The operation is deliberately strict about identity and shape, and delegates
    semantic joins to the attribute lattice.  It never implements source
    precedence or last-writer-wins behavior.
    """

    if left.api_id != right.api_id:
        raise ValueError("cannot merge contracts for different api_id values")
    if left.c_symbol != right.c_symbol:
        raise ValueError("cannot merge contracts with different C symbols")
    if left.scope != right.scope:
        raise ValueError("cannot merge contracts from different build scopes")
    if len(left.parameters) != len(right.parameters):
        raise ValueError("cannot merge contracts with different parameter arity")

    diagnostics: list[str] = [*left.diagnostics, *right.diagnostics]
    parameters: list[ParameterContract] = []
    for left_parameter, right_parameter in zip(left.parameters, right.parameters):
        if left_parameter.index != right_parameter.index:
            raise ValueError("cannot merge contracts with different parameter indices")
        if left_parameter.c_type != right_parameter.c_type:
            raise ValueError("cannot merge contracts with different parameter C types")
        if (
            left_parameter.name != right_parameter.name
            and not left_parameter.name.startswith("arg")
            and not right_parameter.name.startswith("arg")
        ):
            diagnostics.append(
                f"parameter {left_parameter.index} has conflicting names: "
                f"{left_parameter.name!r} vs {right_parameter.name!r}"
            )
        name = _preferred_parameter_name(left_parameter.name, right_parameter.name)
        value, value_diagnostics = _merge_value_contracts(
            left_parameter.contract,
            right_parameter.contract,
            subject=f"parameter {left_parameter.index}",
        )
        diagnostics.extend(value_diagnostics)
        parameters.append(
            ParameterContract(left_parameter.index, name, left_parameter.c_type, value)
        )

    if (left.result is None) != (right.result is None):
        raise ValueError("cannot merge contracts with different result presence")
    result: ResultContract | None = None
    if left.result is not None and right.result is not None:
        if left.result.c_type != right.result.c_type:
            raise ValueError("cannot merge contracts with different result C types")
        value, value_diagnostics = _merge_value_contracts(
            left.result.contract,
            right.result.contract,
            subject="result",
        )
        diagnostics.extend(value_diagnostics)
        result = ResultContract(left.result.c_type, value)

    callback_outcome = merge_facts(
        ContractAttribute.CALLBACK,
        left.callback,
        right.callback,
    )
    diagnostics.extend(f"function: {item}" for item in callback_outcome.diagnostics)
    clauses = _merge_clauses(left.clauses, right.clauses)
    metadata, metadata_diagnostics = _merge_metadata(left.metadata, right.metadata)
    diagnostics.extend(metadata_diagnostics)
    unique_diagnostics = tuple(sorted(set(diagnostics)))
    contract = APIContract(
        api_id=left.api_id,
        c_symbol=left.c_symbol,
        scope=left.scope,
        parameters=tuple(parameters),
        result=result,
        callback=_callback_fact(callback_outcome.fact),
        clauses=clauses,
        diagnostics=unique_diagnostics,
        metadata=metadata,
    )
    return ContractMergeResult(contract, unique_diagnostics)


def merge_contract_sources(contracts: Iterable[APIContract]) -> ContractMergeResult:
    items = tuple(contracts)
    if not items:
        raise ValueError("at least one contract source is required")
    result = ContractMergeResult(items[0], items[0].diagnostics)
    for item in items[1:]:
        result = merge_contracts(result.contract, item)
    return result


def _merge_value_contracts(
    left: ValueContract,
    right: ValueContract,
    *,
    subject: str,
) -> tuple[ValueContract, tuple[str, ...]]:
    attributes = (
        (ContractAttribute.MEMORY_ACCESS, "memory_access"),
        (ContractAttribute.OWNERSHIP, "ownership"),
        (ContractAttribute.LIFETIME, "lifetime"),
        (ContractAttribute.ESCAPE, "escape"),
        (ContractAttribute.MUTABILITY, "mutability"),
        (ContractAttribute.REPRESENTATION, "representation"),
    )
    values: dict[str, ContractFact[object]] = {}
    diagnostics: list[str] = []
    for attribute, field_name in attributes:
        outcome = merge_facts(
            attribute,
            getattr(left, field_name),
            getattr(right, field_name),
        )
        values[field_name] = outcome.fact
        diagnostics.extend(
            f"{subject}: {item}" for item in outcome.diagnostics
        )
    return ValueContract(**values), tuple(diagnostics)  # type: ignore[arg-type]


def _callback_fact(fact: ContractFact[object]) -> ContractFact[Callback]:
    if not isinstance(fact.value, Callback):
        raise TypeError("callback lattice returned a non-callback value")
    return ContractFact(fact.value, fact.status, fact.evidence)


def _preferred_parameter_name(left: str, right: str) -> str:
    left_generic = left.startswith("arg") and left[3:].isdigit()
    right_generic = right.startswith("arg") and right[3:].isdigit()
    if left_generic != right_generic:
        return right if left_generic else left
    return min(left, right)


def _merge_clauses(
    left: tuple[ConditionalClause, ...],
    right: tuple[ConditionalClause, ...],
) -> tuple[ConditionalClause, ...]:
    # Condition constants may legally be non-hashable JSON values.  A typed
    # dataclass repr is stable here and avoids assuming hashability.
    by_representation = {repr(item): item for item in (*left, *right)}
    return tuple(by_representation[key] for key in sorted(by_representation))


def _merge_metadata(
    left: tuple[tuple[str, str], ...],
    right: tuple[tuple[str, str], ...],
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    left_map = dict(left)
    right_map = dict(right)
    result: dict[str, str] = {}
    diagnostics: list[str] = []
    for key in sorted(set(left_map) | set(right_map)):
        values = {item for item in (left_map.get(key), right_map.get(key)) if item is not None}
        if len(values) == 1:
            result[key] = values.pop()
        else:
            diagnostics.append(
                f"metadata key {key!r} has conflicting values: {sorted(values)!r}"
            )
    return tuple(sorted(result.items())), tuple(diagnostics)
