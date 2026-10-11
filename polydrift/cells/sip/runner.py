"""SIP cell runner: fixtures x repeats for one implementation -> records.

Each repeat starts from a clean restart (``restart_cmd``, run through the
shell; the in-process parser drivers start a fresh helper process per
fixture, so they need none). A canary fixture runs before and after every
batch. If either canary fails, every record of that batch is a harness
fault: the instrument, not the implementation, is in doubt.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from polydrift.cells.sip.adapter import HarnessSignals, load_spec, to_record
from polydrift.core.record import ObservationRecord
from polydrift.drivers.protocol import StackDriver
from polydrift.fixtures import FIXTURE_INDEX, load_fixture
from polydrift.harness import StackObservation

CANARY = "F-INVITE-MIN"
CANARY_START_LINE = "INVITE"


def harness_commit() -> str:
    root = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "-c", "safe.directory=*", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        return f"{sha}{'-dirty' if dirty else ''}" if sha else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def canary_ok(obs: StackObservation) -> bool:
    return bool(obs.ok and obs.start_line and obs.start_line.upper().startswith(CANARY_START_LINE))


def run_impl(
    driver: StackDriver,
    *,
    repeats: int = 3,
    fixture_ids: list[str] | None = None,
    impl_version: str = "",
    restart: Callable[[], None] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> list[ObservationRecord]:
    spec = load_spec()
    ids = fixture_ids if fixture_ids is not None else list(FIXTURE_INDEX)
    commit = harness_commit()
    records: list[ObservationRecord] = []
    for r in range(1, repeats + 1):
        if restart is not None:
            restart()
        start = canary_ok(driver.observe(load_fixture(CANARY)))
        batch = [(fid, driver.observe(load_fixture(fid))) for fid in ids]
        end = canary_ok(driver.observe(load_fixture(CANARY)))
        log(f"{driver.stack_id} repeat {r}: canary start={start} end={end}, {len(batch)} fixtures")
        signals = HarnessSignals(ready=start, sent=True, canary_ok=start and end)
        for fid, obs in batch:
            records.append(
                to_record(
                    obs,
                    fid,
                    spec=spec,
                    impl_version=impl_version,
                    repeat=r,
                    harness_commit=commit,
                    signals=signals,
                )
            )
    return records


def shell_restart(cmd: str, timeout: int = 120) -> Callable[[], None]:
    def _restart() -> None:
        subprocess.run(cmd, shell=True, check=True, timeout=timeout)

    return _restart
