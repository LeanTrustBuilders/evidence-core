"""Self-checks over datasets (dependency-testing.md §9 in LeanTrustBuilders/design).

**Graph against hash** (check 1). The meaning hash is deep: it changes when anything a declaration's
meaning rests on changes. The `meaning` graph says what that is. Over two datasets of consecutive
commits, the two must agree, for every project declaration D present in both:

* **unexplained**: D's meaning hash changed but its local hash did not ("stale underneath"), and
  nothing in D's graph closure changed. The graph is missing a dependency, or the hash depends on
  something the graph does not count;
* **missed**: something in D's graph closure changed meaning, but D's meaning hash did not. The
  hash does not depend on something the graph counts, or the graph has an extra dependency. A review
  of D would read "current" although the graph says what it rests on changed.

"Changed" is judged by name: a node whose meaning hash differs between the datasets, or that exists
in only one of them. Closures are taken in both datasets' graphs, since a dependency can be added or
removed by the change itself. Each finding comes with a path through the graph to the changed node
it is about, which is where to start looking.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .dataset import Dataset


@dataclass
class Finding:
    decl: str
    kind: str                      # "unexplained" or "missed"
    #: for "missed": a shortest path from D to a changed node, in the graph it was found in
    path: list[str] = field(default_factory=list)
    graph: str = ""                # "old" or "new"
    #: for the changed node at the end of the path: was it itself rewritten (local hash changed)?
    rewritten: bool | None = None
    #: for "unexplained": the size of D's closure in each graph
    closure: tuple[int, int] = (0, 0)

    def as_json(self) -> dict:
        out = {"decl": self.decl, "kind": self.kind}
        if self.path:
            out.update(path=self.path, graph=self.graph, rewritten=self.rewritten)
        else:
            out.update(closure={"old": self.closure[0], "new": self.closure[1]})
        return out


@dataclass
class GraphHashReport:
    compared: int
    changed: int                   # project declarations whose meaning hash changed
    stale_underneath: int          # of which with an unchanged local hash
    findings: list[Finding]

    @property
    def unexplained(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "unexplained"]

    @property
    def missed(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "missed"]

    def summary(self) -> dict:
        return {"compared": self.compared, "meaningChanged": self.changed,
                "staleUnderneath": self.stale_underneath,
                "unexplained": len(self.unexplained), "missed": len(self.missed)}


def changed_names(a: Dataset, b: Dataset) -> set[str]:
    """Nodes whose meaning moved between ``a`` and ``b``: present in both with another meaning hash,
    or present in only one with a meaning hash the other does not have. A renamed declaration keeps
    its meaning hash, which does not depend on names, and is not a change."""
    out = set()
    for d in a.decls:
        e = b.by_name.get(d.name)
        if e is not None:
            if e.meaning != d.meaning:
                out.add(d.name)
        elif d.meaning not in b.by_meaning:
            out.add(d.name)
    out.update(d.name for d in b.decls if d.name not in a.by_name and d.meaning not in a.by_meaning)
    return out


def _reaching(ds: Dataset, targets: set[str], notion: str) -> set[str]:
    """Every node that reaches one of ``targets`` along ``notion`` (targets themselves excluded
    unless they reach another)."""
    rev: dict[int, list[int]] = defaultdict(list)
    for s, ts in ds.edges(notion).items():
        for t in ts:
            rev[t].append(s)
    seen: set[int] = set()
    stack = [ds.by_name[n].id for n in targets if n in ds.by_name]
    while stack:
        x = stack.pop()
        for y in rev.get(x, ()):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return {ds.decls[i].name for i in seen}


def _path_to(ds: Dataset, start: str, targets: set[str], notion: str) -> list[str]:
    """A shortest path from ``start`` to a node of ``targets``, along ``notion``."""
    if start not in ds.by_name:
        return []
    edges = ds.edges(notion)
    s = ds.by_name[start].id
    prev = {s: None}
    queue = deque([s])
    while queue:
        x = queue.popleft()
        for t in edges.get(x, ()):
            if t in prev:
                continue
            prev[t] = x
            if ds.decls[t].name in targets:
                path, y = [], t
                while y is not None:
                    path.append(ds.decls[y].name)
                    y = prev[y]
                return path[::-1]
            queue.append(t)
    return []


def _closure_size(ds: Dataset, name: str, notion: str) -> int:
    return len(ds.closure(name, notion, include_self=False)) if name in ds.by_name else 0


def graph_against_hash(old: Dataset, new: Dataset, notion: str = "meaning") -> GraphHashReport:
    changed = changed_names(old, new)
    reach_old, reach_new = _reaching(old, changed, notion), _reaching(new, changed, notion)
    findings: list[Finding] = []
    compared = moved = stale_under = 0
    for d in new.decls:
        o = old.by_name.get(d.name)
        if not d.is_project or o is None:
            continue
        compared += 1
        hash_moved = o.meaning != d.meaning
        if hash_moved:
            moved += 1
            if o.local == d.local:
                stale_under += 1
                if d.name not in reach_old and d.name not in reach_new:
                    findings.append(Finding(d.name, "unexplained",
                                            closure=(_closure_size(old, d.name, notion),
                                                     _closure_size(new, d.name, notion))))
        elif d.name in reach_old or d.name in reach_new:
            graph, ds = ("new", new) if d.name in reach_new else ("old", old)
            path = _path_to(ds, d.name, changed, notion)
            end = path[-1] if path else ""
            e_old, e_new = old.by_name.get(end), new.by_name.get(end)
            rewritten = (e_old is None or e_new is None or e_old.local != e_new.local) if end else None
            findings.append(Finding(d.name, "missed", path=path, graph=graph, rewritten=rewritten))
    findings.sort(key=lambda f: (f.kind, f.decl))
    return GraphHashReport(compared=compared, changed=moved, stale_underneath=stale_under,
                           findings=findings)
