"""Migrating existing review data to S3 records.

Three sources:

* **Reviewed-by** ledgers (``reviewed-by/v1``, ``tests/v1``, ``named/v1``, problem events);
* **Referee** audit exports (``{"version": 1, "verdicts": {name: {verdict, note, at, meaning}}}``);
* **trust** marks files (``trust-marks.json``: trusted marks).

Each migrated record gets the S1 key of its subject from a dataset of the commit the original was
made at, when one is supplied; otherwise from the closest dataset available, with
``migration.hashes_from`` saying which commit the hashes were taken at. A source that recorded no
commit gets hashes from the given dataset, flagged the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import Dataset
from .records import subject_from_decl, with_id


@dataclass
class Report:
    migrated: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _subject(name: str, commit: str, datasets: dict[str, Dataset], fallback: Dataset,
             subject_kind: str | None = None) -> tuple[dict | None, dict]:
    ds = datasets.get(commit, fallback)
    decl = ds.by_name.get(name)
    if decl is None:
        return None, {}
    subject = subject_from_decl(decl, ds, subject_kind)
    note = {}
    if commit and ds.commit != commit:
        # The hashes describe the declaration at `ds.commit`, not at the commit of the original
        # record; say so rather than pretending otherwise.
        note = {"hashes_from": ds.commit}
    if commit:
        subject["commit"] = commit
    return subject, note


def _github_by(login: str, kind: str, agent: str) -> dict:
    by = {"kind": "agent" if kind == "agent" else "person",
          "identity": {"kind": "github", "id": login}, "involvement": "unknown"}
    if kind == "agent":
        by["agent"] = agent or "unknown agent"
    return by


def from_reviewed_by(records: list[dict], tests: list[dict], named: list[dict],
                     problems: list[dict], repo: str, datasets: dict[str, Dataset],
                     fallback: Dataset) -> Report:
    """Converts Reviewed-by's ledgers. ``repo`` is the ledger's repository (for origins)."""
    rep = Report()
    for r in records:
        if r.get("schema") != "reviewed-by/v1":
            rep.skipped.append(f"record with schema {r.get('schema')!r}")
            continue
        subject, note = _subject(r["decl"], r.get("tauceti", ""), datasets, fallback)
        if subject is None:
            rep.skipped.append(f"review of {r['decl']}: not in any dataset")
            continue
        out = {"schema": "ltb-evidence/0", "kind": "review", "subject": subject,
               "verdict": "accept", "rationale": r.get("evidence", ""),
               "by": _github_by(r.get("by", ""), r.get("kind", "person"), r.get("agent", "")),
               "at": r.get("at", ""),
               "origin": {"kind": "issue", "ref": f"{repo}#{r.get('source', {}).get('issue')}"},
               "migration": {"from": "reviewed-by/v1", "text_hash": r.get("hash"), **note}}
        rep.migrated.append(with_id(out))
    for t in tests:
        subject, note = _subject(t["decl"], t.get("tauceti", ""), datasets, fallback)
        test_subject, _ = _subject(t["test"], t.get("tauceti", ""), datasets, fallback)
        if subject is None:
            rep.skipped.append(f"test of {t['decl']}: not in any dataset")
            continue
        src = t.get("source", {})
        ref = f"{repo}#{src.get('issue')}" + (f" comment {src['comment']}" if "comment" in src else "")
        out = {"schema": "ltb-evidence/0", "kind": "test", "subject": subject,
               "test": test_subject or {"name": t["test"]}, "checks": t.get("checks", ""),
               "by": _github_by(t.get("by", ""), t.get("kind", "person"), t.get("agent", "")),
               "at": t.get("at", ""), "origin": {"kind": "comment", "ref": ref},
               "migration": {"from": "tests/v1", **note}}
        rep.migrated.append(with_id(out))
    for n in named:
        subject, note = _subject(n["decl"], "", datasets, fallback)
        if subject is None:
            rep.skipped.append(f"named {n['decl']}: not in the dataset")
            continue
        by_agent = n.get("kind") == "agent"
        out = {"schema": "ltb-evidence/0", "kind": "named", "subject": subject,
               "name": n.get("name", ""), "what": n.get("what", ""), "about": n.get("about", ""),
               "source": n.get("source", {}),
               "by": {"kind": "agent" if by_agent else "person",
                      "identity": {"kind": "none"}, "involvement": "unknown",
                      **({"agent": n.get("agent") or n.get("by", "agent")} if by_agent else {})},
               "at": n.get("at", ""), "origin": {"kind": "migration", "ref": repo},
               "migration": {"from": "named/v1", **note}}
        rep.migrated.append(with_id(out))
    # Problem events (schema problem/v1): "reported" becomes a problem review; "closed" and
    # "reopened" become statuses of it. "updated" edits are not carried over: the review keeps the
    # report as first made.
    reports: dict[int, dict] = {}
    for p in sorted(problems, key=lambda p: p.get("at", "")):
        issue = p.get("issue")
        event = p.get("event")
        if event == "reported":
            subject, note = _subject(p["decl"], p.get("tauceti", ""), datasets, fallback)
            if subject is None:
                rep.skipped.append(f"problem on {p.get('decl')}: not in any dataset")
                continue
            category = {"wrong": "F1", "misleading": "naming"}.get(p.get("what", ""), "other")
            rationale = p.get("why", "")
            if p.get("fix"):
                rationale += f"\n\nSuggested fix: {p['fix']}"
            out = {"schema": "ltb-evidence/0", "kind": "review", "subject": subject,
                   "verdict": "problem", "problem": {"category": category},
                   "rationale": rationale or "(no rationale recorded)",
                   "by": _github_by(p.get("by", ""), p.get("kind", "person"), p.get("agent", "")),
                   "at": p.get("at", ""), "origin": {"kind": "issue", "ref": f"{repo}#{issue}"},
                   "migration": {"from": "problem/v1", "text_hash": p.get("hash"), **note}}
            out = with_id(out)
            reports[issue] = out
            rep.migrated.append(out)
        elif event in ("closed", "reopened"):
            target = reports.get(issue)
            if target is None:
                rep.skipped.append(f"{event} event for #{issue}: no report recorded")
                continue
            state = "reopened" if event == "reopened" else \
                {"fixed": "fixed", "not planned": "invalid", "duplicate": "invalid"}.get(
                    p.get("resolution", ""), "invalid")
            out = {"schema": "ltb-evidence/0", "kind": "status", "target": target["id"],
                   "state": state, "by": _github_by(p.get("by", ""), "person", ""),
                   "at": p.get("at", ""), "origin": {"kind": "issue", "ref": f"{repo}#{issue}"}}
            rep.migrated.append(with_id(out))
    return rep


def from_referee_audit(audit: dict, dataset: Dataset, reviewer: str = "") -> Report:
    """Converts a Referee audit export. Referee records no commit and no reviewer identity; the
    subject's hashes come from ``dataset``, and the verdict's own ``meaning`` hash is kept as the
    meaning hash when present (Referee stamps verdicts with the proof-irrelevant semantic hash,
    from a semantic_hash revision it does not record)."""
    rep = Report()
    for name, v in sorted(audit.get("verdicts", {}).items()):
        verdict = {"accepted": "accept", "query": "question"}.get(v.get("verdict"))
        if verdict is None:
            continue
        decl = dataset.by_name.get(name)
        if decl is None:
            rep.skipped.append(f"verdict on {name}: not in the dataset")
            continue
        subject = subject_from_decl(decl, dataset)
        if v.get("meaning"):
            subject["hashes"] = {"meaning": v["meaning"]}
            subject["hasher"] = {"name": "semantic_hash", "revision": None,
                                 "local": None}
        out = {"schema": "ltb-evidence/0", "kind": "review", "subject": subject,
               "verdict": verdict, "rationale": v.get("note", ""),
               "by": {"kind": "person", "involvement": "unknown",
                      "identity": ({"kind": "github", "id": reviewer} if reviewer
                                   else {"kind": "none"})},
               "at": v.get("at", ""), "origin": {"kind": "migration",
                                                 "ref": f"referee audit {audit.get('dataId', '')}"},
               "migration": {"from": "referee-audit/1", "hashes_from": dataset.commit}}
        rep.migrated.append(with_id(out))
    return rep


def from_trust_marks(marks: dict, datasets: dict[str, Dataset], fallback: Dataset,
                     reviewer: str = "") -> Report:
    """Converts trust's trusted marks. Characterizations and protected declarations have no S3
    kind in this version and are reported as skipped."""
    rep = Report()
    for m in marks.get("trusted", []):
        subject, note = _subject(m["name"], m.get("commit", ""), datasets, fallback)
        if subject is None:
            rep.skipped.append(f"trusted {m['name']}: not in any dataset")
            continue
        out = {"schema": "ltb-evidence/0", "kind": "review", "subject": subject,
               "verdict": "accept", "rationale": m.get("note", ""),
               "by": {"kind": "person", "involvement": "unknown",
                      "identity": ({"kind": "github", "id": reviewer} if reviewer
                                   else {"kind": "none"})},
               "at": m.get("at", "") or "1970-01-01T00:00:00Z",
               "origin": {"kind": "migration", "ref": "trust-marks.json"},
               "migration": {"from": "trust-marks", **note}}
        rep.migrated.append(with_id(out))
    for c in marks.get("characterizations", []):
        rep.skipped.append(f"characterization of {c.get('definition')}: no S3 kind yet")
    for p in marks.get("protected", []):
        rep.skipped.append(f"protected {p.get('name')}: not evidence")
    return rep
