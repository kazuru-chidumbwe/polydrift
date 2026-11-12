# sipb lab client: plays alice, bob, carol and dave around the element under test (driver.py, stdlib only).
# Build: docker build -f client.Dockerfile -t pd-sipb-py:lab .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
