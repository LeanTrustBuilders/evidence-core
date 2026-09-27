"""Merging a catalogue's dataset into a library's (evidence_core.merge).

The catalogue here is written by hand: one theorem of its own (`Catalogue.double_spec`), and the
library's `Fixture.double` as an upstream node, with a domain declared for it and a characterization
stated by the catalogue's theorem.

Run with ``python3 -m unittest discover -s tests``.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from array import array
from pathlib import Path

from evidence_core import Dataset, analysis
from evidence_core.merge import merge

V = Path(__file__).parent / "vectors"
F = "Fixture."


def write_catalogue(root: Path, double_meaning: str) -> None:
    """A dataset of a catalogue about the fixture library."""
    (root / "edges").mkdir(parents=True)
    (root / "facets").mkdir()
    decls = [
        {"id": 0, "name": "Catalogue.double_spec", "module": "Catalogue.Basic", "package": "Catalogue",
         "scope": "project", "kind": "theorem", "isProp": True, "hashes": {"meaning": "aaaa", "local": "bbbb"}},
        {"id": 1, "name": F + "double", "module": "Fixture.Basic", "package": "Fixture", "scope": "upstream",
         "kind": "definition", "isProp": False, "hashes": {"meaning": double_meaning}},
    ]
    (root / "decls.jsonl").write_text("".join(json.dumps(d) + "\n" for d in decls))
    (root / "modules.jsonl").write_text(json.dumps({"name": "Catalogue.Basic", "imports": ["Fixture.Basic"]}) + "\n")
    (root / "edges" / "meaning.bin").write_bytes(array("i", [0, 1]).tobytes())
    facets = {
        "annotation.domain": [{"decl": F + "double", "entries": [{"statement": "n < 100", "note": "small",
                                                                  "source": "catalogue", "predicate": "Fixture.double._domain"}]}],
        "annotation.up_to": [{"decl": F + "double", "entries": [{"statement": "x % 2 = y % 2", "relationHead": "Eq",
                                                                 "note": "", "source": "catalogue", "relation": "Fixture.double._upTo"}]}],
        "annotation.characterization": [{"decl": "Catalogue.double_spec", "entries": [
            {"role": "theorem", "property": "Catalogue.double_spec", "target": F + "double", "relation": "m = double n",
             "form": "iff", "conditions": [{"text": "m = n + n", "proved": True, "by": [], "assuming": []}],
             "context": [], "variables": ["n : Nat"], "complete": True}]}],
        "welldefined": [{"decl": F + "double_zero", "obligations": [
                            {"kind": "domain", "op": F + "double", "source": "catalogue", "place": "conclusion",
                             "term": "double 0", "goal": "0 < 100", "status": "discharged", "by": "omega"},
                            {"kind": "domain", "op": F + "double", "source": "catalogue", "place": "conclusion",
                             "term": "double n", "goal": "n < 100", "status": "open"},
                            {"kind": "domain", "op": F + "double", "source": "catalogue", "place": "hypothesis",
                             "name": "h", "index": 1, "term": "double n", "goal": "n < 100", "status": "open"}]},
                        {"decl": "Elsewhere.not_in_the_library", "obligations": []}],
        "docstring": [{"decl": "Catalogue.double_spec", "doc": "Twice, by the catalogue."},
                      {"decl": F + "double", "doc": "the catalogue's copy of the library's docstring"}],
    }
    for name, rows in facets.items():
        (root / "facets" / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    meta = {"spec": "ltb-dataset/1", "toolchain": "leanprover/lean4:v4.34.0",
            "library": {"root": "Catalogue", "package": "Catalogue"},
            "packages": [{"name": "Catalogue", "requires": ["Fixture"], "modules": 1}],
            "edges": [{"name": "meaning", "format": "i32le-pairs", "file": "edges/meaning.bin", "count": 1}],
            "facets": [{"name": n, "file": f"facets/{n}.jsonl", "schema": "annotation/2" if n.startswith("annotation") else f"{n}/1",
                        "count": len(r), **({"dischargers": ["omega"]} if n == "welldefined" else {})}
                       for n, r in facets.items()],
            "counts": {"nodes": 2, "project": 1, "upstream": 1}}
    (root / "meta.json").write_text(json.dumps(meta))


class MergeTests(unittest.TestCase):
    def test_a_catalogue_merged_into_its_library(self):
        base = Dataset.load(V / "fixture-b")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_catalogue(tmp / "cat", base.by_name[F + "double"].meaning)
            s = merge(V / "fixture-b", tmp / "cat", tmp / "out")
            self.assertEqual((s["nodes"], s["project"], s["shared"]), (1, 1, 1))
            ds = Dataset.load(tmp / "out")
            # the catalogue's theorem is a node after the library's, with its edge remapped by name
            thm = ds.by_name["Catalogue.double_spec"]
            self.assertEqual(thm.id, len(base.decls))
            self.assertTrue(thm.is_project)
            self.assertEqual(ds.successors("Catalogue.double_spec"), [F + "double"])
            # the catalogue's annotations of the library's declaration are kept, its copies of the
            # library's other facets are not
            self.assertEqual(analysis.domains(ds)[F + "double"], {"statement": "n < 100", "note": "small", "source": "catalogue"})
            # the fixture's own characterization of `double`, by a predicate, and the catalogue's
            [c] = [c for c in analysis.characterizations(ds)[F + "double"] if c["property"] == "Catalogue.double_spec"]
            self.assertEqual(analysis.up_to(ds)[F + "double"],
                             {"statement": "x % 2 = y % 2", "note": "", "relationHead": "Eq", "source": "catalogue"})
            self.assertEqual((c["property"], c["complete"], c["variables"]), ("Catalogue.double_spec", True, ["n : Nat"]))
            self.assertEqual(ds.facet_row("docstring", "Catalogue.double_spec")["doc"], "Twice, by the catalogue.")
            self.assertEqual(ds.facet_row("docstring", F + "double"), base.facet_row("docstring", F + "double"))
            self.assertEqual(ds.meta["merged"][0]["library"]["root"], "Catalogue")
            # its theorem pins the definition down, from a catalogue: not written by the library's authors
            from evidence_core.pins import Pins
            by_source = {(p["source"], p["decl"]) for p in Pins(ds).of(F + "double") if p.get("decl")}
            self.assertIn(("catalogue", "Catalogue.double_spec"), by_source)
            self.assertIn(("code", F + "IsDouble"), by_source)
            self.assertEqual(ds.meta["counts"]["nodes"], len(base.decls) + 1)
            # its analysis of the library's statements is kept, for the declarations the library has
            wd = analysis.well_definedness(ds)
            # what is left, each goal once
            self.assertEqual((wd[F + "double_zero"]["counts"], wd[F + "double_zero"]["left"]),
                             ({"discharged": 1, "open": 2}, ["n < 100"]))
            self.assertNotIn("Elsewhere.not_in_the_library", wd)
            self.assertEqual(analysis.well_definedness_meta(ds)["dischargers"], ["omega"])

    def test_a_catalogue_of_another_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_catalogue(tmp / "cat", "0123456789abcdef")
            with self.assertRaises(ValueError):
                merge(V / "fixture-b", tmp / "cat", tmp / "out")


if __name__ == "__main__":
    unittest.main()
