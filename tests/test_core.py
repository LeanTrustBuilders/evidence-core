"""Tests against the extractor's two-version fixture (tests/vectors/fixture-a and fixture-b).

The fixture's version B changes `double`, rewrites the statement of `triple_one`, changes only the
proof of `triple_two`, renames a binder of `triple_comm`, and renames `triple_three` to
`triple_three'`. The vectors are real output of `trust-extract` (see LeanTrustBuilders/extractor,
`test/run.sh vectors-dir`).

Run with ``python3 -m unittest discover -s tests``.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from evidence_core import Dataset, Evidence, Policy, classify, coverage, queue, with_id, validate
from evidence_core import records as rec
from evidence_core import status as st
from evidence_core import migrate as mig
from evidence_core.cli import main as cli

VECTORS = Path(__file__).parent / "vectors"
A = Dataset.load(VECTORS / "fixture-a")
B = Dataset.load(VECTORS / "fixture-b")
F = "Fixture."


def review(name: str, ds: Dataset = A, verdict: str = "accept", agent: bool = False,
           at: str = "2026-09-25T10:00:00Z", **extra) -> dict:
    by = {"kind": "agent", "agent": "test agent", "identity": {"kind": "none"}} if agent else \
        {"kind": "person", "identity": {"kind": "github", "id": "tester"}}
    r = {"schema": "ltb-evidence/0", "kind": "review",
         "subject": rec.subject_from_decl(ds.by_name[F + name], ds), "verdict": verdict,
         "by": by, "at": at, "origin": {"kind": "cli"}}
    if agent or verdict == "problem":
        r["rationale"] = "because"
    if verdict == "problem":
        r["problem"] = {"category": "F1"}
    r.update(extra)
    return with_id(r)


class DatasetTests(unittest.TestCase):
    def test_meta(self):
        self.assertEqual(A.commit, "A")
        self.assertEqual(B.commit, "B")
        self.assertEqual(set(A.notions()), {"statement", "meaning", "term"})

    def test_closure(self):
        names = {d.name for d in A.closure(F + "triple_pos")}
        self.assertIn(F + "triple_pos", names)
        self.assertIn(F + "triple", names)
        self.assertNotIn(F + "double", names)
        self.assertIn("Nat", names)  # upstream nodes are in the closure, and are leaves

    def test_meaning_skips_proof_fields(self):
        self.assertIn(F + "one_pos'", A.successors(F + "one", "term"))
        self.assertNotIn(F + "one_pos'", A.successors(F + "one", "meaning"))

    def test_facets(self):
        self.assertEqual(A.facet_row("annotation.claim", F + "triple_pos")["payload"],
                         {"reference": "Fixture, Theorem 1"})
        self.assertEqual(A.facet_row("source", F + "double")["keyword"], "def")
        self.assertIsNone(A.facet_row("source", "Nat"))


class RecordTests(unittest.TestCase):
    def test_id_is_canonical(self):
        r = review("triple")
        shuffled = dict(reversed(list(r.items())))
        self.assertEqual(rec.record_id(shuffled), r["id"])
        self.assertEqual(validate(r), [])

    def test_validation(self):
        r = review("triple")
        del r["at"]
        self.assertIn("missing at", validate(r))
        bad = review("triple")
        bad["verdict"] = "maybe"
        self.assertTrue(any("verdict" in e for e in validate(bad)))
        agent = review("triple", agent=True)
        del agent["rationale"]
        self.assertTrue(any("agent needs a rationale" in e for e in validate(agent)))
        tampered = review("triple")
        tampered["rationale"] = "changed after the id was computed"
        self.assertIn("id does not match the canonical form", validate(tampered))

    def test_append_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "evidence.jsonl"
            rec.append(path, [review("triple"), review("double")])
            self.assertEqual(len(rec.load(path)), 2)
            with self.assertRaises(ValueError):
                rec.append(path, [{"schema": "ltb-evidence/0", "kind": "review"}])


class StatusTests(unittest.TestCase):
    def status(self, name):
        return classify(review(name)["subject"], B, old=A)

    def test_current(self):
        for name in ("triple", "triple_comm", "triple_two", "triple_pos"):
            self.assertEqual(self.status(name).state, st.CURRENT, name)

    def test_stale_underneath_names_the_cause(self):
        s = self.status("double_zero")
        self.assertEqual(s.state, st.STALE_UNDERNEATH)
        self.assertEqual(s.changed, [F + "double"])
        self.assertEqual(self.status("double_triple").state, st.STALE_UNDERNEATH)

    def test_stale(self):
        self.assertEqual(self.status("double").state, st.STALE)
        self.assertEqual(self.status("triple_one").state, st.STALE)

    def test_renamed(self):
        s = self.status("triple_three")
        self.assertEqual(s.state, st.RENAMED)
        self.assertEqual(s.decl.name, F + "triple_three'")

    def test_orphaned_unknown_incomparable(self):
        subject = dict(review("triple")["subject"])
        self.assertEqual(classify(dict(subject, name=F + "gone",
                                       hashes={"meaning": "0" * 16}), B).state, st.ORPHANED)
        self.assertEqual(classify(dict(subject, hashes={}), B).state, st.UNKNOWN)
        other = dict(subject, hasher={"name": "semantic_hash", "revision": "another"})
        self.assertEqual(classify(other, B).state, st.INCOMPARABLE)
        unknown_rev = dict(subject, hasher={"name": "semantic_hash", "revision": None})
        s = classify(unknown_rev, B)
        self.assertEqual(s.state, st.CURRENT)
        self.assertTrue(s.assumed_hasher)


class CoverageTests(unittest.TestCase):
    def test_covered_by_people(self):
        ev = Evidence.resolve([review("triple_pos"), review("triple")], B)
        c = coverage(ev, F + "triple_pos")
        self.assertTrue(c.is_covered, c.summary())
        self.assertEqual({d.name for d in c.members}, {F + "triple_pos", F + "triple"})
        self.assertGreater(len(c.upstream), 0)

    def test_agents_count_only_by_policy(self):
        ev = Evidence.resolve([review("triple_pos"), review("triple", agent=True)], B)
        self.assertFalse(coverage(ev, F + "triple_pos").is_covered)
        self.assertTrue(coverage(ev, F + "triple_pos", Policy(agents=True)).is_covered)

    def test_stale_underneath_counts_only_by_policy(self):
        records = [review("double_triple"), review("double"), review("triple")]
        ev = Evidence.resolve(records, B)
        # `double` changed (stale): never counts; `double_triple` changed underneath.
        c = coverage(ev, F + "double_triple", Policy(stale_underneath=True))
        self.assertEqual({d.name for d in c.uncovered}, {F + "double"})
        c = coverage(ev, F + "double_triple")
        self.assertEqual({d.name for d in c.uncovered}, {F + "double", F + "double_triple"})

    def test_problem_lifecycle(self):
        problem = review("triple", verdict="problem", at="2026-09-25T11:00:00Z")
        records = [review("triple_pos"), review("triple"), problem]
        ev = Evidence.resolve(records, B)
        c = coverage(ev, F + "triple_pos")
        self.assertFalse(c.is_covered)
        self.assertEqual([d.name for d in c.with_problems], [F + "triple"])
        fixed = with_id({"schema": "ltb-evidence/0", "kind": "status", "target": problem["id"],
                         "state": "fixed", "at": "2026-09-25T12:00:00Z",
                         "by": {"kind": "person", "identity": {"kind": "none"}}})
        self.assertEqual(validate(fixed), [])
        ev = Evidence.resolve(records + [fixed], B)
        self.assertTrue(coverage(ev, F + "triple_pos").is_covered)

    def test_queue(self):
        ev = Evidence.resolve([review("triple_pos")], B)
        ranked = [d.name for d, _ in queue(ev, [F + "triple_pos", F + "double_triple"])]
        self.assertEqual(ranked[0], F + "triple")  # in both claims' closures
        self.assertNotIn(F + "triple_pos", ranked)


class MigrationTests(unittest.TestCase):
    def test_reviewed_by(self):
        records = [{"schema": "reviewed-by/v1", "decl": F + "triple", "hash": "f48b835ce630",
                    "tauceti": "A", "trailer": "Reviewed-by", "by": "someone", "kind": "person",
                    "agent": "", "evidence": "LGTM", "source": {"issue": 4},
                    "at": "2026-09-21T15:05:54Z"},
                   {"schema": "reviewed-by/v1", "decl": F + "double", "hash": "0",
                    "tauceti": "A", "by": "someone", "kind": "agent", "agent": "An agent",
                    "evidence": "It doubles.", "source": {"issue": 2}, "at": "2026-09-21T14:43:34Z"}]
        tests = [{"schema": "tests/v1", "decl": F + "double", "test": F + "double_zero",
                  "checks": "zero", "by": "someone", "kind": "agent", "agent": "An agent",
                  "source": {"issue": 1, "comment": 5}, "at": "2026-09-22T16:22:52Z"}]
        problems = [{"schema": "problem/v1", "event": "reported", "issue": 8, "decl": F + "one",
                     "hash": "x", "tauceti": "A", "what": "wrong", "why": "It is not one.",
                     "fix": "", "by": "someone", "kind": "person", "agent": "",
                     "at": "2026-09-22T14:20:00Z"},
                    {"schema": "problem/v1", "event": "closed", "issue": 8,
                     "resolution": "not planned", "by": "someone", "at": "2026-09-22T14:25:00Z"}]
        report = mig.from_reviewed_by(records, tests, [], problems, "owner/ledger", {"A": A}, B)
        self.assertEqual(report.skipped, [])
        for r in report.migrated:
            self.assertEqual(validate(r), [], r)
        ev = Evidence.resolve(report.migrated, B)
        states = {r["subject"]["name"]: s.state for rows in ev.by_decl.values() for r, s in rows
                  if r["kind"] == "review"}
        self.assertEqual(states[F + "triple"], st.CURRENT)
        self.assertEqual(states[F + "double"], st.STALE)
        problem = next(r for r in report.migrated if r.get("verdict") == "problem")
        self.assertEqual(ev.problem_state[problem["id"]], "invalid")

    def test_referee_and_trust(self):
        audit = {"version": 1, "project": "Fixture", "dataId": "abc", "verdicts": {
            F + "triple": {"verdict": "accepted", "note": "", "at": "2026-09-01T00:00:00Z",
                           "meaning": A.by_name[F + "triple"].meaning},
            F + "double": {"verdict": "query", "note": "why n + n?", "at": "2026-09-01T00:00:00Z",
                           "meaning": A.by_name[F + "double"].meaning}}}
        report = mig.from_referee_audit(audit, B)
        self.assertEqual(len(report.migrated), 2)
        for r in report.migrated:
            self.assertEqual(validate(r), [])
        states = {r["subject"]["name"]: classify(r["subject"], B).state for r in report.migrated}
        self.assertEqual(states, {F + "triple": st.CURRENT, F + "double": st.STALE})
        marks = {"version": 1, "trusted": [{"name": F + "triple", "commit": "A", "note": "ok"}],
                 "characterizations": [{"definition": F + "IsSmall", "theorems": [], "note": ""}],
                 "protected": []}
        report = mig.from_trust_marks(marks, {"A": A}, B)
        self.assertEqual(len(report.migrated), 1)
        self.assertEqual(len(report.skipped), 1)


class CliTests(unittest.TestCase):
    def run_cli(self, *args) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli(list(args)), 0)
        return buf.getvalue()

    def test_diff(self):
        out = json.loads(self.run_cli("diff", "--old", str(VECTORS / "fixture-a"),
                                      "--new", str(VECTORS / "fixture-b"), "--json"))
        c = out["counts"]
        self.assertEqual(c["stale"], 2)              # double, triple_one
        self.assertEqual(c["stale-underneath"], 2)   # double_zero, double_triple
        self.assertEqual(c["renamed"], 1)            # triple_three → triple_three'
        self.assertEqual(c["current (proof changed)"], 1)  # triple_two
        self.assertEqual(out["added"], 0)

    def test_status_and_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "evidence.jsonl"
            rec.append(path, [review("triple_pos"), review("triple"), review("double_zero")])
            out = self.run_cli("status", "--dataset", str(VECTORS / "fixture-b"), "--records",
                               str(path), "--at", str(VECTORS / "fixture-a"))
            self.assertIn("stale-underneath  review  Fixture.double_zero (changed: Fixture.double)",
                          out)
            out = json.loads(self.run_cli("coverage", "--dataset", str(VECTORS / "fixture-b"),
                                          "--records", str(path), "--json"))
            self.assertEqual(out, [{"claim": F + "triple_pos", "covered": True, "members": 2,
                                    "reviewed": 2, "unreviewed": [], "with_problems": [],
                                    "upstream": out[0]["upstream"]}])
            self.assertEqual(self.run_cli("validate", str(path)).strip(), "ok")


if __name__ == "__main__":
    unittest.main()
