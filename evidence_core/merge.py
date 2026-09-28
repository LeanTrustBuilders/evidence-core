"""Merging a catalogue's dataset into the dataset of the library it is about.

A *catalogue* is a Lean package that says things about another library's declarations from outside
it: their domains (`@[domain]`), and specifications and characterizations stated by its own theorems.
Its dataset (S2) is small: its own declarations are the project, and the library's declarations
they mention are upstream nodes, carrying the catalogue's annotations. `merge` adds it to the
library's dataset, so that a site built from the result shows the catalogue's evidence on the
library's own declarations, without extracting the library again when the catalogue changes.

What is added:

* the catalogue's declarations the library's dataset lacks, with new ids after the library's, and
  their scope, hashes and edges (remapped by name);
* the catalogue's facet rows for those declarations, and its annotation rows (`annotation.*`) and
  well-definedness analysis (`welldefined`) for the library's declarations too: those are the
  catalogue's claims about them, and what follows from them. A row about a declaration the
  merged dataset does not have is left out;
* its modules and packages, and a record of the merge in the metadata (`merged`).

Both datasets must describe the same library: every declaration they share must have the same
meaning hash, which is checked. A catalogue built against another commit of the library would
otherwise attach its evidence to declarations that have since changed. The added nodes keep their
content hashes only if the two datasets have the same content hasher: the merged dataset's
`meta.json` names the library's.
"""
from __future__ import annotations

import json
import shutil
import sys
from array import array
from pathlib import Path

from .dataset import Dataset


def merge(base_dir: str | Path, add_dir: str | Path, out_dir: str | Path) -> dict:
    """Writes the merge of the catalogue dataset `add_dir` into `base_dir` at `out_dir`, and returns a
    summary. Raises ValueError when the two disagree on a shared declaration's meaning."""
    base, add = Dataset.load(base_dir), Dataset.load(add_dir)
    mismatched = [d.name for d in add.decls
                  if d.name in base.by_name and d.meaning and base.by_name[d.name].meaning
                  and d.meaning != base.by_name[d.name].meaning]
    if mismatched:
        raise ValueError(f"the catalogue was not built against this library: {len(mismatched)} shared "
                         f"declarations mean something else, e.g. {', '.join(mismatched[:5])}")

    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base.root, out)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))

    # --- nodes: the catalogue's that the library lacks, after the library's own ------------------
    next_id = len(base.decls)
    idmap: dict[int, int] = {}
    added: list[int] = []
    with (add.root / "decls.jsonl").open(encoding="utf-8") as src, \
            (out / "decls.jsonl").open("a", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["name"] in base.by_name:
                idmap[row["id"]] = base.by_name[row["name"]].id
                continue
            idmap[row["id"]] = next_id
            added.append(row["id"])
            row["id"] = next_id
            next_id += 1
            dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    new = set(added)
    if add.content_hasher != base.content_hasher:
        _drop_content(out / "decls.jsonl", {idmap[i] for i in added})

    # --- edges: those leaving the added nodes, remapped --------------------------------------------
    edge_counts: dict[str, int] = {}
    for entry in meta.get("edges", []):
        notion = entry["name"]
        try:
            adj = add.edges(notion)
        except KeyError:
            continue
        pairs = array("i")
        for s in added:
            for t in adj.get(s, ()):
                pairs.extend((idmap[s], idmap[t]))
        if pairs:
            with (out / entry["file"]).open("ab") as f:
                f.write(pairs.tobytes() if sys.byteorder == "little" else _swapped(pairs))
        entry["count"] = entry.get("count", 0) + len(pairs) // 2
        edge_counts[notion] = len(pairs) // 2

    # --- facets: rows of the added nodes, and what the catalogue says about the library's ---------
    facet_rows: dict[str, int] = {}
    add_ids = {d.name: d.id for d in add.decls}
    known = set(base.by_name) | {add.decls[i].name for i in added}
    for entry in add.meta.get("facets", []):
        name = entry["name"]
        about_library = name.startswith("annotation.") or name == "welldefined"
        rows = []
        for decl, rs in add.facet(name).items():
            i = add_ids.get(decl)
            if (i is not None and i in new) or (about_library and decl in known):
                rows.extend(rs)
        if not rows:
            continue
        mine = next((f for f in meta.get("facets", []) if f["name"] == name), None)
        if mine is None:
            mine = dict(entry)
            meta.setdefault("facets", []).append(mine)
            mine["count"] = 0
        with (out / mine["file"]).open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        mine["count"] = mine.get("count", 0) + len(rows)
        facet_rows[name] = len(rows)

    # --- modules and packages ----------------------------------------------------------------------
    have = {m["name"] for m in base.modules}
    extra = [m for m in add.modules if m["name"] not in have]
    if extra:
        with (out / "modules.jsonl").open("a", encoding="utf-8") as f:
            for m in extra:
                f.write(json.dumps(m, ensure_ascii=False, separators=(",", ":")) + "\n")
    names = {p["name"] for p in meta.get("packages", [])}
    meta.setdefault("packages", []).extend(p for p in add.packages if p["name"] not in names)
    if "modules" in meta and isinstance(meta["modules"], dict):
        meta["modules"]["count"] = meta["modules"].get("count", 0) + len(extra)

    counts = meta.setdefault("counts", {})
    project = sum(1 for i in added if add.decls[i].is_project)
    counts["nodes"] = counts.get("nodes", len(base.decls)) + len(added)
    counts["project"] = counts.get("project", 0) + project
    counts["upstream"] = counts.get("upstream", 0) + len(added) - project
    meta.setdefault("merged", []).append({"library": add.meta.get("library"), "producer": add.meta.get("producer"),
                                          "nodes": len(added), "facetRows": facet_rows})
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"nodes": len(added), "project": project, "edges": edge_counts, "facets": facet_rows,
            "modules": len(extra), "shared": len(idmap) - len(added)}


def _drop_content(path: Path, ids: set[int]) -> None:
    """Removes the content hash of the nodes `ids`, hashed by another content hasher."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in ids:
            row.get("hashes", {}).pop("content", None)
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _swapped(pairs: array) -> bytes:
    swapped = array("i", pairs)
    swapped.byteswap()
    return swapped.tobytes()
