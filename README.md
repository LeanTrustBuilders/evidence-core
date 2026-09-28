# evidence-core

Pure functions over datasets (S2) and evidence records (S3), both specified in
[LeanTrustBuilders/specs](https://github.com/LeanTrustBuilders/specs): whether a review still applies,
what changed underneath it, whether a claim is covered, what to review next, and everything a page
shows about them. Python, standard library only.

## Install

```bash
pip install git+https://github.com/LeanTrustBuilders/evidence-core
```

or run it from a checkout with `python3 -m evidence_core`.

## What it computes

**Status of a record.** A record keeps the key of its subject when it was made: its name and two
hashes. Against a dataset of the current code, it is:

| status | meaning |
|---|---|
| `current` | same name, same meaning hash |
| `renamed` | the name is gone, but exactly one declaration has the same meaning hash (or, when several do, exactly one of the record's aspect): the record follows it |
| `stale-underneath` | same name and local hash, different meaning hash: written the same, but something it rests on changed. Given a dataset of the record's commit, the status names the dependencies that were rewritten |
| `stale` | same name, different local hash: the declaration itself changed |
| `unavailable` | the name is gone, and its module did not build at the dataset's commit (`library.unavailable`) |
| `orphaned` | neither the name nor the meaning hash exists any more |
| `incomparable` | the record's hashes come from another hasher |
| `unknown` | the record has no meaning hash |

The meaning and local hashes follow the rule that draws the `meaning` graph, so a record is stale
underneath exactly when something in its `meaning` closure changed, and the members whose local
hash changed are the ones to blame. The content hash decides no status; between two datasets it
tells a declaration whose only change is a proof in its closure.

**Threads.** The records about a declaration read as threads: each review with the comments replying
to it and the statuses about it. A problem or question is open until a status resolves it, and can be
reopened; a review can be withdrawn by its author or superseded by a later review of the same
reviewer. A review is **in force** when it applies to the current code and is neither withdrawn nor
superseded. An acceptance and an open problem in force on one declaration are a disagreement, shown
rather than resolved. Records are never anonymous: each names a GitHub account, or an AI agent
(`{tool, model, session}`), or both.

**Tests and challenges.** A `test` names a declaration of the library that tests another; a
`challenge` is a proposed test, open until it is met, failed, declined or withdrawn.
`Evidence.tests(name)` lists a declaration's tests, those of met challenges included, each
**passing** while the testing declaration is in the dataset without `sorry`, **with sorry**, or
**missing**.

**Coverage of a claim, under a reader's policy.** A claim is covered when every project declaration in
its `meaning` closure, the claim included, has an acceptance in force that the policy counts, and none
has an open problem. The policy says whose reviews count: AI agents or not, authors or not,
acceptances with caveats or not, acceptances stale underneath or not, and whether upstream
declarations must be reviewed too. Reviews are data; which ones count is the reader's choice.

**The review queue:** unreviewed declarations in the claims' closures, ranked by how many claims rest
on them, then by how many declarations use them.

**Evidence stores** (`store`): a directory `evidence/` in a git repository, append-only. `Store.add`
appends records by month; `store-check` checks a change against the revision before it: every new
record valid and by the account that made the change, nothing changed or removed. The GitHub side of
a store (issue forms and intake) is [evidence-store](https://github.com/LeanTrustBuilders/evidence-store).

**For pages.** A front end takes what it shows from here and only chooses how to display it:

- `views`: each record as a page shows it: who made it, where it came from, its status, its latest
  state, whether it is in force, its replies and statuses, and the changes of state it allows now;
- `Evidence.decl_state`: where a declaration stands under a policy (covered, uncounted, stale,
  unreviewed, problem, disputed); `all_policies()` and `policy_key` let a static page compute every
  policy once;
- `claims`: what a library claims, from a store's list, `formalization.yaml` (with
  `pip install 'evidence-core[yaml]'`), Comparator configs and `@[claim]`;
- `changes`: what changed between two datasets, as a returning reader needs it (statement, body,
  underneath, proof only, renamed, added, removed);
- `ledger`: provenance across builds, when each declaration's meaning last changed;
- `analysis`: closures split into project and upstream, whose `sorry` it is, specifications,
  characterizations, domains and well-definedness, the scope of a claims-only page;
- `pins`: what pins a definition down: in the code (`@[specifies]`, `@[example_of]` and
  `@[nonexample_of]`, characterizations), from reviewers (tests and met challenges, passing or not) and wanted (open
  challenges);
- `checks`: the kernel check's results (`check.kernel.<notion>`), per declaration and over a closure;
- `source`: a declaration's text from the `source` facet and a checkout, and its statement apart from
  its proof;
- `docs`: a library's own documentation: module docstrings as sections, docstrings' first sentences as
  titles, and the `attributes` facet as links (Stacks, Kerodon, Wikidata, deprecation);
- `catalogs`: the catalogues a library keeps beside its code (subject trees, famous theorems, a
  bibliography), matched against the dataset;
- `graphs`: the meaning and proof graphs summarized for readers who are not Lean experts: concepts and
  results, what each is built from and used by, basic notions and routine proof steps.

**Datasets.** `merge` adds a catalogue's dataset to that of the library it is about, so a site shows
the catalogue's annotations and analysis on the library's declarations.

## Command line

```bash
python3 -m evidence_core status   --dataset DS --records STORE [--at OLD_DS]
python3 -m evidence_core coverage --dataset DS --records STORE [--claim NAME] [--agents] [--stale-underneath]
python3 -m evidence_core queue    --dataset DS --records STORE [--claim NAME]
python3 -m evidence_core claims   --dataset DS [--source CHECKOUT] [--store evidence] [--json]
python3 -m evidence_core diff     --old DS1 --new DS2 [--json]
python3 -m evidence_core ledger   --ledger ledger.json --dataset DS [--date D] [--label L]
python3 -m evidence_core validate records.jsonl
python3 -m evidence_core store-check --repo . --base origin/main [--author LOGIN]
python3 -m evidence_core merge    --dataset LIB_DS --add CATALOGUE_DS --out MERGED_DS
python3 -m evidence_core check-graph   --old DS1 --new DS2 [--hash meaning|content] [--strict]
python3 -m evidence_core compare-rules --a DS_A --b DS_B
python3 -m evidence_core migrate  reviewed-by|referee|trust INPUT --dataset DS --out records.jsonl
```

`--records` takes a JSONL file or a store's directory. Two commands check the suite itself:
`check-graph`, over datasets of consecutive commits, lists the declarations whose hash and the graph
it follows disagree about whether something beneath them changed: the meaning hash and the `meaning`
graph, or with `--hash content`, the content hash and the `term` graph (it must find none);
`compare-rules` compares two datasets of one commit made under two rules. `migrate` converts
Reviewed-by's ledgers, Referee's audit exports and trust's marks to records; the last two record no
reviewer, so they need `--reviewer`.

## Library

```python
from evidence_core import Dataset, Evidence, Policy, coverage
from evidence_core import records

ds = Dataset.load("dataset")
ev = Evidence.resolve(records.load("evidence.jsonl"), ds)
print(coverage(ev, "MyLib.main_theorem", Policy(agents=True)).summary())
```

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests run on `tests/vectors`, datasets written by the extractor from a fixture in two versions
(its `test/run.sh DIR` writes them): version B changes a definition, rewrites a statement, changes
only a proof, and renames a binder and a theorem, so each status is exercised on real output.
