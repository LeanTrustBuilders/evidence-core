"""What a library's own documentation says, read from a dataset (S2): module docstrings, and the
attributes declarations are written with.

Mathlib's module docstrings follow a convention (its "documentation style") that other libraries
share: a `# Title`, a summary, and sections: `## Main definitions`, `## Main results` (or
`statements`, `declarations`, `theorems`), `## Notation`, `## Implementation notes`,
`## References` (items `[Author, *Title*][bibkey]`, keys of the library's bibliography) and
`## Tags` (keywords, comma-separated). The declarations the main sections name, in backquotes, are
resolved against the dataset as a reader would: as written, then under the module's namespaces, then
by the unique declaration of the module whose name ends with it.

The attributes (facet `attributes`, written by the extractor's `scripts/attributes.py`) link a
declaration outside the library: Mathlib's `@[stacks TAG]`, `@[kerodon TAG]` and
`@[wikidata QID]`; and `@[deprecated]` marks one kept only for compatibility.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dataset import Dataset

MAIN_DEFINITIONS = ("main definitions", "main definition", "main declarations", "main declaration",
                    "definitions", "main constructions")
MAIN_RESULTS = ("main results", "main result", "main statements", "main statement", "main theorems",
                "main theorem", "results", "theorems")
BACKTICKED = re.compile(r"`([^`\s]+)`")
BIBKEY = re.compile(r"\]\[([A-Za-z0-9_:\-]+)\]")
HEADING = re.compile(r"^(#{1,3})\s+(.*?)\s*#*\s*$")


@dataclass
class Item:
    """One item of a main section: the declarations it names, and what it says of them."""
    names: list[str]
    text: str


@dataclass
class ModuleDoc:
    module: str
    title: str = ""
    summary: str = ""
    #: section heading (as written) → its markdown
    sections: dict[str, str] = field(default_factory=dict)
    definitions: list[Item] = field(default_factory=list)
    results: list[Item] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    #: bibliography keys cited anywhere in the module's docstrings
    references: list[str] = field(default_factory=list)
    #: the whole docstring text, the module's docstrings joined
    text: str = ""
    #: the backquoted names of the docstring that resolve, as written → the declaration
    names: dict[str, str] = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"module": self.module, "title": self.title, "summary": self.summary, "sections": self.sections,
                "definitions": [i.__dict__ for i in self.definitions], "results": [i.__dict__ for i in self.results],
                "tags": self.tags, "references": self.references, "names": self.names}


def _clean(text: str) -> str:
    return " ".join(text.split())


def split_sections(text: str) -> tuple[str, str, dict[str, str]]:
    """(title, the text before the first section, section heading → body) of a module docstring."""
    title, head, sections, current, buf = "", [], {}, None, []
    for line in text.splitlines():
        m = HEADING.match(line.strip())
        if m and len(m.group(1)) == 1 and not title and current is None:
            title = m.group(2)
            continue
        if m and len(m.group(1)) >= 2:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = m.group(2), []
            continue
        (buf if current is not None else head).append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return title, "\n".join(head).strip(), sections


#: What attributes add to a docstring (`@[stacks 09HR]` adds "Stacks Tag 09HR"): not a description.
GENERATED = re.compile(r"^\s*(\[?(Stacks|Kerodon) Tag [0-9A-Z]{4}\]?(\([^)]*\))?|Wikidata Q\d+[A-Z]?|\[?Wikidata\]?.*wikidata\.org\S*)[\s.:]*",
                       re.I)


def first_paragraph(text: str) -> str:
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if para and not para.startswith(("#", "```", "|")) and not GENERATED.fullmatch(para):
            return _clean(para)
    return ""


def items(body: str) -> list[tuple[list[str], str]]:
    """The items of a list section: the backquoted names each mentions, and its text. Text outside
    list items (a sentence introducing the list) is one item too when it names something."""
    out, cur = [], []
    for line in body.splitlines():
        s = line.strip()
        if re.match(r"^([*\-+]|\d+\.)\s", s):
            if cur:
                out.append(" ".join(cur))
            cur = [re.sub(r"^([*\-+]|\d+\.)\s+", "", s)]
        elif s and cur:
            cur.append(s)
        elif s:
            out.append(s)
        elif cur:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return [(BACKTICKED.findall(t), _clean(t)) for t in out]


def first_sentence(text: str, limit: int = 160) -> str:
    """The first sentence of a docstring, on one line: what a reader sees as its title. Markdown
    stays (math included), a heading or a code block is skipped, and a long sentence is cut at a
    word."""
    para = first_paragraph(re.sub(r"^#+\s.*$", "", text or "", flags=re.M))
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z`$(\[])", para)
    first = para[:m.start()] if m else para
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + " …"
    return first


class Resolver:
    """Declaration names as a docstring writes them, resolved against a dataset."""

    def __init__(self, ds: Dataset):
        self.ds = ds
        self.by_module: dict[str, list[str]] = {}
        for d in ds.decls:
            if d.is_project:
                self.by_module.setdefault(d.module, []).append(d.name)
        self._spaces: dict[str, list[str]] = {}

    def spaces(self, module: str) -> list[str]:
        """The namespaces the module's declarations live in, longest first."""
        if module not in self._spaces:
            self._spaces[module] = sorted({n.rsplit(".", 1)[0] for n in self.by_module.get(module, []) if "." in n},
                                          key=len, reverse=True)
        return self._spaces[module]

    def names_in(self, text: str, module: str) -> dict[str, str]:
        """Every backquoted name of a docstring that resolves, as written → the declaration."""
        out = {}
        for written in set(BACKTICKED.findall(text or "")):
            if written not in out and (r := self.resolve(written, module)):
                out[written] = r
        return out

    def resolve(self, name: str, module: str) -> str | None:
        name = name.strip().removeprefix("_root_.").rstrip(".,;:")
        name = re.sub(r"[()]", "", name)
        if not name or " " in name:
            return None
        by_name = self.ds.by_name
        if name in by_name:
            return name
        mine = self.by_module.get(module, [])
        # Under the namespaces the module's own declarations live in.
        for ns in self.spaces(module):
            parts = ns.split(".")
            for k in range(len(parts), 0, -1):
                cand = ".".join(parts[:k] + [name])
                if cand in by_name:
                    return cand
        ends = [n for n in mine if n == name or n.endswith("." + name)]
        return ends[0] if len(ends) == 1 else None


def module_doc(ds: Dataset, module: dict, resolver: Resolver | None = None) -> ModuleDoc:
    """The docstring of a module (a row of the dataset's ``modules``), read as its sections."""
    resolver = resolver or Resolver(ds)
    text = "\n\n".join(module.get("doc") or [])
    title, head, sections = split_sections(text)
    out = ModuleDoc(module=module["name"], title=title, sections=sections, text=text)
    out.summary = first_paragraph(head) or next((first_paragraph(b) for h, b in sections.items()
                                                 if h.lower() not in ("tags", "references")), "")
    for heading, body in sections.items():
        key = heading.lower().rstrip(":")
        if key in MAIN_DEFINITIONS or key in MAIN_RESULTS:
            found = [Item([r for n in names if (r := resolver.resolve(n, module["name"]))], t) for names, t in items(body)]
            found = [i for i in found if i.names or i.text]
            (out.definitions if key in MAIN_DEFINITIONS else out.results).extend(found)
        elif key in ("tags", "keywords"):
            out.tags = [_clean(t) for t in re.split(r"[,;\n]", body) if _clean(t)]
    out.references = list(dict.fromkeys(BIBKEY.findall(text)))
    out.names = resolver.names_in(text, module["name"])
    return out


def module_docs(ds: Dataset) -> dict[str, ModuleDoc]:
    """Every module's docstring, read."""
    resolver = Resolver(ds)
    return {m["name"]: module_doc(ds, m, resolver) for m in ds.modules}


# --- attributes ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Links:
    """Where a declaration is also described: Stacks project and Kerodon tags, Wikidata items."""
    stacks: tuple[tuple[str, str], ...] = ()      # (tag, comment)
    kerodon: tuple[tuple[str, str], ...] = ()
    wikidata: tuple[str, ...] = ()
    deprecated: bool = False

    def as_json(self) -> dict:
        out = {}
        for key in ("stacks", "kerodon"):
            if getattr(self, key):
                out[key] = [{"tag": t, "comment": c} for t, c in getattr(self, key)]
        if self.wikidata:
            out["wikidata"] = list(self.wikidata)
        if self.deprecated:
            out["deprecated"] = True
        return out


def _tag(args: str) -> tuple[str, str]:
    tag, _, rest = args.partition(" ")
    rest = rest.strip()
    return tag.strip(), rest[1:-1] if rest.startswith('"') and rest.endswith('"') else rest


def links(ds: Dataset, name: str) -> Links:
    attrs = (ds.facet_row("attributes", name) or {}).get("attributes", [])
    return Links(stacks=tuple(_tag(a["args"]) for a in attrs if a["name"] == "stacks"),
                 kerodon=tuple(_tag(a["args"]) for a in attrs if a["name"] == "kerodon"),
                 wikidata=tuple(a["args"].split()[0] for a in attrs if a["name"] == "wikidata" and a["args"]),
                 deprecated=any(a["name"] == "deprecated" for a in attrs))


def deprecated(ds: Dataset) -> set[str]:
    """The declarations written `@[deprecated]` (aliases kept for compatibility)."""
    return {name for name, rows in ds.facet("attributes").items()
            if any(a["name"] == "deprecated" for a in rows[0].get("attributes", []))}
