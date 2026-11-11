# Exim built from the exim.org release tarball (current or old/ directory); the .asc is checked against the Exim
# maintainers' keyring and the sha256 printed into the build log. Sieve filtering is in the default build.
# Build: docker build -f exim.Dockerfile --build-arg VER=4.100.1 -t pd-exim:4.100.1 .
FROM debian:bookworm
ARG VER
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
      gnupg xz-utils build-essential pkg-config libpcre2-dev libdb-dev libssl-dev python3 \
 && useradd -r -s /usr/sbin/nologin exim \
 && cd /tmp && (curl -fsSLO https://ftp.exim.org/pub/exim/exim4/exim-${VER}.tar.xz \
      && curl -fsSLO https://ftp.exim.org/pub/exim/exim4/exim-${VER}.tar.xz.asc \
   || (curl -fsSLO https://ftp.exim.org/pub/exim/exim4/old/exim-${VER}.tar.xz \
      && curl -fsSLO https://ftp.exim.org/pub/exim/exim4/old/exim-${VER}.tar.xz.asc)) \
 && curl -fsSL https://downloads.exim.org/Exim-Maintainers-Keyring.asc | gpg --batch --import \
 && gpg --batch --verify exim-${VER}.tar.xz.asc exim-${VER}.tar.xz \
 && sha256sum exim-${VER}.tar.xz \
 && tar xJf exim-${VER}.tar.xz && cd exim-${VER} && mkdir -p Local /var/spool/exim \
 && sed -e 's/^EXIM_USER=.*$/EXIM_USER=exim/' src/EDITME > Local/Makefile \
 && printf '%s\n' 'BIN_DIRECTORY=/usr/local/bin' 'CONFIGURE_FILE=/etc/exim.conf' 'SPOOL_DIRECTORY=/var/spool/exim' \
      'EXIM_MONITOR=' 'USE_OPENSSL=yes' 'TLS_LIBS=-lssl -lcrypto' >> Local/Makefile \
 && make -j16 && make install && cd / && rm -rf /tmp/exim-* && rm -rf /var/lib/apt/lists/* \
 && printf '%s\n' 'primary_hostname = pd-exim' 'qualify_domain = lab.test' 'never_users =' 'acl_smtp_rcpt = accept' \
      'begin routers' 'begin transports' 'begin retry' 'begin rewrite' 'begin authenticators' > /etc/exim.conf
