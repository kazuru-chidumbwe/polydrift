# Unbound built from the NLnet Labs release tarball (checksum verified against the published .sha256), so the
# resolver under test is the upstream release and not a third-party image.
# Build: docker build -f unbound.Dockerfile --build-arg VER=1.26.0 -t pd-unbound:1.26.0 .
FROM debian:trixie-slim AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential \
      libssl-dev libexpat1-dev libevent-dev bison flex \
 && curl -fsSLO https://nlnetlabs.nl/downloads/unbound/unbound-${VER}.tar.gz \
 && curl -fsSL https://nlnetlabs.nl/downloads/unbound/unbound-${VER}.tar.gz.sha256 -o sum \
 && echo "$(cat sum)  unbound-${VER}.tar.gz" | sha256sum -c - \
 && tar xzf unbound-${VER}.tar.gz && cd unbound-${VER} \
 && ./configure --prefix=/usr/local --with-libevent --disable-flto && make -j4 && make install
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 libexpat1 libevent-2.1-7t64 \
 && rm -rf /var/lib/apt/lists/* && useradd -r -s /usr/sbin/nologin unbound
COPY --from=build /usr/local /usr/local
RUN ldconfig
