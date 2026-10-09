# PolyDrift

Differential testing of independent protocol implementations.

[![CI](https://github.com/kazuru-chidumbwe/polydrift/actions/workflows/ci.yml/badge.svg)](https://github.com/kazuru-chidumbwe/polydrift/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

PolyDrift sends the same input to several independent implementations of a protocol, records what each one does, and reports where they agree and where they differ. Each input (a *fixture*) is a pinned protocol message. Each implementation is reached through a *driver* that turns its behaviour into an observation on a fixed set of comparison axes. A fixture's outcome for a pair of implementations is `agree`, `diverge`, `error` or `skip`.

This repository is the software artifact for a cross-protocol study in preparation. The SIP cell is implemented and is described below. The protocol-independent observation model and the further protocol cells used by the study are being added here; the [status table](#status) says what is present today.

## Status

| Component | State |
| --- | --- |
| SIP fixture corpus: 53 messages (requests, responses, compact and folded headers, SDP bodies, RFC 4475-class torture cases) | implemented |
| Stub drivers for continuous integration: `builtin`, `pjsip-stub`, `sofia-stub`, `kamailio-stub` | implemented |
| Laboratory drivers for PJSIP, Sofia-SIP and Kamailio | implemented |
| Comparison on eight SIP axes, with text and JSON reports | implemented |
| Protocol-independent observation model and graded oracle | in development |
| Further protocol cells: DNS, MQTT, LDAP, Sieve and JMAP | planned |

## Requirements

- Python 3.11 or later; `pytest` for the test suite.
- Stub path: no native SIP libraries; runs on Linux, macOS and Windows.
- Laboratory path: Linux, a C compiler, PJSIP and Sofia-SIP development libraries, and Kamailio with its Lua module. See [`docs/LAB-DRIVERS.md`](docs/LAB-DRIVERS.md).

## Installation

```bash
git clone https://github.com/kazuru-chidumbwe/polydrift.git
cd polydrift
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Quick start (stub path)

```bash
pytest -q                                                   # run the test suite
polydrift fixtures                                          # list the corpus
polydrift drivers                                           # list registered drivers
polydrift compare F-200-MIN                                 # one fixture: builtin vs pjsip-stub
polydrift suite --right sofia-stub                          # whole corpus, one pair
polydrift compare F-COMPACT-VIA --right sofia-stub --format json
```

Worked examples with expected output are in [`examples/`](examples/).

## Laboratory path

Build the observe helpers on a Linux host, point the drivers at them, and compare real implementations:

```bash
make -C tools sofia
export POLYDRIFT_SOFIA_OBSERVE=$PWD/tools/sofia_observe
make -C tools pjsip PJDIR=/path/to/pjproject
export POLYDRIFT_PJSIP_OBSERVE=$PWD/tools/pjsip_observe
polydrift suite --left pjsip-lab --right sofia-lab

bash tools/kamailio/start_observe.sh
export POLYDRIFT_KAMAILIO_PORT=5090
polydrift suite --left sofia-lab --right kamailio-lab
```

`tools/run_lab_experiments.py` runs every driver pair over the corpus and writes a pack: one JSON report per suite plus a `meta.json` naming the drivers, fixtures, host and start time. The Kamailio driver observes the proxy's receive path; its scope, and the fixtures it cannot observe, are listed in [`docs/KAMAILIO-SCOPE.md`](docs/KAMAILIO-SCOPE.md).

## Comparison axes

| Axis | Meaning |
| --- | --- |
| `start_line` | request line or status line |
| `status_code` | response status code |
| `via` | top Via value |
| `cseq` | CSeq value |
| `content_type`, `content_length` | body headers |
| `body_sha256`, `sdp_sha256` | digests of the raw body and of the normalised SDP |

The classification contract is in [`docs/METRICS.md`](docs/METRICS.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `polydrift/` | package: CLI, fixture loader, parser, comparison harness |
| `polydrift/drivers/` | driver protocol, registry, stub and laboratory drivers |
| `fixtures/` | SIP fixture corpus (`.sip`) and its index |
| `tools/` | observe helpers in C, Kamailio configuration and Lua observer, laboratory runners |
| `tests/` | test suite |
| `examples/` | walkthroughs and expected JSON output |
| `docs/` | driver setup, Kamailio scope, axis contract |

## Reproducibility

JSON reports are written with sorted keys, so two runs on the same implementations can be compared by hashing their reports. Cite a tagged release, once one is published, rather than `main`.

## Extending

A new driver implements the `StackDriver` protocol in `polydrift/drivers/protocol.py`, registers itself in `polydrift/drivers/registry.py`, and emits the axis keys in `docs/METRICS.md`. Add a stub first so the driver runs in continuous integration, then the laboratory version.

## Citation

Citation metadata is in [`CITATION.cff`](CITATION.cff) and [`codemeta.json`](codemeta.json). Changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

## Contributing and contact

Issues and pull requests are welcome; see [`CONTRIBUTING.md`](CONTRIBUTING.md). This is an independent research project with best-effort maintenance. Contact: kazuruuni@gmail.com.

## License

MIT. See [`LICENSE`](LICENSE).
