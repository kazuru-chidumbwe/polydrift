# Cyrus IMAP built from the GitHub release tarball (cyrusimap/cyrus-imapd), with HTTP and JMAP. The sha256 is
# printed into the build log; the release's .sig is kept beside it. Debian 12 base because 3.8.x needs PCRE1.
# configure needs xxd for the HTTP module and refuses JMAP without Xapian (Debian's libxapian-dev).
# autocreate is a configure option in a source build; ldconfig makes the installed libcyrus libraries findable.
# Runtime: upstream defaults plus what the JMAP cell needs: httpmodules jmap, conversations (JMAP requires them),
# SASL sasldb with plaintext allowed (lab accounts), INBOX created on first IMAP login (autocreate_quota 0).
# Services: imap on 143 (used once per account to create the INBOX), http on 8008.
# Build: docker build -f cyrus.Dockerfile --build-arg VER=3.12.4 -t pd-cyrus:3.12.4 .
FROM debian:bookworm
ARG VER
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
      build-essential pkg-config bison flex perl libperl-dev libsasl2-dev libsasl2-modules sasl2-bin libssl-dev \
      libjansson-dev libical-dev libxml2-dev libsqlite3-dev libicu-dev uuid-dev zlib1g-dev libpcre3-dev libpcre2-dev \
      libchardet-dev libbsd-dev libnghttp2-dev libbrotli-dev libzstd-dev libwslay-dev xxd libxapian-dev rsync \
 && cd /tmp && curl -fsSLO https://github.com/cyrusimap/cyrus-imapd/releases/download/cyrus-imapd-${VER}/cyrus-imapd-${VER}.tar.gz \
 && curl -fsSLO https://github.com/cyrusimap/cyrus-imapd/releases/download/cyrus-imapd-${VER}/cyrus-imapd-${VER}.tar.gz.sig \
 && sha256sum cyrus-imapd-${VER}.tar.gz \
 && tar xzf cyrus-imapd-${VER}.tar.gz && cd cyrus-imapd-${VER} \
 && ./configure --prefix=/usr/local --enable-http --enable-jmap --enable-xapian --enable-autocreate --enable-calalarmd --with-perl=/usr/bin/perl \
 && make -j16 && make install && ldconfig \
 && mkdir -p /usr/local/share/cyrus && cp tools/mkimap /usr/local/share/cyrus/ \
 && cd / && rm -rf /tmp/cyrus-imapd-* && rm -rf /var/lib/apt/lists/*
RUN useradd -r -g mail -d /var/lib/cyrus -s /usr/sbin/nologin cyrus \
 && printf '%s\n' 'configdirectory: /var/lib/cyrus' 'partition-default: /var/spool/cyrus/mail' 'admins: cyrus' \
      'sasl_pwcheck_method: auxprop' 'sasl_auxprop_plugin: sasldb' 'sasl_mech_list: PLAIN LOGIN' 'allowplaintext: yes' \
      'httpmodules: jmap' 'jmap_nonstandard_extensions: no' 'conversations: yes' 'conversations_db: twoskip' \
      'autocreate_quota: 0' 'syslog_prefix: cyrus' > /etc/imapd.conf \
 && printf '%s\n' 'START {' '  recover cmd="ctl_cyrusdb -r"' '}' 'SERVICES {' '  imap cmd="imapd" listen="143" prefork=0' \
      '  http cmd="httpd" listen="8008" prefork=0' '}' 'EVENTS {' '  checkpoint cmd="ctl_cyrusdb -c" period=30' '}' \
      'DAEMON {' '}' > /etc/cyrus.conf \
 && mkdir -p /var/lib/cyrus /var/spool/cyrus/mail /run/cyrus && chown -R cyrus:mail /var/lib/cyrus /var/spool/cyrus /run/cyrus \
 && su -s /bin/sh cyrus -c "perl /usr/local/share/cyrus/mkimap /etc/imapd.conf" \
 && touch /etc/sasldb2 && chown cyrus:mail /etc/sasldb2
EXPOSE 8008 143
CMD ["sh","-c","exec /usr/local/libexec/master -D"]
