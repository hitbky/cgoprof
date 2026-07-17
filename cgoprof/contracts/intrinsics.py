from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .evidence import Evidence, EvidenceKind, FactStatus
from .identity import (
    APIIdentity,
    APIKind,
    CFunctionSignature,
    CTypeIdentity,
    ProviderIdentity,
    ProviderKind,
)
from .manifest import APIManifest, ManifestAPI, ProviderRecord
from .manifest_store import ManifestIndex
from .model import (
    APIContract,
    BuildScope,
    Callback,
    ContractFact,
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


CGO_INTRINSIC_ABI = "cgo-pseudo-v1"
CGO_INTRINSIC_NAMESPACE = "go.dev/cgo"


@dataclass(frozen=True)
class IntrinsicDefinition:
    symbol: str
    result_type: str
    parameter_types: tuple[str, ...]
    parameter_names: tuple[str, ...]
    contract_factory: Callable[[Evidence], tuple[tuple[ValueContract, ...], ValueContract]]

    def __post_init__(self) -> None:
        if len(self.parameter_types) != len(self.parameter_names):
            raise ValueError("intrinsic parameter types and names must have equal length")


_TYPES = {
    "go_string": "go:string",
    "go_bytes": "go:[]byte",
    "c_char_ptr": "c:char*",
    "c_const_char_ptr": "c:const char*",
    "c_void_ptr": "c:void*",
    "c_int": "c:int",
}


def intrinsic_provider_identity() -> ProviderIdentity:
    return ProviderIdentity(
        kind=ProviderKind.CGO_INTRINSIC,
        namespace=CGO_INTRINSIC_NAMESPACE,
        name="cgo",
    )


def intrinsic_provider_record(go_version: str) -> ProviderRecord:
    return ProviderRecord(
        identity=intrinsic_provider_identity(),
        version=go_version,
        abi_version=CGO_INTRINSIC_ABI,
    )


def intrinsic_identity(
    symbol: str,
    provider: ProviderIdentity | None = None,
) -> APIIdentity | None:
    definition = _DEFINITIONS.get(symbol)
    if definition is None:
        return None
    provider = provider or intrinsic_provider_identity()
    return APIIdentity(
        provider=provider,
        symbol=symbol,
        kind=APIKind.CGO_INTRINSIC,
        signature=CFunctionSignature(
            result=CTypeIdentity(_TYPES[definition.result_type]),
            parameters=tuple(
                CTypeIdentity(_TYPES[item]) for item in definition.parameter_types
            ),
            abi_tag=CGO_INTRINSIC_ABI,
        ),
    )


def intrinsic_manifest_api(
    symbol: str,
    provider: ProviderIdentity | None = None,
) -> ManifestAPI | None:
    identity = intrinsic_identity(symbol, provider)
    if identity is None:
        return None
    return ManifestAPI(identity=identity, declarations=())


def intrinsic_contract(
    manifest: APIManifest,
    api_id: str,
    go_package: str,
) -> APIContract:
    """Instantiate built-in semantics for an exact manifest/package binding."""

    index = ManifestIndex(manifest)
    api = index.require_api(api_id)
    if api.identity.kind != APIKind.CGO_INTRINSIC:
        raise ValueError(f"API {api_id} is not a cgo intrinsic")
    definition = _DEFINITIONS.get(api.identity.symbol)
    if definition is None:
        raise ValueError(f"unsupported cgo intrinsic {api.identity.symbol!r}")
    expected = intrinsic_identity(api.identity.symbol, api.identity.provider)
    if expected is None or expected.api_id != api_id:
        raise ValueError("manifest cgo intrinsic signature does not match the built-in ABI")
    binding = index.resolve_binding(go_package, api.identity.symbol)
    bound_api, _ = binding.require_exact()
    if bound_api.api_id != api_id:
        raise ValueError("Go package binds the intrinsic name to a different API")
    provider = next(
        item
        for item in manifest.providers
        if item.provider_id == api.identity.provider.provider_id
    )
    evidence = Evidence(
        kind=EvidenceKind.CGO_INTRINSIC,
        source=f"Go cgo intrinsic semantics ({provider.version or 'unknown Go release'})",
        detail=f"built-in {CGO_INTRINSIC_ABI} contract for C.{api.identity.symbol}",
    )
    parameter_values, result_value = definition.contract_factory(evidence)
    signature = api.identity.signature
    return APIContract(
        api_id=api_id,
        c_symbol=api.identity.symbol,
        scope=_scope(manifest, provider, go_package),
        parameters=tuple(
            ParameterContract(
                index,
                definition.parameter_names[index],
                c_type.canonical,
                parameter_values[index],
            )
            for index, c_type in enumerate(signature.parameters)
        ),
        result=ResultContract(signature.result.canonical, result_value),
        callback=_fact(Callback.NO_CALLBACK, evidence),
        metadata=(
            ("contract_source", "cgo_intrinsic"),
            ("intrinsic_abi", CGO_INTRINSIC_ABI),
        ),
    )


def intrinsic_contracts_for_package(
    manifest: APIManifest,
    go_package: str,
) -> tuple[APIContract, ...]:
    package = next(
        (item for item in manifest.packages if item.identity.import_path == go_package),
        None,
    )
    if package is None:
        raise KeyError(f"unknown Go package {go_package!r}")
    api_ids = {
        binding.api_id
        for binding in manifest.bindings
        if binding.package_id == package.package_id
        and ManifestIndex(manifest).require_api(binding.api_id).identity.kind
        == APIKind.CGO_INTRINSIC
    }
    return tuple(
        intrinsic_contract(manifest, api_id, go_package)
        for api_id in sorted(api_ids)
    )


def _scope(
    manifest: APIManifest,
    provider: ProviderRecord,
    go_package: str,
) -> BuildScope:
    build = manifest.build
    return BuildScope(
        go_package=go_package,
        goos=build.goos,
        goarch=build.goarch,
        build_tags=build.build_tags,
        c_macros_fingerprint=build.macros_fingerprint,
        library_version=provider.version,
        provider_release_id=provider.release_id,
        build_id=build.build_id,
    )


def _fact(value: object, evidence: Evidence) -> ContractFact[object]:
    return ContractFact(value, FactStatus.INTRINSIC, (evidence,))


def _value(
    evidence: Evidence,
    *,
    memory_access: MemoryAccess,
    ownership: Ownership,
    lifetime: Lifetime,
    escape: Escape,
    mutability: Mutability,
    representation: Representation,
) -> ValueContract:
    return ValueContract(
        memory_access=_fact(memory_access, evidence),  # type: ignore[arg-type]
        ownership=_fact(ownership, evidence),  # type: ignore[arg-type]
        lifetime=_fact(lifetime, evidence),  # type: ignore[arg-type]
        escape=_fact(escape, evidence),  # type: ignore[arg-type]
        mutability=_fact(mutability, evidence),  # type: ignore[arg-type]
        representation=_fact(representation, evidence),  # type: ignore[arg-type]
    )


def _cstring_contract(evidence: Evidence) -> tuple[tuple[ValueContract, ...], ValueContract]:
    source = _value(
        evidence,
        memory_access=MemoryAccess.READ,
        ownership=Ownership.BORROWED,
        lifetime=Lifetime.CALL_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.STABLE,
        representation=Representation(
            kind=RepresentationKind.GO_STRING,
            encoding=Encoding.UTF8,
            nul_terminated=TriState.NO,
        ),
    )
    result = _value(
        evidence,
        memory_access=MemoryAccess.NONE,
        ownership=Ownership.CALLER_OWNED,
        lifetime=Lifetime.UNTIL_EXPLICIT_FREE,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.MAY_MUTATE,
        representation=Representation(
            kind=RepresentationKind.C_STRING,
            encoding=Encoding.UTF8,
            nul_terminated=TriState.YES,
            element_type="char",
            notes="new C allocation; caller releases it with C.free",
        ),
    )
    return (source,), result


def _cbytes_contract(evidence: Evidence) -> tuple[tuple[ValueContract, ...], ValueContract]:
    source = _value(
        evidence,
        memory_access=MemoryAccess.READ,
        ownership=Ownership.BORROWED,
        lifetime=Lifetime.CALL_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.STABLE,
        representation=Representation(
            kind=RepresentationKind.GO_SLICE,
            encoding=Encoding.BYTES,
            nul_terminated=TriState.NO,
            element_type="byte",
        ),
    )
    result = _value(
        evidence,
        memory_access=MemoryAccess.NONE,
        ownership=Ownership.CALLER_OWNED,
        lifetime=Lifetime.UNTIL_EXPLICIT_FREE,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.MAY_MUTATE,
        representation=Representation(
            kind=RepresentationKind.RAW_BYTES,
            encoding=Encoding.BYTES,
            nul_terminated=TriState.NO,
            element_type="byte",
            notes="new C allocation with the same byte length as the input",
        ),
    )
    return (source,), result


def _gostring_contract(
    evidence: Evidence,
) -> tuple[tuple[ValueContract, ...], ValueContract]:
    source = _value(
        evidence,
        memory_access=MemoryAccess.READ,
        ownership=Ownership.BORROWED,
        lifetime=Lifetime.CALL_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.EXTERNALLY_MUTABLE,
        representation=Representation(
            kind=RepresentationKind.C_STRING,
            encoding=Encoding.UTF8,
            nul_terminated=TriState.YES,
            element_type="char",
        ),
    )
    return (source,), _go_copy_result(evidence)


def _gostringn_contract(
    evidence: Evidence,
) -> tuple[tuple[ValueContract, ...], ValueContract]:
    source = _value(
        evidence,
        memory_access=MemoryAccess.READ,
        ownership=Ownership.BORROWED,
        lifetime=Lifetime.CALL_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.EXTERNALLY_MUTABLE,
        representation=Representation(
            kind=RepresentationKind.POINTER_LENGTH,
            encoding=Encoding.UTF8,
            nul_terminated=TriState.NO,
            length_argument=1,
            element_type="char",
        ),
    )
    length = _scalar(evidence)
    return (source, length), _go_copy_result(evidence)


def _gobytes_contract(
    evidence: Evidence,
) -> tuple[tuple[ValueContract, ...], ValueContract]:
    source = _value(
        evidence,
        memory_access=MemoryAccess.READ,
        ownership=Ownership.BORROWED,
        lifetime=Lifetime.CALL_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.EXTERNALLY_MUTABLE,
        representation=Representation(
            kind=RepresentationKind.POINTER_LENGTH,
            encoding=Encoding.BYTES,
            nul_terminated=TriState.NO,
            length_argument=1,
            element_type="byte",
        ),
    )
    result = _value(
        evidence,
        memory_access=MemoryAccess.NONE,
        ownership=Ownership.CALLER_OWNED,
        lifetime=Lifetime.OWNER_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.MAY_MUTATE,
        representation=Representation(
            kind=RepresentationKind.GO_SLICE,
            encoding=Encoding.BYTES,
            nul_terminated=TriState.NO,
            element_type="byte",
            notes="independent Go-owned copy",
        ),
    )
    return (source, _scalar(evidence)), result


def _scalar(evidence: Evidence) -> ValueContract:
    return _value(
        evidence,
        memory_access=MemoryAccess.NONE,
        ownership=Ownership.BORROWED,
        lifetime=Lifetime.CALL_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.STABLE,
        representation=Representation(kind=RepresentationKind.SCALAR),
    )


def _go_copy_result(evidence: Evidence) -> ValueContract:
    return _value(
        evidence,
        memory_access=MemoryAccess.NONE,
        ownership=Ownership.CALLER_OWNED,
        lifetime=Lifetime.OWNER_SCOPED,
        escape=Escape.NO_ESCAPE,
        mutability=Mutability.STABLE,
        representation=Representation(
            kind=RepresentationKind.GO_STRING,
            encoding=Encoding.UTF8,
            nul_terminated=TriState.NO,
            notes="independent Go-owned copy",
        ),
    )


_DEFINITIONS: dict[str, IntrinsicDefinition] = {
    "CString": IntrinsicDefinition(
        "CString", "c_char_ptr", ("go_string",), ("value",), _cstring_contract
    ),
    "CBytes": IntrinsicDefinition(
        "CBytes", "c_void_ptr", ("go_bytes",), ("value",), _cbytes_contract
    ),
    "GoString": IntrinsicDefinition(
        "GoString",
        "go_string",
        ("c_const_char_ptr",),
        ("value",),
        _gostring_contract,
    ),
    "GoStringN": IntrinsicDefinition(
        "GoStringN",
        "go_string",
        ("c_const_char_ptr", "c_int"),
        ("value", "length"),
        _gostringn_contract,
    ),
    "GoBytes": IntrinsicDefinition(
        "GoBytes",
        "go_bytes",
        ("c_void_ptr", "c_int"),
        ("value", "length"),
        _gobytes_contract,
    ),
}


INTRINSIC_SYMBOLS = tuple(sorted(_DEFINITIONS))
