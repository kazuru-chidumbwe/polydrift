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


# TTLs the lab authorities serve (zones.yaml and the raw-answer behaviours), plus common caps and the largest legal
# TTL. A cached record's TTL counts down by the seconds since it was cached, so its raw value depends on when the
# step ran; its class is the smallest anchor at or above it, the TTL it is ageing from (RFC 1035 3.2.1, RFC 2181 8).
TTL_ANCHORS = (0, 2, 60, 300, 3600, 86400, 604800, 2**31 - 1)


def ttl_anchor(t: int) -> int:
    for a in TTL_ANCHORS:
        if t <= a:
            return a
    return t


def _ttl_classes(value: Any) -> Any:
    if value is None:
        return None
    return tuple(sorted({ttl_anchor(int(t)) for t in value}))


KAPPA: dict[str, Callable[[Any], Any]] = {"identity": identity, "rrset": _rrset, "additional": _additional, "ttl_classes": _ttl_classes}

END_STATE_AXES: set[str] = set()


def _skip_name(w: bytes, i: int) -> tuple[int, bool]:
    """Offset after the domain name at i, and whether the name used a compression pointer."""
    ptr = False
    while i < len(w):
        n = w[i]
        if n == 0:
            return i + 1, ptr
        if n & 0xC0 == 0xC0:
            return i + 2, True
        i += 1 + n
    return i, ptr


def dname_compressed(wire_hex: Any) -> Any:
    """True when a DNAME record in the answer section carries its target in compressed form (RFC 6672 2.5 forbids
    it), False when every DNAME target is uncompressed, None when the response has no DNAME or no wire bytes."""
    if not isinstance(wire_hex, str) or not wire_hex:
        return None
    try:
        w = bytes.fromhex(wire_hex)
        qd, an = int.from_bytes(w[4:6], "big"), int.from_bytes(w[6:8], "big")
        i = 12
        for _ in range(qd):
            i, _p = _skip_name(w, i)
            i += 4
        seen = None
        for _ in range(an):
            i, _p = _skip_name(w, i)
            rtype, rdlen = int.from_bytes(w[i:i + 2], "big"), int.from_bytes(w[i + 8:i + 10], "big")
            rd = i + 10
            if rtype == 39:
                _end, ptr = _skip_name(w, rd)
                seen = bool(seen) or ptr
            i = rd + rdlen
        return seen
    except (IndexError, ValueError):
        return None


def synth_ttl_follows(answers: Any, ttls: Any) -> Any:
    """For an answer with a DNAME and the CNAME synthesized from it: is the CNAME's TTL equal to the DNAME's
    (RFC 6672 3.1, a cache answers with the cached DNAME's decremented TTL)? None when either record is absent."""
    if not isinstance(answers, list) or not isinstance(ttls, list) or len(ttls) != len(answers):
        return None
    kinds = [(a.split()[1].lower() if isinstance(a, str) and len(a.split()) > 1 else "") for a in answers]
    if "dname" not in kinds or "cname" not in kinds:
        return None
    return ttls[kinds.index("cname")] == ttls[kinds.index("dname")]


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
    if obs.get("crashed") and obs.get("op") != "upstream":
        return Status.CRASHED  # the runner saw the resolver exit during this fixture (runner.mark_crashed)
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


VOLUME_BUCKETS = ((0, "0"), (3, "1-3"), (10, "4-10"), (30, "11-30"))


def volume_bucket(n: int) -> str:
    """Upstream queries for one client query, in buckets: exact counts move with retry timing, an amplification
    (ResolverFuzz RC7, TuDoor VRC: about 100 against a limit of a few) does not."""
    for top, label in VOLUME_BUCKETS:
        if n <= top:
            return label
    return ">30"


def _upstream_fields(obs: dict[str, Any]) -> dict[str, Any]:
    """What the resolver sent to the lab authorities for the fixture's names (driver `upstream` step)."""
    qs = obs.get("queries") or []
    count: dict[str, int] = {}
    for q in qs:
        k = f"{q['qname'].lower()} {q['qtype']}"
        count[k] = count.get(k, 0) + 1
    # A step that asks for the volume measures an amplification: there the exact count moves with retry timing (7
    # or 8 copies within one bucket), so the bucket replaces it.
    vol = {"upstream_volume": volume_bucket(len(qs))} if obs.get("volume") else \
        {"upstream_count": tuple(sorted(count.items()))}
    return {
        **vol,
        "upstream_rd": sorted({bool(q["rd"]) for q in qs}),
        "upstream_opt_codes": sorted({c for q in qs for c in q.get("opts") or []}),
        "upstream_edns": sorted({int(q["edns"]) for q in qs}),
        # advertised UDP sizes in the order first used (fallback clauses, RFC 6891 6.2.5)
        "upstream_payloads": list(dict.fromkeys(q["payload"] for q in qs if q.get("payload") is not None)),
        "upstream_transports": sorted({q["transport"] for q in qs}),
        "upstream_servers": list(dict.fromkeys(q["server"] for q in qs)),
        # an option from the local/experimental range (65001-65534) can only be the client's own: forwarded
        "upstream_local_opts": any(c >= 65001 for q in qs for c in q.get("opts") or []),
    }


def step_record(obs: dict[str, Any], spec: CellSpec) -> StepRecord:
    """One driver observation (lab/driver.py) as a step: the StackDiff fields plus tc, ad, authority_soa, edns, and
    for the upstream, parallel and tcp_session steps their own fields."""
    status = classify_status(obs)
    fields: dict[str, Field] = {}
    op = obs.get("op", "query")
    if status == Status.RESPONDED and op in ("upstream", "parallel", "tcp_session"):
        if op == "upstream":
            values = _upstream_fields(obs)
        elif op == "parallel":
            values = {"parallel_order": obs.get("order"),
                      "parallel_latency": tuple(sorted((obs.get("latency") or {}).items())),
                      "parallel_rcodes": tuple(sorted((obs.get("rcodes") or {}).items()))}
        else:
            values = {"tcp_rcodes": obs.get("rcodes"), "tcp_open_after": obs.get("open_after")}
        for name, raw in values.items():
            fields[name] = spec.axis(name).field(raw)
        return StepRecord(status=status, fields=fields, detail="")
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
        if "z" in obs:
            values.update({
                "z_bit": obs.get("z"),
                "rd": obs.get("rd"),
                "qname_case": obs.get("qname_case"),
                "answer_ttls": obs.get("ttls"),
                "dname_compressed": dname_compressed(obs.get("wire")),
                "question_mismatch": obs.get("question_mismatch"),
                "synth_ttl_follows": synth_ttl_follows(obs.get("answers"), obs.get("ttls")),
                "additional_soa": obs.get("additional_soa"),
            })
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
