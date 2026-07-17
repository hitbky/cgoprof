from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest

from cgoprof.contracts import (
    AnnotationAssignment,
    AnnotationBundle,
    AnnotationClause,
    AnnotationPolicy,
    AnnotationProvenance,
    AnnotationScope,
    AnnotationTrust,
    ArgumentCondition,
    Callback,
    ClangContractAnalyzer,
    CFrontendOptions,
    CgoDirective,
    ContractAnnotation,
    ContractAttribute,
    ContractTarget,
    ContractTargetKind,
    ConditionOperator,
    Escape,
    FactStatus,
    MemoryAccess,
    Ownership,
    ProviderArtifact,
    ProviderIdentity,
    ProviderKind,
    ProviderRecord,
    RepresentationKind,
    analyze_package_translation_units,
    apply_annotation,
    augment_manifest_with_c_analysis,
    discover_project_manifest,
    dumps_annotation_bundle,
    extract_cgo_preambles,
    infer_contract_catalog,
    intrinsic_contracts_for_package,
    loads_annotation_bundle,
    validate_contract_catalog,
)


class IntrinsicContractTests(unittest.TestCase):
    def test_every_cgo_pseudo_function_has_a_complete_exact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_intrinsic_fixture(root)
            manifest = _discover(root)
            contracts = intrinsic_contracts_for_package(manifest, "example.com/intrinsics")
            self.assertEqual(
                {item.c_symbol for item in contracts},
                {"CString", "CBytes", "GoString", "GoStringN", "GoBytes"},
            )
            catalog = infer_contract_catalog(
                manifest, "example.com/intrinsics"
            ).require_valid()
            self.assertEqual(len(catalog.contracts), 5)
            for contract in contracts:
                self.assertEqual(contract.callback.value, Callback.NO_CALLBACK)
                self.assertEqual(contract.callback.status, FactStatus.INTRINSIC)
                self.assertTrue(contract.scope.provider_release_id)
                self.assertTrue(contract.scope.build_id)
                for parameter in contract.parameters:
                    for fact in (
                        parameter.contract.memory_access,
                        parameter.contract.ownership,
                        parameter.contract.lifetime,
                        parameter.contract.escape,
                        parameter.contract.mutability,
                        parameter.contract.representation,
                    ):
                        self.assertEqual(fact.status, FactStatus.INTRINSIC)
            report = validate_contract_catalog(catalog, manifest)
            self.assertTrue(report.valid, report.issues)


class AnnotationContractTests(unittest.TestCase):
    def test_versioned_annotation_round_trip_and_conflict_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_intrinsic_fixture(root)
            manifest = _discover(root)
            base = next(
                item
                for item in intrinsic_contracts_for_package(
                    manifest, "example.com/intrinsics"
                )
                if item.c_symbol == "CString"
            )
            annotation = _annotation(
                manifest,
                base.api_id,
                AnnotationAssignment(
                    ContractTarget(ContractTargetKind.RESULT),
                    ContractAttribute.OWNERSHIP,
                    Ownership.CALLEE_OWNED,
                    "library wrapper claims it retains the allocation",
                ),
            )
            bundle = AnnotationBundle((annotation,))
            serialized = dumps_annotation_bundle(bundle)
            self.assertEqual(loads_annotation_bundle(serialized), bundle)
            self.assertEqual(dumps_annotation_bundle(loads_annotation_bundle(serialized)), serialized)

            merged = apply_annotation(annotation, manifest, base).contract
            self.assertIsNotNone(merged.result)
            assert merged.result is not None
            fact = merged.result.contract.ownership
            self.assertEqual(fact.value, Ownership.UNKNOWN)
            self.assertEqual(fact.status, FactStatus.CONFLICT)
            self.assertEqual(
                {item.kind.value for item in fact.evidence},
                {"cgo_intrinsic", "user_annotation"},
            )

            tampered = json.loads(serialized)
            tampered["annotations"][0]["assignments"][0]["justification"] = "tampered"
            with self.assertRaisesRegex(ValueError, "content id does not match"):
                loads_annotation_bundle(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                loads_annotation_bundle('{"schema_version":1,"schema_version":1}')

    def test_annotation_trust_gate_rejects_unreviewed_material(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_intrinsic_fixture(root)
            manifest = _discover(root)
            contract = next(
                item
                for item in intrinsic_contracts_for_package(
                    manifest, "example.com/intrinsics"
                )
                if item.c_symbol == "GoBytes"
            )
            annotation = _annotation(
                manifest,
                contract.api_id,
                AnnotationAssignment(
                    ContractTarget(ContractTargetKind.PARAMETER, 0),
                    ContractAttribute.ESCAPE,
                    Escape.MAY_ESCAPE,
                    "external documentation permits retention",
                ),
                trust=AnnotationTrust.UNTRUSTED,
            )
            with self.assertRaises(PermissionError):
                apply_annotation(
                    annotation,
                    manifest,
                    policy=AnnotationPolicy(AnnotationTrust.REVIEWED),
                )

    def test_annotation_conditional_clause_is_typed_and_range_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_intrinsic_fixture(root)
            manifest = _discover(root)
            contract = next(
                item
                for item in intrinsic_contracts_for_package(
                    manifest, "example.com/intrinsics"
                )
                if item.c_symbol == "GoBytes"
            )
            api = next(item for item in manifest.apis if item.api_id == contract.api_id)
            provider = next(
                item
                for item in manifest.providers
                if item.provider_id == api.identity.provider.provider_id
            )
            assignment = AnnotationAssignment(
                ContractTarget(ContractTargetKind.PARAMETER, 0),
                ContractAttribute.MEMORY_ACCESS,
                MemoryAccess.NONE,
                "zero length does not dereference the pointer",
            )
            annotation = ContractAnnotation(
                AnnotationScope(
                    manifest.manifest_id,
                    manifest.build.build_id,
                    provider.release_id,
                    contract.api_id,
                    "example.com/intrinsics",
                ),
                AnnotationProvenance(
                    "Contract Team",
                    "docs/intrinsic-audit.md",
                    "git:conditional",
                    AnnotationTrust.REVIEWED,
                    reviewed_by=("Reviewer A",),
                ),
                clauses=(
                    AnnotationClause(
                        (ArgumentCondition(1, ConditionOperator.EQ, 0),),
                        (assignment,),
                    ),
                ),
            )
            contribution = apply_annotation(annotation, manifest).contract
            self.assertEqual(len(contribution.clauses), 1)
            self.assertEqual(
                contribution.clauses[0].assignments[0].fact.status,
                FactStatus.DECLARED,
            )
            invalid = ContractAnnotation(
                annotation.scope,
                annotation.provenance,
                clauses=(
                    AnnotationClause(
                        (ArgumentCondition(9, ConditionOperator.EQ, 0),),
                        (assignment,),
                    ),
                ),
            )
            with self.assertRaisesRegex(ValueError, "unknown parameter 9"):
                apply_annotation(invalid, manifest)


class CSignatureAndSummaryTests(unittest.TestCase):
    def test_signature_resolution_and_interprocedural_effect_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_c_analysis_fixture(root)
            manifest = _discover(root)
            self.assertEqual(len(manifest.unresolved), 5)
            empty_inference = infer_contract_catalog(
                manifest, "example.com/canalysis"
            )
            # Unresolved selectors are not expected exact APIs yet, so coverage
            # is evaluated again after signature enrichment below.
            self.assertTrue(empty_inference.coverage_complete)
            package_analysis = analyze_package_translation_units(
                root, manifest, "example.com/canalysis"
            )
            self.assertFalse(
                [item for item in package_analysis.diagnostics if item.severity.value == "error"],
                package_analysis.diagnostics,
            )
            provider = ProviderRecord(
                ProviderIdentity(
                    ProviderKind.SOURCE_BUNDLE,
                    "example.com/canalysis",
                    "fixture-c",
                ),
                version="fixture-1",
                abi_version=manifest.build.abi.target_triple,
                artifacts=(
                    ProviderArtifact(
                        "source_bundle",
                        "fixture.c",
                        _sha256(root / "fixture.c"),
                    ),
                ),
            )
            resolved = augment_manifest_with_c_analysis(
                manifest,
                package_analysis.analyses,
                provider,
                "example.com/canalysis",
                definitions_only=True,
            )
            self.assertEqual(len(resolved.unresolved), 0)
            self.assertEqual(len(resolved.bindings), 5)
            missing = infer_contract_catalog(resolved, "example.com/canalysis")
            self.assertFalse(missing.coverage_complete)
            self.assertEqual(len(missing.missing_api_ids), 5)
            inferred = infer_contract_catalog(
                resolved,
                "example.com/canalysis",
                c_analyses=package_analysis.analyses,
            )
            catalog = inferred.require_valid()
            contracts = {item.c_symbol: item for item in catalog.contracts}
            self.assertEqual(set(contracts), {"read_only", "retain", "allocate", "outer", "unknown_bridge"})

            read_only = contracts["read_only"]
            self.assertEqual(read_only.parameters[0].contract.memory_access.value, MemoryAccess.READ)
            self.assertEqual(read_only.parameters[0].contract.escape.value, Escape.NO_ESCAPE)
            self.assertEqual(read_only.callback.value, Callback.NO_CALLBACK)

            retain = contracts["retain"]
            self.assertEqual(retain.parameters[0].contract.memory_access.value, MemoryAccess.NONE)
            self.assertEqual(retain.parameters[0].contract.escape.value, Escape.ESCAPES)

            allocate = contracts["allocate"]
            assert allocate.result is not None
            self.assertEqual(allocate.result.contract.ownership.value, Ownership.CALLER_OWNED)
            self.assertEqual(
                allocate.result.contract.representation.value.kind,
                RepresentationKind.RAW_BYTES,
            )

            outer = contracts["outer"]
            self.assertEqual(outer.parameters[0].contract.memory_access.value, MemoryAccess.READ_WRITE)
            self.assertEqual(outer.parameters[0].contract.escape.value, Escape.ESCAPES)
            self.assertEqual(outer.callback.value, Callback.SYNCHRONOUS)
            self.assertEqual(
                outer.parameters[1].contract.representation.value.kind,
                RepresentationKind.FUNCTION_POINTER,
            )

            unknown = contracts["unknown_bridge"]
            self.assertEqual(unknown.parameters[0].contract.memory_access.value, MemoryAccess.READ_WRITE)
            self.assertEqual(unknown.parameters[0].contract.escape.value, Escape.MAY_ESCAPE)
            self.assertEqual(unknown.callback.value, Callback.MAY_CALLBACK)
            self.assertEqual(dict(unknown.metadata)["analysis_complete"], "false")

            directed = replace(
                resolved,
                bindings=tuple(
                    replace(
                        binding,
                        directives=(
                            CgoDirective.NOESCAPE,
                            CgoDirective.NOCALLBACK,
                        ),
                    )
                    if binding.cgo_name == "unknown_bridge"
                    else binding
                    for binding in resolved.bindings
                ),
            )
            directed_catalog = infer_contract_catalog(
                directed,
                "example.com/canalysis",
                c_analyses=package_analysis.analyses,
            ).require_valid()
            directed_unknown = next(
                item
                for item in directed_catalog.contracts
                if item.c_symbol == "unknown_bridge"
            )
            self.assertEqual(
                directed_unknown.parameters[0].contract.escape.value,
                Escape.MAY_ESCAPE,
            )
            self.assertEqual(
                directed_unknown.parameters[0].contract.escape.status,
                FactStatus.CONFLICT,
            )
            self.assertEqual(directed_unknown.callback.value, Callback.MAY_CALLBACK)
            self.assertEqual(directed_unknown.callback.status, FactStatus.CONFLICT)

    def test_cgo_preamble_extraction_strips_only_cgo_directives(self) -> None:
        source = textwrap.dedent(
            '''
            package fixture
            /*
            #cgo CFLAGS: -DFEATURE=1
            #include "fixture.h"
            int work(int *p);
            */
            import "C"
            '''
        )
        preambles = extract_cgo_preambles(source)
        self.assertEqual(len(preambles), 1)
        self.assertNotIn("#cgo", preambles[0].source)
        self.assertIn('#include "fixture.h"', preambles[0].source)
        self.assertIn("int work(int *p);", preambles[0].source)

    def test_typedef_layout_alias_free_and_c_string_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_intrinsic_fixture(root)
            manifest = _discover(root)
            analyzer = ClangContractAnalyzer(
                root,
                manifest.build,
                CFrontendOptions(clang_binary="clang", target_triple=None),
            )
            result = analyzer.analyze_source(
                textwrap.dedent(
                    '''
                    #include <stdlib.h>
                    #include <string.h>
                    typedef unsigned long word_t;
                    typedef struct Record { int value; } Record;

                    word_t identity(word_t value) { return value; }
                    int record_value(const Record *record) { return record->value; }
                    void release(char *value) {
                        char *alias = value;
                        free(alias);
                    }
                    int string_size(const char *value) {
                        return (int)strlen(value);
                    }
                    void recursive_write(int *value, int count) {
                        if (count) {
                            *value += 1;
                            recursive_write(value, count - 1);
                        }
                    }
                    static void (*saved_callback)(int);
                    void register_callback(void (*callback)(int)) {
                        saved_callback = callback;
                    }
                    void branch_alias(int *left, int *right, int choose) {
                        int *selected = left;
                        if (choose) selected = right;
                        *selected = 1;
                    }
                    void publish_pointer(int *value, int **output) {
                        *output = value;
                    }
                    '''
                ),
                symbols=(
                    "identity",
                    "record_value",
                    "release",
                    "string_size",
                    "recursive_write",
                    "register_callback",
                    "branch_alias",
                    "publish_pointer",
                ),
            )
            identity = result.summary_for("identity")
            self.assertEqual(
                identity.function.parameters[0].canonical_type.canonical,
                "c:unsigned long",
            )
            self.assertIsNotNone(
                identity.function.parameters[0].canonical_type.size_bits
            )
            record = result.summary_for("record_value")
            self.assertEqual(
                record.function.parameters[0].canonical_type.canonical,
                "c:const struct Record*",
            )
            release = result.summary_for("release")
            self.assertEqual(
                release.parameters[0].ownership,
                Ownership.TRANSFERRED_TO_CALLEE,
            )
            string_size = result.summary_for("string_size")
            self.assertEqual(string_size.parameters[0].memory_access, MemoryAccess.READ)
            self.assertEqual(
                string_size.parameters[0].representation.kind,
                RepresentationKind.C_STRING,
            )
            recursive = result.summary_for("recursive_write")
            self.assertTrue(recursive.complete)
            self.assertEqual(
                recursive.parameters[0].memory_access,
                MemoryAccess.READ_WRITE,
            )
            registration = result.summary_for("register_callback")
            self.assertEqual(registration.callback, Callback.ASYNCHRONOUS)
            self.assertEqual(registration.parameters[0].escape, Escape.ESCAPES)
            branch = result.summary_for("branch_alias")
            self.assertEqual(branch.parameters[0].memory_access, MemoryAccess.WRITE)
            self.assertEqual(branch.parameters[1].memory_access, MemoryAccess.WRITE)
            publish = result.summary_for("publish_pointer")
            self.assertEqual(publish.parameters[0].escape, Escape.ESCAPES)
            self.assertEqual(publish.parameters[1].memory_access, MemoryAccess.WRITE)


def _annotation(
    manifest,
    api_id: str,
    assignment: AnnotationAssignment,
    *,
    trust: AnnotationTrust = AnnotationTrust.REVIEWED,
) -> ContractAnnotation:
    api = next(item for item in manifest.apis if item.api_id == api_id)
    provider = next(
        item
        for item in manifest.providers
        if item.provider_id == api.identity.provider.provider_id
    )
    return ContractAnnotation(
        AnnotationScope(
            manifest.manifest_id,
            manifest.build.build_id,
            provider.release_id,
            api_id,
            "example.com/intrinsics",
        ),
        AnnotationProvenance(
            "Contract Team",
            "docs/intrinsic-audit.md",
            "git:0123456789",
            trust,
            reviewed_by=("Reviewer A",) if trust != AnnotationTrust.UNTRUSTED else (),
        ),
        assignments=(assignment,),
    )


def _discover(root: Path):
    previous = os.environ.get("GOCACHE")
    os.environ["GOCACHE"] = "/private/tmp/go-build-cache"
    try:
        return discover_project_manifest(root)
    finally:
        if previous is None:
            os.environ.pop("GOCACHE", None)
        else:
            os.environ["GOCACHE"] = previous


def _write_intrinsic_fixture(root: Path) -> None:
    (root / "go.mod").write_text("module example.com/intrinsics\n\ngo 1.23\n", encoding="utf-8")
    (root / "main.go").write_text(
        textwrap.dedent(
            '''
            package main
            /*
            #include <stdlib.h>
            */
            import "C"
            import "unsafe"

            func main() {
                cs := C.CString("x")
                _ = C.CBytes([]byte("x"))
                _ = C.GoString(cs)
                _ = C.GoStringN(cs, 1)
                _ = C.GoBytes(unsafe.Pointer(cs), 1)
            }
            '''
        ),
        encoding="utf-8",
    )


def _write_c_analysis_fixture(root: Path) -> None:
    (root / "go.mod").write_text("module example.com/canalysis\n\ngo 1.23\n", encoding="utf-8")
    (root / "main.go").write_text(
        textwrap.dedent(
            '''
            package main
            /*
            int read_only(const int *p);
            void retain(int *p);
            char *allocate(int n);
            int outer(int *p, void (*cb)(int));
            int unknown_bridge(int *p);
            */
            import "C"

            func main() {
                _ = C.read_only(nil)
                C.retain(nil)
                _ = C.allocate(8)
                _ = C.outer(nil, nil)
                _ = C.unknown_bridge(nil)
            }
            '''
        ),
        encoding="utf-8",
    )
    (root / "fixture.c").write_text(
        textwrap.dedent(
            '''
            #include <stdlib.h>
            static int helper(const int *p) { return *p; }
            static int *saved;
            int external_effect(int *p);

            int read_only(const int *p) { return helper(p); }
            void retain(int *p) { saved = p; }
            char *allocate(int n) { return (char *)malloc((unsigned long)n); }
            int outer(int *p, void (*cb)(int)) {
                *p += 1;
                cb(*p);
                retain(p);
                return *p;
            }
            int unknown_bridge(int *p) { return external_effect(p); }
            '''
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
