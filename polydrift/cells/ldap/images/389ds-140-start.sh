#!/bin/sh
# First start: create the instance the 389ds/dirsrv images create from the same variables, then run in the foreground.
set -e
if [ ! -d /etc/dirsrv/slapd-localhost ]; then
  cat > /tmp/ds.inf <<EOF
[general]
config_version = 2
full_machine_name = $(hostname)
strict_host_checking = False
start = False
systemd = False

[slapd]
instance_name = localhost
port = 3389
root_password = ${DS_DM_PASSWORD:-Lab-Pass-2026}
self_sign_cert = False

[backend-userroot]
suffix = ${DS_SUFFIX_NAME:-dc=lab,dc=test}
create_suffix_entry = True
EOF
  dscreate from-file /tmp/ds.inf
fi
# dscreate may leave the instance running (through the systemctl stand-in): stop it, then run in the foreground
/usr/local/sbin/systemctl stop dirsrv@localhost
mkdir -p /run/dirsrv /run/lock/dirsrv/slapd-localhost && chown -R dirsrv:dirsrv /run/dirsrv /run/lock/dirsrv
exec /usr/sbin/ns-slapd -D /etc/dirsrv/slapd-localhost -i /run/dirsrv/slapd-localhost.pid -d 0
