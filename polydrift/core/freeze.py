"""Oracle specification bundle and deposit gate (manuscript 3.6, Freezing).

A cell's oracle specification is the set of files under its cell directory
that decide outcomes: tiers.yaml, classes.py, adapter.py, capabilities.yaml,
the fixtures with their predicates, and the replay sampling rule. Before the
first cross-implementation run, the bundle is deposited on Zenodo and its
hash recorded in ``deposits.json``. The runner refuses to compare a cell whose
current spec hash is not recorded there.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SPEC_PATTERNS = (
    "tiers.yaml",
    "classes.py",
    "adapter.py",
    "capabilities.yaml",
    "replay.yaml",
    "replay.py",
    "fixtures.yaml",
    "fixtures_rq5.yaml",
    "fixtures/**/*",
    "lab/consumers/**/*",
)
# protocol-independent files that decide outcomes for every cell (comparison, labels, replay rules, majority votes)
CORE_FILES = ("oracle.py", "record.py", "spec.py", "label.py", "repeat.py", "drift.py", "baselines.py",
              "replay.py", "replay_action.py")


class SpecNotDeposited(RuntimeError):
    """Raised when a cell's oracle specification has no matching deposit."""


@dataclass(frozen=True)
class SpecBundle:
    cell: str
    files: dict[str, str]  # relative path -> sha256

    @property
    def spec_hash(self) -> str:
        h = hashlib.sha256()
        for rel, digest in sorted(self.files.items()):
            h.update(f"{rel}\0{digest}\n".encode())
        return h.hexdigest()

    def manifest(self) -> dict[str, object]:
        return {"cell": self.cell, "spec_hash": self.spec_hash, "files": dict(sorted(self.files.items()))}


def _sha256(path: Path) -> str:
    # Line endings are normalised so a checkout on another OS hashes the same.
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def bundle(cell_dir: str | Path, cell: str | None = None) -> SpecBundle:
    root = Path(cell_dir)
    files: dict[str, str] = {}
    for pattern in SPEC_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(root).as_posix()] = _sha256(path)
    core = root.parent.parent / "core"
    if (core / "oracle.py").is_file():
        for name in CORE_FILES:
            if (core / name).is_file():
                files[f"core/{name}"] = _sha256(core / name)
    return SpecBundle(cell=cell or root.name, files=files)


def load_deposits(path: str | Path) -> dict[str, list[dict[str, str]]]:
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def check_deposited(spec: SpecBundle, deposits_path: str | Path) -> dict[str, str]:
    """Return the deposit entry matching the current spec hash or raise."""
    for entry in load_deposits(deposits_path).get(spec.cell, []):
        if entry.get("spec_hash") == spec.spec_hash:
            return entry
    raise SpecNotDeposited(
        f"cell {spec.cell!r}: oracle spec {spec.spec_hash[:12]} has no deposit in {deposits_path}; "
        "deposit it before any cross-implementation run, or run with --provisional "
        "(results are then marked provisional and are not study results)"
    )
