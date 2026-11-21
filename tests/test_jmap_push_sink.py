"""JMAP push sink (lab/push/sink.py): chunked bodies are read whole; /clear empties the log."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "polydrift" / "cells" / "jmap" / "lab" / "push"))
import sink  # noqa: E402


class _H:
    def __init__(self, raw: bytes, headers: dict):
        self.rfile = io.BytesIO(raw)
        self.headers = headers


def test_chunked_body_is_read_whole():
    raw = b'5\r\n{"@ty\r\n13\r\npe":"PushVerificati\r\n3\r\non"}\r\n0\r\n\r\n'
    h = _H(raw, {"transfer-encoding": "chunked"})
    assert sink.Push._body(h) == b'{"@type":"PushVerification"}'


def test_content_length_body():
    h = _H(b'{"a":1}', {"content-length": "7"})
    assert sink.Push._body(h) == b'{"a":1}'
