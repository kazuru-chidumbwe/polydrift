# Apache James (memory distribution), official apache/james images pinned by release. /root/conf is a VOLUME, so the
# TLS keystore that imapserver.xml names is created at start (its default password), then the image's own java
# command runs. The classpath form differs by release: 3.9.x reads a jib classpath file, 3.7.x and 3.8.x list dirs.
# 3.7.x and 3.8.x still default to the draft JMAP routes, where the RFC 8620 session resource does not exist (404
# without a jmapVersion Accept parameter), so jmap.version.default is set to rfc-8621 there; 3.9.x has no draft.
# Build: docker build -f james.Dockerfile --build-arg VER=3.9.1 -t pd-james:3.9.1 .
ARG VER
FROM apache/james:memory-${VER}
ARG VER
ENV PD_JAMES_VER=${VER}
ENTRYPOINT ["sh","-c","[ -f /root/conf/keystore ] || keytool -genkeypair -alias james -keyalg RSA -keysize 2048 -validity 3650 -dname CN=lab.test -keystore /root/conf/keystore -storetype PKCS12 -storepass james72laBalle -keypass james72laBalle; case \"$PD_JAMES_VER\" in 3.9.*) CP=@/root/jib-classpath-file;; *) CP='/root/resources:/root/classes:/root/libs/*'; grep -q '^jmap.version.default' /root/conf/jmap.properties || { echo; echo 'jmap.version.default=rfc-8621'; } >> /root/conf/jmap.properties;; esac; exec java -Dlogback.configurationFile=/root/conf/logback.xml -Dworking.directory=/root/ -Djdk.tls.ephemeralDHKeySize=2048 -Dextra.props=/root/conf/jvm.properties -cp \"$CP\" org.apache.james.MemoryJamesServerMain"]
