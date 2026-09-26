"""What a dataset (S2) says about its declarations beyond their records: what each rests on, where a
`sorry` comes from, which theorems specify or characterize a definition, what a claim's page covers,
and which packages a reader trusts.

These are read from the dataset alone, the same way for every page that shows them.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .dataset import Dataset

#: The axioms every Mathlib proof may use; any other is worth pointing out.
ORDINARY_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
#: The annotations that are evidence about a definition, and what they say.
EVIDENCE_ANNOTATIONS = ("specifies", "example_of", "nonexample_of", "characterization")


@dataclass
class Closures:
    """The `meaning` closures of a dataset's declarations, split into project declarations and
    upstream constants, each computed once."""

    dataset: Dataset
    notion: str = "meaning"
    _cache: dict[int, tuple[frozenset, frozenset]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.edges = self.dataset.edges(self.notion)

    def of(self, decl_id: int) -> tuple[frozenset, frozenset]:
        """(project ids, upstream ids) the declaration rests on, itself excluded."""
        if decl_id in self._cache:
            return self._cache[decl_id]
        decls = self.dataset.decls
        seen, stack, project, upstream = {decl_id}, [decl_id], set(), set()
        while stack:
            x = stack.pop()
            for t in self.edges.get(x, ()):
                if t in seen:
                    continue
                seen.add(t)
                if decls[t].is_project:
                    project.add(t)
                    stack.append(t)
                else:
                    upstream.add(t)
        self._cache[decl_id] = (frozenset(project), frozenset(upstream))
        return self._cache[decl_id]


@dataclass(frozen=True)
class Sorry:
    """Whether a declaration depends on `sorry`, and whether the `sorry` is its own: none of the
    project declarations it uses depends on one (``via`` lists those that do)."""

    uses: bool
    own: bool
    via: tuple[str, ...] = ()


def sorry_of(ds: Dataset, name: str) -> Sorry:
    """From the axioms facet (which is transitive) and the `term` edges (the `meaning` ones when the
    dataset has no `term` notion)."""
    axioms = ds.facet_row("axioms", name) or {}
    if not axioms.get("sorry"):
        return Sorry(False, False)
    d = ds.by_name[name]
    meaning = ds.edges("meaning")
    uses = ds.edges("term").get(d.id, meaning.get(d.id, ())) if "term" in ds.notions() else meaning.get(d.id, ())
    via = tuple(ds.decls[t].name for t in uses
                if ds.decls[t].is_project and (ds.facet_row("axioms", ds.decls[t].name) or {}).get("sorry"))
    return Sorry(True, not via, via)


def extra_axioms(ds: Dataset, name: str) -> list[str]:
    """The axioms a declaration depends on beyond the ordinary ones and `sorryAx`."""
    return [a for a in (ds.facet_row("axioms", name) or {}).get("axioms", [])
            if a not in ORDINARY_AXIOMS and a != "sorryAx"]


def specifications(ds: Dataset) -> dict[str, list[dict]]:
    """Each definition that theorems say something about (`@[specifies]`, `@[example_of]`,
    `@[nonexample_of]`): ``{decl, comment, kind}`` per theorem, kind ``specifies``, ``example`` or
    ``nonexample``."""
    out: dict[str, list[dict]] = defaultdict(list)
    for thm, payloads in ds.annotations("specifies").items():
        for p in payloads:
            out[p.get("target", "")].append({"decl": thm, "comment": p.get("comment", ""), "kind": "specifies"})
    for attr in ("example_of", "nonexample_of"):
        for thm, payloads in ds.annotations(attr).items():
            for p in payloads:
                out[p.get("target", "")].append({"decl": thm, "comment": "", "kind": attr.replace("_of", "")})
    return dict(out)


def characterizations(ds: Dataset) -> dict[str, list[dict]]:
    """Each definition characterized by a property (`@[characterization]`): per property,
    ``{property, target, comment, existence: [decl], uniqueness: [{decl, relation}]}``. A definition
    is characterized when some property has both an existence and a uniqueness theorem."""
    by_prop: dict[tuple, dict] = {}
    for decl, payloads in ds.annotations("characterization").items():
        for p in payloads:
            key = (p.get("property"), p.get("target"))
            c = by_prop.setdefault(key, {"property": p.get("property"), "target": p.get("target"),
                                         "comment": "", "existence": [], "uniqueness": []})
            if p.get("role") == "property":
                c["comment"] = p.get("comment", "")
            elif p.get("role") == "existence":
                c["existence"].append(decl)
            elif p.get("role") == "uniqueness":
                c["uniqueness"].append({"decl": decl, "relation": p.get("relation", "")})
    out: dict[str, list[dict]] = defaultdict(list)
    for (_, target), c in by_prop.items():
        out[target].append(c)
    return dict(out)


def is_characterized(chars: list[dict]) -> bool:
    return any(c["existence"] and c["uniqueness"] for c in chars)


def evidence_targets(ds: Dataset) -> dict[str, set[str]]:
    """Each theorem annotated as evidence about definitions, and those definitions."""
    out: dict[str, set[str]] = defaultdict(set)
    for attr in EVIDENCE_ANNOTATIONS:
        for decl, payloads in ds.annotations(attr).items():
            for p in payloads:
                out[decl].add(p.get("target", ""))
    return dict(out)


def claim_scope(ds: Dataset, seeds: list[str], notion: str = "meaning") -> tuple[set[int], set[int]]:
    """What a page about some claims covers: the seeds, closed under ``notion`` within the project,
    then closed again after pulling in every theorem annotated as evidence about a definition
    already in scope, to a fixpoint. Returns (scope, the ids pulled in that way)."""
    by_name, edges = ds.by_name, ds.edges(notion)
    targets_of = evidence_targets(ds)
    scope: set[int] = set()
    pulled: set[int] = set()
    frontier = [by_name[s].id for s in seeds]
    while True:
        while frontier:
            x = frontier.pop()
            if x in scope:
                continue
            scope.add(x)
            for t in edges.get(x, ()):
                if ds.decls[t].is_project and t not in scope:
                    frontier.append(t)
        in_scope = {ds.decls[i].name for i in scope}
        new = [by_name[thm].id for thm, ts in targets_of.items()
               if thm in by_name and by_name[thm].id not in scope and ts & in_scope]
        if not new:
            return scope, pulled
        pulled.update(new)
        frontier.extend(new)


def trusted_packages(packages: list[dict], trust: list[str]) -> set[str]:
    """The packages a reader trusts when trusting ``trust``: trusting a package trusts everything it
    depends on."""
    requires = {p["name"]: p["requires"] for p in packages}
    out, stack = set(), list(trust)
    while stack:
        p = stack.pop()
        if p in out:
            continue
        out.add(p)
        stack.extend(requires.get(p, []))
    return out
