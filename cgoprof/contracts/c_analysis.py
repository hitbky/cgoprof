from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .evidence import Evidence, EvidenceKind, FactStatus
from .identity import (
    APIIdentity,
    APIKind,
    BuildContext,
    CallingConvention,
    CFunctionSignature,
    CTypeIdentity,
    Linkage,
    ProviderIdentity,
    ProviderKind,
    normalize_c_type,
)
from .lattice import merge_facts
from .manifest import (
    APIBinding,
    APIDeclaration,
    APIManifest,
    BindingKind,
    CgoDirective,
    DiagnosticSeverity,
    ManifestAPI,
    ManifestDiagnostic,
    ProviderRecord,
    ProviderArtifact,
    SourceLocation,
    UnresolvedReason,
)
from .manifest_builder import ManifestAssembler
from .manifest_store import ManifestIndex
from .merge import merge_contracts
from .preamble import extract_cgo_preambles
from .model import (
    APIContract,
    BuildScope,
    Callback,
    ContractAttribute,
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


class CAnalysisError(RuntimeError):
    pass


class AnalysisSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class AnalysisDiagnostic:
    severity: AnalysisSeverity
    code: str
    message: str
    function: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class CFrontendOptions:
    clang_binary: str = "clang"
    flags: tuple[str, ...] = ()
    language: str = "c"
    target_triple: str | None = None
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.clang_binary.strip():
            raise ValueError("Clang binary must not be blank")
        if self.language not in {"c", "c++", "objective-c", "objective-c++"}:
            raise ValueError(f"unsupported Clang language: {self.language!r}")
        if self.timeout_seconds <= 0:
            raise ValueError("Clang timeout must be positive")
        if any(not item or "\x00" in item for item in self.flags):
            raise ValueError("Clang flags must be non-empty and contain no NUL")
        object.__setattr__(self, "flags", tuple(self.flags))


@dataclass(frozen=True)
class CParameterInfo:
    index: int
    name: str
    source_type: str
    canonical_type: CTypeIdentity


@dataclass(frozen=True)
class CFunctionInfo:
    symbol: str
    linkage_name: str
    signature: CFunctionSignature
    parameters: tuple[CParameterInfo, ...]
    declaration_locations: tuple[SourceLocation, ...]
    definition_location: SourceLocation | None
    storage_class: str | None = None
    has_definition: bool = False
    variadic: bool = False
    ast_id: str = field(default="", compare=False)

    @property
    def internal_linkage(self) -> bool:
        return self.storage_class == "static"


@dataclass(frozen=True)
class ParameterEffect:
    memory_access: MemoryAccess
    ownership: Ownership
    lifetime: Lifetime
    escape: Escape
    mutability: Mutability
    representation: Representation
    complete: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultEffect:
    ownership: Ownership
    lifetime: Lifetime
    escape: Escape
    mutability: Mutability
    representation: Representation
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CFunctionSummary:
    function: CFunctionInfo
    parameters: tuple[ParameterEffect, ...]
    result: ResultEffect
    callback: Callback
    complete: bool
    direct_calls: tuple[str, ...] = ()
    diagnostics: tuple[AnalysisDiagnostic, ...] = ()


@dataclass(frozen=True)
class CAnalysisResult:
    functions: tuple[CFunctionInfo, ...]
    summaries: tuple[CFunctionSummary, ...]
    diagnostics: tuple[AnalysisDiagnostic, ...]
    source_digest: str
    compiler: str
    compiler_version: str
    target_triple: str
    flags: tuple[str, ...]

    def summary_for(self, symbol: str) -> CFunctionSummary:
        candidates = [item for item in self.summaries if item.function.symbol == symbol]
        if len(candidates) != 1:
            raise LookupError(
                f"expected one analyzed summary for {symbol!r}, found {len(candidates)}"
            )
        return candidates[0]


@dataclass(frozen=True)
class PackageCAnalysis:
    analyses: tuple[CAnalysisResult, ...]
    diagnostics: tuple[AnalysisDiagnostic, ...] = ()


@dataclass
class _MutableEffect:
    read: bool = False
    write: bool = False
    escape_level: int = 0  # 0=no evidence, 1=may, 2=known escape
    freed: bool = False
    complete: bool = True
    c_string: bool = False
    reasons: set[str] = field(default_factory=set)

    def merge(self, other: "_MutableEffect", reason: str | None = None) -> bool:
        before = self.snapshot()
        self.read |= other.read
        self.write |= other.write
        self.escape_level = max(self.escape_level, other.escape_level)
        self.freed |= other.freed
        self.complete &= other.complete
        self.c_string |= other.c_string
        self.reasons.update(other.reasons)
        if reason:
            self.reasons.add(reason)
        return before != self.snapshot()

    def snapshot(self) -> tuple[Any, ...]:
        return (
            self.read,
            self.write,
            self.escape_level,
            self.freed,
            self.complete,
            self.c_string,
            tuple(sorted(self.reasons)),
        )


@dataclass(frozen=True)
class _CallSite:
    callee: str | None
    argument_aliases: tuple[frozenset[int], ...]
    argument_pointer_like: tuple[bool, ...]
    returned: bool = False


@dataclass
class _FunctionState:
    info: CFunctionInfo
    effects: list[_MutableEffect]
    body: Mapping[str, Any] | None
    aliases: dict[str, set[int]]
    local_ids: set[str]
    global_ids: set[str]
    calls: list[_CallSite] = field(default_factory=list)
    callback: Callback | None = None
    callback_complete: bool = True
    complete: bool = True
    result_allocator: bool = False
    result_global: bool = False
    returned_parameters: set[int] = field(default_factory=set)
    allocated_locals: set[str] = field(default_factory=set)
    diagnostics: list[AnalysisDiagnostic] = field(default_factory=list)


@dataclass(frozen=True)
class _KnownCall:
    reads: tuple[int, ...] = ()
    writes: tuple[int, ...] = ()
    frees: tuple[int, ...] = ()
    escapes: tuple[int, ...] = ()
    c_strings: tuple[int, ...] = ()
    callback: Callback | None = None
    callback_arguments: tuple[int, ...] = ()
    allocator: bool = False


_KNOWN_CALLS: dict[str, _KnownCall] = {
    "memcpy": _KnownCall(reads=(1,), writes=(0,)),
    "memmove": _KnownCall(reads=(1,), writes=(0,)),
    "memcmp": _KnownCall(reads=(0, 1)),
    "memset": _KnownCall(writes=(0,)),
    "strlen": _KnownCall(reads=(0,), c_strings=(0,)),
    "strnlen": _KnownCall(reads=(0,), c_strings=(0,)),
    "strcmp": _KnownCall(reads=(0, 1), c_strings=(0, 1)),
    "strncmp": _KnownCall(reads=(0, 1), c_strings=(0, 1)),
    "strcpy": _KnownCall(reads=(1,), writes=(0,), c_strings=(0, 1)),
    "strncpy": _KnownCall(reads=(1,), writes=(0,), c_strings=(1,)),
    "strdup": _KnownCall(reads=(0,), c_strings=(0,), allocator=True),
    "free": _KnownCall(frees=(0,)),
    "malloc": _KnownCall(allocator=True),
    "calloc": _KnownCall(allocator=True),
    "realloc": _KnownCall(reads=(0,), frees=(0,), allocator=True),
    "qsort": _KnownCall(reads=(0,), writes=(0,), callback=Callback.SYNCHRONOUS, callback_arguments=(3,)),
    "bsearch": _KnownCall(reads=(0, 1), callback=Callback.SYNCHRONOUS, callback_arguments=(4,)),
    "pthread_create": _KnownCall(
        writes=(0,),
        escapes=(2, 3),
        callback=Callback.ASYNCHRONOUS,
        callback_arguments=(2,),
    ),
}


class ClangContractAnalyzer:
    """Clang JSON-AST signature and interprocedural C effect analyzer."""

    def __init__(
        self,
        root: str | Path,
        build: BuildContext,
        options: CFrontendOptions | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"analysis root is not a directory: {self.root}")
        self.build = build
        self.options = options or CFrontendOptions(
            clang_binary=build.toolchain.c_compiler.split()[0],
            target_triple=build.abi.target_triple,
        )

    def analyze_file(
        self,
        path: str | Path,
        *,
        symbols: Iterable[str] | None = None,
        extra_flags: Sequence[str] = (),
    ) -> CAnalysisResult:
        source_path = Path(path)
        if not source_path.is_absolute():
            source_path = self.root / source_path
        source_path = source_path.resolve()
        if not source_path.is_file():
            raise CAnalysisError(f"C translation unit does not exist: {source_path}")
        source = source_path.read_bytes()
        return self._analyze(
            source_path,
            source,
            symbols=None if symbols is None else set(symbols),
            extra_flags=tuple(extra_flags),
        )

    def analyze_source(
        self,
        source: str,
        *,
        display_path: str = ".cgoprof/preamble.c",
        symbols: Iterable[str] | None = None,
        extra_flags: Sequence[str] = (),
    ) -> CAnalysisResult:
        relative = Path(display_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("synthetic C display path must be workspace-relative")
        suffix = ".cc" if "++" in self.options.language else ".c"
        with tempfile.TemporaryDirectory(prefix="cgoprof-clang-") as temp:
            path = Path(temp) / ("translation-unit" + suffix)
            path.write_text(source, encoding="utf-8")
            return self._analyze(
                path,
                source.encode("utf-8"),
                symbols=None if symbols is None else set(symbols),
                extra_flags=tuple(extra_flags),
                display_path=relative.as_posix(),
            )

    def _analyze(
        self,
        source_path: Path,
        source: bytes,
        *,
        symbols: set[str] | None,
        extra_flags: tuple[str, ...],
        display_path: str | None = None,
    ) -> CAnalysisResult:
        flags = (*self.options.flags, *extra_flags)
        ast, stderr = self._run_ast(source_path, flags)
        compiler_version = self._compiler_query("--version").splitlines()[0]
        target = self.options.target_triple or self._compiler_query("-dumpmachine").strip()
        typedefs = _collect_typedefs(ast)
        complete_records = _collect_complete_records(ast)
        nodes = _collect_reachable_function_nodes(ast, symbols)
        layout_types = _source_types(nodes)
        layouts, layout_diagnostics = self._probe_layouts(
            source_path, flags, layout_types
        )
        functions, function_nodes, extraction_diagnostics = _extract_functions(
            nodes,
            typedefs,
            layouts,
            self.build,
            self.root,
            source_path,
            source,
            display_path,
            target,
        )
        states = _build_states(functions, function_nodes, complete_records)
        _analyze_direct_effects(states)
        _propagate_interprocedural(states)
        summaries = tuple(
            _finalize_summary(state, complete_records)
            for state in sorted(
                states.values(),
                key=lambda item: (
                    item.info.symbol,
                    item.info.signature.signature_id,
                    item.info.linkage_name,
                ),
            )
        )
        diagnostics = tuple(
            sorted(
                (
                    *layout_diagnostics,
                    *extraction_diagnostics,
                    *(item for state in states.values() for item in state.diagnostics),
                    *(
                        (
                            AnalysisDiagnostic(
                                AnalysisSeverity.INFO,
                                "clang_stderr",
                                stderr.strip(),
                            ),
                        )
                        if stderr.strip()
                        else ()
                    ),
                ),
                key=_diagnostic_key,
            )
        )
        digest = hashlib.sha256()
        digest.update(source)
        digest.update(b"\0")
        digest.update(json.dumps(flags, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(compiler_version.encode("utf-8"))
        digest.update(b"\0")
        digest.update(target.encode("utf-8"))
        digest.update(b"\0")
        digest.update(self.options.language.encode("utf-8"))
        return CAnalysisResult(
            functions=tuple(sorted(functions, key=lambda item: (item.symbol, item.signature.signature_id))),
            summaries=summaries,
            diagnostics=diagnostics,
            source_digest=digest.hexdigest(),
            compiler=self.options.clang_binary,
            compiler_version=compiler_version,
            target_triple=target,
            flags=tuple(flags),
        )

    def _command(self, source_path: Path, flags: Sequence[str]) -> list[str]:
        command = [self.options.clang_binary]
        if self.options.target_triple:
            command.append(f"--target={self.options.target_triple}")
        command.extend(("-x", self.options.language))
        command.extend(flags)
        command.extend(("-Xclang", "-ast-dump=json", "-fsyntax-only", str(source_path)))
        return command

    def _run_ast(
        self, source_path: Path, flags: Sequence[str]
    ) -> tuple[Mapping[str, Any], str]:
        command = self._command(source_path, flags)
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env=os.environ.copy(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.options.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CAnalysisError(f"Clang AST extraction failed: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise CAnalysisError(
                f"Clang rejected {source_path} (exit {completed.returncode}): {detail}"
            )
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CAnalysisError(f"Clang emitted malformed AST JSON: {error}") from error
        if not isinstance(data, dict):
            raise CAnalysisError("Clang AST root is not an object")
        return data, completed.stderr

    def _compiler_query(self, flag: str) -> str:
        try:
            completed = subprocess.run(
                [self.options.clang_binary, flag],
                cwd=self.root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.options.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CAnalysisError(f"Clang identity query failed: {error}") from error
        if completed.returncode != 0:
            raise CAnalysisError(
                f"Clang identity query failed: {completed.stderr.strip()}"
            )
        return completed.stdout.strip()

    def _probe_layouts(
        self,
        source_path: Path,
        flags: Sequence[str],
        source_types: tuple[str, ...],
    ) -> tuple[dict[str, tuple[int, int]], tuple[AnalysisDiagnostic, ...]]:
        probe_types = [item for item in source_types if _layout_probe_supported(item)]
        if not probe_types:
            return {}, ()
        escaped = str(source_path).replace("\\", "\\\\").replace('"', '\\"')
        lines = [f'#include "{escaped}"']
        for index, source_type in enumerate(probe_types):
            lines.append(
                "enum { "
                f"__cgoprof_size_{index}=sizeof({source_type})*8, "
                f"__cgoprof_align_{index}=_Alignof({source_type})*8 "
                "};"
            )
        suffix = ".cc" if "++" in self.options.language else ".c"
        with tempfile.TemporaryDirectory(prefix="cgoprof-layout-") as temp:
            probe_path = Path(temp) / ("layout" + suffix)
            probe_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            try:
                ast, _ = self._run_ast(probe_path, flags)
            except CAnalysisError as error:
                return {}, (
                    AnalysisDiagnostic(
                        AnalysisSeverity.WARNING,
                        "layout_probe_failed",
                        str(error),
                    ),
                )
        values: dict[str, int] = {}
        for node in _walk(ast):
            name = node.get("name")
            if node.get("kind") == "EnumConstantDecl" and isinstance(name, str):
                if name.startswith(("__cgoprof_size_", "__cgoprof_align_")):
                    value = _constant_value(node)
                    if value is not None:
                        values[name] = value
        result: dict[str, tuple[int, int]] = {}
        diagnostics: list[AnalysisDiagnostic] = []
        for index, source_type in enumerate(probe_types):
            size = values.get(f"__cgoprof_size_{index}")
            alignment = values.get(f"__cgoprof_align_{index}")
            if size is None or alignment is None:
                diagnostics.append(
                    AnalysisDiagnostic(
                        AnalysisSeverity.WARNING,
                        "layout_value_missing",
                        f"Clang did not constant-fold layout for {source_type!r}",
                    )
                )
            else:
                result[source_type] = (size, alignment)
        return result, tuple(diagnostics)


def c_analysis_to_dict(result: CAnalysisResult) -> dict[str, Any]:
    return {
        "compiler": result.compiler,
        "compiler_version": result.compiler_version,
        "diagnostics": [_analysis_diagnostic_data(item) for item in result.diagnostics],
        "flags": list(result.flags),
        "functions": [
            {
                "definition_location": (
                    None
                    if item.definition_location is None
                    else item.definition_location.payload()
                ),
                "has_definition": item.has_definition,
                "internal_linkage": item.internal_linkage,
                "linkage_name": item.linkage_name,
                "parameters": [
                    {
                        "canonical_type": parameter.canonical_type.record_payload(),
                        "index": parameter.index,
                        "name": parameter.name,
                        "source_type": parameter.source_type,
                    }
                    for parameter in item.parameters
                ],
                "signature": item.signature.record_payload(),
                "signature_id": item.signature.signature_id,
                "storage_class": item.storage_class,
                "symbol": item.symbol,
            }
            for item in result.functions
        ],
        "source_digest": result.source_digest,
        "summaries": [
            {
                "callback": item.callback.value,
                "complete": item.complete,
                "diagnostics": [
                    _analysis_diagnostic_data(diagnostic)
                    for diagnostic in item.diagnostics
                ],
                "direct_calls": list(item.direct_calls),
                "parameters": [
                    {
                        "complete": effect.complete,
                        "escape": effect.escape.value,
                        "lifetime": effect.lifetime.value,
                        "memory_access": effect.memory_access.value,
                        "mutability": effect.mutability.value,
                        "ownership": effect.ownership.value,
                        "reasons": list(effect.reasons),
                        "representation": _representation_data(effect.representation),
                    }
                    for effect in item.parameters
                ],
                "result": {
                    "escape": item.result.escape.value,
                    "lifetime": item.result.lifetime.value,
                    "mutability": item.result.mutability.value,
                    "ownership": item.result.ownership.value,
                    "reasons": list(item.result.reasons),
                    "representation": _representation_data(
                        item.result.representation
                    ),
                },
                "signature_id": item.function.signature.signature_id,
                "symbol": item.function.symbol,
            }
            for item in result.summaries
        ],
        "target_triple": result.target_triple,
    }


def dumps_c_analysis(result: CAnalysisResult, *, indent: int = 2) -> str:
    return json.dumps(c_analysis_to_dict(result), indent=indent, sort_keys=True) + "\n"


def augment_manifest_with_c_analysis(
    manifest: APIManifest,
    analyses: Sequence[CAnalysisResult],
    provider: ProviderRecord,
    go_package: str,
    *,
    symbols: Iterable[str] | None = None,
    definitions_only: bool = False,
) -> APIManifest:
    """Resolve package-local selectors using exact analyzed signatures/provider."""

    package = next(
        (item for item in manifest.packages if item.identity.import_path == go_package),
        None,
    )
    if package is None:
        raise KeyError(f"unknown Go package {go_package!r}")
    requested = None if symbols is None else set(symbols)
    unresolved_by_name = {
        item.cgo_name: item
        for item in manifest.unresolved
        if item.package_id == package.package_id
        and (requested is None or item.cgo_name in requested)
    }
    if not unresolved_by_name:
        return manifest
    candidates: dict[str, dict[str, CFunctionInfo]] = {}
    for analysis in analyses:
        for function in analysis.functions:
            if function.symbol not in unresolved_by_name:
                continue
            if definitions_only and not function.has_definition:
                continue
            candidate_key = (
                function.signature.signature_id + ":" + function.linkage_name
            )
            existing = candidates.setdefault(function.symbol, {}).get(candidate_key)
            if existing is None or (function.has_definition and not existing.has_definition):
                candidates[function.symbol][candidate_key] = function

    assembler = ManifestAssembler(manifest.build, generated_by="cgoprof c-contract")
    for item in manifest.packages:
        assembler.add_package(item)
    for item in manifest.providers:
        assembler.add_provider(item)
    assembler.add_provider(provider)
    for item in manifest.apis:
        assembler.add_api(item)
    for item in manifest.bindings:
        assembler.add_binding(item)

    resolved_reference_ids: set[str] = set()
    for name, unresolved in sorted(unresolved_by_name.items()):
        by_signature = candidates.get(name, {})
        if len(by_signature) != 1:
            reason = (
                UnresolvedReason.MISSING_SIGNATURE
                if not by_signature
                else UnresolvedReason.AMBIGUOUS_CANDIDATE
            )
            candidate_ids = tuple(
                APIIdentity(
                    provider.identity,
                    name,
                    item.signature,
                    linkage_name=item.linkage_name,
                ).api_id
                for item in by_signature.values()
            )
            assembler.add_unresolved(
                replace(
                    unresolved,
                    reason=reason,
                    candidate_api_ids=candidate_ids,
                    detail=(
                        "Clang found no declaration"
                        if not by_signature
                        else "Clang found multiple ABI-distinct declarations"
                    ),
                )
            )
            for function in by_signature.values():
                assembler.add_api(_manifest_api(function, provider.identity))
            assembler.add_diagnostic(
                ManifestDiagnostic(
                    DiagnosticSeverity.WARNING,
                    "c_signature_unresolved",
                    f"C.{name} has {len(by_signature)} ABI signature candidates",
                    unresolved.reference_id,
                )
            )
            continue
        function = next(iter(by_signature.values()))
        api = _manifest_api(function, provider.identity)
        assembler.add_api(api)
        assembler.add_binding(
            APIBinding(
                package_id=package.package_id,
                cgo_name=name,
                api_id=api.api_id,
                kind=(
                    BindingKind.STATIC_FUNCTION
                    if function.internal_linkage
                    else BindingKind.DIRECT
                ),
                linkage=(
                    Linkage.INTERNAL if function.internal_linkage else Linkage.EXTERNAL
                ),
                use_sites=unresolved.use_sites,
                declaration_sites=function.declaration_locations,
                directives=unresolved.directives,
                metadata=(
                    ("c_frontend", "clang-json-ast"),
                    ("signature_id", function.signature.signature_id),
                ),
            )
        )
        resolved_reference_ids.add(unresolved.reference_id)

    selected = set(unresolved_by_name)
    for item in manifest.unresolved:
        if item.package_id != package.package_id or item.cgo_name not in selected:
            assembler.add_unresolved(item)
    for diagnostic in manifest.diagnostics:
        if diagnostic.subject_id not in resolved_reference_ids:
            assembler.add_diagnostic(diagnostic)
    return assembler.build_manifest(
        metadata=tuple(
            sorted(
                {
                    **dict(manifest.metadata),
                    "c_signature_frontend": "clang-json-ast",
                }.items()
            )
        )
    )


def analyze_package_translation_units(
    root: str | Path,
    manifest: APIManifest,
    go_package: str,
    *,
    options: CFrontendOptions | None = None,
    source_paths: Sequence[str | Path] = (),
    include_preambles: bool = True,
) -> PackageCAnalysis:
    """Analyze build-selected package C/C++ units and cgo preambles.

    Translation units are kept separate, matching cgo compilation semantics.
    A failed unit becomes an explicit diagnostic and never turns into a safe
    empty summary.
    """

    root_path = Path(root).resolve()
    package = next(
        (item for item in manifest.packages if item.identity.import_path == go_package),
        None,
    )
    if package is None:
        raise KeyError(f"unknown Go package {go_package!r}")
    package_files = tuple(root_path / item for item in package.files)
    package_dir = (
        package_files[0].parent if package_files else root_path
    )
    flags = tuple(
        _expand_manifest_flag(item, root_path, package_dir)
        for item in (*manifest.build.cgo_cppflags, *manifest.build.cgo_cflags, *package.cgo_cppflags, *package.cgo_cflags)
    )
    flags = (*flags, f"-I{package_dir}")
    target_symbols = {
        item.cgo_name
        for item in manifest.unresolved
        if item.package_id == package.package_id
    }
    selected_paths = [Path(item) for item in source_paths]
    if not selected_paths:
        selected_paths = [
            Path(item)
            for item in package.files
            if Path(item).suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".m", ".mm"}
        ]
    analyses: list[CAnalysisResult] = []
    diagnostics: list[AnalysisDiagnostic] = []
    for path in selected_paths:
        try:
            source_options = _frontend_options_for_path(options, path)
            analyzer = ClangContractAnalyzer(root_path, manifest.build, source_options)
            analyses.append(
                analyzer.analyze_file(path, symbols=target_symbols, extra_flags=flags)
            )
        except CAnalysisError as error:
            diagnostics.append(
                AnalysisDiagnostic(
                    AnalysisSeverity.ERROR,
                    "translation_unit_failed",
                    str(error),
                    location=str(path),
                )
            )
    if include_preambles:
        preamble_options = _frontend_options_for_path(options, Path("preamble.c"))
        preamble_analyzer = ClangContractAnalyzer(
            root_path, manifest.build, preamble_options
        )
        for relative in package.files:
            if Path(relative).suffix != ".go":
                continue
            path = root_path / relative
            text = path.read_text(encoding="utf-8")
            for ordinal, preamble in enumerate(extract_cgo_preambles(text), start=1):
                display_path = (
                    f".cgoprof/preambles/{Path(relative).as_posix().replace('/', '_')}-"
                    f"{preamble.import_line}-{ordinal}.c"
                )
                try:
                    analyses.append(
                        preamble_analyzer.analyze_source(
                            preamble.source,
                            display_path=display_path,
                            symbols=target_symbols,
                            extra_flags=flags,
                        )
                    )
                except CAnalysisError as error:
                    diagnostics.append(
                        AnalysisDiagnostic(
                            AnalysisSeverity.ERROR,
                            "preamble_failed",
                            str(error),
                            location=f"{relative}:{preamble.import_line}",
                        )
                    )
    return PackageCAnalysis(
        tuple(analyses), tuple(sorted(diagnostics, key=_diagnostic_key))
    )


def local_package_provider(
    manifest: APIManifest,
    go_package: str,
) -> ProviderRecord:
    """Create an exact source-bundle provider for package-defined C bodies."""

    package = next(
        (item for item in manifest.packages if item.identity.import_path == go_package),
        None,
    )
    if package is None:
        raise KeyError(f"unknown Go package {go_package!r}")
    if package.source_sha256 is None:
        raise ValueError("package has no source digest for a local provider release")
    return ProviderRecord(
        identity=ProviderIdentity(
            ProviderKind.GO_PACKAGE_LOCAL,
            package.identity.module_path,
            package.identity.import_path,
        ),
        version=package.module_version or f"source-{package.source_sha256[:16]}",
        abi_version=manifest.build.abi.target_triple,
        artifacts=(
            ProviderArtifact(
                "source_bundle",
                package.identity.import_path,
                package.source_sha256,
            ),
        ),
        metadata=(("package_id", package.package_id),),
    )


def contracts_from_c_analysis(
    manifest: APIManifest,
    analyses: Sequence[CAnalysisResult],
    go_package: str,
) -> tuple[APIContract, ...]:
    """Generate signature/body/directive contracts for exact package bindings."""

    index = ManifestIndex(manifest)
    package = next(
        (item for item in manifest.packages if item.identity.import_path == go_package),
        None,
    )
    if package is None:
        raise KeyError(f"unknown Go package {go_package!r}")
    summaries: dict[tuple[str, str, str], tuple[CFunctionSummary, CAnalysisResult]] = {}
    for analysis in analyses:
        for summary in analysis.summaries:
            key = (
                summary.function.symbol,
                summary.function.signature.signature_id,
                summary.function.linkage_name,
            )
            existing = summaries.get(key)
            if existing is None or (
                summary.function.has_definition
                and not existing[0].function.has_definition
            ):
                summaries[key] = (summary, analysis)
    contracts: list[APIContract] = []
    for binding in manifest.bindings:
        if binding.package_id != package.package_id:
            continue
        api = index.require_api(binding.api_id)
        key = (
            api.identity.symbol,
            api.identity.signature.signature_id,
            api.identity.linkage_name or api.identity.symbol,
        )
        selected = summaries.get(key)
        if selected is None:
            continue
        summary, analysis = selected
        contract = _summary_contract(manifest, go_package, binding, summary, analysis)
        directive_contract = _directive_contract(
            manifest, go_package, binding, summary.function
        )
        if directive_contract is not None:
            contract = merge_contracts(contract, directive_contract).contract
        contracts.append(contract)
    return tuple(sorted(contracts, key=lambda item: item.api_id))


def _manifest_api(function: CFunctionInfo, provider: ProviderIdentity) -> ManifestAPI:
    identity = APIIdentity(
        provider=provider,
        symbol=function.symbol,
        signature=function.signature,
        kind=APIKind.FUNCTION,
        linkage_name=function.linkage_name,
    )
    declarations = tuple(
        APIDeclaration(
            location,
            _declaration_spelling(function),
            header=location.path if location.path.endswith((".h", ".hpp")) else None,
        )
        for location in function.declaration_locations
    )
    return ManifestAPI(
        identity,
        declarations,
        metadata=(
            ("definition", str(function.has_definition).lower()),
            ("frontend", "clang-json-ast"),
        ),
    )


def _summary_contract(
    manifest: APIManifest,
    go_package: str,
    binding: APIBinding,
    summary: CFunctionSummary,
    analysis: CAnalysisResult,
) -> APIContract:
    api = ManifestIndex(manifest).require_api(binding.api_id)
    provider = next(
        item
        for item in manifest.providers
        if item.provider_id == api.identity.provider.provider_id
    )
    scope = _build_scope(manifest, provider, go_package)
    signature_evidence = Evidence(
        EvidenceKind.C_SIGNATURE,
        f"Clang {analysis.compiler_version}",
        detail=(
            f"ABI-canonical signature {summary.function.signature.signature_id}; "
            f"target={analysis.target_triple}"
        ),
        location=_location_text(summary.function.declaration_locations[0]),
    )
    body_location = summary.function.definition_location
    body_evidence = Evidence(
        EvidenceKind.C_BODY_ANALYSIS,
        f"CGOProf Clang AST analysis {analysis.source_digest}",
        detail="conservative local may-alias effects plus fixed-point direct-call propagation",
        location=None if body_location is None else _location_text(body_location),
    )
    parameters: list[ParameterContract] = []
    for info, effect in zip(summary.function.parameters, summary.parameters):
        parameter_evidence = replace(
            body_evidence,
            detail=(
                body_evidence.detail
                + "; reasons="
                + (" | ".join(effect.reasons) if effect.reasons else "no reachable effect")
            ),
        )
        signature_representation = _type_representation(info.canonical_type)
        signature_value = ValueContract(
            representation=ContractFact(
                signature_representation,
                FactStatus.DECLARED,
                (signature_evidence,),
            )
        )
        effect_value = ValueContract(
            memory_access=ContractFact(
                effect.memory_access, FactStatus.PROVEN, (parameter_evidence,)
            ),
            ownership=_optional_proven_fact(effect.ownership, parameter_evidence),
            lifetime=_optional_proven_fact(effect.lifetime, parameter_evidence),
            escape=ContractFact(effect.escape, FactStatus.PROVEN, (parameter_evidence,)),
            mutability=_optional_proven_fact(effect.mutability, parameter_evidence),
            representation=(
                ContractFact(Representation.unknown())
                if effect.representation in {
                    Representation.unknown(),
                    signature_representation,
                }
                else ContractFact(
                    effect.representation, FactStatus.PROVEN, (parameter_evidence,)
                )
            ),
        )
        parameters.append(
            ParameterContract(
                info.index,
                info.name,
                info.canonical_type.canonical,
                _merge_values(signature_value, effect_value),
            )
        )
    result_signature_representation = _type_representation(
        summary.function.signature.result
    )
    result_signature = ValueContract(
        representation=ContractFact(
            result_signature_representation,
            FactStatus.DECLARED,
            (signature_evidence,),
        )
    )
    result_evidence = replace(
        body_evidence,
        detail=(
            body_evidence.detail
            + "; result_reasons="
            + (" | ".join(summary.result.reasons) if summary.result.reasons else "direct value result")
        ),
    )
    result_effect = ValueContract(
        memory_access=ContractFact(MemoryAccess.NONE, FactStatus.PROVEN, (result_evidence,)),
        ownership=_optional_proven_fact(summary.result.ownership, result_evidence),
        lifetime=_optional_proven_fact(summary.result.lifetime, result_evidence),
        escape=_optional_proven_fact(summary.result.escape, result_evidence),
        mutability=_optional_proven_fact(summary.result.mutability, result_evidence),
        representation=(
            ContractFact(Representation.unknown())
            if summary.result.representation in {
                Representation.unknown(),
                result_signature_representation,
            }
            else ContractFact(
                summary.result.representation, FactStatus.PROVEN, (result_evidence,)
            )
        ),
    )
    return APIContract(
        api_id=api.api_id,
        c_symbol=api.identity.symbol,
        scope=scope,
        parameters=tuple(parameters),
        result=ResultContract(
            summary.function.signature.result.canonical,
            _merge_values(result_signature, result_effect),
        ),
        callback=ContractFact(
            summary.callback,
            FactStatus.PROVEN,
            (
                replace(
                    body_evidence,
                    detail=(
                        body_evidence.detail
                        + f"; callback={summary.callback.value}; direct_calls="
                        + (",".join(summary.direct_calls) or "none")
                    ),
                ),
            ),
        ),
        diagnostics=tuple(
            sorted(
                {
                    f"{item.code}: {item.message}"
                    for item in summary.diagnostics
                    if item.severity != AnalysisSeverity.INFO
                }
            )
        ),
        metadata=(
            ("analysis_complete", str(summary.complete).lower()),
            ("c_analysis_digest", analysis.source_digest),
            ("contract_source", "c_signature+c_body_analysis"),
        ),
    )


def _directive_contract(
    manifest: APIManifest,
    go_package: str,
    binding: APIBinding,
    function: CFunctionInfo,
) -> APIContract | None:
    if not binding.directives:
        return None
    api = ManifestIndex(manifest).require_api(binding.api_id)
    provider = next(
        item
        for item in manifest.providers
        if item.provider_id == api.identity.provider.provider_id
    )
    evidence = Evidence(
        EvidenceKind.CGO_DIRECTIVE,
        f"cgo directives on {go_package}.C.{binding.cgo_name}",
        detail=", ".join(item.value for item in binding.directives),
        location=(
            None
            if not binding.declaration_sites
            else _location_text(binding.declaration_sites[0])
        ),
    )
    noescape = CgoDirective.NOESCAPE in binding.directives
    parameters = tuple(
        ParameterContract(
            item.index,
            item.name,
            item.canonical_type.canonical,
            ValueContract(
                escape=(
                    ContractFact(Escape.NO_ESCAPE, FactStatus.DECLARED, (evidence,))
                    if noescape and _is_pointer_type(item.canonical_type.canonical)
                    else ContractFact(Escape.UNKNOWN)
                )
            ),
        )
        for item in function.parameters
    )
    callback = (
        ContractFact(Callback.NO_CALLBACK, FactStatus.DECLARED, (evidence,))
        if CgoDirective.NOCALLBACK in binding.directives
        else ContractFact(Callback.UNKNOWN)
    )
    return APIContract(
        api.api_id,
        api.identity.symbol,
        _build_scope(manifest, provider, go_package),
        parameters,
        ResultContract(function.signature.result.canonical),
        callback,
        metadata=(("contract_source", "cgo_directive"),),
    )


def _build_scope(
    manifest: APIManifest, provider: ProviderRecord, go_package: str
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


def _collect_typedefs(ast: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in _walk(ast):
        if node.get("kind") != "TypedefDecl" or node.get("isImplicit"):
            continue
        name = node.get("name")
        type_data = node.get("type")
        if isinstance(name, str) and isinstance(type_data, dict):
            spelling = type_data.get("desugaredQualType") or type_data.get("qualType")
            if isinstance(spelling, str) and spelling != name:
                result[name] = spelling
    return result


def _collect_complete_records(ast: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for node in _walk(ast):
        if node.get("kind") in {"RecordDecl", "CXXRecordDecl"} and node.get(
            "completeDefinition"
        ):
            name = node.get("name")
            tag = node.get("tagUsed", "struct")
            if isinstance(name, str):
                result.add(f"{tag} {name}")
    return result


def _collect_function_nodes(
    ast: Mapping[str, Any], symbols: set[str] | None
) -> tuple[Mapping[str, Any], ...]:
    result = []
    for node in _walk(ast):
        if node.get("kind") not in {"FunctionDecl", "CXXMethodDecl"}:
            continue
        if node.get("isImplicit"):
            continue
        name = node.get("name")
        if not isinstance(name, str) or (symbols is not None and name not in symbols):
            continue
        result.append(node)
    return tuple(result)


def _collect_reachable_function_nodes(
    ast: Mapping[str, Any], symbols: set[str] | None
) -> tuple[Mapping[str, Any], ...]:
    all_nodes = _collect_function_nodes(ast, None)
    if symbols is None:
        return all_nodes
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    definitions: set[str] = set()
    for node in all_nodes:
        name = str(node.get("name", ""))
        by_name.setdefault(name, []).append(node)
        if _body_node(node) is not None:
            definitions.add(name)
    reachable = set(symbols)
    changed = True
    while changed:
        changed = False
        for name in tuple(reachable):
            for node in by_name.get(name, ()):
                body = _body_node(node)
                if body is None:
                    continue
                for item in _walk(body):
                    if item.get("kind") != "DeclRefExpr":
                        continue
                    declaration = item.get("referencedDecl")
                    if not isinstance(declaration, dict) or declaration.get("kind") not in {
                        "FunctionDecl",
                        "CXXMethodDecl",
                    }:
                        continue
                    callee = declaration.get("name")
                    if isinstance(callee, str) and callee in definitions and callee not in reachable:
                        reachable.add(callee)
                        changed = True
    return tuple(
        node for node in all_nodes if str(node.get("name", "")) in reachable
    )


def _source_types(nodes: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    result: set[str] = set()
    for node in nodes:
        type_data = node.get("type")
        if isinstance(type_data, dict):
            function_type = type_data.get("qualType")
            if isinstance(function_type, str):
                result.add(_function_result_spelling(function_type))
        for child in node.get("inner", ()):
            if isinstance(child, dict) and child.get("kind") == "ParmVarDecl":
                child_type = child.get("type")
                if isinstance(child_type, dict) and isinstance(
                    child_type.get("qualType"), str
                ):
                    result.add(child_type["qualType"])
    return tuple(sorted(result))


def _extract_functions(
    nodes: Sequence[Mapping[str, Any]],
    typedefs: Mapping[str, str],
    layouts: Mapping[str, tuple[int, int]],
    build: BuildContext,
    root: Path,
    source_path: Path,
    source: bytes,
    display_path: str | None,
    target: str,
) -> tuple[
    tuple[CFunctionInfo, ...],
    dict[tuple[str, str], Mapping[str, Any]],
    tuple[AnalysisDiagnostic, ...],
]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    infos: dict[tuple[str, str], CFunctionInfo] = {}
    diagnostics: list[AnalysisDiagnostic] = []
    source_text = source.decode("utf-8", errors="replace")
    for node in nodes:
        name = str(node["name"])
        type_data = node.get("type")
        if not isinstance(type_data, dict) or not isinstance(type_data.get("qualType"), str):
            diagnostics.append(
                AnalysisDiagnostic(
                    AnalysisSeverity.ERROR,
                    "missing_function_type",
                    "Clang function node has no qualified type",
                    name,
                )
            )
            continue
        function_type = type_data["qualType"]
        parameter_nodes = [
            item
            for item in node.get("inner", ())
            if isinstance(item, dict) and item.get("kind") == "ParmVarDecl"
        ]
        parameters: list[CParameterInfo] = []
        for index, parameter in enumerate(parameter_nodes):
            parameter_type = parameter.get("type", {})
            source_type = str(parameter_type.get("qualType", ""))
            if not source_type:
                raise CAnalysisError(f"parameter {index} of {name} has no C type")
            parameters.append(
                CParameterInfo(
                    index,
                    str(parameter.get("name") or f"arg{index}"),
                    source_type,
                    _canonical_type(source_type, typedefs, layouts.get(source_type), build),
                )
            )
        result_source = _function_result_spelling(function_type)
        signature = CFunctionSignature(
            result=_canonical_type(
                result_source, typedefs, layouts.get(result_source), build
            ),
            parameters=tuple(item.canonical_type for item in parameters),
            variadic=bool(node.get("variadic")) or _function_is_variadic(function_type),
            calling_convention=_calling_convention(function_type, node),
            abi_tag=target,
        )
        location = _source_location(
            node, root, source_path, source_text, display_path
        )
        storage = str(node.get("storageClass")) if node.get("storageClass") else None
        linkage_name = name
        if storage == "static":
            unit_digest = location.content_sha256 or hashlib.sha256(
                location.path.encode("utf-8")
            ).hexdigest()
            linkage_name = f"{name}__tu_{unit_digest[:16]}"
        key = (name, signature.signature_id)
        grouped.setdefault(key, []).append(node)
        has_body = _body_node(node) is not None
        existing = infos.get(key)
        declaration_locations = tuple(
            sorted(
                set((*(existing.declaration_locations if existing else ()), location)),
                key=lambda item: (item.path, item.line, item.column),
            )
        )
        definition_location = location if has_body else (
            existing.definition_location if existing else None
        )
        infos[key] = CFunctionInfo(
            symbol=name,
            linkage_name=linkage_name,
            signature=signature,
            parameters=tuple(parameters),
            declaration_locations=declaration_locations,
            definition_location=definition_location,
            storage_class=storage or (existing.storage_class if existing else None),
            has_definition=has_body or bool(existing and existing.has_definition),
            variadic=signature.variadic,
            ast_id=str(node.get("id", "")),
        )
    definitions: dict[tuple[str, str], Mapping[str, Any]] = {}
    for key, grouped_nodes in grouped.items():
        definition_nodes = [item for item in grouped_nodes if _body_node(item) is not None]
        if len(definition_nodes) > 1:
            diagnostics.append(
                AnalysisDiagnostic(
                    AnalysisSeverity.ERROR,
                    "multiple_definitions",
                    f"multiple bodies found for signature {key[1]}",
                    key[0],
                )
            )
        definitions[key] = definition_nodes[0] if definition_nodes else grouped_nodes[-1]
    by_symbol: dict[str, set[str]] = {}
    for name, signature_id in infos:
        by_symbol.setdefault(name, set()).add(signature_id)
    for name, signatures in by_symbol.items():
        if len(signatures) > 1:
            diagnostics.append(
                AnalysisDiagnostic(
                    AnalysisSeverity.WARNING,
                    "abi_distinct_declarations",
                    f"found {len(signatures)} ABI-distinct declarations",
                    name,
                )
            )
    return tuple(infos.values()), definitions, tuple(diagnostics)


def _build_states(
    functions: Sequence[CFunctionInfo],
    nodes: Mapping[tuple[str, str], Mapping[str, Any]],
    complete_records: set[str],
) -> dict[tuple[str, str], _FunctionState]:
    global_ids: set[str] = set()
    for node in nodes.values():
        root = node
        # References carry stable declaration IDs; collect globals lazily during
        # expression inspection as any non-parameter/non-local VarDecl.
        del root
    states: dict[tuple[str, str], _FunctionState] = {}
    for function in functions:
        key = (function.symbol, function.signature.signature_id)
        node = nodes[key]
        body = _body_node(node)
        aliases: dict[str, set[int]] = {}
        for parameter_node, parameter in zip(
            [
                item
                for item in node.get("inner", ())
                if isinstance(item, dict) and item.get("kind") == "ParmVarDecl"
            ],
            function.parameters,
        ):
            identifier = parameter_node.get("id")
            if isinstance(identifier, str):
                aliases[identifier] = {parameter.index}
        state = _FunctionState(
            info=function,
            effects=[_MutableEffect() for _ in function.parameters],
            body=body,
            aliases=aliases,
            local_ids=set(),
            global_ids=global_ids,
        )
        if body is None:
            state.complete = False
            state.callback_complete = False
            for parameter, effect in zip(function.parameters, state.effects):
                effect.complete = False
                if _is_pointer_type(parameter.canonical_type.canonical):
                    effect.read = True
                    effect.write = True
                    effect.escape_level = 1
                    effect.reasons.add("conservative declaration-only summary")
            state.diagnostics.append(
                AnalysisDiagnostic(
                    AnalysisSeverity.INFO,
                    "declaration_only",
                    "no function body is available for effect proof",
                    function.symbol,
                )
            )
        states[key] = state
    return states


def _analyze_direct_effects(states: Mapping[tuple[str, str], _FunctionState]) -> None:
    internal_by_name: dict[str, set[str]] = {}
    for name, signature_id in states:
        if states[(name, signature_id)].body is not None:
            internal_by_name.setdefault(name, set()).add(signature_id)
    for state in states.values():
        if state.body is None:
            continue
        node_count = sum(1 for _ in _walk(state.body))
        limit = max(4, node_count * max(1, len(state.effects)) + 1)
        for _ in range(limit):
            before = {
                key: frozenset(value) for key, value in state.aliases.items()
            }
            state.calls.clear()
            _visit_node(state.body, state, internal_by_name, returned=False)
            after = {
                key: frozenset(value) for key, value in state.aliases.items()
            }
            if before == after:
                break
        else:
            state.complete = False
            state.callback_complete = False
            state.diagnostics.append(
                AnalysisDiagnostic(
                    AnalysisSeverity.ERROR,
                    "alias_fixed_point_limit",
                    "local may-alias analysis did not converge before its safety limit",
                    state.info.symbol,
                )
            )


def _visit_node(
    node: Mapping[str, Any],
    state: _FunctionState,
    internal_by_name: Mapping[str, set[str]],
    *,
    returned: bool,
) -> None:
    kind = node.get("kind")
    children = [item for item in node.get("inner", ()) if isinstance(item, dict)]
    if kind == "VarDecl":
        identifier = node.get("id")
        if isinstance(identifier, str):
            state.local_ids.add(identifier)
            if children:
                aliases = _expression_aliases(children[-1], state.aliases)
                if aliases:
                    state.aliases[identifier] = set(aliases)
                callee = _callee_name(children[-1])
                if callee in _KNOWN_CALLS and _KNOWN_CALLS[callee].allocator:
                    state.allocated_locals.add(identifier)
        for child in children:
            _visit_node(child, state, internal_by_name, returned=False)
        return
    if kind in {"BinaryOperator", "CompoundAssignOperator"} and len(children) >= 2:
        opcode = node.get("opcode")
        left, right = children[0], children[1]
        if opcode in {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "|=", "^="}:
            memory_aliases = _memory_expression_aliases(left, state.aliases)
            for index in memory_aliases:
                effect = state.effects[index]
                effect.write = True
                effect.reasons.add(f"write through parameter in {opcode} expression")
                if opcode != "=":
                    effect.read = True
            right_aliases = _expression_aliases(right, state.aliases)
            if right_aliases and memory_aliases and _node_pointer_like(left):
                for index in right_aliases:
                    state.effects[index].escape_level = 2
                    state.effects[index].reasons.add(
                        "stored into caller/heap-reachable pointer memory"
                    )
            left_decl = _decl_reference(left)
            if left_decl is not None:
                decl_id, decl_kind = left_decl
                if decl_kind == "VarDecl" and decl_id in state.local_ids:
                    if right_aliases:
                        # Keep a may-alias union.  Killing an earlier alias
                        # without a CFG/path predicate would be unsound when
                        # this assignment is conditional.
                        state.aliases.setdefault(decl_id, set()).update(
                            right_aliases
                        )
                    callee = _callee_name(right)
                    if callee in _KNOWN_CALLS and _KNOWN_CALLS[callee].allocator:
                        state.allocated_locals.add(decl_id)
                elif right_aliases:
                    for index in right_aliases:
                        state.effects[index].escape_level = 2
                        state.effects[index].reasons.add("stored in global/static object")
                        if _is_function_pointer_type(
                            state.info.parameters[index].canonical_type.canonical
                        ):
                            state.callback = _join_callback_value(
                                state.callback, Callback.ASYNCHRONOUS
                            )
            elif right_aliases and not memory_aliases:
                for index in right_aliases:
                    state.effects[index].escape_level = max(
                        1, state.effects[index].escape_level
                    )
                    state.effects[index].reasons.add(
                        "stored through an unresolved pointer path"
                    )
            _visit_node(right, state, internal_by_name, returned=False)
            # Visit address-computation children of the lvalue without treating
            # the lvalue itself as a read.
            for child in left.get("inner", ()):
                if isinstance(child, dict):
                    _visit_node(child, state, internal_by_name, returned=False)
            return
    if kind == "UnaryOperator" and node.get("opcode") in {"++", "--"}:
        aliases = _memory_expression_aliases(node, state.aliases)
        for index in aliases:
            state.effects[index].read = True
            state.effects[index].write = True
            state.effects[index].reasons.add("increment/decrement through parameter")
    if kind == "ImplicitCastExpr" and node.get("castKind") == "LValueToRValue" and children:
        for index in _memory_expression_aliases(children[0], state.aliases):
            state.effects[index].read = True
            state.effects[index].reasons.add("read through parameter")
    if kind == "ReturnStmt":
        for child in children:
            aliases = (
                _expression_aliases(child, state.aliases)
                if _node_pointer_like(child)
                else set()
            )
            state.returned_parameters.update(aliases)
            for index in aliases:
                state.effects[index].escape_level = 2
                state.effects[index].reasons.add("returned from function")
            callee = _callee_name(child)
            if callee in _KNOWN_CALLS and _KNOWN_CALLS[callee].allocator:
                state.result_allocator = True
            decl = _decl_reference(child)
            if decl is not None and decl[1] == "VarDecl":
                if decl[0] in state.allocated_locals:
                    state.result_allocator = True
                elif decl[0] not in state.local_ids:
                    state.result_global = True
            _visit_node(child, state, internal_by_name, returned=True)
        return
    if kind == "CallExpr":
        _analyze_call(node, state, internal_by_name, returned=returned)
        # Analyze argument expressions for their own reads/calls, skipping the
        # callee expression which is not a memory access to a callback context.
        for child in children[1:]:
            _visit_node(child, state, internal_by_name, returned=False)
        return
    if kind in {"GCCAsmStmt", "MSAsmStmt", "AtomicExpr"}:
        state.complete = False
        state.callback_complete = False
        for index in _all_parameter_references(node, state.aliases):
            effect = state.effects[index]
            effect.read = True
            effect.write = True
            effect.escape_level = max(effect.escape_level, 1)
            effect.complete = False
            effect.reasons.add(f"conservative effect from unsupported {kind}")
        state.diagnostics.append(
            AnalysisDiagnostic(
                AnalysisSeverity.WARNING,
                "unsupported_body_construct",
                f"conservative summary emitted for {kind}",
                state.info.symbol,
            )
        )
    propagate_return = returned and kind in {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "ParenExpr",
        "ConditionalOperator",
        "BinaryConditionalOperator",
        "ExprWithCleanups",
    }
    for child in children:
        _visit_node(
            child,
            state,
            internal_by_name,
            returned=propagate_return,
        )


def _analyze_call(
    node: Mapping[str, Any],
    state: _FunctionState,
    internal_by_name: Mapping[str, set[str]],
    *,
    returned: bool,
) -> None:
    children = [item for item in node.get("inner", ()) if isinstance(item, dict)]
    if not children:
        return
    callee_expression = children[0]
    arguments = children[1:]
    callee_aliases = _expression_aliases(callee_expression, state.aliases)
    if callee_aliases:
        state.callback = _join_callback_value(state.callback, Callback.SYNCHRONOUS)
        for index in callee_aliases:
            state.effects[index].reasons.add("invoked as a synchronous callback")
        return
    callee = _callee_name(callee_expression)
    aliases = tuple(
        frozenset(_expression_aliases(argument, state.aliases))
        for argument in arguments
    )
    pointer_like = tuple(_node_pointer_like(argument) for argument in arguments)
    known = _KNOWN_CALLS.get(callee or "")
    if known is not None:
        _apply_known_call(known, aliases, state, callee or "")
        if known.allocator and returned:
            state.result_allocator = True
        return
    if (
        callee is not None
        and callee in internal_by_name
        and len(internal_by_name[callee]) == 1
    ):
        state.calls.append(_CallSite(callee, aliases, pointer_like, returned))
        return
    # An unresolved/indirect call is a conservative effect boundary.  Only
    # pointer-like actual arguments are assigned memory/escape effects, but the
    # function-level callback effect remains unknown because global callbacks
    # do not require a callback argument.
    state.complete = False
    state.callback_complete = False
    state.callback = _join_callback_value(state.callback, Callback.MAY_CALLBACK)
    for argument_aliases, is_pointer in zip(aliases, pointer_like):
        if not is_pointer and not any(
            _is_pointer_type(
                state.info.parameters[index].canonical_type.canonical
            )
            for index in argument_aliases
        ):
            continue
        for index in argument_aliases:
            effect = state.effects[index]
            effect.read = True
            effect.write = True
            effect.escape_level = max(effect.escape_level, 1)
            effect.complete = False
            effect.reasons.add(f"passed to unresolved callee {callee or '<indirect>'}")
    state.calls.append(_CallSite(callee, aliases, pointer_like, returned))
    state.diagnostics.append(
        AnalysisDiagnostic(
            AnalysisSeverity.WARNING,
            "unresolved_callee",
            f"conservative effects for call to {callee or '<indirect function>'}",
            state.info.symbol,
        )
    )


def _apply_known_call(
    known: _KnownCall,
    aliases: tuple[frozenset[int], ...],
    state: _FunctionState,
    callee: str,
) -> None:
    for position in known.reads:
        if position < len(aliases):
            for index in aliases[position]:
                state.effects[index].read = True
                state.effects[index].reasons.add(f"read by known {callee} summary")
    for position in known.writes:
        if position < len(aliases):
            for index in aliases[position]:
                state.effects[index].write = True
                state.effects[index].reasons.add(f"written by known {callee} summary")
    for position in known.frees:
        if position < len(aliases):
            for index in aliases[position]:
                state.effects[index].freed = True
                state.effects[index].reasons.add(f"ownership consumed by {callee}")
    for position in known.escapes:
        if position < len(aliases):
            for index in aliases[position]:
                state.effects[index].escape_level = 2
                state.effects[index].reasons.add(f"retained by known {callee} summary")
    for position in known.c_strings:
        if position < len(aliases):
            for index in aliases[position]:
                state.effects[index].c_string = True
                state.effects[index].reasons.add(f"NUL-terminated use by {callee}")
    if known.callback is not None:
        state.callback = _join_callback_value(state.callback, known.callback)


def _propagate_interprocedural(states: Mapping[tuple[str, str], _FunctionState]) -> None:
    by_name: dict[str, list[_FunctionState]] = {}
    for state in states.values():
        if state.body is not None:
            by_name.setdefault(state.info.symbol, []).append(state)
    limit = max(8, len(states) * len(states) + 1)
    for _ in range(limit):
        changed = False
        for state in states.values():
            for call in state.calls:
                if call.callee is None:
                    continue
                candidates = by_name.get(call.callee, ())
                if len(candidates) != 1:
                    continue
                callee = candidates[0]
                for position, callee_effect in enumerate(callee.effects):
                    if position >= len(call.argument_aliases):
                        break
                    for caller_index in call.argument_aliases[position]:
                        changed |= state.effects[caller_index].merge(
                            callee_effect,
                            f"propagated through call to {callee.info.symbol}",
                        )
                old_callback = state.callback
                if callee.callback is not None:
                    state.callback = _join_callback_value(
                        state.callback, callee.callback
                    )
                changed |= old_callback != state.callback
                old_complete = state.complete
                state.complete &= callee.complete
                state.callback_complete &= callee.callback_complete
                changed |= old_complete != state.complete
                if call.returned:
                    before = (
                        state.result_allocator,
                        tuple(sorted(state.returned_parameters)),
                    )
                    state.result_allocator |= callee.result_allocator
                    for returned_parameter in callee.returned_parameters:
                        if returned_parameter < len(call.argument_aliases):
                            state.returned_parameters.update(
                                call.argument_aliases[returned_parameter]
                            )
                    changed |= before != (
                        state.result_allocator,
                        tuple(sorted(state.returned_parameters)),
                    )
        if not changed:
            return
    for state in states.values():
        state.complete = False
        state.callback_complete = False
        state.diagnostics.append(
            AnalysisDiagnostic(
                AnalysisSeverity.ERROR,
                "summary_fixed_point_limit",
                "interprocedural summary did not converge before the safety limit",
                state.info.symbol,
            )
        )


def _finalize_summary(
    state: _FunctionState, complete_records: set[str]
) -> CFunctionSummary:
    effects = tuple(
        _finalize_parameter_effect(parameter, mutable, complete_records)
        for parameter, mutable in zip(state.info.parameters, state.effects)
    )
    result_representation = _type_representation(
        state.info.signature.result, complete_records
    )
    if state.result_allocator:
        result = ResultEffect(
            Ownership.CALLER_OWNED,
            Lifetime.UNTIL_EXPLICIT_FREE,
            Escape.NO_ESCAPE,
            Mutability.MAY_MUTATE,
            result_representation,
            ("returned allocation from a known allocator",),
        )
    elif state.result_global:
        result = ResultEffect(
            Ownership.CALLEE_OWNED,
            Lifetime.PROCESS_LIFETIME,
            Escape.NO_ESCAPE,
            Mutability.EXTERNALLY_MUTABLE,
            result_representation,
            ("returned global/static object",),
        )
    elif state.returned_parameters:
        result = ResultEffect(
            Ownership.BORROWED,
            Lifetime.OWNER_SCOPED,
            Escape.NO_ESCAPE,
            Mutability.EXTERNALLY_MUTABLE,
            result_representation,
            (
                "aliases input parameter(s) "
                + ",".join(str(item) for item in sorted(state.returned_parameters)),
            ),
        )
    else:
        scalar = not _is_pointer_type(state.info.signature.result.canonical)
        result = ResultEffect(
            Ownership.BORROWED if scalar else Ownership.UNKNOWN,
            Lifetime.CALL_SCOPED if scalar else Lifetime.UNKNOWN,
            Escape.NO_ESCAPE if scalar else Escape.UNKNOWN,
            Mutability.STABLE if scalar else Mutability.UNKNOWN,
            result_representation,
        )
    if state.callback is None:
        callback = (
            Callback.NO_CALLBACK if state.callback_complete else Callback.MAY_CALLBACK
        )
    else:
        callback = state.callback
    calls = tuple(sorted({call.callee or "<indirect>" for call in state.calls}))
    return CFunctionSummary(
        state.info,
        effects,
        result,
        callback,
        state.complete and state.callback_complete and all(item.complete for item in effects),
        calls,
        tuple(sorted(set(state.diagnostics), key=_diagnostic_key)),
    )


def _finalize_parameter_effect(
    parameter: CParameterInfo,
    effect: _MutableEffect,
    complete_records: set[str],
) -> ParameterEffect:
    pointer = _is_pointer_type(parameter.canonical_type.canonical)
    if not pointer:
        return ParameterEffect(
            MemoryAccess.NONE,
            Ownership.BORROWED,
            Lifetime.CALL_SCOPED,
            Escape.NO_ESCAPE,
            Mutability.STABLE,
            _type_representation(parameter.canonical_type, complete_records),
            True,
            tuple(sorted(effect.reasons)),
        )
    memory = (
        MemoryAccess.READ_WRITE
        if effect.read and effect.write
        else MemoryAccess.READ
        if effect.read
        else MemoryAccess.WRITE
        if effect.write
        else MemoryAccess.NONE
    )
    escape = (
        Escape.ESCAPES
        if effect.escape_level == 2
        else Escape.MAY_ESCAPE
        if effect.escape_level == 1 or not effect.complete
        else Escape.NO_ESCAPE
    )
    if effect.freed:
        ownership = Ownership.TRANSFERRED_TO_CALLEE
        lifetime = Lifetime.CALL_SCOPED
    elif escape == Escape.NO_ESCAPE:
        ownership = Ownership.BORROWED
        lifetime = Lifetime.CALL_SCOPED
    else:
        ownership = Ownership.UNKNOWN
        lifetime = Lifetime.UNKNOWN
    mutability = (
        Mutability.CALLEE_MUTATES
        if effect.write
        else Mutability.MAY_MUTATE
        if not effect.complete
        else Mutability.UNKNOWN
    )
    representation = _type_representation(parameter.canonical_type, complete_records)
    if effect.c_string:
        representation = Representation(
            RepresentationKind.C_STRING,
            Encoding.BYTES,
            TriState.YES,
            alignment=representation.alignment,
            element_type="char",
            notes="inferred from NUL-terminated library operation",
        )
    return ParameterEffect(
        memory,
        ownership,
        lifetime,
        escape,
        mutability,
        representation,
        effect.complete,
        tuple(sorted(effect.reasons)),
    )


def _canonical_type(
    source_type: str,
    typedefs: Mapping[str, str],
    layout: tuple[int, int] | None,
    build: BuildContext,
) -> CTypeIdentity:
    resolved = source_type
    for _ in range(len(typedefs) + 1):
        updated = resolved
        for name in sorted(typedefs, key=len, reverse=True):
            updated = re.sub(
                rf"(?<!struct )(?<!union )(?<!enum )\b{re.escape(name)}\b",
                typedefs[name],
                updated,
            )
        if updated == resolved:
            break
        resolved = updated
    resolved = _canonical_builtin_spelling(resolved)
    canonical = "c:" + normalize_c_type(resolved)
    size, alignment = layout or _fallback_layout(canonical, build)
    return CTypeIdentity(canonical, size, alignment, source_type)


def _canonical_builtin_spelling(value: str) -> str:
    normalized = " ".join(value.split())
    replacements = {
        "signed": "int",
        "signed int": "int",
        "long int": "long",
        "signed long": "long",
        "signed long int": "long",
        "long signed int": "long",
        "long unsigned int": "unsigned long",
        "unsigned long int": "unsigned long",
        "short int": "short",
        "signed short": "short",
        "signed short int": "short",
        "short signed int": "short",
        "short unsigned int": "unsigned short",
        "unsigned short int": "unsigned short",
        "long long int": "long long",
        "signed long long": "long long",
        "unsigned long long int": "unsigned long long",
    }
    for source, target in sorted(replacements.items(), key=lambda item: -len(item[0])):
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized)
    # Clang consistently emits leading pointee qualifiers; normalize the common
    # equivalent spelling produced by documentation/frontends too.
    normalized = re.sub(r"\b(char|short|int|long|float|double) const\b", r"const \1", normalized)
    return normalized


def _fallback_layout(
    canonical: str, build: BuildContext
) -> tuple[int | None, int | None]:
    if _is_pointer_type(canonical):
        width = build.abi.pointer_width_bits
        return width, width
    spelling = canonical.removeprefix("c:")
    widths = {
        "_Bool": 8,
        "char": 8,
        "signed char": 8,
        "unsigned char": 8,
        "short": 16,
        "unsigned short": 16,
        "int": 32,
        "unsigned int": 32,
        "long long": 64,
        "unsigned long long": 64,
        "float": 32,
        "double": 64,
    }
    if spelling in {"long", "unsigned long"}:
        width = 32 if build.abi.data_model in {"ILP32", "LLP64"} else 64
        return width, width
    width = widths.get(spelling)
    return width, width


def _function_result_spelling(function_type: str) -> str:
    depth = 0
    for index, character in enumerate(function_type):
        if character == "(" and depth == 0:
            result = function_type[:index].strip()
            if result:
                return result
        depth += character == "("
        depth -= character == ")"
    raise CAnalysisError(f"cannot parse Clang function type {function_type!r}")


def _function_is_variadic(function_type: str) -> bool:
    return bool(re.search(r"(?:^|,)\s*\.\.\.\s*\)$", function_type))


def _calling_convention(
    function_type: str, node: Mapping[str, Any]
) -> CallingConvention:
    text = function_type.lower() + " " + json.dumps(node.get("attributes", "")).lower()
    if "stdcall" in text:
        return CallingConvention.STDCALL
    if "fastcall" in text:
        return CallingConvention.FASTCALL
    if "vectorcall" in text:
        return CallingConvention.VECTORCALL
    if "cdecl" in text:
        return CallingConvention.CDECL
    return CallingConvention.C


def _source_location(
    node: Mapping[str, Any],
    root: Path,
    source_path: Path,
    source: str,
    display_path: str | None,
) -> SourceLocation:
    location = node.get("loc") if isinstance(node.get("loc"), dict) else {}
    if "spellingLoc" in location and isinstance(location["spellingLoc"], dict):
        location = location["spellingLoc"]
    file_value = location.get("file")
    if isinstance(file_value, str):
        actual = Path(file_value)
        if not actual.is_absolute():
            actual = source_path.parent / actual
    else:
        actual = source_path
    if actual.resolve() == source_path.resolve() and display_path is not None:
        relative = display_path
    else:
        try:
            relative = actual.resolve().relative_to(root).as_posix()
        except ValueError:
            digest = hashlib.sha256(str(actual.resolve()).encode("utf-8")).hexdigest()[:16]
            relative = f"external/{digest}/{actual.name or 'source'}"
    line = location.get("line")
    column = location.get("col")
    offset = location.get("offset")
    if not isinstance(line, int) and actual.resolve() == source_path.resolve() and isinstance(offset, int):
        line = source.count("\n", 0, offset) + 1
        previous = source.rfind("\n", 0, offset)
        column = offset - previous
    line = line if isinstance(line, int) and line > 0 else 1
    column = column if isinstance(column, int) and column > 0 else 1
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest() if actual.resolve() == source_path.resolve() else None
    return SourceLocation(relative, line, column, digest)


def _body_node(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for child in node.get("inner", ()):
        if isinstance(child, dict) and child.get("kind") in {
            "CompoundStmt",
            "CXXTryStmt",
        }:
            return child
    return None


def _walk(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield node
    for child in node.get("inner", ()):
        if isinstance(child, dict):
            yield from _walk(child)


def _constant_value(node: Mapping[str, Any]) -> int | None:
    value = node.get("value")
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    for child in node.get("inner", ()):
        if isinstance(child, dict):
            result = _constant_value(child)
            if result is not None:
                return result
    return None


def _layout_probe_supported(source_type: str) -> bool:
    stripped = source_type.strip()
    return stripped not in {"void", "..."} and "<dependent type>" not in stripped


def _decl_reference(node: Mapping[str, Any]) -> tuple[str, str] | None:
    if node.get("kind") == "DeclRefExpr":
        declaration = node.get("referencedDecl")
        if isinstance(declaration, dict) and isinstance(declaration.get("id"), str):
            return declaration["id"], str(declaration.get("kind", ""))
    children = [item for item in node.get("inner", ()) if isinstance(item, dict)]
    if len(children) == 1 and node.get("kind") in {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "ParenExpr",
        "ExprWithCleanups",
        "MaterializeTemporaryExpr",
    }:
        return _decl_reference(children[0])
    return None


def _expression_aliases(
    node: Mapping[str, Any], aliases: Mapping[str, set[int]]
) -> set[int]:
    reference = _decl_reference(node)
    if reference is not None and reference[0] in aliases:
        return set(aliases[reference[0]])
    kind = node.get("kind")
    if kind in {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "ParenExpr",
        "UnaryOperator",
        "ArraySubscriptExpr",
        "MemberExpr",
        "BinaryOperator",
        "ConditionalOperator",
    }:
        result: set[int] = set()
        for child in node.get("inner", ()):
            if isinstance(child, dict):
                result.update(_expression_aliases(child, aliases))
        return result
    return set()


def _memory_expression_aliases(
    node: Mapping[str, Any], aliases: Mapping[str, set[int]]
) -> set[int]:
    kind = node.get("kind")
    children = [item for item in node.get("inner", ()) if isinstance(item, dict)]
    if kind == "UnaryOperator" and node.get("opcode") == "*" and children:
        return _expression_aliases(children[0], aliases)
    if kind == "ArraySubscriptExpr" and children:
        return _expression_aliases(children[0], aliases)
    if kind == "MemberExpr" and children:
        # isArrow marks p->field; for (*p).field alias collection reaches p too.
        return _expression_aliases(children[0], aliases)
    if kind in {"ImplicitCastExpr", "CStyleCastExpr", "ParenExpr"} and children:
        return _memory_expression_aliases(children[0], aliases)
    if kind == "UnaryOperator" and node.get("opcode") in {"++", "--"} and children:
        return _memory_expression_aliases(children[0], aliases)
    return set()


def _all_parameter_references(
    node: Mapping[str, Any], aliases: Mapping[str, set[int]]
) -> set[int]:
    result: set[int] = set()
    for item in _walk(node):
        result.update(_expression_aliases(item, aliases))
    return result


def _callee_name(node: Mapping[str, Any]) -> str | None:
    if node.get("kind") == "CallExpr":
        children = [item for item in node.get("inner", ()) if isinstance(item, dict)]
        return _callee_name(children[0]) if children else None
    reference = _decl_reference(node)
    if reference is not None:
        current: Mapping[str, Any] = node
        while True:
            if current.get("kind") == "DeclRefExpr":
                declaration = current.get("referencedDecl")
                if isinstance(declaration, dict) and declaration.get("kind") in {
                    "FunctionDecl",
                    "CXXMethodDecl",
                }:
                    name = declaration.get("name")
                    return name if isinstance(name, str) else None
                return None
            children = [item for item in current.get("inner", ()) if isinstance(item, dict)]
            if len(children) != 1:
                return None
            current = children[0]
    children = [item for item in node.get("inner", ()) if isinstance(item, dict)]
    if len(children) == 1 and node.get("kind") in {
        "ImplicitCastExpr",
        "CStyleCastExpr",
        "ParenExpr",
        "ExprWithCleanups",
    }:
        return _callee_name(children[0])
    return None


def _node_pointer_like(node: Mapping[str, Any]) -> bool:
    type_data = node.get("type")
    if not isinstance(type_data, dict):
        return False
    spelling = type_data.get("qualType")
    return isinstance(spelling, str) and ("*" in spelling or "[" in spelling)


def _is_pointer_type(canonical: str) -> bool:
    return "*" in canonical or "[" in canonical or "go:[]" in canonical


def _is_function_pointer_type(canonical: str) -> bool:
    spelling = canonical.removeprefix("c:")
    return bool(re.search(r"\(\*[^)]*\)\s*\(", spelling) or "(*)" in spelling)


def _join_callback_value(left: Callback | None, right: Callback) -> Callback:
    if left is None or left == right:
        return right
    if Callback.MAY_CALLBACK in {left, right}:
        return Callback.MAY_CALLBACK
    if {left, right} == {Callback.SYNCHRONOUS, Callback.ASYNCHRONOUS}:
        return Callback.MAY_CALLBACK
    if Callback.NO_CALLBACK in {left, right}:
        return right if left == Callback.NO_CALLBACK else left
    return Callback.MAY_CALLBACK


def _signature_representation(
    canonical: str, complete_records: set[str] | None = None
) -> Representation:
    spelling = canonical.removeprefix("c:")
    if spelling == "void":
        return Representation(RepresentationKind.SCALAR, notes="void result")
    if re.search(r"\(\*[^)]*\)\s*\(", spelling) or "(*)" in spelling:
        return Representation(RepresentationKind.FUNCTION_POINTER)
    if "*" in spelling or "[" in spelling:
        record_match = re.search(r"\b(struct|union)\s+([A-Za-z_]\w*)", spelling)
        if record_match:
            record = f"{record_match.group(1)} {record_match.group(2)}"
            opaque = complete_records is not None and record not in complete_records
            return Representation(
                RepresentationKind.OPAQUE_HANDLE if opaque else RepresentationKind.RAW_BYTES,
                Encoding.NATIVE,
                element_type=record,
            )
        return Representation(
            RepresentationKind.RAW_BYTES,
            Encoding.BYTES if re.search(r"\b(char|void)\b", spelling) else Encoding.NATIVE,
            element_type=_pointer_element_type(spelling),
        )
    if re.search(r"\b(struct|union)\b", spelling):
        return Representation(RepresentationKind.STRUCT, Encoding.NATIVE)
    return Representation(RepresentationKind.SCALAR, Encoding.NATIVE)


def _type_representation(
    c_type: CTypeIdentity, complete_records: set[str] | None = None
) -> Representation:
    representation = _signature_representation(c_type.canonical, complete_records)
    alignment = c_type.alignment_bits
    if alignment is not None and alignment % 8 == 0:
        representation = replace(representation, alignment=alignment // 8)
    return representation


def _pointer_element_type(spelling: str) -> str | None:
    before = spelling.split("*", 1)[0].strip()
    before = re.sub(r"\b(const|volatile|restrict)\b", "", before)
    normalized = " ".join(before.split())
    return normalized or None


def _optional_proven_fact(value: Any, evidence: Evidence) -> ContractFact[Any]:
    if getattr(value, "value", None) == "unknown" or value == Representation.unknown():
        return ContractFact(value)
    return ContractFact(value, FactStatus.PROVEN, (evidence,))


def _merge_values(left: ValueContract, right: ValueContract) -> ValueContract:
    values: dict[str, Any] = {}
    for attribute in (
        ContractAttribute.MEMORY_ACCESS,
        ContractAttribute.OWNERSHIP,
        ContractAttribute.LIFETIME,
        ContractAttribute.ESCAPE,
        ContractAttribute.MUTABILITY,
        ContractAttribute.REPRESENTATION,
    ):
        values[attribute.value] = merge_facts(
            attribute,
            getattr(left, attribute.value),
            getattr(right, attribute.value),
        ).fact
    return ValueContract(**values)


def _declaration_spelling(function: CFunctionInfo) -> str:
    parameters = ", ".join(
        f"{item.source_type} {item.name}" for item in function.parameters
    )
    if function.variadic:
        parameters = f"{parameters}, ..." if parameters else "..."
    result = function.signature.result.source_spelling or function.signature.result.canonical
    return f"{result} {function.symbol}({parameters})"


def _location_text(location: SourceLocation) -> str:
    return f"{location.path}:{location.line}:{location.column}"


def _diagnostic_key(item: AnalysisDiagnostic) -> tuple[str, str, str, str, str]:
    return (
        item.severity.value,
        item.code,
        item.function or "",
        item.location or "",
        item.message,
    )


def _analysis_diagnostic_data(item: AnalysisDiagnostic) -> dict[str, Any]:
    return {
        "code": item.code,
        "function": item.function,
        "location": item.location,
        "message": item.message,
        "severity": item.severity.value,
    }


def _representation_data(item: Representation) -> dict[str, Any]:
    return {
        "alignment": item.alignment,
        "element_type": item.element_type,
        "encoding": item.encoding.value,
        "kind": item.kind.value,
        "length_argument": item.length_argument,
        "notes": item.notes,
        "nul_terminated": item.nul_terminated.value,
    }


def _expand_manifest_flag(flag: str, root: Path, package_dir: Path) -> str:
    return flag.replace("${WORKSPACE}", str(root)).replace(
        "${PACKAGE}", str(package_dir)
    )


def _frontend_options_for_path(
    options: CFrontendOptions | None, path: Path
) -> CFrontendOptions | None:
    suffix = path.suffix.lower()
    inferred = (
        "c++"
        if suffix in {".cc", ".cpp", ".cxx", ".mm"}
        else "objective-c"
        if suffix == ".m"
        else "c"
    )
    if options is None:
        return CFrontendOptions(language=inferred)
    return replace(options, language=inferred)
