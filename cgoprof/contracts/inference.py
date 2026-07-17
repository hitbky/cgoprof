from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .annotations import (
    AnnotationBundle,
    AnnotationPolicy,
    apply_annotation,
)
from .c_analysis import CAnalysisResult, contracts_from_c_analysis
from .intrinsics import intrinsic_contracts_for_package
from .manifest import APIManifest
from .manifest_link import ContractLinkReport, validate_contract_catalog
from .merge import merge_contracts
from .model import APIContract, ContractCatalog


@dataclass(frozen=True)
class ContractInferenceResult:
    catalog: ContractCatalog
    link_report: ContractLinkReport
    diagnostics: tuple[str, ...] = ()
    source_counts: tuple[tuple[str, int], ...] = ()
    missing_api_ids: tuple[str, ...] = ()

    @property
    def coverage_complete(self) -> bool:
        return not self.missing_api_ids

    def require_valid(self) -> ContractCatalog:
        self.link_report.require_valid()
        return self.catalog


def infer_contract_catalog(
    manifest: APIManifest,
    go_package: str,
    *,
    c_analyses: Sequence[CAnalysisResult] = (),
    annotation_bundle: AnnotationBundle | None = None,
    annotation_policy: AnnotationPolicy | None = None,
) -> ContractInferenceResult:
    """Compose all currently available proof/declaration sources.

    Catalogs are intentionally package-scoped: API identity is provider/ABI
    scoped while BuildScope also names the package-local cgo binding.
    """

    contracts: dict[str, APIContract] = {}
    diagnostics: list[str] = []
    source_counts = {"intrinsic": 0, "c_analysis": 0, "annotation": 0}

    for contract in intrinsic_contracts_for_package(manifest, go_package):
        contracts[contract.api_id] = contract
        source_counts["intrinsic"] += 1

    for contract in contracts_from_c_analysis(manifest, c_analyses, go_package):
        existing = contracts.get(contract.api_id)
        if existing is None:
            contracts[contract.api_id] = contract
        else:
            merged = merge_contracts(existing, contract)
            contracts[contract.api_id] = merged.contract
            diagnostics.extend(merged.diagnostics)
        source_counts["c_analysis"] += 1

    if annotation_bundle is not None:
        for annotation in annotation_bundle.annotations:
            if annotation.scope.manifest_id != manifest.manifest_id:
                diagnostics.append(
                    f"ignored annotation {annotation.annotation_id}: manifest mismatch"
                )
                continue
            if annotation.scope.go_package != go_package:
                continue
            merged = apply_annotation(
                annotation,
                manifest,
                contracts.get(annotation.scope.api_id),
                policy=annotation_policy,
            )
            contracts[annotation.scope.api_id] = merged.contract
            diagnostics.extend(merged.diagnostics)
            source_counts["annotation"] += 1

    package = next(
        item for item in manifest.packages if item.identity.import_path == go_package
    )
    expected_api_ids = {
        item.api_id
        for item in manifest.bindings
        if item.package_id == package.package_id
    }
    missing_api_ids = tuple(sorted(expected_api_ids - set(contracts)))
    diagnostics.extend(
        f"missing_contract_source: no Contract facts for {api_id}"
        for api_id in missing_api_ids
    )
    catalog = ContractCatalog(
        contracts=tuple(contracts.values()),
        generated_by="cgoprof contract inference",
        manifest_id=manifest.manifest_id,
        metadata=(
            ("go_package", go_package),
            ("source.c_analysis", str(source_counts["c_analysis"])),
            ("source.annotation", str(source_counts["annotation"])),
            ("source.intrinsic", str(source_counts["intrinsic"])),
        ),
    )
    report = validate_contract_catalog(catalog, manifest)
    diagnostics.extend(
        f"{item.code}: {item.message}" for item in report.issues
    )
    return ContractInferenceResult(
        catalog,
        report,
        tuple(sorted(set(diagnostics))),
        tuple(sorted(source_counts.items())),
        missing_api_ids,
    )
