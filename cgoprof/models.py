from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    CGO_CALL = "cgo_call"
    CONVERSION = "conversion"
    MEMORY = "memory"
    POINTER_CHECK = "pointer_check"
    CALLBACK = "callback"
    SCHEDULER = "scheduler"


@dataclass(frozen=True)
class CallSite:
    site_id: str
    file: str
    line: int
    function: str
    c_symbol: str
    expression: str


@dataclass
class ProfileEvent:
    kind: EventKind
    site_id: str
    timestamp_ns: int = 0
    duration_ns: int = 0
    goroutine: int | None = None
    thread: int | None = None
    function: str = ""
    source: str = ""
    bytes: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "ProfileEvent":
        detail = dict(item.get("detail") or {})
        return cls(
            kind=EventKind(item["kind"]),
            site_id=str(item.get("site_id", "")),
            timestamp_ns=int(item.get("timestamp_ns", 0)),
            duration_ns=int(item.get("duration_ns", 0)),
            goroutine=item.get("goroutine"),
            thread=item.get("thread"),
            function=str(item.get("function", "")),
            source=str(item.get("source", "")),
            bytes=int(item.get("bytes", 0)),
            detail=detail,
        )


@dataclass
class SiteMetrics:
    site_id: str
    call_count: int = 0
    total_cgo_ns: int = 0
    total_c_work_ns: int = 0
    total_boundary_ns: int = 0
    boundary_sample_count: int = 0
    conversion_count: int = 0
    conversion_bytes: int = 0
    inbound_conversion_count: int = 0
    inbound_conversion_bytes: int = 0
    outbound_conversion_count: int = 0
    outbound_conversion_bytes: int = 0
    malloc_count: int = 0
    malloc_bytes: int = 0
    free_count: int = 0
    free_bytes: int = 0
    memcpy_count: int = 0
    memcpy_bytes: int = 0
    pointer_check_count: int = 0
    pointer_check_ns: int = 0
    callback_count: int = 0
    callback_ns: int = 0
    scheduler_block_ns: int = 0

    def add(self, event: ProfileEvent) -> None:
        if event.kind == EventKind.CGO_CALL:
            self.call_count += 1
            self.total_cgo_ns += event.duration_ns
            c_work_ns = int(event.detail.get("c_work_ns", 0) or 0)
            boundary_ns = event.duration_ns - c_work_ns if c_work_ns else int(
                event.detail.get("boundary_ns", 0) or 0
            )
            self.total_c_work_ns += max(c_work_ns, 0)
            self.total_boundary_ns += max(boundary_ns, 0)
            if c_work_ns or boundary_ns:
                self.boundary_sample_count += 1
        elif event.kind == EventKind.CONVERSION:
            self.conversion_count += 1
            self.conversion_bytes += event.bytes
            op = str(event.detail.get("op", ""))
            direction = str(event.detail.get("direction", ""))
            if direction == "c_to_go" or op in {"C.GoString", "C.GoStringN", "C.GoBytes"}:
                self.inbound_conversion_count += 1
                self.inbound_conversion_bytes += event.bytes
            elif direction == "go_to_c" or op in {"C.CString", "C.CBytes"}:
                self.outbound_conversion_count += 1
                self.outbound_conversion_bytes += event.bytes
        elif event.kind == EventKind.MEMORY:
            op = str(event.detail.get("op", ""))
            if op == "malloc":
                self.malloc_count += 1
                self.malloc_bytes += event.bytes
            elif op == "free":
                self.free_count += 1
                self.free_bytes += event.bytes
            elif op == "memcpy":
                self.memcpy_count += 1
                self.memcpy_bytes += event.bytes
        elif event.kind == EventKind.POINTER_CHECK:
            self.pointer_check_count += 1
            self.pointer_check_ns += event.duration_ns
        elif event.kind == EventKind.CALLBACK:
            self.callback_count += 1
            self.callback_ns += event.duration_ns
        elif event.kind == EventKind.SCHEDULER:
            self.scheduler_block_ns += event.duration_ns

    @property
    def avg_cgo_ns(self) -> float:
        return self.total_cgo_ns / self.call_count if self.call_count else 0.0

    @property
    def boundary_ratio(self) -> float:
        if self.total_cgo_ns <= 0:
            return 0.0
        return self.total_boundary_ns / self.total_cgo_ns

    @property
    def has_boundary_samples(self) -> bool:
        return self.boundary_sample_count > 0

    @property
    def conversion_per_call(self) -> float:
        return self.conversion_count / self.call_count if self.call_count else 0.0

    @property
    def avg_malloc_bytes(self) -> float:
        return self.malloc_bytes / self.malloc_count if self.malloc_count else 0.0


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    site_id: str
    summary: str
    evidence: dict[str, Any]
    recommendation: str
