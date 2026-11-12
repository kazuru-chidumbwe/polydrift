# reSIProcate repro releases before 1.14 (autotools build), from the release tarball, for the versioned runs.
# Build: docker build -f repro-autotools.Dockerfile --build-arg VER=1.13.2 -t pd-sipb-repro:1.13.2 .
FROM debian:bookworm-slim AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential autoconf \
      automake libtool pkg-config libssl-dev libdb++-dev libpopt-dev libc-ares-dev libasio-dev libboost-system-dev \
      libpcre3-dev python3 \
 && curl -fsSL -o r.tar.gz https://github.com/resiprocate/resiprocate/archive/refs/tags/resiprocate-${VER}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum r.tar.gz)" \
 && tar xzf r.tar.gz && cd resiprocate-resiprocate-${VER} \
 && autoreconf --install \
 && ./configure --prefix=/usr/local --with-repro --with-ssl --with-c-ares \
 && make -j8 && make install
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 libdb5.3++ libpopt0 libc-ares2 libpcre3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local /usr/local
RUN ldconfig && mkdir -p /var/lib/repro
ENTRYPOINT ["sh", "-c", "exec $(command -v repro || echo /usr/local/sbin/repro) /etc/repro/repro.config"]
