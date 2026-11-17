# OpenSIPS from a release tag, for versions Docker Hub does not publish (opensips/opensips has only the latest of each
# series, e.g. 3.4, no 3.4.1). Core modules only, which is all the lab config loads. The modules directory is linked to
# the Debian path the shared opensips.cfg names in mpath.
# Build: docker build -f opensips-src.Dockerfile --build-arg TAG=3.4.1 -t pd-sipb-opensips:3.4.1-src .
FROM debian:bookworm-slim
ARG TAG=3.4.1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential bison flex git ca-certificates \
      pkg-config libncurses-dev libssl-dev && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$TAG" https://github.com/OpenSIPS/opensips.git /src \
 && cd /src && make -j"$(nproc)" all && make install \
 && d="$(dirname "$(find /usr/local -name tm.so | head -1)")" && test -n "$d" \
 && mkdir -p /usr/lib/x86_64-linux-gnu/opensips && ln -s "$d" /usr/lib/x86_64-linux-gnu/opensips/modules \
 && /usr/local/sbin/opensips -V | head -1 > /OPENSIPS-VERSION && cat /OPENSIPS-VERSION \
 && rm -rf /src
ENTRYPOINT ["/usr/local/sbin/opensips"]
