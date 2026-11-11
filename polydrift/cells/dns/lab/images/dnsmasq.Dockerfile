# dnsmasq built from the thekelleys.org.uk release tarball; the .asc is checked against the author's published key
# (srkgpg.txt) and the sha256 printed into the build log.
# Build: docker build -f dnsmasq.Dockerfile --build-arg VER=2.93 -t pd-dnsmasq:2.93 .
FROM debian:bookworm-slim
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg xz-utils build-essential \
 && cd /tmp && curl -fsSLO https://thekelleys.org.uk/dnsmasq/dnsmasq-${VER}.tar.xz \
 && curl -fsSLO https://thekelleys.org.uk/dnsmasq/dnsmasq-${VER}.tar.xz.asc \
 && curl -fsSL https://thekelleys.org.uk/srkgpg.txt | gpg --batch --import \
 && gpg --batch --verify dnsmasq-${VER}.tar.xz.asc dnsmasq-${VER}.tar.xz && sha256sum dnsmasq-${VER}.tar.xz \
 && tar xJf dnsmasq-${VER}.tar.xz && cd dnsmasq-${VER} && make -j8 && make install PREFIX=/usr/local \
 && cd / && rm -rf /tmp/dnsmasq-* && apt-get purge -y build-essential && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/usr/local/sbin/dnsmasq", "-k", "-C", "/conf/dnsmasq.conf", "--log-facility=-"]
