from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .contracts import (
    AnalysisSeverity,
    AnnotationPolicy,
    AnnotationTrust,
    CAnalysisError,
    CFrontendOptions,
    ContractStore,
    DiscoveryOptions,
    ManifestCompleteness,
    ManifestDiscoveryError,
    ProviderIdentity,
    ProviderKind,
    ProviderArtifact,
    ProviderRecord,
    analyze_package_translation_units,
    augment_manifest_with_c_analysis,
    c_analysis_to_dict,
    discover_project_manifest,
    dumps_catalog,
    dumps_manifest,
    infer_contract_catalog,
    load_annotation_bundle,
    load_catalog,
    load_manifest,
    local_package_provider,
    validate_contract_catalog,
)
from .graph import InteractionGraph, iter_events
from .report import render_text_report
from .rules import run_rules
from .scanner import scan_project


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgoprof")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", aliases=["s"], help="Scan a Go project for cgo call sites")
    scan_parser.add_argument("root", nargs="?", default=".", help="Go project root; defaults to current directory")
    scan_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    instrument_parser = subparsers.add_parser(
        "instrument",
        aliases=["inst", "i"],
        help="Create an automatically instrumented copy of a Go/cgo project",
    )
    instrument_parser.add_argument("root", nargs="?", default=".", help="Input Go project root; defaults to current directory")
    instrument_parser.add_argument(
        "-o",
        "--out",
        help="Output directory; defaults to a sibling directory named <project>.cgoprof",
    )
    instrument_parser.add_argument("--force", action="store_true", help="Overwrite the output directory if it already exists")
    instrument_parser.add_argument(
        "--runtime",
        help="Path to runtime_go/cgoprof; defaults to the runtime bundled with this checkout",
    )

    analyze_parser = subparsers.add_parser("analyze", aliases=["a"], help="Analyze a CGOProf JSON/JSONL profile")
    analyze_parser.add_argument("profile", nargs="?", help="Profile JSON or JSONL file; defaults to cgoprof.jsonl")
    analyze_parser.add_argument("--root", default=".", help="Go project root used to annotate call sites; defaults to current directory")
    analyze_parser.add_argument("--profile", dest="profile_flag", help="Deprecated spelling; use positional profile instead")
    analyze_parser.add_argument("--graph-out", "--graph", dest="graph_out", help="Write CGO Interaction Graph as JSON")
    analyze_parser.add_argument("--json", action="store_true", help="Emit findings as JSON")

    manifest_parser = subparsers.add_parser(
        "manifest",
        aliases=["m"],
        help="Build a content-addressed cgo API identity manifest",
    )
    manifest_parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Go module root; defaults to current directory",
    )
    manifest_parser.add_argument("-o", "--out", help="Write manifest JSON to this path")
    manifest_parser.add_argument(
        "--tags",
        action="append",
        default=[],
        help="Comma-separated Go build tags; may be repeated",
    )
    manifest_parser.add_argument("--goos", help="Override GOOS during discovery")
    manifest_parser.add_argument("--goarch", help="Override GOARCH during discovery")
    manifest_parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Fail when any C.name binding lacks an exact provider/signature identity",
    )
    verify_manifest_parser = subparsers.add_parser(
        "manifest-verify",
        help="Verify manifest schema, referential integrity, and content id",
    )
    verify_manifest_parser.add_argument("path", help="Manifest JSON to verify")

    annotation_verify_parser = subparsers.add_parser(
        "annotation-verify",
        help="Verify a versioned, content-addressed Contract annotation bundle",
    )
    annotation_verify_parser.add_argument("path", help="Annotation bundle JSON")

    contract_verify_parser = subparsers.add_parser(
        "contract-verify",
        help="Verify a Contract catalog against its exact API Manifest",
    )
    contract_verify_parser.add_argument("catalog", help="Contract catalog JSON")
    contract_verify_parser.add_argument("manifest", help="API Manifest JSON")

    contract_parser = subparsers.add_parser(
        "contract-infer",
        aliases=["contracts"],
        help="Infer intrinsic, annotation, C signature, and C body contracts",
    )
    contract_parser.add_argument(
        "root", nargs="?", default=".", help="Go module root"
    )
    contract_parser.add_argument(
        "--manifest",
        help="Existing Manifest JSON; otherwise discover the project first",
    )
    contract_parser.add_argument(
        "--package",
        help="Exact Go import path; required when the Manifest has multiple cgo packages",
    )
    contract_parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="C/C++ translation unit relative to root; may be repeated",
    )
    contract_parser.add_argument(
        "--no-preambles",
        action="store_true",
        help="Do not analyze C code attached to import \"C\"",
    )
    contract_parser.add_argument(
        "--annotation",
        help="Versioned Contract annotation bundle JSON",
    )
    contract_parser.add_argument(
        "--minimum-annotation-trust",
        choices=[item.value for item in AnnotationTrust],
        default=AnnotationTrust.UNTRUSTED.value,
    )
    contract_parser.add_argument("--clang", default="clang", help="Clang executable")
    contract_parser.add_argument(
        "--clang-flag",
        action="append",
        default=[],
        help="Additional C frontend flag; may be repeated",
    )
    contract_parser.add_argument(
        "--provider-kind",
        choices=[
            item.value
            for item in ProviderKind
            if item != ProviderKind.CGO_INTRINSIC
        ],
        help="Explicit provider kind for declaration-only/external APIs",
    )
    contract_parser.add_argument("--provider-namespace")
    contract_parser.add_argument("--provider-name")
    contract_parser.add_argument("--provider-version")
    contract_parser.add_argument("--provider-abi")
    contract_parser.add_argument(
        "--provider-artifact",
        action="append",
        default=[],
        help="Provider library/header/archive file to content-hash; may be repeated",
    )
    contract_parser.add_argument(
        "--allow-declaration-provider",
        action="store_true",
        help="Resolve provider-owned declarations even when no body is available",
    )
    contract_parser.add_argument(
        "--manifest-out",
        help="Write the enriched exact Manifest here",
    )
    contract_parser.add_argument("-o", "--out", help="Write Contract catalog JSON")
    contract_parser.add_argument(
        "--summary-out",
        help="Write raw C signatures/effect summaries and proof reasons as JSON",
    )
    contract_parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Fail unless every package C.name binding is exact and every catalog link is valid",
    )

    args = parser.parse_args(argv)
    if args.command == "scan":
        return _cmd_scan(Path(args.root), args.json)
    if args.command == "instrument":
        return _cmd_instrument(args)
    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command in {"manifest", "m"}:
        return _cmd_manifest(args)
    if args.command == "manifest-verify":
        return _cmd_manifest_verify(args)
    if args.command == "annotation-verify":
        return _cmd_annotation_verify(args)
    if args.command == "contract-verify":
        return _cmd_contract_verify(args)
    if args.command in {"contract-infer", "contracts"}:
        return _cmd_contract_infer(args)
    raise AssertionError(args.command)


def _cmd_scan(root: Path, emit_json: bool) -> int:
    callsites, directives = scan_project(root)
    if emit_json:
        print(
            json.dumps(
                {
                    "callsites": [callsite.__dict__ for callsite in callsites],
                    "directives": directives,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    for callsite in callsites:
        print(
            f"{callsite.site_id} {callsite.file}:{callsite.line} "
            f"{callsite.function} -> C.{callsite.c_symbol}"
        )
    for directive, symbols in directives.items():
        if symbols:
            print(f"#cgo {directive}: {', '.join(sorted(symbols))}")
    return 0


def _cmd_instrument(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parents[1]
    tool_dir = project_root / "instrumenter" / "cgoprof-instrument"
    input_root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve() if args.out else _default_instrumented_out(input_root)
    runtime_dir = Path(args.runtime).resolve() if args.runtime else project_root / "runtime_go" / "cgoprof"
    cmd = [
        "go",
        "run",
        ".",
        "-in",
        str(input_root),
        "-out",
        str(out_dir),
        "-runtime",
        str(runtime_dir.resolve()),
    ]
    if args.force:
        cmd.append("-force")
    env = os.environ.copy()
    env.setdefault("GOCACHE", "/private/tmp/go-build-cache")
    subprocess.run(cmd, cwd=tool_dir, env=env, check=True)
    print(f"instrumented copy written to {out_dir}")
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    graph = InteractionGraph()
    if args.root:
        callsites, _ = scan_project(args.root)
        for callsite in callsites:
            graph.add_callsite(callsite)
    profile = args.profile_flag or args.profile or "cgoprof.jsonl"
    graph.add_events(iter_events(profile))
    findings = run_rules(graph)
    if args.graph_out:
        Path(args.graph_out).write_text(json.dumps(graph.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    if args.json:
        print(json.dumps([finding.__dict__ for finding in findings], indent=2, sort_keys=True))
    else:
        print(render_text_report(graph, findings))
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    tags = tuple(
        sorted(
            {
                tag.strip()
                for group in args.tags
                for tag in group.split(",")
                if tag.strip()
            }
        )
    )
    try:
        manifest = discover_project_manifest(
            args.root,
            DiscoveryOptions(
                goos=args.goos,
                goarch=args.goarch,
                build_tags=tags,
            ),
        )
    except (ManifestDiscoveryError, ValueError) as error:
        print(f"cgoprof manifest: {error}", file=sys.stderr)
        return 2
    if args.out:
        try:
            Path(args.out).write_text(dumps_manifest(manifest), encoding="utf-8")
        except OSError as error:
            print(f"cgoprof manifest: cannot write {args.out}: {error}", file=sys.stderr)
            return 2
        print(
            f"wrote {manifest.completeness.value} manifest {manifest.manifest_id} "
            f"to {args.out}"
        )
    else:
        print(dumps_manifest(manifest), end="")
    if args.require_complete and manifest.completeness != ManifestCompleteness.COMPLETE:
        print(
            f"manifest is partial: {len(manifest.unresolved)} unresolved bindings",
            file=sys.stderr,
        )
        return 2
    return 0


def _cmd_manifest_verify(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(args.path)
    except (OSError, ValueError) as error:
        print(f"invalid API manifest: {error}", file=sys.stderr)
        return 2
    print(
        f"valid {manifest.completeness.value} manifest {manifest.manifest_id}: "
        f"{len(manifest.apis)} APIs, {len(manifest.bindings)} exact bindings, "
        f"{len(manifest.unresolved)} unresolved"
    )
    return 0


def _cmd_annotation_verify(args: argparse.Namespace) -> int:
    try:
        bundle = load_annotation_bundle(args.path)
    except (OSError, ValueError) as error:
        print(f"invalid Contract annotation bundle: {error}", file=sys.stderr)
        return 2
    print(
        f"valid annotation bundle {bundle.bundle_id}: "
        f"{len(bundle.annotations)} exact annotations"
    )
    return 0


def _cmd_contract_verify(args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog(args.catalog)
        manifest = load_manifest(args.manifest)
        report = validate_contract_catalog(catalog, manifest)
        report.require_valid()
        ContractStore(catalog, manifest, require_linked=True)
    except (OSError, ValueError) as error:
        print(f"invalid Contract catalog: {error}", file=sys.stderr)
        return 2
    print(
        f"valid linked Contract catalog for {manifest.manifest_id}: "
        f"{len(catalog.contracts)} APIs"
    )
    return 0


def _cmd_contract_infer(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    try:
        manifest = (
            load_manifest(args.manifest)
            if args.manifest
            else discover_project_manifest(root)
        )
        go_package = _select_contract_package(manifest, args.package)
        options = CFrontendOptions(
            clang_binary=args.clang,
            flags=tuple(args.clang_flag),
            target_triple=manifest.build.abi.target_triple,
        )
        package_analysis = analyze_package_translation_units(
            root,
            manifest,
            go_package,
            options=options,
            source_paths=tuple(args.source),
            include_preambles=not args.no_preambles,
        )
        provider, explicit_provider = _contract_provider(
            args, manifest, go_package, root
        )
        enriched = augment_manifest_with_c_analysis(
            manifest,
            package_analysis.analyses,
            provider,
            go_package,
            definitions_only=not (
                explicit_provider and args.allow_declaration_provider
            ),
        )
        annotations = (
            None
            if args.annotation is None
            else load_annotation_bundle(args.annotation)
        )
        inferred = infer_contract_catalog(
            enriched,
            go_package,
            c_analyses=package_analysis.analyses,
            annotation_bundle=annotations,
            annotation_policy=AnnotationPolicy(
                AnnotationTrust(args.minimum_annotation_trust)
            ),
        )
    except (
        OSError,
        ValueError,
        KeyError,
        LookupError,
        PermissionError,
        ManifestDiscoveryError,
        CAnalysisError,
    ) as error:
        print(f"cgoprof contract-infer: {error}", file=sys.stderr)
        return 2

    if enriched.manifest_id != manifest.manifest_id and not args.manifest_out:
        print(
            "cgoprof contract-infer: C signatures changed the Manifest; "
            "--manifest-out is required so the linked catalog remains usable",
            file=sys.stderr,
        )
        return 2
    try:
        if args.manifest_out:
            Path(args.manifest_out).write_text(dumps_manifest(enriched), encoding="utf-8")
        catalog_text = dumps_catalog(inferred.catalog)
        if args.out:
            Path(args.out).write_text(catalog_text, encoding="utf-8")
        else:
            print(catalog_text, end="")
        if args.summary_out:
            Path(args.summary_out).write_text(
                json.dumps(
                    {
                        "diagnostics": [
                            {
                                "code": item.code,
                                "function": item.function,
                                "location": item.location,
                                "message": item.message,
                                "severity": item.severity.value,
                            }
                            for item in package_analysis.diagnostics
                        ],
                        "translation_units": [
                            c_analysis_to_dict(item)
                            for item in package_analysis.analyses
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
    except OSError as error:
        print(f"cgoprof contract-infer: cannot write output: {error}", file=sys.stderr)
        return 2

    analysis_errors = [
        item
        for item in package_analysis.diagnostics
        if item.severity == AnalysisSeverity.ERROR
    ]
    print(
        f"inferred {len(inferred.catalog.contracts)} contracts for {go_package}; "
        f"manifest={enriched.manifest_id}; unresolved={len(enriched.unresolved)}; "
        f"missing_contracts={len(inferred.missing_api_ids)}; "
        f"frontend_errors={len(analysis_errors)}",
        file=sys.stderr,
    )
    if not inferred.link_report.valid:
        for issue in inferred.link_report.issues:
            print(f"{issue.severity.value}: {issue.code}: {issue.message}", file=sys.stderr)
        return 2
    if args.require_complete and (
        enriched.completeness != ManifestCompleteness.COMPLETE
        or analysis_errors
        or not inferred.coverage_complete
    ):
        return 2
    return 0


def _select_contract_package(manifest, requested: str | None) -> str:
    packages = tuple(item.identity.import_path for item in manifest.packages)
    if requested is not None:
        if requested not in packages:
            raise ValueError(f"Go package {requested!r} is absent from the Manifest")
        return requested
    if len(packages) != 1:
        raise ValueError(
            f"--package is required; Manifest contains {len(packages)} cgo packages"
        )
    return packages[0]


def _contract_provider(
    args, manifest, go_package: str, root: Path
) -> tuple[ProviderRecord, bool]:
    explicit_values = (
        args.provider_kind,
        args.provider_namespace,
        args.provider_name,
        args.provider_version,
        args.provider_abi,
        *args.provider_artifact,
    )
    if not any(item is not None for item in explicit_values):
        return local_package_provider(manifest, go_package), False
    if not all(
        item is not None
        for item in (
            args.provider_kind,
            args.provider_namespace,
            args.provider_name,
        )
    ):
        raise ValueError(
            "explicit provider requires --provider-kind, --provider-namespace, and --provider-name"
        )
    if (
        args.provider_version is None
        and args.provider_abi is None
        and not args.provider_artifact
    ):
        raise ValueError(
            "explicit provider requires a version, ABI, or content-hashed artifact"
        )
    artifacts = tuple(
        _provider_artifact(root, Path(item)) for item in args.provider_artifact
    )
    return (
        ProviderRecord(
            ProviderIdentity(
                ProviderKind(args.provider_kind),
                args.provider_namespace,
                args.provider_name,
            ),
            version=args.provider_version,
            abi_version=args.provider_abi,
            artifacts=artifacts,
        ),
        True,
    )


def _provider_artifact(root: Path, path: Path) -> ProviderArtifact:
    actual = path if path.is_absolute() else root / path
    actual = actual.resolve()
    if not actual.is_file():
        raise ValueError(f"provider artifact does not exist: {actual}")
    digest = hashlib.sha256(actual.read_bytes()).hexdigest()
    try:
        locator = actual.relative_to(root).as_posix()
    except ValueError:
        locator = f"external/{digest[:16]}/{actual.name}"
    return ProviderArtifact("file", locator, digest)


def _default_instrumented_out(input_root: Path) -> Path:
    if input_root.name:
        return input_root.parent / f"{input_root.name}.cgoprof"
    return input_root / ".cgoprof"


if __name__ == "__main__":
    raise SystemExit(main())
