# JMAP push sink (lab/push/sink.py). At start it makes a lab CA and a certificate for pd-push.example.com in /pki
# (a host folder the runner shares), plus what the servers under test need to trust that CA: bundle.pem (the
# system bundle with the lab CA appended, for OpenSSL, curl and rustls platform verification) and trust.p12 (a Java
# truststore holding only the lab CA). Build: docker build -t pd-jmap-push:lab .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY sink.py /sink.py
ENTRYPOINT ["sh", "-c", "set -e; cd /pki; if [ ! -s bundle.pem ]; then \
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj '/CN=polydrift lab push CA' -keyout ca.key -out ca.pem \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign; \
  openssl req -newkey rsa:2048 -nodes -subj '/CN=pd-push.example.com' -keyout sink.key -out sink.csr; \
  printf 'subjectAltName=DNS:pd-push.example.com\\nextendedKeyUsage=serverAuth\\n' > ext.cnf; \
  openssl x509 -req -in sink.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 30 -extfile ext.cnf -out sink.pem; \
  cat /etc/ssl/certs/ca-certificates.crt ca.pem > bundle.pem; \
  openssl pkcs12 -export -nokeys -in ca.pem -jdktrust anyExtendedKeyUsage -passout pass:pdlab -out trust.p12; \
  chmod 644 *.pem trust.p12; fi; exec python3 /sink.py /pki"]
