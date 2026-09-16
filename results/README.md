# Results (illustrative laboratory insets)

Deposited suite digests for citation. Full laboratory packs may also be archived on Zenodo later; these files are the regeneration targets cited in the manuscript.

## Pack: `V-BASE-restart`

Public alias: `sipdrift-lab-20260914T204058Z-V-BASE-restart`  
Directory: `results/V-BASE-restart/`  
Cite pin: **`0.3.4`** (oracle corpus unchanged from `0.3.3`)

| File | Role |
| --- | --- |
| `DIGESTS.json` | SHA-256 map + headline counts |
| `EXPERIMENT-INDEX.json` | N=3 restart stability index |
| `AXIS-BREAKDOWN.json` | Pair counts + Via mismatch mass |
| `ROLE-GAP-REPORT.json` | UA–UA vs UA–proxy gap + Via cases |
| `E-suite-*-lab.json` | Suite JSON (run 1; digests match runs 2–3) |

## Headline pair counts (run 1)

| Pair | agree | diverge | error |
| --- | ---: | ---: | ---: |
| `pjsip-lab` vs `sofia-lab` | 48 | 1 | 4 |
| `pjsip-lab` vs `kamailio-lab` | 41 | 5 | 7 |
| `sofia-lab` vs `kamailio-lab` | 41 | 6 | 6 |

Corpus size: **53** fixtures.  
UA↔UA diverge+error = **5/53**.  
Each UA↔Kamailio receive pair diverge+error = **12/53**.

## Clean-restart digests

N=3 independent suite runs produced **identical** SHA-256 digests per pair (exact equality). See `DIGESTS.json` for hex values.

## Version-matrix boundary

Kamailio 5.7.4→6.0.1 and pjproject 2.15.1→2.16 cells left the headline agree/diverge/error counts unchanged on this corpus.

## Reproduce

See `docs/LAB-DRIVERS.md` and `tools/run_lab_experiments.py` on a lab host with observe binaries installed.
