from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TypeVar

from .identity import (
    APIIdentity,
    APIKind,
    BuildContext,
    CallingConvention,
    CFunctionSignature,
    CTypeIdentity,
    Endianness,
    GoPackageIdentity,
    Linkage,
    MacroDefinition,
    ProviderIdentity,
    ProviderKind,
    TargetABI,
    ToolchainIdentity,
    verify_content_id,
)
from .manifest import (
    APIBinding,
    APIDeclaration,
    APIManifest,
    BindingKind,
    CgoDirective,
    DiagnosticSeverity,
    GoPackageRecord,
    ManifestAPI,
    ManifestCompleteness,
    ManifestDiagnostic,
    ProviderArtifact,
    ProviderRecord,
    SourceLocation,
    UnresolvedBinding,
    UnresolvedReason,
)


E = TypeVar("E", bound=Enum)


def dumps_manifest(manifest: APIManifest, *, indent: int = 2) -> str:
    return json.dumps(
        manifest_to_dict(manifest),
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
    ) + "\n"


def loads_manifest(text: str) -> APIManifest:
    try:
        data = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid manifest JSON: {error}") from error
    return manifest_from_dict(_object(data, "manifest"))


def dump_manifest(manifest: APIManifest, path: str | Path) -> None:
    Path(path).write_text(dumps_manifest(manifest), encoding="utf-8")


def load_manifest(path: str | Path) -> APIManifest:
    return loads_manifest(Path(path).read_text(encoding="utf-8"))


def manifest_to_dict(manifest: APIManifest) -> dict[str, Any]:
    return {"manifest_id": manifest.manifest_id, **manifest.identity_payload()}


def manifest_from_dict(data: Mapping[str, Any]) -> APIManifest:
    _keys(
        data,
        "manifest",
        {
            "apis",
            "bindings",
            "build",
            "build_id",
            "completeness",
            "diagnostics",
            "generated_by",
            "manifest_id",
            "metadata",
            "packages",
            "providers",
            "schema_version",
            "unresolved",
        },
    )
    schema_version = _integer(data["schema_version"], "manifest schema_version")
    manifest = APIManifest(
        schema_version=schema_version,
        build=_build(_object(data["build"], "build")),
        packages=tuple(
            _package(_object(item, "package"))
            for item in _array(data["packages"], "packages")
        ),
        providers=tuple(
            _provider(_object(item, "provider"))
            for item in _array(data["providers"], "providers")
        ),
        apis=tuple(
            _api(_object(item, "API")) for item in _array(data["apis"], "apis")
        ),
        bindings=tuple(
            _binding(_object(item, "binding"))
            for item in _array(data["bindings"], "bindings")
        ),
        unresolved=tuple(
            _unresolved(_object(item, "unresolved binding"))
            for item in _array(data["unresolved"], "unresolved")
        ),
        diagnostics=tuple(
            _diagnostic(_object(item, "diagnostic"))
            for item in _array(data["diagnostics"], "diagnostics")
        ),
        generated_by=_string(data["generated_by"], "generated_by"),
        metadata=_metadata(data["metadata"], "manifest metadata"),
    )
    if _string(data["build_id"], "build_id") != manifest.build.build_id:
        raise ValueError("manifest build_id does not match its build payload")
    completeness = _enum(
        ManifestCompleteness,
        data["completeness"],
        "manifest completeness",
    )
    if completeness != manifest.completeness:
        raise ValueError("manifest completeness does not match its contents")
    manifest_id = _string(data["manifest_id"], "manifest_id")
    verify_content_id(
        manifest_id,
        kind="cgomanifest",
        version=1,
        payload=manifest.identity_payload(),
    )
    return manifest


def _build(data: Mapping[str, Any]) -> BuildContext:
    _keys(
        data,
        "build",
        {
            "abi",
            "build_tags",
            "cgo_cflags",
            "cgo_cppflags",
            "cgo_enabled",
            "cgo_ldflags",
            "goarch",
            "goos",
            "macros",
            "toolchain",
        },
    )
    abi_data = _object(data["abi"], "target ABI")
    _keys(
        abi_data,
        "target ABI",
        {"data_model", "endianness", "pointer_width_bits", "target_triple"},
    )
    toolchain_data = _object(data["toolchain"], "toolchain")
    _keys(
        toolchain_data,
        "toolchain",
        {"c_compiler", "c_compiler_version", "go_version"},
    )
    macros = []
    for item in _array(data["macros"], "build macros"):
        macro = _object(item, "macro")
        _keys(macro, "macro", {"name", "value"})
        macros.append(
            MacroDefinition(
                name=_string(macro["name"], "macro name"),
                value=_optional_string(macro["value"], "macro value"),
            )
        )
    return BuildContext(
        goos=_string(data["goos"], "GOOS"),
        goarch=_string(data["goarch"], "GOARCH"),
        abi=TargetABI(
            target_triple=_string(abi_data["target_triple"], "target triple"),
            pointer_width_bits=_optional_integer(
                abi_data["pointer_width_bits"],
                "pointer width",
            ),
            endianness=_enum(Endianness, abi_data["endianness"], "endianness"),
            data_model=_optional_string(abi_data["data_model"], "data model"),
        ),
        toolchain=ToolchainIdentity(
            go_version=_string(toolchain_data["go_version"], "Go version"),
            c_compiler=_string(toolchain_data["c_compiler"], "C compiler"),
            c_compiler_version=_optional_string(
                toolchain_data["c_compiler_version"],
                "C compiler version",
            ),
        ),
        cgo_enabled=_boolean(data["cgo_enabled"], "cgo_enabled"),
        build_tags=_string_tuple(data["build_tags"], "build tags"),
        cgo_cflags=_string_tuple(data["cgo_cflags"], "cgo_cflags"),
        cgo_cppflags=_string_tuple(data["cgo_cppflags"], "cgo_cppflags"),
        cgo_ldflags=_string_tuple(data["cgo_ldflags"], "cgo_ldflags"),
        macros=tuple(macros),
    )


def _package(data: Mapping[str, Any]) -> GoPackageRecord:
    _keys(
        data,
        "package",
        {
            "files",
            "cgo_cflags",
            "cgo_cppflags",
            "cgo_ldflags",
            "identity",
            "macros",
            "metadata",
            "module_sum",
            "module_version",
            "name",
            "package_id",
            "source_sha256",
        },
    )
    identity = _package_identity(_object(data["identity"], "package identity"))
    macros = []
    for item in _array(data["macros"], "package macros"):
        macro = _object(item, "package macro")
        _keys(macro, "package macro", {"name", "value"})
        macros.append(
            MacroDefinition(
                _string(macro["name"], "package macro name"),
                _optional_string(macro["value"], "package macro value"),
            )
        )
    package = GoPackageRecord(
        identity=identity,
        name=_string(data["name"], "package name"),
        module_version=_optional_string(data["module_version"], "module version"),
        module_sum=_optional_string(data["module_sum"], "module sum"),
        source_sha256=_optional_string(data["source_sha256"], "package source digest"),
        files=_string_tuple(data["files"], "package files"),
        cgo_cflags=_string_tuple(data["cgo_cflags"], "package cgo_cflags"),
        cgo_cppflags=_string_tuple(data["cgo_cppflags"], "package cgo_cppflags"),
        cgo_ldflags=_string_tuple(data["cgo_ldflags"], "package cgo_ldflags"),
        macros=tuple(macros),
        metadata=_metadata(data["metadata"], "package metadata"),
    )
    if _string(data["package_id"], "package_id") != package.package_id:
        raise ValueError("package_id does not match package identity")
    return package


def _package_identity(data: Mapping[str, Any]) -> GoPackageIdentity:
    _keys(data, "package identity", {"import_path", "module_path"})
    return GoPackageIdentity(
        import_path=_string(data["import_path"], "package import path"),
        module_path=_string(data["module_path"], "module path"),
    )


def _provider(data: Mapping[str, Any]) -> ProviderRecord:
    _keys(
        data,
        "provider",
        {
            "abi_version",
            "artifacts",
            "identity",
            "metadata",
            "provider_id",
            "release_id",
            "version",
        },
    )
    artifacts = []
    for item in _array(data["artifacts"], "provider artifacts"):
        artifact = _object(item, "provider artifact")
        _keys(artifact, "provider artifact", {"kind", "locator", "sha256"})
        artifacts.append(
            ProviderArtifact(
                kind=_string(artifact["kind"], "artifact kind"),
                locator=_string(artifact["locator"], "artifact locator"),
                sha256=_optional_string(artifact["sha256"], "artifact digest"),
            )
        )
    provider = ProviderRecord(
        identity=_provider_identity(_object(data["identity"], "provider identity")),
        version=_optional_string(data["version"], "provider version"),
        abi_version=_optional_string(data["abi_version"], "provider ABI version"),
        artifacts=tuple(artifacts),
        metadata=_metadata(data["metadata"], "provider metadata"),
    )
    if _string(data["provider_id"], "provider_id") != provider.provider_id:
        raise ValueError("provider_id does not match provider identity")
    if _string(data["release_id"], "release_id") != provider.release_id:
        raise ValueError("release_id does not match provider release payload")
    return provider


def _provider_identity(data: Mapping[str, Any]) -> ProviderIdentity:
    _keys(data, "provider identity", {"kind", "name", "namespace"})
    return ProviderIdentity(
        kind=_enum(ProviderKind, data["kind"], "provider kind"),
        name=_string(data["name"], "provider name"),
        namespace=_string(data["namespace"], "provider namespace"),
    )


def _api(data: Mapping[str, Any]) -> ManifestAPI:
    _keys(
        data,
        "API",
        {"aliases", "api_id", "declarations", "identity", "metadata"},
    )
    api = ManifestAPI(
        identity=_api_identity(_object(data["identity"], "API identity")),
        declarations=tuple(
            _declaration(_object(item, "API declaration"))
            for item in _array(data["declarations"], "API declarations")
        ),
        aliases=_string_tuple(data["aliases"], "API aliases"),
        metadata=_metadata(data["metadata"], "API metadata"),
    )
    api_id = _string(data["api_id"], "api_id")
    verify_content_id(
        api_id,
        kind="cgoapi",
        version=1,
        payload=api.identity.identity_payload(),
    )
    return api


def _api_identity(data: Mapping[str, Any]) -> APIIdentity:
    _keys(
        data,
        "API identity",
        {"kind", "linkage_name", "provider", "signature", "symbol"},
    )
    return APIIdentity(
        provider=_provider_identity(_object(data["provider"], "API provider")),
        symbol=_string(data["symbol"], "API symbol"),
        linkage_name=_string(data["linkage_name"], "API linkage name"),
        signature=_signature(_object(data["signature"], "API signature")),
        kind=_enum(APIKind, data["kind"], "API kind"),
    )


def _signature(data: Mapping[str, Any]) -> CFunctionSignature:
    _keys(
        data,
        "API signature",
        {"abi_tag", "calling_convention", "parameters", "result", "variadic"},
    )
    return CFunctionSignature(
        result=_c_type(_object(data["result"], "result C type")),
        parameters=tuple(
            _c_type(_object(item, "parameter C type"))
            for item in _array(data["parameters"], "signature parameters")
        ),
        variadic=_boolean(data["variadic"], "signature variadic"),
        calling_convention=_enum(
            CallingConvention,
            data["calling_convention"],
            "calling convention",
        ),
        abi_tag=_string(data["abi_tag"], "signature ABI tag"),
    )


def _c_type(data: Mapping[str, Any]) -> CTypeIdentity:
    _keys(data, "C type", {"alignment_bits", "canonical", "size_bits"})
    return CTypeIdentity(
        canonical=_string(data["canonical"], "canonical C type"),
        size_bits=_optional_integer(data["size_bits"], "C type size"),
        alignment_bits=_optional_integer(data["alignment_bits"], "C type alignment"),
    )


def _declaration(data: Mapping[str, Any]) -> APIDeclaration:
    _keys(data, "API declaration", {"header", "location", "spelling"})
    return APIDeclaration(
        location=_location(_object(data["location"], "declaration location")),
        spelling=_string(data["spelling"], "declaration spelling"),
        header=_optional_string(data["header"], "declaration header"),
    )


def _binding(data: Mapping[str, Any]) -> APIBinding:
    _keys(
        data,
        "binding",
        {
            "api_id",
            "binding_id",
            "cgo_name",
            "declaration_sites",
            "directives",
            "kind",
            "linkage",
            "metadata",
            "package_id",
            "use_sites",
        },
    )
    binding = APIBinding(
        package_id=_string(data["package_id"], "binding package_id"),
        cgo_name=_string(data["cgo_name"], "binding cgo_name"),
        api_id=_string(data["api_id"], "binding api_id"),
        kind=_enum(BindingKind, data["kind"], "binding kind"),
        linkage=_enum(Linkage, data["linkage"], "binding linkage"),
        use_sites=tuple(
            _location(_object(item, "binding use site"))
            for item in _array(data["use_sites"], "binding use sites")
        ),
        declaration_sites=tuple(
            _location(_object(item, "binding declaration site"))
            for item in _array(data["declaration_sites"], "binding declaration sites")
        ),
        directives=tuple(
            _enum(CgoDirective, item, "cgo directive")
            for item in _array(data["directives"], "binding directives")
        ),
        metadata=_metadata(data["metadata"], "binding metadata"),
    )
    if _string(data["binding_id"], "binding_id") != binding.binding_id:
        raise ValueError("binding_id does not match binding identity")
    return binding


def _unresolved(data: Mapping[str, Any]) -> UnresolvedBinding:
    _keys(
        data,
        "unresolved binding",
        {
            "candidate_api_ids",
            "cgo_name",
            "detail",
            "directives",
            "package_id",
            "reason",
            "reference_id",
            "use_sites",
        },
    )
    item = UnresolvedBinding(
        package_id=_string(data["package_id"], "unresolved package_id"),
        cgo_name=_string(data["cgo_name"], "unresolved cgo_name"),
        reason=_enum(UnresolvedReason, data["reason"], "unresolved reason"),
        use_sites=tuple(
            _location(_object(site, "unresolved use site"))
            for site in _array(data["use_sites"], "unresolved use sites")
        ),
        directives=tuple(
            _enum(CgoDirective, directive, "cgo directive")
            for directive in _array(data["directives"], "unresolved directives")
        ),
        candidate_api_ids=_string_tuple(
            data["candidate_api_ids"],
            "candidate_api_ids",
        ),
        detail=_string(data["detail"], "unresolved detail", allow_empty=True),
    )
    if _string(data["reference_id"], "reference_id") != item.reference_id:
        raise ValueError("reference_id does not match unresolved binding identity")
    return item


def _diagnostic(data: Mapping[str, Any]) -> ManifestDiagnostic:
    _keys(data, "diagnostic", {"code", "message", "severity", "subject_id"})
    return ManifestDiagnostic(
        severity=_enum(DiagnosticSeverity, data["severity"], "diagnostic severity"),
        code=_string(data["code"], "diagnostic code"),
        message=_string(data["message"], "diagnostic message"),
        subject_id=_optional_string(data["subject_id"], "diagnostic subject_id"),
    )


def _location(data: Mapping[str, Any]) -> SourceLocation:
    _keys(data, "source location", {"column", "content_sha256", "line", "path"})
    return SourceLocation(
        path=_string(data["path"], "source path"),
        line=_integer(data["line"], "source line"),
        column=_integer(data["column"], "source column"),
        content_sha256=_optional_string(data["content_sha256"], "source digest"),
    )


def _metadata(value: Any, label: str) -> tuple[tuple[str, str], ...]:
    data = _object(value, label)
    return tuple(
        sorted(
            (_string(key, f"{label} key"), _string(item, f"{label} value", allow_empty=True))
            for key, item in data.items()
        )
    )


def _keys(data: Mapping[str, Any], label: str, expected: set[str]) -> None:
    actual = set(data)
    missing = expected - actual
    unknown = actual - expected
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise ValueError(f"{label} has invalid fields: {', '.join(details)}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _optional_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label)


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _array(value, label))


def _enum(enum_type: type[E], value: Any, label: str) -> E:
    raw = _string(value, label)
    try:
        return enum_type(raw)
    except ValueError as error:
        raise ValueError(f"invalid {label}: {raw!r}") from error
