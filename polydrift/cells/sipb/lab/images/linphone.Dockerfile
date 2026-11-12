# linphonec (liblinphone, belle-sip stack) from the distribution, as a user agent: auto-answer (UAS role); commands
# for the UAC role are written to its stdin through a FIFO fed by a small TCP bridge on port 4444.
# Build: docker build -f linphone.Dockerfile -t pd-sipb-linphone:trixie .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends linphone-cli socat && rm -rf /var/lib/apt/lists/*
COPY linphone-run.sh /usr/local/bin/linphone-run.sh
RUN chmod +x /usr/local/bin/linphone-run.sh
ENTRYPOINT ["/usr/local/bin/linphone-run.sh"]
