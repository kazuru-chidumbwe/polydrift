"""MQTT adapter: driver step observations -> ObservationRecord.

Liveness status of a step is that of the acting client:

* harness-fault: the harness could not open its TCP connection or send (the broker was not reachable), or the
  fixture asked for an unknown operation. The broker's own output plays no part.
* responded: the acting client received at least one packet in the step window.
* closed: the acting client's connection was closed by the broker in the window, with no packet before it.
* silent: nothing arrived and the connection stayed open. For a step that needs no answer (a QoS 0 PUBLISH) this is
  the expected status; the comparison treats it like any other.

End-state steps (``end: true``) are folded into the end-state record: their deliveries become ``retained_after``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from polydrift.cells.mqtt import classes
from polydrift.core.canon import identity
from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent
TIERS_PATH = CELL_DIR / "tiers.yaml"

FORWARDED = ("payload_format_indicator", "content_type", "response_topic", "correlation_data", "user_property")


def _deliveries(value: Any) -> Any:
    """Per client: sorted (topic with the fixture prefix removed, payload, qos, retain)."""
    if value is None:
        return None
    out = []
    for client, msgs in sorted(dict(value).items()):
        out.append((client, tuple(sorted(tuple(m) for m in msgs))))
    return tuple(out)


def _forwarded(value: Any) -> Any:
    if value is None:
        return None
    return tuple(sorted((c, tuple(tuple(x) for x in v)) for c, v in dict(value).items()))


def _connack_props(value: Any) -> Any:
    if value is None:
        return None
    d = dict(value)
    if "assigned_client_identifier" in d:
        d["assigned_client_identifier"] = "present"
    return tuple(sorted((k, str(v)) for k, v in d.items()))


def _sorted_tuple(value: Any) -> Any:
    return None if value is None else tuple(sorted(value))


KAPPA: dict[str, Callable[[Any], Any]] = {
    "identity": identity, "deliveries": _deliveries, "forwarded_props": _forwarded,
    "connack_props": _connack_props, "sorted_tuple": _sorted_tuple,
}
END_STATE_AXES: set[str] = set()


def load_spec() -> CellSpec:
    tiers = yaml.safe_load(TIERS_PATH.read_text(encoding="utf-8"))
    axes = {}
    END_STATE_AXES.clear()
    for name, row in tiers["axes"].items():
        axes[name] = AxisSpec(name=name, tier=Tier(row["tier"]), kappa=KAPPA[row["kappa"]], rule_clause=row.get("clause", ""))
        if row.get("end_state"):
            END_STATE_AXES.add(name)
    return CellSpec(cell="mqtt", axes=axes, refusal_class=tiers.get("refusal_class"))


def _strip(topic: str, prefix: str) -> str:
    return topic[len(prefix):] if prefix and topic.startswith(prefix) else topic


def _msg(p: dict[str, Any], prefix: str) -> tuple:
    return (_strip(p.get("topic", ""), prefix), p.get("payload", ""), p.get("qos", 0), p.get("retain", 0))


def classify(obs: dict[str, Any]) -> Status:
    if obs.get("error"):
        return Status.HARNESS_FAULT
    me = obs["clients"].get(obs["actor"], {})
    if me.get("packets"):
        return Status.RESPONDED
    if me.get("closed_now"):
        return Status.CLOSED
    return Status.SILENT


def step_fields(obs: dict[str, Any], spec: CellSpec, prefix: str, version: int) -> dict[str, Field]:
    actor = obs["actor"]
    me = obs["clients"].get(actor, {})
    pkts = me.get("packets") or []
    nonpub = [p for p in pkts if p.get("type") != "PUBLISH"]
    values: dict[str, Any] = {}
    dc = None
    for p in nonpub:
        dc = classes.decision(p, version)
        if dc is not None:
            break
    values["decision_class"] = dc
    values["packet_types"] = [p.get("type") for p in pkts]
    codes = []
    for p in nonpub:
        if "reason_code" in p:
            codes.append(p["reason_code"])
        codes.extend(p.get("reason_codes") or [])
    values["native_codes"] = codes
    ca = next((p for p in pkts if p.get("type") == "CONNACK"), None)
    if ca is not None:
        values["session_present"] = ca.get("session_present")
        values["connack_props"] = ca.get("properties") or {}
    sa = next((p for p in pkts if p.get("type") == "SUBACK"), None)
    if sa is not None:
        values["granted"] = sa.get("reason_codes")
    values["own_publishes"] = {actor: [_msg(p, prefix) for p in pkts if p.get("type") == "PUBLISH"]}
    others = {n: [_msg(p, prefix) for p in c.get("packets") or [] if p.get("type") == "PUBLISH"]
              for n, c in obs["clients"].items() if n != actor}
    values["deliveries"] = {n: v for n, v in others.items() if v}
    fwd = {}
    for n, c in obs["clients"].items():
        for p in c.get("packets") or []:
            if p.get("type") == "PUBLISH":
                pr = p.get("properties") or {}
                fwd.setdefault(n, []).append(tuple((k, str(pr[k])) for k in FORWARDED if k in pr))
    values["forwarded_props"] = fwd
    values["peers_closed"] = [n for n, c in obs["clients"].items() if n != actor and c.get("closed_now")]
    return {name: spec.axis(name).field(raw) for name, raw in values.items()}


def to_record(
    observations: list[dict[str, Any]],
    impl: str,
    fixture: str,
    *,
    spec: CellSpec,
    prefix: str,
    version: int = 5,
    repeat: int = 1,
    impl_version: str = "",
    harness_commit: str = "",
) -> ObservationRecord:
    steps = []
    end: dict[str, Field] = {}
    retained: dict[str, list] = {}
    for obs in observations:
        if obs.get("end"):
            for n, c in obs["clients"].items():
                msgs = [_msg(p, prefix) for p in c.get("packets") or [] if p.get("type") == "PUBLISH"]
                if msgs:
                    retained.setdefault(n, []).extend(msgs)
            continue
        status = classify(obs)
        fields = step_fields(obs, spec, prefix, version) if status != Status.HARNESS_FAULT else {}
        steps.append(StepRecord(status=status, fields=fields, detail=str(obs.get("error") or "")))
    if any(o.get("end") for o in observations):
        end["retained_after"] = spec.axis("retained_after").field(retained)
    return ObservationRecord(
        cell="mqtt", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
        harness_commit=harness_commit, steps=tuple(steps), end_state=end,
    )
