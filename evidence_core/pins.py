"""What pins a definition down: everything that says, or would say, what it means.

A definition is taken on faith unless something says what it means. Three sources say it, and a page
lists them together, each marked by where it comes from:

- **in the code**, written by the library's authors: theorems annotated `@[specifies]`,
  `@[example_of]`, `@[nonexample_of]` and `@[characterization]` (whose shapes Lean checks). An
  `example` that merely names the definition is not one: it need not be about it;
- **from reviewers**, in an evidence store (S3): declarations of the library listed as its tests
  (`test` records) and proposed tests someone met (`challenge` met by a declaration). Each passes
  while it is in the library at the dataset's commit without `sorry`, and says whether its statement
  mentions the definition, as `@[specifies]` requires;
- **wanted**: proposed tests still open (`challenge` records nobody has met).

A theorem in the code that is annotated as saying what a definition means, but was written in another
package than the definition's, comes **from a catalogue**: a package that says things about a
library's declarations from outside it (merged into its dataset, see ``merge``). It is checked by
Lean like the library's own, and is shown apart, since the library's authors did not write it.

A definition is **pinned** when something in the code or in a catalogue, or a reviewer's test that
passes, says what it means; what is only wanted does not pin it.
"""
from __future__ import annotations

from .analysis import characterizations, is_characterized, specifications
from .coverage import Evidence
from .dataset import Dataset
from . import records as rec
from .views import actions, by_view

CODE, CATALOGUE, REVIEWERS, WANTED = "code", "catalogue", "reviewers", "wanted"


class Pins:
    """The pins of every definition of a dataset, computed once."""

    def __init__(self, ds: Dataset, ev: Evidence | None = None):
        self.ds, self.ev = ds, ev
        self.specs = specifications(ds)
        self.chars = characterizations(ds)

    def of(self, name: str) -> list[dict]:
        """Each pin of a definition: ``{source, kind, …}``, in the order code, catalogue, reviewers,
        wanted."""
        out = []
        # A characterization is also recorded as a specification: it is shown once, as what it is.
        characterizing = {c["property"] for c in self.chars.get(name, [])}
        for s in self.specs.get(name, []):
            if s["decl"] in characterizing:
                continue
            out.append({"source": self._written(s["decl"], name), "kind": s["kind"], "decl": s["decl"],
                        "comment": s["comment"]})
        for c in self.chars.get(name, []):
            out.append({"source": self._written(c["property"], name), "kind": "characterization",
                        "decl": c["property"], "comment": c["comment"],
                        "existence": c["existence"], "uniqueness": c["uniqueness"],
                        "complete": c["complete"], "open": c["open"], "context": c["context"],
                        "variables": c["variables"], "specialized": c["specialized"],
                        "assuming": c["assuming"]})
        out.sort(key=lambda p: p["source"] == CATALOGUE)       # the library's own first
        if self.ev is not None:
            for t in self.ev.tests(name):
                r = t["record"] if "challenge" not in t else t["met"]
                out.append({"source": REVIEWERS, "kind": "met challenge" if "challenge" in t else "test", "decl": t["test"],
                            "comment": t["text"], "result": t["result"], "mentions": t["mentions"],
                            "by": by_view(r.get("by", {})), "at": r.get("at", ""), "url": rec.origin_url(r.get("origin")),
                            "id": (t.get("challenge") or t["record"])["id"]})
            for c, state in self.ev.challenges(name):
                if state == "open":
                    out.append({"source": WANTED, "kind": "challenge", "comment": c.get("text", ""),
                                "statement": c.get("statement", ""), "catches": c.get("catches", ""),
                                "by": by_view(c.get("by", {})), "at": c.get("at", ""),
                                "url": rec.origin_url(c.get("origin")), "id": c["id"], "actions": actions(self.ev, c)})
        return out

    def _written(self, decl: str, name: str) -> str:
        """Where a theorem about `name` was written: in the code, or in a catalogue when its package is
        not the definition's."""
        d, n = self.ds.by_name.get(decl), self.ds.by_name.get(name)
        return CATALOGUE if d is not None and n is not None and d.package != n.package else CODE

    def pinned(self, pins: list[dict]) -> bool:
        """Whether something says what the definition means: anything in the code, or a reviewer's
        test that passes and is about it."""
        return any(p["source"] in (CODE, CATALOGUE) and p.get("result", "passes") == "passes" for p in pins) or \
            any(p["source"] == REVIEWERS and p["result"] == "passes" and p.get("mentions") is not False for p in pins)

    def summary(self, name: str, pins: list[dict] | None = None) -> dict:
        pins = self.of(name) if pins is None else pins
        count = lambda src: sum(1 for p in pins if p["source"] == src)
        return {"pinned": self.pinned(pins), "characterized": is_characterized(self.chars.get(name, [])),
                "code": count(CODE), "catalogue": count(CATALOGUE), "reviewers": count(REVIEWERS),
                "wanted": count(WANTED)}
