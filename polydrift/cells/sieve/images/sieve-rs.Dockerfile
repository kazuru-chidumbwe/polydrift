# sieve-rs (the Sieve interpreter Stalwart uses) from crates.io, exact release pinned, behind the polydrift intent
# runner (sieve-rs-runner). Build: docker build -f sieve-rs.Dockerfile --build-arg VER=1.0.2 -t pd-sieve-rs:1.0.2 .
FROM rust:1-bookworm AS build
ARG VER
ENV PD_SIEVE_RS_VERSION=${VER}
WORKDIR /b
COPY sieve-rs-runner/src src
COPY sieve-rs-runner/Cargo.toml.in Cargo.toml.in
RUN sed "s/@VER@/${VER}/" Cargo.toml.in > Cargo.toml && cargo build --release && cargo tree -i sieve-rs | head -1
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY --from=build /b/target/release/pd-sieve-rs /usr/local/bin/pd-sieve-rs
