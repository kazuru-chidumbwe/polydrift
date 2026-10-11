# Shared oracle

Every cell uses the same comparison layer in `polydrift/core`. Per-protocol code is limited to the cell folder under `polydrift/cells/<cell>/`.

## Layout

| Path | Role |
| --- | --- |
| `core/record.py` | Observation record: one step record per fixture step plus an end state; each field keeps `raw` next to `canon` |
| `core/canon.py` | Canonicalisation helpers (case folding, whitespace, unordered collections, buckets, consistent renaming of volatile identifiers, digest after canonicalisation) |
| `core/oracle.py` | Pairwise outcome and partitions per tier |
| `core/repeat.py` | Self-disagreement over N clean restarts gives the flaky (impl, fixture, axis) set |
| `core/label.py` | Fault class and consequence from fixture predicates |
| `core/drift.py` | Divergence signatures, introduced / fixed / persistent, convergence |
| `core/baselines.py` | Raw-value and coarse (status + native code) oracles |
| `core/replay.py` | Consumer replay sampling rule and confusion matrix |
| `core/freeze.py` | Oracle specification bundle and deposit check |
| `cells/<cell>/adapter.py` | Raw trace to observation record, liveness status rules |
| `cells/<cell>/classes.py` | Native code to decision class |
| `cells/<cell>/tiers.yaml` | Axis to tier, with the clause of the tier rule each placement relies on |
| `cells/<cell>/capabilities.yaml` | Capability matrix |

`core` never imports a cell module; a test enforces this.

## Liveness statuses

`responded`, `silent`, `closed`, `crashed`, `refused-input`, `harness-fault`. A harness fault is decided only from the harness's own instrumentation (readiness probe, send acknowledgement, canary fixtures at both ends of each batch).

## Tiers

| Tier | Effect on a conforming consumer |
| --- | --- |
| T0 | no usable response |
| T1 | next action changes (decision class) |
| T2 | same action, different data or state kept |
| T3 | visible in the protocol, no change in action or kept data |
| T4 | disappears once parsed; recorded, never counted |

## Pairwise outcome

Checked in this order:

1. **incomparable**: a harness fault on either side, or a capability the fixture requires is missing from the capability matrix
2. **flaky**: either side's liveness status was not reproducible over the clean restarts
3. **liveness gating**: at the first step where statuses differ, a T0 divergence; later fields are undefined
4. **co-failure**: both sides reach the same non-responded status at the same step; never counted as agreement
5. **divergence**: grade is the lowest tier of any differing counted axis, over all steps and the end state
6. **agreement**: no counted axis differs; agreements that differ only at T4 are flagged

A cell may declare a `refusal_class`: a `refused-input` step is then compared as a decision of that class (a parser rejection is a decision, not a failure).

## Running a cell

```bash
polydrift cell-run --impl pjsip-lab --repeats 3 --out pjsip.jsonl
polydrift cell-run --impl kamailio-lab --repeats 3 --restart-cmd "tools/kamailio/start_observe.sh" --out kamailio.jsonl
polydrift cell-analyse pjsip.jsonl sofia.jsonl kamailio.jsonl --deposits deposits.json --out result.json
```

`cell-analyse` refuses to run unless the cell's current oracle specification hash is listed in `deposits.json`. `--provisional` overrides this for development; the output is then marked `"provisional": true`.

## Transfer cost

`tools/adapter_loc.py` counts the per-cell lines of code (adapter, classes, tier table, capability matrix).
