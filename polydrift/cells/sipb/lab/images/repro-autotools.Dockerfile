# reSIProcate repro 1.12 (autotools build), from the release tarball, for the versioned runs. 1.13 ships CMake only
# and uses repro.Dockerfile. 1.12 needs OpenSSL 1.1 (it calls CRYPTO_set_mem_debug, gone in OpenSSL 3), so the
# build runs on Ubuntu 20.04 (Debian bullseye's archive no longer serves it reliably).
# The bundled cajun JSON headers (contrib/cajun) are not on the default include path, and configure needs gperf.
# Build: docker build -f repro-autotools.Dockerfile --build-arg VER=1.12.0 -t pd-sipb-repro:1.12.0 .
FROM ubuntu:20.04 AS build
ARG VER
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential autoconf \
      automake libtool pkg-config libssl-dev libdb++-dev libpopt-dev libc-ares-dev libasio-dev libboost-system-dev \
      libpcre3-dev gperf python3 \
 && curl -fsSL -o r.tar.gz https://github.com/resiprocate/resiprocate/archive/refs/tags/resiprocate-${VER}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum r.tar.gz)" \
 && tar xzf r.tar.gz && cd resiprocate-resiprocate-${VER} \
 && autoreconf --install \
 && CPPFLAGS="-I$PWD/contrib/cajun/include" ./configure --prefix=/usr/local --with-repro --with-ssl --with-c-ares \
 && make -j8 && make install
FROM ubuntu:20.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends libssl1.1 libdb5.3++ libpopt0 libc-ares2 libpcre3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local /usr/local
RUN ldconfig && mkdir -p /var/lib/repro
ENTRYPOINT ["sh", "-c", "exec $(command -v repro || echo /usr/local/sbin/repro) /etc/repro/repro.config"]
