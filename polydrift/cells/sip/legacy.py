"""Legacy mode: the 0.3.x oracle (raw equality on eight axes, one ok flag).

Used once, as an acceptance test: re-scoring stored 0.3.5 suite outputs with
this oracle must reproduce their published counts exactly, so any change in
SIP results under the shared model is attributable to the model alone.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from polydrift.harness import StackObservation, classify_observations

_FIELDS = (
    "stack_id",
    "start_line",
    "status_code",
    "via",
    "cseq",
    "content_type",
    "content_length",
    "body_sha256",
    "sdp_sha256",
    "ok",
    "detail",
)


def observation_from_dict(data: dict[str, Any]) -> StackObservation:
    return StackObservation(**{k: data.get(k) if k != "detail" else data.get(k) or "" for k in _FIELDS})


def rescore_suite(path: str | Path) -> tuple[dict[str, int], dict[str, int]]:
    """Return (stored summary, recomputed summary) for one stored suite file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    for case in data["cases"]:
        left = observation_from_dict(case["left"])
        right = observation_from_dict(case["right"])
        counts[classify_observations(case["fixture_id"], left, right).status.value] += 1
    return dict(data["summary"]), dict(counts)
