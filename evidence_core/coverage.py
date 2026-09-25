"""Coverage of a claim, under a policy chosen by the reader.

A claim is **covered** when every project declaration in its ``meaning`` closure (the claim itself
included) has at least one review that *counts* under the reader's policy, and none has an open
problem. Upstream declarations are reported separately and, by default, not required.

Reviews are data; which ones count is the reader's policy. The same records serve a cautious
referee (people only, current reviews only) and a relaxed user (anyone, stale underneath accepted).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import Dataset, Decl
from . import status as st


@dataclass(frozen=True)
class Policy:
    """Which reviews count toward coverage."""

    #: Count reviews by AI agents.
    agents: bool = False
    #: Count acceptances whose subject changed underneath since (the declaration itself did not).
    stale_underneath: bool = False
    #: Count acceptances with caveats.
    caveats: bool = True
    #: Count reviews whose reviewer is the declaration's author.
    authors: bool = True
    #: Require upstream declarations in the closure to be reviewed too.
    upstream: bool = False


@dataclass
class Evidence:
    """Every record about the current declarations, resolved against a dataset."""

    dataset: Dataset
    #: current declaration name → [(record, status)]
    by_decl: dict[str, list[tuple[dict, st.Status]]] = field(default_factory=dict)
    #: records whose subject has no current declaration
    orphans: list[tuple[dict, st.Status]] = field(default_factory=list)
    #: problem record id → latest state ("open", "fixed", "intended", "invalid", "withdrawn")
    problem_state: dict[str, str] = field(default_factory=dict)

    @classmethod
    def resolve(cls, records: list[dict], dataset: Dataset, old: dict[str, Dataset] | None = None
                ) -> "Evidence":
        """Resolves records against ``dataset``. ``old`` optionally maps commits to datasets of
        those commits, so that stale-underneath statuses can name what changed."""
        ev = cls(dataset=dataset)
        old = old or {}
        for r in sorted(records, key=lambda r: r.get("at", "")):
            if r.get("kind") == "status":
                target = r.get("target")
                state = r.get("state")
                if target:
                    ev.problem_state[target] = "open" if state == "reopened" else state
                continue
            subject = r.get("subject") or {}
            s = st.classify(subject, dataset, old.get(subject.get("commit", "")))
            if r.get("kind") == "review" and r.get("verdict") == "problem":
                ev.problem_state.setdefault(r["id"], "open")
            if s.decl is not None:
                ev.by_decl.setdefault(s.decl.name, []).append((r, s))
            else:
                ev.orphans.append((r, s))
        return ev

    def records_on(self, name: str, kind: str | None = None) -> list[tuple[dict, st.Status]]:
        rows = self.by_decl.get(name, [])
        return [(r, s) for r, s in rows if kind is None or r.get("kind") == kind]

    def open_problems(self, name: str) -> list[dict]:
        return [r for r, _ in self.records_on(name, "review")
                if r.get("verdict") == "problem" and self.problem_state.get(r["id"]) == "open"]

    def counting_accepts(self, name: str, policy: Policy) -> list[dict]:
        out = []
        for r, s in self.records_on(name, "review"):
            if r.get("verdict") != "accept":
                continue
            if self.problem_state.get(r["id"]) == "withdrawn":
                continue
            if not (s.applies or (policy.stale_underneath and s.state == st.STALE_UNDERNEATH)):
                continue
            by = r.get("by", {})
            if by.get("kind") == "agent" and not policy.agents:
                continue
            if by.get("involvement") == "author" and not policy.authors:
                continue
            if r.get("caveats") and not policy.caveats:
                continue
            out.append(r)
        return out

    def reviewed(self, name: str, policy: Policy) -> bool:
        return bool(self.counting_accepts(name, policy)) and not self.open_problems(name)


@dataclass
class Coverage:
    claim: str
    members: list[Decl]
    covered: list[Decl]
    uncovered: list[Decl]
    with_problems: list[Decl]
    upstream: list[Decl]

    @property
    def is_covered(self) -> bool:
        return not self.uncovered and not self.with_problems

    @property
    def fraction(self) -> float:
        return len(self.covered) / len(self.members) if self.members else 1.0

    def summary(self) -> dict:
        return {"claim": self.claim, "covered": self.is_covered, "members": len(self.members),
                "reviewed": len(self.covered), "unreviewed": [d.name for d in self.uncovered],
                "with_problems": [d.name for d in self.with_problems],
                "upstream": len(self.upstream)}


def coverage(ev: Evidence, claim: str, policy: Policy = Policy(), notion: str = "meaning"
             ) -> Coverage:
    closure = ev.dataset.closure(claim, notion)
    members = [d for d in closure if d.is_project or policy.upstream]
    upstream = [d for d in closure if not d.is_project]
    covered, uncovered, problems = [], [], []
    for d in members:
        if ev.open_problems(d.name):
            problems.append(d)
        elif ev.reviewed(d.name, policy):
            covered.append(d)
        else:
            uncovered.append(d)
    return Coverage(claim=claim, members=members, covered=covered, uncovered=uncovered,
                    with_problems=problems, upstream=upstream)


def queue(ev: Evidence, claims: list[str], policy: Policy = Policy(), notion: str = "meaning",
          limit: int | None = None) -> list[tuple[Decl, int]]:
    """Unreviewed project declarations in the claims' closures, ranked by how many claims rest on
    them, then by how many project declarations use them."""
    weight: dict[str, int] = {}
    for c in claims:
        for d in ev.dataset.closure(c, notion):
            if d.is_project and not ev.reviewed(d.name, policy):
                weight[d.name] = weight.get(d.name, 0) + 1
    rev = ev.dataset.reverse(notion)
    ranked = sorted(weight.items(), key=lambda kv: (-kv[1], -len(rev.get(ev.dataset.by_name[kv[0]].id,
                                                                        [])), kv[0]))
    out = [(ev.dataset.by_name[n], w) for n, w in ranked]
    return out[:limit] if limit else out
