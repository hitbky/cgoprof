from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .manifest import APIBinding, APIManifest, ManifestAPI, UnresolvedBinding


class ResolutionStatus(str, Enum):
    EXACT = "exact"
    CANDIDATE = "candidate"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    api: ManifestAPI | None = None
    binding: APIBinding | None = None
    unresolved: UnresolvedBinding | None = None
    candidates: tuple[ManifestAPI, ...] = ()
    detail: str = ""

    def require_exact(self) -> tuple[ManifestAPI, APIBinding | None]:
        if self.status != ResolutionStatus.EXACT or self.api is None:
            raise LookupError(self.detail or f"API resolution is {self.status.value}")
        return self.api, self.binding


class ManifestIndex:
    """Immutable, ambiguity-preserving lookup view over one build manifest."""

    def __init__(self, manifest: APIManifest) -> None:
        packages_by_import = {
            item.identity.import_path: item for item in manifest.packages
        }
        apis_by_id = {item.api_id: item for item in manifest.apis}
        bindings_by_selector: dict[tuple[str, str], APIBinding] = {}
        unresolved_by_selector: dict[tuple[str, str], UnresolvedBinding] = {}
        apis_by_symbol: dict[str, list[ManifestAPI]] = defaultdict(list)
        for api in manifest.apis:
            names = {
                api.identity.symbol,
                api.identity.linkage_name or api.identity.symbol,
                *api.aliases,
            }
            for name in names:
                apis_by_symbol[name].append(api)
        for binding in manifest.bindings:
            bindings_by_selector[(binding.package_id, binding.cgo_name)] = binding
        for item in manifest.unresolved:
            unresolved_by_selector[(item.package_id, item.cgo_name)] = item
        self._manifest = manifest
        self._packages_by_import = MappingProxyType(packages_by_import)
        self._apis_by_id: Mapping[str, ManifestAPI] = MappingProxyType(apis_by_id)
        self._bindings_by_selector = MappingProxyType(bindings_by_selector)
        self._unresolved_by_selector = MappingProxyType(unresolved_by_selector)
        self._apis_by_symbol: Mapping[str, tuple[ManifestAPI, ...]] = MappingProxyType(
            {
                symbol: tuple(sorted(items, key=lambda item: item.api_id))
                for symbol, items in apis_by_symbol.items()
            }
        )

    @property
    def manifest(self) -> APIManifest:
        return self._manifest

    def get_api(self, api_id: str) -> ManifestAPI | None:
        return self._apis_by_id.get(api_id)

    def require_api(self, api_id: str) -> ManifestAPI:
        api = self.get_api(api_id)
        if api is None:
            raise KeyError(f"unknown manifest API: {api_id}")
        return api

    def resolve_binding(self, import_path: str, cgo_name: str) -> ResolutionResult:
        package = self._packages_by_import.get(import_path)
        if package is None:
            return ResolutionResult(
                ResolutionStatus.NOT_FOUND,
                detail=f"manifest has no Go package {import_path!r}",
            )
        selector = (package.package_id, cgo_name)
        binding = self._bindings_by_selector.get(selector)
        if binding is not None:
            api = self._apis_by_id[binding.api_id]
            return ResolutionResult(
                ResolutionStatus.EXACT,
                api=api,
                binding=binding,
                candidates=(api,),
            )
        unresolved = self._unresolved_by_selector.get(selector)
        if unresolved is not None:
            candidates = tuple(
                self._apis_by_id[item] for item in unresolved.candidate_api_ids
            )
            return ResolutionResult(
                ResolutionStatus.UNRESOLVED,
                unresolved=unresolved,
                candidates=candidates,
                detail=unresolved.detail
                or f"C.{cgo_name} is unresolved: {unresolved.reason.value}",
            )
        return ResolutionResult(
            ResolutionStatus.NOT_FOUND,
            detail=f"manifest has no binding for {import_path}.C.{cgo_name}",
        )

    def resolve_symbol(
        self,
        symbol: str,
        *,
        provider_id: str | None = None,
        signature_id: str | None = None,
    ) -> ResolutionResult:
        candidates = self._apis_by_symbol.get(symbol, ())
        if provider_id is not None:
            candidates = tuple(
                item
                for item in candidates
                if item.identity.provider.provider_id == provider_id
            )
        if signature_id is not None:
            candidates = tuple(
                item
                for item in candidates
                if item.identity.signature.signature_id == signature_id
            )
        if not candidates:
            return ResolutionResult(
                ResolutionStatus.NOT_FOUND,
                detail=f"no API matches C symbol {symbol!r}",
            )
        exact_query = provider_id is not None and signature_id is not None
        if exact_query and len(candidates) == 1:
            return ResolutionResult(
                ResolutionStatus.EXACT,
                api=candidates[0],
                candidates=candidates,
            )
        if len(candidates) == 1:
            return ResolutionResult(
                ResolutionStatus.CANDIDATE,
                api=candidates[0],
                candidates=candidates,
                detail=(
                    "symbol-only lookup is not proof of API identity; "
                    "provider_id and signature_id are required"
                ),
            )
        return ResolutionResult(
            ResolutionStatus.AMBIGUOUS,
            candidates=candidates,
            detail=f"{len(candidates)} APIs match C symbol {symbol!r}",
        )

    def candidates_for_symbol(self, symbol: str) -> tuple[ManifestAPI, ...]:
        return self._apis_by_symbol.get(symbol, ())

    def __len__(self) -> int:
        return len(self._apis_by_id)
