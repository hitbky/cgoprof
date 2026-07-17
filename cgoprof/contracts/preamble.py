from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CgoPreamble:
    source: str
    import_line: int


_IMPORT_C_RE = re.compile(
    r"(?m)^[ \t]*import[ \t]+(?:C[ \t]+)?\"C\"[ \t]*(?://[^\n]*)?$"
)


def extract_cgo_preambles(go_source: str) -> tuple[CgoPreamble, ...]:
    """Extract documentation comments attached to each `import "C"`.

    cgo treats that comment as C input.  This scanner intentionally accepts
    both block comments and contiguous `//` comments, preserves C line order,
    and removes Go-only `#cgo` directive lines before invoking Clang.
    """

    result: list[CgoPreamble] = []
    for match in _IMPORT_C_RE.finditer(go_source):
        prefix = go_source[: match.start()]
        comment = _attached_comment(prefix)
        if comment is None:
            continue
        cleaned = _remove_cgo_directives(comment)
        if cleaned.strip():
            result.append(
                CgoPreamble(
                    cleaned,
                    go_source.count("\n", 0, match.start()) + 1,
                )
            )
    return tuple(result)


def _attached_comment(prefix: str) -> str | None:
    # Only horizontal/vertical whitespace may occur between the documentation
    # comment and import declaration.  A blank line is rejected because cgo's
    # preamble must be the import declaration's attached doc comment.
    stripped = prefix.rstrip()
    if stripped.endswith("*/"):
        end = len(stripped)
        start = stripped.rfind("/*", 0, end - 1)
        if start < 0:
            return None
        between = prefix[end:]
        if between.count("\n") > 1:
            return None
        return stripped[start + 2 : end - 2]

    lines = prefix.splitlines()
    if not lines:
        return None
    index = len(lines) - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index < 0 or not lines[index].lstrip().startswith("//"):
        return None
    comment_lines: list[str] = []
    while index >= 0 and lines[index].lstrip().startswith("//"):
        line = lines[index].lstrip()[2:]
        if line.startswith(" "):
            line = line[1:]
        comment_lines.append(line)
        index -= 1
    comment_lines.reverse()
    return "\n".join(comment_lines)


def _remove_cgo_directives(source: str) -> str:
    return "\n".join(
        "" if line.lstrip().startswith("#cgo ") else line
        for line in source.splitlines()
    ) + ("\n" if source.endswith("\n") else "")
