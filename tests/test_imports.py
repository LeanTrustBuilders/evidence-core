"""Records imported from other stores (S3, "Imported records").

A view of the fixture library at version B imports the store `other/lib`, whose records were made
against version A, as a library resting on the fixture would make them.

Run with ``python3 -m unittest discover -s tests``.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evidence_core import Dataset, Evidence, Policy
from evidence_core import records as rec
from evidence_core import status as st
from evidence_core import views
from evidence_core.coverage import UNCOUNTED, COVERED
from evidence_core.store import Store, StoreError, default_config, with_imports

V = Path(__file__).parent / "vectors"
A, B = Dataset.load(V / "fixture-a"), Dataset.load(V / "fixture-b")
F = "Fixture."
OTHER = "other/lib"


def person(login: str) -> dict:
    return {"kind": "person", "identity": {"kind": "github", "id": login}}


def record(kind: str, by: dict, at: str, subject: dict | None = None, **fields) -> dict:
    r = {"schema": rec.SCHEMA, "kind": kind, "by": by, "at": at, **fields}
    if subject is not None:
        r["subject"] = subject
    return rec.with_id(r)


def key(name: str, ds: Dataset = A) -> dict:
    return rec.subject_from_decl(ds.by_name[F + name], ds)


def review(name: str, login: str, verdict: str = "accept", ds: Dataset = A, at: str = "2026-09-20T10:00:00Z", **fields) -> dict:
    extra = {"category": "edge-cases", "rubric": "ltb-rubric/1", "text": "because"} if verdict == "problem" else {}
    return record("review", person(login), at, key(name, ds), verdict=verdict, **extra, **fields)


def status(target: dict, state: str, login: str, at: str) -> dict:
    return record("status", person(login), at, target=target["id"], state=state)


class Imports(unittest.TestCase):
    def setUp(self):
        self.local = review("triple", "alice", ds=B)
        self.current = review("triple_pos", "bob")        # the same meaning in B: current
        self.stale = review("double", "bob")              # `double` changed in B: stale
        elsewhere = dict(key("triple"), name="Elsewhere.thing", hashes={"meaning": "0" * 16, "local": "1" * 16})
        self.elsewhere = record("review", person("bob"), "2026-09-20T10:00:00Z", elsewhere, verdict="accept")
        other_hasher = dict(key("triple"), hasher={"meaning": "ltb-meaning/1", "local": "ltb-local/2"})
        self.other_hasher = record("review", person("bob"), "2026-09-20T10:00:00Z", other_hasher, verdict="accept")
        self.reply = record("comment", person("carol"), "2026-09-20T11:00:00Z", text="about elsewhere",
                            links={"replies_to": self.elsewhere["id"]})
        self.fixed = review("triple_one", "carol", "problem")
        self.fixed_status = status(self.fixed, "fixed", "maint", "2026-09-21T10:00:00Z")      # held with it
        self.open = review("triple_comm", "carol", "problem")
        self.foreign_status = status(self.open, "invalid", "mallory", "2026-09-21T10:00:00Z")  # held here
        self.both = review("triple_two", "dave", ds=B)                                         # in both
        imported = [self.current, self.stale, self.elsewhere, self.other_hasher, self.reply, self.fixed,
                    self.fixed_status, self.open, self.both]
        local = [self.local, self.foreign_status, self.both]
        self.records = local + [r for r in imported if r not in local]
        self.sources = {r["id"]: OTHER for r in imported if r not in local}
        self.ev = Evidence.resolve(self.records, B, sources=self.sources)

    def test_relevance(self):
        # Kept, stale or not: the declaration is a node here.
        self.assertEqual([s.state for r, s in self.ev.records_on(F + "double", "review")], [st.STALE])
        self.assertIn(self.current["id"], [r["id"] for r, _ in self.ev.records_on(F + "triple_pos")])
        # Left out, not orphaned: about another library, or keyed by another hasher; with the reply.
        seen = {r["id"] for rows in self.ev.by_decl.values() for r, _ in rows} | {r["id"] for r, _ in self.ev.orphans}
        for r in (self.elsewhere, self.other_hasher, self.reply):
            self.assertNotIn(r["id"], seen)
        self.assertNotIn(self.elsewhere["id"], self.ev.replies)

    def test_who_sets_a_state(self):
        # A status held in the record's store counts; one from another store, by someone else, does not.
        self.assertEqual(self.ev.state(self.fixed["id"]), "fixed")
        self.assertEqual(self.ev.state(self.open["id"]), "open")
        # The record's maker may set its state from anywhere.
        withdrawn = status(self.open, "withdrawn", "carol", "2026-09-22T10:00:00Z")
        ev = Evidence.resolve(self.records + [withdrawn], B, sources=self.sources)
        self.assertEqual(ev.state(self.open["id"]), "withdrawn")

    def test_policy(self):
        self.assertEqual(self.ev.decl_state(F + "triple_pos", Policy()), COVERED)
        self.assertEqual(self.ev.decl_state(F + "triple_pos", Policy(imported=False)), UNCOUNTED)
        self.assertEqual(self.ev.why_uncounted(F + "triple_pos", Policy(imported=False)), "imported")

    def test_a_record_in_both_stores_is_the_importing_store_s(self):
        self.assertNotIn(self.both["id"], self.ev.source)
        self.assertEqual(self.ev.decl_state(F + "triple_two", Policy(imported=False)), COVERED)

    def test_views(self):
        v = views.record_view(self.ev, self.current, self.ev.records_on(F + "triple_pos")[0][1], F + "triple_pos")
        self.assertEqual((v["source"], v["actions"]), (OTHER, []))
        mine = views.record_view(self.ev, self.local, self.ev.records_on(F + "triple")[0][1], F + "triple")
        self.assertEqual(mine["source"], None)
        self.assertTrue(mine["actions"])


class ReadingImports(unittest.TestCase):
    def write_store(self, where: Path, records: list[dict], **config) -> None:
        where.mkdir(parents=True)
        (where / "store.json").write_text(json.dumps({**default_config("me/lib", "Fixture"), **config}))
        (where / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

    def test_with_imports(self):
        mine, theirs = review("triple", "alice", ds=B), review("triple_pos", "bob")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self.write_store(tmp / "evidence", [mine], imports=[{"repo": OTHER}])
            self.write_store(tmp / "cache" / OTHER / "evidence", [theirs, mine],
                             imports=[{"repo": "third/one"}])   # one level: not read
            (tmp / "cache" / "imports.json").write_text(json.dumps([{"repo": OTHER, "path": "evidence", "commit": "abc"}]))
            got = with_imports(Store.load(tmp / "evidence"), tmp / "cache")
            self.assertEqual([r["id"] for r in got.records], [mine["id"], theirs["id"]])
            self.assertEqual(got.sources, {theirs["id"]: OTHER})
            self.assertEqual(got.read, [{"repo": OTHER, "path": "evidence", "ref": None, "commit": "abc", "records": 1}])
            with self.assertRaises(StoreError):  # imported, but not fetched
                self.write_store(tmp / "e2", [], imports=[{"repo": "not/fetched"}])
                with_imports(Store.load(tmp / "e2"), tmp / "cache")

    def test_imports_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ({"repo": "no-slash"}, {"repo": "a/b", "ref": 3}, {"repo": "a/b", "branch": "x"}, "a/b"):
                where = Path(tmp) / str(abs(hash(json.dumps(bad))))
                self.write_store(where, [], imports=[bad])
                with self.assertRaises(StoreError, msg=bad):
                    Store.load(where)


if __name__ == "__main__":
    unittest.main()
