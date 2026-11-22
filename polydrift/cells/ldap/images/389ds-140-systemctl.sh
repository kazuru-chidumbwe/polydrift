#!/bin/sh
# systemctl stand-in for lib389 1.4.0 in a container without systemd (dscreate ignores "systemd = False" there and
# runs "systemctl start dirsrv@<instance>"): start, stop, restart and is-active run ns-slapd directly.
act=""; unit=""
for a in "$@"; do case "$a" in -*) ;; *) if [ -z "$act" ]; then act=$a; else unit=$a; fi ;; esac; done
inst=${unit#dirsrv@}; inst=${inst%.service}
pid=/run/dirsrv/slapd-$inst.pid
running() { [ -f "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null; }
case "$act" in
  start)
    running && exit 0
    mkdir -p /run/dirsrv /run/lock/dirsrv/slapd-"$inst" && chown -R dirsrv:dirsrv /run/dirsrv /run/lock/dirsrv 2>/dev/null
    /usr/sbin/ns-slapd -D /etc/dirsrv/slapd-"$inst" -i "$pid" || exit 1
    for i in $(seq 1 30); do running && exit 0; sleep 1; done; exit 1 ;;
  stop)
    running || exit 0
    kill "$(cat "$pid")"; for i in $(seq 1 30); do running || exit 0; sleep 1; done; exit 1 ;;
  restart) "$0" stop "$unit"; exec "$0" start "$unit" ;;
  is-active) if running; then echo active; exit 0; else echo inactive; exit 3; fi ;;
  *) exit 0 ;;
esac
