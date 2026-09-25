"""S3 evidence records (spec ``ltb-evidence/0``).

Records are JSON objects, stored one per line (JSONL), append-only. Every record has:

* ``schema``: ``"ltb-evidence/0"``;
* ``kind``: ``review``, ``status``, ``test``, or ``named`` in this version;
* ``id``: the first 16 hex digits of the SHA-256 of the record's canonical form, which excludes
  ``id`` and ``signature``;
* ``subject`` (all kinds but ``status``): the S1 key of the declaration the record is about;
* ``by``: who made it; ``at``: when (RFC 3339, UTC); ``origin``: where it came from.

The canonical form is the JSON encoding with sorted keys, no whitespace, and non-ASCII characters
written as themselves (as trust's canonical claims are).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

SCHEMA = "ltb-evidence/0"
KINDS = ("review", "status", "test", "named")
VERDICTS = ("accept", "problem", "question")
SUBJECT_KINDS = ("definition", "statement", "instance", "link", "text")
PROBLEM_CATEGORIES = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "naming", "other")
CHECK_STATES = ("checked", "unchecked", "na")
STATES = ("fixed", "intended", "invalid", "reopened", "withdrawn")
REVIEWER_KINDS = ("person", "agent")
IDENTITY_KINDS = ("none", "github", "key")
INVOLVEMENT = ("author", "contributor", "outsider", "unknown")


def canonical(record: dict) -> str:
    """The canonical JSON text of a record: sorted keys, no whitespace, without id and signature."""
    body = {k: v for k, v in record.items() if k not in ("id", "signature")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_id(record: dict) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()[:16]


def with_id(record: dict) -> dict:
    """The record with its ``id`` set (or checked, if already present)."""
    out = dict(record)
    out["id"] = record_id(out)
    return out


def subject_from_decl(decl, dataset, subject_kind: str | None = None) -> dict:
    """The S1 key of a dataset node, as a record's ``subject``."""
    if subject_kind is None:
        subject_kind = {"instance": "instance"}.get(decl.kind,
                                                   "statement" if decl.is_prop else "definition")
    hashes = {k: v for k, v in (("meaning", decl.meaning), ("local", decl.local),
                                ("content", decl.content)) if v}
    return {
        "name": decl.name, "module": decl.module, "package": decl.package,
        "commit": dataset.commit, "toolchain": dataset.toolchain,
        "hasher": {k: dataset.hasher.get(k) for k in ("name", "revision", "local")},
        "hashes": hashes, "kind": subject_kind,
    }


def validate(record: dict) -> list[str]:
    """Problems with a record, as messages. Empty when the record is valid."""
    errs: list[str] = []

    def need(key: str, where: dict = record, path: str = "") -> bool:
        if key not in where or where[key] in (None, ""):
            errs.append(f"missing {path}{key}")
            return False
        return True

    if record.get("schema") != SCHEMA:
        errs.append(f"schema is {record.get('schema')!r}, expected {SCHEMA!r}")
    kind = record.get("kind")
    if kind not in KINDS:
        errs.append(f"unknown kind {kind!r}")
    if "id" in record and record["id"] != record_id(record):
        errs.append("id does not match the canonical form")
    need("at")
    if need("by"):
        by = record["by"]
        if by.get("kind") not in REVIEWER_KINDS:
            errs.append(f"by.kind must be one of {REVIEWER_KINDS}")
        ident = by.get("identity", {})
        if ident.get("kind") not in IDENTITY_KINDS:
            errs.append(f"by.identity.kind must be one of {IDENTITY_KINDS}")
        if by.get("kind") == "agent" and not by.get("agent"):
            errs.append("by.agent is required for an agent")
        if by.get("involvement", "unknown") not in INVOLVEMENT:
            errs.append(f"by.involvement must be one of {INVOLVEMENT}")
    if kind != "status":
        if need("subject"):
            s = record["subject"]
            for k in ("name", "commit"):
                need(k, s, "subject.")
            if s.get("kind", "definition") not in SUBJECT_KINDS:
                errs.append(f"subject.kind must be one of {SUBJECT_KINDS}")
    if kind == "review":
        verdict = record.get("verdict")
        if verdict not in VERDICTS:
            errs.append(f"verdict must be one of {VERDICTS}")
        if verdict == "problem":
            if record.get("problem", {}).get("category") not in PROBLEM_CATEGORIES:
                errs.append(f"problem.category must be one of {PROBLEM_CATEGORIES}")
            if not record.get("rationale"):
                errs.append("a problem needs a rationale")
        if record.get("by", {}).get("kind") == "agent" and not record.get("rationale"):
            errs.append("a review by an agent needs a rationale")
        for f, state in (record.get("checked") or {}).items():
            if state not in CHECK_STATES:
                errs.append(f"checked.{f} must be one of {CHECK_STATES}")
        for c in record.get("caveats") or []:
            if c.get("category") not in PROBLEM_CATEGORIES:
                errs.append(f"caveat category {c.get('category')!r} is unknown")
    elif kind == "status":
        need("target")
        if record.get("state") not in STATES:
            errs.append(f"state must be one of {STATES}")
    elif kind == "test":
        if need("test"):
            need("name", record["test"], "test.")
    elif kind == "named":
        need("name")
    return errs


def load(path: str | Path) -> list[dict]:
    """The records of a JSONL file, in file order."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}:{n}: {e}") from e
    return out


def append(path: str | Path, records: Iterable[dict]) -> list[dict]:
    """Appends records (with ids set) to a JSONL file; returns them. Refuses invalid records."""
    written = []
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            r = with_id(r)
            errs = validate(r)
            if errs:
                raise ValueError(f"invalid record: {'; '.join(errs)}")
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            written.append(r)
    return written
