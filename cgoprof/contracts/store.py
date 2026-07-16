from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType
from typing import Iterator, Mapping

from .model import APIContract, ContractCatalog


class ContractStore:
    """Immutable lookup view over a validated contract catalog."""

    def __init__(self, catalog: ContractCatalog) -> None:
        by_id = {contract.api_id: contract for contract in catalog.contracts}
        by_symbol: dict[str, list[APIContract]] = defaultdict(list)
        for contract in catalog.contracts:
            by_symbol[contract.c_symbol].append(contract)
        self._catalog = catalog
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

    def get(self, api_id: str) -> APIContract | None:
        return self._by_id.get(api_id)

    def require(self, api_id: str) -> APIContract:
        contract = self.get(api_id)
        if contract is None:
            raise KeyError(f"unknown cgo API contract: {api_id}")
        return contract

    def for_symbol(self, c_symbol: str) -> tuple[APIContract, ...]:
        return self._by_symbol.get(c_symbol, ())

    def __contains__(self, api_id: object) -> bool:
        return api_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[APIContract]:
        return iter(self._catalog.contracts)
