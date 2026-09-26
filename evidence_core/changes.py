"""What changed since a baseline dataset, in the classes a returning reader needs.

Each declaration of the baseline is classified as a review made at the baseline would be (S3
statuses, from evidence-core), then refined:

* **statement changed** — the declaration itself was rewritten (its local hash moved) and what it
  states reads differently;
* **body changed** — a definition rewritten while its statement reads the same: its meaning moved
  through its body;
* **meaning changed underneath** — written the same, but something its statement rests on changed
  (the rewritten dependencies are named);
* **proof only** — the meaning is the same and only a proof somewhere in its closure changed: no
  re-reading follows;
* **renamed** — gone under its old name, present under a new one with the same meaning;
* **added**, **removed**.

"Reads differently" compares the `statement` facet's text (binders and conclusion, pretty-printed
by Lean), so a statement whose `variable`s changed counts as changed even though its own source
lines did not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import Dataset
from .status import classify
from . import records as evrec

ORDER = ["statement", "body", "underneath", "added", "renamed", "removed", "proof"]


@dataclass
class Changes:
    status: dict[str, str] = field(default_factory=dict)      # new name → class
    detail: dict[str, dict] = field(default_factory=dict)     # new name → {class, was, causes}
    summary: dict = field(default_factory=dict)


def _statement_text(ds: Dataset, name: str) -> str | None:
    row = ds.facet_row("statement", name)
    if not row:
        return None
    return "\n".join(f"{b.get('role')} {b.get('name')} : {b.get('type')}" for b in row.get("binders", [])) + \
        "\n⊢ " + row.get("conclusion", "")


def compare(new: Dataset, old: Dataset, scope_names: set[str] | None = None) -> Changes:
    ch = Changes()
    in_scope = (lambda n: True) if scope_names is None else (lambda n: n in scope_names)
    lists: dict[str, list] = {k: [] for k in ORDER}
    followed: set[str] = set()
    for d in old.decls:
        if not d.is_project:
            continue
        s = classify(evrec.subject_from_decl(d, old), new, old=old)
        now = s.decl.name if s.decl else None
        if s.state == "orphaned" or now is None:
            lists["removed"].append(d.name)
            continue
        followed.add(now)
        if not in_scope(now):
            continue
        cls, extra = None, {}
        if s.state == "current":
            if new.by_name[now].content != d.content:
                cls = "proof"
        elif s.state == "renamed":
            cls, extra = "renamed", {"was": d.name}
        elif s.state == "stale-underneath":
            cls, extra = "underneath", {"causes": s.changed}
        elif s.state == "stale":
            same_text = _statement_text(old, d.name) == _statement_text(new, now) and \
                _statement_text(new, now) is not None
            cls = "body" if (same_text and not d.is_prop) else "statement"
        if cls:
            ch.status[now] = cls
            ch.detail[now] = {"class": cls, **extra}
            lists[cls].append(now)
    for d in new.decls:
        if d.is_project and d.name not in followed and d.name not in old.by_name and in_scope(d.name):
            ch.status[d.name] = "added"
            ch.detail[d.name] = {"class": "added"}
            lists["added"].append(d.name)
    ch.summary = {
        "baseline": {"commit": old.commit, "producer": old.producer(), "toolchain": old.toolchain,
                     "decls": sum(1 for d in old.decls if d.is_project)},
        "current": {"commit": new.commit, "decls": sum(1 for d in new.decls if d.is_project)},
        "counts": {k: len(v) for k, v in lists.items()},
        "lists": {k: sorted(v) for k, v in lists.items()},
        # The same hasher, or a baseline of ltb-dataset/0 against a dataset that carries its hashes
        # as `legacy`, which `classify` then compares with.
        "comparable": any(all(old.hasher.get(k) == h.get(k) for k in ("name", "revision", "local"))
                          for h in (new.hasher, new.legacy_hasher)),
    }
    return ch
