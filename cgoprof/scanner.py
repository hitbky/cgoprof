from __future__ import annotations

from bisect import bisect_right
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .models import CallSite


IMPORT_C_RE = re.compile(r'^\s*import\s+"C"\s*$|^\s*import\s*\([^)]*"C"', re.M)
C_CALL_RE = re.compile(r"\bC\.([A-Za-z_]\w*)\s*\(")
PROF_CALL_RE = re.compile(r"\b(?:[A-Za-z_]\w*\.)?(?:BeginCall|Call(?:Void|WithCost)?)\s*\(\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"")
FUNC_RE = re.compile(r"^\s*func\s+([A-Za-z_]\w*)\s*\(")
COMMENT_DIRECTIVE_RE = re.compile(
    r"^\s*\*?\s*#cgo\s+(noescape|nocallback)\s+([A-Za-z_]\w*)\b",
    re.M,
)
C_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])C\s*\.\s*([A-Za-z_]\w*)\s*\("
)
C_SHADOW_PATTERNS = (
    re.compile(r"\b(?:var|const|type)\s+C\b"),
    re.compile(r"(?:^|[;{(,])\s*C(?:\s*,[^:=]+)?\s*:=", re.M),
    re.compile(r"\bfunc\b[^{]*\([^)]*\bC\s+[A-Za-z_*\[]", re.S),
    re.compile(r"\bfor\s+C(?:\s*,[^:=]+)?\s*(?::=|=)"),
)
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


@dataclass(frozen=True)
class CgoReference:
    symbol: str
    line: int
    column: int


@dataclass(frozen=True)
class _GoComment:
    text: str
    start: int
    end: int


def scan_project(root: str | Path) -> tuple[list[CallSite], dict[str, list[str]]]:
    root_path = Path(root)
    callsites: list[CallSite] = []
    directives: dict[str, list[str]] = {"noescape": [], "nocallback": []}
    for path in sorted(root_path.rglob("*.go")):
        text = path.read_text(encoding="utf-8")
        if not _has_import_c(text):
            continue
        rel = str(path.relative_to(root_path))
        file_callsites, file_directives = scan_go_source(rel, text)
        for directive, symbols in file_directives.items():
            directives.setdefault(directive, []).extend(symbols)
        callsites.extend(file_callsites)
    return callsites, directives


def scan_go_source(
    rel_path: str,
    text: str,
) -> tuple[list[CallSite], dict[str, list[str]]]:
    """Scan one cgo source file without walking into nested Go packages."""

    directives: dict[str, list[str]] = {"noescape": [], "nocallback": []}
    if not _has_import_c(text):
        return [], directives
    for directive, symbol in scan_cgo_directives(text):
        directives[directive].append(symbol)
    for symbols in directives.values():
        symbols.sort()
    return _scan_file(rel_path, text), directives


def scan_cgo_references(
    text: str,
) -> tuple[tuple[CgoReference, ...], dict[str, tuple[str, ...]]]:
    """Lexically enumerate C call selectors while excluding comments and literals."""

    masked, _ = _mask_non_code(text)
    line_starts = [0]
    line_starts.extend(index + 1 for index, char in enumerate(masked) if char == "\n")
    references = []
    for match in C_REFERENCE_RE.finditer(masked):
        previous = match.start() - 1
        while previous >= 0 and masked[previous].isspace():
            previous -= 1
        if previous >= 0 and masked[previous] == ".":
            continue
        symbol = match.group(1)
        if symbol in C_TYPE_NAMES or symbol == "sizeof":
            continue
        line_index = bisect_right(line_starts, match.start()) - 1
        line_start = line_starts[line_index]
        references.append(
            CgoReference(
                symbol=symbol,
                line=line_index + 1,
                column=match.start() - line_start + 1,
            )
        )
    directives: dict[str, list[str]] = {"noescape": [], "nocallback": []}
    for directive, symbol in scan_cgo_directives(text):
        directives[directive].append(symbol)
    return (
        tuple(references),
        {
            name: tuple(sorted(set(symbols)))
            for name, symbols in directives.items()
        },
    )


def scan_cgo_directives(text: str) -> tuple[tuple[str, str], ...]:
    masked, comments = _mask_non_code(text, preserve_c_import_string=True)
    preamble_comments: set[_GoComment] = set()
    for import_match in IMPORT_C_RE.finditer(masked):
        import_start = masked.find("import", import_match.start(), import_match.end())
        if import_start < 0:
            continue
        preceding = [
            comment for comment in comments if comment.end <= import_start
        ]
        if not preceding:
            continue
        cursor = import_start
        for comment in reversed(preceding):
            gap = text[comment.end : cursor]
            if not _is_adjacent_comment_gap(gap):
                break
            preamble_comments.add(comment)
            cursor = comment.start
    directives = {
        (directive, symbol)
        for comment in preamble_comments
        for directive, symbol in COMMENT_DIRECTIVE_RE.findall(comment.text)
    }
    return tuple(sorted(directives))


def c_identifier_may_be_shadowed(text: str) -> bool:
    masked, _ = _mask_non_code(text)
    return any(pattern.search(masked) is not None for pattern in C_SHADOW_PATTERNS)


def _has_import_c(text: str) -> bool:
    masked, _ = _mask_non_code(text, preserve_c_import_string=True)
    return IMPORT_C_RE.search(masked) is not None


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


def _mask_non_code(
    text: str,
    *,
    preserve_c_import_string: bool = False,
) -> tuple[str, tuple[_GoComment, ...]]:
    """Preserve code positions while replacing Go comments and literals with spaces."""

    output = list(text)
    comments: list[_GoComment] = []
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            if end < 0:
                end = len(text)
            comments.append(_GoComment(text[index + 2 : end], index, end))
            _blank(output, index, end)
            index = end
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            end = len(text) if close < 0 else close + 2
            comment_end = len(text) if close < 0 else close
            comments.append(
                _GoComment(text[index + 2 : comment_end], index, end)
            )
            _blank(output, index, end)
            index = end
            continue
        marker = text[index]
        if marker in {'"', "'"}:
            end = _quoted_literal_end(text, index, marker)
            if not (
                preserve_c_import_string
                and marker == '"'
                and text[index:end] == '"C"'
            ):
                _blank(output, index, end)
            index = end
            continue
        if marker == "`":
            close = text.find("`", index + 1)
            end = len(text) if close < 0 else close + 1
            _blank(output, index, end)
            index = end
            continue
        index += 1
    return "".join(output), tuple(comments)


def _is_adjacent_comment_gap(value: str) -> bool:
    return not value.strip() and value.count("\n") <= 1


def _quoted_literal_end(text: str, start: int, marker: str) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == marker:
            return index + 1
        index += 1
    return len(text)


def _blank(output: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if output[index] not in {"\n", "\r"}:
            output[index] = " "
