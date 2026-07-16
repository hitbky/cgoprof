from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


IDENTITY_SCHEMA_VERSION = 1

_CONTENT_ID_RE = re.compile(
    r"^(?P<kind>[a-z][a-z0-9_-]*):v(?P<version>[1-9][0-9]*):(?P<digest>[0-9a-f]{64})$"
)
_C_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GO_IMPORT_PATH_RE = re.compile(r"^[^\s\\]+$")
_MACRO_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class APIKind(str, Enum):
    FUNCTION = "function"
    FUNCTION_LIKE_MACRO = "function_like_macro"
    CGO_INTRINSIC = "cgo_intrinsic"


class ProviderKind(str, Enum):
    SYSTEM_LIBRARY = "system_library"
    PKG_CONFIG = "pkg_config"
    SHARED_LIBRARY = "shared_library"
    STATIC_ARCHIVE = "static_archive"
    FRAMEWORK = "framework"
    SOURCE_BUNDLE = "source_bundle"
    GO_PACKAGE_LOCAL = "go_package_local"
    CGO_INTRINSIC = "cgo_intrinsic"


class CallingConvention(str, Enum):
    C = "c"
    CDECL = "cdecl"
    STDCALL = "stdcall"
    FASTCALL = "fastcall"
    VECTORCALL = "vectorcall"
    SYSTEM = "system"


class Linkage(str, Enum):
    EXTERNAL = "external"
    INTERNAL = "internal"
    WEAK = "weak"
    GENERATED_WRAPPER = "generated_wrapper"
    INTRINSIC = "intrinsic"


class Endianness(str, Enum):
    LITTLE = "little"
    BIG = "big"
    UNKNOWN = "unknown"


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the only byte encoding used by content-addressed identities."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def make_content_id(kind: str, version: int, payload: Mapping[str, Any]) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
        raise ValueError(f"invalid content id kind: {kind!r}")
    if version <= 0:
        raise ValueError("content id version must be positive")
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{kind}:v{version}:{digest}"


def validate_content_id(
    value: str,
    *,
    expected_kind: str | None = None,
    expected_version: int | None = None,
) -> None:
    match = _CONTENT_ID_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"malformed content id: {value!r}")
    if expected_kind is not None and match.group("kind") != expected_kind:
        raise ValueError(
            f"expected {expected_kind} content id, got {match.group('kind')}"
        )
    if expected_version is not None and int(match.group("version")) != expected_version:
        raise ValueError(
            f"expected content id version {expected_version}, "
            f"got {match.group('version')}"
        )


def verify_content_id(
    value: str,
    *,
    kind: str,
    version: int,
    payload: Mapping[str, Any],
) -> None:
    validate_content_id(value, expected_kind=kind, expected_version=version)
    expected = make_content_id(kind, version, payload)
    if value != expected:
        raise ValueError(f"{kind} content id does not match its payload")


def normalize_c_type(value: str) -> str:
    """Normalize an already typedef-resolved C type spelling.

    This removes irrelevant whitespace only. It deliberately does not pretend to
    resolve typedefs or layouts; the producing C frontend must supply an
    ABI-canonical spelling before constructing an exact API identity.
    """

    if "\x00" in value:
        raise ValueError("C type spelling must not contain NUL")
    normalized = " ".join(value.strip().split())
    for token in ("*", "[", "]", "(", ")", ","):
        normalized = re.sub(rf"\s*{re.escape(token)}\s*", token, normalized)
    if not normalized:
        raise ValueError("C type spelling must not be empty")
    return normalized


@dataclass(frozen=True)
class CTypeIdentity:
    """A C type after typedef and target-ABI canonicalization."""

    canonical: str
    size_bits: int | None = None
    alignment_bits: int | None = None
    source_spelling: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical", normalize_c_type(self.canonical))
        if self.size_bits is not None and self.size_bits < 0:
            raise ValueError("C type size must be non-negative")
        if self.alignment_bits is not None and self.alignment_bits <= 0:
            raise ValueError("C type alignment must be positive")
        if self.source_spelling is not None and not self.source_spelling.strip():
            raise ValueError("C type source spelling must not be blank")

    def identity_payload(self) -> dict[str, Any]:
        return {"canonical": self.canonical}

    def record_payload(self) -> dict[str, Any]:
        return {
            "alignment_bits": self.alignment_bits,
            "canonical": self.canonical,
            "size_bits": self.size_bits,
        }


@dataclass(frozen=True)
class CFunctionSignature:
    result: CTypeIdentity
    parameters: tuple[CTypeIdentity, ...] = ()
    variadic: bool = False
    calling_convention: CallingConvention = CallingConvention.C
    abi_tag: str = "c"

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", tuple(self.parameters))
        if not self.abi_tag.strip():
            raise ValueError("signature ABI tag must not be blank")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "abi_tag": self.abi_tag,
            "calling_convention": self.calling_convention.value,
            "parameters": [item.identity_payload() for item in self.parameters],
            "result": self.result.identity_payload(),
            "variadic": self.variadic,
        }

    def record_payload(self) -> dict[str, Any]:
        return {
            "abi_tag": self.abi_tag,
            "calling_convention": self.calling_convention.value,
            "parameters": [item.record_payload() for item in self.parameters],
            "result": self.result.record_payload(),
            "variadic": self.variadic,
        }

    @property
    def signature_id(self) -> str:
        return make_content_id("cgosig", IDENTITY_SCHEMA_VERSION, self.identity_payload())


@dataclass(frozen=True)
class ProviderIdentity:
    kind: ProviderKind
    namespace: str
    name: str

    def __post_init__(self) -> None:
        namespace = self.namespace.strip().rstrip("/")
        name = self.name.strip()
        if not namespace or any(char.isspace() for char in namespace):
            raise ValueError("provider namespace must be a non-blank URI-like name")
        if not name or "\x00" in name:
            raise ValueError("provider name must not be blank")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "name", name)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "namespace": self.namespace,
        }

    @property
    def provider_id(self) -> str:
        return make_content_id("cgoprov", IDENTITY_SCHEMA_VERSION, self.identity_payload())


@dataclass(frozen=True)
class APIIdentity:
    provider: ProviderIdentity
    symbol: str
    signature: CFunctionSignature
    kind: APIKind = APIKind.FUNCTION
    linkage_name: str | None = None

    def __post_init__(self) -> None:
        if _C_IDENTIFIER_RE.fullmatch(self.symbol) is None:
            raise ValueError(f"invalid C API symbol: {self.symbol!r}")
        linkage_name = self.linkage_name or self.symbol
        if _C_IDENTIFIER_RE.fullmatch(linkage_name) is None:
            raise ValueError(f"invalid C linkage name: {linkage_name!r}")
        object.__setattr__(self, "linkage_name", linkage_name)
        if self.kind == APIKind.CGO_INTRINSIC:
            if self.provider.kind != ProviderKind.CGO_INTRINSIC:
                raise ValueError("cgo intrinsic APIs require a cgo intrinsic provider")
        elif self.provider.kind == ProviderKind.CGO_INTRINSIC:
            raise ValueError("only cgo intrinsic APIs may use a cgo intrinsic provider")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "linkage_name": self.linkage_name,
            "provider": self.provider.identity_payload(),
            "signature": self.signature.identity_payload(),
            "symbol": self.symbol,
        }

    def record_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "linkage_name": self.linkage_name,
            "provider": self.provider.identity_payload(),
            "signature": self.signature.record_payload(),
            "symbol": self.symbol,
        }

    @property
    def api_id(self) -> str:
        return make_content_id("cgoapi", IDENTITY_SCHEMA_VERSION, self.identity_payload())

    @property
    def family_id(self) -> str:
        payload = {
            "kind": self.kind.value,
            "provider": self.provider.identity_payload(),
            "symbol": self.symbol,
        }
        return make_content_id("cgofamily", IDENTITY_SCHEMA_VERSION, payload)


@dataclass(frozen=True)
class GoPackageIdentity:
    import_path: str
    module_path: str

    def __post_init__(self) -> None:
        import_path = self.import_path.strip().rstrip("/")
        module_path = self.module_path.strip().rstrip("/")
        if not import_path or _GO_IMPORT_PATH_RE.fullmatch(import_path) is None:
            raise ValueError(f"invalid Go package import path: {self.import_path!r}")
        if not module_path or _GO_IMPORT_PATH_RE.fullmatch(module_path) is None:
            raise ValueError(f"invalid Go module path: {self.module_path!r}")
        if import_path != module_path and not import_path.startswith(module_path + "/"):
            raise ValueError("Go package import path must belong to its module path")
        object.__setattr__(self, "import_path", import_path)
        object.__setattr__(self, "module_path", module_path)

    def identity_payload(self) -> dict[str, Any]:
        return {"import_path": self.import_path, "module_path": self.module_path}

    @property
    def package_id(self) -> str:
        return make_content_id("cgopkg", IDENTITY_SCHEMA_VERSION, self.identity_payload())


@dataclass(frozen=True)
class MacroDefinition:
    name: str
    value: str | None = None

    def __post_init__(self) -> None:
        if _MACRO_RE.fullmatch(self.name) is None:
            raise ValueError(f"invalid C macro name: {self.name!r}")
        if self.value is not None and "\x00" in self.value:
            raise ValueError("C macro value must not contain NUL")

    def identity_payload(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True)
class TargetABI:
    target_triple: str
    pointer_width_bits: int | None = None
    endianness: Endianness = Endianness.UNKNOWN
    data_model: str | None = None

    def __post_init__(self) -> None:
        if not self.target_triple.strip():
            raise ValueError("target triple must not be blank")
        if self.pointer_width_bits is not None and self.pointer_width_bits <= 0:
            raise ValueError("pointer width must be positive")
        if self.data_model is not None and not self.data_model.strip():
            raise ValueError("data model must not be blank")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "data_model": self.data_model,
            "endianness": self.endianness.value,
            "pointer_width_bits": self.pointer_width_bits,
            "target_triple": self.target_triple,
        }


@dataclass(frozen=True)
class ToolchainIdentity:
    go_version: str
    c_compiler: str
    c_compiler_version: str | None = None

    def __post_init__(self) -> None:
        if not self.go_version.strip():
            raise ValueError("Go version must not be blank")
        if not self.c_compiler.strip():
            raise ValueError("C compiler identity must not be blank")
        if self.c_compiler_version is not None and not self.c_compiler_version.strip():
            raise ValueError("C compiler version must not be blank")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "c_compiler": self.c_compiler,
            "c_compiler_version": self.c_compiler_version,
            "go_version": self.go_version,
        }


@dataclass(frozen=True)
class BuildContext:
    goos: str
    goarch: str
    abi: TargetABI
    toolchain: ToolchainIdentity
    cgo_enabled: bool = True
    build_tags: tuple[str, ...] = ()
    cgo_cflags: tuple[str, ...] = ()
    cgo_cppflags: tuple[str, ...] = ()
    cgo_ldflags: tuple[str, ...] = ()
    macros: tuple[MacroDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not self.goos.strip() or not self.goarch.strip():
            raise ValueError("GOOS and GOARCH must not be blank")
        tags = tuple(sorted(set(self.build_tags)))
        if len(tags) != len(self.build_tags):
            raise ValueError("build tags must be unique")
        for tag in tags:
            if not tag or any(char.isspace() for char in tag):
                raise ValueError(f"invalid Go build tag: {tag!r}")
        macros = tuple(
            sorted(
                self.macros,
                key=lambda item: (item.name, item.value is not None, item.value or ""),
            )
        )
        if len({item.name for item in macros}) != len(macros):
            raise ValueError("C macro names must be unique")
        for group in (self.cgo_cflags, self.cgo_cppflags, self.cgo_ldflags):
            if any(not item or "\x00" in item for item in group):
                raise ValueError("cgo flags must be non-empty and contain no NUL")
        object.__setattr__(self, "build_tags", tags)
        object.__setattr__(self, "macros", macros)
        object.__setattr__(self, "cgo_cflags", tuple(self.cgo_cflags))
        object.__setattr__(self, "cgo_cppflags", tuple(self.cgo_cppflags))
        object.__setattr__(self, "cgo_ldflags", tuple(self.cgo_ldflags))

    def identity_payload(self) -> dict[str, Any]:
        return {
            "abi": self.abi.identity_payload(),
            "build_tags": list(self.build_tags),
            "cgo_cflags": list(self.cgo_cflags),
            "cgo_cppflags": list(self.cgo_cppflags),
            "cgo_enabled": self.cgo_enabled,
            "cgo_ldflags": list(self.cgo_ldflags),
            "goarch": self.goarch,
            "goos": self.goos,
            "macros": [item.identity_payload() for item in self.macros],
            "toolchain": self.toolchain.identity_payload(),
        }

    @property
    def build_id(self) -> str:
        return make_content_id("cgobuild", IDENTITY_SCHEMA_VERSION, self.identity_payload())

    @property
    def macros_fingerprint(self) -> str:
        payload = {"macros": [item.identity_payload() for item in self.macros]}
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_api_id(value: str) -> None:
    validate_content_id(
        value,
        expected_kind="cgoapi",
        expected_version=IDENTITY_SCHEMA_VERSION,
    )


def validate_manifest_id(value: str) -> None:
    validate_content_id(
        value,
        expected_kind="cgomanifest",
        expected_version=IDENTITY_SCHEMA_VERSION,
    )
