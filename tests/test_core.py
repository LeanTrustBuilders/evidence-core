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
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from evidence_core import Dataset, Evidence, Policy, classify, coverage, queue, with_id, validate
from evidence_core import records as rec
from evidence_core import status as st
from evidence_core import migrate as mig
from evidence_core import store as sto
from evidence_core.cli import main as cli

VECTORS = Path(__file__).parent / "vectors"
A = Dataset.load(VECTORS / "fixture-a")
B = Dataset.load(VECTORS / "fixture-b")
# Version B extracted as if `Fixture.Uses` did not build: it and the root module are unavailable.
B_PARTIAL = Dataset.load(VECTORS / "fixture-b-partial")
F = "Fixture."


def review(name: str, ds: Dataset = A, verdict: str = "accept", agent: bool = False,
           at: str = "2026-09-25T10:00:00Z", **extra) -> dict:
    by = {"kind": "agent", "agent": {"tool": "test agent"}} if agent else \
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

    def test_unavailable(self):
        self.assertEqual(B_PARTIAL.unavailable, {"Fixture", "Fixture.Uses"})
        self.assertEqual(classify(review("double_triple")["subject"], B_PARTIAL).state, st.UNAVAILABLE)
        # A declaration of a module that built is classified as usual.
        self.assertEqual(classify(review("double_zero")["subject"], B_PARTIAL, old=A).state,
                         st.STALE_UNDERNEATH)


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
                         "by": {"kind": "person", "identity": {"kind": "github", "id": "maintainer"}}})
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
        # Referee's audit records no reviewer, and records are never anonymous.
        self.assertEqual(mig.from_referee_audit(audit, B).migrated, [])
        report = mig.from_referee_audit(audit, B, reviewer="someone")
        self.assertEqual(len(report.migrated), 2)
        for r in report.migrated:
            self.assertEqual(validate(r), [])
        states = {r["subject"]["name"]: classify(r["subject"], B).state for r in report.migrated}
        self.assertEqual(states, {F + "triple": st.CURRENT, F + "double": st.STALE})
        marks = {"version": 1, "trusted": [{"name": F + "triple", "commit": "A", "note": "ok"}],
                 "characterizations": [{"definition": F + "IsSmall", "theorems": [], "note": ""}],
                 "protected": []}
        report = mig.from_trust_marks(marks, {"A": A}, B, reviewer="someone")
        self.assertEqual(len(report.migrated), 1)
        self.assertEqual(len(report.skipped), 1)


class IdentityTests(unittest.TestCase):
    """Records are never anonymous: a GitHub account, an AI agent, or an agent acting through an
    account."""

    def test_who_may_make_a_record(self):
        base = review("triple")
        person = {"kind": "person", "identity": {"kind": "github", "id": "someone"}}
        agent = {"kind": "agent", "agent": {"tool": "Claude Code", "model": "claude-opus-5-5"}}
        ok = [person, agent, {**agent, "identity": {"kind": "github", "id": "operator"}}]
        bad = [{"kind": "person"}, {"kind": "person", "identity": {"kind": "none"}},
               {"kind": "person", "identity": {"kind": "key", "fingerprint": "ab"}},
               {"kind": "agent", "agent": "Claude Code, Opus 5"}, {"kind": "agent"}]
        for by in ok:
            self.assertEqual(validate(with_id({**base, "by": by, "rationale": "r"})), [], by)
        for by in bad:
            self.assertNotEqual(validate(with_id({**base, "by": by, "rationale": "r"})), [], by)

    def test_agent_labels(self):
        self.assertEqual(rec.parse_agent("Claude Code, Opus 5, session 095781b9"),
                         {"tool": "Claude Code", "model": "Opus 5", "session": "095781b9"})
        self.assertEqual(rec.parse_agent("Voyager"), {"tool": "Voyager"})
        self.assertEqual(rec.who({"kind": "agent", "agent": {"tool": "T", "model": "m"},
                                  "identity": {"kind": "github", "id": "op"}}), "T (m) via op")


def person(login: str) -> dict:
    return {"kind": "person", "identity": {"kind": "github", "id": login}}


def status(target: dict, state: str, by: dict, at: str) -> dict:
    return with_id({"schema": "ltb-evidence/0", "kind": "status", "target": target["id"],
                    "state": state, "by": by, "at": at})


def comment(target: dict, text: str, by: dict, at: str) -> dict:
    return with_id({"schema": "ltb-evidence/0", "kind": "comment", "text": text,
                    "links": {"replies_to": target["id"]}, "by": by, "at": at})


class ThreadTests(unittest.TestCase):
    def test_supersedes_only_the_same_reviewer(self):
        first = review("triple")
        again = with_id({**review("triple", at="2026-09-26T10:00:00Z"), "links": {"supersedes": first["id"]},
                         "caveats": [{"category": "F3", "note": "at 0"}]})
        other = with_id({**review("triple_pos", at="2026-09-26T11:00:00Z"), "by": person("other"),
                         "links": {"supersedes": review("triple_pos")["id"]}})
        ev = Evidence.resolve([first, again, review("triple_pos"), other], B)
        self.assertEqual(ev.superseded_by, {first["id"]: again["id"]})
        self.assertEqual(ev.counting_accepts(F + "triple", Policy()), [again])
        self.assertEqual(ev.counting_accepts(F + "triple", Policy(caveats=False)), [])
        # Someone else cannot supersede a review.
        self.assertEqual(len(ev.counting_accepts(F + "triple_pos", Policy())), 2)

    def test_withdrawn_answered_and_reopened(self):
        acc = review("triple")
        q = review("triple", verdict="question", rationale="what is triple 0?")
        answer = comment(q, "0, by `rfl`", person("author"), "2026-09-25T11:00:00Z")
        records = [acc, q, answer,
                   status(acc, "withdrawn", person("tester"), "2026-09-25T12:00:00Z"),
                   status(q, "answered", person("tester"), "2026-09-25T12:00:00Z")]
        ev = Evidence.resolve(records, B)
        self.assertEqual(ev.counting_accepts(F + "triple", Policy()), [])
        self.assertEqual(ev.open_questions(F + "triple"), [])
        self.assertEqual(ev.replies[q["id"]], [answer])
        self.assertIn(answer, [r for r, _ in ev.records_on(F + "triple", "comment")])
        ev = Evidence.resolve(records + [status(q, "reopened", person("x"), "2026-09-25T13:00:00Z")], B)
        self.assertEqual(ev.open_questions(F + "triple"), [q])

    def test_disagreement_and_checklist(self):
        acc = with_id({**review("triple"), "checked": {"F1": "checked", "F4": "unchecked"}})
        prob = with_id({**review("triple", verdict="problem", problem={"category": "F3"}),
                        "by": person("other")})
        ev = Evidence.resolve([acc, prob], B)
        self.assertTrue(ev.disagreement(F + "triple"))
        self.assertEqual(list(ev.checked(F + "triple")), ["F1"])
        self.assertFalse(ev.reviewed(F + "triple", Policy()))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_load_and_conflicts(self):
        store = sto.Store.init(self.root / "evidence", sto.default_config("o/lib", "Fixture"))
        added = store.add([review("triple"), review("triple"),
                           review("double", at="2026-10-01T00:00:00Z")])
        self.assertEqual(len(added), 2)
        self.assertEqual(sorted(p.name for p in (self.root / "evidence" / "records").glob("*.jsonl")),
                         ["2026-09.jsonl", "2026-10.jsonl"])
        again = sto.Store.load(self.root / "evidence")
        self.assertEqual(again.ids, store.ids)
        self.assertEqual(again.dataset_tag("0123456789abcdef"), "dataset-0123456789ab")
        # The same record twice is fine; two records under one id are not.
        line = json.dumps(added[0])
        (self.root / "evidence" / "copy.jsonl").write_text(line + "\n")
        sto.Store.load(self.root / "evidence")
        (self.root / "evidence" / "bad.jsonl").write_text(json.dumps({**added[0], "at": "x"}) + "\n")
        with self.assertRaises(sto.StoreError):
            sto.Store.load(self.root / "evidence")
        with self.assertRaises(sto.StoreError):
            store.add([{**review("triple"), "by": {"kind": "person"}}])

    def test_check(self):
        a, b = review("triple"), review("double")
        self.assertEqual(sto.check([a], [a, b], author="tester"), [])
        self.assertTrue(any("removed" in e for e in sto.check([a, b], [a])))
        self.assertTrue(any("changed" in e for e in sto.check([a], [{**a, "at": "2027"}])))
        self.assertTrue(any("but the change is by" in e for e in sto.check([a], [a, b], author="else")))

    def test_check_against_git(self):
        repo = self.root / "repo"
        repo.mkdir()
        git = lambda *args: subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.email", "t@example.org")
        git("config", "user.name", "t")
        store = sto.Store.init(repo / "evidence", sto.default_config("o/lib", "Fixture"))
        store.add([review("triple")])
        git("add", "-A")
        git("commit", "-qm", "one")
        store.add([review("double")])
        before = sto.records_at(repo, "HEAD")
        self.assertEqual(len(before), 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli(["store-check", "--repo", str(repo), "--base", "HEAD", "--author", "tester"]), 0)
            self.assertEqual(cli(["store-check", "--repo", str(repo), "--base", "HEAD", "--author", "else"]), 1)
        self.assertIn("2 records, 1 new since HEAD", buf.getvalue())


class GraphAgainstHashTests(unittest.TestCase):
    """Check 1 on the fixture, and on copies of it with one planted disagreement of each kind."""

    def planted(self, ds_path: Path, tmp: Path, edit) -> Dataset:
        import shutil, struct
        out = tmp / ds_path.name
        shutil.copytree(ds_path, out)
        meta = json.loads((out / "meta.json").read_text())
        e = next(x for x in meta["edges"] if x["name"] == "meaning")
        data = (out / e["file"]).read_bytes()
        pairs = [struct.unpack_from("<ii", data, 8 * k) for k in range(len(data) // 8)]
        pairs = edit(Dataset.load(ds_path), pairs)
        (out / e["file"]).write_bytes(b"".join(struct.pack("<ii", a, b) for a, b in pairs))
        e["count"] = len(pairs)
        (out / "meta.json").write_text(json.dumps(meta))
        return Dataset.load(out)

    def test_the_fixture_agrees(self):
        from evidence_core.checks import graph_against_hash
        r = graph_against_hash(A, B)
        self.assertEqual(r.findings, [])
        self.assertGreaterEqual(r.stale_underneath, 2)   # double_zero, double_triple: explained by double

    def test_planted_disagreements_are_found(self):
        from evidence_core.checks import graph_against_hash
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            # Drop double_zero's edges in both versions: it goes stale underneath with nothing to explain it.
            drop = lambda ds, pairs: [p for p in pairs if p[0] != ds.by_name[F + "double_zero"].id]
            (t / "a").mkdir(); (t / "b").mkdir()
            a = self.planted(VECTORS / "fixture-a", t / "a", drop)
            b = self.planted(VECTORS / "fixture-b", t / "b", drop)
            r = graph_against_hash(a, b)
            self.assertIn(F + "double_zero", [f.decl for f in r.unexplained])
            # Add an edge from triple (unchanged) to double (changed): its hash should have moved.
            (t / "c").mkdir()
            add = lambda ds, pairs: pairs + [(ds.by_name[F + "triple"].id, ds.by_name[F + "double"].id)]
            b2 = self.planted(VECTORS / "fixture-b", t / "c", add)
            r = graph_against_hash(A, b2)
            [f] = [f for f in r.missed if f.decl == F + "triple"]
            self.assertEqual((f.path, f.graph, f.rewritten), ([F + "triple", F + "double"], "new", True))


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
