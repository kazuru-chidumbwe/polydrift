# sipdrift

Differential testing harness for open-source SIP/VoIP stacks.

Pinned SIP fixtures are observed through multiple drivers, normalized onto shared
oracle axes (start-line, status, Via, CSeq, Content-Type/Length, body and SDP
digests), and classified as `agree`, `diverge`, `error`, or `skip`.

[![CI](https://github.com/kazuru-chidumbwe/sipdrift/actions/workflows/ci.yml/badge.svg)](https://github.com/kazuru-chidumbwe/sipdrift/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Cite pin [`0.3.4`](https://github.com/kazuru-chidumbwe/sipdrift/tree/0.3.4).
See [CODE_METADATA.md](CODE_METADATA.md), [CITATION.cff](CITATION.cff), and [codemeta.json](codemeta.json).
Cite the release tag, not floating `main`.

## Quick start (stub / CI)

No native SIP libraries required.

```bash
git clone https://github.com/kazuru-chidumbwe/sipdrift.git
cd sipdrift
git checkout 0.3.4
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -U pip && pip install -e ".[dev]"
pytest -q
sipdrift compare F-200-MIN
sipdrift suite --right sofia-stub
```

## What it does

| Capability | Role |
| --- | --- |
| Fixture corpus | Pinned `.sip` messages under `fixtures/` (53 at `0.3.4`) |
| Drivers | `builtin` / `*-stub` for CI; `pjsip-lab` / `sofia-lab` / `kamailio-lab` for host labs |
| Oracle | Shared axes + agree / diverge / error / skip |
| Suite JSON | Machine-readable pairwise results; SHA-256 digests for restart checks |

Headline laboratory comparisons:

| Pair | Role |
| --- | --- |
| `pjsip-lab` vs `sofia-lab` | UA library vs UA library |
| `pjsip-lab` / `sofia-lab` vs `kamailio-lab` | UA library vs proxy receive path |

`builtin` and stub drivers are for continuous integration and calibration, not a fourth production SIP stack.

## Lab path (optional)

Requires host-built observe helpers. See [`docs/LAB-DRIVERS.md`](docs/LAB-DRIVERS.md).

```bash
make -C tools sofia
export SIPDRIFT_SOFIA_OBSERVE=$PWD/tools/sofia_observe
make -C tools pjsip PJDIR=/path/to/pjproject
export SIPDRIFT_PJSIP_OBSERVE=$PWD/tools/pjsip_observe
sipdrift suite --left pjsip-lab --right sofia-lab

bash tools/kamailio/start_observe.sh
export SIPDRIFT_KAMAILIO_PORT=5090
sipdrift suite --left sofia-lab --right kamailio-lab
```

Known divergences: [`docs/DIVERGENCES.md`](docs/DIVERGENCES.md) · Kamailio scope: [`docs/KAMAILIO-SCOPE.md`](docs/KAMAILIO-SCOPE.md) · Metrics: [`docs/METRICS.md`](docs/METRICS.md) · Tags: [`docs/TAGS.md`](docs/TAGS.md).

Committed illustrative counts from laboratory packs: [`results/`](results/).

## Extending

To add a driver: implement the observe protocol in `sipdrift/drivers/`, register it, and keep axis keys aligned with `docs/METRICS.md`. Stub first, then lab.

## Cite

See [`CITATION.cff`](CITATION.cff). Changelog: [`CHANGELOG.md`](CHANGELOG.md).

## Contributing and support

Contributions via issues and pull requests are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md). Best-effort maintainer response; this is an independent research project. Support email: kazuruuni@gmail.com.

## License

MIT. See [LICENSE](LICENSE).
