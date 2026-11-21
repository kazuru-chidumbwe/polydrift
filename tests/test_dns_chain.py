"""DNS runner role chains: each hop starts behind the element under test at its own address (tasklist 1.9)."""
import subprocess

import pytest

from polydrift.cells.dns import runner


def test_chain_starts_upstream_hops_first(monkeypatch):
    calls = []

    def fake(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runner, "sh", fake)
    rec = {"key": "u", "image": "pd-unbound:1.26.0", "cmd": []}
    mid = {"key": "m", "image": "coredns/coredns:1.15.0", "cmd": [], "upstream": rec}
    nl = {"key": "n", "image": "node-cache", "cmd": [], "upstream": mid}
    runner.start_resolver(nl, "/lab")
    runs = [(a[a.index("--name") + 1], a[a.index("--ip") + 1]) for a in calls if a[:3] == ["docker", "run", "-d"]]
    assert runs == [("pdns-up2", runner.UPSTREAM2_IP), ("pdns-up", runner.UPSTREAM_IP), ("pdns-res", runner.RESOLVER_IP)]


def test_three_hops_behind_the_element_are_refused(monkeypatch):
    monkeypatch.setattr(runner, "sh", lambda a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    t = {"key": "a", "image": "x", "upstream": {"key": "b", "image": "x", "upstream": {
        "key": "c", "image": "x", "upstream": {"key": "d", "image": "x"}}}}
    with pytest.raises(RuntimeError):
        runner.start_resolver(t, "/lab")
