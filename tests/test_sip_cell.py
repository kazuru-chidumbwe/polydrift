"""SIP adapter, decision classes and legacy reproduction."""

import os
from pathlib import Path

import pytest

from polydrift.cells.sip import classes
from polydrift.cells.sip.adapter import HarnessSignals, classify_status, load_spec, to_record
from polydrift.cells.sip.legacy import rescore_suite
from polydrift.core.freeze import bundle
from polydrift.core.oracle import Outcome, compare_pair
from polydrift.core.record import Status, Tier
from polydrift.harness import StackObservation

SPEC = load_spec()


def obs(stack, **kw):
    base = dict(stack_id=stack, start_line="INVITE sip:bob@example.com SIP/2.0", ok=True, status_code=None,
                via="SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bK-1", cseq="1 INVITE")
    base.update(kw)
    return StackObservation(**base)


def cmp(a, b):
    return compare_pair(
        to_record(a, "F", spec=SPEC), to_record(b, "F", spec=SPEC), refusal_class=SPEC.refusal_class
    )


def test_decision_classes():
    assert classes.decision_class(401, False) == "authenticate"
    assert classes.decision_class(403, False) == "stop"
    assert classes.decision_class(499, False) == "fix-request"  # x00 fallback
    assert classes.decision_class(599, False) == "retry-later"
    assert classes.decision_class(183, False) == "no-decision"
    assert classes.decision_class(None, True) == "proceed"


def test_status_classification():
    sig = HarnessSignals()
    assert classify_status(obs("p"), sig) == Status.RESPONDED
    assert classify_status(obs("p", ok=False, detail="pjsip_parse_msg failed"), sig) == Status.REFUSED_INPUT
    assert classify_status(obs("s", ok=False, detail="msg_make failed"), sig) == Status.REFUSED_INPUT
    assert classify_status(obs("p", ok=False, detail="pjsip_observe missing: /x"), sig) == Status.HARNESS_FAULT
    assert classify_status(obs("p", ok=False, detail="no json line rc=-11 err="), sig) == Status.CRASHED
    assert classify_status(obs("k", ok=False, detail="no observation file from kamailio at /tmp/x"), sig) == Status.SILENT
    # Silence only counts as silence when the harness proved the target was ready.
    not_ready = HarnessSignals(ready=False)
    assert classify_status(obs("k", ok=False, detail="no observation file"), not_ready) == Status.HARNESS_FAULT
    assert classify_status(obs("p"), HarnessSignals(canary_ok=False)) == Status.HARNESS_FAULT


def test_lower_case_method_is_t2():
    """3.8: a parser that keeps a lower-case method hands the dialog layer a
    different method."""
    r = cmp(obs("a"), obs("b", start_line="invite sip:bob@example.com SIP/2.0", cseq="1 invite"))
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T2
    assert {"method", "cseq"} <= {d.axis for d in r.diffs}


def test_presentation_only_is_t4_agreement():
    r = cmp(
        obs("a"),
        obs("b", start_line="INVITE  sip:bob@example.com  sip/2.0", via="sip/2.0/udp  192.0.2.1:5060 ; branch=z9hG4bK-1"),
    )
    assert r.outcome == Outcome.AGREEMENT
    assert r.t4_only_agreement


def test_branch_value_is_case_sensitive():
    r = cmp(obs("a"), obs("b", via="SIP/2.0/UDP 192.0.2.1:5060;branch=Z9HG4BK-1"))
    assert r.grade == Tier.T2


def test_parser_rejection_vs_acceptance_is_t1():
    r = cmp(obs("a", ok=False, start_line=None, detail="pjsip_parse_msg failed"), obs("b"))
    assert r.outcome == Outcome.DIVERGENCE and r.grade == Tier.T1


def test_two_rejections_agree():
    r = cmp(
        obs("a", ok=False, start_line=None, detail="pjsip_parse_msg failed"),
        obs("b", ok=False, start_line=None, detail="msg_make failed"),
    )
    assert r.outcome == Outcome.AGREEMENT


def test_response_code_within_class_is_t3():
    a = obs("a", start_line="SIP/2.0 404 Not Found", status_code=404)
    b = obs("b", start_line="SIP/2.0 403 Forbidden", status_code=403)
    r = cmp(a, b)
    assert r.grade == Tier.T3


def test_out_of_range_status_code():
    rec = to_record(obs("a", start_line="SIP/2.0 999 Odd", status_code=999), "F", spec=SPEC)
    assert rec.steps[0].fields["decision_class"].canon == "no-decision"


def test_spec_bundle_covers_oracle_files():
    cell = Path(__file__).resolve().parents[1] / "polydrift" / "cells" / "sip"
    files = bundle(cell).files
    assert {"tiers.yaml", "classes.py", "adapter.py", "capabilities.yaml"} <= set(files)


LEGACY_DIR = os.environ.get("POLYDRIFT_LEGACY_DIR", "")


@pytest.mark.skipif(not LEGACY_DIR, reason="POLYDRIFT_LEGACY_DIR not set (stored 0.3.5 suite outputs)")
def test_legacy_mode_reproduces_035_counts():
    files = sorted(Path(LEGACY_DIR).glob("*-lab-vs-*.json"))
    assert files, f"no suite files in {LEGACY_DIR}"
    for path in files:
        stored, recomputed = rescore_suite(path)
        assert stored == recomputed, path.name
