#!/bin/sh
# Sendmail stand-in for the Sieve lab: every message an engine sends (redirect, reject, vacation, notify) is kept
# under $PD_OUT (default /tmp/pd-out) as N.args (the command line) and N.eml (the message), and never leaves.
O=${PD_OUT:-/tmp/pd-out}
mkdir -p "$O"
n=$(ls "$O" 2>/dev/null | grep -c '\.eml$')
printf '%s\n' "$@" > "$O/$n.args"
# the MTA hop: an engine that hands its message to sendmail leaves the trace line to the MTA (RFC 5321 4.4)
{ printf 'Received: by pd-lab-mta (sendmail interface); Mon, 14 Sep 2026 19:00:00 +0000\r\n'; cat; } > "$O/$n.eml"
exit 0
