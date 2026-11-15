# JMAP replay consumers (manuscript 3.6): jmapc (Python) and go-jmap (Go), plus the replay server's lab
# certificate (self-signed for the replay server address; both consumers trust it). Build: docker build -t pd-consumer-jmap .
FROM golang:1.23-bookworm AS go
WORKDIR /src
COPY gojmap/main.go .
RUN go mod init pdjmap && go get git.sr.ht/~rockorager/go-jmap@v0.5.0 && go mod tidy \
 && CGO_ENABLED=0 go build -o /pd-gojmap . && go list -m git.sr.ht/~rockorager/go-jmap > /GOJMAP-VERSION

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir jmapc==0.4.1 \
 && mkdir /certs && openssl req -x509 -newkey rsa:2048 -nodes -keyout /certs/key.pem -out /certs/cert.pem -days 3650 \
      -subj /CN=pd-replay -addext "subjectAltName=IP:172.31.63.53"
COPY --from=go /pd-gojmap /usr/local/bin/pd-gojmap
COPY --from=go /GOJMAP-VERSION /GOJMAP-VERSION
COPY jmapcconsumer.py /jmapcconsumer.py
ENV REQUESTS_CA_BUNDLE=/certs/cert.pem SSL_CERT_FILE=/certs/cert.pem
RUN echo "jmapc $(pip show jmapc | grep ^Version | cut -d' ' -f2) $(cat /GOJMAP-VERSION)" > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
