from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Iterator, Mapping

from .manifest import APIManifest
from .manifest_link import ContractLinkReport, validate_contract_catalog
from .manifest_store import ManifestIndex, ResolutionStatus
from .model import APIContract, ContractCatalog


class ContractStore:
    """Immutable lookup view over a validated contract catalog."""

    def __init__(
        self,
        catalog: ContractCatalog,
        manifest: APIManifest | None = None,
        *,
        require_linked: bool = False,
    ) -> None:
        if require_linked and manifest is None:
            raise ValueError("require_linked=True requires an API manifest")
        by_id = {contract.api_id: contract for contract in catalog.contracts}
        by_symbol: dict[str, list[APIContract]] = defaultdict(list)
        for contract in catalog.contracts:
            by_symbol[contract.c_symbol].append(contract)
        self._catalog = catalog
        self._manifest_index = None if manifest is None else ManifestIndex(manifest)
        self._link_report = (
            None if manifest is None else validate_contract_catalog(catalog, manifest)
        )
        if require_linked and self._link_report is not None:
            self._link_report.require_valid()
        self._by_id: Mapping[str, APIContract] = MappingProxyType(by_id)
        self._by_symbol: Mapping[str, tuple[APIContract, ...]] = MappingProxyType(
            {
                symbol: tuple(sorted(items, key=lambda item: item.api_id))
                for symbol, items in by_symbol.items()
            }
        )

    @property
    def catalog(self) -> ContractCatalog:
        return self._catalog

    @property
    def link_report(self) -> ContractLinkReport | None:
        return self._link_report

    def get(self, api_id: str) -> APIContract | None:
        return self._by_id.get(api_id)

    def require(self, api_id: str) -> APIContract:
        contract = self.get(api_id)
        if contract is None:
            raise KeyError(f"unknown cgo API contract: {api_id}")
        return contract

    def for_symbol(self, c_symbol: str) -> tuple[APIContract, ...]:
        return self._by_symbol.get(c_symbol, ())

    def for_binding(self, import_path: str, cgo_name: str) -> APIContract | None:
        if self._manifest_index is None:
            raise RuntimeError("binding lookup requires a linked API manifest")
        if self._link_report is None or not self._link_report.valid:
            raise RuntimeError("binding lookup requires a valid Contract–Manifest link")
        resolution = self._manifest_index.resolve_binding(import_path, cgo_name)
        if resolution.status == ResolutionStatus.UNRESOLVED:
            raise LookupError(resolution.detail)
        if resolution.status != ResolutionStatus.EXACT or resolution.api is None:
            return None
        return self.get(resolution.api.api_id)

    def __contains__(self, api_id: object) -> bool:
        return api_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[APIContract]:
        return iter(self._catalog.contracts)
