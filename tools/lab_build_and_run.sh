#!/bin/bash
set -euo pipefail
cd /opt/atlas/repos/polydrift
python3 tools/gen_extra_fixtures.py >/dev/null
python3 tools/normalize_fixtures.py >/dev/null
# malformed must stay empty
: > fixtures/malformed_start.sip
cd tools
make sofia
make pjsip PJDIR=/opt/atlas/pjproject
ls -la sofia_observe pjsip_observe
echo '--- sofia 200 ---'
./sofia_observe ../fixtures/response_200_min.sip
echo '--- pjsip 200 ---'
./pjsip_observe ../fixtures/response_200_min.sip
echo '--- pjsip invite ---'
./pjsip_observe ../fixtures/invite_min.sip
echo '--- pjsip compact ---'
./pjsip_observe ../fixtures/compact_via.sip
cd /opt/atlas/repos/polydrift
/opt/atlas/venvs/polydrift/bin/pip install -q -e '.[dev]'
export POLYDRIFT_SOFIA_OBSERVE=/opt/atlas/repos/polydrift/tools/sofia_observe
export POLYDRIFT_PJSIP_OBSERVE=/opt/atlas/repos/polydrift/tools/pjsip_observe
/opt/atlas/venvs/polydrift/bin/python -m polydrift.cli drivers
/opt/atlas/venvs/polydrift/bin/python -m polydrift.cli fixtures | wc -l
/opt/atlas/venvs/polydrift/bin/python tools/run_lab_experiments.py
