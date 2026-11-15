# DNS replay consumer, glibc stub resolver (manuscript 3.6). Build: docker build -f glibc.Dockerfile -t pd-consumer-glibc .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY pdstub.c /src/pdstub.c
RUN gcc -O2 -Wall -o /usr/local/bin/pdstub /src/pdstub.c -lresolv && ldd --version | head -1 > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
