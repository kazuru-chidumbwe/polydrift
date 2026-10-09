# Minimal walkthroughs for reviewers (stub / CI path).

## 1. Install and smoke

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m polydrift.cli compare F-200-MIN
python -m polydrift.cli suite --right sofia-stub
```

## 2. JSON report

```bash
python -m polydrift.cli compare F-COMPACT-VIA --left builtin --right sofia-stub --format json
```

## 3. Lab path (optional)

Requires `tools/sofia_observe` and/or `tools/pjsip_observe` — see `docs/LAB-DRIVERS.md`.

```bash
export POLYDRIFT_SOFIA_OBSERVE=$PWD/tools/sofia_observe
export POLYDRIFT_PJSIP_OBSERVE=$PWD/tools/pjsip_observe
python -m polydrift.cli suite --left pjsip-lab --right sofia-lab --format json

# Secondary: harness reference vs a lab driver
python -m polydrift.cli suite --left builtin --right sofia-lab --format json
```

## 4. Expected JSON (reviewer gold)

Checked-in samples under `examples/expected/` (including the body and SDP axes):

```bash
python -m polydrift.cli compare F-200-MIN --left builtin --right sofia-stub --format json
# compare to examples/expected/F-200-MIN.builtin-vs-sofia-stub.json

python -m polydrift.cli compare F-INVITE-SDP --left builtin --right sofia-stub --format json
# compare to examples/expected/F-INVITE-SDP.builtin-vs-sofia-stub.json
```

```bash
python tools/run_lab_experiments.py
```

Writes `/opt/atlas/polydrift-packs/polydrift-lab-<UTC>/`.