# Dovecot + Pigeonhole built from the dovecot.org release tarballs (sha256 printed into the build log; .sig kept).
# Build: docker build -f dovecot.Dockerfile --build-arg DV=2.4.5 --build-arg SERIES=2.4 \
#          --build-arg PH=dovecot-pigeonhole-2.4.5 -t pd-pigeonhole:2.4.5 .
#        2.3: DV=2.3.21.1 SERIES=2.3 PH=dovecot-2.3-pigeonhole-0.5.21.1
FROM debian:bookworm
ARG DV
ARG SERIES
ARG PH
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
      build-essential pkg-config libssl-dev zlib1g-dev libbz2-dev liblz4-dev libzstd-dev libicu-dev libpcre2-dev \
      bison flex python3 \
 && cd /tmp && curl -fsSLO https://dovecot.org/releases/${SERIES}/dovecot-${DV}.tar.gz \
 && curl -fsSLO https://pigeonhole.dovecot.org/releases/${SERIES}/${PH}.tar.gz \
 && sha256sum dovecot-${DV}.tar.gz ${PH}.tar.gz \
 && tar xzf dovecot-${DV}.tar.gz && cd dovecot-${DV} \
 && ./configure --prefix=/usr/local --sysconfdir=/etc --localstatedir=/var --without-docs && make -j16 && make install \
 && cd /tmp && tar xzf ${PH}.tar.gz && cd $(ls -d /tmp/*pigeonhole*/ | head -1) \
 && ./configure --prefix=/usr/local --with-dovecot=/usr/local/lib/dovecot && make -j16 && make install \
 && cd / && rm -rf /tmp/dovecot-* /tmp/*pigeonhole* && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1000 -m pd
