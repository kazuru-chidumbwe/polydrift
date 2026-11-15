"""MQTT consumer for replay (manuscript 3.6, lab L9), Eclipse Paho (paho-mqtt). Same script format and output
events as mosqconsumer.c: connack, suback, unsuback, puback, message, disconnect, and errors the API raises.
Usage: python3 pahoconsumer.py HOST PORT < script"""

from __future__ import annotations

import json
import sys
import time

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.subscribeoptions import SubscribeOptions


def emit(**kw) -> None:  # noqa: ANN003
    print(json.dumps(kw, sort_keys=True), flush=True)


def prop_names(p) -> list[str]:  # noqa: ANN001
    if p is None:
        return []
    return sorted(k for k in vars(p) if k[0].isupper())


def rc_value(rc) -> int:  # noqa: ANN001
    return getattr(rc, "value", rc) if not isinstance(rc, int) else rc


def main() -> int:
    host, port = sys.argv[1], int(sys.argv[2])
    c: mqtt.Client | None = None

    def pump(ms: int) -> None:
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            rc = c.loop(timeout=0.05)
            if rc not in (mqtt.MQTT_ERR_SUCCESS,) and not getattr(pump, "said", False):
                emit(ev="loop", rc=int(rc))
                pump.said = True

    for line in sys.stdin:
        w = line.split()
        if not w:
            continue
        try:
            if w[0] == "connect":
                ver, clean, ka, cid = int(w[1]), int(w[2]), int(w[3]), w[4]
                proto = mqtt.MQTTv5 if ver == 5 else mqtt.MQTTv311
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, protocol=proto,
                                clean_session=None if ver == 5 else bool(clean))
                c.on_connect = lambda cl, u, f, rc, p: emit(ev="connack", rc=rc_value(rc), sp=int(bool(f.session_present)), props=prop_names(p))
                c.on_subscribe = lambda cl, u, mid, rcs, p: emit(ev="suback", rcs=[rc_value(r) for r in rcs], props=prop_names(p))
                c.on_unsubscribe = lambda cl, u, mid, rcs, p: emit(ev="unsuback", props=prop_names(p))
                c.on_publish = lambda cl, u, mid, rc, p: emit(ev="puback", rc=rc_value(rc))
                c.on_message = lambda cl, u, m: emit(ev="message", topic=m.topic, payload=m.payload.decode("latin-1"),
                                                      qos=m.qos, retain=int(m.retain),
                                                      props=prop_names(getattr(m, "properties", None)))
                c.on_disconnect = lambda cl, u, f, rc, p: emit(ev="disconnect", rc=rc_value(rc))
                cp = None
                if ver == 5:
                    cp = Properties(PacketTypes.CONNECT)
                    for kv in w[5:]:
                        k, v = kv.split("=")
                        setattr(cp, {"sei": "SessionExpiryInterval", "rmax": "ReceiveMaximum",
                                     "mps": "MaximumPacketSize", "tam": "TopicAliasMaximum"}[k], int(v))
                    c.connect(host, port, keepalive=ka, clean_start=bool(clean), properties=cp)
                else:
                    c.connect(host, port, keepalive=ka)
                pump(800)
            elif c is not None and w[0] == "subscribe":
                qos, nl, rap, rh, topic = int(w[1]), int(w[2]), int(w[3]), int(w[4]), w[5]
                if c._protocol == mqtt.MQTTv5:
                    c.subscribe(topic, options=SubscribeOptions(qos=qos, noLocal=bool(nl), retainAsPublished=bool(rap),
                                                                retainHandling=rh))
                else:
                    c.subscribe(topic, qos=qos)
                pump(800)
            elif c is not None and w[0] == "unsubscribe":
                c.unsubscribe(w[1])
                pump(800)
            elif c is not None and w[0] == "publish":
                qos, retain, topic = int(w[1]), int(w[2]), w[3]
                payload = bytes.fromhex(w[4]) if len(w) > 4 and w[4] != "-" else b""
                c.publish(topic, payload, qos=qos, retain=bool(retain))
                pump(800)
            elif c is not None and w[0] == "disconnect":
                c.disconnect()
                pump(300)
        except Exception as e:  # noqa: BLE001
            emit(ev="api", call=w[0], error=type(e).__name__)
    if c is not None:
        pump(1500)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
