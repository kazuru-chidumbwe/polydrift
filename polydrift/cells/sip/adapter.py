"""SIP adapter: driver observation + harness signals -> ObservationRecord.

Per-protocol code for the SIP cell lives here, in classes.py and in
tiers.yaml. The liveness status is decided as follows:

* harness-fault comes only from the harness's own instrumentation (readiness
  probe, send acknowledgement, observer binary present, helper output
  readable), passed in as ``HarnessSignals`` or recognised in the driver's
  instrument messages. The implementation's output plays no part.
* refused-input: the library parser returned an error for the message.
* crashed: the observer process died on a signal.
* silent: the implementation was ready and the send was acknowledged, but no
  observation arrived within the step timeout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from polydrift.cells.sip import classes
from polydrift.core.canon import casefold, collapse_lws, identity
from polydrift.core.record import Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec
from polydrift.harness import StackObservation
from polydrift.parse import parse_method, parse_status_code

CELL_DIR = Path(__file__).resolve().parent
TIERS_PATH = CELL_DIR / "tiers.yaml"
CAPS_PATH = CELL_DIR / "capabilities.yaml"

_REQUEST_LINE = re.compile(r"^\s*(\S+)[ \t]+(\S*)[ \t]*(\S+)\s*$")
_STATUS_LINE = re.compile(r"^\s*(\S+)[ \t]+(\d{3})(?:[ \t]+(.*))?$")
_VIA_SENT_BY = re.compile(r"^\s*([^\s/]+)\s*/\s*([^\s/]+)\s*/\s*(\S+)\s+([^;]+)(.*)$", re.DOTALL)


def _upper(value: str | None) -> str | None:
    return None if value is None else value.upper()


def _uri_scheme_casefold(value: str | None) -> str | None:
    """URI schemes are case-insensitive (RFC 3261 19.1.4); the rest is kept."""
    if value is None:
        return None
    if ":" in value:
        scheme, _, rest = value.partition(":")
        return f"{scheme.lower()}:{rest}"
    return value


def _params(text: str) -> tuple[tuple[str, str | None], ...]:
    out: list[tuple[str, str | None]] = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, val = part.partition("=")
        out.append((name.strip().lower(), val.strip() if sep else None))
    return tuple(out)


def _via(value: str | None) -> Any:
    """Top Via value: protocol name, version and transport case-folded, LWS
    collapsed, host case-folded, parameter names case-folded; parameter values
    (including branch) kept as sent."""
    if value is None:
        return None
    text = collapse_lws(value) or ""
    top = text.split(",", 1)[0].strip()
    m = _VIA_SENT_BY.match(top)
    if not m:
        return ("unparsed", top)
    proto, version, transport, sent_by, rest = m.groups()
    return (
        f"{proto.upper()}/{version}/{transport.upper()}",
        sent_by.strip().lower(),
        _params(rest),
    )


def _cseq(value: str | None) -> Any:
    if value is None:
        return None
    parts = (collapse_lws(value) or "").split(" ")
    if len(parts) != 2:
        return ("unparsed", collapse_lws(value))
    num, method = parts
    try:
        return (int(num), method)
    except ValueError:
        return ("unparsed", collapse_lws(value))


def _media_params(value: str | None) -> Any:
    if value is None:
        return None
    _, _, rest = value.partition(";")
    return tuple(sorted(_params(rest)))


KAPPA: dict[str, Callable[[Any], Any]] = {
    "identity": identity,
    "upper": _upper,
    "casefold": casefold,
    "uri_scheme_casefold": _uri_scheme_casefold,
    "via": _via,
    "cseq": _cseq,
    "media_params": _media_params,
}


def load_spec() -> CellSpec:
    tiers = yaml.safe_load(TIERS_PATH.read_text(encoding="utf-8"))
    axes = {
        name: AxisSpec(
            name=name,
            tier=Tier(row["tier"]),
            kappa=KAPPA[row["kappa"]],
            volatile=name in (tiers.get("volatile") or []),
            rule_clause=row.get("clause", ""),
        )
        for name, row in tiers["axes"].items()
    }
    caps_raw = yaml.safe_load(CAPS_PATH.read_text(encoding="utf-8")) or {}
    caps = {impl: frozenset(v or ()) for impl, v in caps_raw.items()}
    return CellSpec(cell="sip", axes=axes, capabilities=caps, refusal_class=tiers.get("refusal_class"))


@dataclass(frozen=True)
class HarnessSignals:
    """What the harness knows independently of the implementation's output."""

    ready: bool = True  # readiness probe passed before the run
    sent: bool = True  # the send was acknowledged / the helper was started
    canary_ok: bool = True  # canary fixtures at the batch edges agreed with expectation


# Driver detail messages that describe the instrument, not the implementation.
_INSTRUMENT = (
    re.compile(r"observe missing"),
    re.compile(r"^json parse"),
    re.compile(r"udp send failed"),
    re.compile(r"No such file or directory"),
    re.compile(r"Permission denied"),
)
_REFUSED = (
    re.compile(r"pjsip_parse_msg failed"),
    re.compile(r"msg_make failed"),
    # Sofia parsed the bytes into a message with neither a request nor a status line. Its transaction layer
    # counts such a message as bad and drops it (nta.c, agent_recv_garbage), so the library refused the input.
    re.compile(r"no request/status line"),
)
_RC = re.compile(r"rc=(-?\d+)")


def classify_status(obs: StackObservation, signals: HarnessSignals) -> Status:
    if not (signals.ready and signals.sent and signals.canary_ok):
        return Status.HARNESS_FAULT
    if obs.ok:
        return Status.RESPONDED
    detail = obs.detail or ""
    if any(p.search(detail) for p in _INSTRUMENT):
        return Status.HARNESS_FAULT
    if any(p.search(detail) for p in _REFUSED):
        return Status.REFUSED_INPUT
    m = _RC.search(detail)
    if m and int(m.group(1)) < 0:
        return Status.CRASHED
    if "timed out" in detail or "TimeoutExpired" in detail or "no observation file" in detail:
        return Status.SILENT
    # Helper ran but its output was unreadable: the instrument failed.
    return Status.HARNESS_FAULT


def _split_start_line(line: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if line is None:
        return out
    status_code = parse_status_code(line)
    if status_code is not None:
        m = _STATUS_LINE.match(line)
        out["kind"] = "response"
        out["native_code"] = status_code
        out["sip_version"] = m.group(1) if m else None
        out["reason_phrase"] = (m.group(3) or "") if m else None
        return out
    if parse_method(line) is not None:
        m = _REQUEST_LINE.match(line)
        out["kind"] = "request"
        if m:
            out["method"] = m.group(1)
            out["request_uri"] = m.group(2) or None
            out["sip_version"] = m.group(3)
        return out
    out["kind"] = "unknown"
    return out


def to_record(
    obs: StackObservation,
    fixture: str,
    *,
    spec: CellSpec,
    impl_version: str = "",
    repeat: int = 1,
    profile: str = "sip-default-v1",
    harness_commit: str = "",
    signals: HarnessSignals = HarnessSignals(),
) -> ObservationRecord:
    status = classify_status(obs, signals)
    fields: dict[str, Field] = {}
    if status == Status.RESPONDED:
        parts = _split_start_line(obs.start_line)
        is_request = parts.get("kind") == "request"
        code = obs.status_code if obs.status_code is not None else parts.get("native_code")
        if parts.get("kind") == "response" and code is not None and not 100 <= int(code) <= 699:
            dclass = classes.OUT_OF_RANGE
        else:
            dclass = classes.decision_class(code, is_request)
        values: dict[str, Any] = {
            "decision_class": dclass,
            "method": parts.get("method"),
            "request_uri": parts.get("request_uri"),
            "sip_version": parts.get("sip_version"),
            "native_code": None if is_request or code is None else str(code),
            "via": obs.via,
            "cseq": obs.cseq,
            "content_length": obs.content_length,
            "content_type_media": None if obs.content_type is None else obs.content_type.split(";", 1)[0].strip(),
            "content_type_params": obs.content_type,
            "body_digest": obs.sdp_sha256 if obs.sdp_sha256 is not None else obs.body_sha256,
            "reason_phrase": parts.get("reason_phrase"),
            "start_line_text": obs.start_line,
        }
        for name, raw in values.items():
            fields[name] = spec.axis(name).field(raw)
    step = StepRecord(status=status, fields=fields, detail=obs.detail or "")
    return ObservationRecord(
        cell="sip",
        impl=obs.stack_id,
        impl_version=impl_version,
        fixture=fixture,
        repeat=repeat,
        profile=profile,
        harness_commit=harness_commit,
        steps=(step,),
    )
