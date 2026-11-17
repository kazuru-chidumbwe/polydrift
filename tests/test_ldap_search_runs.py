"""LDAP response_ops canonical form: entries and references of one result set in any order (RFC 4511 4.5.2)."""

from polydrift.cells.ldap.adapter import KAPPA

k = KAPPA["search_runs_sorted"]
E, R, D, B = "SearchResultEntry", "SearchResultReference", "SearchResultDone", "BindResponse"


def test_interleaving_within_a_result_set_is_canonical():
    a = [R, E, R, E, E, D, B]
    b = [E, E, R, R, E, D, B]
    assert k(a) == k(b) == (E, E, E, R, R, D, B)


def test_other_messages_keep_their_position():
    assert k([B, E, R, D, E, D]) == (B, E, R, D, E, D)
    assert k([D, B]) == (D, B)


def test_count_differences_still_show():
    assert k([E, E, D]) != k([E, D])
    assert k(None) is None
