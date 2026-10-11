# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Shared comparison layer (`polydrift/core`): observation records with six liveness statuses, canonicalisation helpers, tiered pairwise outcomes (incomparable, liveness gating, co-failure, graded divergence, agreement) and partitions, repeatability over clean restarts, fault-class and consequence labels from fixture predicates, version-drift signatures, raw and coarse baseline oracles, replay sampling, and a deposit gate for each cell's oracle specification.
- Cells: SIP adapter, decision classes and tier table; DNS adapter for role-consistency observations. `cell-run` and `cell-analyse` commands.
- Legacy mode for the SIP cell that re-scores stored suite outputs with the eight-axis equality oracle.
- SIP cell: 53-message fixture corpus, stub drivers for continuous integration, laboratory drivers for PJSIP, Sofia-SIP and Kamailio, comparison on eight axes with text and JSON reports.
- Laboratory runner that writes one JSON report per driver pair and a pack manifest.
