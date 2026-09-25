"""Command line: ``python -m evidence_core <command> …``.

Commands:

* ``status``    the status of every record against a dataset;
* ``coverage``  the coverage of claims under a policy;
* ``queue``     unreviewed declarations in claims' closures, ranked;
* ``claims``    the declarations annotated ``@[claim]`` in a dataset;
* ``diff``      how every project declaration moved between two datasets;
* ``validate``  check an S3 file;
* ``migrate``   convert Reviewed-by ledgers, a Referee audit export, or trust marks to S3.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .coverage import Evidence, Policy, coverage as coverage_of, queue as queue_of
from . import migrate as mig
from . import records as rec
from . import status as st
from .dataset import Dataset


def _old_datasets(specs: list[str]) -> dict[str, Dataset]:
    """Parses ``--at COMMIT=DIR`` options (or plain ``DIR``, whose commit is read from it)."""
    out = {}
    for spec in specs or []:
        if "=" in spec:
            commit, path = spec.split("=", 1)
            out[commit] = Dataset.load(path)
        else:
            ds = Dataset.load(spec)
            out[ds.commit] = ds
    return out


def _policy(args) -> Policy:
    return Policy(agents=args.agents, stale_underneath=args.stale_underneath,
                      caveats=not args.no_caveats, authors=not args.no_authors,
                      upstream=args.upstream)


def cmd_status(args) -> int:
    ds = Dataset.load(args.dataset)
    old = _old_datasets(args.at)
    counts: Counter = Counter()
    rows = []
    for r in rec.load(args.records):
        if r.get("kind") == "status":
            continue
        s = st.classify(r.get("subject", {}), ds, old.get(r.get("subject", {}).get("commit", "")))
        counts[s.state] += 1
        rows.append({"id": r.get("id"), "kind": r.get("kind"), "subject": r["subject"]["name"],
                     "status": s.state, "now": s.decl.name if s.decl else None,
                     "changed": s.changed, "assumed_hasher": s.assumed_hasher})
    if args.json:
        print(json.dumps({"counts": counts, "records": rows}, indent=1))
    else:
        for row in rows:
            extra = f" → {row['now']}" if row["now"] and row["now"] != row["subject"] else ""
            changed = f" (changed: {', '.join(row['changed'])})" if row["changed"] else ""
            print(f"{row['status']:17} {row['kind']:7} {row['subject']}{extra}{changed}")
        print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return 0


def _claims(ds: Dataset, explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    return sorted(ds.facet("annotation.claim"))


def cmd_coverage(args) -> int:
    ds = Dataset.load(args.dataset)
    ev = Evidence.resolve(rec.load(args.records), ds, _old_datasets(args.at))
    out = [coverage_of(ev, c, _policy(args)).summary() for c in _claims(ds, args.claim)]
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for c in out:
            mark = "covered" if c["covered"] else f"{c['reviewed']}/{c['members']} reviewed"
            print(f"{c['claim']}: {mark}; {c['upstream']} upstream declarations")
            for n in c["with_problems"]:
                print(f"  open problem: {n}")
    return 0


def cmd_queue(args) -> int:
    ds = Dataset.load(args.dataset)
    ev = Evidence.resolve(rec.load(args.records), ds)
    for d, w in queue_of(ev, _claims(ds, args.claim), _policy(args), limit=args.limit):
        print(f"{w:4} {d.kind:12} {d.name}")
    return 0


def cmd_claims(args) -> int:
    ds = Dataset.load(args.dataset)
    for name, rows in sorted(ds.facet("annotation.claim").items()):
        ref = rows[0].get("payload", {}).get("reference", "")
        print(f"{name}" + (f"  ({ref})" if ref else ""))
    return 0


def cmd_diff(args) -> int:
    old, new = Dataset.load(args.old), Dataset.load(args.new)
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {}
    for d in old.decls:
        if not d.is_project:
            continue
        subject = {"name": d.name, "hashes": {"meaning": d.meaning, "local": d.local},
                   "kind": st.subject_kind_of(d), "hasher": old.hasher}
        s = st.classify(subject, new)
        state = s.state
        if state == st.CURRENT and new.by_name[d.name].content != d.content:
            state = "current (proof changed)"
        counts[state] += 1
        examples.setdefault(state, []).append(d.name if state != st.RENAMED else
                                              f"{d.name} → {s.decl.name}")
    added = [d.name for d in new.decls if d.is_project and d.name not in old.by_name]
    renamed_to = {e.split(" → ")[1] for e in examples.get(st.RENAMED, [])}
    added = [n for n in added if n not in renamed_to]
    report = {"old": old.commit, "new": new.commit, "project_old": sum(counts.values()),
              "counts": dict(counts), "added": len(added)}
    if args.json:
        report["examples"] = {k: v[: args.examples] for k, v in examples.items()}
        report["added_examples"] = added[: args.examples]
        print(json.dumps(report, indent=1))
    else:
        print(f"{old.commit[:10]} → {new.commit[:10]}: {report['project_old']} project declarations")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {k:24} {v}")
        print(f"  {'added':24} {len(added)}")
    return 0


def cmd_validate(args) -> int:
    bad = 0
    for n, r in enumerate(rec.load(args.records), 1):
        for e in rec.validate(r):
            print(f"{args.records}: record {n}: {e}")
            bad += 1
    print("ok" if not bad else f"{bad} problems")
    return 1 if bad else 0


def cmd_migrate(args) -> int:
    ds = Dataset.load(args.dataset)
    at = _old_datasets(args.at)
    if args.source == "reviewed-by":
        d = Path(args.input)
        report = mig.from_reviewed_by(rec.load(d / "records.jsonl"), rec.load(d / "tests.jsonl"),
                                      rec.load(d / "named.jsonl"), rec.load(d / "problems.jsonl"),
                                      args.repo, at, ds)
    elif args.source == "referee":
        report = mig.from_referee_audit(json.loads(Path(args.input).read_text()), ds, args.reviewer)
    else:
        report = mig.from_trust_marks(json.loads(Path(args.input).read_text()), at, ds, args.reviewer)
    rec.append(args.out, report.migrated)
    print(f"migrated {len(report.migrated)} records to {args.out}")
    for s in report.skipped:
        print(f"skipped: {s}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evidence_core", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def policy_args(q):
        q.add_argument("--agents", action="store_true", help="count reviews by AI agents")
        q.add_argument("--stale-underneath", action="store_true",
                       help="count acceptances whose subject changed underneath since")
        q.add_argument("--no-caveats", action="store_true", help="do not count accepts with caveats")
        q.add_argument("--no-authors", action="store_true", help="do not count authors' reviews")
        q.add_argument("--upstream", action="store_true", help="require upstream reviews too")

    q = sub.add_parser("status", help="status of every record")
    q.add_argument("--dataset", required=True)
    q.add_argument("--records", required=True)
    q.add_argument("--at", action="append", help="COMMIT=DIR: a dataset of an older commit")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_status)

    q = sub.add_parser("coverage", help="coverage of claims")
    q.add_argument("--dataset", required=True)
    q.add_argument("--records", required=True)
    q.add_argument("--claim", action="append", help="default: every @[claim] in the dataset")
    q.add_argument("--at", action="append")
    q.add_argument("--json", action="store_true")
    policy_args(q)
    q.set_defaults(fn=cmd_coverage)

    q = sub.add_parser("queue", help="what to review next")
    q.add_argument("--dataset", required=True)
    q.add_argument("--records", required=True)
    q.add_argument("--claim", action="append")
    q.add_argument("--limit", type=int, default=30)
    policy_args(q)
    q.set_defaults(fn=cmd_queue)

    q = sub.add_parser("claims", help="the @[claim] declarations of a dataset")
    q.add_argument("--dataset", required=True)
    q.set_defaults(fn=cmd_claims)

    q = sub.add_parser("diff", help="how project declarations moved between two datasets")
    q.add_argument("--old", required=True)
    q.add_argument("--new", required=True)
    q.add_argument("--json", action="store_true")
    q.add_argument("--examples", type=int, default=10)
    q.set_defaults(fn=cmd_diff)

    q = sub.add_parser("validate", help="check an S3 file")
    q.add_argument("records")
    q.set_defaults(fn=cmd_validate)

    q = sub.add_parser("migrate", help="convert existing review data to S3")
    q.add_argument("source", choices=["reviewed-by", "referee", "trust"])
    q.add_argument("input", help="Reviewed-by's reviews/ directory, a Referee audit export, or "
                                 "a trust-marks.json")
    q.add_argument("--dataset", required=True, help="dataset to take hashes from")
    q.add_argument("--at", action="append", help="COMMIT=DIR: datasets of the original commits")
    q.add_argument("--repo", default="", help="the ledger's repository, for origins")
    q.add_argument("--reviewer", default="", help="GitHub login to attribute unsigned records to")
    q.add_argument("--out", required=True)
    q.set_defaults(fn=cmd_migrate)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
