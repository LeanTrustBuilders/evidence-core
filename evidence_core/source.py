"""Reading declarations' source text out of a checkout, from the dataset's `source` facet (S2).

The facet gives each declaration's range, from its doc comment to its end; a checkout of the library
at the dataset's commit gives the text. Lean counts columns in UTF-16 code units.
"""
from __future__ import annotations

from pathlib import Path


class Sources:
    """The project's source files, read once each."""

    def __init__(self, root: Path | None):
        self.root = root
        self._files: dict[str, list[str] | None] = {}

    def lines(self, path: str) -> list[str] | None:
        if self.root is None:
            return None
        if path not in self._files:
            p = self.root / path
            self._files[path] = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else None
        return self._files[path]

    def text(self, row: dict, doc_comment: bool = True) -> str | None:
        """The declaration's text: from its start (line, UTF-16 column) to its end; without its doc
        comment when ``doc_comment`` is false (pages show the docstring apart)."""
        span = self.span(row, doc_comment)
        return span[2] if span else None

    def span(self, row: dict, doc_comment: bool = True) -> tuple[int, int, str] | None:
        """(first line, last line, text) of the declaration, lines counted from 1; the first line is
        below the doc comment when ``doc_comment`` is false."""
        text = self._text(row)
        if text is None:
            return None
        start, end = row["start"][0], row["end"][0]
        if not doc_comment:
            k = doc_comment_end(text)
            start += text.count("\n", 0, k)
            text = text[k:]
        return start, end, text

    def _text(self, row: dict) -> str | None:
        lines = self.lines(row["path"])
        if lines is None:
            return None
        (l0, c0), (l1, c1) = row["start"], row["end"]
        if l0 < 1 or l1 > len(lines):
            return None
        chunk = lines[l0 - 1:l1]
        if not chunk:
            return None
        chunk[-1] = chunk[-1][:utf16_to_index(chunk[-1], c1)]
        chunk[0] = chunk[0][utf16_to_index(chunk[0], c0):]
        return "\n".join(chunk)

    def readme(self) -> tuple[str, str] | None:
        """The project's README, as (file name, markdown)."""
        if self.root is None:
            return None
        for name in ("README.md", "readme.md", "Readme.md"):
            p = self.root / name
            if p.exists():
                return name, p.read_text(encoding="utf-8", errors="replace")
        return None


def utf16_to_index(line: str, col: int) -> int:
    """The index in `line` of the UTF-16 column `col` (Lean's columns count UTF-16 code units)."""
    units = 0
    for i, ch in enumerate(line):
        if units >= col:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(line)


def doc_comment_end(text: str) -> int:
    """Where a declaration's text starts past its doc comment (`/-- … -/`, which may nest block
    comments) and the blank space after it; 0 when it has none."""
    i = len(text) - len(text.lstrip())
    if not text.startswith("/--", i):
        return 0
    depth, n = 0, len(text)
    while i < n:
        if text.startswith("/-", i):
            depth, i = depth + 1, i + 2
        elif text.startswith("-/", i):
            depth, i = depth - 1, i + 2
            if depth == 0:
                while i < n and text[i].isspace():
                    i += 1
                return i
        else:
            i += 1
    return 0


OPEN, CLOSE = "([{⟨⦃", ")]}⟩⦄"


def split_statement(text: str) -> tuple[str, str]:
    """A declaration's text split at the start of its proof or body: the first `:=`, `where` or
    equation-compiler `|` outside brackets, strings and comments. `("text", "")` when there is none."""
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/-", i):
            j = text.find("-/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0:
            if text.startswith(":=", i):
                return text[:i].rstrip(), text[i:]
            if text.startswith("where", i) and (i == 0 or text[i - 1].isspace()) and \
                    (i + 5 == n or not (text[i + 5].isalnum() or text[i + 5] in "_'.")):
                return text[:i].rstrip(), text[i:]
            if ch == "|" and text[:i].rstrip().endswith(("\n", "")) and _line_start(text, i):
                return text[:i].rstrip(), text[i:]
        i += 1
    return text, ""


def _line_start(text: str, i: int) -> bool:
    j = text.rfind("\n", 0, i)
    return text[j + 1:i].strip() == ""
