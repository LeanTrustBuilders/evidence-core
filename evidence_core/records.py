"""S3 evidence records (spec ``ltb-evidence/2``).

Records are JSON objects, stored one per line (JSONL), append-only. Every record has:

* ``schema``: ``"ltb-evidence/2"``;
* ``kind``: ``review``, ``comment``, ``status``, ``test``, ``challenge`` or ``named`` in this
  version;
* ``id``: the first 16 hex digits of the SHA-256 of the record's canonical form, which excludes
  ``id`` and ``signature``;
* ``subject`` (reviews, tests, challenges, named results; optional for comments): the S1 key of the
  declaration the record is about, and its ``aspect``;
* ``text``: the record's words (a review's reasons, a comment, a status's note, what a test checks,
  a challenge's property, what a named result is);
* ``by``: who made it, which is never anonymous: a GitHub account (``identity``), or an AI agent
  (``agent``), or an agent acting through a GitHub account (both); ``at``: when (RFC 3339, UTC);
  ``origin``: where it came from;
* ``rubric``, when the record names axes (a problem's ``category``, ``checked``, ``caveats``, a
  challenge's ``modes``): the rubric they are axes of (``rubric.py``).

The canonical form is the JSON encoding with sorted keys, no whitespace, and non-ASCII characters
written as themselves: RFC 8785 for records, whose keys are ASCII and numbers integers.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from . import rubric as rb

SCHEMA = "ltb-evidence/2"
KINDS = ("review", "comment", "status", "test", "challenge", "named")
VERDICTS = ("accept", "problem", "question")
ASPECTS = ("definition", "statement", "instance")
CHECK_STATES = ("checked", "unchecked", "na")
STATES = ("fixed", "intended", "invalid", "answered", "met", "failed", "declined", "reopened", "withdrawn")
#: What each state may be set on: a review of that verdict, or a record of that kind.
STATE_TARGETS = {"fixed": ("problem",), "intended": ("problem",), "invalid": ("problem",),
                 "answered": ("question",), "met": ("challenge",), "failed": ("challenge",),
                 "declined": ("challenge",), "reopened": ("problem", "question", "challenge"),
                 "withdrawn": ("accept", "problem", "question", "test", "challenge", "named")}
NAMED_WHAT = ("result", "definition")
REVIEWER_KINDS = ("person", "agent")
IDENTITY_KINDS = ("github",)
INVOLVEMENT = ("author", "contributor", "outsider", "unknown")


def parse_agent(label: str) -> dict:
    """An agent given as a label, as Reviewed-by writes it ("Claude Code, Opus 5, session 0957"), as
    ``{tool, model, session}``."""
    parts = [p.strip() for p in label.split(",") if p.strip()]
    out: dict = {"tool": parts[0] if parts else "unknown agent"}
    for p in parts[1:]:
        if p.lower().startswith("session "):
            out["session"] = p[len("session "):].strip()
        elif "model" not in out:
            out["model"] = p
    return out


def agent_label(agent: dict | str | None) -> str:
    """How to show an agent: "Claude Code (claude-opus-5-5)"."""
    if not agent:
        return ""
    if isinstance(agent, str):
        return agent
    return agent.get("tool", "agent") + (f" ({agent['model']})" if agent.get("model") else "")


def origin_issue(ref: str) -> tuple[str, int | None]:
    """(repository, issue number) of a record's origin reference (S3 ``origin.ref``): ``owner/repo#12``,
    ``owner/repo#12/event/5`` (a close or reopen), or the URL of an issue or of a comment on one (with
    ``/line/k`` for one line of a comment)."""
    m = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", ref or "")
    if m:
        return m.group(1), int(m.group(2))
    repo, _, rest = (ref or "").partition("#")
    number = re.match(r"\d+", rest)
    return (repo, int(number.group(0))) if number and "/" in repo else (repo, None)


def origin_url(origin: dict | None) -> str:
    """Where a record came from, as a link: the comment it was read from, else its issue; empty for
    an origin that is not on GitHub."""
    ref = (origin or {}).get("ref", "")
    if ref.startswith("https://"):
        return re.sub(r"/line/\d+$", "", ref)
    repo, number = origin_issue(ref)
    return f"https://github.com/{repo}/issues/{number}" if repo and number else ""


def who(by: dict) -> str:
    """How to show a record's author: the GitHub login, and the agent if it is one."""
    login = (by.get("identity") or {}).get("id", "")
    if by.get("kind") == "agent":
        label = agent_label(by.get("agent"))
        return f"{label} via {login}" if login else label
    return login


def same_reviewer(a: dict, b: dict) -> bool:
    """Whether two ``by`` fields name the same reviewer: the same account, and for agents the same
    tool and model."""
    ia, ib = (a.get("identity") or {}).get("id"), (b.get("identity") or {}).get("id")
    if ia != ib or a.get("kind") != b.get("kind"):
        return False
    if a.get("kind") == "agent":
        ga, gb = a.get("agent") or {}, b.get("agent") or {}
        if isinstance(ga, str) or isinstance(gb, str):
            return ga == gb
        return (ga.get("tool"), ga.get("model")) == (gb.get("tool"), gb.get("model"))
    return True


def canonical(record: dict) -> str:
    """The canonical JSON text of a record: sorted keys, no whitespace, without id and signature."""
    body = {k: v for k, v in record.items() if k not in ("id", "signature")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_id(record: dict) -> str:
    return hashlib.sha256(canonical(record).encode("utf-8")).hexdigest()[:16]


def with_id(record: dict) -> dict:
    """The record with its ``id`` set (or checked, if already present)."""
    out = dict(record)
    out["id"] = record_id(out)
    return out


def aspect_of(decl) -> str:
    """What of a declaration a record is about by default: ``instance`` for an instance,
    ``statement`` for a proof, ``definition`` otherwise (S3)."""
    return "instance" if decl.kind == "instance" else ("statement" if decl.is_prop else "definition")


def subject_from_decl(decl, dataset, aspect: str | None = None) -> dict:
    """The S1 key of a dataset node, as a record's ``subject``: without the content hash, which
    decides nothing about a record (S1)."""
    return {
        "name": decl.name, "module": decl.module, "package": decl.package,
        "commit": dataset.commit, "toolchain": dataset.toolchain,
        "hasher": {"meaning": dataset.hasher.get("meaning"), "local": dataset.hasher.get("local")},
        "hashes": {k: v for k, v in (("meaning", decl.meaning), ("local", decl.local)) if v},
        "aspect": aspect or aspect_of(decl),
    }


def readable(record: dict) -> bool:
    """Whether a reader of this version can use a record: of a version it reads, of a kind it
    knows, and with a verdict or state it knows (S3, "Compatibility")."""
    if record.get("schema") != SCHEMA or record.get("kind") not in KINDS:
        return False
    if record.get("kind") == "review" and record.get("verdict") not in VERDICTS:
        return False
    if record.get("kind") == "status" and record.get("state") not in STATES:
        return False
    return True


def validate(record: dict, rubrics: dict[str, rb.Rubric] | None = None) -> list[str]:
    """Problems with a record, as messages. Empty when the record is valid. The axes it names are
    checked against its rubric when that is one of ``rubrics`` (by name; the standard one always)."""
    errs: list[str] = []

    def need(key: str, where: dict = record, path: str = "") -> bool:
        if key not in where or where[key] in (None, ""):
            errs.append(f"missing {path}{key}")
            return False
        return True

    if record.get("schema") != SCHEMA:
        errs.append(f"schema is {record.get('schema')!r}, expected {SCHEMA!r}")
    kind = record.get("kind")
    if kind not in KINDS:
        errs.append(f"unknown kind {kind!r}")
    if "id" in record and record["id"] != record_id(record):
        errs.append("id does not match the canonical form")
    need("at")
    errs += _by_errors(record)
    agent = (record.get("by") or {}).get("kind") == "agent"
    if kind in ("review", "test", "challenge", "named"):
        if need("subject"):
            s = record["subject"]
            for k in ("name", "commit"):
                need(k, s, "subject.")
            if s.get("aspect", "definition") not in ASPECTS:
                errs.append(f"subject.aspect must be one of {ASPECTS}")
    if kind == "review":
        verdict = record.get("verdict")
        if verdict not in VERDICTS:
            errs.append(f"verdict must be one of {VERDICTS}")
        if verdict == "problem":
            need("category")
            if not record.get("text"):
                errs.append("a problem needs its text: why")
        if agent and not record.get("text"):
            errs.append("a review by an agent needs its text: why")
        for f, state in (record.get("checked") or {}).items():
            if state not in CHECK_STATES:
                errs.append(f"checked.{f} must be one of {CHECK_STATES}")
    elif kind == "comment":
        need("text")
        if not (record.get("links") or {}).get("replies_to"):
            errs.append("a comment needs links.replies_to")
    elif kind == "status":
        need("target")
        if record.get("state") not in STATES:
            errs.append(f"state must be one of {STATES}")
    elif kind == "test":
        if need("test"):
            need("name", record["test"], "test.")
        if agent and not record.get("text"):
            errs.append("a test listed by an agent says what it checks (text)")
    elif kind == "challenge":
        need("text")
    elif kind == "named":
        need("name")
        if record.get("what", "result") not in NAMED_WHAT:
            errs.append(f"what must be one of {NAMED_WHAT}")
    errs += _axes_errors(record, rb.known(*(rubrics or {}).values()))
    return errs


def _axes_errors(record: dict, rubrics: dict[str, rb.Rubric]) -> list[str]:
    """Problems with the axes a record names (S3, "Rubrics"): each is an axis of the record's
    rubric (only ``category`` may be ``other``), checked when that rubric is known."""
    axes = [(f"checked.{a}", a, False) for a in record.get("checked") or {}]
    axes += [("modes", m, False) for m in record.get("modes") or []]
    axes += [("caveat category", c.get("category") if isinstance(c, dict) else None, True)
             for c in record.get("caveats") or []]
    if record.get("category") is not None:
        axes.append(("category", record["category"], True))
    if not axes:
        return []
    name = record.get("rubric")
    if not isinstance(name, str) or not name:
        return ["a record that names axes says which rubric they are from (rubric)"]
    rubric = rubrics.get(name)
    errs = []
    for where, axis, other_ok in axes:
        if axis == rb.OTHER and other_ok:
            continue
        if not isinstance(axis, str) or not rb.AXIS_NAME.match(axis) or axis == rb.OTHER:
            errs.append(f"{where}: {axis!r} is not an axis name")
        elif rubric and axis not in rubric.names:
            errs.append(f"{where}: {axis!r} is not an axis of {name}")
    return errs


def _by_errors(record: dict) -> list[str]:
    """Problems with who made a record."""
    errs: list[str] = []
    by = record.get("by")
    if not by:
        return ["missing by"]
    if by.get("kind") not in REVIEWER_KINDS:
        errs.append(f"by.kind must be one of {REVIEWER_KINDS}")
    ident = by.get("identity")
    if ident is not None:
        if ident.get("kind") not in IDENTITY_KINDS:
            errs.append(f"by.identity.kind must be one of {IDENTITY_KINDS}")
        elif not ident.get("id"):
            errs.append("by.identity.id is required")
    if by.get("kind") == "person" and ident is None:
        errs.append("a person is identified by a GitHub account (by.identity)")
    if by.get("kind") == "agent":
        agent = by.get("agent")
        if not isinstance(agent, dict) or not agent.get("tool"):
            errs.append("by.agent is required for an agent, as {tool, model, session}")
    if by.get("involvement", "unknown") not in INVOLVEMENT:
        errs.append(f"by.involvement must be one of {INVOLVEMENT}")
    return errs


def target_kind(record: dict) -> str:
    """What a status can be about: a review's verdict, or the record's kind."""
    return record.get("verdict", "") if record.get("kind") == "review" else record.get("kind", "")


def load(path: str | Path) -> list[dict]:
    """The records of a JSONL file, in file order."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}:{n}: {e}") from e
    return out


def append(path: str | Path, records: Iterable[dict]) -> list[dict]:
    """Appends records (with ids set) to a JSONL file; returns them. Refuses invalid records."""
    written = []
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            r = with_id(r)
            errs = validate(r)
            if errs:
                raise ValueError(f"invalid record: {'; '.join(errs)}")
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            written.append(r)
    return written
