# Stalwart (sieve-rs inside the server) for the Sieve phase 2 runs over LMTP: the official image plus python3 for the
# engine wrapper (lab/run.py, engine stalwart), which starts the server on a throwaway store per run, configures it
# through the server's own management methods and delivers over LMTP. Entrypoint reset so the runner's command runs.
# Build: docker build -f stalwart.Dockerfile --build-arg VER=v1.1.0 -t pd-sieve-stalwart:1.1.0 .
ARG VER=v1.1.0
FROM stalwartlabs/stalwart:${VER}
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 \
      && rm -rf /var/lib/apt/lists/*
USER stalwart
ENTRYPOINT []
CMD []
