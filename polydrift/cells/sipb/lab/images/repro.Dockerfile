# reSIProcate repro (C++, the reSIProcate stack), built from the project's release tarball with CMake (1.14 dropped
# the autotools build), as SIP proxy and registrar; only repro and its libraries are built.
# Build: docker build -f repro.Dockerfile --build-arg VER=1.14.0 -t pd-sipb-repro:1.14.0 .
FROM debian:trixie-slim AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential cmake \
      pkg-config libssl-dev libdb++-dev libpopt-dev libc-ares-dev libasio-dev libboost-system-dev libpcre2-dev \
      python3 \
 && curl -fsSL -o r.tar.gz https://github.com/resiprocate/resiprocate/archive/refs/tags/resiprocate-${VER}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum r.tar.gz)" \
 && tar xzf r.tar.gz && cd resiprocate-resiprocate-${VER} \
 && cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_BUILD_TYPE=Release -DBUILD_REPRO=ON \
      -DBUILD_RETURN=OFF -DBUILD_REFLOW=OFF -DBUILD_MEDIA=OFF -DBUILD_REND=OFF -DBUILD_TFM=OFF -DBUILD_RECON=OFF \
      -DBUILD_TESTING=OFF -DUSE_MAXMIND_GEOIP=OFF -DUSE_SIGCOMP=OFF -DWITH_SSL=ON -DUSE_POPT=ON \
 && cmake --build build -j4 && cmake --install build
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 libdb5.3++ libpopt0 libc-ares2 \
      libpcre2-8-0 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local /usr/local
RUN ldconfig && mkdir -p /var/lib/repro
ENTRYPOINT ["sh", "-c", "exec $(command -v repro || echo /usr/local/sbin/repro) /etc/repro/repro.config"]
