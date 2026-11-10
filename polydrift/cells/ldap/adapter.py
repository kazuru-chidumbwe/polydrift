"""LDAP adapter: driver step observations -> ObservationRecord.

Status of a step (acting connection): harness-fault when the harness could not connect or the fixture is malformed;
responded when a message arrived, or when the step needs no answer and the connection stayed up; closed when the
server closed the connection (or the step was sent on a connection the server had closed); silent when a request that
needs an answer got none. Unsolicited Notice of Disconnection (ExtendedResponse, message ID 0) is its own axis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from polydrift.cells.ldap import classes
from polydrift.core.canon import identity
from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent
NOD = "1.3.6.1.4.1.1466.20036"


def _norm_dn(dn: str, base: str) -> str:
    d = ",".join(p.strip() for p in dn.split(",")).lower()
    b = ",".join(p.strip() for p in base.split(",")).lower()
    if b and d.endswith(b):
        d = d[: -len(b)].rstrip(",") or "{B}"
    return d


def _dn_set(v: Any) -> Any:
    return None if v is None else tuple(sorted(v))


def _attr_map(v: Any) -> Any:
    return None if v is None else tuple(sorted((k, tuple(sorted(x))) for k, x in dict(v).items()))


KAPPA: dict[str, Callable[[Any], Any]] = {"identity": identity, "dn_set": _dn_set, "attr_map": _attr_map}


def load_spec() -> CellSpec:
    t = yaml.safe_load((CELL_DIR / "tiers.yaml").read_text(encoding="utf-8"))
    axes = {n: AxisSpec(name=n, tier=Tier(r["tier"]), kappa=KAPPA[r["kappa"]], rule_clause=r.get("clause", ""))
            for n, r in t["axes"].items()}
    return CellSpec(cell="ldap", axes=axes, refusal_class=t.get("refusal_class"))


def classify(obs: dict[str, Any]) -> Status:
    err = obs.get("error") or ""
    if err.startswith("not-open") or err.startswith("send-failed"):
        return Status.CLOSED
    if err:
        return Status.HARNESS_FAULT
    me = obs["clients"].get(obs["actor"], {})
    if me.get("messages"):
        return Status.RESPONDED
    if me.get("closed_now"):
        # after the client's UnbindRequest the server closing the connection is the specified outcome (4.3)
        return Status.RESPONDED if obs.get("op") == "unbind" else Status.CLOSED
    return Status.SILENT if obs.get("expects_reply") else Status.RESPONDED


def step_fields(obs: dict[str, Any], spec: CellSpec, base: str, value_attrs: set[str]) -> dict[str, Field]:
    me = obs["clients"].get(obs["actor"], {})
    msgs = me.get("messages") or []
    own = [m for m in msgs if m.get("id") == obs.get("id")] if obs.get("id") is not None else msgs
    final = next((m for m in own if "result" in m), None)
    v: dict[str, Any] = {}
    v["decision_class"] = classes.decision(final["result"]) if final else None
    v["response_ops"] = [m.get("op") for m in msgs]
    v["native_codes"] = [m["result"] for m in msgs if "result" in m]
    v["diagnostic_present"] = [bool(m.get("diagnostic")) for m in msgs if "result" in m]
    ents = [m for m in own if m.get("op") == "SearchResultEntry"]
    if any(m.get("op") == "SearchResultDone" for m in own):
        v["entries"] = [_norm_dn(m["dn"], base) for m in ents]
        am = {}
        for m in ents:
            for k, vals in m.get("attributes", {}).items():
                kk = k.lower()
                am[f"{_norm_dn(m['dn'], base)}|{kk}"] = vals if kk in value_attrs else ["*"]
        v["entry_attrs"] = am
    v["notice_of_disconnection"] = any(m.get("op") == "ExtendedResponse" and m.get("id") == 0
                                       and m.get("response_name") == NOD for m in msgs)
    return {k: spec.axis(k).field(x) for k, x in v.items()}


def to_record(observations: list[dict[str, Any]], impl: str, fixture: str, *, spec: CellSpec, base: str,
              value_attrs: set[str] | None = None, repeat: int = 1, impl_version: str = "",
              harness_commit: str = "") -> ObservationRecord:
    steps = []
    for obs in observations:
        s = classify(obs)
        f = step_fields(obs, spec, base, value_attrs or set()) if s not in (Status.HARNESS_FAULT,) else {}
        steps.append(StepRecord(status=s, fields=f, detail=str(obs.get("error") or "")))
    return ObservationRecord(cell="ldap", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
                             harness_commit=harness_commit, steps=tuple(steps))
