from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .manifest import APIManifest
from .manifest_store import ManifestIndex
from .identity import normalize_c_type
from .model import APIContract, ContractCatalog


class LinkSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class ContractLinkIssue:
    severity: LinkSeverity
    code: str
    message: str
    api_id: str | None = None


@dataclass(frozen=True)
class ContractLinkReport:
    manifest_id: str
    issues: tuple[ContractLinkIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(item.severity == LinkSeverity.ERROR for item in self.issues)

    def require_valid(self) -> None:
        if self.valid:
            return
        messages = "; ".join(
            f"{item.code}: {item.message}"
            for item in self.issues
            if item.severity == LinkSeverity.ERROR
        )
        raise ValueError(f"contract catalog is not linked to its API manifest: {messages}")


def validate_contract_catalog(
    catalog: ContractCatalog,
    manifest: APIManifest,
) -> ContractLinkReport:
    index = ManifestIndex(manifest)
    issues: list[ContractLinkIssue] = []
    if catalog.manifest_id is None:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "unbound_catalog",
                "catalog has no manifest_id",
            )
        )
    elif catalog.manifest_id != manifest.manifest_id:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "manifest_mismatch",
                f"catalog names {catalog.manifest_id}, loaded {manifest.manifest_id}",
            )
        )
    for contract in catalog.contracts:
        api = index.get_api(contract.api_id)
        if api is None:
            issues.append(
                ContractLinkIssue(
                    LinkSeverity.ERROR,
                    "unknown_api",
                    "contract api_id is absent from the manifest",
                    contract.api_id,
                )
            )
            continue
        issues.extend(_validate_contract_scope(contract, manifest, index))
        signature = api.identity.signature
        expected_parameters = len(signature.parameters)
        if len(contract.parameters) != expected_parameters:
            issues.append(
                ContractLinkIssue(
                    LinkSeverity.ERROR,
                    "signature_arity_mismatch",
                    f"contract has {len(contract.parameters)} parameters, "
                    f"manifest signature has {expected_parameters}",
                    contract.api_id,
                )
            )
        else:
            for parameter, expected_type in zip(
                contract.parameters,
                signature.parameters,
            ):
                if normalize_c_type(parameter.c_type) != expected_type.canonical:
                    issues.append(
                        ContractLinkIssue(
                            LinkSeverity.ERROR,
                            "signature_type_mismatch",
                            f"parameter {parameter.index} type {parameter.c_type!r} "
                            f"!= manifest canonical type {expected_type.canonical!r}",
                            contract.api_id,
                        )
                    )
        if (
            contract.result is not None
            and normalize_c_type(contract.result.c_type) != signature.result.canonical
        ):
            issues.append(
                ContractLinkIssue(
                    LinkSeverity.ERROR,
                    "result_type_mismatch",
                    f"result type {contract.result.c_type!r} != manifest canonical "
                    f"type {signature.result.canonical!r}",
                    contract.api_id,
                )
            )
        if contract.c_symbol != api.identity.symbol:
            issues.append(
                ContractLinkIssue(
                    LinkSeverity.ERROR,
                    "symbol_mismatch",
                    f"contract symbol {contract.c_symbol!r} != manifest symbol "
                    f"{api.identity.symbol!r}",
                    contract.api_id,
                )
            )
    return ContractLinkReport(
        manifest_id=manifest.manifest_id,
        issues=tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.severity.value,
                    item.code,
                    item.api_id or "",
                    item.message,
                ),
            )
        ),
    )


def _validate_contract_scope(
    contract: APIContract,
    manifest: APIManifest,
    index: ManifestIndex,
) -> list[ContractLinkIssue]:
    issues: list[ContractLinkIssue] = []
    scope = contract.scope
    build = manifest.build
    if scope.build_id is None:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "missing_build_id",
                "contract scope has no exact build_id",
                contract.api_id,
            )
        )
    elif scope.build_id != build.build_id:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "build_mismatch",
                f"contract build is {scope.build_id}, manifest build is {build.build_id}",
                contract.api_id,
            )
        )
    if not scope.goos:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "missing_goos",
                "contract scope has no GOOS",
                contract.api_id,
            )
        )
    elif scope.goos != build.goos:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "goos_mismatch",
                f"contract GOOS {scope.goos!r} != manifest GOOS {build.goos!r}",
                contract.api_id,
            )
        )
    if not scope.goarch:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "missing_goarch",
                "contract scope has no GOARCH",
                contract.api_id,
            )
        )
    elif scope.goarch != build.goarch:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "goarch_mismatch",
                f"contract GOARCH {scope.goarch!r} != manifest GOARCH {build.goarch!r}",
                contract.api_id,
            )
        )
    if scope.build_tags != build.build_tags:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "build_tags_mismatch",
                "contract and manifest build tags differ",
                contract.api_id,
            )
        )
    if not scope.c_macros_fingerprint:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "missing_macros_fingerprint",
                "contract scope has no exact C macro fingerprint",
                contract.api_id,
            )
        )
    elif scope.c_macros_fingerprint != build.macros_fingerprint:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "macros_mismatch",
                "contract and manifest C macro fingerprints differ",
                contract.api_id,
            )
        )
    if not scope.go_package:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "missing_go_package",
                "contract scope has no Go package import path",
                contract.api_id,
            )
        )
    else:
        package_bindings = [
            binding
            for binding in manifest.bindings
            if binding.api_id == contract.api_id
            and any(
                package.package_id == binding.package_id
                and package.identity.import_path == scope.go_package
                for package in manifest.packages
            )
        ]
        if not package_bindings:
            issues.append(
                ContractLinkIssue(
                    LinkSeverity.ERROR,
                    "package_binding_mismatch",
                    f"manifest does not bind this API in Go package {scope.go_package!r}",
                    contract.api_id,
                )
            )
    api = index.require_api(contract.api_id)
    provider = next(
        item
        for item in manifest.providers
        if item.provider_id == api.identity.provider.provider_id
    )
    if scope.provider_release_id is None:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "missing_provider_release_id",
                "contract scope has no exact provider release id",
                contract.api_id,
            )
        )
    elif scope.provider_release_id != provider.release_id:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "provider_release_mismatch",
                f"contract provider release {scope.provider_release_id} != "
                f"manifest provider release {provider.release_id}",
                contract.api_id,
            )
        )
    if scope.library_version is not None and scope.library_version != provider.version:
        issues.append(
            ContractLinkIssue(
                LinkSeverity.ERROR,
                "library_version_mismatch",
                f"contract library version {scope.library_version!r} != "
                f"manifest provider version {provider.version!r}",
                contract.api_id,
            )
        )
    return issues
