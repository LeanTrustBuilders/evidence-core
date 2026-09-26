"""Records as a page shows them: one plain view of each record resolved against a dataset, the same
for every front end, which only chooses what to display.

A view carries what evidence-core decided about the record (its status against the dataset, its
latest state, whether it is in force or superseded, the replies and statuses on it, which changes
of state it allows) along with the record's own fields.
"""
from __future__ import annotations

from . import records as rec
from . import status as st
from .coverage import Evidence

#: A change of state as the status form and the intake commands name it (``/withdraw``, …).
ACTION_OF_STATE = {"withdrawn": "withdraw", "reopened": "reopen"}


def by_view(by: dict) -> dict:
    """Who made a record: ``{kind, login, agent: {tool, model, session} | None, label, involvement}``."""
    agent = by.get("agent") if isinstance(by.get("agent"), dict) else \
        (rec.parse_agent(by["agent"]) if by.get("agent") else None)
    return {"kind": by.get("kind", "person"), "login": (by.get("identity") or {}).get("id", ""),
            "agent": agent, "label": rec.who(by), "involvement": by.get("involvement", "unknown")}


def actions(ev: Evidence, r: dict) -> list[str]:
    """The changes of state a record allows now, as the status form names them: nothing once it is
    withdrawn or superseded; for a record that stays open (a problem, question or challenge), the
    states that resolve it while it is open, and reopening once it is not; for anything else,
    withdrawing it."""
    if r["id"] in ev.superseded_by or ev.state(r["id"]) == "withdrawn":
        return []
    target = rec.target_kind(r)
    allowed = [s for s, targets in rec.STATE_TARGETS.items() if target in targets]
    if "reopened" not in allowed:
        return [ACTION_OF_STATE.get(s, s) for s in allowed]
    if ev.state(r["id"]) == "open":
        return [ACTION_OF_STATE.get(s, s) for s in allowed if s != "reopened"]
    return ["reopen"]


def status_view(x: dict) -> dict:
    return {"state": x["state"], "at": x.get("at", ""), "by": by_view(x.get("by", {})), "note": x.get("note", ""),
            "commit": x.get("commit", ""), "test": x.get("test"), "url": rec.origin_url(x.get("origin")),
            "id": x.get("id", "")}


def record_view(ev: Evidence, r: dict, s: st.Status, decl: str) -> dict:
    """One record about ``decl`` (its current name), with ``s`` its status against the dataset."""
    subject = r.get("subject") or {}
    out = {"id": r["id"], "kind": r["kind"], "decl": decl, "at": r.get("at", ""), "by": by_view(r.get("by", {})),
           "url": rec.origin_url(r.get("origin")), "status": s.state, "applies": s.applies, "changed": s.changed,
           "hash": (subject.get("hashes") or {}).get("meaning", ""), "commit": subject.get("commit", ""),
           "renamedFrom": subject.get("name") if s.state == st.RENAMED else None,
           "state": ev.state(r["id"]), "supersededBy": ev.superseded_by.get(r["id"]),
           "supersedes": (r.get("links") or {}).get("supersedes"),
           "replies": [c["id"] for c in ev.replies.get(r["id"], [])],
           "statuses": [status_view(x) for x in ev.statuses.get(r["id"], [])],
           "actions": actions(ev, r)}
    out["inForce"] = out["supersededBy"] is None and out["state"] != "withdrawn"
    kind = r["kind"]
    if kind == "review":
        out.update(verdict=r["verdict"], category=(r.get("problem") or {}).get("category"),
                   reference=r.get("reference"), checked=r.get("checked") or {}, caveats=r.get("caveats") or [],
                   rationale=r.get("rationale", ""), fix=r.get("fix", ""))
    elif kind == "comment":
        out.update(text=r.get("text", ""), repliesTo=(r.get("links") or {}).get("replies_to"))
    elif kind == "test":
        out.update(test=(r.get("test") or {}).get("name"), checks=r.get("checks", ""))
    elif kind == "challenge":
        out.update(property=r.get("property", ""), statement=r.get("statement", ""), catches=r.get("catches", ""),
                   modes=r.get("modes", []), rationale=r.get("rationale", ""))
    elif kind == "named":
        out.update(name=r.get("name", ""), what=r.get("what", ""), about=r.get("about", ""), source=r.get("source") or {})
    return out


def views_on(ev: Evidence, name: str, kind: str | None = None) -> list[dict]:
    """The views of every record about a declaration (of one kind, if given)."""
    return [record_view(ev, r, s, name) for r, s in ev.records_on(name, kind)]
