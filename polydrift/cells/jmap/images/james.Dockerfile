# Apache James (memory distribution), official apache/james images pinned by release. /root/conf is a VOLUME, so the
# TLS keystore that imapserver.xml names is created at start (its default password), then the image's own java
# command runs. The classpath form differs by release: 3.9.x reads a jib classpath file, 3.7.x and 3.8.x list dirs.
# Build: docker build -f james.Dockerfile --build-arg VER=3.9.1 -t pd-james:3.9.1 .
ARG VER
FROM apache/james:memory-${VER}
ARG VER
ENV PD_JAMES_VER=${VER}
ENTRYPOINT ["sh","-c","[ -f /root/conf/keystore ] || keytool -genkeypair -alias james -keyalg RSA -keysize 2048 -validity 3650 -dname CN=lab.test -keystore /root/conf/keystore -storetype PKCS12 -storepass james72laBalle -keypass james72laBalle; case \"$PD_JAMES_VER\" in 3.9.*) CP=@/root/jib-classpath-file;; *) CP='/root/resources:/root/classes:/root/libs/*';; esac; exec java -Dlogback.configurationFile=/root/conf/logback.xml -Dworking.directory=/root/ -Djdk.tls.ephemeralDHKeySize=2048 -Dextra.props=/root/conf/jvm.properties -cp \"$CP\" org.apache.james.MemoryJamesServerMain"]
