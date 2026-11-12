# Kamailio from the distribution (Debian trixie 6.0.x) or, with VER set, from the Kamailio project's own repository
# for that release line. The config is mounted at /etc/kamailio/kamailio.cfg by the runner.
# Build: docker build -f kamailio.Dockerfile -t pd-sipb-kamailio:trixie .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends kamailio && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["kamailio", "-DD", "-E", "-f", "/etc/kamailio/kamailio.cfg"]
