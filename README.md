# evidence-core

Pure functions over [LeanTrustBuilders](https://github.com/LeanTrustBuilders) datasets (S2, written
by the [extractor](https://github.com/LeanTrustBuilders/extractor)) and evidence records (S3): whether
a review still applies, what changed underneath it, whether a claim is covered, and what to review
next. Python, standard library only.

The specifications are in [LeanTrustBuilders/specs](https://github.com/LeanTrustBuilders/specs);
the design is in [the design notes](https://github.com/LeanTrustBuilders/design/tree/main/AI_initial_docs)
(`reviews.md`, `suite-design.md`).

## Install

```bash
pip install git+https://github.com/LeanTrustBuilders/evidence-core
```

or run it from a checkout with `python3 -m evidence_core`.

## What it computes

**Status of a record.** A record stores the key of its subject when it was made: its name, and three
hashes (meaning, local, content). Against a dataset of the current code, a record is:

| status | meaning |
|---|---|
| `current` | same name, same meaning hash |
| `renamed` | the name is gone, but exactly one declaration has the same meaning hash: the record follows it |
| `stale-underneath` | same name and local hash, different meaning hash: written the same, but something it rests on changed. Given a dataset of the record's commit, the status names the dependencies that were rewritten |
| `stale` | same name, different local hash: the declaration itself changed |
| `unavailable` | the name is gone, and the declaration's module did not build at the dataset's commit (the dataset lists it in `library.unavailable`): the record cannot be checked |
| `orphaned` | neither the name nor the meaning hash exists any more |
| `incomparable` | the record's hashes come from another hasher revision |
| `unknown` | the record carries no meaning hash |

**Hashes and the graph.** Datasets of `ltb-dataset/1` compute the meaning and local hashes with the
rule that draws the `meaning` graph (`ltb-meaning/1`), so a record is stale underneath exactly when
something in its `meaning` closure changed, and the closure members whose local hash changed are the
ones to blame. Records keyed by the hashes of `ltb-dataset/0` (semantic_hash's) still resolve: given
a dataset of the record's commit that carries both (every `ltb-dataset/1` dataset keeps the old
hashes as `legacy`), the record is re-keyed through it and judged like a new one; without one, it is
compared with the current dataset's legacy hashes.

**Threads.** The records about a declaration read as threads: each review with the comments
replying to it and the statuses about it. A problem or question is open until a status resolves it
(`fixed`, `intended`, `invalid`, `answered`), and can be reopened; a review can be withdrawn by its
author, or superseded by a later review of the same reviewer. A review is **in force** when it
applies to the current code and is neither withdrawn nor superseded. An acceptance and an open
problem in force on one declaration are a disagreement, shown rather than resolved.

**Identities.** Records are never anonymous: each names the GitHub account it came from, or is
labelled as an AI agent's (`{tool, model, session}`), or both, for an agent acting through an
account.

**Coverage of a claim, under a reader's policy.** A claim is covered when every project declaration
in its `meaning` closure, the claim included, has at least one acceptance in force that counts under
the policy, and none has an open problem. The policy says whose reviews count: AI agents or not, authors
or not, acceptances with caveats or not, acceptances that are stale underneath or not, and whether
upstream declarations must be reviewed too. Reviews are data; which ones count is the reader's
choice.

**The review queue:** unreviewed declarations in the claims' closures, ranked by how many claims
rest on them, then by how many declarations use them.

**Evidence stores** (`evidence_core.store`): a directory `evidence/` in a git repository, with a
`store.json` and records in `*.jsonl` files, append-only. `Store.add` appends records by month;
`store-check` checks a change to a store against the revision before it: every record valid, nothing
changed or removed, and every new record by the account that made the change. The GitHub side of a
store (issue forms, intake from issues and comments, the pull-request check) is
[evidence-store](https://github.com/LeanTrustBuilders/evidence-store).

**Migration** from existing tools: Reviewed-by's ledgers, Referee's audit exports, trust's marks.
Referee's audits and trust's marks record no reviewer, so their migration needs the reviewer's
GitHub login.

## Command line

```bash
python3 -m evidence_core status   --dataset DS --records evidence.jsonl [--at OLD_DS]
python3 -m evidence_core coverage --dataset DS --records evidence.jsonl [--claim NAME] [--agents] [--stale-underneath]
python3 -m evidence_core queue    --dataset DS --records evidence.jsonl [--claim NAME]
python3 -m evidence_core claims   --dataset DS
python3 -m evidence_core diff     --old DS1 --new DS2 [--json]
python3 -m evidence_core validate evidence.jsonl
python3 -m evidence_core check-graph   --old DS1 --new DS2 [--strict]
python3 -m evidence_core compare-rules --a DS_RULE_A --b DS_RULE_B
python3 -m evidence_core store-check --repo . --base origin/main [--author LOGIN]
python3 -m evidence_core migrate  reviewed-by path/to/reviews/ --dataset DS --at COMMIT=DS_AT_COMMIT --repo OWNER/NAME --out evidence.jsonl
```

`--records` takes a JSONL file or an evidence store's directory. `diff` classifies every project
declaration of the first dataset against the second, which is how the stability of the hashes is
measured between commits.

Two self-checks of the suite (dependency-testing.md §9 in the design notes):
- `check-graph` (check 1): over two datasets of consecutive commits, the declarations whose meaning
  hash and meaning graph disagree about whether something beneath them changed, each with the path
  to look at. Under `ltb-meaning/1` it must find nothing; `--strict` exits 1 if it does.
- `compare-rules`: two datasets of one commit under two rules, for instance `ltb-dataset/0` and
  `ltb-dataset/1`: the declarations, edges and closures one has and the other lacks, by kind.

## Library

```python
from evidence_core import Dataset, Evidence, Policy, coverage, classify
from evidence_core import records

ds = Dataset.load("dataset")
ev = Evidence.resolve(records.load("evidence.jsonl"), ds)
print(coverage(ev, "TauCeti.Foo.main_theorem", Policy(agents=True)).summary())
```

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests run on `tests/vectors/fixture-a` and `fixture-b`, two datasets written by the extractor
from a fixture in two versions (the extractor's `test/run.sh VECTORS_DIR` regenerates them). Version
B changes a definition, rewrites a statement, changes only a proof, renames a binder, and renames a
theorem, so each status above is exercised on real extractor output.
