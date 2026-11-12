"""Fixture loading helpers (scaffold)."""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

# Stable fixture IDs for deposited suite manifests.
FIXTURE_INDEX: dict[str, str] = {
    "F-INVITE-MIN": "invite_min.sip",
    "F-200-MIN": "response_200_min.sip",
    "F-486-MIN": "response_486_busy.sip",
    "F-503-MIN": "response_503_unavail.sip",
    "F-OPTIONS-MIN": "options_min.sip",
    "F-REGISTER-MIN": "register_min.sip",
    "F-MALFORMED-START": "malformed_start.sip",
    "F-100-TRYING": "response_100_trying.sip",
    "F-180-RINGING": "response_180_ringing.sip",
    "F-401-AUTH": "response_401_auth.sip",
    "F-404-NOTFOUND": "response_404_notfound.sip",
    "F-603-DECLINE": "response_603_decline.sip",
    "F-ACK-MIN": "ack_min.sip",
    "F-BYE-MIN": "bye_min.sip",
    "F-CANCEL-MIN": "cancel_min.sip",
    "F-INFO-MIN": "info_min.sip",
    "F-MESSAGE-MIN": "message_min.sip",
    "F-NOTIFY-MIN": "notify_min.sip",
    "F-SUBSCRIBE-MIN": "subscribe_min.sip",
    "F-COMPACT-VIA": "compact_via.sip",
    "F-MULTI-VIA": "multi_via.sip",
    "F-FOLDED-VIA": "folded_via.sip",
    "F-LOWER-SIP": "lower_sip_version.sip",
    "F-SPACES-START": "spaces_start.sip",
    "F-TAB-SEP": "tab_sep_headers.sip",
    "F-UNKNOWN-METHOD": "unknown_method.sip",
    "F-NO-HEADERS": "no_headers.sip",
    "F-ONLY-START": "only_start_line.sip",
    "F-JUNK-AFTER": "junk_after_message.sip",
    "F-MISSING-VIA": "missing_via.sip",
    "F-MISSING-CSEQ": "missing_cseq.sip",
    "F-BAD-STATUS": "bad_status_code.sip",
    "F-TORTURE-WS-END": "torture_ws_end.sip",
    "F-TORTURE-LWS-COLON": "torture_lws_colon.sip",
    "F-TORTURE-ESCAPED-URI": "torture_escaped_uri.sip",
    "F-TORTURE-LONG-URI": "torture_long_uri.sip",
    "F-TORTURE-BAD-CLEN": "torture_bad_clen.sip",
    "F-TORTURE-DUP-VIA": "torture_dup_cseq_via.sip",
    "F-TORTURE-NONASCII": "torture_nonascii_reason.sip",
    "F-TORTURE-NULL-IN-BODY": "torture_null_claim.sip",
    "F-TORTURE-UNRECOG-HDR": "torture_unrecog_hdr.sip",
    "F-TORTURE-DISPLAY-NAME": "torture_display_name.sip",
    "F-TORTURE-IPV6-VIA": "torture_ipv6_via.sip",
    "F-TORTURE-NO-MAGIC-COOKIE": "torture_no_magic.sip",
    "F-TORTURE-EMPTY-SUBJECT": "torture_empty_subject.sip",
    "F-TORTURE-MULTI-CLEN": "torture_multi_clen.sip",
    "F-TORTURE-UNKNOWN-SCHEME": "torture_unknown_scheme.sip",
    "F-TORTURE-REQ-URI-PARAM": "torture_ruri_param.sip",
    "F-INVITE-SDP": "invite_sdp.sip",
    "F-200-SDP": "response_200_sdp.sip",
    "F-SDP-TRAIL-WS": "sdp_trail_ws.sip",
    "F-COMPACT-CTYPE": "compact_ctype.sip",
    "F-MESSAGE-BODY": "message_body.sip",
    "F-CENSUS-NO-EMPTY-LINE": "r_no_empty_line.sip",
    "F-CENSUS-RURI-SPACE": "r_ruri_space.sip",
    "F-CENSUS-RURI-ANGLE": "r_ruri_angle.sip",
    "F-CENSUS-RURI-TEL": "r_ruri_tel.sip",
    "F-CENSUS-VIA-ROWS-AND-COMMAS": "r_via_rows_and_commas.sip",
    "F-CENSUS-DUP-HEADER-PARAM": "r_dup_header_param.sip",
    "F-CENSUS-AUTH-ROWS": "r_auth_rows.sip",
    "F-CENSUS-REQUEST-HEADER-IN-RESPONSE": "r_request_header_in_response.sip",
    "F-CENSUS-REQUEST-BODY-MULTIPART": "r_request_body_multipart.sip",
    "F-CENSUS-RESPONSE-BODY": "r_response_body.sip",
    "F-CENSUS-PROVISIONAL-BODY": "r_provisional_body.sip",
    "F-CENSUS-CONTENT-ENCODING": "r_content_encoding.sip",
    "F-CENSUS-CHUNKED": "r_chunked.sip",
    "F-CENSUS-LEADING-CRLF": "r_leading_crlf.sip",
    "F-CENSUS-SIP-SCHEME": "r_sip_scheme.sip",
    "F-CENSUS-CSEQ-METHOD-MISMATCH": "r_cseq_method_mismatch.sip",
    "F-CENSUS-CSEQ-TOO-BIG": "r_cseq_too_big.sip",
    "F-CENSUS-CSEQ-OVER-2-31": "r_cseq_over_2_31.sip",
    "F-CENSUS-VIA-BAD-PROTOCOL": "r_via_bad_protocol.sip",
    "F-CENSUS-VIA-NO-BRANCH": "r_via_no_branch.sip",
    "F-CENSUS-OPTION-TAGS-NONSTANDARD": "r_option_tags_nonstandard.sip",
    "F-CENSUS-MISSING-MANDATORY-FROM": "r_missing_mandatory_from.sip",
    "F-CENSUS-NOT-APPLICABLE-HEADER": "r_not_applicable_header.sip",
    "F-CENSUS-FROM-ADDRSPEC-COMMA": "r_from_addrspec_comma.sip",
    "F-CENSUS-BODY-WITHOUT-TYPE": "r_body_without_type.sip",
    "F-CENSUS-NO-BODY-CLEN-ZERO": "r_no_body_clen_zero.sip",
    "F-CENSUS-USER-EMPTY": "r_user_empty.sip",
    "F-CENSUS-NO-USERINFO": "r_no_userinfo.sip",
    "F-CENSUS-USER-PASSWORD": "r_user_password.sip",
    "F-CENSUS-UNKNOWN-URI-PARAM": "r_unknown_uri_param.sip",
    "F-CENSUS-DUP-URI-PARAM": "r_dup_uri_param.sip",
    "F-CENSUS-ESCAPED-HOST": "r_escaped_host.sip",
    "F-CENSUS-UNUSUAL-REASON": "r_unusual_reason.sip",
}


def list_fixtures() -> list[tuple[str, Path]]:
    """Return (fixture_id, path) pairs in index order."""
    rows: list[tuple[str, Path]] = []
    for fixture_id, name in FIXTURE_INDEX.items():
        path = FIXTURES_DIR / name
        rows.append((fixture_id, path))
    return rows


def load_fixture(fixture_id: str) -> str:
    """Load a fixture by stable ID.

    Raises:
        KeyError: unknown fixture id.
        FileNotFoundError: file missing on disk.
    """
    try:
        name = FIXTURE_INDEX[fixture_id]
    except KeyError as exc:
        raise KeyError(f"unknown fixture id: {fixture_id}") from exc
    path = FIXTURES_DIR / name
    return path.read_bytes().decode("utf-8", errors="replace")
