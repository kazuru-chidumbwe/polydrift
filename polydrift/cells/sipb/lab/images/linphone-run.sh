#!/bin/sh
# Start linphonec on 5060 and feed its stdin from a FIFO; port 4444 writes one command per line into the FIFO
# (UAC-role fixtures send e.g. "call sip:bob@172.31.60.99:5071"). A background writer keeps the FIFO open, so
# linphonec does not block on open() before the first command. AUTO=0 starts it without auto-answer (ringing UAS).
mkfifo /tmp/lin.in
sleep infinity > /tmp/lin.in &
socat -u TCP-LISTEN:4444,fork,reuseaddr OPEN:/tmp/lin.in,wronly,append &
if [ "${AUTO:-1}" = "0" ]; then
  exec linphonec -c /etc/linphone/linphonerc < /tmp/lin.in
fi
exec linphonec -a -c /etc/linphone/linphonerc < /tmp/lin.in
