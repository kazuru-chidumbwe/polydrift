# Cyrus IMAP (lmtpd, sievec) from the Debian archive, for the Sieve phase 2 runs over LMTP. The engine wrapper
# (lab/run.py, engine cyrus) writes /etc/imapd.conf and /etc/cyrus.conf per run, creates the lab mailbox over IMAP
# as the Cyrus admin (sasldb, lab only) and delivers over LMTP. Releases by base image: trixie 3.10.x, bookworm 3.6.x.
# Build: docker build -f cyrus.Dockerfile --build-arg BASE=debian:trixie-slim -t pd-cyrus:3.10.2 .
ARG BASE=debian:trixie-slim
FROM ${BASE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cyrus-imapd sasl2-bin \
      python3 && rm -rf /var/lib/apt/lists/* && dpkg-query -W cyrus-imapd
