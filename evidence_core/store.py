"""Evidence stores (S3, "Evidence stores"): a directory ``evidence/`` in a git repository.

* ``evidence/store.json`` describes the store: the library its records are about, and where the S2
  datasets of the library's commits are;
* records are the lines of every ``*.jsonl`` file under ``evidence/``, and the store is the set of
  them by ``id``;
* the store is append-only: a record, once written, is never changed or removed.

``Store.add`` appends records, one file per month (``records/2026-09.jsonl``); ``check`` verifies what
a change to a store did: every record valid, nothing changed or removed, and each new record's
identity the one allowed to write it.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import records as rec

STORE_SPEC = "ltb-evidence-store/0"
CONFIG = "store.json"


class StoreError(ValueError):
    pass


def default_config(repo: str, root: str, datasets_repo: str | None = None) -> dict:
    return {"spec": STORE_SPEC, "library": {"repo": repo, "root": root},
            "datasets": {"repo": datasets_repo or repo, "tag": "dataset-{commit12}",
                         "asset": "dataset.tar.gz"},
            "claims": [], "maintainers": []}


def parse_records(text: str, where: str) -> list[dict]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise StoreError(f"{where}:{n}: {e}") from e
    return out


def merge(chunks: list[tuple[str, list[dict]]]) -> tuple[list[dict], list[str]]:
    """The set of records of several files, by id, in file order; and the conflicts: two different
    records with one id."""
    seen: dict[str, tuple[str, str]] = {}
    out, conflicts = [], []
    for where, records in chunks:
        for r in records:
            rid = r.get("id")
            if not rid:
                conflicts.append(f"{where}: a record without an id")
                continue
            text = rec.canonical(r)
            if rid in seen:
                if seen[rid][1] != text:
                    conflicts.append(f"{where}: record {rid} differs from the one in {seen[rid][0]}")
                continue
            seen[rid] = (where, text)
            out.append(r)
    return out, conflicts


@dataclass
class Store:
    root: Path
    config: dict
    records: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, root: str | Path) -> "Store":
        root = Path(root)
        cfg_path = root / CONFIG
        if not cfg_path.exists():
            raise StoreError(f"{root}: no {CONFIG}: not an evidence store")
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
        if config.get("spec") != STORE_SPEC:
            raise StoreError(f"{cfg_path}: spec {config.get('spec')!r}, expected {STORE_SPEC!r}")
        chunks = [(str(p.relative_to(root)), parse_records(p.read_text(encoding="utf-8"), str(p)))
                  for p in sorted(root.rglob("*.jsonl"))]
        records, conflicts = merge(chunks)
        if conflicts:
            raise StoreError("; ".join(conflicts))
        return cls(root=root, config=config, records=records)

    @classmethod
    def init(cls, root: str | Path, config: dict) -> "Store":
        root = Path(root)
        (root / "records").mkdir(parents=True, exist_ok=True)
        (root / CONFIG).write_text(json.dumps(config, indent=1) + "\n", encoding="utf-8")
        keep = root / "records" / ".gitkeep"
        if not any((root / "records").iterdir()):
            keep.write_text("")
        return cls.load(root)

    @property
    def ids(self) -> set[str]:
        return {r["id"] for r in self.records}

    def dataset_tag(self, commit: str) -> str:
        return self.config["datasets"]["tag"].replace("{commit12}", commit[:12]).replace("{commit}", commit)

    def find(self, pred) -> list[dict]:
        return [r for r in self.records if pred(r)]

    def from_origin(self, ref: str) -> list[dict]:
        """The records made from one issue or comment (``origin.ref``)."""
        return [r for r in self.records if (r.get("origin") or {}).get("ref") == ref]

    def add(self, records: list[dict]) -> list[dict]:
        """Validates records, sets their ids, and appends those the store does not have yet, each to
        the file of its month. Returns the records added."""
        added = []
        for r in records:
            r = rec.with_id(r)
            errs = rec.validate(r)
            if errs:
                raise StoreError(f"invalid record: {'; '.join(errs)}")
            if r["id"] in self.ids:
                continue
            month = (r.get("at") or "0000-00")[:7]
            path = self.root / "records" / f"{month}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            self.records.append(r)
            added.append(r)
        return added


def check(before: list[dict], after: list[dict], author: str | None = None,
          may_write=None) -> list[str]:
    """What is wrong with a change of a store from ``before`` to ``after``: invalid records, records
    changed or removed, and new records whose identity is not the writer's.

    ``author`` is the GitHub login that made the change (a pull request's author): every new record
    must name it as ``by.identity``. ``may_write(record)``, when given, decides instead (an intake
    bot writes records for the accounts whose issues and comments it read)."""
    errs = []
    old = {r.get("id"): rec.canonical(r) for r in before}
    new = {r.get("id"): r for r in after}
    for rid, text in old.items():
        if rid not in new:
            errs.append(f"record {rid} was removed")
        elif rec.canonical(new[rid]) != text:
            errs.append(f"record {rid} was changed")
    for rid, r in new.items():
        if rid in old:
            continue
        for e in rec.validate(r):
            errs.append(f"record {rid}: {e}")
        if may_write is not None:
            if not may_write(r):
                errs.append(f"record {rid}: not allowed for this writer")
        elif author is not None:
            login = ((r.get("by") or {}).get("identity") or {}).get("id")
            if login != author:
                errs.append(f"record {rid}: by {login or 'no GitHub account'}, but the change is by {author}")
    return errs


def records_at(repo: str | Path, ref: str, store_dir: str = "evidence") -> list[dict]:
    """The records of the store ``store_dir`` at git revision ``ref`` of the repository ``repo``."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                              text=True).stdout
    try:
        listing = git("ls-tree", "-r", "--name-only", ref, "--", store_dir)
    except subprocess.CalledProcessError:
        return []
    chunks = []
    for path in sorted(p for p in listing.splitlines() if p.endswith(".jsonl")):
        chunks.append((f"{ref}:{path}", parse_records(git("show", f"{ref}:{path}"), f"{ref}:{path}")))
    records, conflicts = merge(chunks)
    if conflicts:
        raise StoreError("; ".join(conflicts))
    return records
