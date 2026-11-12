# reSIProcate repro (C++, the reSIProcate stack), built from the project's release tarball, as SIP proxy and
# registrar. Build: docker build -f repro.Dockerfile --build-arg VER=1.14.0 -t pd-sipb-repro:1.14.0 .
FROM debian:trixie-slim AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential autoconf \
      automake libtool pkg-config libssl-dev libdb++-dev libpopt-dev libc-ares-dev libasio-dev libboost-system-dev \
      libpcre2-dev python3 \
 && curl -fsSL -o r.tar.gz https://github.com/resiprocate/resiprocate/archive/refs/tags/resiprocate-${VER}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum r.tar.gz)" \
 && tar xzf r.tar.gz && cd resiprocate-resiprocate-${VER} \
 && autoreconf --install \
 && ./configure --prefix=/usr/local --with-repro --enable-repro-plugins=no --with-ssl --with-c-ares \
 && make -j4 && make install
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 libdb5.3++ libpopt0 libc-ares2 \
      libboost-system1.83.0 libpcre2-8-0 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local /usr/local
RUN ldconfig && mkdir -p /var/lib/repro
ENTRYPOINT ["/usr/local/sbin/repro", "/etc/repro/repro.config"]
