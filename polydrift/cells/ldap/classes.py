"""LDAP resultCode -> decision class (manuscript 3.3). RFC 4511 Section 4.1.9 and Appendix A; the class is the
client's next action. Codes not listed are treated as "stop" (a non-success the client cannot correct by itself)."""

from __future__ import annotations

CLASSES: dict[int, str] = {
    0: "proceed", 5: "proceed", 6: "proceed",          # success, compareFalse, compareTrue
    14: "authenticate",                                 # saslBindInProgress
    1: "stop", 3: "stop", 4: "stop", 11: "stop",        # operationsError, timeLimit, sizeLimit, adminLimit
    2: "fix-request",                                   # protocolError
    7: "authenticate", 8: "authenticate", 13: "authenticate",  # authMethodNotSupported, strongerAuthRequired, confidentialityRequired
    10: "redirect",                                     # referral
    12: "fix-request",                                  # unavailableCriticalExtension
    16: "fix-request", 17: "fix-request", 18: "fix-request", 19: "fix-request", 20: "fix-request", 21: "fix-request",
    32: "stop", 33: "stop", 34: "fix-request", 36: "stop",   # noSuchObject, aliasProblem, invalidDNSyntax, aliasDereferencingProblem
    48: "authenticate", 49: "authenticate", 50: "stop",      # inappropriateAuthentication, invalidCredentials, insufficientAccessRights
    51: "retry-later", 52: "retry-later", 53: "stop", 54: "stop",  # busy, unavailable, unwillingToPerform, loopDetect
    64: "fix-request", 65: "fix-request", 66: "stop", 67: "stop", 68: "stop", 69: "stop", 71: "stop",
    80: "stop",
}


def decision(code: int | None) -> str | None:
    if code is None:
        return None
    return CLASSES.get(code, "stop")
