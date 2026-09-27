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
    """Each definition's characterizations (`@[characterization]`), per characterization:
    ``{property, target, comment, existence: [decl], uniqueness: [{decl, relation}], complete, open, context,
    assuming}``.

    With a predicate (`@[characterization property d]`), the parts are assembled per predicate, and a
    characterization is complete when it has both an existence and a uniqueness theorem. Stated by one
    theorem (role ``theorem``, no predicate), the theorem is both the property and the uniqueness half:
    ``existence`` lists the `@[specifies]` theorems that showed the definition satisfies each condition
    (the theorem itself when reflexivity did), ``open`` the conditions nothing showed, ``context``
    the hypotheses that are not about the candidate, where the characterization holds, and
    ``assuming`` the premises of those `@[specifies]` theorems that were assumed rather than shown:
    where the definition has the property."""
    by_prop: dict[tuple, dict] = {}
    for decl, payloads in ds.annotations("characterization").items():
        for p in payloads:
            key = (p.get("property"), p.get("target"))
            c = by_prop.setdefault(key, {"property": p.get("property"), "target": p.get("target"),
                                         "comment": "", "existence": [], "uniqueness": [],
                                         "open": [], "context": [], "variables": [], "specialized": [],
                                         "assuming": []})
            if p.get("role") == "property":
                c["comment"] = p.get("comment", "")
            elif p.get("role") == "existence":
                c["existence"].append(decl)
            elif p.get("role") == "uniqueness":
                c["uniqueness"].append({"decl": decl, "relation": p.get("relation", "")})
            elif p.get("role") == "theorem":
                conds = p.get("conditions", [])
                shown = list(dict.fromkeys(b for k in conds if k.get("proved") for b in k.get("by", [])))
                c.update(comment=p.get("comment", ""), form=p.get("form", ""),
                         existence=shown or ([decl] if p.get("complete") else []),
                         uniqueness=[{"decl": decl, "relation": p.get("relation", "")}],
                         complete=bool(p.get("complete")), context=p.get("context", []),
                         variables=p.get("variables", []), specialized=p.get("specialized", []),
                         open=[k.get("text", "") for k in conds if not k.get("proved")],
                         assuming=list(dict.fromkeys(a for k in conds for a in k.get("assuming", []))))
    out: dict[str, list[dict]] = defaultdict(list)
    for (_, target), c in by_prop.items():
        c.setdefault("complete", bool(c["existence"] and c["uniqueness"]))
        out[target].append(c)
    return dict(out)


def domains(ds: Dataset) -> dict[str, dict]:
    """Each definition's declared domain (`@[domain]`): where it is meant to apply, as
    ``{statement, note, source}``, ``source`` being ``author`` when declared in the definition's own
    module and ``catalogue`` when a catalogue declared it for a definition it does not own."""
    out: dict[str, dict] = {}
    for decl, payloads in ds.annotations("domain").items():
        for p in payloads:
            out[decl] = {"statement": p.get("statement", ""), "note": p.get("note", ""),
                         "source": p.get("source", "author")}
    return out


def up_to(ds: Dataset) -> dict[str, dict]:
    """What each definition is declared to be determined up to (`@[up_to]`): ``{statement,
    relationHead, note, source}``, ``statement`` being the relation applied to two variables
    (``x =ᵐ[μ] y``). What proves it is a characterization whose uniqueness theorem ends in such a
    relation; the two are shown side by side rather than compared here."""
    out: dict[str, dict] = {}
    for decl, payloads in ds.annotations("up_to").items():
        for p in payloads:
            head = p.get("relationHead", "")
            out[decl] = {"statement": p.get("statement", ""), "note": p.get("note", ""),
                         "relationHead": "" if head in ("", "[anonymous]") else head,
                         "source": p.get("source", "author")}
    return out


# The statuses of a well-definedness obligation, from the one a reader should see first.
WELL_DEFINED_STATUSES = ("open", "refuted", "unapplied", "irrelevant", "discharged")
# Those that leave a use of the definition outside what the statement shows to be its domain.
WELL_DEFINED_LEFT = ("open", "refuted", "unapplied")


def well_definedness(ds: Dataset) -> dict[str, dict]:
    """What the well-definedness analyzer found in each declaration's statement (facet `welldefined`,
    written by `trust-extract welldefined`): each use of a definition with a declared domain, and
    whether its arguments are shown to be in the domain given what is in scope where it sits.

    ``{obligations, counts, left, error}``: the obligations as the facet has them (``op``,
    ``source``, ``place``, ``term``, ``goal``, ``status``, ``by``, ``hypothesis``, ``bound``),
    their count per status, and ``left``, the goals not shown (open, refuted or unapplied), each
    once: what a claim leaves unsaid about the domains of what it uses. A merged dataset can hold
    two analyses of a declaration; the last one wins."""
    out: dict[str, dict] = {}
    for decl, rows in ds.facet("welldefined").items():
        row = rows[-1]
        obs = row.get("obligations", [])
        counts: dict[str, int] = {}
        left: list[str] = []
        for o in obs:
            s = o.get("status", "open")
            counts[s] = counts.get(s, 0) + 1
            if s in WELL_DEFINED_LEFT and o.get("goal", o.get("term", "")) not in left:
                left.append(o.get("goal") or o.get("term", ""))
        out[decl] = {"obligations": obs, "counts": counts, "left": left, "error": row.get("error")}
    return out


def well_definedness_meta(ds: Dataset) -> dict | None:
    """How the well-definedness facet was made: the analyzer, its dischargers and their budget, the
    domains it knew of. The results depend on them. ``None`` when the dataset has no such facet."""
    entry = next((f for f in ds.meta.get("facets", []) if f["name"] == "welldefined"), None)
    if entry is None:
        return None
    return {k: entry[k] for k in ("analyzer", "dischargers", "heartbeats", "domains", "targets") if k in entry}


def is_characterized(chars: list[dict]) -> bool:
    return any(c["complete"] for c in chars)


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
