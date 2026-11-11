# GNU Mailutils built from the ftp.gnu.org release tarball; the .sig is checked against the GNU keyring
# (gnu-keyring.gpg from ftp.gnu.org) and the sha256 is printed into the build log.
# GDBM is enabled in every version: 3.20 cannot build its uidnew Sieve extension without a DBM library, and DBM backs
# the vacation reply database, so all versions share one configuration.
# Build: docker build -f mailutils.Dockerfile --build-arg VER=3.21 -t pd-mailutils:3.21 .
FROM debian:bookworm
ARG VER
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
      gnupg xz-utils build-essential pkg-config libltdl-dev libgnutls28-dev libreadline-dev libgsasl-dev libgdbm-dev python3 \
 && cd /tmp && curl -fsSLO https://ftp.gnu.org/gnu/mailutils/mailutils-${VER}.tar.xz \
 && curl -fsSLO https://ftp.gnu.org/gnu/mailutils/mailutils-${VER}.tar.xz.sig \
 && curl -fsSL https://ftp.gnu.org/gnu/gnu-keyring.gpg -o kr.gpg \
 && gpg --batch --no-default-keyring --keyring ./kr.gpg --verify mailutils-${VER}.tar.xz.sig mailutils-${VER}.tar.xz \
 && sha256sum mailutils-${VER}.tar.xz \
 && tar xJf mailutils-${VER}.tar.xz && cd mailutils-${VER} \
 && ./configure --prefix=/usr/local --without-guile --without-python --disable-nls && (make -j16 || make) && make install \
 && ldconfig && cd / && rm -rf /tmp/mailutils-* /tmp/kr.gpg && rm -rf /var/lib/apt/lists/*
