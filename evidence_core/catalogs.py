"""The catalogues a library keeps beside its code, read and matched against a dataset (S2).

Mathlib keeps them in `docs/`:
- `overview.yaml` and `undergrad.yaml`: subject trees (area → subarea → concept), each concept named
  in words and pointing at the declaration that formalizes it, or at nothing yet (`''`);
- `100.yaml` and `1000.yaml`: famous theorems (Freek Wiedijk's 100, and the 1000+ list keyed by
  Wikidata items), with the declarations that prove them, who formalized them and when;
- `references.bib`: the bibliography that module docstrings cite as `[Author, *Title*][key]`.

Reading them needs PyYAML (``pip install 'evidence-core[yaml]'``) for the YAML files; the
bibliography is read here. Declarations are matched by name; one the dataset lacks (renamed since,
or in another library such as Mathlib's `Archive`) is kept, marked as not found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .dataset import Dataset

try:
    import yaml
except ImportError:  # PyYAML is optional (evidence-core[yaml])
    yaml = None


def _load_yaml(path: Path) -> object:
    if yaml is None:
        raise RuntimeError(f"reading {path.name} needs PyYAML (pip install 'evidence-core[yaml]')")
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@dataclass
class Entry:
    """A concept or theorem a catalogue names, and the declarations it points at."""
    title: str
    decls: list[str] = field(default_factory=list)
    #: the declarations the dataset has
    found: list[str] = field(default_factory=list)
    #: anything else the catalogue says (authors, date, Wikidata item, a note)
    meta: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"title": self.title, "decls": self.decls, "found": self.found, **self.meta}


@dataclass
class Topic:
    title: str
    entries: list[Entry] = field(default_factory=list)
    children: list["Topic"] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"title": self.title, "entries": [e.as_json() for e in self.entries],
                "children": [c.as_json() for c in self.children]}

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def _decls(value) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _entry(ds: Dataset | None, title: str, decls: list[str], **meta) -> Entry:
    return Entry(title=title, decls=decls, found=[d for d in decls if ds is None or d in ds.by_name],
                 meta={k: v for k, v in meta.items() if v not in (None, "", [], {})})


def topic_tree(path: Path, ds: Dataset | None = None, title: str = "") -> Topic:
    """A subject tree (`overview.yaml`, `undergrad.yaml`): nested areas, whose leaves name concepts."""
    def build(name: str, node) -> Topic:
        t = Topic(title=name)
        for key, value in (node or {}).items():
            if isinstance(value, dict):
                t.children.append(build(str(key), value))
            else:
                t.entries.append(_entry(ds, str(key), _decls(value)))
        return t
    return build(title, _load_yaml(path) or {})


def famous_theorems(path: Path, ds: Dataset | None = None) -> list[Entry]:
    """A list of famous theorems (`100.yaml`, keyed by number; `1000.yaml`, keyed by Wikidata item)."""
    out = []
    for key, value in (_load_yaml(path) or {}).items():
        value = value or {}
        key = str(key)
        decls = _decls(value.get("decl")) + _decls(value.get("decls"))
        out.append(_entry(ds, str(value.get("title", key)), decls,
                          id=key, wikidata=key if re.fullmatch(r"Q\d+[A-Z]?", key) else None,
                          statement=value.get("statement"), authors=value.get("authors"),
                          date=str(value["date"]) if value.get("date") else None, url=value.get("url"),
                          note=value.get("note") or value.get("comment"), links=value.get("links")))
    return out


# --- bibliography ---------------------------------------------------------------------------------

BIB_FIELDS = ("title", "author", "editor", "year", "journal", "booktitle", "publisher", "series",
              "volume", "number", "pages", "url", "doi", "eprint", "isbn", "note", "edition")


def _value(text: str, i: int) -> tuple[str, int]:
    """A BibTeX field value starting at ``i`` (braced, quoted or bare), and where it ends."""
    if text[i] == "{":
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return text[i + 1:j], j + 1
            j += 1
        return text[i + 1:], len(text)
    if text[i] == '"':
        j = text.find('"', i + 1)
        return text[i + 1:j], j + 1
    m = re.match(r"[^,}\s]+", text[i:])
    return (m.group(0), i + m.end()) if m else ("", i)


def _plain(value: str) -> str:
    value = re.sub(r"\\[a-zA-Z]+\s*|[{}]", "", value)
    return " ".join(value.split())


def bibliography(path: Path) -> dict[str, dict]:
    """The entries of a BibTeX file, by key: ``{type, title, authors: [..], year, …}``."""
    text = Path(path).read_text(encoding="utf-8")
    out: dict[str, dict] = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        kind, key, i = m.group(1).lower(), m.group(2), m.end()
        if kind in ("comment", "string", "preamble"):
            continue
        entry: dict = {"type": kind}
        while i < len(text):
            f = re.match(r"\s*([A-Za-z_\-]+)\s*=\s*", text[i:])
            if not f:
                break
            name = f.group(1).lower()
            value, i = _value(text, i + f.end())
            if name in BIB_FIELDS:
                entry[name] = _plain(value)
            c = re.match(r"\s*,?", text[i:])
            i += c.end()
            if text[i:i + 1] == "}":
                break
        for who in ("author", "editor"):
            if who in entry:
                entry[who + "s"] = [" ".join(reversed([p.strip() for p in a.split(",", 1)])) if "," in a else a.strip()
                                    for a in re.split(r"\s+and\s+", entry.pop(who))]
        out[key] = entry
    return out


def cite(entry: dict) -> str:
    """A short citation: "Atiyah, Macdonald, Introduction to commutative algebra (1969)"."""
    people = entry.get("authors") or entry.get("editors") or []
    names = [p.split()[-1] for p in people[:3]] + (["et al."] if len(people) > 3 else [])
    return ", ".join(names + [entry.get("title", "")]).strip(", ") + (f" ({entry['year']})" if entry.get("year") else "")
