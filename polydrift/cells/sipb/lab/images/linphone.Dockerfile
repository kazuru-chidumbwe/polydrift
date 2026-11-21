# linphonec (liblinphone, belle-sip stack) from the distribution, as a user agent: auto-answer (UAS role); commands
# for the UAC role are written to its stdin through a FIFO fed by a small TCP bridge on port 4444.
# Build: docker build -f linphone.Dockerfile -t pd-sipb-linphone:trixie .
# Older lines: --build-arg BASE=debian:bookworm-slim (liblinphone 5.1.65); --build-arg BASE=debian:bullseye-slim
# --build-arg ARCHIVE=bullseye (4.4.21; bullseye left the mirrors for archive.debian.org after its LTS end).
ARG BASE=debian:trixie-slim
FROM ${BASE}
ARG ARCHIVE=""
RUN if [ -n "$ARCHIVE" ]; then \
      printf 'deb http://archive.debian.org/debian %s main\ndeb http://archive.debian.org/debian-security %s-security main\n' \
        "$ARCHIVE" "$ARCHIVE" > /etc/apt/sources.list && rm -f /etc/apt/sources.list.d/*; fi \
 && apt-get update && apt-get install -y --no-install-recommends linphone-cli socat && rm -rf /var/lib/apt/lists/* \
 && echo "linphone-cli $(dpkg-query -W -f='${Version}' linphone-cli)" > /LINPHONE-VERSION
COPY linphone-run.sh /usr/local/bin/linphone-run.sh
RUN chmod +x /usr/local/bin/linphone-run.sh
ENTRYPOINT ["/usr/local/bin/linphone-run.sh"]
