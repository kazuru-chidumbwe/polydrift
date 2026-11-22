"""Every RQ5 case names fixtures that exist in its cell's rq5 set, and every MQTT rq5 step encodes."""
from pathlib import Path

import yaml

from polydrift.cells.mqtt import codec
from polydrift.recall import load_cases

ROOT = Path(__file__).resolve().parent.parent
CELLS = ROOT / "polydrift" / "cells"


def rq5_fixtures(cell: str) -> dict:
    return yaml.safe_load((CELLS / cell / "fixtures_rq5.yaml").read_text(encoding="utf-8"))["fixtures"]


def test_case_fixtures_exist():
    for cid, c in load_cases().items():
        have = rq5_fixtures(c.cell)
        for f in c.fixtures:
            assert f in have, (cid, f)


def test_mqtt_rq5_steps_encode():
    for name, fx in rq5_fixtures("mqtt").items():
        version = 5
        for st in fx["steps"]:
            op = st.get("op", "hex")
            args = dict(st.get("args") or {})
            if op == "connect":
                version = int(args.get("version", 5))
                for k in ("client_id",):
                    args[k] = str(args.get(k, "")).replace("{C}", "pd")
                assert codec.connect(args)[0] == 0x10, name
            elif op == "hex":
                data = bytes.fromhex(st["hex"].replace(" ", ""))
                # remaining length is a Variable Byte Integer (MQTT 5.0 1.5.5), one to four bytes
                n, mult, i = 0, 1, 1
                while True:
                    assert i < len(data) and i <= 4, (name, st["hex"])
                    n += (data[i] & 0x7F) * mult
                    mult *= 128
                    if not data[i] & 0x80:
                        break
                    i += 1
                assert n == len(data) - 1 - i, (name, st["hex"])
            elif op in codec.BUILDERS:
                args.setdefault("version", version)
                if args.get("packet_id") == "last":
                    args["packet_id"] = 1
                codec.BUILDERS[op](args)
            else:
                assert op in ("close", "sleep", "open"), (name, op)
