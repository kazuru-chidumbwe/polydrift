# OpenLDAP built from the openldap.org release tarball. The sha3-512 published next to the tarball is checked and
# the tarball's sha256 is printed into the build log. Runtime: upstream defaults (no access directives, so the
# default "access to * by * read"), mdb backend, suffix dc=lab,dc=test, rootdn cn=admin, base entry added at first
# start (as the Debian package's postinst does), slapd.conf configuration.
# Build: docker build -f openldap.Dockerfile --build-arg VER=2.7.2 -t pd-openldap:2.7.2 .
FROM debian:trixie-slim
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl build-essential groff-base \
      libssl-dev libsasl2-dev libltdl-dev libevent-dev python3 \
 && cd /tmp && curl -fsSLO https://www.openldap.org/software/download/OpenLDAP/openldap-release/openldap-${VER}.tgz \
 && curl -fsSL https://www.openldap.org/software/download/OpenLDAP/openldap-release/openldap-${VER}.sha3-512 -o sum \
 && python3 -c "import hashlib,re,sys; d=open('openldap-${VER}.tgz','rb').read(); want=re.findall(r'[0-9a-f]{128}',open('sum').read())[0]; got=hashlib.sha3_512(d).hexdigest(); print('sha3-512 ok' if got==want else sys.exit('sha3-512 MISMATCH')); print('sha256', hashlib.sha256(d).hexdigest())" \
 && tar xzf openldap-${VER}.tgz && cd openldap-${VER} \
 && ./configure --prefix=/usr/local --sysconfdir=/etc --localstatedir=/var --enable-slapd --enable-mdb \
 && make depend && make -j8 && make install \
 && cd / && rm -rf /tmp/openldap-* /tmp/sum \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/lib/openldap-data /var/run/openldap
RUN printf '%s\n' \
      'include /etc/openldap/schema/core.schema' 'include /etc/openldap/schema/cosine.schema' \
      'include /etc/openldap/schema/inetorgperson.schema' 'include /etc/openldap/schema/nis.schema' \
      'pidfile /var/run/openldap/slapd.pid' 'argsfile /var/run/openldap/slapd.args' \
      'database mdb' 'maxsize 1073741824' 'suffix "dc=lab,dc=test"' 'rootdn "cn=admin,dc=lab,dc=test"' \
      'rootpw labpass' 'directory /var/lib/openldap-data' 'index objectClass eq' > /etc/openldap/slapd.conf \
 && printf '%s\n' 'dn: dc=lab,dc=test' 'objectClass: dcObject' 'objectClass: organization' 'o: lab' 'dc: lab' > /etc/openldap/base.ldif \
 && printf '%s\n' '#!/bin/sh' 'set -e' \
      '[ -f /var/lib/openldap-data/data.mdb ] || /usr/local/sbin/slapadd -f /etc/openldap/slapd.conf -l /etc/openldap/base.ldif' \
      'exec /usr/local/libexec/slapd -f /etc/openldap/slapd.conf -h ldap:/// -d 0' > /usr/local/bin/pd-slapd \
 && chmod +x /usr/local/bin/pd-slapd
EXPOSE 389
CMD ["/usr/local/bin/pd-slapd"]
