#!/bin/sh
# Start linphonec with auto-answer on 5060 and feed its stdin from a FIFO; port 4444 writes one command per line
# into the FIFO (UAC-role fixtures send e.g. "call sip:bob@172.31.60.99:5071").
mkfifo /tmp/lin.in
socat -u TCP-LISTEN:4444,fork,reuseaddr OPEN:/tmp/lin.in,wronly,append &
exec linphonec -a -c /etc/linphone/linphonerc < /tmp/lin.in
