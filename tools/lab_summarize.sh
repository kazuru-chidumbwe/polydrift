#!/usr/bin/env bash
# Summarize suite JSON files in a laboratory pack directory.
# Usage: SIPDRIFT_PACK_DIR=/path/to/pack bash tools/lab_summarize.sh
set -euo pipefail
PACK="${SIPDRIFT_PACK_DIR:-}"
if [[ -z "$PACK" || ! -d "$PACK" ]]; then
  echo "Set SIPDRIFT_PACK_DIR to a pack directory" >&2
  exit 1
fi
python3 - "$PACK" <<'PY'
import json, sys
from pathlib import Path
pack = Path(sys.argv[1])
for p in sorted(pack.glob("E-suite-*.json"))[:12]:
    d = json.loads(p.read_text(encoding="utf-8"))
    s = d.get("summary") or d.get("counts") or {}
    print(p.name, s)
PY
