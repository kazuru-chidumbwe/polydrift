"""JMAP adapter: driver step observations -> ObservationRecord.

Status: harness-fault when the harness could not reach the API; responded otherwise (an HTTP error status is an
answer). Decision class: request level from the HTTP status and problem type, else the first method error, else
proceed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polydrift.core.canon import identity
from polydrift.core.record import ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent

METHOD_ERRORS = {
    "serverUnavailable": "retry-later", "serverFail": "retry-later", "serverPartialFail": "retry-later",
    "unknownMethod": "fix-request", "invalidArguments": "fix-request", "invalidResultReference": "fix-request",
    "forbidden": "stop", "accountNotFound": "fix-request", "accountNotSupportedByMethod": "fix-request",
    "accountReadOnly": "stop", "cannotCalculateChanges": "stop", "requestTooLarge": "fix-request",
    "stateMismatch": "retry-later", "unsupportedFilter": "fix-request", "unsupportedSort": "fix-request",
    "anchorNotFound": "fix-request", "fromAccountNotFound": "fix-request", "fromAccountNotSupportedByMethod": "fix-request",
    "tooManyChanges": "fix-request",
}


def http_class(code: int) -> str:
    if code < 300:
        return "proceed"
    if code in (401, 407):
        return "authenticate"
    if code in (403, 404, 410):
        return "stop"
    if code in (408, 429, 502, 503, 504):
        return "retry-later"
    if code >= 500:
        return "retry-later"
    return "fix-request"


def load_spec() -> CellSpec:
    t = yaml.safe_load((CELL_DIR / "tiers.yaml").read_text(encoding="utf-8"))
    axes = {n: AxisSpec(name=n, tier=Tier(r["tier"]), kappa=identity, rule_clause=r.get("clause", ""))
            for n, r in t["axes"].items()}
    return CellSpec(cell="jmap", axes=axes, refusal_class=t.get("refusal_class"))


def _type(t: Any) -> Any:
    if not isinstance(t, str):
        return t
    return t.rsplit(":", 1)[-1] if t.startswith("urn:ietf:params:jmap:error:") else t


def step_fields(obs: dict[str, Any], spec: CellSpec) -> dict:
    v: dict[str, Any] = {}
    code = obs.get("http", 0)
    j = obs.get("json") if isinstance(obs.get("json"), dict) else None
    v["http_status"] = code
    if code != 200 or j is None or "methodResponses" not in j:
        v["decision_class"] = http_class(code) if code != 200 else "no-decision"
        v["request_error"] = _type(j.get("type")) if j else (None if code == 200 else "non-json")
        return {k: spec.axis(k).field(x) for k, x in v.items()}
    resps = [r for r in j["methodResponses"] if isinstance(r, list) and len(r) == 3]
    v["method_names"] = [r[0] for r in resps]
    errs = [(_type(r[1].get("type")) if r[0] == "error" and isinstance(r[1], dict) else None) for r in resps]
    v["method_errors"] = errs
    first = next((e for e in errs if e), None)
    v["decision_class"] = METHOD_ERRORS.get(first, "stop") if first else "proceed"
    sets, gets, queries, changes, states = [], [], [], [], []
    for name, args, _cid in resps:
        if not isinstance(args, dict):
            continue
        states.append("state" in args or "newState" in args)
        if name.endswith("/set"):
            sets.append({
                "created": sorted((args.get("created") or {}).keys()),
                "notCreated": sorted((k, _type((e or {}).get("type"))) for k, e in (args.get("notCreated") or {}).items()),
                "updated": len(args.get("updated") or {}),
                "notUpdated": sorted(_type((e or {}).get("type")) for e in (args.get("notUpdated") or {}).values()),
                "destroyed": len(args.get("destroyed") or []),
                "notDestroyed": sorted(_type((e or {}).get("type")) for e in (args.get("notDestroyed") or {}).values()),
            })
        elif name.endswith("/get"):
            gets.append((len(args.get("list") or []), len(args.get("notFound") or [])))
        elif name.endswith("/query"):
            queries.append((len(args.get("ids") or []), "total" in args))
        elif name.endswith("/changes"):
            changes.append((len(args.get("created") or []), len(args.get("updated") or []),
                            len(args.get("destroyed") or []), args.get("hasMoreChanges")))
    if sets:
        v["set_outcome"] = sets
    if gets:
        v["get_shape"] = gets
    if queries:
        v["query_shape"] = queries
    if changes:
        v["changes_shape"] = changes
    v["state_present"] = states
    return {k: spec.axis(k).field(x) for k, x in v.items()}


def to_record(observations: list[dict[str, Any]], impl: str, fixture: str, *, spec: CellSpec, repeat: int = 1,
              impl_version: str = "", harness_commit: str = "") -> ObservationRecord:
    steps = []
    for obs in observations:
        if obs.get("error"):
            steps.append(StepRecord(status=Status.HARNESS_FAULT, detail=obs["error"]))
        else:
            steps.append(StepRecord(status=Status.RESPONDED, fields=step_fields(obs, spec)))
    return ObservationRecord(cell="jmap", impl=impl, impl_version=impl_version, fixture=fixture, repeat=repeat,
                             harness_commit=harness_commit, steps=tuple(steps))
