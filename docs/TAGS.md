# Tags and cite pins

| Tag | Role |
| --- | --- |
| `0.3.3` | **Current cite pin** for manuscripts and Code metadata C1/C2 |

Cite the tag tree URL:

https://github.com/kazuru-chidumbwe/sipdrift/tree/0.3.3

Do not cite floating `main`. If laboratory packs were produced on commits after `0.3.3`, either cut a new tag or disclose the commit in the paper while keeping C1/C2 on a published tag.

## Release hygiene

1. Align `pyproject.toml` version, `CITATION.cff`, `codemeta.json`, and `CODE_METADATA.md`.
2. Run `pytest -q` on stubs.
3. Create annotated tag + GitHub Release for the cite pin.
4. Optional: mint Zenodo from the GitHub Release; put Zenodo in docs / C7 only, **not** C2.
