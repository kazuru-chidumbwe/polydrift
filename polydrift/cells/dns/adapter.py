"""DNS adapter for StackDiff-style observations.

Input is the per-resolver dict the StackDiff harness records::

    {"rcode": "NOERROR", "answers": [...], "aa": bool, "ra": bool,
     "error": None | "timeout" | "dig exit N" | ..., "additional": [...],
     "glue_cache_accept": bool}

Liveness status from ``error``:

* no error and an rcode: responded
* "timeout", "dig exit 9" (dig: no reply from server): silent
* "dig exit 1/8/10" (dig usage, batch file, internal error): harness-fault,
  because the failing party is the measuring tool
* "short response": closed (the transport ended before a full message)
* anything else: harness-fault
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from polydrift.core.canon import identity
from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent
TIERS_PATH = CELL_DIR / "tiers.yaml"

RCODE_CLASS = {
    "NOERROR": "proceed",
    "NXDOMAIN": "stop",
    "FORMERR": "fix-request",
    "NOTIMP": "fix-request",
    "SERVFAIL": "retry-later",
    "REFUSED": "retry-later",
    "BADVERS": "fix-request",  # RFC 6891 6.1.3: the requester must lower its EDNS version
}


def _rrset(values: Any) -> Any:
    return tuple(sorted(str(a).strip().lower().rstrip(".") for a in (values or []) if a))


def _additional(values: Any) -> Any:
    out = []
    for raw in values or []:
        s = str(raw).strip().lower()
        if not s:
            continue
        if "|" in s:
            owner, ip = s.split("|", 1)
            out.append(f"{owner.rstrip('.')}|{ip.strip()}")
        else:
            out.append(s.rstrip("."))
    return tuple(sorted(out))


KAPPA: dict[str, Callable[[Any], Any]] = {"identity": identity, "rrset": _rrset, "additional": _additional}

END_STATE_AXES: set[str] = set()


def load_spec() -> CellSpec:
    tiers = yaml.safe_load(TIERS_PATH.read_text(encoding="utf-8"))
    axes = {}
    END_STATE_AXES.clear()
    for name, row in tiers["axes"].items():
        axes[name] = AxisSpec(name=name, tier=Tier(row["tier"]), kappa=KAPPA[row["kappa"]], rule_clause=row.get("clause", ""))
        if row.get("end_state"):
            END_STATE_AXES.add(name)
    return CellSpec(cell="dns", axes=axes, refusal_class=tiers.get("refusal_class"))


def classify_status(obs: dict[str, Any]) -> Status:
    err = obs.get("error")
    if not err:
        if obs.get("rcode") in (None, "", "null", "NULL"):
            return Status.SILENT
        return Status.RESPONDED
    e = str(err).lower()
    if "timeout" in e or e.strip() == "dig exit 9":
        return Status.SILENT
    if "short response" in e:
        return Status.CLOSED
    return Status.HARNESS_FAULT


def response_type(rcode: str | None, answers: Any) -> str | None:
    if rcode == "NXDOMAIN":
        return "nxdomain"
    if rcode == "NOERROR":
        return "positive" if answers else "nodata"
    return None


def decision(rcode: str | None, answers: Any, tc: bool = False) -> str | None:
    if tc and not answers:
        return "no-decision"  # truncated: not a final answer, the stub asks again over TCP (RFC 2181 9)
    if rcode == "NOERROR" and not answers:
        return "stop"  # NODATA: give up on this type
    return RCODE_CLASS.get(rcode or "")


def step_record(obs: dict[str, Any], spec: CellSpec) -> StepRecord:
    """One driver observation (lab/driver.py) as a step: the StackDiff fields plus tc, ad, authority_soa, edns."""
    status = classify_status(obs)
    fields: dict[str, Field] = {}
    if status == Status.RESPONDED:
        rcode, answers, tc = obs.get("rcode"), obs.get("answers") or [], bool(obs.get("tc"))
        values = {
            "decision_class": decision(rcode, answers, tc),
            "response_type": None if (tc and not answers) else response_type(rcode, answers),
            "answers": answers,
            "native_code": rcode,
            "aa": obs.get("aa"),
            "ra": obs.get("ra"),
            "additional": obs.get("additional"),
            "tc": tc,
            "ad": obs.get("ad"),
            "authority_soa": obs.get("authority_soa"),
            "edns": obs.get("edns"),
        }
        for name, raw in values.items():
            fields[name] = spec.axis(name).field(raw)
    return StepRecord(status=status, fields=fields, detail=str(obs.get("error") or ""))


def to_record_script(observations: list[dict[str, Any]], impl: str, fixture: str, *, spec: CellSpec,
                     repeat: int = 1, impl_version: str = "", harness_commit: str = "") -> ObservationRecord:
    return ObservationRecord(cell="dns", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
                             harness_commit=harness_commit, steps=tuple(step_record(o, spec) for o in observations))


def to_record(
    obs: dict[str, Any],
    impl: str,
    fixture: str,
    *,
    spec: CellSpec,
    repeat: int = 1,
    impl_version: str = "",
) -> ObservationRecord:
    status = classify_status(obs)
    fields: dict[str, Field] = {}
    end: dict[str, Field] = {}
    if status == Status.RESPONDED:
        rcode = obs.get("rcode")
        answers = obs.get("answers") or []
        values = {
            "decision_class": decision(rcode, answers),
            "response_type": response_type(rcode, answers),
            "answers": answers,
            "native_code": rcode,
            "aa": obs.get("aa"),
            "ra": obs.get("ra"),
        }
        if "additional" in obs:
            values["additional"] = obs.get("additional")
        for name, raw in values.items():
            fields[name] = spec.axis(name).field(raw)
    if "glue_cache_accept" in obs and obs.get("glue_cache_accept") is not None:
        end["glue_cache_accept"] = spec.axis("glue_cache_accept").field(obs["glue_cache_accept"])
    return ObservationRecord(
        cell="dns",
        impl=impl,
        impl_version=impl_version,
        fixture=fixture,
        repeat=repeat,
        steps=(StepRecord(status=status, fields=fields, detail=str(obs.get("error") or "")),),
        end_state=end,
    )
