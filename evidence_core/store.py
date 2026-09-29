"""Evidence stores (S3, "Evidence stores"): a directory ``evidence/`` in a git repository.

* ``evidence/store.json`` describes the store: the library its records are about, where the S2
  datasets of the library's commits are, and the rubric its forms ask for (``ltb-rubric/1`` unless
  it gives another);
* records are the lines of every ``*.jsonl`` file under ``evidence/``, and the store is the set of
  them by ``id``;
* the store is append-only: a record, once written, is never changed or removed;
* ``imports`` names other stores whose records a view of this one shows beside its own
  (S3, "Imported records"): ``with_imports`` reads them, once fetched (``evidence-store
  fetch-imports``).

``Store.add`` appends records, one file per month (``records/2026-09.jsonl``); ``check`` verifies what
a change to a store did: every record valid, nothing changed or removed, and each new record's
identity the one allowed to write it.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import records as rec
from . import rubric as rb

STORE_SPEC = "ltb-evidence-store/0"
CONFIG = "store.json"
#: In a directory of fetched imports: which commit each imported store was read at.
IMPORTS_MANIFEST = "imports.json"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class StoreError(ValueError):
    pass


def default_config(repo: str, root: str, datasets_repo: str | None = None) -> dict:
    return {"spec": STORE_SPEC, "library": {"repo": repo, "root": root},
            "datasets": {"repo": datasets_repo or repo, "tag": "dataset-{commit12}",
                         "asset": "dataset.tar.gz"},
            "claims": [], "maintainers": []}


def parse_records(text: str, where: str) -> list[dict]:
    out = []
    # JSON lines are separated by "\n" only: `splitlines` would also split inside strings, at
    # Unicode line separators.
    for n, line in enumerate(text.split("\n"), 1):
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
        if config.get("rubric") is not None and rb.errors(config["rubric"]):
            raise StoreError(f"{cfg_path}: rubric: {'; '.join(rb.errors(config['rubric']))}")
        if import_errors(config):
            raise StoreError(f"{cfg_path}: imports: {'; '.join(import_errors(config))}")
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
    def imports(self) -> list[dict]:
        """The stores this one imports, defaults filled in (``import_specs``)."""
        return import_specs(self.config)

    @property
    def rubric(self) -> rb.Rubric:
        """The rubric the store's forms ask for."""
        return rb.of_config(self.config)

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
            errs = rec.validate(r, {self.rubric.name: self.rubric})
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


def import_errors(config: dict) -> list[str]:
    """What is wrong with a store's ``imports``: a list of ``{repo, path, ref}``, ``repo`` as
    ``owner/name`` and the other two optional strings."""
    imports = config.get("imports")
    if imports is None:
        return []
    if not isinstance(imports, list):
        return ["a list of stores"]
    out = []
    for n, i in enumerate(imports):
        if not isinstance(i, dict) or not isinstance(i.get("repo"), str) or not REPO.match(i["repo"]):
            out.append(f"#{n}: `repo` must be owner/name")
        elif set(i) - {"repo", "path", "ref"}:
            out.append(f"#{n}: unknown fields {sorted(set(i) - {'repo', 'path', 'ref'})}")
        elif any(k in i and not isinstance(i[k], str) for k in ("path", "ref")):
            out.append(f"#{n}: `path` and `ref` are strings")
    return out


def import_specs(config: dict) -> list[dict]:
    """The stores ``config`` imports, each ``{repo, path, ref}``: ``path`` the store's directory in
    the repository (default ``evidence``), ``ref`` None for the repository's default branch."""
    return [{"repo": i["repo"], "path": i.get("path") or "evidence", "ref": i.get("ref")}
            for i in config.get("imports") or []]


@dataclass
class Imported:
    """A store's records with those of the stores it imports."""

    #: the store's own records, then each imported record it does not hold
    records: list[dict]
    #: the id of each imported record → the store it comes from (``owner/name``)
    sources: dict[str, str]
    #: each imported store as read: ``{repo, path, ref, commit, records}``, ``records`` the number
    #: of its records the store did not hold already
    read: list[dict]


def with_imports(store: Store, cache: str | Path) -> Imported:
    """``store``'s records and those of the stores it imports, fetched into ``cache`` by
    ``evidence-store fetch-imports``: each at ``<cache>/<owner>/<name>/<path>``, and
    ``<cache>/imports.json`` saying at which commit each was read. One level: the imported stores'
    own imports are not read."""
    cache = Path(cache)
    manifest = cache / IMPORTS_MANIFEST
    commits = {(m["repo"], m["path"]): m.get("commit")
               for m in (json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else [])}
    own = {r["id"] for r in store.records}
    records, sources, read = list(store.records), {}, []
    for spec in store.imports:
        where = cache / spec["repo"] / spec["path"]
        if not (where / CONFIG).exists():
            raise StoreError(f"{spec['repo']}: not fetched into {cache} (evidence-store fetch-imports)")
        n = 0
        for r in Store.load(where).records:
            if r["id"] not in own and r["id"] not in sources:
                sources[r["id"]] = spec["repo"]
                records.append(r)
                n += 1
        read.append({**spec, "commit": commits.get((spec["repo"], spec["path"])), "records": n})
    return Imported(records=records, sources=sources, read=read)


def check(before: list[dict], after: list[dict], author: str | None = None,
          may_write=None, rubrics: dict[str, rb.Rubric] | None = None) -> list[str]:
    """What is wrong with a change of a store from ``before`` to ``after``: invalid records, records
    changed or removed, and new records whose identity is not the writer's.

    ``author`` is the GitHub login that made the change (a pull request's author): every new record
    must name it as ``by.identity``. ``may_write(record)``, when given, decides instead (an intake
    bot writes records for the accounts whose issues and comments it read). ``rubrics`` are the
    rubrics, besides the standard one, whose axes new records are checked against."""
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
        for e in rec.validate(r, rubrics):
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
