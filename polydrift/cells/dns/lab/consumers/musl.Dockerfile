# DNS replay consumer, musl stub resolver (manuscript 3.6). Build: docker build -f musl.Dockerfile -t pd-consumer-musl .
FROM alpine:3.22
RUN apk add --no-cache gcc musl-dev
COPY pdstub.c /src/pdstub.c
RUN gcc -O2 -Wall -o /usr/local/bin/pdstub /src/pdstub.c && apk list --installed musl > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
