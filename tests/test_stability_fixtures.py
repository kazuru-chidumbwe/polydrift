"""Fixtures and canonical values made stable before the freeze: the stalled TCP client of census_tcp_session, the
10-second floor of a received MQTT Message Expiry Interval, and the SERVFAIL name used only by census_servfail_caching."""

import importlib.util
import socket
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def _driver():
    spec = importlib.util.spec_from_file_location("pd_dns_driver", ROOT / "polydrift/cells/dns/lab/driver.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_stall(monkeypatch, close_peer: bool) -> dict:
    drv = _driver()
    a, b = socket.socketpair()
    if close_peer:
        b.close()
    monkeypatch.setattr(drv.socket, "create_connection", lambda *args, **kw: a)
    try:
        return drv.parallel("127.0.0.1", [{"label": "t1", "stall": True, "hold_ms": 300}], 2)
    finally:
        if not close_peer:
            b.close()


def test_stall_held_when_server_keeps_connection(monkeypatch):
    obs = _run_stall(monkeypatch, close_peer=False)
    assert obs["rcodes"] == {"t1": "held"} and obs["order"] == ["t1"]


def test_stall_closed_when_server_closes(monkeypatch):
    obs = _run_stall(monkeypatch, close_peer=True)
    assert obs["rcodes"] == {"t1": "closed"}


def test_expiry_floor_keeps_undecremented_apart():
    from polydrift.cells.mqtt.adapter import _publish_meta

    def meta(e):
        return _publish_meta({"b": [("x", 0, (), e, False)]})

    assert meta(57) == meta(58) == meta(50)
    assert meta(60) != meta(59)
    assert meta(None) != meta(0)
    assert meta(1) == meta(2)


def test_servfail_caching_has_its_own_name():
    fx = yaml.safe_load((ROOT / "polydrift/cells/dns/fixtures.yaml").read_text(encoding="utf-8"))
    zones = yaml.safe_load((ROOT / "polydrift/cells/dns/lab/zones.yaml").read_text(encoding="utf-8"))
    fixtures = fx.get("fixtures", fx)
    names = {s.get("qname") for s in fixtures["census_servfail_caching"]["steps"] if s.get("qname")}
    assert names == {"servfailc.pd.pdlab"}
    others = {s.get("qname") for k, f in fixtures.items() if k != "census_servfail_caching" and isinstance(f, dict)
              for s in f.get("steps", []) if isinstance(s, dict)}
    assert "servfailc.pd.pdlab" not in others
    assert "servfailc: {kind: rcode" in (ROOT / "polydrift/cells/dns/lab/zones.yaml").read_text(encoding="utf-8")
    assert zones
