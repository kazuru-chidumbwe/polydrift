#!/usr/bin/env python3
"""Count per-cell lines of code: the cost of bringing a protocol to the shared oracle.

Counts non-blank lines that are not comments in each cell's adapter.py,
classes.py, tiers.yaml and capabilities.yaml, and in core/ for comparison.
Docstrings count as code; they are part of what a new cell has to write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "polydrift"
CELL_FILES = ("adapter.py", "classes.py", "tiers.yaml", "capabilities.yaml")


def loc(path: Path) -> int:
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def main() -> int:
    out: dict[str, dict[str, int]] = {}
    for cell in sorted(p for p in (ROOT / "cells").iterdir() if p.is_dir() and p.name != "__pycache__"):
        row = {name: loc(cell / name) for name in CELL_FILES if (cell / name).is_file()}
        row["total"] = sum(row.values())
        out[cell.name] = row
    core = {p.name: loc(p) for p in sorted((ROOT / "core").glob("*.py"))}
    core["total"] = sum(core.values())
    out["core"] = core
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
