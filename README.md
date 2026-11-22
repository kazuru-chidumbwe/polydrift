# PolyDrift

Differential testing of independent protocol implementations.

[![CI](https://github.com/kazuru-chidumbwe/polydrift/actions/workflows/ci.yml/badge.svg)](https://github.com/kazuru-chidumbwe/polydrift/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

PolyDrift sends the same input to several independent implementations of a protocol, records what each one does, and reports where they agree and where they differ. Each input (a *fixture*) is a pinned protocol message. Each implementation is reached through a *driver* that turns its behaviour into an observation on a fixed set of comparison axes. A fixture's outcome for a pair of implementations is `agree`, `diverge`, `error` or `skip`.

One observation model and one graded oracle serve six protocols in five cells. Agreement is equality after declared canonicalisation, and every divergence is graded on five tiers by its effect on a conforming consumer, from T0 (no usable response) to T4 (gone once the message is parsed). The grading is checked by replaying the recorded responses into two real client libraries per cell.

## Cells

| Cell | Implementations | Replay consumers |
| --- | --- | --- |
| SIP, proxy and registrar | Kamailio, reSIProcate repro, ejabberd; OpenSIPS as a declared lineage relative of Kamailio | PJSIP, Sofia-SIP |
| SIP, user agent | PJSIP pjsua, baresip, linphone | none |
| DNS, recursive and forwarding | Unbound, BIND 9, Knot Resolver, PowerDNS Recursor, Technitium; CoreDNS and dnsmasq as forwarders | glibc, musl |
| MQTT | Mosquitto, EMQX, HiveMQ CE, VerneMQ, NanoMQ, FlashMQ | Eclipse Paho, libmosquitto |
| LDAP | OpenLDAP, 389 Directory Server, ApacheDS, Samba AD, lldap | libldap, ldap3 |
| Mail: Sieve and JMAP | Sieve: Pigeonhole, sieve-rs, Stalwart, Cyrus, Exim filter, GNU Mailutils, jSieve. JMAP: Stalwart, Cyrus, Apache James | JMAP: jmapc, go-jmap |

Each cell lives in `polydrift/cells/<cell>/`: an adapter that turns raw traces into observation records, the tier placement of every axis (`tiers.yaml`), the decision classes (`classes.py`), the fixtures with their specification references (`fixtures.yaml`), a laboratory runner and the replay consumers. Comparison, grading, labelling, replay scoring and drift analysis are protocol-independent and live in `polydrift/core/`.

## Status

| Component | State |
| --- | --- |
| Observation model, graded oracle, outcome rules, labels, drift | implemented (`polydrift/core/`) |
| Cells: SIP (proxy, registrar, user agent), DNS, MQTT, LDAP, Sieve, JMAP | implemented |
| Consumer replay at a strict and an action level, with a permutation check of the tier placements | implemented |
| Recall against known bugs from published studies and project trackers | implemented (`polydrift/rq5/`) |
| Campaign plan: targets, recall sets, replay and flake jobs | implemented (`campaign/`, `tools/campaign.py`) |
| SIP corpus of 53 messages with stub and laboratory drivers (the original SIP harness) | implemented |

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

## Quick start (original SIP harness, stub path)

```bash
pytest -q                                                   # run the test suite
polydrift fixtures                                          # list the corpus
polydrift drivers                                           # list registered drivers
polydrift compare F-200-MIN                                 # one fixture: builtin vs pjsip-stub
polydrift suite --right sofia-stub                          # whole corpus, one pair
polydrift compare F-COMPACT-VIA --right sofia-stub --format json
```

Worked examples with expected output are in [`examples/`](examples/).

## Laboratory path (original SIP harness)

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

## Comparison axes (original SIP harness)

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
| `polydrift/core/` | observation model, oracle, labels, replay scoring, drift, freeze bundle |
| `polydrift/cells/` | one folder per cell: adapter, tiers, classes, fixtures, runner, replay consumers |
| `polydrift/rq5/` | known-bug cases for the recall runs |
| `campaign/` | campaign sources, targets and plan |
| `polydrift/` | package: CLI, fixture loader, parser, comparison harness |
| `polydrift/drivers/` | driver protocol, registry, stub and laboratory drivers |
| `fixtures/` | SIP fixture corpus (`.sip`) and its index |
| `tools/` | observe helpers in C, Kamailio configuration and Lua observer, laboratory runners |
| `tests/` | test suite |
| `examples/` | walkthroughs and expected JSON output |
| `docs/` | driver setup, Kamailio scope, axis contract |

## Reproducibility

The harness and oracle specification used for the study's campaign are frozen in commit `a497bdd` and deposited at
[doi:10.5281/zenodo.24445747](https://doi.org/10.5281/zenodo.24445747), together with the analysis plan and the hash of
every file that decides an outcome. The campaign plan is rebuilt and checked with

```bash
python tools/campaign.py build     # targets, recall sets and flake sample from campaign/sources.json
python tools/campaign.py check     # every target, image and case is consistent
python tools/campaign.py jobs --mode campaign --commit a497bdd --out jobs/   # one detached job per cell
```

JSON reports are written with sorted keys, so two runs on the same implementations can be compared by hashing their reports.

## Extending

A new driver implements the `StackDriver` protocol in `polydrift/drivers/protocol.py`, registers itself in `polydrift/drivers/registry.py`, and emits the axis keys in `docs/METRICS.md`. Add a stub first so the driver runs in continuous integration, then the laboratory version.

## Citation

Citation metadata is in [`CITATION.cff`](CITATION.cff) and [`codemeta.json`](codemeta.json). Changes are listed in [`CHANGELOG.md`](CHANGELOG.md).

## Contributing and contact

Issues and pull requests are welcome; see [`CONTRIBUTING.md`](CONTRIBUTING.md). This is an independent research project with best-effort maintenance. Contact: kazuruuni@gmail.com.

## License

MIT. See [`LICENSE`](LICENSE).
