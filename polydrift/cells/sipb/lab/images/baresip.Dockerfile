# baresip (libre/librem stack) from the distribution, as a user agent with auto-answer (UAS role) and the ctrl_tcp
# command interface (UAC role). Config mounted by the runner at /pd/baresip.
# Build: docker build -f baresip.Dockerfile -t pd-sipb-baresip:trixie .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends baresip-core && rm -rf /var/lib/apt/lists/*
# the runner mounts the config read-only at /pd/baresip; baresip writes into its config directory, so it runs on a copy
ENTRYPOINT ["sh", "-c", "mkdir -p /root/.baresip && cp -r /pd/baresip/. /root/.baresip/ && exec baresip -f /root/.baresip"]
