from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .conditions import ArgumentCondition, ConditionOperator
from .evidence import Evidence, EvidenceKind, FactStatus
from .model import (
    APIContract,
    CONTRACT_SCHEMA_VERSION,
    BuildScope,
    Callback,
    ConditionalClause,
    ContractAssignment,
    ContractAttribute,
    ContractCatalog,
    ContractFact,
    ContractTarget,
    ContractTargetKind,
    Encoding,
    Escape,
    Lifetime,
    MemoryAccess,
    Mutability,
    Ownership,
    ParameterContract,
    Representation,
    RepresentationKind,
    ResultContract,
    TriState,
    ValueContract,
)


def dumps_catalog(catalog: ContractCatalog, *, indent: int = 2) -> str:
    return json.dumps(catalog_to_dict(catalog), indent=indent, sort_keys=True) + "\n"


def loads_catalog(text: str) -> ContractCatalog:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("contract catalog must be a JSON object")
    return catalog_from_dict(data)


def dump_catalog(catalog: ContractCatalog, path: str | Path) -> None:
    Path(path).write_text(dumps_catalog(catalog), encoding="utf-8")


def load_catalog(path: str | Path) -> ContractCatalog:
    return loads_catalog(Path(path).read_text(encoding="utf-8"))


def catalog_to_dict(catalog: ContractCatalog) -> dict[str, Any]:
    return {
        "schema_version": catalog.schema_version,
        "generated_by": catalog.generated_by,
        "manifest_id": catalog.manifest_id,
        "metadata": dict(sorted(catalog.metadata)),
        "contracts": [contract_to_dict(item) for item in sorted(catalog.contracts, key=lambda c: c.api_id)],
    }


def catalog_from_dict(data: Mapping[str, Any]) -> ContractCatalog:
    schema_version = int(data.get("schema_version", 0))
    if schema_version != CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported catalog schema version: {schema_version}")
    contracts_data = _require_list(data.get("contracts", []), "contracts")
    metadata = _metadata_from_dict(data.get("metadata", {}), "catalog metadata")
    return ContractCatalog(
        schema_version=schema_version,
        generated_by=str(data.get("generated_by", "cgoprof")),
        manifest_id=(
            None if data.get("manifest_id") is None else str(data.get("manifest_id"))
        ),
        metadata=metadata,
        contracts=tuple(contract_from_dict(_require_dict(item, "contract")) for item in contracts_data),
    )


def contract_to_dict(contract: APIContract) -> dict[str, Any]:
    return {
        "schema_version": contract.schema_version,
        "api_id": contract.api_id,
        "c_symbol": contract.c_symbol,
        "scope": _scope_to_dict(contract.scope),
        "parameters": [
            _parameter_to_dict(item)
            for item in sorted(contract.parameters, key=lambda parameter: parameter.index)
        ],
        "result": _result_to_dict(contract.result) if contract.result is not None else None,
        "callback": _fact_to_dict(contract.callback),
        "clauses": [_clause_to_dict(item) for item in contract.clauses],
        "diagnostics": list(contract.diagnostics),
        "metadata": dict(sorted(contract.metadata)),
    }


def contract_from_dict(data: Mapping[str, Any]) -> APIContract:
    schema_version = int(data.get("schema_version", 0))
    if schema_version != CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported contract schema version: {schema_version}")
    parameters = tuple(
        _parameter_from_dict(_require_dict(item, "parameter"))
        for item in _require_list(data.get("parameters", []), "parameters")
    )
    result_data = data.get("result")
    result = None if result_data is None else _result_from_dict(_require_dict(result_data, "result"))
    callback_data = data.get("callback")
    callback = (
        ContractFact(Callback.UNKNOWN)
        if callback_data is None
        else _fact_from_dict(_require_dict(callback_data, "callback fact"), Callback)
    )
    clauses = tuple(
        _clause_from_dict(_require_dict(item, "conditional clause"))
        for item in _require_list(data.get("clauses", []), "clauses")
    )
    diagnostics_data = _require_list(data.get("diagnostics", []), "diagnostics")
    return APIContract(
        schema_version=schema_version,
        api_id=str(data.get("api_id", "")),
        c_symbol=str(data.get("c_symbol", "")),
        scope=_scope_from_dict(_require_dict(data.get("scope", {}), "scope")),
        parameters=parameters,
        result=result,
        callback=callback,
        clauses=clauses,
        diagnostics=tuple(str(item) for item in diagnostics_data),
        metadata=_metadata_from_dict(data.get("metadata", {}), "contract metadata"),
    )


def _scope_to_dict(scope: BuildScope) -> dict[str, Any]:
    return {
        "go_package": scope.go_package,
        "goos": scope.goos,
        "goarch": scope.goarch,
        "build_tags": list(scope.build_tags),
        "c_macros_fingerprint": scope.c_macros_fingerprint,
        "library_version": scope.library_version,
        "provider_release_id": scope.provider_release_id,
        "build_id": scope.build_id,
    }


def _scope_from_dict(data: Mapping[str, Any]) -> BuildScope:
    tags = _require_list(data.get("build_tags", []), "build_tags")
    library_version = data.get("library_version")
    provider_release_id = data.get("provider_release_id")
    build_id = data.get("build_id")
    return BuildScope(
        go_package=str(data.get("go_package", "")),
        goos=str(data.get("goos", "")),
        goarch=str(data.get("goarch", "")),
        build_tags=tuple(str(item) for item in tags),
        c_macros_fingerprint=str(data.get("c_macros_fingerprint", "")),
        library_version=None if library_version is None else str(library_version),
        provider_release_id=(
            None if provider_release_id is None else str(provider_release_id)
        ),
        build_id=None if build_id is None else str(build_id),
    )


def _parameter_to_dict(parameter: ParameterContract) -> dict[str, Any]:
    return {
        "index": parameter.index,
        "name": parameter.name,
        "c_type": parameter.c_type,
        "contract": _value_contract_to_dict(parameter.contract),
    }


def _parameter_from_dict(data: Mapping[str, Any]) -> ParameterContract:
    return ParameterContract(
        index=int(data.get("index", -1)),
        name=str(data.get("name", "")),
        c_type=str(data.get("c_type", "")),
        contract=_value_contract_from_dict(
            _require_dict(data.get("contract", {}), "parameter value contract")
        ),
    )


def _result_to_dict(result: ResultContract) -> dict[str, Any]:
    return {"c_type": result.c_type, "contract": _value_contract_to_dict(result.contract)}


def _result_from_dict(data: Mapping[str, Any]) -> ResultContract:
    return ResultContract(
        c_type=str(data.get("c_type", "")),
        contract=_value_contract_from_dict(_require_dict(data.get("contract", {}), "result contract")),
    )


def _value_contract_to_dict(contract: ValueContract) -> dict[str, Any]:
    return {
        "memory_access": _fact_to_dict(contract.memory_access),
        "ownership": _fact_to_dict(contract.ownership),
        "lifetime": _fact_to_dict(contract.lifetime),
        "escape": _fact_to_dict(contract.escape),
        "mutability": _fact_to_dict(contract.mutability),
        "representation": _fact_to_dict(contract.representation),
    }


def _value_contract_from_dict(data: Mapping[str, Any]) -> ValueContract:
    return ValueContract(
        memory_access=_optional_fact(data, "memory_access", MemoryAccess, MemoryAccess.UNKNOWN),
        ownership=_optional_fact(data, "ownership", Ownership, Ownership.UNKNOWN),
        lifetime=_optional_fact(data, "lifetime", Lifetime, Lifetime.UNKNOWN),
        escape=_optional_fact(data, "escape", Escape, Escape.UNKNOWN),
        mutability=_optional_fact(data, "mutability", Mutability, Mutability.UNKNOWN),
        representation=_optional_fact(
            data, "representation", _representation_from_dict, Representation.unknown()
        ),
    )


def _optional_fact(
    data: Mapping[str, Any],
    name: str,
    parser: Callable[[Any], Any],
    unknown_value: Any,
) -> ContractFact[Any]:
    item = data.get(name)
    if item is None:
        return ContractFact(unknown_value)
    return _fact_from_dict(_require_dict(item, f"{name} fact"), parser)


def _fact_to_dict(fact: ContractFact[Any]) -> dict[str, Any]:
    return {
        "value": _encode_value(fact.value),
        "status": fact.status.value,
        "evidence": [_evidence_to_dict(item) for item in fact.evidence],
    }


def _fact_from_dict(
    data: Mapping[str, Any], parser: Callable[[Any], Any]
) -> ContractFact[Any]:
    if "value" not in data:
        raise ValueError("contract fact is missing value")
    evidence_data = _require_list(data.get("evidence", []), "fact evidence")
    return ContractFact(
        value=parser(data["value"]),
        status=FactStatus(str(data.get("status", FactStatus.UNKNOWN.value))),
        evidence=tuple(
            _evidence_from_dict(_require_dict(item, "evidence")) for item in evidence_data
        ),
    )


def _evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "kind": evidence.kind.value,
        "source": evidence.source,
        "detail": evidence.detail,
        "location": evidence.location,
    }


def _evidence_from_dict(data: Mapping[str, Any]) -> Evidence:
    location = data.get("location")
    return Evidence(
        kind=EvidenceKind(str(data.get("kind", ""))),
        source=str(data.get("source", "")),
        detail=str(data.get("detail", "")),
        location=None if location is None else str(location),
    )


def _representation_to_dict(value: Representation) -> dict[str, Any]:
    return {
        "kind": value.kind.value,
        "encoding": value.encoding.value,
        "nul_terminated": value.nul_terminated.value,
        "length_argument": value.length_argument,
        "alignment": value.alignment,
        "element_type": value.element_type,
        "notes": value.notes,
    }


def _representation_from_dict(value: Any) -> Representation:
    data = _require_dict(value, "representation")
    length_argument = data.get("length_argument")
    alignment = data.get("alignment")
    element_type = data.get("element_type")
    return Representation(
        kind=RepresentationKind(str(data.get("kind", RepresentationKind.UNKNOWN.value))),
        encoding=Encoding(str(data.get("encoding", Encoding.UNKNOWN.value))),
        nul_terminated=TriState(str(data.get("nul_terminated", TriState.UNKNOWN.value))),
        length_argument=None if length_argument is None else int(length_argument),
        alignment=None if alignment is None else int(alignment),
        element_type=None if element_type is None else str(element_type),
        notes=str(data.get("notes", "")),
    )


def _encode_value(value: Any) -> Any:
    if isinstance(value, Representation):
        return _representation_to_dict(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _clause_to_dict(clause: ConditionalClause) -> dict[str, Any]:
    return {
        "when": [_condition_to_dict(item) for item in clause.conditions],
        "assign": [_assignment_to_dict(item) for item in clause.assignments],
    }


def _clause_from_dict(data: Mapping[str, Any]) -> ConditionalClause:
    conditions = tuple(
        _condition_from_dict(_require_dict(item, "condition"))
        for item in _require_list(data.get("when", []), "clause conditions")
    )
    assignments = tuple(
        _assignment_from_dict(_require_dict(item, "assignment"))
        for item in _require_list(data.get("assign", []), "clause assignments")
    )
    return ConditionalClause(conditions=conditions, assignments=assignments)


def _condition_to_dict(condition: ArgumentCondition) -> dict[str, Any]:
    data = {"argument": condition.argument, "operator": condition.operator.value}
    if condition.operator not in {ConditionOperator.IS_NULL, ConditionOperator.NOT_NULL}:
        data["value"] = condition.value
    return data


def _condition_from_dict(data: Mapping[str, Any]) -> ArgumentCondition:
    return ArgumentCondition(
        argument=int(data.get("argument", -1)),
        operator=ConditionOperator(str(data.get("operator", ""))),
        value=data.get("value"),
    )


def _assignment_to_dict(assignment: ContractAssignment) -> dict[str, Any]:
    target: dict[str, Any] = {"kind": assignment.target.kind.value}
    if assignment.target.index is not None:
        target["index"] = assignment.target.index
    return {
        "target": target,
        "attribute": assignment.attribute.value,
        "fact": _fact_to_dict(assignment.fact),
    }


def _assignment_from_dict(data: Mapping[str, Any]) -> ContractAssignment:
    target_data = _require_dict(data.get("target", {}), "assignment target")
    target_kind = ContractTargetKind(str(target_data.get("kind", "")))
    target_index = target_data.get("index")
    attribute = ContractAttribute(str(data.get("attribute", "")))
    parser = _parser_for_attribute(attribute)
    return ContractAssignment(
        target=ContractTarget(
            kind=target_kind,
            index=None if target_index is None else int(target_index),
        ),
        attribute=attribute,
        fact=_fact_from_dict(_require_dict(data.get("fact", {}), "assignment fact"), parser),
    )


def _parser_for_attribute(attribute: ContractAttribute) -> Callable[[Any], Any]:
    return {
        ContractAttribute.MEMORY_ACCESS: MemoryAccess,
        ContractAttribute.OWNERSHIP: Ownership,
        ContractAttribute.LIFETIME: Lifetime,
        ContractAttribute.ESCAPE: Escape,
        ContractAttribute.CALLBACK: Callback,
        ContractAttribute.MUTABILITY: Mutability,
        ContractAttribute.REPRESENTATION: _representation_from_dict,
    }[attribute]


def _metadata_from_dict(value: Any, label: str) -> tuple[tuple[str, str], ...]:
    data = _require_dict(value, label)
    return tuple(sorted((str(key), str(item)) for key, item in data.items()))


def _require_dict(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value
