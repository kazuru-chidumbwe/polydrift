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


# CONNACK properties that announce limits and capabilities set by the broker's configuration (MQTT 5.0 3.2.2.3).
# They are recorded as announced_config and not compared (manuscript 3.2): two brokers with different defaults
# would otherwise differ on every pair whatever the fixture does.
CONFIG_PROPS = frozenset({
    "session_expiry_interval", "receive_maximum", "maximum_qos", "retain_available", "maximum_packet_size",
    "topic_alias_maximum", "wildcard_subscription_available", "subscription_identifier_available",
    "shared_subscription_available", "server_keep_alive",
})


def _connack_props(value: Any) -> Any:
    if value is None:
        return None
    d = dict(value)
    if "assigned_client_identifier" in d:
        d["assigned_client_identifier"] = "present"
    return tuple(sorted((k, str(v)) for k, v in d.items()))


def _sorted_tuple(value: Any) -> Any:
    return None if value is None else tuple(sorted(value))


def _expiry10(value: Any) -> Any:
    # a received Message Expiry Interval counts whole seconds down while the broker holds the message, so the exact
    # value depends on when the broker's clock ticks; the canonical value is the 10-second floor (57 and 58 agree,
    # an undecremented 60 still differs from 50 to 59)
    return value if not isinstance(value, int) or isinstance(value, bool) else (value // 10) * 10


def _publish_meta(value: Any) -> Any:
    if value is None:
        return None
    return tuple(sorted((c, tuple(sorted(tuple(m[:3]) + (_expiry10(m[3]),) + tuple(m[4:]) for m in v)))
                        for c, v in dict(value).items()))


KAPPA: dict[str, Callable[[Any], Any]] = {
    "publish_meta": _publish_meta,
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
    """A step whose protocol needs no answer (QoS 0 PUBLISH, DISCONNECT, an acknowledgement) is responded when the
    connection stays up: the broker's silence is then the observation (packet_types empty), not a liveness failure.
    silent is kept for a step that required an answer and got none, so the core's liveness gate stops there."""
    err = obs.get("error") or ""
    if err.startswith("closed-by-peer") or err.startswith("send-failed"):
        return Status.CLOSED
    if err:
        return Status.HARNESS_FAULT
    me = obs["clients"].get(obs["actor"], {})
    if me.get("packets"):
        return Status.RESPONDED
    if me.get("closed_now"):
        # after the client's own DISCONNECT the server closing the connection is the specified outcome (3.14.4)
        return Status.RESPONDED if obs.get("op") == "disconnect" else Status.CLOSED
    if obs.get("expects_reply", True):
        return Status.SILENT
    return Status.RESPONDED


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
        props = ca.get("properties") or {}
        values["connack_props"] = {k: v for k, v in props.items() if k not in CONFIG_PROPS}
        values["announced_config"] = {k: v for k, v in props.items() if k in CONFIG_PROPS}
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
                # topic-valued properties carry the per-repeat fixture prefix; compare them without it
                fwd.setdefault(n, []).append(tuple((k, _strip(str(pr[k]), prefix) if k == "response_topic" else str(pr[k]))
                                                   for k in FORWARDED if k in pr))
    values["forwarded_props"] = fwd
    values["peers_closed"] = [n for n, c in obs["clients"].items() if n != actor and c.get("closed_now")]
    # the acting client's connection is closed at the end of the step window, after any packets it received
    values["actor_closed"] = obs.get("op") != "close" and not me.get("open", True)
    # limits the receiving client announced (Maximum Packet Size, Topic Alias Maximum) that a received packet broke
    values["limit_violations"] = [f"{n}:{kind}" for n, c in obs["clients"].items()
                                  for kind in ("oversize", "alias_over") if c.get(kind)]
    # per client, PUBLISH metadata that delivery comparisons leave out: DUP, subscription identifiers, message
    # expiry interval as received, whether a topic alias was used
    meta = {}
    for n, c in obs["clients"].items():
        for p in c.get("packets") or []:
            if p.get("type") == "PUBLISH":
                pr = p.get("properties") or {}
                sid = pr.get("subscription_identifier")
                sid = tuple(sorted(sid)) if isinstance(sid, list) else ((sid,) if sid is not None else ())
                meta.setdefault(n, []).append((_strip(p.get("topic", ""), prefix), p.get("dup", 0), sid,
                                               pr.get("message_expiry_interval"), "topic_alias" in pr))
    values["publish_meta"] = meta
    # CONNACK properties whose presence a clause fixes (3.1.2-28, 3.1.3-7, 4.12.0-5/6); "refused" marks a CONNACK
    # with a reason code of 0x80 or more, where none of them is expected
    if ca is not None:
        pr = ca.get("properties") or {}
        pres = [k for k in ("assigned_client_identifier", "response_information", "authentication_method") if k in pr]
        if (ca.get("reason_code") or 0) >= 0x80:
            pres.append("refused")
        values["connack_presence"] = pres
    # packets other than PUBLISH, CONNACK and DISCONNECT that carry a Reason String or User Property (3.1.2-29)
    values["problem_info"] = [p.get("type") for p in nonpub if p.get("type") not in ("CONNACK", "DISCONNECT")
                              and {"reason_string", "user_property"} & set(p.get("properties") or {})]
    # AUTH packets the acting client received, by reason code (4.12)
    values["auth_packets"] = [p.get("reason_code") for p in pkts if p.get("type") == "AUTH"]
    if ca is not None:
        # CONNACK Acknowledge Flags bits 7 to 1 are reserved (3.2.2-1)
        values["connack_reserved"] = (ca.get("ack_flags") or 0) >> 1
    # property names in DISCONNECT packets the server sent (3.14.2-2: never Session Expiry Interval)
    values["disconnect_props"] = sorted({k for p in nonpub if p.get("type") == "DISCONNECT"
                                         for k in (p.get("properties") or {})})
    # Packet Identifiers of the acknowledgements the acting client received (4.3.2-4, 4.3.3-8, 4.3.3-11, 3.10.4-5)
    values["ack_ids"] = [p.get("packet_id") for p in nonpub
                         if p.get("type") in ("PUBACK", "PUBREC", "PUBCOMP", "SUBACK", "UNSUBACK")]
    # does each PUBREL the acting client received carry the identifier of the QoS 2 PUBLISH it was delivered (4.3.3-4)
    last = me.get("last_pid_before")
    values["pubrel_matches"] = [p.get("packet_id") == last for p in nonpub if p.get("type") == "PUBREL"]
    # arrival order of application messages per client, not sorted (4.6.0-5)
    values["arrival_order"] = {n: [(_strip(p.get("topic", ""), prefix), p.get("payload", ""))
                                   for p in c.get("packets") or [] if p.get("type") == "PUBLISH"]
                               for n, c in obs["clients"].items()
                               if any(p.get("type") == "PUBLISH" for p in c.get("packets") or [])}
    # topics beginning with $ that any client received (4.7.2-1: never through a filter starting with a wildcard)
    values["dollar_topics"] = sorted({p.get("topic", "") for c in obs["clients"].values()
                                      for p in c.get("packets") or []
                                      if p.get("type") == "PUBLISH" and str(p.get("topic", "")).startswith("$")})
    # a QoS 1 or 2 Packet Identifier reused by the server while the first delivery is unacknowledged (4.3.2-1)
    values["pid_reuse"] = [n for n, c in obs["clients"].items() if c.get("pid_reuse")]
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
    aggregate_deliveries: bool = False,
) -> ObservationRecord:
    """``aggregate_deliveries``: the fixture's clause fixes what is delivered but not in which step (MQTT 5.0 4.3.3
    lets a QoS 2 receiver forward on PUBLISH or on PUBREL), so per-step deliveries are dropped and their union over
    the fixture is compared as the end-state axis ``deliveries_total``."""
    steps = []
    end: dict[str, Field] = {}
    retained: dict[str, list] = {}
    total: dict[str, list] = {}
    for obs in observations:
        if obs.get("end"):
            for n, c in obs["clients"].items():
                msgs = [_msg(p, prefix) for p in c.get("packets") or [] if p.get("type") == "PUBLISH"]
                if msgs:
                    retained.setdefault(n, []).extend(msgs)
            continue
        status = classify(obs)
        fields = step_fields(obs, spec, prefix, version) if status != Status.HARNESS_FAULT else {}
        if aggregate_deliveries:
            fields.pop("forwarded_props", None)
        if aggregate_deliveries and "deliveries" in fields:
            for n, msgs in dict(fields.pop("deliveries").raw).items():
                total.setdefault(n, []).extend(list(m) for m in msgs)
        steps.append(StepRecord(status=status, fields=fields, detail=str(obs.get("error") or "")))
    if aggregate_deliveries:
        end["deliveries_total"] = spec.axis("deliveries_total").field(total)
    if any(o.get("end") for o in observations):
        end["retained_after"] = spec.axis("retained_after").field(retained)
    return ObservationRecord(
        cell="mqtt", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
        harness_commit=harness_commit, steps=tuple(steps), end_state=end,
    )
