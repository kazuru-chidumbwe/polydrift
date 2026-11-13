# baresip (libre/librem stack) from the distribution, as a user agent with auto-answer (UAS role) and the ctrl_tcp
# command interface (UAC role). Config mounted by the runner at /pd/baresip.
# Build: docker build -f baresip.Dockerfile -t pd-sipb-baresip:trixie .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends baresip-core && rm -rf /var/lib/apt/lists/*
# audio source: 120 s of 8 kHz 16-bit mono silence (ausine runs only at 48 kHz, so a PCMU call closed at once)
RUN printf 'RIFF\044\114\035\000WAVEfmt \020\000\000\000\001\000\001\000\100\037\000\000\200\076\000\000\002\000\020\000data\000\114\035\000' \
      > /usr/local/share/pd-src.wav && head -c 1920000 /dev/zero >> /usr/local/share/pd-src.wav
# the runner mounts the config read-only at /pd/baresip; baresip writes into its config directory, so it runs on a copy
ENTRYPOINT ["sh", "-c", "mkdir -p /root/.baresip && cp -r /pd/baresip/. /root/.baresip/ && exec baresip -f /root/.baresip"]
