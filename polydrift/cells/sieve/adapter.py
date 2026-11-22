"""Sieve adapter: the engine wrapper's JSON (lab/run.py) as observation records (manuscript 3.1).

One engine run gives two records. `<fixture>` holds the compile result and the intent (every engine has them);
`<fixture>@effect` holds where the message ended up and what was sent, and exists only for engines that executed
the script against a lab mailbox. An engine without an effect layer therefore never diverges on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polydrift.core.canon import identity
from polydrift.core.record import NOT_OBSERVED, Field, ObservationRecord, Status, StepRecord, Tier
from polydrift.core.spec import AxisSpec, CellSpec

CELL_DIR = Path(__file__).resolve().parent
EFFECT = "@effect"


def load_spec() -> CellSpec:
    t = yaml.safe_load((CELL_DIR / "tiers.yaml").read_text(encoding="utf-8"))
    axes = {n: AxisSpec(name=n, tier=Tier(r["tier"]), kappa=identity, rule_clause=r.get("clause", ""))
            for n, r in t["axes"].items()}
    return CellSpec(cell="sieve", axes=axes, refusal_class=t.get("refusal_class"))


def outcome(res: dict[str, Any]) -> str:
    if res.get("compile") == "refused":
        return "refused"
    if res.get("runtime_error") or (res.get("intent") is None and not res.get("effect_only")):
        return "runtime-error"
    return "ok"


def _f(spec: CellSpec, values: dict[str, Any]) -> dict[str, Field]:
    return {k: spec.axis(k).field(v) for k, v in values.items()}


def intent_step(res: dict[str, Any], spec: CellSpec) -> StepRecord:
    if res.get("harness_error") or res.get("runner_error"):
        return StepRecord(status=Status.HARNESS_FAULT, detail=str(res.get("harness_error") or res.get("compile_text"))[:200])
    oc = outcome(res)
    vals = {"decision_class": "fix-request" if oc == "refused" else "proceed", "outcome": oc,
            "compile_class": res.get("compile_class") if oc == "refused" else None,
            "intent": tuple(sorted(res.get("intent") or [])) if oc == "ok" else None}
    fields = _f(spec, vals)
    if oc == "ok" and res.get("effect_only"):
        # no dry run (Cyrus, Stalwart): the compile result is observed, the intent is not
        fields["intent"] = Field(tier=spec.axis("intent").tier, raw=NOT_OBSERVED, canon=None)
    return StepRecord(status=Status.RESPONDED, fields=fields)


def effect_step(res: dict[str, Any], spec: CellSpec) -> StepRecord | None:
    eff = res.get("effect")
    if not eff:
        return None
    stored = tuple(sorted((k, int(v)) for k, v in (eff.get("stored") or {}).items() if int(v) > 0))
    emitted = tuple(sorted((e.get("kind"), tuple(sorted(e.get("to") or []))) for e in (eff.get("emitted") or [])))
    vals = {"stored": stored, "emitted": emitted}
    pre = eff.get("pre") or {}
    # vacation tracking fixtures: did the deliveries before the measured one cause a vacation reply (so the
    # measured delivery's silence or reply is evidence about tracking); None for every other fixture
    vals["pre_vacation_sent"] = (pre.get("vacations", 0) > 0) if "senders" in pre and not pre.get("error") else None
    if "flags" in eff:
        # IMAP flags of the stored messages per folder (RFC 5232)
        vals["stored_flags"] = tuple(sorted((k, tuple(v)) for k, v in (eff.get("flags") or {}).items()))
    if eff.get("lmtp"):
        # the reply to DATA the sending MTA acts on (RFC 5429 2.1; RFC 5321 4.2 and 4.5.3.1.5)
        r = eff["lmtp"]
        vals["lmtp_reply"] = (r.get("class"), r.get("enhanced"), r.get("lines"), (r.get("max_line") or 0) <= 512,
                              bool(r.get("marker")))
    if any("facts" in e for e in eff.get("emitted") or []):
        # header facts of each sent message, by kind (RFC 5228 4.2, RFC 5230 5, RFC 5429 2.1 and 2.2)
        vals["emitted_headers"] = tuple(sorted((e.get("kind"), tuple(sorted((e.get("facts") or {}).items())))
                                               for e in eff.get("emitted") or []))
        sent = eff.get("emitted") or []
        def per(kind: str, key: str, fn=lambda x: x) -> tuple:
            return tuple(fn((e.get("facts") or {}).get(key)) for e in sent if e.get("kind") == kind)
        vals["redirect_received_more"] = per("redirect", "received_delta", lambda d: d is not None and d > 0)
        vals["redirect_env_from"] = per("redirect", "env_from")
        vals["vacation_env_from"] = per("vacation", "env_from")
        vals["vacation_in_reply_to"] = per("vacation", "in_reply_to_orig")
        vals["vacation_subject"] = per("vacation", "subject")
        vals["vacation_subject_encoded"] = per("vacation", "subject", lambda x: bool(x) and "=?" in x)
        vals["vacation_date"] = per("vacation", "date_present")
        vals["vacation_from"] = per("vacation", "from", lambda x: (x or "").strip("<>").lower() or None)
        vals["vacation_to"] = per("vacation", "to", lambda x: (x or "").strip("<>").lower() or None)
        vals["mdn_disposition"] = per("reject", "mdn_disposition")
        vals["dsn_action"] = per("dsn", "dsn_action")
        vals["reason_in_text"] = tuple((e.get("facts") or {}).get("marker") for e in sent
                                       if e.get("kind") in ("reject", "dsn"))
        vals["vacation_notify_never"] = per("vacation", "notify_never")
        vals["reject_text_extra"] = tuple((e.get("facts") or {}).get("text_beyond_reason") for e in sent
                                          if e.get("kind") in ("reject", "dsn"))
        vals["notice_has_script_edit"] = tuple((e.get("facts") or {}).get("notice_has_script_edit") for e in sent
                                               if e.get("kind") in ("reject", "dsn"))
    return StepRecord(status=Status.RESPONDED, fields=_f(spec, vals))


def to_records(res: dict[str, Any], impl: str, fixture: str, *, spec: CellSpec, repeat: int = 1,
               impl_version: str = "", harness_commit: str = "") -> list[ObservationRecord]:
    base = dict(cell="sieve", impl=impl, impl_version=impl_version, repeat=repeat, harness_commit=harness_commit)
    out = []
    # every engine gives the compile record, so compile-time rows are scored for engines without a dry run too
    # (whose intent field is marked not observed)
    out.append(ObservationRecord(fixture=fixture, steps=(intent_step(res, spec),), **base))
    eff = effect_step(res, spec)
    if eff is not None:
        out.append(ObservationRecord(fixture=fixture + EFFECT, steps=(eff,), **base))
    return out
