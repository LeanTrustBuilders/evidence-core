"""Rubrics (S3, "Rubrics"): the axes a review says it checked and a problem says is wrong.

A rubric's name carries its version (``ltb-rubric/1``). A record that names an axis says which rubric
in its ``rubric`` field. A store asks for a rubric in its ``store.json`` (``rubric``: ``{name, axes}``,
each axis ``{name, check, problem}``), and for ``ltb-rubric/1``, the one S3 suggests, when it names
none. ``other`` is not an axis: as a problem's category, it says what is wrong is on none of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AXIS_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
OTHER = "other"


@dataclass(frozen=True)
class Axis:
    name: str
    #: What a review that checked it says.
    check: str
    #: A problem on it, named.
    problem: str


@dataclass(frozen=True)
class Rubric:
    name: str
    axes: tuple[Axis, ...]

    @property
    def names(self) -> list[str]:
        return [a.name for a in self.axes]

    def axis(self, name: str) -> Axis | None:
        return next((a for a in self.axes if a.name == name), None)

    def to_json(self) -> dict:
        return {"name": self.name,
                "axes": [{"name": a.name, "check": a.check, "problem": a.problem} for a in self.axes]}

    @classmethod
    def from_json(cls, d: dict) -> "Rubric":
        errs = errors(d)
        if errs:
            raise ValueError(f"invalid rubric: {'; '.join(errs)}")
        return cls(d["name"], tuple(Axis(a["name"], a["check"], a["problem"]) for a in d["axes"]))


STANDARD = Rubric("ltb-rubric/1", (
    Axis("object", "it is the intended notion, or states the intended result, and not a different one",
         "a different object"),
    Axis("convention", "it follows the source's conventions: normalization, indexing, signs, and the "
         "instances it picks up", "a different convention"),
    Axis("edge-cases", "it decides degenerate and boundary inputs as the source does", "different edge cases"),
    Axis("junk", "no default value outside the intended domain changes what it means", "a junk value"),
    Axis("vacuous", "it is neither vacuous nor trivial", "vacuous or trivial"),
    Axis("choice", "it makes no arbitrary choice where the intended object is canonical", "an arbitrary choice"),
    Axis("generality", "it is as general as the source", "less general than the source"),
    Axis("naming", "its name and docstring do not mislead", "a misleading name or docstring"),
))

KNOWN = {STANDARD.name: STANDARD}


def errors(d) -> list[str]:
    """What is wrong with a rubric as ``store.json`` gives it."""
    if not isinstance(d, dict):
        return ["a rubric is an object {name, axes}"]
    errs = []
    if not isinstance(d.get("name"), str) or not d["name"]:
        errs.append("a rubric needs a name")
    axes = d.get("axes")
    if not isinstance(axes, list) or not axes:
        return errs + ["a rubric needs axes"]
    seen = set()
    for a in axes:
        n = a.get("name") if isinstance(a, dict) else None
        if not isinstance(n, str) or not AXIS_NAME.match(n) or n == OTHER:
            errs.append(f"axis name {n!r}: lowercase letters, digits and hyphens, not {OTHER!r}")
        elif n in seen:
            errs.append(f"axis {n!r} twice")
        seen.add(n)
        for k in ("check", "problem"):
            if not isinstance(a, dict) or not isinstance(a.get(k), str) or not a[k]:
                errs.append(f"axis {n!r}: missing {k}")
    return errs


def of_config(config: dict) -> Rubric:
    """The rubric a store asks for (its ``store.json``)."""
    d = (config or {}).get("rubric")
    return Rubric.from_json(d) if d else STANDARD


def known(*rubrics: Rubric) -> dict[str, Rubric]:
    """The rubrics by name: the standard one, and those given."""
    return {**KNOWN, **{r.name: r for r in rubrics}}


def problem_named(rubrics: dict[str, Rubric], rubric: str | None, category: str | None) -> str:
    """What a problem says is wrong, in words: its axis's ``problem`` when its rubric is known, and
    its name otherwise."""
    if not category or category == OTHER:
        return "something else"
    axis = rubrics[rubric].axis(category) if rubric in rubrics else None
    return axis.problem if axis else category
