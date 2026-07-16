from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

from .identity import (
    APIIdentity,
    APIKind,
    BuildContext,
    GoPackageIdentity,
    IDENTITY_SCHEMA_VERSION,
    Linkage,
    MacroDefinition,
    ProviderIdentity,
    make_content_id,
    validate_api_id,
    validate_content_id,
)


MANIFEST_SCHEMA_VERSION = 1

_C_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BindingKind(str, Enum):
    DIRECT = "direct"
    STATIC_FUNCTION = "static_function"
    MACRO_WRAPPER = "macro_wrapper"
    GENERATED_WRAPPER = "generated_wrapper"
    CGO_INTRINSIC = "cgo_intrinsic"


class CgoDirective(str, Enum):
    NOESCAPE = "noescape"
    NOCALLBACK = "nocallback"


class UnresolvedReason(str, Enum):
    MISSING_IDENTITY_COMPONENTS = "missing_identity_components"
    MISSING_PROVIDER = "missing_provider"
    MISSING_SIGNATURE = "missing_signature"
    AMBIGUOUS_CANDIDATE = "ambiguous_candidate"
    UNSUPPORTED_CONSTRUCT = "unsupported_construct"
    BUILD_ERROR = "build_error"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ManifestCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


def _metadata(value: tuple[tuple[str, str], ...], label: str) -> tuple[tuple[str, str], ...]:
    normalized = tuple(sorted(value))
    if len(normalized) != len({key for key, _ in normalized}):
        raise ValueError(f"{label} keys must be unique")
    if any(not key.strip() for key, _ in normalized):
        raise ValueError(f"{label} keys must not be blank")
    return normalized


@dataclass(frozen=True)
class SourceLocation:
    path: str
    line: int
    column: int = 1
    content_sha256: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        raw = self.path.replace("\\", "/").strip()
        path = PurePosixPath(raw)
        if (
            not raw
            or str(path) == "."
            or path.is_absolute()
            or ".." in path.parts
            or re.match(r"^[A-Za-z]:/", raw)
        ):
            raise ValueError("source paths must be non-empty, workspace-relative paths")
        if self.line <= 0 or self.column <= 0:
            raise ValueError("source line and column must be positive")
        if self.content_sha256 is not None and _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise ValueError("source content digest must be a lowercase SHA-256 hex string")
        object.__setattr__(self, "path", str(path))

    def payload(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "content_sha256": self.content_sha256,
            "line": self.line,
            "path": self.path,
        }


@dataclass(frozen=True)
class GoPackageRecord:
    identity: GoPackageIdentity
    name: str
    module_version: str | None = None
    module_sum: str | None = None
    source_sha256: str | None = None
    files: tuple[str, ...] = ()
    cgo_cflags: tuple[str, ...] = ()
    cgo_cppflags: tuple[str, ...] = ()
    cgo_ldflags: tuple[str, ...] = ()
    macros: tuple[MacroDefinition, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Go package name must not be blank")
        files = tuple(sorted(set(_relative_path(item, "Go package file") for item in self.files)))
        if len(files) != len(self.files):
            raise ValueError("Go package files must be unique")
        if self.source_sha256 is not None and _SHA256_RE.fullmatch(self.source_sha256) is None:
            raise ValueError("package source digest must be a lowercase SHA-256 hex string")
        if self.module_version is not None and not self.module_version.strip():
            raise ValueError("module version must not be blank")
        if self.module_sum is not None and not self.module_sum.strip():
            raise ValueError("module sum must not be blank")
        for group in (self.cgo_cflags, self.cgo_cppflags, self.cgo_ldflags):
            if any(not item or "\x00" in item for item in group):
                raise ValueError("package cgo flags must be non-empty and contain no NUL")
        macros = tuple(
            sorted(
                self.macros,
                key=lambda item: (item.name, item.value is not None, item.value or ""),
            )
        )
        if len(macros) != len({item.name for item in macros}):
            raise ValueError("package C macro names must be unique")
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "cgo_cflags", tuple(self.cgo_cflags))
        object.__setattr__(self, "cgo_cppflags", tuple(self.cgo_cppflags))
        object.__setattr__(self, "cgo_ldflags", tuple(self.cgo_ldflags))
        object.__setattr__(self, "macros", macros)
        object.__setattr__(self, "metadata", _metadata(self.metadata, "package metadata"))

    @property
    def package_id(self) -> str:
        return self.identity.package_id

    def payload(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "cgo_cflags": list(self.cgo_cflags),
            "cgo_cppflags": list(self.cgo_cppflags),
            "cgo_ldflags": list(self.cgo_ldflags),
            "identity": self.identity.identity_payload(),
            "macros": [item.identity_payload() for item in self.macros],
            "metadata": dict(self.metadata),
            "module_sum": self.module_sum,
            "module_version": self.module_version,
            "name": self.name,
            "package_id": self.package_id,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class ProviderArtifact:
    kind: str
    locator: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.locator.strip():
            raise ValueError("provider artifact kind and locator must not be blank")
        if "\x00" in self.locator:
            raise ValueError("provider artifact locator must not contain NUL")
        if self.sha256 is not None and _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("provider artifact digest must be a lowercase SHA-256 hex string")

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "locator": self.locator, "sha256": self.sha256}


@dataclass(frozen=True)
class ProviderRecord:
    identity: ProviderIdentity
    version: str | None = None
    abi_version: str | None = None
    artifacts: tuple[ProviderArtifact, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.version is not None and not self.version.strip():
            raise ValueError("provider version must not be blank")
        if self.abi_version is not None and not self.abi_version.strip():
            raise ValueError("provider ABI version must not be blank")
        if self.version is None and self.abi_version is None and not self.artifacts:
            raise ValueError(
                "provider records require a version, ABI version, or artifact identity"
            )
        artifacts = tuple(
            sorted(
                set(self.artifacts),
                key=lambda item: (item.kind, item.locator, item.sha256 or ""),
            )
        )
        if len(artifacts) != len(self.artifacts):
            raise ValueError("provider artifacts must be unique")
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "metadata", _metadata(self.metadata, "provider metadata"))

    @property
    def provider_id(self) -> str:
        return self.identity.provider_id

    @property
    def release_id(self) -> str:
        return make_content_id(
            "cgorelease",
            IDENTITY_SCHEMA_VERSION,
            {
                "abi_version": self.abi_version,
                "artifacts": [item.payload() for item in self.artifacts],
                "provider_id": self.provider_id,
                "version": self.version,
            },
        )

    def payload(self) -> dict[str, Any]:
        return {
            "abi_version": self.abi_version,
            "artifacts": [item.payload() for item in self.artifacts],
            "identity": self.identity.identity_payload(),
            "metadata": dict(self.metadata),
            "provider_id": self.provider_id,
            "release_id": self.release_id,
            "version": self.version,
        }


@dataclass(frozen=True)
class APIDeclaration:
    location: SourceLocation
    spelling: str
    header: str | None = None

    def __post_init__(self) -> None:
        if not self.spelling.strip() or "\x00" in self.spelling:
            raise ValueError("API declaration spelling must not be blank")
        if self.header is not None:
            object.__setattr__(self, "header", _relative_path(self.header, "header"))

    def payload(self) -> dict[str, Any]:
        return {
            "header": self.header,
            "location": self.location.payload(),
            "spelling": self.spelling,
        }


@dataclass(frozen=True)
class ManifestAPI:
    identity: APIIdentity
    declarations: tuple[APIDeclaration, ...]
    aliases: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        intrinsic = self.identity.kind == APIKind.CGO_INTRINSIC
        if not self.declarations and not intrinsic:
            raise ValueError("non-intrinsic manifest APIs require a declaration")
        if self.declarations and intrinsic:
            raise ValueError("cgo intrinsic APIs must not have C declarations")
        declarations = tuple(
            sorted(
                set(self.declarations),
                key=lambda item: (
                    item.location.path,
                    item.location.line,
                    item.location.column,
                    item.location.content_sha256 or "",
                    item.header or "",
                    item.spelling,
                ),
            )
        )
        aliases = tuple(sorted(set(self.aliases)))
        if len(declarations) != len(self.declarations):
            raise ValueError("API declarations must be unique")
        if len(aliases) != len(self.aliases):
            raise ValueError("API aliases must be unique")
        if any(_C_IDENTIFIER_RE.fullmatch(item) is None for item in aliases):
            raise ValueError("API aliases must be C identifiers")
        object.__setattr__(self, "declarations", declarations)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "metadata", _metadata(self.metadata, "API metadata"))

    @property
    def api_id(self) -> str:
        return self.identity.api_id

    def payload(self) -> dict[str, Any]:
        return {
            "aliases": list(self.aliases),
            "api_id": self.api_id,
            "declarations": [item.payload() for item in self.declarations],
            "identity": self.identity.record_payload(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class APIBinding:
    package_id: str
    cgo_name: str
    api_id: str
    kind: BindingKind
    linkage: Linkage
    use_sites: tuple[SourceLocation, ...] = ()
    declaration_sites: tuple[SourceLocation, ...] = ()
    directives: tuple[CgoDirective, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        validate_content_id(
            self.package_id,
            expected_kind="cgopkg",
            expected_version=IDENTITY_SCHEMA_VERSION,
        )
        validate_api_id(self.api_id)
        if _C_IDENTIFIER_RE.fullmatch(self.cgo_name) is None:
            raise ValueError(f"invalid cgo selector name: {self.cgo_name!r}")
        use_sites = tuple(sorted(set(self.use_sites), key=_location_sort_key))
        declaration_sites = tuple(
            sorted(set(self.declaration_sites), key=_location_sort_key)
        )
        directives = tuple(sorted(set(self.directives), key=lambda item: item.value))
        if len(use_sites) != len(self.use_sites):
            raise ValueError("binding use sites must be unique")
        if len(declaration_sites) != len(self.declaration_sites):
            raise ValueError("binding declaration sites must be unique")
        if len(directives) != len(self.directives):
            raise ValueError("binding directives must be unique")
        object.__setattr__(self, "use_sites", use_sites)
        object.__setattr__(self, "declaration_sites", declaration_sites)
        object.__setattr__(self, "directives", directives)
        object.__setattr__(self, "metadata", _metadata(self.metadata, "binding metadata"))

    def identity_payload(self) -> dict[str, Any]:
        return {
            "api_id": self.api_id,
            "cgo_name": self.cgo_name,
            "kind": self.kind.value,
            "linkage": self.linkage.value,
            "package_id": self.package_id,
        }

    @property
    def binding_id(self) -> str:
        return make_content_id("cgobind", IDENTITY_SCHEMA_VERSION, self.identity_payload())

    def payload(self) -> dict[str, Any]:
        return {
            "api_id": self.api_id,
            "binding_id": self.binding_id,
            "cgo_name": self.cgo_name,
            "declaration_sites": [item.payload() for item in self.declaration_sites],
            "directives": [item.value for item in self.directives],
            "kind": self.kind.value,
            "linkage": self.linkage.value,
            "metadata": dict(self.metadata),
            "package_id": self.package_id,
            "use_sites": [item.payload() for item in self.use_sites],
        }


@dataclass(frozen=True)
class UnresolvedBinding:
    package_id: str
    cgo_name: str
    reason: UnresolvedReason
    use_sites: tuple[SourceLocation, ...] = ()
    directives: tuple[CgoDirective, ...] = ()
    candidate_api_ids: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        validate_content_id(
            self.package_id,
            expected_kind="cgopkg",
            expected_version=IDENTITY_SCHEMA_VERSION,
        )
        if _C_IDENTIFIER_RE.fullmatch(self.cgo_name) is None:
            raise ValueError(f"invalid unresolved cgo selector: {self.cgo_name!r}")
        candidates = tuple(sorted(set(self.candidate_api_ids)))
        for candidate in candidates:
            validate_api_id(candidate)
        if len(candidates) != len(self.candidate_api_ids):
            raise ValueError("unresolved candidate API ids must be unique")
        if self.reason == UnresolvedReason.AMBIGUOUS_CANDIDATE and len(candidates) < 2:
            raise ValueError("ambiguous bindings require at least two candidates")
        use_sites = tuple(sorted(set(self.use_sites), key=_location_sort_key))
        directives = tuple(sorted(set(self.directives), key=lambda item: item.value))
        if len(use_sites) != len(self.use_sites):
            raise ValueError("unresolved use sites must be unique")
        if len(directives) != len(self.directives):
            raise ValueError("unresolved directives must be unique")
        object.__setattr__(self, "candidate_api_ids", candidates)
        object.__setattr__(self, "use_sites", use_sites)
        object.__setattr__(self, "directives", directives)

    def identity_payload(self) -> dict[str, Any]:
        return {"cgo_name": self.cgo_name, "package_id": self.package_id}

    @property
    def reference_id(self) -> str:
        return make_content_id("cgoref", IDENTITY_SCHEMA_VERSION, self.identity_payload())

    def payload(self) -> dict[str, Any]:
        return {
            "candidate_api_ids": list(self.candidate_api_ids),
            "cgo_name": self.cgo_name,
            "detail": self.detail,
            "directives": [item.value for item in self.directives],
            "package_id": self.package_id,
            "reason": self.reason.value,
            "reference_id": self.reference_id,
            "use_sites": [item.payload() for item in self.use_sites],
        }


@dataclass(frozen=True)
class ManifestDiagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    subject_id: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("manifest diagnostic code and message must not be blank")

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "subject_id": self.subject_id,
        }


@dataclass(frozen=True)
class APIManifest:
    build: BuildContext
    packages: tuple[GoPackageRecord, ...] = ()
    providers: tuple[ProviderRecord, ...] = ()
    apis: tuple[ManifestAPI, ...] = ()
    bindings: tuple[APIBinding, ...] = ()
    unresolved: tuple[UnresolvedBinding, ...] = ()
    diagnostics: tuple[ManifestDiagnostic, ...] = ()
    generated_by: str = "cgoprof"
    metadata: tuple[tuple[str, str], ...] = ()
    schema_version: int = MANIFEST_SCHEMA_VERSION
    _manifest_id: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported manifest schema version: {self.schema_version}")
        if not self.generated_by.strip():
            raise ValueError("manifest generator must not be blank")
        packages = tuple(sorted(self.packages, key=lambda item: item.package_id))
        providers = tuple(sorted(self.providers, key=lambda item: item.provider_id))
        apis = tuple(sorted(self.apis, key=lambda item: item.api_id))
        bindings = tuple(sorted(self.bindings, key=lambda item: item.binding_id))
        unresolved = tuple(sorted(self.unresolved, key=lambda item: item.reference_id))
        diagnostics = tuple(
            sorted(
                set(self.diagnostics),
                key=lambda item: (
                    item.severity.value,
                    item.code,
                    item.subject_id or "",
                    item.message,
                ),
            )
        )
        _require_unique((item.package_id for item in packages), "package id")
        _require_unique(
            (item.identity.import_path for item in packages),
            "Go package import path",
        )
        _require_unique((item.provider_id for item in providers), "provider id")
        _require_unique((item.api_id for item in apis), "API id")
        _require_unique((item.binding_id for item in bindings), "binding id")
        _require_unique((item.reference_id for item in unresolved), "unresolved reference id")
        package_ids = {item.package_id for item in packages}
        provider_ids = {item.provider_id for item in providers}
        api_ids = {item.api_id for item in apis}
        apis_by_id = {item.api_id: item for item in apis}
        for api in apis:
            if api.identity.provider.provider_id not in provider_ids:
                raise ValueError(f"API {api.api_id} references an unknown provider")
        selectors: set[tuple[str, str]] = set()
        for binding in bindings:
            _validate_binding_references(binding, package_ids, api_ids)
            _validate_binding_kind(binding, apis_by_id[binding.api_id])
            selector = (binding.package_id, binding.cgo_name)
            if selector in selectors:
                raise ValueError("a cgo package selector has multiple resolved bindings")
            selectors.add(selector)
        for item in unresolved:
            if item.package_id not in package_ids:
                raise ValueError("unresolved binding references an unknown package")
            selector = (item.package_id, item.cgo_name)
            if selector in selectors:
                raise ValueError("a cgo package selector cannot be resolved and unresolved")
            selectors.add(selector)
            unknown_candidates = set(item.candidate_api_ids) - api_ids
            if unknown_candidates:
                raise ValueError("unresolved binding contains candidate APIs absent from manifest")
        object.__setattr__(self, "packages", packages)
        object.__setattr__(self, "providers", providers)
        object.__setattr__(self, "apis", apis)
        object.__setattr__(self, "bindings", bindings)
        object.__setattr__(self, "unresolved", unresolved)
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "metadata", _metadata(self.metadata, "manifest metadata"))
        object.__setattr__(
            self,
            "_manifest_id",
            make_content_id(
                "cgomanifest",
                IDENTITY_SCHEMA_VERSION,
                self.identity_payload(),
            ),
        )

    @property
    def completeness(self) -> ManifestCompleteness:
        has_errors = any(item.severity == DiagnosticSeverity.ERROR for item in self.diagnostics)
        if self.unresolved or has_errors:
            return ManifestCompleteness.PARTIAL
        return ManifestCompleteness.COMPLETE

    def identity_payload(self) -> dict[str, Any]:
        return {
            "apis": [item.payload() for item in self.apis],
            "bindings": [item.payload() for item in self.bindings],
            "build": self.build.identity_payload(),
            "build_id": self.build.build_id,
            "completeness": self.completeness.value,
            "diagnostics": [item.payload() for item in self.diagnostics],
            "generated_by": self.generated_by,
            "metadata": dict(self.metadata),
            "packages": [item.payload() for item in self.packages],
            "providers": [item.payload() for item in self.providers],
            "schema_version": self.schema_version,
            "unresolved": [item.payload() for item in self.unresolved],
        }

    @property
    def manifest_id(self) -> str:
        return self._manifest_id

    def require_complete(self) -> None:
        if self.completeness != ManifestCompleteness.COMPLETE:
            error_count = sum(
                item.severity == DiagnosticSeverity.ERROR
                for item in self.diagnostics
            )
            raise ValueError(
                f"manifest {self.manifest_id} is partial: "
                f"{len(self.unresolved)} unresolved bindings, "
                f"{error_count} error diagnostics"
            )


def _relative_path(value: str, label: str) -> str:
    raw = value.replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if (
        not raw
        or str(path) == "."
        or path.is_absolute()
        or ".." in path.parts
        or re.match(r"^[A-Za-z]:/", raw)
    ):
        raise ValueError(f"{label} must be a workspace-relative path")
    return str(path)


def _location_sort_key(item: SourceLocation) -> tuple[str, int, int, str]:
    return (
        item.path,
        item.line,
        item.column,
        item.content_sha256 or "",
    )


def _require_unique(values: Any, label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"manifest contains duplicate {label} values")


def _validate_binding_references(
    binding: APIBinding,
    package_ids: set[str],
    api_ids: set[str],
) -> None:
    if binding.package_id not in package_ids:
        raise ValueError(f"binding {binding.binding_id} references an unknown package")
    if binding.api_id not in api_ids:
        raise ValueError(f"binding {binding.binding_id} references an unknown API")


def _validate_binding_kind(binding: APIBinding, api: ManifestAPI) -> None:
    intrinsic = api.identity.kind == APIKind.CGO_INTRINSIC
    if intrinsic:
        if (
            binding.kind != BindingKind.CGO_INTRINSIC
            or binding.linkage != Linkage.INTRINSIC
        ):
            raise ValueError("cgo intrinsic APIs require intrinsic bindings")
        return
    if binding.kind == BindingKind.CGO_INTRINSIC or binding.linkage == Linkage.INTRINSIC:
        raise ValueError("non-intrinsic APIs cannot use intrinsic bindings")
    if (
        api.identity.kind == APIKind.FUNCTION_LIKE_MACRO
        and binding.kind not in {BindingKind.MACRO_WRAPPER, BindingKind.GENERATED_WRAPPER}
    ):
        raise ValueError("function-like macros require wrapper bindings")
    if binding.kind == BindingKind.STATIC_FUNCTION and binding.linkage != Linkage.INTERNAL:
        raise ValueError("static function bindings require internal linkage")
    if (
        binding.kind == BindingKind.GENERATED_WRAPPER
        and binding.linkage != Linkage.GENERATED_WRAPPER
    ):
        raise ValueError("generated wrapper bindings require generated-wrapper linkage")
