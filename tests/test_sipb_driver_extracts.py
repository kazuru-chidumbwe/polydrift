"""SIP driver timer extracts (tasklist 1.6): retransmission schedules on the T1 doubling scale, first-copy time."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "polydrift" / "cells" / "sipb" / "lab"))
import driver  # noqa: E402


def _reqs(times, method="MESSAGE"):
    return [{"kind": "req", "method": method, "at": t} for t in times]


def test_retx_maps_jittered_gaps_to_the_doubling_scale():
    got = driver.evaluate("retx:bob=MESSAGE", {"bob": _reqs([0, 0.52, 1.49, 3.6, 7.5, 11.4, 15.6, 19.5, 23.5, 27.5, 31.6])})
    assert got == {"copies": 11, "gaps": [0.5, 1, 2, 4, 4, 4, 4, 4, 4, 4]}


def test_retx_counts_response_codes_and_ignores_other_messages():
    msgs = [{"kind": "resp", "code": 487, "at": t} for t in (0, 0.5, 1.5)] + [{"kind": "resp", "code": 200, "at": 0.1}]
    assert driver.evaluate("retx:alice=487", {"alice": msgs}) == {"copies": 3, "gaps": [0.5, 1]}


def test_firstat_is_seconds_from_step_start_in_tens():
    assert driver.evaluate("firstat:bob=CANCEL", {"bob": _reqs([125.3], "CANCEL"), "t0": 0.0}) == 120
    assert driver.evaluate("firstat:bob=CANCEL", {"bob": [], "t0": 0.0}) is None
