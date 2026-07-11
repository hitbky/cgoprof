from __future__ import annotations

from .graph import InteractionGraph
from .models import Finding, SiteMetrics


def run_rules(graph: InteractionGraph) -> list[Finding]:
    findings: list[Finding] = []
    for metric in graph.metrics.values():
        findings.extend(
            finding
            for finding in (
                detect_small_calls(metric),
                detect_conversion_copy(metric),
                detect_pointer_check(metric),
                detect_callback_pingpong(metric),
                detect_inbound_copy(metric),
            )
            if finding is not None
        )
    return sorted(findings, key=lambda item: _severity_rank(item.severity))


def detect_small_calls(metric: SiteMetrics) -> Finding | None:
    if metric.call_count < 1000:
        return None
    if metric.avg_cgo_ns > 50_000:
        return None
    if metric.has_boundary_samples and metric.boundary_ratio < 0.35:
        return None
    evidence = {
        "call_count": metric.call_count,
        "avg_cgo_ns": round(metric.avg_cgo_ns, 1),
    }
    if metric.has_boundary_samples:
        evidence.update(
            {
                "boundary_ratio": round(metric.boundary_ratio, 3),
                "total_boundary_ms": round(metric.total_boundary_ns / 1_000_000, 3),
            }
        )
    else:
        evidence.update(
            {
                "boundary_ratio": "unknown",
                "reason": "high-frequency cgo calls with very small measured total latency",
            }
        )
    return Finding(
        rule="small-call-detector",
        severity="high" if metric.call_count >= 100_000 else "medium",
        site_id=metric.site_id,
        summary="High-frequency small cgo calls spend a large share of time crossing the Go/C boundary.",
        evidence=evidence,
        recommendation="Batch work across the cgo boundary or move the loop entirely to Go or C.",
    )


def detect_conversion_copy(metric: SiteMetrics) -> Finding | None:
    repeated_conversions = metric.conversion_count >= 100 and metric.conversion_per_call >= 0.5
    repeated_memory = metric.memcpy_count >= 100 or (metric.conversion_count >= 100 and metric.malloc_count >= 100)
    if not (repeated_conversions or repeated_memory):
        return None
    bytes_total = metric.conversion_bytes + metric.memcpy_bytes
    return Finding(
        rule="conversion-copy-detector",
        severity="high" if bytes_total >= 16 * 1024 * 1024 else "medium",
        site_id=metric.site_id,
        summary="Repeated Go/C data conversion or memory copying appears on this cgo path.",
        evidence={
            "call_count": metric.call_count,
            "conversion_count": metric.conversion_count,
            "conversion_bytes": metric.conversion_bytes,
            "malloc_count": metric.malloc_count,
            "free_count": metric.free_count,
            "memcpy_count": metric.memcpy_count,
            "memcpy_bytes": metric.memcpy_bytes,
        },
        recommendation="Reuse C buffers, cache stable C strings, pass explicit lengths, or batch conversions.",
    )


def detect_pointer_check(metric: SiteMetrics) -> Finding | None:
    if metric.pointer_check_count < 100:
        return None
    total_ns = metric.total_cgo_ns + metric.pointer_check_ns
    ratio = metric.pointer_check_ns / total_ns if total_ns > 0 else 0.0
    if ratio < 0.1 and metric.pointer_check_ns < 5_000_000:
        return None
    return Finding(
        rule="pointer-check-overhead-detector",
        severity="medium",
        site_id=metric.site_id,
        summary="cgo pointer checking is a visible part of the cross-language cost.",
        evidence={
            "pointer_check_count": metric.pointer_check_count,
            "pointer_check_ms": round(metric.pointer_check_ns / 1_000_000, 3),
            "estimated_ratio": round(ratio, 3),
        },
        recommendation=(
            "Audit whether the C function stores Go pointers or calls back into Go. "
            "If the cgo safety contract holds, consider #cgo noescape or reducing pointer passing."
        ),
    )


def detect_callback_pingpong(metric: SiteMetrics) -> Finding | None:
    if metric.callback_count < 1000:
        return None
    avg_callback_ns = metric.callback_ns / metric.callback_count if metric.callback_count else 0.0
    if avg_callback_ns > 100_000:
        return None
    return Finding(
        rule="callback-pingpong-detector",
        severity="high" if metric.callback_count >= 100_000 else "medium",
        site_id=metric.site_id,
        summary="C code frequently calls back into Go with very small per-callback work.",
        evidence={
            "callback_count": metric.callback_count,
            "avg_callback_ns": round(avg_callback_ns, 1),
            "callback_total_ms": round(metric.callback_ns / 1_000_000, 3),
            "scheduler_block_ms": round(metric.scheduler_block_ns / 1_000_000, 3),
        },
        recommendation="Aggregate callbacks, move callback work to one side, or pass a batch result buffer.",
    )


def detect_inbound_copy(metric: SiteMetrics) -> Finding | None:
    if metric.inbound_conversion_count < 500:
        return None
    if metric.inbound_conversion_bytes < 64 * 1024 and metric.inbound_conversion_count < 10_000:
        return None
    return Finding(
        rule="inbound-copy-detector",
        severity="high" if metric.inbound_conversion_bytes >= 16 * 1024 * 1024 else "medium",
        site_id=metric.site_id,
        summary="C-to-Go string or byte conversion repeatedly copies native data into Go memory.",
        evidence={
            "inbound_conversion_count": metric.inbound_conversion_count,
            "inbound_conversion_bytes": metric.inbound_conversion_bytes,
            "call_count": metric.call_count,
        },
        recommendation=(
            "Avoid materializing Go strings/bytes on every call; cache immutable metadata, "
            "return lightweight handles, or batch native-to-Go decoding."
        ),
    )


def _severity_rank(severity: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(severity, 3)
