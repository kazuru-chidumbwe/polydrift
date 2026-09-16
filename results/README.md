# Results (illustrative laboratory insets)

Deposited suite digests for citation. Full laboratory packs may also be archived on Zenodo later; these files are the regeneration targets cited in the manuscript.

## Pack: `V-BASE-restart`

Public alias: `sipdrift-lab-20260914T204058Z-V-BASE-restart`  
Directory: `results/V-BASE-restart/`  
Cite pin: **`0.3.5`** (oracle corpus unchanged from `0.3.3` / `0.3.4`)

All JSON files are stored with **LF** newlines (see `.gitattributes`). Hash on Linux/macOS without rewriting line endings.

| File | Role | Exact-equality (N=3) |
| --- | --- | --- |
| `DIGESTS.json` | SHA-256 map + headline counts | n/a (index) |
| `E-suite-*-lab.json` | Suite JSON (run 1; digests match runs 2–3) | **yes** |
| `AXIS-BREAKDOWN.json` | Pair counts + Via mismatch mass | derived |
| `EXPERIMENT-INDEX.json` | Restart stability index / pack manifest | manifest pin |
| `ROLE-GAP-REPORT.json` | UA–UA vs UA–proxy gap + Via cases | manifest pin |

## Headline pair counts (run 1)

| Pair | agree | diverge | error |
| --- | ---: | ---: | ---: |
| `pjsip-lab` vs `sofia-lab` | 48 | 1 | 4 |
| `pjsip-lab` vs `kamailio-lab` | 41 | 5 | 7 |
| `sofia-lab` vs `kamailio-lab` | 41 | 6 | 6 |

Corpus size: **53** fixtures.  
UA↔UA diverge+error = **5/53**.  
Each UA↔Kamailio receive pair diverge+error = **12/53**.

## Reproduce

```bash
sha256sum results/V-BASE-restart/E-suite-*.json
```

See `DIGESTS.json` for expected hex digests and `docs/LAB-DRIVERS.md` for laboratory setup.
