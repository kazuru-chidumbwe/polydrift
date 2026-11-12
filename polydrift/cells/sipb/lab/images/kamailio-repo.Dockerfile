# Kamailio from the Kamailio project's own Debian repository for one release line (LINE: 61, 60, 58, ...), so the
# versioned runs use upstream packages. The config is mounted at /etc/kamailio/kamailio.cfg by the runner.
# Build: docker build -f kamailio-repo.Dockerfile --build-arg LINE=61 -t pd-sipb-kamailio:6.1 .
FROM debian:bookworm-slim
ARG LINE
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && curl -fsSL http://deb.kamailio.org/kamailiodebkey.gpg | gpg --dearmor > /usr/share/keyrings/kamailio.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/kamailio.gpg] http://deb.kamailio.org/kamailio${LINE} bookworm main" \
      > /etc/apt/sources.list.d/kamailio.list \
 && apt-get update && apt-get install -y --no-install-recommends kamailio && rm -rf /var/lib/apt/lists/* \
 && echo "KAMAILIO-VERSION $(kamailio -v | head -1)"
ENTRYPOINT ["kamailio", "-DD", "-E", "-f", "/etc/kamailio/kamailio.cfg"]
