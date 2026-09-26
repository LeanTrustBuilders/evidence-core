"""A library's own documentation (docs), its catalogues (catalogs), and its graphs summarized for
readers (graphs). On the extractor's fixture (tests/vectors, see test_core.py) and small catalogue
files written here in Mathlib's formats."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from evidence_core import Dataset
from evidence_core import catalogs, docs
from evidence_core.graphs import Graphs, is_auxiliary, is_machinery

V = Path(__file__).parent / "vectors"
B = Dataset.load(V / "fixture-b")
F = "Fixture."

MODULE_DOC = """# Doubling

This file defines doubling and proves what it is.

## Main definitions

* `double`: twice a number.
* `IsDouble`: being twice something.

## Main results

- `double_triple`: doubling and tripling, together.
- `Fixture.isDouble_double`, `nothing_here`: every double is a double.

## References

* [A. Author, *A book*][author2020]

## Tags

doubling, twice, multiplication by two
"""


class ModuleDocTests(unittest.TestCase):
    def setUp(self):
        self.row = {"name": "Fixture.Basic", "doc": [MODULE_DOC]}
        # The fixture's declarations live in its modules; resolve against those of the module named.
        self.module = B.by_name[F + "double"].module
        self.row["name"] = self.module

    def test_the_sections_of_a_module_docstring(self):
        md = docs.module_doc(B, self.row)
        self.assertEqual((md.title, md.summary), ("Doubling", "This file defines doubling and proves what it is."))
        self.assertEqual(list(md.sections), ["Main definitions", "Main results", "References", "Tags"])
        self.assertEqual(md.tags, ["doubling", "twice", "multiplication by two"])
        self.assertEqual(md.references, ["author2020"])

    def test_the_main_declarations_resolved_as_a_reader_would(self):
        md = docs.module_doc(B, self.row)
        self.assertEqual(md.definitions[0].names, [F + "double"])        # under the module's namespace
        self.assertEqual(md.definitions[0].text, "`double`: twice a number.")
        self.assertEqual(md.results[1].names, [F + "isDouble_double"])   # as written; `nothing_here` resolves to nothing
        self.assertEqual(md.names["double"], F + "double")
        self.assertNotIn("nothing_here", md.names)

    def test_a_docstrings_first_sentence_is_its_title(self):
        self.assertEqual(docs.first_sentence("The Gamma function $\\Gamma(s)$. It extends."), "The Gamma function $\\Gamma(s)$.")
        self.assertEqual(docs.first_sentence("Stacks Tag 09HR\n\nAutomorphisms."), "Automorphisms.")
        self.assertEqual(docs.first_sentence("Wikidata Q83478"), "")


class AttributeTests(unittest.TestCase):
    def test_links_and_deprecation_from_the_attributes_facet(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds_dir = Path(tmp) / "ds"
            shutil.copytree(V / "fixture-b", ds_dir)
            meta = json.loads((ds_dir / "meta.json").read_text())
            meta["facets"].append({"name": "attributes", "file": "facets/attributes.jsonl", "schema": "attributes/1", "count": 2})
            (ds_dir / "meta.json").write_text(json.dumps(meta))
            (ds_dir / "facets" / "attributes.jsonl").write_text(
                json.dumps({"decl": F + "double", "attributes": [{"name": "stacks", "args": '09GA "the doubling map"'},
                                                                 {"name": "wikidata", "args": "Q616608"}, {"name": "simp", "args": ""}]}) + "\n" +
                json.dumps({"decl": F + "triple", "attributes": [{"name": "deprecated", "args": '(since := "2026-01-01")'}]}) + "\n")
            ds = Dataset.load(ds_dir)
            self.assertEqual(docs.links(ds, F + "double").as_json(),
                             {"stacks": [{"tag": "09GA", "comment": "the doubling map"}], "wikidata": ["Q616608"]})
            self.assertEqual(docs.deprecated(ds), {F + "triple"})


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_subject_tree_with_concepts_formalized_or_not(self):
        (self.dir / "overview.yaml").write_text(f"Arithmetic:\n  Doubling:\n    double: '{F}double'\n    halving: ''\n  tripling: '{F}triple'\n")
        t = catalogs.topic_tree(self.dir / "overview.yaml", B, "Overview")
        [arith] = t.children
        self.assertEqual([e.title for e in arith.entries], ["tripling"])
        self.assertEqual([(e.title, e.found) for e in arith.children[0].entries], [("double", [F + "double"]), ("halving", [])])

    def test_famous_theorems_by_number_or_by_wikidata_item(self):
        (self.dir / "100.yaml").write_text(f"1:\n  title  : Doubling is tripling\n  decl   : {F}double_triple\n  authors: Someone\n")
        (self.dir / "1000.yaml").write_text(f"Q26708:\n  title: Binomial theorem\n  decls:\n    - {F}gone\n    - {F}triple_pos\n  date: 2024\n")
        [h] = catalogs.famous_theorems(self.dir / "100.yaml", B)
        self.assertEqual((h.title, h.found, h.meta["id"], h.meta["authors"]), ("Doubling is tripling", [F + "double_triple"], "1", "Someone"))
        [t] = catalogs.famous_theorems(self.dir / "1000.yaml", B)
        self.assertEqual((t.found, t.meta["wikidata"], t.meta["date"]), ([F + "triple_pos"], "Q26708", "2024"))

    def test_a_bibliography(self):
        (self.dir / "references.bib").write_text("""
@Book{atiyah-macdonald,
  author    = {Atiyah, M. F. and Macdonald, I. G.},
  title     = {Introduction to commutative algebra},
  publisher = {Addison-Wesley},
  year      = {1969},
}
@Article{ Some_Key:2020 , title = "A {\\em short} paper", author = {Noether, Emmy}, year = 1921 }
""")
        bib = catalogs.bibliography(self.dir / "references.bib")
        self.assertEqual(bib["atiyah-macdonald"]["authors"], ["M. F. Atiyah", "I. G. Macdonald"])
        self.assertEqual(catalogs.cite(bib["atiyah-macdonald"]), "Atiyah, Macdonald, Introduction to commutative algebra (1969)")
        self.assertEqual((bib["Some_Key:2020"]["title"], bib["Some_Key:2020"]["year"]), ("A short paper", "1921"))


class GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = Graphs(B)
        cls.r = cls.g.reverse()

    def names(self, ids):
        return [B.decls[i].name for i in ids]

    def test_concepts_and_results(self):
        g = self.g
        self.assertTrue(g.concept[B.by_name[F + "double"].id] and g.result[B.by_name[F + "double_triple"].id])
        self.assertTrue(is_auxiliary(F + "double.match_1") and is_auxiliary(F + "foo.eq_1") and not is_auxiliary(F + "double"))
        self.assertTrue(is_machinery("Mathlib.Tactic.Ring.of_eq") and not is_machinery("Nat.Prime"))

    def test_what_a_theorem_is_about_and_what_a_concept_is_built_from(self):
        about = self.names(self.g.about(B.by_name[F + "double_triple"].id))
        self.assertIn(F + "double", about)
        self.assertIn(F + "triple", about)
        self.assertIn("Nat", self.names(self.g.built_from(B.by_name[F + "double"].id)))

    def test_basic_notions_are_what_the_library_builds_on(self):
        g = self.g
        self.assertTrue(g.basic[B.by_name["Nat"].id])
        self.assertFalse(g.basic[B.by_name[F + "double"].id])

    def test_the_reverse_facts_about_a_concept(self):
        facts = self.names(self.r.facts[B.by_name[F + "double"].id])
        self.assertIn(F + "double_triple", facts)
        self.assertIn(F + "double_zero", facts)


if __name__ == "__main__":
    unittest.main()
