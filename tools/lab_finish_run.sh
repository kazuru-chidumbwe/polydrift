#!/bin/bash
set -euo pipefail
cd /opt/atlas/repos/polydrift
python3 tools/gen_extra_fixtures.py >/dev/null || true
python3 tools/gen_torture_fixtures.py
python3 tools/normalize_fixtures.py
: > fixtures/malformed_start.sip
/opt/atlas/venvs/polydrift/bin/pip install -q -e '.[dev]'
export POLYDRIFT_SOFIA_OBSERVE=/opt/atlas/repos/polydrift/tools/sofia_observe
export POLYDRIFT_PJSIP_OBSERVE=/opt/atlas/repos/polydrift/tools/pjsip_observe
# kill stale responders
pkill -f sip_udp_responder.py 2>/dev/null || true
/opt/atlas/venvs/polydrift/bin/python -m pytest -q
/opt/atlas/venvs/polydrift/bin/python -m polydrift.cli fixtures | wc -l
/opt/atlas/venvs/polydrift/bin/python tools/run_lab_experiments.py
