"""Provenance: when each declaration's meaning last changed, across the builds a site has seen.

A dataset knows one commit; history needs a record kept from build to build. The ledger is that
record, one JSON file a deployment carries from one build to the next (committed, or kept as a
release asset):

    {"format": "trust-site-ledger/1",
     "builds": [{"commit": "…", "date": "2026-09-22", "label": "v4.35.0-rc2-1-g61e506b"}, …],
     "decls": {"Bandits.etcAlgorithm": [[0, "<meaning hash>"], [3, "<meaning hash>"]], …}}

Each declaration lists the builds at which its meaning hash was new: when it first appeared, and
each change after that. Nothing else is recorded, so a build that changes nothing adds only its
line to `builds`. A declaration renamed with the same meaning keeps its history (it is found by
meaning hash). A history recorded before the datasets moved to `ltb-dataset/1` holds the old kind of
meaning hash, which those datasets still carry (`legacy`): it is carried over, not counted as a
change.

`trust-site ledger --ledger FILE --dataset DIR` records a build; `build --ledger FILE` reads it:
each declaration's page then says when its meaning last changed, and Changes lets a reader pick
the build they last worked through.
"""
from __future__ import annotations

import json
from pathlib import Path

from .dataset import Dataset

FORMAT = "trust-site-ledger/1"


def load(path: Path | None) -> dict:
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") == FORMAT:
            return data
    return {"format": FORMAT, "builds": [], "decls": {}}


def record(ledger: dict, ds: Dataset, date: str = "", label: str = "") -> bool:
    """Adds the build of `ds` to `ledger`. False when that commit is already the last build."""
    builds = ledger["builds"]
    if builds and builds[-1]["commit"] == ds.commit:
        return False
    k = len(builds)
    builds.append({"commit": ds.commit, "date": date, "label": label or ds.commit[:12]})
    decls = ledger["decls"]
    # A declaration renamed since the last build carries its history over, found by meaning hash.
    last_by_meaning = {hist[-1][1]: name for name, hist in decls.items() if hist}
    for d in ds.decls:
        if not d.is_project or not d.meaning:
            continue
        hist = decls.get(d.name)
        if hist is None:
            old = last_by_meaning.get(d.meaning) or last_by_meaning.get(d.legacy_meaning or "")
            if old and old not in ds.by_name:
                decls[d.name] = decls.pop(old)
                if decls[d.name][-1][1] != d.meaning:
                    decls[d.name][-1][1] = d.meaning
                continue
            decls[d.name] = [[k, d.meaning]]
        elif hist[-1][1] == d.legacy_meaning:
            # Recorded before the datasets moved to ltb-dataset/1, and unchanged since by the old
            # hash: the same meaning, now under the new hash.
            hist[-1][1] = d.meaning
        elif hist[-1][1] != d.meaning:
            hist.append([k, d.meaning])
    return True


def previous(ledger: dict, commit: str) -> str | None:
    """The commit of the last build recorded before ``commit``: the baseline a new build of it
    compares against."""
    commits = [b["commit"] for b in ledger["builds"] if b["commit"] != commit]
    return commits[-1] if commits else None


def save(ledger: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(ledger, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
