# BIND 9 built from the ISC release tarball, for versions with no usable upstream image (recall versions, RQ5).
# The tarball's SHA-256 is printed in the build log and recorded by the image job; ISC signs releases with PGP
# (bind-${VER}.tar.xz.asc), which the build also fetches and keeps in the image for later checking.
# Build: docker build -f bind.Dockerfile --build-arg VER=9.16.15 -t pd-bind:9.16.15 .
FROM debian:bullseye-slim AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils build-essential \
      pkg-config libssl-dev libuv1-dev libcap-dev python3 \
 && curl -fsSLO https://downloads.isc.org/isc/bind9/${VER}/bind-${VER}.tar.xz \
 && curl -fsSLO https://downloads.isc.org/isc/bind9/${VER}/bind-${VER}.tar.xz.asc \
 && echo "TARBALL-SHA256 $(sha256sum bind-${VER}.tar.xz)" \
 && tar xJf bind-${VER}.tar.xz && cd bind-${VER} \
 && ./configure --prefix=/usr/local --sysconfdir=/etc/bind --localstatedir=/var --without-python \
      --disable-linux-caps --with-openssl \
 && make -j4 && make install \
 && mkdir -p /usr/local/share/bind-src && cp ../bind-${VER}.tar.xz.asc /usr/local/share/bind-src/
FROM debian:bullseye-slim
RUN apt-get update && apt-get install -y --no-install-recommends libssl1.1 libuv1 libcap2 \
 && rm -rf /var/lib/apt/lists/* && useradd -r -s /usr/sbin/nologin bind && mkdir -p /etc/bind /var/cache/bind \
 && chown bind /var/cache/bind
COPY --from=build /usr/local /usr/local
RUN ldconfig
ENTRYPOINT ["/usr/local/sbin/named", "-g", "-c", "/etc/bind/named.conf"]
