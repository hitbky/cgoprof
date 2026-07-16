from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FactStatus(str, Enum):
    UNKNOWN = "unknown"
    HEURISTIC = "heuristic"
    OBSERVED = "observed"
    DECLARED = "declared"
    PROVEN = "proven"
    INTRINSIC = "intrinsic"
    CONFLICT = "conflict"


class EvidenceKind(str, Enum):
    CGO_INTRINSIC = "cgo_intrinsic"
    CGO_DIRECTIVE = "cgo_directive"
    C_SIGNATURE = "c_signature"
    C_BODY_ANALYSIS = "c_body_analysis"
    GO_ANALYSIS = "go_analysis"
    API_DOCUMENTATION = "api_documentation"
    USER_ANNOTATION = "user_annotation"
    DYNAMIC_OBSERVATION = "dynamic_observation"
    HEURISTIC = "heuristic"


@dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind
    source: str
    detail: str = ""
    location: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("evidence source must not be empty")


def evidence_sort_key(item: Evidence) -> tuple[str, str, str, str]:
    return (item.kind.value, item.source, item.location or "", item.detail)
