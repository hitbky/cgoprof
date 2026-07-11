from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .models import CallSite


IMPORT_C_RE = re.compile(r'^\s*import\s+"C"\s*$|^\s*import\s*\([^)]*"C"', re.M)
C_CALL_RE = re.compile(r"\bC\.([A-Za-z_]\w*)\s*\(")
PROF_CALL_RE = re.compile(r"\b(?:[A-Za-z_]\w*\.)?(?:BeginCall|Call(?:Void|WithCost)?)\s*\(\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"")
FUNC_RE = re.compile(r"^\s*func\s+([A-Za-z_]\w*)\s*\(")
DIRECTIVE_RE = re.compile(r"^\s*//\s*#cgo\s+(noescape|nocallback)\s+([A-Za-z_]\w*)")
C_TYPE_NAMES = {
    "char",
    "double",
    "float",
    "int",
    "long",
    "longlong",
    "size_t",
    "short",
    "ssize_t",
    "uchar",
    "uint",
    "uintptr_t",
    "ulong",
    "ulonglong",
    "ushort",
    "sqlite3_int64",
    "sqlite3_uint64",
}


def scan_project(root: str | Path) -> tuple[list[CallSite], dict[str, list[str]]]:
    root_path = Path(root)
    callsites: list[CallSite] = []
    directives: dict[str, list[str]] = {"noescape": [], "nocallback": []}
    for path in sorted(root_path.rglob("*.go")):
        text = path.read_text(encoding="utf-8")
        if "import \"C\"" not in text and 'import ("C"' not in text and 'import (\n\t"C"' not in text:
            continue
        rel = str(path.relative_to(root_path))
        for directive, symbol in DIRECTIVE_RE.findall(text):
            directives.setdefault(directive, []).append(symbol)
        callsites.extend(_scan_file(rel, text))
    return callsites, directives


def _scan_file(rel_path: str, text: str) -> list[CallSite]:
    callsites: list[CallSite] = []
    current_func = "<package>"
    inside_wrapped_call = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = FUNC_RE.match(line)
        if match:
            current_func = match.group(1)
        stripped = _strip_line_comment(line)
        wrapped_on_line = False
        for wrapped in PROF_CALL_RE.finditer(stripped):
            wrapped_on_line = True
            inside_wrapped_call = True
            callsites.append(
                CallSite(
                    site_id=wrapped.group(1),
                    file=rel_path,
                    line=line_no,
                    function=current_func,
                    c_symbol=wrapped.group(2),
                    expression=stripped.strip(),
                )
            )
        for call in C_CALL_RE.finditer(stripped):
            symbol = call.group(1)
            if symbol in C_TYPE_NAMES or symbol in {"sizeof"}:
                continue
            if wrapped_on_line or inside_wrapped_call:
                continue
            site_key = f"{rel_path}:{line_no}:{symbol}:{call.start()}"
            site_id = hashlib.sha1(site_key.encode("utf-8")).hexdigest()[:10]
            callsites.append(
                CallSite(
                    site_id=site_id,
                    file=rel_path,
                    line=line_no,
                    function=current_func,
                    c_symbol=symbol,
                    expression=stripped.strip(),
                )
            )
        if inside_wrapped_call and "})" in stripped:
            inside_wrapped_call = False
    return callsites


def _strip_line_comment(line: str) -> str:
    in_string = False
    escape = False
    for idx, ch in enumerate(line):
        if ch == "\\" and in_string:
            escape = not escape
            continue
        if ch == '"' and not escape:
            in_string = not in_string
        escape = False
        if not in_string and line[idx : idx + 2] == "//":
            return line[:idx]
    return line
