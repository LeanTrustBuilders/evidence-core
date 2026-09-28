"""Whether a record still applies to the current version of its subject.

A record stores the S1 key of its subject at the time it was made. Against a dataset of the current
code, it is:

* ``current``: the subject exists under the same name, with the same meaning hash;
* ``renamed``: no declaration has the name any more, but exactly one has the same meaning hash
  (and aspect): the record follows it;
* ``stale-underneath``: same name, different meaning hash, same local hash — the declaration is
  written the same, but something it rests on changed;
* ``stale``: same name, different meaning and local hashes — the declaration itself changed;
* ``unavailable``: nothing has the name, and the subject's module did not build at the dataset's
  commit (``library.unavailable``): the record cannot be checked against this dataset;
* ``orphaned``: nothing has the name or the meaning hash any more;
* ``incomparable``: the record's hashes come from a different hasher than the dataset's (another
  ``hasher.meaning`` or ``hasher.local``);
* ``unknown``: the record has no meaning hash to compare (for example, migrated from a tool that
  did not record one).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import Dataset, Decl
from .records import aspect_of

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

    @property
    def applies(self) -> bool:
        return self.state in APPLIES


def hasher_compatible(subject: dict, dataset: Dataset) -> bool:
    """Whether the record's hashes can be compared with the dataset's: the same meaning and local
    hashers (S1)."""
    h, ds = subject.get("hasher") or {}, dataset.hasher
    return all(h.get(k) == ds.get(k) for k in ("meaning", "local"))


def classify(subject: dict, dataset: Dataset, old: Dataset | None = None) -> Status:
    """The status of a record whose subject is ``subject`` against ``dataset``.

    ``old``, when given, is a dataset of the commit the record was made at; it lets a
    ``stale-underneath`` status name the dependencies that were rewritten.

    A record keyed by another hasher is ``incomparable``.
    """
    hashes = subject.get("hashes") or {}
    meaning, local = hashes.get("meaning"), hashes.get("local")
    name = subject.get("name", "")
    current = dataset.by_name.get(name)
    if not meaning:
        return Status(UNKNOWN, decl=current)
    if not hasher_compatible(subject, dataset):
        return Status(INCOMPARABLE, decl=current)
    if current is not None:
        if current.meaning == meaning:
            return Status(CURRENT, decl=current)
        if local and current.local == local:
            return Status(STALE_UNDERNEATH, decl=current,
                          changed=changed_underneath(name, dataset, old) if old else [])
        return Status(STALE, decl=current)
    if subject.get("module") in dataset.unavailable:
        return Status(UNAVAILABLE)
    candidates = dataset.by_meaning.get(meaning, [])
    if len(candidates) > 1:
        candidates = [d for d in candidates if aspect_of(d) == subject.get("aspect")]
    if len(candidates) == 1:
        return Status(RENAMED, decl=candidates[0])
    return Status(ORPHANED)


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
