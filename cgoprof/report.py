from __future__ import annotations

from .graph import InteractionGraph
from .models import Finding


def render_text_report(graph: InteractionGraph, findings: list[Finding]) -> str:
    lines: list[str] = []
    lines.append("CGOProf Analysis Report")
    lines.append("=" * 23)
    lines.append("")
    lines.append(f"Call sites: {len(graph.metrics)}")
    lines.append(f"Findings: {len(findings)}")
    lines.append("")
    for finding in findings:
        callsite = graph.callsites.get(finding.site_id)
        location = f"{finding.site_id}"
        if callsite:
            location = f"{callsite.file}:{callsite.line} ({callsite.c_symbol})"
        lines.append(f"[{finding.severity.upper()}] {finding.rule} at {location}")
        lines.append(f"  {finding.summary}")
        lines.append(f"  Evidence: {_format_evidence(finding.evidence)}")
        lines.append(f"  Recommendation: {finding.recommendation}")
        lines.append("")
    if not findings:
        lines.append("No rule fired for the loaded profile.")
    return "\n".join(lines)


def _format_evidence(evidence: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in evidence.items())
