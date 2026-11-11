# ApacheDS from the Apache release tarball (archive.apache.org). The detached signature is checked against the
# Apache Directory KEYS file, the published sha512 is checked, and the sha256 is printed into the build log.
# The .sha512 file uses the gpg --print-md layout ("name: AF233A1F CB7EAE00 ..." over several lines).
# Runtime: the distribution's default instance (base dc=example,dc=com, uid=admin,ou=system / secret).
# Build: docker build -f apacheds.Dockerfile --build-arg VER=2.0.0.AM27 -t pd-apacheds:2.0.0.AM27 .
FROM debian:trixie
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jre-headless curl ca-certificates gnupg \
 && cd /tmp && B=https://archive.apache.org/dist/directory/apacheds/dist/${VER} \
 && curl -fsSLO $B/apacheds-${VER}.tar.gz && curl -fsSLO $B/apacheds-${VER}.tar.gz.asc \
 && curl -fsSLO $B/apacheds-${VER}.tar.gz.sha512 \
 && curl -fsSL https://downloads.apache.org/directory/KEYS | gpg --batch --import \
 && gpg --batch --verify apacheds-${VER}.tar.gz.asc apacheds-${VER}.tar.gz \
 && H=$(cut -d: -f2- apacheds-${VER}.tar.gz.sha512 | tr -cd '0-9A-Fa-f' | tr 'A-F' 'a-f') \
 && echo "$H  apacheds-${VER}.tar.gz" | sha512sum -c - \
 && sha256sum apacheds-${VER}.tar.gz \
 && tar xzf apacheds-${VER}.tar.gz -C /opt && ln -s "$(ls -d /opt/apacheds-2.0.0.*)" /opt/apacheds \
 && rm -f /tmp/apacheds-* && rm -rf /var/lib/apt/lists/*
EXPOSE 10389
CMD ["/opt/apacheds/bin/apacheds.sh","default","run"]
