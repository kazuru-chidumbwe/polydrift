"""SIP native status code -> decision class (manuscript 3.3).

Classes are defined by the consumer's next action. Codes not listed fall back
to the x00 code of their class, as RFC 3261 Section 8.1.3.2 tells a UAC to
treat an unrecognised response. Registry: IANA "Session Initiation Protocol
(SIP) Parameters", Response Codes.
"""

from __future__ import annotations

EXPLICIT: dict[int, str] = {
    # 4xx: client side
    400: "fix-request",
    401: "authenticate",
    402: "stop",
    403: "stop",
    404: "stop",
    405: "fix-request",
    406: "fix-request",
    407: "authenticate",
    408: "retry-later",
    410: "stop",
    412: "fix-request",
    413: "fix-request",
    414: "fix-request",
    415: "fix-request",
    416: "fix-request",
    417: "fix-request",
    420: "fix-request",
    421: "fix-request",
    422: "fix-request",
    423: "fix-request",
    428: "fix-request",
    429: "fix-request",
    430: "retry-later",
    433: "fix-request",
    436: "fix-request",
    437: "fix-request",
    438: "fix-request",
    439: "fix-request",
    440: "fix-request",
    469: "fix-request",
    470: "stop",
    480: "retry-later",
    481: "stop",
    482: "stop",
    483: "stop",
    484: "fix-request",
    485: "fix-request",
    486: "retry-later",
    487: "stop",
    488: "fix-request",
    489: "fix-request",
    491: "retry-later",
    493: "stop",
    494: "authenticate",
    # 5xx: server side
    500: "retry-later",
    501: "fix-request",
    502: "retry-later",
    503: "retry-later",
    504: "retry-later",
    505: "fix-request",
    513: "fix-request",
    555: "fix-request",
    580: "fix-request",
    # 6xx: global
    600: "retry-later",
    603: "stop",
    604: "stop",
    606: "fix-request",
    607: "stop",
    608: "stop",
}

CLASS_DEFAULT: dict[int, str] = {
    1: "no-decision",
    2: "proceed",
    3: "redirect",
    4: EXPLICIT[400],
    5: EXPLICIT[500],
    6: EXPLICIT[600],
}

# A request the parser accepted is handed to the transaction layer, which
# processes it: the decision is to proceed.
REQUEST_ACCEPTED = "proceed"

# A parser rejection is a decision that the message must be fixed (3.8).
REFUSAL_CLASS = "fix-request"

# A response line whose code is outside 100-699 is not a final answer the
# consumer can act on.
OUT_OF_RANGE = "no-decision"


def decision_class(status_code: int | None, is_request: bool) -> str | None:
    if is_request:
        return REQUEST_ACCEPTED
    if status_code is None:
        return None
    if status_code in EXPLICIT:
        return EXPLICIT[status_code]
    family = status_code // 100
    return CLASS_DEFAULT.get(family, OUT_OF_RANGE)
