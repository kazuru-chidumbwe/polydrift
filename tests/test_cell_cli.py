"""cell-run / cell-analyse end to end with the stub drivers (no lab)."""

from polydrift.fixtures import FIXTURE_INDEX
import json

from polydrift.cli import main


def test_cell_run_and_analyse_with_stubs(tmp_path):
    outs = []
    for impl in ("builtin", "pjsip-stub", "sofia-stub"):
        out = tmp_path / f"{impl}.jsonl"
        assert main(["cell-run", "--impl", impl, "--repeats", "3", "--out", str(out)]) == 0
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(FIXTURE_INDEX) * 3
        outs.append(str(out))

    # Refused without a deposit.
    result = tmp_path / "result.json"
    deposits = tmp_path / "deposits.json"
    assert main(["cell-analyse", *outs, "--deposits", str(deposits), "--out", str(result)]) == 2
    assert not result.exists()

    # Provisional run works and is marked as such.
    assert main(["cell-analyse", *outs, "--deposits", str(deposits), "--provisional", "--out", str(result)]) == 0
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["provisional"] is True and data["deposit"] is None
    assert data["impls"] == ["builtin", "pjsip-stub", "sofia-stub"]
    assert sum(data["summary"].get(k, 0) for k in data["summary"] if k != "t4-only-agreement") == len(FIXTURE_INDEX) * 3
    assert data["repeat"]["flaky_triples"] == 0

    # With a matching deposit the run is not provisional.
    deposits.write_text(json.dumps({"sip": [{"spec_hash": data["spec_hash"], "doi": "test"}]}))
    assert main(["cell-analyse", *outs, "--deposits", str(deposits), "--out", str(result)]) == 0
    assert json.loads(result.read_text(encoding="utf-8"))["deposit"]["doi"] == "test"


def test_cell_analyse_rejects_too_few_repeats(tmp_path):
    out = tmp_path / "b.jsonl"
    assert main(["cell-run", "--impl", "builtin", "--repeats", "2", "--fixture", "F-200-MIN", "--out", str(out)]) == 0
    res = tmp_path / "r.json"
    assert main(["cell-analyse", str(out), "--provisional", "--out", str(res)]) == 2
