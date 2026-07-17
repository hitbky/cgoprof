from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .conditions import ArgumentCondition, ConditionOperator
from .evidence import Evidence, EvidenceKind, FactStatus
from .identity import (
    IDENTITY_SCHEMA_VERSION,
    make_content_id,
    validate_api_id,
    validate_content_id,
    validate_manifest_id,
    verify_content_id,
)
from .manifest import APIManifest
from .manifest_store import ManifestIndex
from .lattice import merge_facts
from .merge import ContractMergeResult, merge_contracts
from .model import (
    APIContract,
    BuildScope,
    Callback,
    ConditionalClause,
    ContractAssignment,
    ContractAttribute,
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


ANNOTATION_SCHEMA_VERSION = 1


class AnnotationTrust(str, Enum):
    UNTRUSTED = "untrusted"
    REVIEWED = "reviewed"
    TRUSTED = "trusted"


_TRUST_RANK = {
    AnnotationTrust.UNTRUSTED: 0,
    AnnotationTrust.REVIEWED: 1,
    AnnotationTrust.TRUSTED: 2,
}


@dataclass(frozen=True)
class AnnotationScope:
    """An annotation is valid for exactly one API in one build and release."""

    manifest_id: str
    build_id: str
    provider_release_id: str
    api_id: str
    go_package: str

    def __post_init__(self) -> None:
        validate_manifest_id(self.manifest_id)
        validate_content_id(
            self.build_id, expected_kind="cgobuild", expected_version=1
        )
        validate_content_id(
            self.provider_release_id,
            expected_kind="cgorelease",
            expected_version=1,
        )
        validate_api_id(self.api_id)
        if not self.go_package.strip():
            raise ValueError("annotation Go package must not be blank")

    def payload(self) -> dict[str, Any]:
        return {
            "api_id": self.api_id,
            "build_id": self.build_id,
            "go_package": self.go_package,
            "manifest_id": self.manifest_id,
            "provider_release_id": self.provider_release_id,
        }


@dataclass(frozen=True)
class AnnotationProvenance:
    author: str
    source: str
    revision: str
    trust: AnnotationTrust = AnnotationTrust.UNTRUSTED
    organization: str | None = None
    reviewed_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("author", self.author),
            ("source", self.source),
            ("revision", self.revision),
        ):
            if not value.strip():
                raise ValueError(f"annotation {label} must not be blank")
        parsed = urlparse(self.source)
        if not parsed.scheme and (
            self.source.startswith(("/", "\\"))
            or "\x00" in self.source
            or (len(self.source) >= 2 and self.source[1] == ":")
        ):
            raise ValueError(
                "annotation source must be a URI or a non-absolute documentation path"
            )
        reviewers = tuple(sorted(set(item.strip() for item in self.reviewed_by)))
        if any(not item for item in reviewers):
            raise ValueError("annotation reviewer names must not be blank")
        if len(reviewers) != len(self.reviewed_by):
            raise ValueError("annotation reviewer names must be unique")
        if self.trust in {AnnotationTrust.REVIEWED, AnnotationTrust.TRUSTED} and not reviewers:
            raise ValueError("reviewed/trusted annotations require at least one reviewer")
        if self.organization is not None and not self.organization.strip():
            raise ValueError("annotation organization must not be blank")
        object.__setattr__(self, "reviewed_by", reviewers)

    def payload(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "organization": self.organization,
            "reviewed_by": list(self.reviewed_by),
            "revision": self.revision,
            "source": self.source,
            "trust": self.trust.value,
        }


AnnotationValue = (
    MemoryAccess
    | Ownership
    | Lifetime
    | Escape
    | Callback
    | Mutability
    | Representation
)


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
class AnnotationAssignment:
    target: ContractTarget
    attribute: ContractAttribute
    value: AnnotationValue
    justification: str

    def __post_init__(self) -> None:
        if not self.justification.strip():
            raise ValueError("annotation assignments require a justification")
        expected = _ATTRIBUTE_TYPES[self.attribute]
        if not isinstance(self.value, expected):
            raise TypeError(
                f"{self.attribute.value} annotation requires {expected.__name__}"
            )
        if _semantically_unknown(self.value):
            raise ValueError("annotations must not assert an unknown value")
        if self.attribute == ContractAttribute.CALLBACK:
            if self.target.kind != ContractTargetKind.FUNCTION:
                raise ValueError("callback annotations must target the function")
        elif self.target.kind == ContractTargetKind.FUNCTION:
            raise ValueError("only callback annotations may target the function")

    def payload(self) -> dict[str, Any]:
        target: dict[str, Any] = {"kind": self.target.kind.value}
        if self.target.index is not None:
            target["index"] = self.target.index
        return {
            "attribute": self.attribute.value,
            "justification": self.justification,
            "target": target,
            "value": _value_to_data(self.value),
        }


@dataclass(frozen=True)
class AnnotationClause:
    conditions: tuple[ArgumentCondition, ...]
    assignments: tuple[AnnotationAssignment, ...]

    def __post_init__(self) -> None:
        if not self.conditions or not self.assignments:
            raise ValueError("annotation clauses require conditions and assignments")

    def payload(self) -> dict[str, Any]:
        return {
            "assign": [item.payload() for item in self.assignments],
            "when": [_condition_to_data(item) for item in self.conditions],
        }


@dataclass(frozen=True)
class ContractAnnotation:
    scope: AnnotationScope
    provenance: AnnotationProvenance
    assignments: tuple[AnnotationAssignment, ...] = ()
    clauses: tuple[AnnotationClause, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    schema_version: int = ANNOTATION_SCHEMA_VERSION
    _annotation_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != ANNOTATION_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported annotation schema version: {self.schema_version}"
            )
        if not self.assignments and not self.clauses:
            raise ValueError("an annotation must contain at least one fact or clause")
        metadata = tuple(sorted(self.metadata))
        if len(metadata) != len({key for key, _ in metadata}):
            raise ValueError("annotation metadata keys must be unique")
        if any(not key.strip() for key, _ in metadata):
            raise ValueError("annotation metadata keys must not be blank")
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(
            self,
            "_annotation_id",
            make_content_id(
                "cgoannotation", IDENTITY_SCHEMA_VERSION, self.identity_payload()
            ),
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "assignments": [item.payload() for item in self.assignments],
            "clauses": [item.payload() for item in self.clauses],
            "metadata": dict(self.metadata),
            "provenance": self.provenance.payload(),
            "schema_version": self.schema_version,
            "scope": self.scope.payload(),
        }

    @property
    def annotation_id(self) -> str:
        return self._annotation_id


@dataclass(frozen=True)
class AnnotationBundle:
    annotations: tuple[ContractAnnotation, ...]
    generated_by: str = "cgoprof annotation"
    schema_version: int = ANNOTATION_SCHEMA_VERSION
    _bundle_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != ANNOTATION_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported annotation bundle schema: {self.schema_version}"
            )
        if not self.generated_by.strip():
            raise ValueError("annotation bundle generator must not be blank")
        annotations = tuple(sorted(self.annotations, key=lambda item: item.annotation_id))
        if len(annotations) != len({item.annotation_id for item in annotations}):
            raise ValueError("annotation bundle contains duplicate annotation ids")
        object.__setattr__(self, "annotations", annotations)
        object.__setattr__(
            self,
            "_bundle_id",
            make_content_id(
                "cgoannotations",
                IDENTITY_SCHEMA_VERSION,
                {
                    "annotation_ids": [item.annotation_id for item in annotations],
                    "generated_by": self.generated_by,
                    "schema_version": self.schema_version,
                },
            ),
        )

    @property
    def bundle_id(self) -> str:
        return self._bundle_id


@dataclass(frozen=True)
class AnnotationPolicy:
    minimum_trust: AnnotationTrust = AnnotationTrust.UNTRUSTED


def apply_annotation(
    annotation: ContractAnnotation,
    manifest: APIManifest,
    base: APIContract | None = None,
    *,
    policy: AnnotationPolicy | None = None,
) -> ContractMergeResult:
    """Validate, materialize, and conservatively merge one annotation."""

    policy = policy or AnnotationPolicy()
    if _TRUST_RANK[annotation.provenance.trust] < _TRUST_RANK[policy.minimum_trust]:
        raise PermissionError(
            f"annotation trust {annotation.provenance.trust.value} is below required "
            f"{policy.minimum_trust.value}"
        )
    api, scope = _validate_scope(annotation.scope, manifest)
    skeleton = _contract_skeleton(manifest, annotation.scope.go_package, api.api_id)
    contribution = _annotation_contribution(annotation, skeleton, scope)
    if base is None:
        return ContractMergeResult(contribution, contribution.diagnostics)
    return merge_contracts(base, contribution)


def apply_annotation_bundle(
    bundle: AnnotationBundle,
    manifest: APIManifest,
    contracts: Mapping[str, APIContract] | None = None,
    *,
    go_package: str,
    policy: AnnotationPolicy | None = None,
) -> tuple[APIContract, ...]:
    result = dict(contracts or {})
    for annotation in bundle.annotations:
        if annotation.scope.manifest_id != manifest.manifest_id:
            continue
        if annotation.scope.go_package != go_package:
            continue
        merged = apply_annotation(
            annotation,
            manifest,
            result.get(annotation.scope.api_id),
            policy=policy,
        )
        result[annotation.scope.api_id] = merged.contract
    return tuple(result[key] for key in sorted(result))


def dumps_annotation_bundle(bundle: AnnotationBundle, *, indent: int = 2) -> str:
    return json.dumps(_bundle_to_data(bundle), indent=indent, sort_keys=True) + "\n"


def loads_annotation_bundle(text: str) -> AnnotationBundle:
    data = json.loads(text, object_pairs_hook=_strict_object)
    return _bundle_from_data(_require_object(data, "annotation bundle"))


def dump_annotation_bundle(bundle: AnnotationBundle, path: str | Path) -> None:
    Path(path).write_text(dumps_annotation_bundle(bundle), encoding="utf-8")


def load_annotation_bundle(path: str | Path) -> AnnotationBundle:
    return loads_annotation_bundle(Path(path).read_text(encoding="utf-8"))


def _validate_scope(scope: AnnotationScope, manifest: APIManifest) -> tuple[Any, BuildScope]:
    if scope.manifest_id != manifest.manifest_id:
        raise ValueError("annotation manifest_id does not match the loaded manifest")
    if scope.build_id != manifest.build.build_id:
        raise ValueError("annotation build_id does not match the loaded manifest")
    index = ManifestIndex(manifest)
    api = index.require_api(scope.api_id)
    package = next(
        (
            item
            for item in manifest.packages
            if item.identity.import_path == scope.go_package
        ),
        None,
    )
    if package is None:
        raise ValueError("annotation names a Go package absent from the manifest")
    package_bindings = [
        item
        for item in manifest.bindings
        if item.package_id == package.package_id and item.api_id == api.api_id
    ]
    if not package_bindings:
        raise ValueError("annotation package binding resolves to a different API")
    provider = next(
        item
        for item in manifest.providers
        if item.provider_id == api.identity.provider.provider_id
    )
    if scope.provider_release_id != provider.release_id:
        raise ValueError("annotation provider release does not match the manifest")
    build = manifest.build
    return api, BuildScope(
        go_package=scope.go_package,
        goos=build.goos,
        goarch=build.goarch,
        build_tags=build.build_tags,
        c_macros_fingerprint=build.macros_fingerprint,
        library_version=provider.version,
        provider_release_id=provider.release_id,
        build_id=build.build_id,
    )


def _contract_skeleton(
    manifest: APIManifest,
    go_package: str,
    api_id: str,
) -> APIContract:
    api = ManifestIndex(manifest).require_api(api_id)
    _, scope = _validate_scope(
        AnnotationScope(
            manifest.manifest_id,
            manifest.build.build_id,
            next(
                provider.release_id
                for provider in manifest.providers
                if provider.provider_id == api.identity.provider.provider_id
            ),
            api_id,
            go_package,
        ),
        manifest,
    )
    return APIContract(
        api_id=api_id,
        c_symbol=api.identity.symbol,
        scope=scope,
        parameters=tuple(
            ParameterContract(index, f"arg{index}", item.canonical)
            for index, item in enumerate(api.identity.signature.parameters)
        ),
        result=ResultContract(api.identity.signature.result.canonical),
    )


def _annotation_contribution(
    annotation: ContractAnnotation,
    skeleton: APIContract,
    scope: BuildScope,
) -> APIContract:
    values = [parameter.contract for parameter in skeleton.parameters]
    result_value = skeleton.result.contract if skeleton.result is not None else None
    callback: ContractFact[Callback] = ContractFact(Callback.UNKNOWN)
    for assignment in annotation.assignments:
        _validate_target(assignment.target, skeleton)
        fact = _annotation_fact(annotation, assignment)
        if assignment.target.kind == ContractTargetKind.PARAMETER:
            assert assignment.target.index is not None
            values[assignment.target.index] = _set_value_fact(
                values[assignment.target.index], assignment.attribute, fact
            )
        elif assignment.target.kind == ContractTargetKind.RESULT:
            if result_value is None:
                raise ValueError("annotation targets a missing result")
            result_value = _set_value_fact(result_value, assignment.attribute, fact)
        else:
            if assignment.attribute != ContractAttribute.CALLBACK:
                raise ValueError("only callback may target the function")
            outcome = merge_facts(ContractAttribute.CALLBACK, callback, fact)
            callback = outcome.fact  # type: ignore[assignment]
    clauses = tuple(
        ConditionalClause(
            conditions=item.conditions,
            assignments=tuple(
                _contract_assignment(annotation, assignment)
                for assignment in item.assignments
            ),
        )
        for item in annotation.clauses
    )
    for clause in annotation.clauses:
        for condition in clause.conditions:
            if condition.argument >= len(skeleton.parameters):
                raise ValueError(
                    f"annotation condition references unknown parameter {condition.argument}"
                )
        for assignment in clause.assignments:
            _validate_target(assignment.target, skeleton)
    return APIContract(
        api_id=skeleton.api_id,
        c_symbol=skeleton.c_symbol,
        scope=scope,
        parameters=tuple(
            replace(parameter, contract=values[parameter.index])
            for parameter in skeleton.parameters
        ),
        result=(
            None
            if skeleton.result is None or result_value is None
            else replace(skeleton.result, contract=result_value)
        ),
        callback=callback,
        clauses=clauses,
        metadata=(
            ("annotation_id", annotation.annotation_id),
            ("annotation_trust", annotation.provenance.trust.value),
        ),
    )


def _annotation_fact(
    annotation: ContractAnnotation,
    assignment: AnnotationAssignment,
) -> ContractFact[Any]:
    provenance = annotation.provenance
    evidence = Evidence(
        kind=EvidenceKind.USER_ANNOTATION,
        source=f"{provenance.author}: {provenance.source}@{provenance.revision}",
        detail=(
            f"{assignment.justification}; trust={provenance.trust.value}; "
            f"annotation_id={annotation.annotation_id}"
        ),
    )
    # User material can only declare a fact.  It cannot manufacture a static
    # proof or intrinsic status, irrespective of trust policy.
    return ContractFact(assignment.value, FactStatus.DECLARED, (evidence,))


def _contract_assignment(
    annotation: ContractAnnotation,
    assignment: AnnotationAssignment,
) -> ContractAssignment:
    return ContractAssignment(
        assignment.target,
        assignment.attribute,
        _annotation_fact(annotation, assignment),
    )


def _validate_target(target: ContractTarget, skeleton: APIContract) -> None:
    if target.kind == ContractTargetKind.PARAMETER:
        assert target.index is not None
        if target.index >= len(skeleton.parameters):
            raise ValueError(f"annotation targets unknown parameter {target.index}")
    elif target.kind == ContractTargetKind.RESULT:
        if skeleton.result is None or skeleton.result.c_type in {"c:void", "void"}:
            raise ValueError("annotation cannot assign facts to a void result")


def _set_value_fact(
    value: ValueContract,
    attribute: ContractAttribute,
    fact: ContractFact[Any],
) -> ValueContract:
    if attribute == ContractAttribute.CALLBACK:
        raise ValueError("callback is not a value-level attribute")
    outcome = merge_facts(attribute, getattr(value, attribute.value), fact)
    return replace(value, **{attribute.value: outcome.fact})


def _bundle_to_data(bundle: AnnotationBundle) -> dict[str, Any]:
    return {
        "annotations": [
            {"annotation_id": item.annotation_id, **item.identity_payload()}
            for item in bundle.annotations
        ],
        "bundle_id": bundle.bundle_id,
        "generated_by": bundle.generated_by,
        "schema_version": bundle.schema_version,
    }


def _bundle_from_data(data: Mapping[str, Any]) -> AnnotationBundle:
    _fields(
        data,
        required={"annotations", "bundle_id", "generated_by", "schema_version"},
        context="annotation bundle",
    )
    schema = _integer(data["schema_version"], "annotation bundle schema_version")
    if schema != ANNOTATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported annotation bundle schema: {schema}")
    annotations = tuple(
        _annotation_from_data(_require_object(item, "annotation"))
        for item in _require_list(data["annotations"], "annotations")
    )
    bundle = AnnotationBundle(
        annotations,
        generated_by=_string(data["generated_by"], "generated_by"),
        schema_version=schema,
    )
    verify_content_id(
        _string(data["bundle_id"], "bundle_id"),
        kind="cgoannotations",
        version=IDENTITY_SCHEMA_VERSION,
        payload={
            "annotation_ids": [item.annotation_id for item in bundle.annotations],
            "generated_by": bundle.generated_by,
            "schema_version": bundle.schema_version,
        },
    )
    return bundle


def _annotation_from_data(data: Mapping[str, Any]) -> ContractAnnotation:
    _fields(
        data,
        required={
            "annotation_id",
            "assignments",
            "clauses",
            "metadata",
            "provenance",
            "schema_version",
            "scope",
        },
        context="annotation",
    )
    scope_data = _require_object(data["scope"], "annotation scope")
    _fields(
        scope_data,
        required={
            "api_id",
            "build_id",
            "go_package",
            "manifest_id",
            "provider_release_id",
        },
        context="annotation scope",
    )
    provenance_data = _require_object(data["provenance"], "annotation provenance")
    _fields(
        provenance_data,
        required={"author", "reviewed_by", "revision", "source", "trust"},
        optional={"organization"},
        context="annotation provenance",
    )
    annotation = ContractAnnotation(
        scope=AnnotationScope(
            manifest_id=_string(scope_data["manifest_id"], "manifest_id"),
            build_id=_string(scope_data["build_id"], "build_id"),
            provider_release_id=_string(
                scope_data["provider_release_id"], "provider_release_id"
            ),
            api_id=_string(scope_data["api_id"], "api_id"),
            go_package=_string(scope_data["go_package"], "go_package"),
        ),
        provenance=AnnotationProvenance(
            author=_string(provenance_data["author"], "author"),
            source=_string(provenance_data["source"], "source"),
            revision=_string(provenance_data["revision"], "revision"),
            trust=AnnotationTrust(_string(provenance_data["trust"], "trust")),
            organization=(
                None
                if provenance_data.get("organization") is None
                else _string(provenance_data["organization"], "organization")
            ),
            reviewed_by=tuple(
                _string(item, "reviewed_by item")
                for item in _require_list(provenance_data["reviewed_by"], "reviewed_by")
            ),
        ),
        assignments=tuple(
            _assignment_from_data(_require_object(item, "annotation assignment"))
            for item in _require_list(data["assignments"], "assignments")
        ),
        clauses=tuple(
            _clause_from_data(_require_object(item, "annotation clause"))
            for item in _require_list(data["clauses"], "clauses")
        ),
        metadata=_metadata(data["metadata"]),
        schema_version=_integer(data["schema_version"], "annotation schema_version"),
    )
    verify_content_id(
        _string(data["annotation_id"], "annotation_id"),
        kind="cgoannotation",
        version=IDENTITY_SCHEMA_VERSION,
        payload=annotation.identity_payload(),
    )
    return annotation


def _assignment_from_data(data: Mapping[str, Any]) -> AnnotationAssignment:
    _fields(
        data,
        required={"attribute", "justification", "target", "value"},
        context="annotation assignment",
    )
    target_data = _require_object(data["target"], "annotation target")
    kind = ContractTargetKind(_string(target_data.get("kind"), "target kind"))
    if kind == ContractTargetKind.PARAMETER:
        _fields(target_data, required={"kind", "index"}, context="parameter target")
        target = ContractTarget(kind, _integer(target_data["index"], "target index"))
    else:
        _fields(target_data, required={"kind"}, context="annotation target")
        target = ContractTarget(kind)
    attribute = ContractAttribute(_string(data["attribute"], "attribute"))
    return AnnotationAssignment(
        target=target,
        attribute=attribute,
        value=_value_from_data(attribute, data["value"]),
        justification=_string(data["justification"], "justification"),
    )


def _clause_from_data(data: Mapping[str, Any]) -> AnnotationClause:
    _fields(data, required={"assign", "when"}, context="annotation clause")
    return AnnotationClause(
        conditions=tuple(
            _condition_from_data(_require_object(item, "condition"))
            for item in _require_list(data["when"], "clause conditions")
        ),
        assignments=tuple(
            _assignment_from_data(_require_object(item, "assignment"))
            for item in _require_list(data["assign"], "clause assignments")
        ),
    )


def _condition_to_data(condition: ArgumentCondition) -> dict[str, Any]:
    result: dict[str, Any] = {
        "argument": condition.argument,
        "operator": condition.operator.value,
    }
    if condition.operator not in {ConditionOperator.IS_NULL, ConditionOperator.NOT_NULL}:
        result["value"] = condition.value
    return result


def _condition_from_data(data: Mapping[str, Any]) -> ArgumentCondition:
    operator = ConditionOperator(_string(data.get("operator"), "condition operator"))
    required = {"argument", "operator"}
    optional = set()
    if operator not in {ConditionOperator.IS_NULL, ConditionOperator.NOT_NULL}:
        required.add("value")
    _fields(data, required=required, optional=optional, context="annotation condition")
    return ArgumentCondition(
        _integer(data["argument"], "condition argument"),
        operator,
        data.get("value"),
    )


def _value_to_data(value: AnnotationValue) -> Any:
    if isinstance(value, Representation):
        return {
            "alignment": value.alignment,
            "element_type": value.element_type,
            "encoding": value.encoding.value,
            "kind": value.kind.value,
            "length_argument": value.length_argument,
            "notes": value.notes,
            "nul_terminated": value.nul_terminated.value,
        }
    return value.value


def _value_from_data(attribute: ContractAttribute, data: Any) -> AnnotationValue:
    if attribute == ContractAttribute.REPRESENTATION:
        value = _require_object(data, "representation")
        _fields(
            value,
            required={
                "alignment",
                "element_type",
                "encoding",
                "kind",
                "length_argument",
                "notes",
                "nul_terminated",
            },
            context="representation",
        )
        return Representation(
            kind=RepresentationKind(_string(value["kind"], "representation kind")),
            encoding=Encoding(_string(value["encoding"], "representation encoding")),
            nul_terminated=TriState(
                _string(value["nul_terminated"], "representation nul_terminated")
            ),
            length_argument=_optional_integer(value["length_argument"], "length_argument"),
            alignment=_optional_integer(value["alignment"], "alignment"),
            element_type=_optional_string(value["element_type"], "element_type"),
            notes=_string(value["notes"], "representation notes"),
        )
    enum_type = _ATTRIBUTE_TYPES[attribute]
    return enum_type(_string(data, f"{attribute.value} value"))


def _semantically_unknown(value: AnnotationValue) -> bool:
    if isinstance(value, Representation):
        return value == Representation.unknown()
    return value.value == "unknown"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _fields(
    data: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    context: str,
) -> None:
    optional = optional or set()
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing:
        raise ValueError(f"{context} is missing fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{context} has unknown fields: {sorted(unknown)}")


def _metadata(value: Any) -> tuple[tuple[str, str], ...]:
    data = _require_object(value, "annotation metadata")
    return tuple(sorted((_string(key, "metadata key"), _string(item, "metadata value")) for key, item in data.items()))


def _require_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _optional_integer(value: Any, label: str) -> int | None:
    return None if value is None else _integer(value, label)
