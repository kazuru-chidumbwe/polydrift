# SIP replay consumers (manuscript 3.6): PJSIP (pjsua-lib) and Sofia-SIP (nua), each placing one call as alice.
# Build: docker build -t pd-consumer-sip .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev pkg-config libpjproject-dev \
      libsofia-sip-ua-dev python3 && rm -rf /var/lib/apt/lists/*
COPY pjconsumer.c sofconsumer.c /src/
RUN gcc -O2 -o /usr/local/bin/pjconsumer /src/pjconsumer.c $(pkg-config --cflags --libs libpjproject) \
 && gcc -O2 -o /usr/local/bin/sofconsumer /src/sofconsumer.c $(pkg-config --cflags --libs sofia-sip-ua) \
 && echo "pjproject $(dpkg-query -W -f='${Version}' libpjproject-dev) sofia-sip $(dpkg-query -W -f='${Version}' libsofia-sip-ua-dev)" > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
