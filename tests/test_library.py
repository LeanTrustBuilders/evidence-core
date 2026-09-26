"""What evidence-core computes for pages: claims, changes, provenance, source text, the dataset's
analyses, record views and where each declaration stands under every policy. On the extractor's
two-version fixture (tests/vectors, see test_core.py); source-a and source-b are its sources.

Moved here from referee-site, whose pages now take all of this from evidence-core.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evidence_core import Dataset, Evidence, Policy, with_id
from evidence_core import analysis, claims as claims_mod, ledger as ledger_mod
from evidence_core import records as rec
from evidence_core.changes import compare
from evidence_core.coverage import all_policies, policy_key
from evidence_core.source import Sources, doc_comment_end, split_statement
from evidence_core.views import actions, by_view, views_on

V = Path(__file__).parent / "vectors"
A, B = Dataset.load(V / "fixture-a"), Dataset.load(V / "fixture-b")
F = "Fixture."


def person(login: str, **extra) -> dict:
    return {"kind": "person", "identity": {"kind": "github", "id": login}, **extra}


def agent(login: str) -> dict:
    return {"kind": "agent", "identity": {"kind": "github", "id": login}, "agent": {"tool": "Codex", "model": "m"}}


def review(name: str, by: dict, verdict: str = "accept", ds: Dataset = B, at: str = "2026-09-25T10:00:00Z",
           **extra) -> dict:
    r = {"schema": rec.SCHEMA, "kind": "review", "subject": rec.subject_from_decl(ds.by_name[F + name], ds),
         "verdict": verdict, "by": by, "at": at, "origin": {"kind": "issue", "ref": "o/r#3"}, **extra}
    if by["kind"] == "agent" or verdict != "accept":
        r.setdefault("rationale", "because")
    if verdict == "problem":
        r.setdefault("problem", {"category": "F3"})
    return with_id(r)


def status(target: dict, state: str, by: dict, at: str = "2026-09-26T10:00:00Z") -> dict:
    return with_id({"schema": rec.SCHEMA, "kind": "status", "target": target["id"], "state": state, "by": by,
                    "at": at, "origin": {"kind": "issue", "ref": "o/r#3/event/7"}})


class ClaimsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.names = {d.name for d in B.decls if d.is_project}

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, path: str, names: list[str]) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"challenge_module": "Challenge", "solution_module": "Solution",
                                 "theorem_names": names, "permitted_axioms": ["propext"]}))

    def test_formalization_yaml_ranks_a_comparator_config(self):
        self.config("comparator/pos.json", [F + "triple_comm", F + "triple_pos"])
        (self.root / "formalization.yaml").write_text(f"""
status:
  scope: >-
    Everything about triples.
  main_results:
    - declaration: "{F}triple_pos"
      source_statement: "Theorem 1"
      comparator_config: "comparator/pos.json"
    - declaration: "{F}missing"
      file: "Fixture/Gone.lean"
""")
        cl = claims_mod.resolve(self.root, self.names)
        self.assertEqual([c.decl for c in cl.claims], [F + "triple_pos", F + "missing"])
        self.assertEqual(cl.claims[0].label, "Theorem 1")
        # The file ranks the config: its declaration is the headline, the config's other name an
        # additional target.
        self.assertEqual(cl.claims[0].additional, [F + "triple_comm"])
        self.assertEqual(cl.claims[0].comparator["permitted_axioms"], ["propext"])
        self.assertFalse(cl.claims[1].found)
        self.assertTrue(any("missing" in w for w in cl.warnings))
        self.assertEqual(cl.scope, "Everything about triples.")
        self.assertEqual(cl.names, [F + "triple_pos"])

    def test_a_store_lists_its_claims_first_and_the_file_says_more_about_them(self):
        (self.root / "formalization.yaml").write_text(f"""
status:
  main_results:
    - declaration: "{F}triple_pos"
      source_statement: "Theorem 1"
""")
        cl = claims_mod.resolve(self.root, self.names, store_claims=[F + "triple_pos"],
                                annotations=B.annotations("claim"))
        self.assertEqual([(c.decl, c.source, c.label) for c in cl.claims], [(F + "triple_pos", "store", "Theorem 1")])
        self.assertEqual(cl.sources, ["store"])

    def test_one_config_is_one_claim(self):
        self.config("comparator/both.json", [F + "triple_comm", F + "triple_pos"])
        cl = claims_mod.resolve(self.root, self.names)
        self.assertEqual([(c.decl, c.additional, c.source) for c in cl.claims],
                         [(F + "triple_comm", [F + "triple_pos"], "comparator")])

    def test_annotations_and_the_command_line(self):
        cl = claims_mod.resolve(self.root, self.names, annotations=B.annotations("claim"))
        self.assertEqual([(c.decl, c.reference) for c in cl.claims], [(F + "triple_pos", "Fixture, Theorem 1")])
        cl = claims_mod.resolve(self.root, self.names, explicit=[F + "double"], annotations=B.annotations("claim"))
        self.assertEqual([c.decl for c in cl.claims], [F + "double"])

    def test_a_malformed_file_is_a_warning(self):
        (self.root / "formalization.yaml").write_text("status: [unclosed\n")
        cl = claims_mod.resolve(self.root, self.names)
        self.assertEqual(cl.claims, [])
        self.assertTrue(cl.warnings)


class ChangesTests(unittest.TestCase):
    def test_classes(self):
        ch = compare(B, A)
        self.assertEqual(ch.status[F + "double"], "body")           # same statement, new body
        self.assertEqual(ch.status[F + "triple_one"], "statement")
        self.assertEqual(ch.status[F + "double_zero"], "underneath")
        self.assertEqual(ch.detail[F + "double_zero"]["causes"], [F + "double"])
        self.assertEqual(ch.status[F + "triple_three'"], "renamed")
        self.assertEqual(ch.detail[F + "triple_three'"]["was"], F + "triple_three")
        self.assertNotIn(F + "triple_comm", ch.status)                 # a renamed binder is no change
        self.assertEqual(ch.summary["counts"]["removed"], 0)


class LedgerTests(unittest.TestCase):
    def test_history_follows_renames(self):
        led = ledger_mod.load(None)
        self.assertTrue(ledger_mod.record(led, A, date="2026-01-01", label="A"))
        self.assertFalse(ledger_mod.record(led, A))
        self.assertTrue(ledger_mod.record(led, B, date="2026-01-02", label="B"))
        self.assertEqual([k for k, _ in led["decls"][F + "double"]], [0, 1])
        self.assertEqual([k for k, _ in led["decls"][F + "triple_comm"]], [0])
        self.assertEqual([k for k, _ in led["decls"][F + "triple_three'"]], [0])   # carried over
        self.assertNotIn(F + "triple_three", led["decls"])

    def test_a_history_recorded_before_ltb_dataset_1_carries_over(self):
        # The history as a site built from ltb-dataset/0 datasets left it: the old meaning hashes.
        led = {"builds": [{"commit": "before", "date": "", "label": "before"}],
               "decls": {d.name: [[0, d.legacy_meaning]] for d in A.decls if d.is_project and d.legacy_meaning}}
        self.assertTrue(ledger_mod.record(led, A))
        self.assertTrue(all(len(h) == 1 and h[0][1] == A.by_name[n].meaning for n, h in led["decls"].items()))


class SourceTests(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_statement("theorem t (h : a = (b := c)) : x := by simp"),
                         ("theorem t (h : a = (b := c)) : x", ":= by simp"))
        self.assertEqual(split_statement('theorem t : f "a := b" := rfl')[0], 'theorem t : f "a := b"')
        self.assertEqual(split_statement("instance : Foo Nat where\n  x := 1")[0], "instance : Foo Nat")
        self.assertEqual(split_statement("/-- `x := y` -/\ntheorem t : x := rfl")[0], "/-- `x := y` -/\ntheorem t : x")

    def test_a_doc_comment_ends_where_the_declaration_starts(self):
        text = "/-- A /- nested -/ comment. -/\n\n@[simp]\ndef x := 1"
        self.assertEqual(text[doc_comment_end(text):], "@[simp]\ndef x := 1")
        self.assertEqual("/-- On one line. -/ def one := 1"[doc_comment_end("/-- On one line. -/ def one := 1"):], "def one := 1")
        self.assertEqual(doc_comment_end("def x := 1"), 0)

    def test_the_text_of_a_declaration_with_or_without_its_doc_comment(self):
        src = Sources(V / "source-b")
        with_doc = [(n, rows[0]) for n, rows in B.facet("source").items()
                    if B.facet_row("docstring", n) and rows[0]["start"][0] < rows[0]["end"][0]]
        name, row = with_doc[0]
        full = src.text(row)
        start, end, text = src.span(row, doc_comment=False)
        self.assertTrue(full.startswith("/--"))
        self.assertFalse(text.startswith("/--"))
        self.assertTrue(full.endswith(text))
        self.assertEqual(src.lines(row["path"])[start - 1].lstrip()[:len(text.split("\n")[0].lstrip())],
                         text.split("\n")[0].lstrip())
        self.assertEqual(end, row["end"][0])


class AnalysisTests(unittest.TestCase):
    def test_the_claims_scope_pulls_in_the_theorems_about_its_definitions(self):
        scope, pulled = analysis.claim_scope(B, [F + "double_zero"])
        names = {B.decls[i].name for i in scope}
        # What `double_zero`'s statement rests on …
        self.assertIn(F + "double", names)
        # … the theorems saying what `double` means, and what their statements rest on in turn.
        for n in ("double_triple", "IsDouble", "isDouble_double", "IsDouble.unique", "triple"):
            self.assertIn(F + n, names, n)
        self.assertIn(B.by_name[F + "double_triple"].id, pulled)
        # Nothing a proof merely calls.
        self.assertNotIn(F + "triple_pos", names)

    def test_specifications_and_characterizations(self):
        specs = analysis.specifications(B)
        self.assertIn({"decl": F + "double_triple", "comment": "relates it to `triple`", "kind": "specifies"},
                      specs[F + "double"])
        chars = analysis.characterizations(B)[F + "double"]
        self.assertTrue(analysis.is_characterized(chars))
        self.assertIn(F + "isDouble_double", chars[0]["existence"])

    def test_sorry_and_closures(self):
        self.assertEqual(analysis.sorry_of(B, F + "double"), analysis.Sorry(False, False))
        project, upstream = analysis.Closures(B).of(B.by_name[F + "double_zero"].id)
        self.assertIn(B.by_name[F + "double"].id, project)
        self.assertTrue(upstream)

    def test_trusting_a_package_trusts_what_it_depends_on(self):
        packages = [{"name": "a", "requires": ["b"]}, {"name": "b", "requires": ["c"]}, {"name": "c", "requires": []}]
        self.assertEqual(analysis.trusted_packages(packages, ["a"]), {"a", "b", "c"})


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.alice, self.bot = person("alice"), agent("carol")
        self.accept = review("double", self.alice)
        self.withdrawn = review("triple", self.alice)
        self.problem = review("triple", self.alice, verdict="problem", at="2026-09-25T11:00:00Z")
        self.agent_only = review("double_zero", self.bot)
        self.records = [self.accept, self.withdrawn, status(self.withdrawn, "withdrawn", self.alice),
                        self.problem, self.agent_only]
        self.ev = Evidence.resolve(self.records, B)

    def test_a_view_says_what_evidence_core_decided(self):
        [v] = views_on(self.ev, F + "double", "review")
        self.assertEqual((v["status"], v["applies"], v["state"], v["inForce"], v["url"]),
                         ("current", True, "stands", True, "https://github.com/o/r/issues/3"))
        self.assertEqual(v["by"], by_view(self.alice))
        self.assertEqual(v["actions"], ["withdraw"])
        withdrawn = next(v for v in views_on(self.ev, F + "triple", "review") if v["id"] == self.withdrawn["id"])
        self.assertEqual((withdrawn["state"], withdrawn["inForce"], withdrawn["actions"]), ("withdrawn", False, []))
        self.assertEqual(withdrawn["statuses"][0]["url"], "https://github.com/o/r/issues/3")

    def test_what_can_be_done_with_a_problem(self):
        self.assertEqual(actions(self.ev, self.problem), ["fixed", "intended", "invalid", "withdraw"])
        ev = Evidence.resolve(self.records + [status(self.problem, "fixed", self.alice)], B)
        self.assertEqual(actions(ev, self.problem), ["reopen"])

    def test_where_a_declaration_stands_under_each_policy(self):
        self.assertEqual(self.ev.decl_state(F + "double", Policy()), "covered")
        self.assertEqual(self.ev.decl_state(F + "triple", Policy()), "problem")
        self.assertEqual(self.ev.decl_state(F + "double_zero", Policy()), "uncounted")
        self.assertEqual(self.ev.why_uncounted(F + "double_zero", Policy()), "agents")
        self.assertEqual(self.ev.decl_state(F + "double_zero", Policy(agents=True)), "covered")
        self.assertEqual(self.ev.decl_state(F + "triple_one", Policy()), "unreviewed")
        keys = [policy_key(p) for p in all_policies()]
        self.assertEqual((len(keys), len(set(keys)), policy_key(Policy())), (16, 16, "0011"))

    def test_an_acceptance_of_an_earlier_version_is_stale(self):
        ev = Evidence.resolve([review("triple_one", self.alice, ds=A)], B, {A.commit: A})
        self.assertEqual(ev.decl_state(F + "triple_one", Policy()), "stale")


class OriginTests(unittest.TestCase):
    def test_every_kind_of_origin_links_to_its_issue_or_comment(self):
        self.assertEqual(rec.origin_issue("o/r#12/event/5"), ("o/r", 12))
        self.assertEqual(rec.origin_url({"ref": "o/r#12"}), "https://github.com/o/r/issues/12")
        self.assertEqual(rec.origin_url({"ref": "https://github.com/o/r/issues/7#issuecomment-99/line/2"}),
                         "https://github.com/o/r/issues/7#issuecomment-99")
        self.assertEqual(rec.origin_url({"kind": "zulip", "ref": "voyager 614"}), "")
        self.assertEqual(rec.origin_url(None), "")


if __name__ == "__main__":
    unittest.main()
