# Knot Resolver from CZ.NIC's signed release packages (pkg.labs.nic.cz), version pinned. Replaces the Docker Hub
# cznic/knot-resolver tags, which are weekly git snapshots (6.5.1.dev1+<commit>), not releases.
# Build: docker build -f knot.Dockerfile --build-arg PKG=knot-resolver6 --build-arg VER=6.5.1-cznic.1~trixie -t pd-knot:6.5.1 .
FROM debian:trixie-slim
ARG PKG
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && curl -fsSL https://pkg.labs.nic.cz/gpg -o /usr/share/keyrings/cznic-labs-pkg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/cznic-labs-pkg.gpg] https://pkg.labs.nic.cz/knot-resolver trixie main" \
      > /etc/apt/sources.list.d/cznic-labs-knot-resolver.list \
 && apt-get update && apt-get install -y --no-install-recommends "${PKG}=${VER}" \
 && rm -rf /var/lib/apt/lists/*
