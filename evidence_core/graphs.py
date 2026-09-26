"""The dependency graphs of a dataset (S2), summarized for readers who are not Lean experts.

Two graphs answer two questions:
- the **meaning** graph, *what a declaration is*: a definition is built from the concepts in its
  type and value; a theorem is about the concepts in its statement;
- the **proof** graph (`term` edges beyond the statement's), *why a theorem is true*: the results
  its proof uses.

A reader wants concepts and results, not the machinery around them, so both are summarized:
- a **concept** is a definition, structure, class or inductive type that is not a proof and not an
  instance; a **result** is a theorem; instances, generated helpers (`match_1`, `proof_2`, `eq_1`, …)
  and constructors are gone through, to the concepts and results behind them;
- every declaration gets an **importance**, from how much rests on it (meaning and proofs), whether
  it is documented, and how much a library's own catalogues single it out;
- **basic** notions are flagged: those the library builds on rather than defines (Lean's own
  equality, numbers, notation classes, …), which pages list apart rather than as what a declaration
  is "built from";
- **routine** proof steps are flagged: results a large share of all proofs use (`mul_one`,
  `le_refl`), results from below the library, and the lemmas of proof automation (`Mathlib.Tactic.…`,
  `Mathlib.Meta.…`), which pages count rather than list.

Instances and helpers are gone through by their type (their statement), never by their value: an
instance's value is how it is implemented, not what the declaration using it is about.

Everything is computed once for the whole dataset, in arrays indexed by declaration id.
"""
from __future__ import annotations

import math
import re
from array import array

from .dataset import Dataset, Decl

CONCEPT_KINDS = frozenset({"definition", "structure", "class", "inductive", "opaque", "axiom"})
AUXILIARY = re.compile(r"(^_private\.|\._|\.proof_\d+$|\.match_\d+$|\.eq_\d+$|\.eq_def$|\.sizeOf_spec$|"
                       r"\.(rec|recOn|casesOn|noConfusion|noConfusionType|below|brecOn|binductionOn|ibelow|"
                       r"injEq|inj|ctorIdx|toCtorIdx)$|_aux(_\d+)?$|\._simp_\d+$)")


MACHINERY = re.compile(r"^(Mathlib\.Tactic|Mathlib\.Meta|Mathlib\.Util|Lean|Std\.Tactic|Aesop|Qq|Batteries\.Tactic)\.")


def is_auxiliary(name: str) -> bool:
    return bool(AUXILIARY.search(name))


def is_machinery(name: str) -> bool:
    """Part of a tactic's workings rather than of the mathematics."""
    return bool(MACHINERY.match(name))


def is_concept(d: Decl) -> bool:
    return not d.is_prop and d.kind in CONCEPT_KINDS and not is_auxiliary(d.name)


def is_result(d: Decl) -> bool:
    return d.is_prop and d.kind == "theorem" and not is_auxiliary(d.name)


class Graphs:
    """Summaries of a dataset's meaning and proof graphs."""

    def __init__(self, ds: Dataset, boost: dict[str, float] | None = None, routine_share: float = 0.002):
        self.ds = ds
        n = len(ds.decls)
        self.meaning = ds.edges("meaning")
        self.statement = ds.edges("statement") if "statement" in ds.notions() else self.meaning
        self.term = ds.edges("term") if "term" in ds.notions() else {}
        decls = ds.decls
        self.concept = bytearray(1 if is_concept(d) else 0 for d in decls)
        self.result = bytearray(1 if is_result(d) else 0 for d in decls)
        # How much rests on each declaration: meaning users, and proofs that use it.
        self.meaning_users = array("i", [0]) * n
        for s, ts in self.meaning.items():
            for t in ts:
                self.meaning_users[t] += 1
        self.proof_users = array("i", [0]) * n
        for s, ts in self.term.items():
            st = set(self.statement.get(s, ()))
            for t in ts:
                if t not in st:
                    self.proof_users[t] += 1
        boost = boost or {}
        self.boost = boost
        documented = set(ds.facet("docstring"))
        self.documented = bytearray(1 if d.name in documented else 0 for d in decls)
        self.importance = array("d", [0.0]) * n
        for d in decls:
            self.importance[d.id] = (math.log1p(self.meaning_users[d.id]) + 0.5 * math.log1p(self.proof_users[d.id])
                                     + (1.0 if d.name in documented else 0.0) + boost.get(d.name, 0.0)
                                     - (3.0 if is_auxiliary(d.name) else 0.0))
        # Basic: what the library builds on rather than defines. Routine: results a large share of the
        # proofs use, results from below the library, and proof automation's lemmas.
        self.basic = bytearray(1 if not d.is_project else 0 for d in decls)
        results = sum(1 for d in decls if d.is_project and self.result[d.id])
        threshold = max(100, int(routine_share * results))
        self.routine = bytearray(1 if self.result[d.id] and (not d.is_project or is_machinery(d.name)
                                                            or self.proof_users[d.id] > threshold) else 0
                                 for d in decls)
        self._built: dict[int, tuple[int, ...]] = {}

    # --- going through what a reader does not want to see ---------------------------------------

    def _to(self, start, want, through: dict, go_through=None) -> tuple[int, ...]:
        """The declarations of the kind ``want`` reached from ``start``, going through the others
        (instances, helpers) along ``through``, in first-seen order; ``go_through`` limits which
        others are gone through."""
        out, seen, stack = [], set(), list(reversed(start))
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            if want(x):
                out.append(x)
            elif not self.concept[x] and not self.result[x] and (go_through is None or go_through(x)):
                stack.extend(reversed(through.get(x, ())))
        return tuple(out)

    def built_from(self, i: int) -> tuple[int, ...]:
        """The concepts a declaration is directly built from: those its meaning (type and value)
        names, instances and helpers gone through by their type."""
        if i not in self._built:
            self._built[i] = self._to([t for t in self.meaning.get(i, ()) if t != i], lambda x: self.concept[x],
                                      self.statement)
        return self._built[i]

    def about(self, i: int) -> tuple[int, ...]:
        """The concepts a statement is about."""
        return self._to([t for t in self.statement.get(i, ()) if t != i], lambda x: self.concept[x], self.statement)

    def proof_uses(self, i: int) -> tuple[int, ...]:
        """The results a declaration's proof (or value) uses beyond its statement, through the helpers
        its proof was split into (not through instances or definitions, whose proofs are their own)."""
        st = set(self.statement.get(i, ()))
        decls = self.ds.decls
        return self._to([t for t in self.term.get(i, ()) if t not in st and t != i], lambda x: self.result[x],
                        self.term, lambda x: is_auxiliary(decls[x].name) and decls[x].kind != "instance")

    # --- reverse ----------------------------------------------------------------------------------

    def reverse(self) -> "Reverse":
        return Reverse(self)

    def rank(self, ids, limit: int | None = None) -> list[int]:
        """Most important first."""
        ranked = sorted(set(ids), key=lambda x: (-self.importance[x], self.ds.decls[x].name))
        return ranked[:limit] if limit else ranked

    def rank_steps(self, ids, limit: int | None = None) -> list[int]:
        """The steps of a proof, the telling ones first: not routine, documented or singled out by
        the catalogues, then those more results use."""
        name = lambda x: self.ds.decls[x].name
        ranked = sorted(set(ids), key=lambda x: (self.routine[x], -(self.documented[x] + self.boost.get(name(x), 0.0)),
                                                 -self.proof_users[x], name(x)))
        return ranked[:limit] if limit else ranked


class Reverse:
    """What rests on each declaration: concepts built on it, results about it, instances of it
    (examples), results whose proofs use it."""

    def __init__(self, g: Graphs):
        self.g = g
        n = len(g.ds.decls)
        self.built_on: list[list[int]] = [[] for _ in range(n)]
        self.facts: list[list[int]] = [[] for _ in range(n)]
        self.examples: list[list[int]] = [[] for _ in range(n)]
        self.used_in: list[list[int]] = [[] for _ in range(n)]
        decls = g.ds.decls
        for d in decls:
            if not d.is_project:
                continue
            if g.concept[d.id]:
                for t in g.built_from(d.id):
                    self.built_on[t].append(d.id)
            elif g.result[d.id]:
                for t in g.about(d.id):
                    self.facts[t].append(d.id)
                for t in g.proof_uses(d.id):
                    self.used_in[t].append(d.id)
            elif d.kind == "instance":
                # Its value may use results: it is one of their uses (`ℂ is algebraically closed`
                # is how most of a library uses the fundamental theorem of algebra).
                for t in g.proof_uses(d.id):
                    self.used_in[t].append(d.id)
                # An example of the class it is an instance of: the head of its statement.
                head = (g.ds.facet_row("statement", d.name) or {}).get("conclusionHead")
                t = g.ds.by_name.get(head) if head else None
                if t is not None and g.concept[t.id]:
                    self.examples[t.id].append(d.id)
