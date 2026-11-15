"""Which fixture file a cell runner loads (manuscript 4.4).

The main set is each cell's ``fixtures.yaml``. The RQ5 known-bug fixtures are a separate set,
``fixtures_rq5.yaml``, written from bug reports and kept out of the RQ3 counts. A run selects the set with the
environment variable ``POLYDRIFT_FIXTURE_SET`` (unset or ``main``: the main set; ``rq5``: the known-bug set), so the
runners, the stability script and ``cell-report`` read the same file without new arguments.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV = "POLYDRIFT_FIXTURE_SET"
SETS = ("main", "rq5")


def current_set() -> str:
    s = os.environ.get(ENV, "").strip() or "main"
    if s not in SETS:
        raise ValueError(f"{ENV}={s!r}: expected one of {SETS}")
    return s


def fixtures_file(cell_dir: Path) -> Path:
    s = current_set()
    return cell_dir / ("fixtures.yaml" if s == "main" else f"fixtures_{s}.yaml")
