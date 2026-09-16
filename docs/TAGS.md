# Tags and cite pins

| Tag | Role |
| --- | --- |
| `0.3.4` | **Current cite pin** for manuscripts and Code metadata C1/C2 |
| `0.3.3` | Prior corpus pin (oracle/fixtures unchanged into 0.3.4) |

Cite the tag tree URL:

https://github.com/kazuru-chidumbwe/sipdrift/tree/0.3.4

Do not cite floating `main`. Laboratory suite digests under `results/V-BASE-restart/` match this pin.

## Release hygiene

1. Align `pyproject.toml` version, `CITATION.cff`, `codemeta.json`, and `CODE_METADATA.md`.
2. Run `pytest -q` on stubs.
3. Create annotated tag + GitHub Release for the cite pin.
4. Optional: mint Zenodo from the GitHub Release; put Zenodo in docs / C7 only, **not** C2.
