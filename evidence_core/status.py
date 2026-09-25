"""Whether a record still applies to the current version of its subject.

A record stores the S1 key of its subject at the time it was made. Against a dataset of the current
code, it is:

* ``current``: the subject exists under the same name, with the same meaning hash;
* ``renamed``: no declaration has the name any more, but exactly one has the same meaning hash
  (and kind): the record follows it;
* ``stale-underneath``: same name, different meaning hash, same local hash — the declaration is
  written the same, but something it rests on changed;
* ``stale``: same name, different meaning and local hashes — the declaration itself changed;
* ``unavailable``: nothing has the name, and the subject's module did not build at the dataset's
  commit (``library.unavailable``): the record cannot be checked against this dataset;
* ``orphaned``: nothing has the name or the meaning hash any more;
* ``incomparable``: the record's hashes come from a different hasher than the dataset's;
* ``unknown``: the record has no meaning hash to compare (for example, migrated from a tool that
  did not record one).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import Dataset, Decl

CURRENT = "current"
RENAMED = "renamed"
STALE_UNDERNEATH = "stale-underneath"
STALE = "stale"
UNAVAILABLE = "unavailable"
ORPHANED = "orphaned"
INCOMPARABLE = "incomparable"
UNKNOWN = "unknown"

#: Statuses under which a record still applies to the subject as it is now.
APPLIES = (CURRENT, RENAMED)


@dataclass
class Status:
    state: str
    #: The node the record applies to now, if any (the renamed declaration for ``renamed``).
    decl: Decl | None = None
    #: For ``stale-underneath`` with an old dataset: the dependencies that were rewritten.
    changed: list[str] = field(default_factory=list)
    #: Set when the record did not name its hasher revision and it was assumed to match.
    assumed_hasher: bool = False

    @property
    def applies(self) -> bool:
        return self.state in APPLIES


def hasher_compatible(subject: dict, dataset: Dataset) -> tuple[bool, bool]:
    """(compatible, assumed): whether the record's hashes can be compared with the dataset's."""
    rec = subject.get("hasher") or {}
    ds = dataset.hasher
    if rec.get("name") and ds.get("name") and rec["name"] != ds["name"]:
        return False, False
    rev, ds_rev = rec.get("revision"), ds.get("revision")
    if rev and ds_rev and rev != ds_rev:
        return False, False
    return True, not rev


def classify(subject: dict, dataset: Dataset, old: Dataset | None = None) -> Status:
    """The status of a record whose subject is ``subject`` against ``dataset``.

    ``old``, when given, is a dataset of the commit the record was made at; it lets a
    ``stale-underneath`` status name the dependencies that were rewritten.
    """
    hashes = subject.get("hashes") or {}
    meaning, local = hashes.get("meaning"), hashes.get("local")
    name = subject.get("name", "")
    current = dataset.by_name.get(name)
    if not meaning:
        return Status(UNKNOWN, decl=current)
    ok, assumed = hasher_compatible(subject, dataset)
    if not ok:
        return Status(INCOMPARABLE, decl=current)
    if current is not None:
        if current.meaning == meaning:
            return Status(CURRENT, decl=current, assumed_hasher=assumed)
        if local and current.local == local:
            return Status(STALE_UNDERNEATH, decl=current,
                          changed=changed_underneath(name, dataset, old) if old else [],
                          assumed_hasher=assumed)
        return Status(STALE, decl=current, assumed_hasher=assumed)
    if subject.get("module") in dataset.unavailable:
        return Status(UNAVAILABLE, assumed_hasher=assumed)
    candidates = dataset.by_meaning.get(meaning, [])
    kind = subject.get("kind")
    if kind:
        same_kind = [d for d in candidates if subject_kind_of(d) == kind]
        candidates = same_kind or candidates
    if len(candidates) == 1:
        return Status(RENAMED, decl=candidates[0], assumed_hasher=assumed)
    return Status(ORPHANED, assumed_hasher=assumed)


def subject_kind_of(decl: Decl) -> str:
    if decl.kind == "instance":
        return "instance"
    return "statement" if decl.is_prop else "definition"


def changed_underneath(name: str, new: Dataset, old: Dataset, notion: str = "meaning") -> list[str]:
    """The declarations in ``name``'s old closure that were rewritten (local hash changed) or
    removed between ``old`` and ``new``: the causes of a ``stale-underneath`` status."""
    out = []
    for d in old.closure(name, notion, include_self=False):
        now = new.by_name.get(d.name)
        if now is None:
            out.append(d.name)
        elif d.local and now.local != d.local:
            out.append(d.name)
    return out
