"""RQ5 recall evaluation (polydrift/recall.py)."""

import pytest

from core_helpers import f, rec, step
from polydrift.recall import Case, evaluate, load_cases, summary


def runs(impl, value, other=0, fixture="F1", status="responded"):
    return [rec(impl, step(status, x=f(2, value), y=f(2, other)), fixture=fixture, repeat=i) for i in (1, 2, 3)]


def case(**kw):
    d = {"cell": "test", "family": "srv", "fixtures": ["F1"], "axes": ["x"], "affected": ["1.0"], "fixed": ["1.1"]}
    d.update(kw)
    return Case.from_dict("C1", d)


def peers():
    return runs("p-2.0", 0) + runs("q-3.1", 0)


def test_recalled_and_fix_confirmed():
    recs = runs("srv-1.0", 64) + runs("srv-1.1", 0) + peers()
    r = evaluate(recs, case(known=[{"fixture": "F1", "axis": "x", "step": 1, "value": 64}]))
    assert r["outcome"] == "recalled, fix confirmed"
    assert r["reproduced"] is True and r["detected"] and r["fix_confirmed"]
    assert r["peers"] == ["p-2.0", "q-3.1"]
    assert r["still_divergent"] == {}


def test_other_axis_does_not_count():
    recs = runs("srv-1.0", 0, other=9) + runs("srv-1.1", 0) + peers()
    r = evaluate(recs, case())
    assert r["outcome"] == "missed" and not r["detected"]


def test_fix_changes_value_but_still_divergent():
    recs = runs("srv-1.0", 64) + runs("srv-1.1", 7) + peers()
    r = evaluate(recs, case())
    assert r["fix_confirmed"] and r["still_divergent"] == {"1.1": ["p-2.0", "q-3.1"]}


def test_fix_not_confirmed_when_same_signature():
    recs = runs("srv-1.0", 64) + runs("srv-1.1", 64) + peers()
    r = evaluate(recs, case())
    assert r["outcome"] == "recalled, fix not confirmed" and not r["fix_confirmed"]


def test_not_reproduced_is_apart_from_miss():
    recs = runs("srv-1.0", 0) + runs("srv-1.1", 0) + peers()
    r = evaluate(recs, case(known=[{"fixture": "F1", "axis": "x", "step": 1, "value": 64}]))
    assert r["outcome"] == "not reproduced"
    s = summary([r])
    assert s["not_reproduced"] == 1 and s["recall"] is None


def test_crash_counts_on_status_axis():
    recs = runs("srv-1.0", 0, status="crashed") + runs("srv-1.1", 0) + peers()
    r = evaluate(recs, case(axes=["__status__"]))
    assert r["outcome"] == "recalled, fix confirmed"


def test_unfixed_case_and_missing_versions():
    recs = runs("srv-1.0", 64) + peers()
    assert evaluate(recs, case(status="unfixed", fixed=[]))["outcome"] == "recalled"
    assert evaluate(recs, case())["outcome"] == "recalled, fixed version not run"
    assert evaluate(peers(), case())["outcome"] == "not-run"


def test_v_prefix_and_own_family_not_a_peer():
    recs = runs("srv-v1.0", 64) + runs("srv-v1.1", 0) + runs("srv-v9.0", 0) + peers()
    r = evaluate(recs, case(affected=["v1.0"], fixed=["v1.1"]))
    assert "srv-v9.0" not in r["peers"] and r["fix_confirmed"]


def test_summary_rates():
    recs = runs("srv-1.0", 64) + runs("srv-1.1", 0) + peers()
    a = evaluate(recs, case())
    b = evaluate(runs("srv-1.0", 0) + runs("srv-1.1", 0) + peers(), case())
    s = summary([a, b])
    assert s["recall"] == 0.5 and s["fix_confirmation"] == 1.0


def test_case_validation():
    with pytest.raises(ValueError):
        case(fixed=[])
    with pytest.raises(ValueError):
        case(known=[{"fixture": "F9", "axis": "x", "value": 1}])


def test_frozen_case_list_loads_and_names_rq5_fixtures():
    import yaml
    from pathlib import Path

    cases = load_cases()
    assert cases
    root = Path(__file__).resolve().parents[1] / "polydrift" / "cells"
    for c in cases.values():
        fx = yaml.safe_load((root / c.cell / "fixtures_rq5.yaml").read_text(encoding="utf-8"))["fixtures"]
        for fid in c.fixtures:
            assert fid in fx, (c.id, fid)
            assert fid.startswith("rq5_"), fid


def test_dns_crash_marking_maps_to_crashed():
    from polydrift.cells.dns import adapter
    from polydrift.cells.dns.runner import mark_crashed
    from polydrift.core.record import Status

    obs = [{"step": 1, "op": "query", "rcode": "NOERROR", "answers": ["a"], "error": None},
           {"step": 2, "op": "query", "error": "timeout"},
           {"step": 3, "op": "upstream", "rcode": "NOERROR", "queries": [], "error": None}]
    marked = mark_crashed(obs)
    assert [o.get("crashed", False) for o in marked] == [False, True, False]
    rec = adapter.to_record_script(marked, "bind-9.16.13", "rq5_d10_dname_self_answer", spec=adapter.load_spec())
    assert [s.status for s in rec.steps][:2] == [Status.RESPONDED, Status.CRASHED]


def test_recall_cli(tmp_path):
    from polydrift.cli import main
    from polydrift.core.record import ObservationRecord, write_jsonl
    import json

    recs = []
    for impl, code in (("lldap-0.5.0", [64]), ("lldap-0.6.0", [48]), ("openldap-2.7.2", [0]), ("389ds-3.1", [0])):
        for i in (1, 2, 3):
            recs.append(ObservationRecord(cell="ldap", impl=impl, fixture="rq5_l2_bind_empty_dn", repeat=i,
                                          steps=(step(native_codes=f(1, tuple(code))),)))
    p = tmp_path / "r.jsonl"
    write_jsonl(str(p), recs)
    out = tmp_path / "o.json"
    assert main(["recall", str(p), "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    (l2,) = res["cases"]
    assert l2["case"] == "L2" and l2["outcome"] == "recalled, fix confirmed" and l2["reproduced"] is True
