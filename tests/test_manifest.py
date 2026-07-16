from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest

from cgoprof.cli import main
from cgoprof.scanner import c_identifier_may_be_shadowed, scan_cgo_references
from cgoprof.contracts import (
    APIBinding,
    APIContract,
    APIDeclaration,
    APIIdentity,
    APIKind,
    APIManifest,
    BindingKind,
    BuildContext,
    BuildScope,
    CFunctionSignature,
    CTypeIdentity,
    CgoDirective,
    ContractCatalog,
    ContractStore,
    DiscoveryOptions,
    Endianness,
    GoPackageIdentity,
    GoPackageRecord,
    Linkage,
    MacroDefinition,
    ManifestAPI,
    ManifestAssembler,
    ManifestCompleteness,
    ManifestIndex,
    ParameterContract,
    ProviderArtifact,
    ProviderIdentity,
    ProviderKind,
    ProviderRecord,
    ResolutionStatus,
    SourceLocation,
    TargetABI,
    ToolchainIdentity,
    UnresolvedBinding,
    UnresolvedReason,
    discover_project_manifest,
    dumps_catalog,
    dumps_manifest,
    loads_catalog,
    loads_manifest,
    validate_contract_catalog,
    validate_content_id,
)


class APIIdentityTests(unittest.TestCase):
    def test_api_id_ignores_source_spelling_but_tracks_abi_signature(self) -> None:
        provider = _sqlite_provider()
        left = APIIdentity(
            provider,
            "sqlite3_bind_text",
            CFunctionSignature(
                result=CTypeIdentity(" c:int ", 32, 32, "int"),
                parameters=(CTypeIdentity("c:const char *", None, None, "const char *"),),
                abi_tag="x86_64-linux-gnu",
            ),
        )
        right = APIIdentity(
            provider,
            "sqlite3_bind_text",
            CFunctionSignature(
                result=CTypeIdentity("c:int", source_spelling="signed int"),
                parameters=(CTypeIdentity("c:const char*", None, None, "char const*"),),
                abi_tag="x86_64-linux-gnu",
            ),
        )
        changed = APIIdentity(
            provider,
            "sqlite3_bind_text",
            CFunctionSignature(
                result=CTypeIdentity("c:int", 32, 32),
                parameters=(CTypeIdentity("c:void*"),),
                abi_tag="x86_64-linux-gnu",
            ),
        )
        self.assertEqual(left.api_id, right.api_id)
        self.assertNotEqual(left.api_id, changed.api_id)
        self.assertEqual(left.family_id, changed.family_id)

    def test_provider_is_part_of_api_identity(self) -> None:
        signature = CFunctionSignature(CTypeIdentity("c:int"))
        sqlite = APIIdentity(_sqlite_provider(), "open", signature)
        libc = APIIdentity(
            ProviderIdentity(ProviderKind.SYSTEM_LIBRARY, "posix.org", "libc"),
            "open",
            signature,
        )
        self.assertNotEqual(sqlite.api_id, libc.api_id)

    def test_provider_release_changes_without_changing_library_api_id(self) -> None:
        provider = _sqlite_provider()
        older = ProviderRecord(provider, version="3.44.0", abi_version="sqlite3")
        newer = ProviderRecord(provider, version="3.45.0", abi_version="sqlite3")
        self.assertEqual(older.provider_id, newer.provider_id)
        self.assertNotEqual(older.release_id, newer.release_id)

    def test_content_ids_are_typed_and_full_length(self) -> None:
        api_id = _sample_manifest().apis[0].api_id
        validate_content_id(api_id, expected_kind="cgoapi", expected_version=1)
        with self.assertRaisesRegex(ValueError, "malformed content id"):
            validate_content_id("cgoapi:v1:deadbeef")
        with self.assertRaisesRegex(ValueError, "expected cgopkg"):
            validate_content_id(api_id, expected_kind="cgopkg")

    def test_package_identity_rejects_cross_module_import_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "belong to its module"):
            GoPackageIdentity("other.example/pkg", "example.com/module")


class CgoReferenceScannerTests(unittest.TestCase):
    def test_lexical_scanner_excludes_literals_comments_and_type_conversions(self) -> None:
        source = textwrap.dedent(
            """
            package fixture

            /*
            #cgo noescape real_call
            C.in_block_comment()
            */
            import "C"

            // #cgo nocallback not_a_preamble
            const interpreted = "C.in_string()"
            const raw = `C.in_raw_string()`

            func run() {
                // C.in_line_comment()
                _ = C.int(1)
                _ = some . C.fake_field()
                _ = C.real_call()
                _ = C.CString("value")
            }
            """
        )
        references, directives = scan_cgo_references(source)
        self.assertEqual(
            [item.symbol for item in references],
            ["real_call", "CString"],
        )
        self.assertEqual(directives["noescape"], ("real_call",))
        self.assertEqual(directives["nocallback"], ())

    def test_c_pseudo_package_shadowing_is_detected_conservatively(self) -> None:
        self.assertTrue(
            c_identifier_may_be_shadowed(
                'package p\nimport "C"\nfunc f() { C := value; C.CString("x") }\n'
            )
        )
        self.assertTrue(
            c_identifier_may_be_shadowed(
                'package p\nimport "C"\nfunc f(C value) { C.call() }\n'
            )
        )
        self.assertFalse(
            c_identifier_may_be_shadowed(
                'package p\nimport "C"\nfunc f() { C.CString("x") }\n'
            )
        )


class ManifestModelTests(unittest.TestCase):
    def test_manifest_is_content_addressed_and_deterministic(self) -> None:
        manifest = _sample_manifest()
        serialized = dumps_manifest(manifest)
        self.assertEqual(loads_manifest(serialized), manifest)
        self.assertEqual(dumps_manifest(loads_manifest(serialized)), serialized)
        parsed = json.loads(serialized)
        self.assertEqual(parsed["manifest_id"], manifest.manifest_id)
        self.assertEqual(parsed["build_id"], manifest.build.build_id)
        self.assertEqual(parsed["completeness"], "complete")

    def test_manifest_rejects_tampered_payload(self) -> None:
        data = json.loads(dumps_manifest(_sample_manifest()))
        data["build"]["goarch"] = "arm64"
        with self.assertRaisesRegex(ValueError, "build_id does not match"):
            loads_manifest(json.dumps(data))

    def test_manifest_rejects_tampered_nested_release_id(self) -> None:
        data = json.loads(dumps_manifest(_sample_manifest()))
        data["providers"][0]["release_id"] = "cgorelease:v1:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "release_id does not match"):
            loads_manifest(json.dumps(data))

    def test_manifest_codec_rejects_unknown_and_duplicate_fields(self) -> None:
        data = json.loads(dumps_manifest(_sample_manifest()))
        data["typo_field"] = 1
        with self.assertRaisesRegex(ValueError, "unknown"):
            loads_manifest(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            loads_manifest('{"schema_version": 1, "schema_version": 1}')

    def test_source_locations_never_embed_absolute_or_parent_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "workspace-relative"):
            SourceLocation("/tmp/header.h", 1)
        with self.assertRaisesRegex(ValueError, "workspace-relative"):
            SourceLocation("../header.h", 1)
        with self.assertRaisesRegex(ValueError, "workspace-relative"):
            SourceLocation(".", 1)

    def test_manifest_rejects_dangling_and_overlapping_bindings(self) -> None:
        manifest = _sample_manifest()
        binding = manifest.bindings[0]
        unresolved = UnresolvedBinding(
            package_id=binding.package_id,
            cgo_name=binding.cgo_name,
            reason=UnresolvedReason.MISSING_SIGNATURE,
        )
        with self.assertRaisesRegex(ValueError, "resolved and unresolved"):
            APIManifest(
                build=manifest.build,
                packages=manifest.packages,
                providers=manifest.providers,
                apis=manifest.apis,
                bindings=manifest.bindings,
                unresolved=(unresolved,),
            )
        dangling = APIBinding(
            package_id=binding.package_id,
            cgo_name="missing",
            api_id=_another_api().api_id,
            kind=BindingKind.DIRECT,
            linkage=Linkage.EXTERNAL,
        )
        with self.assertRaisesRegex(ValueError, "unknown API"):
            APIManifest(
                build=manifest.build,
                packages=manifest.packages,
                providers=manifest.providers,
                apis=manifest.apis,
                bindings=(dangling,),
            )

    def test_partial_manifest_cannot_be_used_as_complete(self) -> None:
        complete = _sample_manifest()
        unresolved = UnresolvedBinding(
            package_id=complete.packages[0].package_id,
            cgo_name="sqlite3_step",
            reason=UnresolvedReason.MISSING_SIGNATURE,
            detail="canonical signature unavailable",
        )
        manifest = APIManifest(
            build=complete.build,
            packages=complete.packages,
            providers=complete.providers,
            apis=complete.apis,
            bindings=complete.bindings,
            unresolved=(unresolved,),
        )
        self.assertEqual(manifest.completeness, ManifestCompleteness.PARTIAL)
        with self.assertRaisesRegex(ValueError, "partial"):
            manifest.require_complete()

    def test_non_intrinsic_api_cannot_use_intrinsic_binding(self) -> None:
        base = _sample_manifest()
        invalid = replace(
            base.bindings[0],
            kind=BindingKind.CGO_INTRINSIC,
            linkage=Linkage.INTRINSIC,
        )
        with self.assertRaisesRegex(ValueError, "non-intrinsic"):
            APIManifest(
                build=base.build,
                packages=base.packages,
                providers=base.providers,
                apis=base.apis,
                bindings=(invalid,),
            )


class ManifestResolutionTests(unittest.TestCase):
    def test_package_binding_is_exact_but_symbol_only_lookup_is_not(self) -> None:
        manifest = _sample_manifest()
        index = ManifestIndex(manifest)
        resolution = index.resolve_binding(
            "example.com/sqlitewrap",
            "sqlite3_bind_text",
        )
        api, binding = resolution.require_exact()
        self.assertEqual(api.api_id, manifest.apis[0].api_id)
        self.assertEqual(binding, manifest.bindings[0])
        candidate = index.resolve_symbol("sqlite3_bind_text")
        self.assertEqual(candidate.status, ResolutionStatus.CANDIDATE)
        with self.assertRaisesRegex(LookupError, "provider_id and signature_id"):
            candidate.require_exact()
        exact = index.resolve_symbol(
            "sqlite3_bind_text",
            provider_id=api.identity.provider.provider_id,
            signature_id=api.identity.signature.signature_id,
        )
        self.assertEqual(exact.status, ResolutionStatus.EXACT)

    def test_same_symbol_from_two_providers_remains_ambiguous(self) -> None:
        base = _sample_manifest()
        second_api = _another_api(symbol="sqlite3_bind_text")
        second_provider = ProviderRecord(second_api.identity.provider, version="1.0")
        manifest = APIManifest(
            build=base.build,
            packages=base.packages,
            providers=(*base.providers, second_provider),
            apis=(*base.apis, second_api),
            bindings=base.bindings,
        )
        result = ManifestIndex(manifest).resolve_symbol("sqlite3_bind_text")
        self.assertEqual(result.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(len(result.candidates), 2)

    def test_unresolved_binding_is_never_returned_as_exact(self) -> None:
        base = _sample_manifest()
        unresolved = UnresolvedBinding(
            package_id=base.packages[0].package_id,
            cgo_name="sqlite3_step",
            reason=UnresolvedReason.AMBIGUOUS_CANDIDATE,
            candidate_api_ids=(base.apis[0].api_id, _another_api().api_id),
        )
        another = _another_api()
        manifest = APIManifest(
            build=base.build,
            packages=base.packages,
            providers=(
                *base.providers,
                ProviderRecord(another.identity.provider, version="1.0"),
            ),
            apis=(*base.apis, another),
            bindings=base.bindings,
            unresolved=(unresolved,),
        )
        result = ManifestIndex(manifest).resolve_binding(
            "example.com/sqlitewrap",
            "sqlite3_step",
        )
        self.assertEqual(result.status, ResolutionStatus.UNRESOLVED)
        with self.assertRaises(LookupError):
            result.require_exact()


class ManifestAssemblerTests(unittest.TestCase):
    def test_assembler_enriches_layout_without_changing_api_identity(self) -> None:
        base = _sample_manifest()
        known = base.apis[0]
        signature = known.identity.signature
        sparse = ManifestAPI(
            APIIdentity(
                known.identity.provider,
                known.identity.symbol,
                CFunctionSignature(
                    CTypeIdentity(signature.result.canonical),
                    tuple(
                        CTypeIdentity(item.canonical)
                        for item in signature.parameters
                    ),
                    abi_tag=signature.abi_tag,
                ),
            ),
            known.declarations,
        )
        self.assertEqual(sparse.api_id, known.api_id)
        assembler = ManifestAssembler(base.build)
        assembler.add_provider(base.providers[0])
        assembler.add_api(sparse)
        assembler.add_api(known)
        merged = assembler.build_manifest().apis[0]
        self.assertEqual(merged.identity.signature.result.size_bits, 32)
        self.assertEqual(merged.identity.signature.parameters[0].size_bits, 64)

        conflicting = replace(
            known,
            identity=replace(
                known.identity,
                signature=replace(
                    signature,
                    result=CTypeIdentity("c:int", 64, 64),
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "conflicting size"):
            assembler.add_api(conflicting)

    def test_assembler_merges_evidence_and_resolution_replaces_unresolved(self) -> None:
        base = _sample_manifest()
        package = base.packages[0]
        provider = base.providers[0]
        api = base.apis[0]
        assembler = ManifestAssembler(base.build)
        assembler.add_package(package)
        assembler.add_provider(provider)
        assembler.add_api(api)
        assembler.add_unresolved(
            UnresolvedBinding(
                package.package_id,
                "sqlite3_bind_text",
                UnresolvedReason.MISSING_SIGNATURE,
            )
        )
        first = APIBinding(
            package.package_id,
            "sqlite3_bind_text",
            api.api_id,
            BindingKind.DIRECT,
            Linkage.EXTERNAL,
            use_sites=(SourceLocation("bind.go", 10, 2),),
        )
        second = APIBinding(
            package.package_id,
            "sqlite3_bind_text",
            api.api_id,
            BindingKind.DIRECT,
            Linkage.EXTERNAL,
            use_sites=(SourceLocation("bind.go", 20, 2),),
            directives=(CgoDirective.NOESCAPE,),
        )
        assembler.add_binding(first)
        assembler.add_binding(second)
        manifest = assembler.build_manifest()
        self.assertEqual(manifest.completeness, ManifestCompleteness.COMPLETE)
        self.assertEqual(len(manifest.bindings[0].use_sites), 2)
        self.assertEqual(manifest.bindings[0].directives, (CgoDirective.NOESCAPE,))

    def test_assembler_rejects_conflicting_exact_bindings(self) -> None:
        base = _sample_manifest()
        assembler = ManifestAssembler(base.build)
        for package in base.packages:
            assembler.add_package(package)
        for provider in base.providers:
            assembler.add_provider(provider)
        for api in base.apis:
            assembler.add_api(api)
        assembler.add_binding(base.bindings[0])
        conflict = APIBinding(
            base.bindings[0].package_id,
            base.bindings[0].cgo_name,
            _another_api().api_id,
            BindingKind.DIRECT,
            Linkage.EXTERNAL,
        )
        with self.assertRaisesRegex(ValueError, "conflicting exact bindings"):
            assembler.add_binding(conflict)


class ContractManifestLinkTests(unittest.TestCase):
    def test_exact_manifest_scope_links_contract_store(self) -> None:
        manifest = _sample_manifest()
        contract = _linked_contract(manifest)
        catalog = ContractCatalog(
            contracts=(contract,),
            manifest_id=manifest.manifest_id,
        )
        report = validate_contract_catalog(catalog, manifest)
        self.assertTrue(report.valid, report.issues)
        restored = loads_catalog(dumps_catalog(catalog))
        self.assertEqual(restored, catalog)
        store = ContractStore(catalog, manifest, require_linked=True)
        self.assertIs(
            store.for_binding("example.com/sqlitewrap", "sqlite3_bind_text"),
            contract,
        )

    def test_linker_rejects_unbound_catalog_and_wrong_arity(self) -> None:
        manifest = _sample_manifest()
        contract = _linked_contract(manifest, parameter_count=1)
        catalog = ContractCatalog(contracts=(contract,))
        report = validate_contract_catalog(catalog, manifest)
        self.assertFalse(report.valid)
        self.assertIn("unbound_catalog", {item.code for item in report.issues})
        self.assertIn("signature_arity_mismatch", {item.code for item in report.issues})
        with self.assertRaisesRegex(ValueError, "not linked"):
            ContractStore(catalog, manifest, require_linked=True)
        store = ContractStore(catalog, manifest)
        with self.assertRaisesRegex(RuntimeError, "valid Contract"):
            store.for_binding("example.com/sqlitewrap", "sqlite3_bind_text")

    def test_linker_rejects_parameter_type_drift(self) -> None:
        manifest = _sample_manifest()
        contract = _linked_contract(manifest)
        contract = replace(
            contract,
            parameters=(
                ParameterContract(0, "p0", "c:void*"),
                contract.parameters[1],
            ),
        )
        report = validate_contract_catalog(
            ContractCatalog(
                contracts=(contract,),
                manifest_id=manifest.manifest_id,
            ),
            manifest,
        )
        self.assertIn("signature_type_mismatch", {item.code for item in report.issues})


class ProjectManifestDiscoveryTests(unittest.TestCase):
    def test_discovery_is_independent_of_checkout_directory(self) -> None:
        with tempfile.TemporaryDirectory() as left_tmp, tempfile.TemporaryDirectory() as right_tmp:
            left = Path(left_tmp)
            right = Path(right_tmp)
            _write_cgo_fixture(left)
            _write_cgo_fixture(right)
            left_manifest = discover_project_manifest(left)
            right_manifest = discover_project_manifest(right)
            self.assertEqual(left_manifest.build.build_id, right_manifest.build.build_id)
            self.assertEqual(left_manifest.manifest_id, right_manifest.manifest_id)

    def test_discovery_records_exact_intrinsic_and_honest_unresolved_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cgo_fixture(root)
            manifest = discover_project_manifest(root, DiscoveryOptions(timeout_seconds=30))
            again = discover_project_manifest(root, DiscoveryOptions(timeout_seconds=30))
            self.assertEqual(manifest.manifest_id, again.manifest_id)
            self.assertEqual(manifest.completeness, ManifestCompleteness.PARTIAL)
            self.assertEqual(len(manifest.packages), 1)
            self.assertEqual(
                ManifestIndex(manifest)
                .resolve_binding("example.com/manifestfixture", "CString")
                .status,
                ResolutionStatus.EXACT,
            )
            unresolved = {
                item.cgo_name: item for item in manifest.unresolved
            }
            self.assertIn("add_one", unresolved)
            self.assertNotIn("fake_from_string", unresolved)
            self.assertIn(CgoDirective.NOESCAPE, unresolved["add_one"].directives)
            serialized = dumps_manifest(manifest)
            self.assertNotIn(str(root), serialized)
            self.assertIn("${PACKAGE}/include", serialized)
            self.assertIn('"FIXTURE"', serialized)
            self.assertEqual(loads_manifest(serialized), manifest)

    def test_manifest_cli_writes_and_verifies_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_cgo_fixture(root)
            output = root / "api-manifest.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(["manifest", str(root), "--out", str(output)])
            self.assertEqual(result, 0, stderr.getvalue())
            self.assertTrue(output.is_file())
            with redirect_stdout(stdout), redirect_stderr(stderr):
                verify_result = main(["manifest-verify", str(output)])
            self.assertEqual(verify_result, 0, stderr.getvalue())
            with redirect_stdout(stdout), redirect_stderr(stderr):
                strict_result = main(
                    [
                        "manifest",
                        str(root),
                        "--out",
                        str(output),
                        "--require-complete",
                    ]
                )
            self.assertEqual(strict_result, 2)
            self.assertIn("partial", stderr.getvalue())


def _sample_build() -> BuildContext:
    return BuildContext(
        goos="linux",
        goarch="amd64",
        abi=TargetABI(
            "x86_64-linux-gnu",
            64,
            Endianness.LITTLE,
            "LP64",
        ),
        toolchain=ToolchainIdentity("go1.25.0", "clang", "clang 20.0.0"),
        build_tags=("cgo", "sqlite"),
        cgo_cflags=("-O2",),
        cgo_cppflags=("-DSQLITE_THREADSAFE=1",),
        cgo_ldflags=("-lsqlite3",),
        macros=(MacroDefinition("SQLITE_THREADSAFE", "1"),),
    )


def _sqlite_provider() -> ProviderIdentity:
    return ProviderIdentity(ProviderKind.PKG_CONFIG, "sqlite.org", "sqlite3")


def _sample_api() -> ManifestAPI:
    identity = APIIdentity(
        provider=_sqlite_provider(),
        symbol="sqlite3_bind_text",
        signature=CFunctionSignature(
            result=CTypeIdentity("c:int", 32, 32),
            parameters=(
                CTypeIdentity("c:sqlite3_stmt*", 64, 64),
                CTypeIdentity("c:int", 32, 32),
            ),
            abi_tag="x86_64-linux-gnu",
        ),
    )
    return ManifestAPI(
        identity,
        declarations=(
            APIDeclaration(
                SourceLocation("include/sqlite3.h", 4578, 1, "a" * 64),
                "int sqlite3_bind_text(sqlite3_stmt *, int);",
                "include/sqlite3.h",
            ),
        ),
    )


def _another_api(symbol: str = "other") -> ManifestAPI:
    provider = ProviderIdentity(
        ProviderKind.SOURCE_BUNDLE,
        "example.com/other",
        "other",
    )
    return ManifestAPI(
        APIIdentity(
            provider,
            symbol,
            CFunctionSignature(
                CTypeIdentity("c:int", 32, 32),
                (CTypeIdentity("c:void*", 64, 64),),
                abi_tag="x86_64-linux-gnu",
            ),
        ),
        (
            APIDeclaration(
                SourceLocation("include/other.h", 1),
                f"int {symbol}(void *);",
                "include/other.h",
            ),
        ),
    )


def _sample_manifest() -> APIManifest:
    build = _sample_build()
    package = GoPackageRecord(
        GoPackageIdentity("example.com/sqlitewrap", "example.com/sqlitewrap"),
        "sqlitewrap",
        module_version="v1.2.3",
        module_sum="h1:fixture",
        source_sha256="b" * 64,
        files=("bind.go",),
        cgo_cflags=("-O2",),
        cgo_cppflags=("-DSQLITE_THREADSAFE=1",),
        cgo_ldflags=("-lsqlite3",),
        macros=(MacroDefinition("SQLITE_THREADSAFE", "1"),),
    )
    provider = ProviderRecord(
        _sqlite_provider(),
        version="3.45.1",
        abi_version="sqlite3",
        artifacts=(
            ProviderArtifact("pkg_config", "pkg-config:sqlite3"),
        ),
    )
    api = _sample_api()
    binding = APIBinding(
        package.package_id,
        "sqlite3_bind_text",
        api.api_id,
        BindingKind.DIRECT,
        Linkage.EXTERNAL,
        use_sites=(SourceLocation("bind.go", 42, 9, "c" * 64),),
        declaration_sites=(SourceLocation("bind.go", 5, 1, "c" * 64),),
        directives=(CgoDirective.NOESCAPE,),
    )
    return APIManifest(
        build=build,
        packages=(package,),
        providers=(provider,),
        apis=(api,),
        bindings=(binding,),
        generated_by="cgoprof-test",
    )


def _linked_contract(
    manifest: APIManifest,
    *,
    parameter_count: int = 2,
) -> APIContract:
    api = manifest.apis[0]
    provider = manifest.providers[0]
    return APIContract(
        api_id=api.api_id,
        c_symbol=api.identity.symbol,
        scope=BuildScope(
            go_package=manifest.packages[0].identity.import_path,
            goos=manifest.build.goos,
            goarch=manifest.build.goarch,
            build_tags=manifest.build.build_tags,
            c_macros_fingerprint=manifest.build.macros_fingerprint,
            library_version=provider.version,
            provider_release_id=provider.release_id,
            build_id=manifest.build.build_id,
        ),
        parameters=tuple(
            ParameterContract(index, f"p{index}", parameter.canonical)
            for index, parameter in enumerate(
                api.identity.signature.parameters[:parameter_count]
            )
        ),
    )


def _write_cgo_fixture(root: Path) -> None:
    (root / "include").mkdir()
    (root / "go.mod").write_text(
        "module example.com/manifestfixture\n\ngo 1.21\n",
        encoding="utf-8",
    )
    (root / "main.go").write_text(
        textwrap.dedent(
            """
            package main

            /*
            #cgo CFLAGS: -I${SRCDIR}/include -DFIXTURE=1
            #cgo noescape add_one
            static int add_one(int value) { return value + 1; }
            */
            import "C"

            const ignored = "C.fake_from_string()"

            func main() {
                _ = C.add_one(C.int(1))
                _ = C.CString("value")
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
