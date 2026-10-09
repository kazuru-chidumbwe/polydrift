# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Project, Python package and command-line tool are named PolyDrift (`polydrift`). Environment variables use the `POLYDRIFT_` prefix.
- Identifier tokens inside the fixtures (Call-ID, branch, SDP session name) use `polydrift`; message structure is unchanged, so fixture digests differ from release 0.3.5.
- README rewritten as an artifact description with a status table.

## [0.3.5] - 2026-09-16

### Fixed

- LF-normalized `results/V-BASE-restart/` JSON so SHA-256 digests match Linux/macOS readers.
- Document exact-equality claim for the three `E-suite-*.json` files; align complete diverge/error fixture listing with `AXIS-BREAKDOWN.json`.

### Added

- `.gitattributes` with `eol=lf` for results JSON and common text files.

## [0.3.4] - 2026-09-16

### Added

- Deposited laboratory suite digests under `results/V-BASE-restart/` (N=3 restart SHA-256 targets).
- Public documentation: `docs/METRICS.md`, results README.

### Changed

- Headline docs lead with lab-vs-lab pairs; `builtin` framed as calibration.
- Laboratory experiment runner named `tools/run_lab_experiments.py` (pack prefix `polydrift-lab-`).
- Kamailio observe binary PATH resolution when `/usr/sbin` is omitted.

## [0.3.3] - 2026-09-05

### Added

- Body and SDP oracle axes (`body_sha256`, `sdp_sha256`, Content-Type / Content-Length).
- Expanded RFC 4475-class torture corpus: 53 fixtures in total.
- Canonical laboratory pack digests recorded in divergences docs (53-fixture baseline).

## [0.3.2] - 2026-09-05

### Added

- `kamailio-stub` and `kamailio-lab` (UDP + Lua observe).
- Seven-driver laboratory pack and expected JSON examples.

## [0.3.1] - 2026-09-05

### Added

- Torture fixture expansion (40 fixtures).
- Live OPTIONS lab path repair.
- Examples walkthrough under `examples/`.

## [0.3.0] - 2026-09-05

### Added

- `pjsip-lab` and `sofia-lab` observe helpers.
- 32-fixture corpus and laboratory experiment pack.

## [0.2.0] - 2026-08-30

### Added

- Multi-axis oracle (`start_line`, `status_code`, `via`, `cseq`).
- `sofia-stub` driver.
- `polydrift suite` command and divergences documentation.

## [0.1.0] - 2026-08-30

### Added

- Initial harness package, `builtin` driver, and fixture loader.
