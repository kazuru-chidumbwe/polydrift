#!/bin/bash
set -euo pipefail
ROOT=/opt/atlas/repos/polydrift
cd "$ROOT"

# stop distro service if running (avoid port fights)
sudo systemctl stop kamailio 2>/dev/null || true
sudo systemctl disable kamailio 2>/dev/null || true

sed -i 's/\r$//' tools/kamailio/*.sh tools/kamailio/*.cfg tools/kamailio/*.lua || true
chmod +x tools/kamailio/start_observe.sh

# refresh install
/opt/atlas/venvs/polydrift/bin/pip install -q -e '.[dev]'

bash tools/kamailio/start_observe.sh

export POLYDRIFT_SOFIA_OBSERVE=$ROOT/tools/sofia_observe
export POLYDRIFT_PJSIP_OBSERVE=$ROOT/tools/pjsip_observe
export POLYDRIFT_KAMAILIO_HOST=127.0.0.1
export POLYDRIFT_KAMAILIO_PORT=5090
export POLYDRIFT_KAMAILIO_OBS=/tmp/polydrift-kamailio-obs.json

# smoke one compare
/opt/atlas/venvs/polydrift/bin/python - <<'PY'
from polydrift.drivers.registry import get_driver
from polydrift.run import run_compare, format_report
case = run_compare("F-INVITE-MIN", get_driver("builtin"), get_driver("kamailio-lab"))
print(format_report(case))
case2 = run_compare("F-200-MIN", get_driver("sofia-lab"), get_driver("kamailio-lab"))
print(format_report(case2))
PY

/opt/atlas/venvs/polydrift/bin/python -m pytest -q
/opt/atlas/venvs/polydrift/bin/python tools/run_lab_experiments.py
