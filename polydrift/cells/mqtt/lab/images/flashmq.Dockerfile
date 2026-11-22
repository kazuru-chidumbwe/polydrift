# FlashMQ from its release tag (no upstream container image). VER is the tag without the leading "v".
ARG BASE=debian:bookworm-slim
FROM ${BASE} AS build
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git cmake g++ make libssl-dev \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "v${VER}" https://github.com/halfgaar/FlashMQ.git /src \
 && cmake -S /src -B /b -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /b -j"$(nproc)"

FROM ${BASE}
ARG VER
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 && rm -rf /var/lib/apt/lists/*
COPY --from=build /b/flashmq /usr/local/bin/flashmq
COPY flashmq.conf /etc/flashmq.conf
RUN useradd -r -M flashmq
USER flashmq
EXPOSE 1883
LABEL org.polydrift.impl="flashmq" org.polydrift.version="${VER}"
CMD ["/usr/local/bin/flashmq", "--config-file", "/etc/flashmq.conf"]
