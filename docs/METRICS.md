# Metrics and classification contract

sipdrift projects driver observations onto shared **oracle axes** and classifies each fixture.

## Axes (headline)

| Axis | Meaning |
| --- | --- |
| `start_line` | Request or status start-line string |
| `status` | Response status code when applicable |
| `via` | Via value used for comparison |
| `cseq` | CSeq value |
| `content_type` | Content-Type |
| `content_length` | Content-Length |
| `body_sha256` | SHA-256 of raw body bytes |
| `sdp_sha256` | SHA-256 of normalized SDP when Content-Type is `application/sdp` |

Exact field names in JSON follow the harness emitter. Treat this table as the semantic contract.

## Classifications

| Label | Rule |
| --- | --- |
| `error` | At least one driver returns `ok` false, or a required start-line is missing |
| `skip` | Both succeed but a compared axis is out of scope for that fixture |
| `diverge` | Both succeed, start-lines present, at least one compared axis differs |
| `agree` | Both succeed, start-lines present, all compared axes equal |

## What is not claimed

- Stub / `builtin` agreement is not an open-source interoperability result.
- `kamailio-lab` is a **receive** observe path, not production routing equivalence.
- Body/SDP digests are wire fingerprints until a re-serialize path exists.
- No CVE or stack-desynchronization attack claim.

## Illustrative laboratory counts (`0.3.3`)

See [`../results/README.md`](../results/README.md). Regenerate from suite JSON; do not hand-edit rates in papers without regenerating digests.
