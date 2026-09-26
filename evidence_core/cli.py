"""Command line: ``python -m evidence_core <command> …``.

Commands:

* ``status``    the status of every record against a dataset;
* ``coverage``  the coverage of claims under a policy;
* ``queue``     unreviewed declarations in claims' closures, ranked;
* ``claims``    the declarations annotated ``@[claim]`` in a dataset;
* ``diff``      how every project declaration moved between two datasets;
* ``validate``  check an S3 file;
* ``store-check`` check a change to an evidence store (git): nothing changed or removed, every new
  record valid and written by the right account;
* ``check-graph`` graph against hash over two datasets: declarations whose meaning hash and meaning
  graph disagree about whether something beneath them changed;
* ``compare-rules`` two datasets of one commit under two rules: how their graphs differ;
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
from . import store as sto
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


def _records(path: str) -> list[dict]:
    """The records of a JSONL file, or of an evidence store (a directory)."""
    return sto.Store.load(path).records if Path(path).is_dir() else rec.load(path)


def cmd_store_check(args) -> int:
    after = sto.Store.load(Path(args.repo) / args.store).records
    before = sto.records_at(args.repo, args.base, args.store) if args.base else []
    errs = sto.check(before, after, author=args.author or None)
    for e in errs:
        print(f"error: {e}", file=sys.stderr)
    new = len({r["id"] for r in after} - {r["id"] for r in before})
    print(f"{len(after)} records, {new} new" + (f" since {args.base}" if args.base else "") +
          (f"; {len(errs)} problems" if errs else "; ok"))
    return 1 if errs else 0


def cmd_check_graph(args) -> int:
    from .checks import graph_against_hash
    report = graph_against_hash(Dataset.load(args.old), Dataset.load(args.new), args.notion)
    if args.json:
        print(json.dumps({**report.summary(), "findings": [f.as_json() for f in report.findings]}, indent=1))
    else:
        s = report.summary()
        print(f"{s['compared']} project declarations in both; meaning hash changed for {s['meaningChanged']} "
              f"({s['staleUnderneath']} stale underneath)")
        print(f"unexplained (stale underneath, nothing changed beneath in the graph): {s['unexplained']}")
        for f in report.unexplained[:args.limit]:
            print(f"  {f.decl}  (closure {f.closure[0]} → {f.closure[1]})")
        print(f"missed (something beneath changed in the graph, hash unchanged): {s['missed']}")
        for f in report.missed[:args.limit]:
            print(f"  {' → '.join(f.path)}  [{f.graph} graph; {'rewritten' if f.rewritten else 'changed underneath'}]")
    return 1 if report.findings and args.strict else 0


def cmd_compare_rules(args) -> int:
    from .checks import compare_rules
    a, b = Dataset.load(args.a), Dataset.load(args.b)
    if a.commit != b.commit:
        print(f"warning: the datasets are of different commits ({a.commit[:12]}, {b.commit[:12]})",
              file=sys.stderr)
    c = compare_rules(a, b, args.notion, args.examples)
    s = c.summary()
    if args.json:
        print(json.dumps({**s, "onlyA": c.only_a, "onlyB": c.only_b,
                          "examplesRemoved": c.examples_removed, "examplesAdded": c.examples_added},
                         indent=1))
        return 0
    print(f"`{args.notion}` graphs of {a.producer()} ({a.hasher.get('name')}) and "
          f"{b.producer()} ({b.hasher.get('name')})")
    print(f"project declarations: {s['common']} in both, {s['onlyA']} in A only, {s['onlyB']} in B only")
    for n in c.only_b[:args.examples]:
        print(f"  B only: {n}")
    print(f"direct edges from them: {s['edges']['both']} in both, {s['edges']['onlyA']} in A only, "
          f"{s['edges']['onlyB']} in B only")
    print(f"  A only, by target: {s['removedByTargetKind']}")
    print(f"  B only, by target: {s['addedByTargetKind']}")
    print(f"closure sizes: A {s['closureA']}, B {s['closureB']}")
    print(f"  smaller in B: {s['closureSmaller']}, larger: {s['closureLarger']}; lost project "
          f"declarations: {s['lostProjectDeclarations']}, gained: {s['gainedProjectDeclarations']}")
    for x, t in c.examples_removed:
        print(f"  A only: {x} → {t}")
    for x, t in c.examples_added:
        print(f"  B only: {x} → {t}")
    return 0


def _policy(args) -> Policy:
    return Policy(agents=args.agents, stale_underneath=args.stale_underneath,
                      caveats=not args.no_caveats, authors=not args.no_authors,
                      upstream=args.upstream)


def cmd_status(args) -> int:
    ds = Dataset.load(args.dataset)
    old = _old_datasets(args.at)
    counts: Counter = Counter()
    rows = []
    for r in _records(args.records):
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
    return sorted(ds.annotations("claim"))


def cmd_coverage(args) -> int:
    ds = Dataset.load(args.dataset)
    ev = Evidence.resolve(_records(args.records), ds, _old_datasets(args.at))
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
    ev = Evidence.resolve(_records(args.records), ds)
    for d, w in queue_of(ev, _claims(ds, args.claim), _policy(args), limit=args.limit):
        print(f"{w:4} {d.kind:12} {d.name}")
    return 0


def cmd_claims(args) -> int:
    from . import claims as claims_mod
    ds = Dataset.load(args.dataset)
    store_claims = None
    if args.store:
        from .store import Store
        store_claims = Store.load(args.store).config.get("claims") or None
    names = {d.name for d in ds.decls if d.is_project}
    cl = claims_mod.resolve(Path(args.source) if args.source else None, names, explicit=args.claim,
                            annotations=ds.annotations("claim"), store_claims=store_claims)
    if args.json:
        print(json.dumps({"claims": [c.as_json() for c in cl.claims], "scope": cl.scope, "sources": cl.sources,
                          "warnings": cl.warnings}, indent=1, ensure_ascii=False))
        return 0
    for c in cl.claims:
        print(f"{c.decl}  [{c.source}]" + (f"  {c.label or c.reference}" if c.label or c.reference else "")
              + ("" if c.found else "  (not in the dataset)"))
    for w in cl.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


def cmd_diff(args) -> int:
    from .changes import compare
    old, new = Dataset.load(args.old), Dataset.load(args.new)
    ch = compare(new, old)
    summary = ch.summary
    if args.json:
        out = {"old": old.commit, "new": new.commit, "counts": summary["counts"],
               "comparable": summary["comparable"],
               "examples": {k: v[: args.examples] for k, v in summary["lists"].items() if v}}
        print(json.dumps(out, indent=1))
    else:
        print(f"{old.commit[:10]} → {new.commit[:10]}: {summary['baseline']['decls']} → "
              f"{summary['current']['decls']} project declarations")
        for k, v in summary["counts"].items():
            print(f"  {k:12} {v}")
        if not summary["comparable"]:
            print("  (the two datasets' hashes are not comparable: different hashers)")
    return 0


def cmd_ledger(args) -> int:
    from . import ledger as ledger_mod
    led = ledger_mod.load(Path(args.ledger))
    if args.previous:
        print(ledger_mod.previous(led, args.previous) or "")
        return 0
    if not args.dataset:
        print("--dataset: the build to record", file=sys.stderr)
        return 2
    ds = Dataset.load(args.dataset)
    if ledger_mod.record(led, ds, date=args.date, label=args.label):
        ledger_mod.save(led, Path(args.ledger))
        print(f"recorded the build of {ds.commit[:12]} ({len(led['builds'])} builds)")
    else:
        print(f"{ds.commit[:12]} is already the last build")
    return 0


def cmd_validate(args) -> int:
    bad = 0
    for n, r in enumerate(_records(args.records), 1):
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

    q = sub.add_parser("claims", help="what a library claims: from a store, formalization.yaml, "
                                      "Comparator configs and @[claim]")
    q.add_argument("--dataset", required=True)
    q.add_argument("--source", help="a checkout of the library (for formalization.yaml and Comparator configs)")
    q.add_argument("--store", help="an evidence store, whose claims come first")
    q.add_argument("--claim", action="append", help="name the claims instead")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_claims)

    q = sub.add_parser("ledger", help="record a build in a provenance ledger (when each meaning changed)")
    q.add_argument("--ledger", required=True, help="the ledger file (created if missing)")
    q.add_argument("--dataset", help="the build to record")
    q.add_argument("--previous", metavar="COMMIT",
                   help="instead, print the last build before COMMIT (a new build's baseline)")
    q.add_argument("--date", default="")
    q.add_argument("--label", default="")
    q.set_defaults(fn=cmd_ledger)

    q = sub.add_parser("diff", help="how project declarations moved between two datasets")
    q.add_argument("--old", required=True)
    q.add_argument("--new", required=True)
    q.add_argument("--json", action="store_true")
    q.add_argument("--examples", type=int, default=10)
    q.set_defaults(fn=cmd_diff)

    q = sub.add_parser("validate", help="check an S3 file")
    q.add_argument("records")
    q.set_defaults(fn=cmd_validate)

    q = sub.add_parser("check-graph", help="graph against hash over two datasets")
    q.add_argument("--old", required=True)
    q.add_argument("--new", required=True)
    q.add_argument("--notion", default="meaning")
    q.add_argument("--limit", type=int, default=20, help="findings listed per kind")
    q.add_argument("--json", action="store_true")
    q.add_argument("--strict", action="store_true", help="exit 1 when anything disagrees")
    q.set_defaults(fn=cmd_check_graph)

    q = sub.add_parser("compare-rules", help="two datasets of one commit under two rules")
    q.add_argument("--a", required=True)
    q.add_argument("--b", required=True)
    q.add_argument("--notion", default="meaning")
    q.add_argument("--examples", type=int, default=10)
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_compare_rules)

    q = sub.add_parser("store-check", help="check a change to an evidence store")
    q.add_argument("--repo", default=".", help="the git repository holding the store")
    q.add_argument("--store", default="evidence", help="the store's directory in it")
    q.add_argument("--base", help="the revision before the change (default: check the store alone)")
    q.add_argument("--author", help="the GitHub login that made the change: new records must be by it")
    q.set_defaults(fn=cmd_store_check)

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
