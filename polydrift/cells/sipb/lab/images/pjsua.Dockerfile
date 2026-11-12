# pjsua (PJSIP) built from the pjproject release tarball, as a user agent: auto-answers calls (UAS role) and takes
# commands on its telnet CLI (UAC role). Build: docker build -f pjsua.Dockerfile --build-arg VER=2.15.1 -t pd-sipb-pjsua:2.15.1 .
FROM debian:trixie-slim AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential pkg-config \
      libssl-dev python3 \
 && curl -fsSL -o p.tar.gz https://github.com/pjsip/pjproject/archive/refs/tags/${VER}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum p.tar.gz)" \
 && tar xzf p.tar.gz && cd pjproject-${VER} \
 && ./configure --disable-video --disable-sound --disable-libyuv --disable-libwebrtc --disable-opencore-amr \
 && make dep && make -j4 \
 && cp pjsip-apps/bin/pjsua-* /usr/local/bin/pjsua
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl3t64 && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin/pjsua /usr/local/bin/pjsua
ENTRYPOINT ["pjsua"]
