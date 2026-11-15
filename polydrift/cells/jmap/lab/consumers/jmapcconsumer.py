"""JMAP consumer for replay (manuscript 3.6, lab L9), jmapc (Python). Reads {"host", "user", "password", "steps":
[[[method, args, callId], ...], ...]} on stdin; each step is one API request whose calls are sent as given
(jmapc CustomMethod). Prints one JSON line per step: for each method response, the class jmapc parsed it into and
that object's fields as jmapc exposes them, or the exception a step raised.
Usage: python3 jmapcconsumer.py < script.json"""

from __future__ import annotations

import json
import sys

import jmapc
from jmapc.methods import CustomMethod


def plain(o):  # noqa: ANN001, ANN201
    if hasattr(o, "to_dict"):
        try:
            return o.to_dict()
        except Exception as e:  # noqa: BLE001
            return {"to_dict_error": type(e).__name__}
    if hasattr(o, "__dict__"):
        return {k: plain(v) for k, v in vars(o).items() if not k.startswith("_")}
    return o


def main() -> int:
    d = json.load(sys.stdin)
    try:
        client = jmapc.Client.create_with_password(host=d["host"], user=d["user"], password=d["password"])
        acct = client.account_id
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"session_error": type(e).__name__, "msg": str(e)[:200]}))
        return 0
    for calls in d["steps"]:
        methods = []
        for name, args, _cid in calls:
            m = CustomMethod(data=json.loads(json.dumps(args).replace("{acct}", acct)))
            m.jmap_method = name
            methods.append(m)
        try:
            res = client.request(methods, raise_errors=False)
            out = [{"id": r.id, "class": type(r.response).__name__, "data": plain(r.response)} for r in res]
            print(json.dumps({"responses": out}, sort_keys=True, default=str))
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"error": type(e).__name__, "msg": str(e)[:200]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
