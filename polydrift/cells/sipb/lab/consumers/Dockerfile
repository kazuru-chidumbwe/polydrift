# SIP replay consumers (manuscript 3.6): PJSIP pjsua-lib (built from the pjproject release tarball, as the cell's
# pjsua image is) and Sofia-SIP nua (Debian package), each placing one call as alice.
# Build: docker build --build-arg VER=2.15.1 -t pd-consumer-sip .
FROM debian:trixie-slim
ARG VER=2.15.1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential pkg-config \
      libssl-dev python3 libsofia-sip-ua-dev \
 && curl -fsSL -o /p.tar.gz https://github.com/pjsip/pjproject/archive/refs/tags/${VER}.tar.gz \
 && echo "TARBALL-SHA256 $(sha256sum /p.tar.gz)" \
 && cd / && tar xzf p.tar.gz && cd pjproject-${VER} \
 && ./configure --prefix=/usr/local --disable-video --disable-sound --disable-libyuv --disable-libwebrtc \
      --disable-opencore-amr \
 && make dep && make -j4 && make install && cd / && rm -rf /pjproject-${VER} /p.tar.gz \
 && rm -rf /var/lib/apt/lists/*
COPY pjconsumer.c sofconsumer.c /src/
RUN gcc -O2 -o /usr/local/bin/pjconsumer /src/pjconsumer.c $(PKG_CONFIG_PATH=/usr/local/lib/pkgconfig pkg-config --static --cflags --libs libpjproject) -lssl -lcrypto -lpthread -lm \
 && gcc -O2 -o /usr/local/bin/sofconsumer /src/sofconsumer.c $(pkg-config --cflags --libs sofia-sip-ua) \
 && echo "pjproject ${VER} sofia-sip $(dpkg-query -W -f='${Version}' libsofia-sip-ua-dev)" > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
