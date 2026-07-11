from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .models import CallSite, EventKind, ProfileEvent, SiteMetrics


class InteractionGraph:
    def __init__(self) -> None:
        self.callsites: dict[str, CallSite] = {}
        self.metrics: dict[str, SiteMetrics] = {}
        self.edges: dict[tuple[str, str, str], int] = defaultdict(int)

    def add_callsite(self, callsite: CallSite) -> None:
        self.callsites[callsite.site_id] = callsite
        self.metrics.setdefault(callsite.site_id, SiteMetrics(site_id=callsite.site_id))

    def add_event(self, event: ProfileEvent) -> None:
        site_id = event.site_id or "<unknown>"
        metrics = self.metrics.setdefault(site_id, SiteMetrics(site_id=site_id))
        metrics.add(event)
        self._add_event_edges(event)

    def _add_event_edges(self, event: ProfileEvent) -> None:
        site = f"site:{event.site_id or '<unknown>'}"
        if event.kind == EventKind.CGO_CALL:
            c_func = event.function or event.detail.get("c_symbol", "<c>")
            self.edges[(site, f"c:{c_func}", "calls")] += 1
            if event.detail.get("boundary_ns") or event.detail.get("c_work_ns"):
                self.edges[(site, "runtime:cgocall", "crosses")] += 1
        elif event.kind == EventKind.CONVERSION:
            conversion = event.detail.get("op", "conversion")
            self.edges[(site, f"conversion:{conversion}", "converts")] += 1
        elif event.kind == EventKind.MEMORY:
            op = event.detail.get("op", "memory")
            self.edges[(site, f"memory:{op}", "uses")] += 1
        elif event.kind == EventKind.POINTER_CHECK:
            self.edges[(site, "runtime:cgoCheckPointer", "checks")] += 1
        elif event.kind == EventKind.CALLBACK:
            self.edges[(f"c:{event.source or '<c>'}", site, "callback")] += 1
        elif event.kind == EventKind.SCHEDULER:
            self.edges[(site, "runtime:scheduler", "blocks")] += 1

    def add_events(self, events: Iterable[ProfileEvent]) -> None:
        for event in events:
            self.add_event(event)

    def to_dict(self) -> dict[str, object]:
        nodes: dict[str, dict[str, object]] = {}
        for site_id, callsite in self.callsites.items():
            nodes[f"site:{site_id}"] = {
                "kind": "cgo_call_site",
                "label": f"{callsite.file}:{callsite.line} {callsite.c_symbol}",
                "file": callsite.file,
                "line": callsite.line,
                "function": callsite.function,
            }
        for src, dst, edge_kind in self.edges:
            for node in (src, dst):
                nodes.setdefault(node, {"kind": node.split(":", 1)[0], "label": node})
        return {
            "nodes": [{"id": node_id, **attrs} for node_id, attrs in sorted(nodes.items())],
            "edges": [
                {"source": src, "target": dst, "kind": kind, "count": count}
                for (src, dst, kind), count in sorted(self.edges.items())
            ],
            "metrics": {site_id: _metrics_to_dict(metric) for site_id, metric in sorted(self.metrics.items())},
        }


def iter_events(path: str | Path) -> Iterable[ProfileEvent]:
    path_obj = Path(path)
    if path_obj.suffix == ".jsonl":
        with path_obj.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield ProfileEvent.from_json(json.loads(line))
    else:
        data = json.loads(path_obj.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("events", [])
        for item in items:
            repeat = int(item.get("repeat", 1))
            clean_item = dict(item)
            clean_item.pop("repeat", None)
            for _ in range(repeat):
                yield ProfileEvent.from_json(clean_item)


def load_events(path: str | Path) -> list[ProfileEvent]:
    return list(iter_events(path))


def _metrics_to_dict(metric: SiteMetrics) -> dict[str, object]:
    return {
        "call_count": metric.call_count,
        "total_cgo_ns": metric.total_cgo_ns,
        "avg_cgo_ns": metric.avg_cgo_ns,
        "total_boundary_ns": metric.total_boundary_ns,
        "boundary_sample_count": metric.boundary_sample_count,
        "boundary_ratio": metric.boundary_ratio,
        "conversion_count": metric.conversion_count,
        "conversion_bytes": metric.conversion_bytes,
        "inbound_conversion_count": metric.inbound_conversion_count,
        "inbound_conversion_bytes": metric.inbound_conversion_bytes,
        "outbound_conversion_count": metric.outbound_conversion_count,
        "outbound_conversion_bytes": metric.outbound_conversion_bytes,
        "malloc_count": metric.malloc_count,
        "malloc_bytes": metric.malloc_bytes,
        "free_count": metric.free_count,
        "free_bytes": metric.free_bytes,
        "memcpy_count": metric.memcpy_count,
        "memcpy_bytes": metric.memcpy_bytes,
        "pointer_check_count": metric.pointer_check_count,
        "pointer_check_ns": metric.pointer_check_ns,
        "callback_count": metric.callback_count,
        "callback_ns": metric.callback_ns,
        "scheduler_block_ns": metric.scheduler_block_ns,
    }
