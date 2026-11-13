"""DNS answer TTLs are compared as the lab TTL they are ageing from, so a repeat that runs a second later agrees."""

from polydrift.cells.dns import adapter


def test_aged_ttls_fall_in_their_anchor():
    assert adapter._ttl_classes([299, 300]) == (300,)
    assert adapter._ttl_classes([264]) == (300,)
    assert adapter._ttl_classes([59]) == (60,)


def test_zero_split_and_msb_stay_distinct():
    assert adapter._ttl_classes([0]) == (0,)
    assert adapter._ttl_classes([58, 300]) == (60, 300)
    assert adapter._ttl_classes([2**31]) == (2**31,)
    assert adapter._ttl_classes(None) is None


def test_answer_ttls_axis_uses_classes():
    spec = adapter.load_spec()
    assert spec.axis("answer_ttls").field([287]).canon == (300,)
