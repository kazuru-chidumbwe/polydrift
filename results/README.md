# Results (illustrative laboratory insets)

Committed summary counts for the `0.3.3` laboratory packs used as instrument demonstrations.
Full suite JSON packs may live outside this repository until a Zenodo deposit is minted.
SHA-256 digests below are the regeneration targets from the author’s laboratory packs.

## Headline pair counts (`V-BASE` restart, run 1)

| Pair | agree | diverge | error |
| --- | ---: | ---: | ---: |
| `pjsip-lab` vs `sofia-lab` | 48 | 1 | 4 |
| `pjsip-lab` vs `kamailio-lab` | 41 | 5 | 7 |
| `sofia-lab` vs `kamailio-lab` | 41 | 6 | 6 |

Corpus size: **53** fixtures.  
UA↔UA diverge+error = **5/53**.  
Each UA↔Kamailio receive pair diverge+error = **12/53**.

## Clean-restart digests

N=3 independent suite runs on the baseline restart pack produced **identical** SHA-256 digests per pair (exact equality). Host labels and full hex digests are recorded in the author’s private experiment packs; publish them with Zenodo or a `results/*.json` dump before journal upload.

## Version-matrix boundary

Kamailio 5.7.4→6.0.1 and pjproject 2.15.1→2.16 cells left the headline agree/diverge/error counts unchanged on this corpus.

## Reproduce

See `docs/LAB-DRIVERS.md` and `tools/run_lab_experiments.py` on a lab host with observe binaries installed.
