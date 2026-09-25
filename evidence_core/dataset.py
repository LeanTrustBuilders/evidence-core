"""Reading an S2 dataset (spec ``ltb-dataset/0``).

A dataset is a directory written by ``trust-extract``:

* ``meta.json``: what produced it, from what, and which edge files and facets it holds;
* ``decls.jsonl``: one node per line, with its id, name, module, package, scope, kind, and hashes;
* ``edges/<notion>.bin``: little-endian int32 pairs (source id, target id);
* ``facets/<name>.jsonl``: per-declaration data, keyed by ``decl`` (a name).

Edge files and facets are read lazily, on first use.
"""
from __future__ import annotations

import json
from array import array
from dataclasses import dataclass, field
from pathlib import Path
import sys

SUPPORTED_SPECS = ("ltb-dataset/0",)


@dataclass(frozen=True)
class Decl:
    """One node of a dataset."""

    id: int
    name: str
    module: str
    package: str
    scope: str  # "project" or "upstream"
    kind: str
    is_prop: bool
    meaning: str | None
    content: str | None
    local: str | None

    @property
    def is_project(self) -> bool:
        return self.scope == "project"

    @classmethod
    def from_json(cls, d: dict) -> "Decl":
        h = d.get("hashes", {})
        return cls(id=d["id"], name=d["name"], module=d.get("module", ""),
                   package=d.get("package", ""), scope=d.get("scope", "project"),
                   kind=d.get("kind", ""), is_prop=bool(d.get("isProp", False)),
                   meaning=h.get("meaning"), content=h.get("content"), local=h.get("local"))


@dataclass
class Dataset:
    """An S2 dataset, loaded from a directory."""

    root: Path
    meta: dict
    decls: list[Decl]
    by_name: dict[str, Decl]
    by_meaning: dict[str, list[Decl]]
    _edges: dict[str, dict[int, list[int]]] = field(default_factory=dict, repr=False)
    _facets: dict[str, dict[str, list[dict]]] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, root: str | Path) -> "Dataset":
        root = Path(root)
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        if meta.get("spec") not in SUPPORTED_SPECS:
            raise ValueError(f"{root}: unsupported dataset spec {meta.get('spec')!r}")
        decls = []
        with (root / "decls.jsonl").open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    decls.append(Decl.from_json(json.loads(line)))
        by_name = {d.name: d for d in decls}
        by_meaning: dict[str, list[Decl]] = {}
        for d in decls:
            if d.meaning:
                by_meaning.setdefault(d.meaning, []).append(d)
        return cls(root=root, meta=meta, decls=decls, by_name=by_name, by_meaning=by_meaning)

    # --- identity -------------------------------------------------------------------------

    @property
    def commit(self) -> str:
        return self.meta.get("library", {}).get("commit", "")

    @property
    def toolchain(self) -> str:
        return self.meta.get("toolchain", "")

    @property
    def hasher(self) -> dict:
        return self.meta.get("hasher", {})

    def producer(self) -> str:
        p = self.meta.get("producer", {})
        return f"{p.get('name', '?')} {p.get('version', '?')}"

    # --- edges ----------------------------------------------------------------------------

    def notions(self) -> list[str]:
        return [e["name"] for e in self.meta.get("edges", [])]

    def edges(self, notion: str) -> dict[int, list[int]]:
        """Adjacency lists (source id → target ids) for one notion of dependency."""
        if notion not in self._edges:
            entry = next((e for e in self.meta.get("edges", []) if e["name"] == notion), None)
            if entry is None:
                raise KeyError(f"no edge file for notion {notion!r} in {self.root}")
            if entry.get("format") != "i32le-pairs":
                raise ValueError(f"unsupported edge format {entry.get('format')!r}")
            data = array("i")
            data.frombytes((self.root / entry["file"]).read_bytes())
            if sys.byteorder != "little":
                data.byteswap()
            adj: dict[int, list[int]] = {}
            for k in range(0, len(data), 2):
                adj.setdefault(data[k], []).append(data[k + 1])
            self._edges[notion] = adj
        return self._edges[notion]

    def successors(self, name: str, notion: str = "meaning") -> list[str]:
        d = self.by_name.get(name)
        if d is None:
            return []
        return [self.decls[t].name for t in self.edges(notion).get(d.id, [])]

    def closure(self, name: str, notion: str = "meaning", include_self: bool = True) -> list[Decl]:
        """Every node reachable from ``name`` along ``notion`` edges, in breadth-first order."""
        start = self.by_name.get(name)
        if start is None:
            return []
        adj = self.edges(notion)
        seen = {start.id}
        order = [start.id]
        k = 0
        while k < len(order):
            for t in adj.get(order[k], []):
                if t not in seen:
                    seen.add(t)
                    order.append(t)
            k += 1
        nodes = [self.decls[i] for i in order]
        return nodes if include_self else nodes[1:]

    def reverse(self, notion: str = "meaning") -> dict[int, list[int]]:
        """Target id → source ids."""
        rev: dict[int, list[int]] = {}
        for s, ts in self.edges(notion).items():
            for t in ts:
                rev.setdefault(t, []).append(s)
        return rev

    # --- facets ---------------------------------------------------------------------------

    def facet_names(self) -> list[str]:
        return [f["name"] for f in self.meta.get("facets", [])]

    def facet(self, name: str) -> dict[str, list[dict]]:
        """A facet, as declaration name → its rows (usually one). Empty if the dataset lacks it."""
        if name not in self._facets:
            entry = next((f for f in self.meta.get("facets", []) if f["name"] == name), None)
            rows: dict[str, list[dict]] = {}
            if entry is not None:
                with (self.root / entry["file"]).open(encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            row = json.loads(line)
                            rows.setdefault(row["decl"], []).append(row)
            self._facets[name] = rows
        return self._facets[name]

    def facet_row(self, facet: str, name: str) -> dict | None:
        rows = self.facet(facet).get(name)
        return rows[0] if rows else None
