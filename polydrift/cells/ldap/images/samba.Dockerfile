# Samba AD DC built from the samba.org release tarball. The detached signature (.tar.asc, over the uncompressed tar)
# is checked against the Samba distribution key published on samba.org, and the tarball's sha256 is printed into the
# build log. Build dependencies come from the release's own bootstrap script for Debian 12. The domain is provisioned
# at first start (sysvol NT ACLs need SYS_ADMIN at run time); simple binds over plain LDAP are then allowed, as for
# the earlier Debian-package image, because the cell's admin steps use them.
# Build: docker build -f samba.Dockerfile --build-arg VER=4.25.1 -t pd-samba-ad:4.25.1 .
FROM debian:bookworm
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && cd /tmp && curl -fsSLO https://download.samba.org/pub/samba/stable/samba-${VER}.tar.gz \
 && curl -fsSLO https://download.samba.org/pub/samba/stable/samba-${VER}.tar.asc \
 && curl -fsSL https://download.samba.org/pub/samba/samba-pubkey.asc | gpg --batch --import \
 && sha256sum samba-${VER}.tar.gz \
 && gunzip -k samba-${VER}.tar.gz && gpg --batch --verify samba-${VER}.tar.asc samba-${VER}.tar \
 && tar xf samba-${VER}.tar && cd samba-${VER} \
 && DEBIAN_FRONTEND=noninteractive bash bootstrap/generated-dists/debian12/bootstrap.sh \
 && ./configure --prefix=/usr/local/samba --without-systemd \
 && make -j16 && make install \
 && cd / && rm -rf /tmp/samba-* && rm -rf /var/lib/apt/lists/*
ENV PATH=/usr/local/samba/bin:/usr/local/samba/sbin:$PATH
RUN printf '%s\n' '#!/bin/sh' 'set -e' \
    'if [ ! -f /usr/local/samba/private/sam.ldb ]; then' \
    '  rm -f /usr/local/samba/etc/smb.conf' \
    '  samba-tool domain provision --use-rfc2307 --realm=LAB.TEST --domain=LAB --server-role=dc --dns-backend=SAMBA_INTERNAL --adminpass="Lab-Pass-2026!"' \
    '  sed -i "/^\[global\]/a ldap server require strong auth = no" /usr/local/samba/etc/smb.conf' \
    'fi' 'exec samba -i --debuglevel=1' > /usr/local/bin/pd-samba-start && chmod +x /usr/local/bin/pd-samba-start
EXPOSE 389
CMD ["/usr/local/bin/pd-samba-start"]
