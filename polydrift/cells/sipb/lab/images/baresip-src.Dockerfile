# baresip and libre built from the upstream release tags (github.com/baresip, matching TAG for both), so the UA under
# test follows the version rule (latest release and the two preceding minor releases); the Debian package is 1.1.0.
# Only the modules the lab config loads are built, as shared modules in /usr/lib/baresip/modules.
# Build: docker build -f baresip-src.Dockerfile --build-arg TAG=v4.13.0 -t pd-sipb-baresip:4.13.0 .
FROM debian:trixie-slim AS build
ARG TAG
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential cmake \
      pkg-config libssl-dev zlib1g-dev \
 && cd /tmp && curl -fsSL -o re.tgz https://github.com/baresip/re/archive/refs/tags/${TAG}.tar.gz \
 && curl -fsSL -o baresip.tgz https://github.com/baresip/baresip/archive/refs/tags/${TAG}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum re.tgz baresip.tgz | tr '\n' ' ')" \
 && mkdir re baresip && tar xzf re.tgz -C re --strip-components=1 && tar xzf baresip.tgz -C baresip --strip-components=1 \
 && cmake -S re -B re/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib && cmake --build re/build -j8 \
 && cmake --install re/build \
 && cmake -S baresip -B baresip/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib -DSTATIC=OFF \
      -DMODULES="g711;aufile;account;contact;menu;ctrl_tcp" \
 && cmake --build baresip/build -j8 && cmake --install baresip/build
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 zlib1g && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/bin/baresip /usr/bin/baresip
COPY --from=build /usr/lib /usr/lib
RUN ldconfig && baresip -h >/dev/null 2>&1; ls /usr/lib/baresip/modules
# audio source: 120 s of 8 kHz 16-bit mono silence (as baresip.Dockerfile)
RUN printf 'RIFF\044\114\035\000WAVEfmt \020\000\000\000\001\000\001\000\100\037\000\000\200\076\000\000\002\000\020\000data\000\114\035\000' \
      > /usr/local/share/pd-src.wav && head -c 1920000 /dev/zero >> /usr/local/share/pd-src.wav
ENTRYPOINT ["sh", "-c", "mkdir -p /root/.baresip && cp -r /pd/baresip/. /root/.baresip/ && exec baresip -f /root/.baresip"]
