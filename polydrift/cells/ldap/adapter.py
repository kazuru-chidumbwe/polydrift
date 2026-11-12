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


def _norm_dn(dn: str, base: str, unique: str = "") -> str:
    d = ",".join(p.strip() for p in dn.split(",")).lower()
    if unique:
        d = d.replace(unique.lower(), "{U}")
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


def _u(value: str, unique: str) -> str:
    return value.replace(unique, "{U}").replace(unique.lower(), "{U}") if unique else value


def step_fields(obs: dict[str, Any], spec: CellSpec, base: str, value_attrs: set[str], unique: str = "") -> dict[str, Field]:
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
        v["entries"] = [_norm_dn(m["dn"], base, unique) for m in ents]
        am = {}
        for m in ents:
            for k, vals in m.get("attributes", {}).items():
                kk = k.lower()
                am[f"{_norm_dn(m['dn'], base, unique)}|{kk}"] = [_u(x, unique) for x in vals] if kk in value_attrs else ["*"]
        v["entry_attrs"] = am
        # attribute descriptions the server used (case-folded), over all entries (RFC 4511 4.5.2 short names)
        v["attr_names"] = sorted({k.lower() for m in ents for k in m.get("attributes", {})})
        # objectClass values of the returned entries (case-folded), for superclass and subentry clauses (RFC 4512 3.3)
        v["objectclasses"] = sorted({x.lower() for m in ents for k, xs in m.get("attributes", {}).items()
                                     if k.lower() == "objectclass" for x in xs})
        # attribute type of the first RDN of each subschemaSubentry value (RFC 4512 3.2: subentries are named by cn)
        v["subschema_rdn_attr"] = sorted({x.split("=", 1)[0].strip().lower() for m in ents
                                          for k, xs in m.get("attributes", {}).items()
                                          if k.lower() == "subschemasubentry" for x in xs})
    # referral shape (RFC 4511 4.1.10): "none" (no referral result, no continuation reference), "uris" (at least
    # one URI), "empty" (a referral or reference without a URI); the URIs themselves are compared with the base and
    # the unique name removed
    uris = [u for m in msgs for u in (m.get("referral") or []) + (m.get("uris") or [])]
    has_ref = any(m.get("result") == 10 or m.get("op") == "SearchResultReference" for m in msgs)
    v["referral_shape"] = "none" if not has_ref else ("uris" if uris else "empty")
    v["referral_uris"] = sorted(_u(x, unique) for x in uris)
    # whether each BindResponse carried serverSaslCreds (RFC 4511 4.2.2, RFC 4513 5.2.1.3)
    v["sasl_creds_present"] = [m.get("sasl_creds") is not None for m in msgs if m.get("op") == "BindResponse"]
    v["notice_of_disconnection"] = any(m.get("op") == "ExtendedResponse" and m.get("id") == 0
                                       and m.get("response_name") == NOD for m in msgs)
    return {k: spec.axis(k).field(x) for k, x in v.items()}


def to_record(observations: list[dict[str, Any]], impl: str, fixture: str, *, spec: CellSpec, base: str,
              value_attrs: set[str] | None = None, unique: str = "", repeat: int = 1, impl_version: str = "",
              harness_commit: str = "") -> ObservationRecord:
    steps = []
    for obs in observations:
        s = classify(obs)
        f = step_fields(obs, spec, base, value_attrs or set(), unique) if s not in (Status.HARNESS_FAULT,) else {}
        steps.append(StepRecord(status=s, fields=f, detail=str(obs.get("error") or "")))
    return ObservationRecord(cell="ldap", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
                             harness_commit=harness_commit, steps=tuple(steps))
