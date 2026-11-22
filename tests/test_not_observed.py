"""An axis an implementation cannot show is kept as not observed and neither compared nor labelled (tracing fix 32:
Sieve engines without a dry run still give their compile record)."""
from __future__ import annotations

from polydrift.cells.sieve import adapter
from polydrift.core.label import inside
from polydrift.core.oracle import Outcome, compare_pair
from polydrift.core.record import NOT_OBSERVED, Field, Tier

from core_helpers import f, rec, step


def test_not_observed_axis_is_not_compared_or_labelled():
    a = rec("a", step(intent=f(2, ("keep",)), decision_class=f(1, "proceed")))
    b = rec("b", step(intent=Field(tier=Tier(2), raw=NOT_OBSERVED, canon=None), decision_class=f(1, "proceed")))
    assert compare_pair(a, b).outcome == Outcome.AGREEMENT
    assert inside(b, {"req_id": "S-1", "keyword": "MUST", "axis": "intent", "step": 1, "allowed": [("keep",)]})


def test_effect_only_engine_gives_compile_record():
    spec = adapter.load_spec()
    res = {"effect_only": True, "compile": "ok", "intent": None, "effect": {"stored": {"INBOX": 1}}}
    recs = adapter.to_records(res, "stalwart-1.1.1", "require_unknown", spec=spec)
    assert [r.fixture for r in recs] == ["require_unknown", "require_unknown@effect"]
    st = recs[0].steps[0]
    assert st.fields["decision_class"].canon == "proceed" and st.fields["outcome"].canon == "ok"
    assert st.fields["intent"].raw == NOT_OBSERVED
