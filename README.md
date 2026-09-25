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

**Coverage of a claim, under a reader's policy.** A claim is covered when every project declaration
in its `meaning` closure, the claim included, has at least one acceptance that counts under the
policy, and none has an open problem. The policy says whose reviews count: AI agents or not, authors
or not, acceptances with caveats or not, acceptances that are stale underneath or not, and whether
upstream declarations must be reviewed too. Reviews are data; which ones count is the reader's
choice.

**The review queue:** unreviewed declarations in the claims' closures, ranked by how many claims
rest on them, then by how many declarations use them.

**Migration** from existing tools: Reviewed-by's ledgers, Referee's audit exports, trust's marks.

## Command line

```bash
python3 -m evidence_core status   --dataset DS --records evidence.jsonl [--at OLD_DS]
python3 -m evidence_core coverage --dataset DS --records evidence.jsonl [--claim NAME] [--agents] [--stale-underneath]
python3 -m evidence_core queue    --dataset DS --records evidence.jsonl [--claim NAME]
python3 -m evidence_core claims   --dataset DS
python3 -m evidence_core diff     --old DS1 --new DS2 [--json]
python3 -m evidence_core validate evidence.jsonl
python3 -m evidence_core migrate  reviewed-by path/to/reviews/ --dataset DS --at COMMIT=DS_AT_COMMIT --repo OWNER/NAME --out evidence.jsonl
```

`diff` classifies every project declaration of the first dataset against the second, which is how
the stability of the hashes is measured between commits.

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
