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
from . import records as rec
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


#: The state a record is in when it has no status: a problem or question is open, anything else
#: stands.
OPEN = "open"
STANDS = "stands"


@dataclass
class Evidence:
    """Every record about the current declarations, resolved against a dataset, and read as threads
    (S3, "Threads"): each record's latest status, the comments replying to it, and whether a later
    review by the same reviewer superseded it."""

    dataset: Dataset
    #: current declaration name → [(record, status)], reviews, tests, named results and comments
    by_decl: dict[str, list[tuple[dict, st.Status]]] = field(default_factory=dict)
    #: records whose subject has no current declaration
    orphans: list[tuple[dict, st.Status]] = field(default_factory=list)
    #: record id → the record
    by_id: dict[str, dict] = field(default_factory=dict)
    #: record id → the statuses about it, in time order
    statuses: dict[str, list[dict]] = field(default_factory=dict)
    #: record id → the comments replying to it, in time order
    replies: dict[str, list[dict]] = field(default_factory=dict)
    #: review id → the id of the later review, by the same reviewer, that superseded it
    superseded_by: dict[str, str] = field(default_factory=dict)
    #: problem record id → latest state ("open", "fixed", "intended", "invalid", "withdrawn")
    problem_state: dict[str, str] = field(default_factory=dict)

    @classmethod
    def resolve(cls, records: list[dict], dataset: Dataset, old: dict[str, Dataset] | None = None
                ) -> "Evidence":
        """Resolves records against ``dataset``. ``old`` optionally maps commits to datasets of
        those commits, so that stale-underneath statuses can name what changed."""
        ev = cls(dataset=dataset)
        old = old or {}
        ordered = sorted(records, key=lambda r: r.get("at", ""))
        ev.by_id = {r["id"]: r for r in ordered if r.get("id")}
        decl_of: dict[str, str] = {}
        for r in ordered:
            kind = r.get("kind")
            if kind == "status":
                if r.get("target"):
                    ev.statuses.setdefault(r["target"], []).append(r)
                continue
            if kind == "comment":
                target = (r.get("links") or {}).get("replies_to")
                if target:
                    ev.replies.setdefault(target, []).append(r)
                name = decl_of.get(target or "")
                subject = r.get("subject")
                if name is None and subject:
                    s = st.classify(subject, dataset, old.get(subject.get("commit", "")))
                    name = s.decl.name if s.decl else None
                if name is not None:
                    ev.by_decl.setdefault(name, []).append((r, st.Status(st.CURRENT, dataset.by_name.get(name))))
                    decl_of[r["id"]] = name
                continue
            subject = r.get("subject") or {}
            s = st.classify(subject, dataset, old.get(subject.get("commit", "")))
            if s.decl is not None:
                ev.by_decl.setdefault(s.decl.name, []).append((r, s))
                decl_of[r.get("id", "")] = s.decl.name
            else:
                ev.orphans.append((r, s))
            earlier = (r.get("links") or {}).get("supersedes")
            if kind == "review" and earlier in ev.by_id and \
                    rec.same_reviewer(ev.by_id[earlier].get("by", {}), r.get("by", {})):
                ev.superseded_by[earlier] = r["id"]
        for r in ordered:
            if r.get("kind") == "review" and r.get("verdict") == "problem":
                ev.problem_state[r["id"]] = ev.state(r["id"])
        return ev

    def state(self, record_id: str) -> str:
        """The latest status of a record: ``open`` for a problem or question with none (or
        reopened), ``stands`` for anything else with none."""
        latest = (self.statuses.get(record_id) or [None])[-1]
        r = self.by_id.get(record_id, {})
        opens = r.get("kind") == "review" and r.get("verdict") in ("problem", "question")
        if latest is None or latest.get("state") == "reopened":
            return OPEN if opens else STANDS
        return latest["state"]

    def in_force(self, r: dict, s: st.Status | None = None) -> bool:
        """Whether a review still counts as its reviewer's view of the current code: it applies,
        and is neither withdrawn nor superseded."""
        if r.get("id") in self.superseded_by or self.state(r.get("id", "")) == "withdrawn":
            return False
        return s is None or s.applies

    def records_on(self, name: str, kind: str | None = None) -> list[tuple[dict, st.Status]]:
        rows = self.by_decl.get(name, [])
        return [(r, s) for r, s in rows if kind is None or r.get("kind") == kind]

    def open_problems(self, name: str) -> list[dict]:
        """Problems reported on the declaration and not resolved, whatever version they were
        reported against: a problem stays open until someone says it was fixed."""
        return [r for r, _ in self.records_on(name, "review")
                if r.get("verdict") == "problem" and r["id"] not in self.superseded_by
                and self.state(r["id"]) == OPEN]

    def open_questions(self, name: str) -> list[dict]:
        return [r for r, _ in self.records_on(name, "review")
                if r.get("verdict") == "question" and self.state(r["id"]) == OPEN]

    def counting_accepts(self, name: str, policy: Policy) -> list[dict]:
        out = []
        for r, s in self.records_on(name, "review"):
            if r.get("verdict") != "accept":
                continue
            if not self.in_force(r):
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

    def disagreement(self, name: str) -> bool:
        """An acceptance in force and an open problem on the same declaration."""
        accepts = [r for r, s in self.records_on(name, "review")
                   if r.get("verdict") == "accept" and self.in_force(r, s)]
        return bool(accepts) and bool(self.open_problems(name))

    def checked(self, name: str) -> dict[str, list[dict]]:
        """For each failure mode, the acceptances in force that say they checked it."""
        out: dict[str, list[dict]] = {}
        for r, s in self.records_on(name, "review"):
            if r.get("verdict") == "accept" and self.in_force(r, s):
                for mode, state in (r.get("checked") or {}).items():
                    if state == "checked":
                        out.setdefault(mode, []).append(r)
        return out


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
