"""sipb readiness: an element is ready when the canary answers and its ready_exec checks pass."""

from polydrift.cells.sipb import runner


def test_ejabberd_waits_for_every_lab_user():
    t = {"impl": "ejabberd", "creds": {"bob": "p", "alice": "p"}}
    assert runner.ready_checks(t, "lab.test") == [["ejabberdctl", "check_account", "alice", "lab.test"],
                                                  ["ejabberdctl", "check_account", "bob", "lab.test"]]


def test_explicit_ready_exec_wins_and_others_have_none():
    assert runner.ready_checks({"impl": "ejabberd", "ready_exec": [["true"]], "creds": {"bob": "p"}}, "d") == [["true"]]
    assert runner.ready_checks({"impl": "ejabberd", "ready_exec": [], "creds": {"bob": "p"}}, "d") == []
    assert runner.ready_checks({"impl": "kamailio", "creds": {"bob": "p"}}, "d") == []


def test_eut_ready_needs_all_checks(monkeypatch):
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def fake_sh(args, timeout=300, stdin=None):
        calls.append(args)
        return R(0 if args[-2] == "alice" else 1)

    monkeypatch.setattr(runner, "sh", fake_sh)
    assert runner.eut_ready([]) is True
    assert runner.eut_ready([["ejabberdctl", "check_account", "alice", "d"]]) is True
    assert runner.eut_ready([["ejabberdctl", "check_account", "alice", "d"],
                             ["ejabberdctl", "check_account", "bob", "d"]]) is False
    assert calls[0][:3] == ["docker", "exec", "psipb-eut"]
