from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..scanner import c_identifier_may_be_shadowed, scan_cgo_references
from .identity import (
    APIIdentity,
    BuildContext,
    CFunctionSignature,
    CTypeIdentity,
    Endianness,
    GoPackageIdentity,
    Linkage,
    MacroDefinition,
    TargetABI,
    ToolchainIdentity,
)
from .manifest import (
    APIBinding,
    APIManifest,
    BindingKind,
    CgoDirective,
    DiagnosticSeverity,
    GoPackageRecord,
    ManifestAPI,
    ManifestDiagnostic,
    ProviderRecord,
    SourceLocation,
    UnresolvedBinding,
    UnresolvedReason,
)
from .intrinsics import intrinsic_manifest_api, intrinsic_provider_record


class ManifestDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveryOptions:
    go_binary: str = "go"
    goos: str | None = None
    goarch: str | None = None
    build_tags: tuple[str, ...] = ()
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.go_binary.strip():
            raise ValueError("Go binary must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("discovery timeout must be positive")
        tags = tuple(sorted(set(self.build_tags)))
        if len(tags) != len(self.build_tags):
            raise ValueError("discovery build tags must be unique")
        object.__setattr__(self, "build_tags", tags)


class ManifestAssembler:
    """Conflict-detecting assembler shared by discovery and future frontends."""

    def __init__(self, build: BuildContext, *, generated_by: str = "cgoprof") -> None:
        self.build = build
        self.generated_by = generated_by
        self._packages: dict[str, GoPackageRecord] = {}
        self._providers: dict[str, ProviderRecord] = {}
        self._apis: dict[str, ManifestAPI] = {}
        self._bindings: dict[tuple[str, str], APIBinding] = {}
        self._unresolved: dict[tuple[str, str], UnresolvedBinding] = {}
        self._diagnostics: set[ManifestDiagnostic] = set()

    def add_package(self, package: GoPackageRecord) -> None:
        _insert_exact(self._packages, package.package_id, package, "Go package")

    def add_provider(self, provider: ProviderRecord) -> None:
        existing = self._providers.get(provider.provider_id)
        if existing is not None:
            if existing.identity.identity_payload() != provider.identity.identity_payload():
                raise ValueError(f"provider id collision for {provider.provider_id}")
            if (
                existing.version is not None
                and provider.version is not None
                and existing.version != provider.version
            ) or (
                existing.abi_version is not None
                and provider.abi_version is not None
                and existing.abi_version != provider.abi_version
            ):
                raise ValueError(
                    f"conflicting API provider releases for {provider.provider_id}"
                )
            provider = ProviderRecord(
                identity=provider.identity,
                version=existing.version or provider.version,
                abi_version=existing.abi_version or provider.abi_version,
                artifacts=tuple({*existing.artifacts, *provider.artifacts}),
                metadata=_merge_metadata(existing.metadata, provider.metadata),
            )
        self._providers[provider.provider_id] = provider

    def add_api(self, api: ManifestAPI) -> None:
        existing = self._apis.get(api.api_id)
        if existing is not None:
            if existing.identity.identity_payload() != api.identity.identity_payload():
                raise ValueError(f"API id collision for {api.api_id}")
            api = ManifestAPI(
                identity=_merge_api_identity(existing.identity, api.identity),
                declarations=tuple({*existing.declarations, *api.declarations}),
                aliases=tuple({*existing.aliases, *api.aliases}),
                metadata=_merge_metadata(existing.metadata, api.metadata),
            )
        self._apis[api.api_id] = api

    def add_binding(self, binding: APIBinding) -> None:
        selector = (binding.package_id, binding.cgo_name)
        existing = self._bindings.get(selector)
        if existing is not None:
            if existing.identity_payload() != binding.identity_payload():
                raise ValueError(
                    f"conflicting exact bindings for {binding.package_id}.C.{binding.cgo_name}"
                )
            binding = APIBinding(
                package_id=binding.package_id,
                cgo_name=binding.cgo_name,
                api_id=binding.api_id,
                kind=binding.kind,
                linkage=binding.linkage,
                use_sites=tuple({*existing.use_sites, *binding.use_sites}),
                declaration_sites=tuple(
                    {*existing.declaration_sites, *binding.declaration_sites}
                ),
                directives=tuple({*existing.directives, *binding.directives}),
                metadata=_merge_metadata(existing.metadata, binding.metadata),
            )
        self._bindings[selector] = binding
        self._unresolved.pop(selector, None)

    def add_unresolved(self, item: UnresolvedBinding) -> None:
        selector = (item.package_id, item.cgo_name)
        if selector in self._bindings:
            return
        existing = self._unresolved.get(selector)
        if existing is not None:
            if existing.reason != item.reason:
                raise ValueError(
                    f"conflicting unresolved reasons for {item.package_id}.C.{item.cgo_name}"
                )
            item = UnresolvedBinding(
                package_id=item.package_id,
                cgo_name=item.cgo_name,
                reason=item.reason,
                use_sites=tuple({*existing.use_sites, *item.use_sites}),
                directives=tuple({*existing.directives, *item.directives}),
                candidate_api_ids=tuple(
                    {*existing.candidate_api_ids, *item.candidate_api_ids}
                ),
                detail=existing.detail or item.detail,
            )
        self._unresolved[selector] = item

    def add_diagnostic(self, diagnostic: ManifestDiagnostic) -> None:
        self._diagnostics.add(diagnostic)

    def build_manifest(
        self,
        *,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> APIManifest:
        return APIManifest(
            build=self.build,
            packages=tuple(self._packages.values()),
            providers=tuple(self._providers.values()),
            apis=tuple(self._apis.values()),
            bindings=tuple(self._bindings.values()),
            unresolved=tuple(self._unresolved.values()),
            diagnostics=tuple(self._diagnostics),
            generated_by=self.generated_by,
            metadata=metadata,
        )


def discover_project_manifest(
    root: str | Path,
    options: DiscoveryOptions | None = None,
) -> APIManifest:
    options = options or DiscoveryOptions()
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ManifestDiscoveryError(f"project root is not a directory: {root_path}")
    command_env = os.environ.copy()
    if options.goos is not None:
        command_env["GOOS"] = options.goos
    if options.goarch is not None:
        command_env["GOARCH"] = options.goarch
    env_data = _run_json(
        [
            options.go_binary,
            "env",
            "-json",
            "GOOS",
            "GOARCH",
            "GOVERSION",
            "CGO_ENABLED",
            "CC",
            "CGO_CFLAGS",
            "CGO_CPPFLAGS",
            "CGO_LDFLAGS",
        ],
        cwd=root_path,
        env=command_env,
        timeout=options.timeout_seconds,
        label="go env",
    )
    if not isinstance(env_data, dict):
        raise ManifestDiscoveryError("go env did not return a JSON object")
    goos = _required_env(env_data, "GOOS")
    goarch = _required_env(env_data, "GOARCH")
    if str(env_data.get("CGO_ENABLED", "0")) != "1":
        raise ManifestDiscoveryError(
            f"CGO_ENABLED=0 for {goos}/{goarch}; no authoritative cgo binding set exists"
        )
    compiler = _required_env(env_data, "CC")
    compiler_version = _compiler_version(compiler, root_path, command_env, options)
    target_triple = _compiler_target(
        compiler,
        root_path,
        command_env,
        options,
        fallback=f"{goarch}-{goos}",
    )
    global_cflags = _normalize_flags(
        shlex.split(str(env_data.get("CGO_CFLAGS", ""))),
        root_path,
    )
    global_cppflags = _normalize_flags(
        shlex.split(str(env_data.get("CGO_CPPFLAGS", ""))),
        root_path,
    )
    global_ldflags = _normalize_flags(
        shlex.split(str(env_data.get("CGO_LDFLAGS", ""))),
        root_path,
    )
    global_macros = _extract_macros((*global_cflags, *global_cppflags))
    build = BuildContext(
        goos=goos,
        goarch=goarch,
        abi=_target_abi(goos, goarch, target_triple),
        toolchain=ToolchainIdentity(
            go_version=_required_env(env_data, "GOVERSION"),
            c_compiler=_compiler_identity(compiler),
            c_compiler_version=compiler_version,
        ),
        cgo_enabled=str(env_data.get("CGO_ENABLED", "0")) == "1",
        build_tags=options.build_tags,
        cgo_cflags=global_cflags,
        cgo_cppflags=global_cppflags,
        cgo_ldflags=global_ldflags,
        macros=global_macros,
    )
    list_command = [options.go_binary, "list", "-json"]
    if options.build_tags:
        list_command.append("-tags=" + ",".join(options.build_tags))
    list_command.append("./...")
    package_data = _run_json_stream(
        list_command,
        cwd=root_path,
        env=command_env,
        timeout=options.timeout_seconds,
        label="go list",
    )
    assembler = ManifestAssembler(build, generated_by="cgoprof manifest")
    intrinsic_provider = intrinsic_provider_record(build.toolchain.go_version)
    used_intrinsics = False
    for package_item in package_data:
        if not isinstance(package_item, dict):
            raise ManifestDiscoveryError("go list emitted a non-object package")
        cgo_files = tuple(str(item) for item in package_item.get("CgoFiles", []) or [])
        if not cgo_files:
            continue
        package, package_dir = _package_record(root_path, package_item)
        assembler.add_package(package)
        calls_by_symbol: dict[str, set[SourceLocation]] = {}
        directives_by_symbol: dict[str, set[CgoDirective]] = {}
        for filename in cgo_files:
            path = package_dir / filename
            if not path.is_file():
                raise ManifestDiscoveryError(f"go list named a missing cgo file: {path}")
            rel_path = path.relative_to(root_path).as_posix()
            text = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if c_identifier_may_be_shadowed(text):
                assembler.add_diagnostic(
                    ManifestDiagnostic(
                        DiagnosticSeverity.ERROR,
                        "shadowed_c_identifier",
                        f"{rel_path} may shadow the cgo pseudo-package name C; "
                        "references in this file were not resolved",
                        package.package_id,
                    )
                )
                continue
            references, directives = scan_cgo_references(text)
            for reference in references:
                calls_by_symbol.setdefault(reference.symbol, set()).add(
                    SourceLocation(
                        rel_path,
                        reference.line,
                        reference.column,
                        digest,
                    )
                )
            for directive_name, symbols in directives.items():
                directive = CgoDirective(directive_name)
                for symbol in symbols:
                    directives_by_symbol.setdefault(symbol, set()).add(directive)
        symbols = sorted({*calls_by_symbol, *directives_by_symbol})
        for symbol in symbols:
            intrinsic = intrinsic_manifest_api(symbol, intrinsic_provider.identity)
            uses = tuple(calls_by_symbol.get(symbol, ()))
            directives = tuple(directives_by_symbol.get(symbol, ()))
            if intrinsic is not None:
                used_intrinsics = True
                assembler.add_api(intrinsic)
                assembler.add_binding(
                    APIBinding(
                        package_id=package.package_id,
                        cgo_name=symbol,
                        api_id=intrinsic.api_id,
                        kind=BindingKind.CGO_INTRINSIC,
                        linkage=Linkage.INTRINSIC,
                        use_sites=uses,
                        directives=directives,
                    )
                )
                continue
            unresolved = UnresolvedBinding(
                package_id=package.package_id,
                cgo_name=symbol,
                reason=UnresolvedReason.MISSING_IDENTITY_COMPONENTS,
                use_sites=uses,
                directives=directives,
                detail=(
                    "provider and ABI-canonical C signature were not supplied by a "
                    "declaration frontend; exact api_id intentionally withheld"
                ),
            )
            assembler.add_unresolved(unresolved)
            assembler.add_diagnostic(
                ManifestDiagnostic(
                    DiagnosticSeverity.WARNING,
                    "unresolved_api_identity",
                    f"C.{symbol} has no proof-grade provider/signature identity",
                    unresolved.reference_id,
                )
            )
    if used_intrinsics:
        assembler.add_provider(intrinsic_provider)
    return assembler.build_manifest(
        metadata=(("discovery", "go-list+source-scan"),)
    )


def _package_record(
    root: Path,
    data: Mapping[str, Any],
) -> tuple[GoPackageRecord, Path]:
    import_path = str(data.get("ImportPath", ""))
    package_name = str(data.get("Name", ""))
    package_dir = Path(str(data.get("Dir", ""))).resolve()
    try:
        package_dir.relative_to(root)
    except ValueError as error:
        raise ManifestDiscoveryError(
            f"go list package {import_path!r} is outside project root"
        ) from error
    module = data.get("Module")
    if not isinstance(module, dict) or not module.get("Path"):
        raise ManifestDiscoveryError(
            f"cgo package {import_path!r} has no authoritative Go module identity"
        )
    file_names = _package_file_names(data)
    relative_files = tuple(
        (package_dir / name).relative_to(root).as_posix() for name in file_names
    )
    source_sha256 = _source_digest(root, relative_files)
    cflags = _normalize_flags(
        (str(item) for item in data.get("CgoCFLAGS", []) or []),
        root,
        package_dir,
    )
    cppflags = _normalize_flags(
        (str(item) for item in data.get("CgoCPPFLAGS", []) or []),
        root,
        package_dir,
    )
    ldflags = _normalize_flags(
        (str(item) for item in data.get("CgoLDFLAGS", []) or []),
        root,
        package_dir,
    )
    macros = _extract_macros((*cflags, *cppflags))
    package = GoPackageRecord(
        identity=GoPackageIdentity(import_path, str(module["Path"])),
        name=package_name,
        module_version=_optional_text(module.get("Version")),
        module_sum=_optional_text(module.get("Sum")),
        source_sha256=source_sha256,
        files=relative_files,
        cgo_cflags=cflags,
        cgo_cppflags=cppflags,
        cgo_ldflags=ldflags,
        macros=macros,
        metadata=(("module_main", str(bool(module.get("Main"))).lower()),),
    )
    return package, package_dir


def _package_file_names(data: Mapping[str, Any]) -> tuple[str, ...]:
    fields = (
        "GoFiles",
        "CgoFiles",
        "CFiles",
        "CXXFiles",
        "MFiles",
        "HFiles",
        "FFiles",
        "SFiles",
        "SwigFiles",
        "SwigCXXFiles",
        "SysoFiles",
    )
    names: set[str] = set()
    for field in fields:
        names.update(str(item) for item in data.get(field, []) or [])
    return tuple(sorted(names))


def _source_digest(root: Path, files: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for rel_path in sorted(files):
        path = root / rel_path
        if not path.is_file():
            raise ManifestDiscoveryError(f"go list named a missing source file: {path}")
        data = path.read_bytes()
        encoded_path = rel_path.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _target_abi(goos: str, goarch: str, target_triple: str) -> TargetABI:
    pointer_width = {
        "386": 32,
        "amd64": 64,
        "amd64p32": 32,
        "arm": 32,
        "arm64": 64,
        "loong64": 64,
        "mips": 32,
        "mips64": 64,
        "mips64le": 64,
        "mipsle": 32,
        "ppc64": 64,
        "ppc64le": 64,
        "riscv64": 64,
        "s390x": 64,
        "wasm": 32,
    }.get(goarch)
    big_endian = {"mips", "mips64", "ppc64", "s390x"}
    little_endian = {
        "386",
        "amd64",
        "amd64p32",
        "arm",
        "arm64",
        "loong64",
        "mipsle",
        "mips64le",
        "ppc64le",
        "riscv64",
        "wasm",
    }
    if goarch in big_endian:
        endianness = Endianness.BIG
    elif goarch in little_endian:
        endianness = Endianness.LITTLE
    else:
        endianness = Endianness.UNKNOWN
    data_model = None
    if pointer_width == 64:
        data_model = "LLP64" if goos == "windows" else "LP64"
    elif pointer_width == 32:
        data_model = "ILP32"
    return TargetABI(target_triple, pointer_width, endianness, data_model)


def _extract_macros(flags: Iterable[str]) -> tuple[MacroDefinition, ...]:
    items = list(flags)
    definitions: dict[str, str | None] = {}
    index = 0
    while index < len(items):
        item = items[index]
        raw: str | None = None
        if item == "-D":
            if index + 1 >= len(items):
                raise ManifestDiscoveryError("dangling -D in cgo flags")
            index += 1
            raw = items[index]
        elif item.startswith("-D") and len(item) > 2:
            raw = item[2:]
        if raw is not None:
            name, separator, value = raw.partition("=")
            macro_value = value if separator else None
            existing = definitions.get(name)
            if name in definitions and existing != macro_value:
                raise ManifestDiscoveryError(
                    f"conflicting definitions for C macro {name!r}"
                )
            definitions[name] = macro_value
        index += 1
    return tuple(MacroDefinition(name, value) for name, value in definitions.items())


def _run_json(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    label: str,
) -> Any:
    output = _run(command, cwd=cwd, env=env, timeout=timeout, label=label)
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise ManifestDiscoveryError(f"{label} emitted invalid JSON: {error}") from error


def _run_json_stream(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    label: str,
) -> list[Any]:
    output = _run(command, cwd=cwd, env=env, timeout=timeout, label=label)
    decoder = json.JSONDecoder()
    values = []
    position = 0
    while position < len(output):
        while position < len(output) and output[position].isspace():
            position += 1
        if position >= len(output):
            break
        try:
            value, position = decoder.raw_decode(output, position)
        except json.JSONDecodeError as error:
            raise ManifestDiscoveryError(f"{label} emitted invalid JSON: {error}") from error
        values.append(value)
    return values


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    label: str,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestDiscoveryError(f"{label} failed: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ManifestDiscoveryError(
            f"{label} failed with exit code {completed.returncode}: {detail}"
        )
    return completed.stdout


def _compiler_version(
    compiler: str,
    cwd: Path,
    env: Mapping[str, str],
    options: DiscoveryOptions,
) -> str | None:
    command = [*shlex.split(compiler), "--version"]
    try:
        output = _run(
            command,
            cwd=cwd,
            env=env,
            timeout=options.timeout_seconds,
            label="C compiler version query",
        )
    except ManifestDiscoveryError:
        return None
    return output.splitlines()[0].strip() if output.splitlines() else None


def _compiler_target(
    compiler: str,
    cwd: Path,
    env: Mapping[str, str],
    options: DiscoveryOptions,
    *,
    fallback: str,
) -> str:
    command = [*shlex.split(compiler), "-dumpmachine"]
    try:
        output = _run(
            command,
            cwd=cwd,
            env=env,
            timeout=options.timeout_seconds,
            label="C compiler target query",
        )
    except ManifestDiscoveryError:
        return fallback
    return output.strip() or fallback


def _compiler_identity(command: str) -> str:
    parts = shlex.split(command)
    if not parts:
        raise ManifestDiscoveryError("empty C compiler command")
    return " ".join((Path(parts[0]).name, *parts[1:]))


def _normalize_flags(
    flags: Iterable[str],
    root: Path,
    package_dir: Path | None = None,
) -> tuple[str, ...]:
    root_text = str(root.resolve())
    package_text = None if package_dir is None else str(package_dir.resolve())
    normalized = []
    for flag in flags:
        value = flag
        if package_text is not None:
            value = value.replace(package_text, "${PACKAGE}")
        value = value.replace(root_text, "${WORKSPACE}")
        normalized.append(value)
    return tuple(normalized)


def _required_env(data: Mapping[str, Any], name: str) -> str:
    value = str(data.get(name, "")).strip()
    if not value:
        raise ManifestDiscoveryError(f"go env returned no {name}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _insert_exact(
    items: dict[str, Any],
    key: str,
    value: Any,
    label: str,
) -> None:
    existing = items.get(key)
    if existing is not None and existing != value:
        raise ValueError(f"conflicting {label} records for {key}")
    items[key] = value


def _merge_metadata(
    left: tuple[tuple[str, str], ...],
    right: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    result = dict(left)
    for key, value in right:
        if key in result and result[key] != value:
            raise ValueError(f"conflicting binding metadata value for {key!r}")
        result[key] = value
    return tuple(sorted(result.items()))


def _merge_api_identity(left: APIIdentity, right: APIIdentity) -> APIIdentity:
    if left.identity_payload() != right.identity_payload():
        raise ValueError("cannot merge distinct API identities")
    left_signature = left.signature
    right_signature = right.signature
    return APIIdentity(
        provider=left.provider,
        symbol=left.symbol,
        kind=left.kind,
        linkage_name=left.linkage_name,
        signature=CFunctionSignature(
            result=_merge_c_type(left_signature.result, right_signature.result),
            parameters=tuple(
                _merge_c_type(left_type, right_type)
                for left_type, right_type in zip(
                    left_signature.parameters,
                    right_signature.parameters,
                )
            ),
            variadic=left_signature.variadic,
            calling_convention=left_signature.calling_convention,
            abi_tag=left_signature.abi_tag,
        ),
    )


def _merge_c_type(left: CTypeIdentity, right: CTypeIdentity) -> CTypeIdentity:
    if left.canonical != right.canonical:
        raise ValueError("cannot merge distinct canonical C types")
    size_bits = _merge_optional_layout(
        left.size_bits,
        right.size_bits,
        f"size for {left.canonical}",
    )
    alignment_bits = _merge_optional_layout(
        left.alignment_bits,
        right.alignment_bits,
        f"alignment for {left.canonical}",
    )
    return CTypeIdentity(
        canonical=left.canonical,
        size_bits=size_bits,
        alignment_bits=alignment_bits,
        source_spelling=left.source_spelling or right.source_spelling,
    )


def _merge_optional_layout(
    left: int | None,
    right: int | None,
    label: str,
) -> int | None:
    if left is not None and right is not None and left != right:
        raise ValueError(f"conflicting {label}: {left} != {right}")
    return left if left is not None else right
